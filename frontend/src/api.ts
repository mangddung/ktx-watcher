// 백엔드 API 와 공용 타입. 모든 요청은 같은 출처(/api, /auth)로 보내고 세션 쿠키로 인증한다.
import { createContext, useContext } from 'react'
import dayjs from 'dayjs'

export type Basis = 'dep' | 'arr'
export type WatchStatus = 'creating' | 'active' | 'paused' | 'ended' // creating: 자동 채널을 만드는 몇 초
export type ChannelKind = 'discord_bot' | 'discord_webhook' | 'telegram'
export type SeatState = 'avail' | 'short' | 'soldout' | 'none' | 'wait' // wait: 시간표로만 알고 좌석은 아직 모름
export type LogLevel = 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR'
export type HealthLevel = 'ok' | 'warning' | 'critical'

export interface TrainPick {
  date: string // YYYY-MM-DD
  no: string
  dep: string // HH:mm
}

export interface Watch {
  id: number
  dep: string
  arr: string
  start: string // YYYY-MM-DD HH:mm. start~end 는 하나로 이어진 구간
  end: string
  basis: Basis
  trainTypes: string[]
  seats: string[]
  passengers: number
  trains: TrainPick[] | null // null 이면 조건에 맞는 전체
  status: WatchStatus
  pauseReason: string | null
  channelIds: number[]
  viewers: string[]
  createdVia: 'web' | 'discord'
  scans: number
  found: number
  errors: number
  remaining: number | null
  lastScan: number | null
  lastError: string | null
  endReason: 'no_targets' | 'manual' | null
  endedAt: number | null
  archivedAt: number | null
  createdAt: number
}

// 상세: 알렸고 아직 다시 매진되지 않은 좌석
export interface WatchDetail extends Watch {
  available: { date: string; no: string; seats: string[] }[]
}

export interface Channel {
  id: number
  kind: ChannelKind
  name: string
  target: string // 서버가 가린 값
  managed: boolean
  system: boolean
  broken: boolean
  endedWatchId: number | null
}

export interface Train {
  date: string
  no: string
  type: string | null // 시간표로만 알면 null. 좌석 조회 결과가 오면 채워진다
  label?: string // type 이 null 일 때 열차번호로 짐작한 종류 이름(예: KTX-이음, 무궁화, KTX)
  groups?: string[] // type 이 null 일 때 열차번호로 짐작한 묶음
  dep: string
  arr: string
  arrDate: string
  minutes: number
  general: SeatState
  special: SeatState
}

export interface Member {
  id: string
  name: string
  username: string
  avatar: string | null
}

export interface LogLine {
  t: number
  level: LogLevel
  source: string
  msg: string
}

export interface Health {
  level: HealthLevel
  title: string
  reasons: { label: string; value: string }[]
  errors1h: number
  failRate1h: number
}

export interface Overview {
  groups: number
  cycleSeconds: number
  lastCycle: number | null
  nextCycle: number | null
}

export interface Config {
  maxActive: number
  maxWatchDays: number
  cutoffMinutes: number
  stations: string[]
  trainGroups: { name: string; types: string[] }[]
  seatClasses: string[]
  routes: Record<string, string[]> // 출발역 → 갈아타지 않고 갈 수 있는 도착역
  botReady: boolean
  telegram: boolean
}

// 역 선택 지도 데이터(백엔드 stations.json, boundaries.json)
export interface StationMapData {
  stations: { name: string; lat: number; lon: number; label: 'r' | 'l' | 't' | 'b'; major?: boolean }[]
  lines: string[][]
  provinces: string // 해안선·도 경계 SVG path
  metros: string // 광역시·세종 경계 SVG path
  rank: string[] // 역 이름을 먼저 보여줄 순서(KTX, ITX, 기타 편수 많은 역부터)
}

// 지도에 그리는 출발→도착 직통 열차 경로(열차 시간표 기준). weekly 는 묶음(KTX, ITX, 기타)별 주간 편수
export interface RoutePath {
  track: string[] // 선로를 따라 지나는 역들
  stops: string[] // 이 경로 열차가 서는 역들
  weekly: Record<string, number>
}

export interface Me {
  id: string
  name: string | null
  avatar: string | null
}

export interface ChannelPickBody {
  mode: 'auto' | 'manual'
  viewers: string[]
  channelIds: number[]
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(path, {
    method,
    credentials: 'same-origin',
    // 상태를 바꾸는 요청에는 서버가 이 헤더를 요구한다(CSRF 방지)
    headers: { 'X-Requested-With': 'ktx', ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}) },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (!r.ok) throw await errorOf(r)
  return r.json() as Promise<T>
}

async function errorOf(r: Response) {
  let msg = r.status === 401 ? '로그인이 필요합니다' : `요청에 실패했습니다 (${r.status})`
  try {
    const j = await r.json()
    if (typeof j.detail === 'string') msg = j.detail
  } catch {
    // 본문이 JSON 이 아니면 기본 문구
  }
  return new ApiError(r.status, msg)
}

export interface SearchBody { dep: string; arr: string; start: string; end: string; basis: Basis; passengers: number }

// 열차 조회. 먼저 시간표(좌석 모름)가 오고, 그다음 코레일 한 페이지(10편)씩 SSE 로 온다.
// signal 로 끊으면 서버도 다음 페이지를 요청하지 않는다
async function searchTrains(
  body: SearchBody, signal: AbortSignal, on: { timetable: (t: Train[]) => void; trains: (t: Train[]) => void },
) {
  const r = await fetch('/api/trains/search', {
    method: 'POST',
    credentials: 'same-origin',
    signal,
    headers: { 'X-Requested-With': 'ktx', 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw await errorOf(r)
  const reader = r.body!.pipeThrough(new TextDecoderStream()).getReader()
  let buf = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) throw new Error('조회가 중간에 끊겼습니다')
    buf += value
    let i
    while ((i = buf.indexOf('\n\n')) >= 0) {
      const e = JSON.parse(buf.slice('data: '.length, i))
      buf = buf.slice(i + 2)
      if (e.error) throw new Error(e.error)
      if (e.done) return
      if (e.timetable) on.timetable(e.timetable)
      else on.trains(e.trains)
    }
  }
}

export const api = {
  me: () => request<Me>('GET', '/api/me'),
  config: () => request<Config>('GET', '/api/config'),
  map: () => request<StationMapData>('GET', '/api/map'),
  route: (dep: string, arr: string) =>
    request<{ paths: RoutePath[] }>('GET', `/api/map/route?dep=${encodeURIComponent(dep)}&arr=${encodeURIComponent(arr)}`),
  health: () => request<Health>('GET', '/api/health'),
  overview: () => request<Overview>('GET', '/api/overview'),
  watches: () => request<Watch[]>('GET', '/api/watches'),
  channels: () => request<Channel[]>('GET', '/api/channels'),
  members: () => request<Member[]>('GET', '/api/members'),
  logs: (level: LogLevel) => request<LogLine[]>('GET', `/api/logs?level=${level}`),
  search: searchTrains,
  createWatch: (body: unknown) => request<Watch>('POST', '/api/watches', body),
  watch: (id: number) => request<WatchDetail>('GET', `/api/watches/${id}`),
  setViewers: (id: number, viewers: string[]) => request('PUT', `/api/watches/${id}/viewers`, { viewers }),
  endWatch: (id: number) => request('POST', `/api/watches/${id}/end`),
  archiveWatch: (id: number) => request('POST', `/api/watches/${id}/archive`),
  pauseWatch: (id: number) => request('POST', `/api/watches/${id}/pause`),
  // pick 이 없으면 지금 채널 그대로 재개한다(수동 일시정지)
  resumeWatch: (id: number, pick?: ChannelPickBody) => request('POST', `/api/watches/${id}/resume`, pick),
  addWebhook: (name: string, url: string) => request<Channel>('POST', '/api/channels/webhook', { name, url }),
  deleteChannel: (id: number) => request('DELETE', `/api/channels/${id}`),
  testChannel: (id: number) => request('POST', `/api/channels/${id}/test`),
  logout: () => request('POST', '/auth/logout'),
}

export const ConfigContext = createContext<Config | null>(null)

export function useConfig(): Config {
  const c = useContext(ConfigContext)
  if (!c) throw new Error('config 가 아직 없습니다')
  return c
}

export const groupOf = (config: Config, type: string) =>
  config.trainGroups.find((g) => g.types.includes(type))?.name ?? '기타'

// 감시 구간이 걸친 날짜들(조회는 날짜 단위로 묶인다)
export function watchDates(start: string, end: string) {
  const out: string[] = []
  for (let d = dayjs(start).startOf('day'); !d.isAfter(dayjs(end), 'day'); d = d.add(1, 'day')) out.push(d.format('YYYY-MM-DD'))
  return out
}

// 열차의 출발·도착 일시
export function trainTimes(t: Train) {
  return { dep: dayjs(`${t.date} ${t.dep}`), arr: dayjs(`${t.arrDate} ${t.arr}`) }
}
