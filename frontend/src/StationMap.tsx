import { useCallback, useEffect, useRef, useState } from 'react'
import { Button, Flex, Modal, Result, Segmented, Spin, Typography, theme } from 'antd'
import { CompressOutlined, MinusOutlined, PlusOutlined, SwapOutlined } from '@ant-design/icons'
import { api, useConfig, type RoutePath, type StationMapData } from './api'

type Side = StationMapData['stations'][number]['label']

// 출발→도착 경로는 시간표가 바뀌기 전까지 같으니 한 번 받은 건 다시 받지 않는다
const routes = new Map<string, RoutePath[]>()
const MAX_PATHS = 3 // 지도에 그리고 아래에 요약하는 경로 수(편수 많은 순)

const COS = Math.cos((36.3 * Math.PI) / 180) // 경도 1도를 위도 1도 길이에 맞춘다
const S = 200 // 위도 1도의 지도 단위 길이
const PAD = 40

// 백엔드 지도 데이터(역 위치, 선로, 경계선)를 지도 단위 좌표로 바꾼다
function build(d: StationMapData) {
  const lats = d.stations.map((s) => s.lat)
  const lons = d.stations.map((s) => s.lon)
  const lat1 = Math.max(...lats)
  const lon0 = Math.min(...lons)
  return {
    W: (Math.max(...lons) - lon0) * S * COS + 2 * PAD,
    H: (lat1 - Math.min(...lats)) * S + 2 * PAD,
    P: Object.fromEntries(d.stations.map((s) => [s.name, [PAD + (s.lon - lon0) * S * COS, PAD + (lat1 - s.lat) * S]])) as
      Record<string, [number, number]>,
    side: Object.fromEntries(d.stations.map((s) => [s.name, s.label])) as Record<string, Side>,
    // 자리가 모자라면 이 역들 이름부터 자리를 잡는다
    rank: d.rank,
    lines: d.lines,
    provinces: d.provinces,
    metros: d.metros,
    // 경계선 path 좌표(x = 경도*S*COS, y = -위도*S)를 지도 좌표로 옮긴다
    boundary: `translate(${PAD - lon0 * S * COS},${PAD + lat1 * S})`,
  }
}
// 고정 데이터라 한 번 받으면 페이지를 새로 열 때까지 다시 받지 않는다. 실패하면 다음에 열 때 다시 받는다
let G: ReturnType<typeof build> | null = null
let loading: Promise<unknown> | null = null
const loadMap = () =>
  (loading ??= api.map().then((d) => (G = build(d))).catch((e) => {
    loading = null
    throw e
  }))

const FONT = 12
// 이름을 놓는 자리: 오른쪽, 왼쪽, 위, 아래와 대각선 네 곳(역이 몰린 곳에서 네 방향이 다 막힐 때)
type Pos = Side | 'tr' | 'tl' | 'br' | 'bl'
const POSITIONS: Pos[] = ['r', 'l', 't', 'b', 'tl', 'tr', 'bl', 'br']
const GAPS = [0, 6, 12] // 이름을 역 점에서 더 떨어뜨려 보는 거리(px)
// 역 점 반지름 rad 만큼 떨어뜨린 이름 기준점(dx, dy 는 글자 기준선 위치)
const label = (side: Pos, rad: number): { dx: number; dy: number; anchor: 'start' | 'end' | 'middle' } =>
  side === 'r' ? { dx: rad + 3, dy: 4, anchor: 'start' }
    : side === 'l' ? { dx: -rad - 3, dy: 4, anchor: 'end' }
      : side === 't' ? { dx: 0, dy: -rad - 4, anchor: 'middle' }
        : side === 'b' ? { dx: 0, dy: rad + 13, anchor: 'middle' }
          : side === 'tr' ? { dx: rad + 1, dy: -rad - 1, anchor: 'start' }
            : side === 'tl' ? { dx: -rad - 1, dy: -rad - 1, anchor: 'end' }
              : side === 'br' ? { dx: rad + 1, dy: rad + 11, anchor: 'start' }
                : { dx: -rad - 1, dy: rad + 11, anchor: 'end' }
const textWidth = (s: string) => [...s].reduce((w, c) => w + (c.charCodeAt(0) > 0x3000 ? FONT : FONT * 0.65), 0)
type Box = [number, number, number, number]
const hit = (a: Box, b: Box) => a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3]
type View = { k: number; x: number; y: number }

// 지도에 덧그리는 선로 구간. 배열 뒤쪽이 위에 그려진다
export type Highlight = { track: string[]; width: number; opacity: number }

// 역 지도 본체. still 이면 확대·이동 없이 focus 역들이 들어가게 맞춘 그림만 보여준다(스크롤되는 서랍 안에서 쓴다)
export function MapView({ stations, dep, arr, highlights = [], stops, only, focus, priority = [], off = () => false, onTap, still, height }: {
  stations: string[]
  dep: string
  arr: string
  highlights?: Highlight[]
  stops?: Set<string> // 테두리를 굵게 그리는 역(경로 열차가 서는 역)
  only?: Set<string> // 있으면 이 역들과 출발·도착역만 이름을 그리고 나머지는 흐리게(still 이면 점도 숨긴다)
  focus?: string[] // 처음(still 이면 늘) 이 역들이 다 보이게 맞춘다. 없으면 전체
  priority?: string[] // 출발·도착역 다음으로 이름 자리를 먼저 잡는 역
  off?: (s: string) => boolean // 흐리게 두고 못 누르는 역
  onTap?: (s: string) => void // 없으면 역을 누를 수 없다
  still?: boolean
  height: number | string
}) {
  const { token } = theme.useToken()
  const [, setLoaded] = useState(!!G)
  const [error, setError] = useState('')
  useEffect(() => {
    if (!G) loadMap().then(() => setLoaded(true), (e) => setError(e instanceof Error ? e.message : '지도를 불러오지 못했습니다'))
  }, [])
  // 관련 없는 역(only 밖)
  const rest = (s: string) => !!only && !only.has(s) && s !== dep && s !== arr
  const shown = G ? stations.filter((s) => G!.P[s] && !(still && rest(s))) : []
  const box = useRef<HTMLDivElement>(null)
  const size = useRef<[number, number]>([0, 0])
  // k 가 0 이면 아직 크기를 모르는 상태다. 크기를 재면 focus 가 보이게 맞춘다
  const [view, setView] = useState<View>({ k: 0, x: 0, y: 0 })
  const pointers = useRef(new Map<number, [number, number]>())
  const moved = useRef(0)
  const downOn = useRef<string | null>(null)
  const focusRef = useRef(focus)
  focusRef.current = focus

  // 가장 작은 배율은 지도 전체가 들어가는 배율이다. 이동은 가장자리 역도 화면 안쪽으로 끌어올 수 있게
  // 지도 가장자리가 화면 1/3 까지 들어오는 것까지 허용한다. 확대 중심이 화면 가장자리 쪽이어도 밀리지 않는다
  const clamp = (v: View): View => {
    const [cw, ch] = size.current
    const { W, H } = G!
    const fit = Math.min(cw / W, ch / H)
    const k = Math.min(Math.max(v.k, fit), Math.max(6, fit * 2))
    const pan = (t: number, len: number, view: number) =>
      Math.min(Math.max(view - len, view / 3), Math.max(Math.min(0, (view * 2) / 3 - len), t))
    return { k, x: pan(v.x, W * k, cw), y: pan(v.y, H * k, ch) }
  }
  // 전체 지도를 가운데에 맞춘다
  const fitAll = (): View => {
    const [cw, ch] = size.current
    const { W, H } = G!
    const k = Math.min(cw / W, ch / H)
    return { k, x: (cw - W * k) / 2, y: (ch - H * k) / 2 }
  }
  // names 역들이 가장자리에서 조금 떨어져 다 보이게 맞춘다
  const fitTo = (names?: string[]): View => {
    const pts = (names ?? []).map((s) => G!.P[s]).filter(Boolean)
    if (!pts.length) return fitAll()
    const [cw, ch] = size.current
    const xs = pts.map((p) => p[0])
    const ys = pts.map((p) => p[1])
    const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)]
    const m = 60 // 이름이 들어갈 여백(px)
    const k = clamp({ k: Math.min((cw - 2 * m) / Math.max(x1 - x0, 1), (ch - 2 * m) / Math.max(y1 - y0, 1)), x: 0, y: 0 }).k
    return clamp({ k, x: cw / 2 - ((x0 + x1) / 2) * k, y: ch / 2 - ((y0 + y1) / 2) * k })
  }
  // 화면 좌표 (px, py) 에 있던 지점을 (qx, qy) 로 옮기며 f 배 확대한다
  const zoomAt = (v: View, f: number, px: number, py: number, qx = px, qy = py) => {
    const k = clamp({ ...v, k: v.k * f }).k
    return clamp({ k, x: qx - ((px - v.x) * k) / v.k, y: qy - ((py - v.y) * k) / v.k })
  }
  const zoom = (f: number) => setView((v) => zoomAt(v, f, size.current[0] / 2, size.current[1] / 2))

  // Modal 은 내용을 이 컴포넌트의 effect 보다 늦게 붙이므로, 지도 상자가 실제로 생길 때(callback ref) 크기 감시와 휠을 단다
  const attach = useCallback((el: HTMLDivElement | null) => {
    box.current = el
    if (!el) return
    const ro = new ResizeObserver(([e]) => {
      size.current = [e.contentRect.width, e.contentRect.height]
      setView((v) => (v.k && !still ? clamp(v) : fitTo(focusRef.current)))
    })
    ro.observe(el)
    if (still) return () => ro.disconnect()
    // React 의 onWheel 은 passive 라 페이지 스크롤을 막을 수 없어서 직접 단다
    const wheel = (e: WheelEvent) => {
      e.preventDefault()
      const r = el.getBoundingClientRect()
      setView((v) => zoomAt(v, Math.exp(-e.deltaY * 0.002), e.clientX - r.left, e.clientY - r.top))
    }
    el.addEventListener('wheel', wheel, { passive: false })
    return () => {
      ro.disconnect()
      el.removeEventListener('wheel', wheel)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [still])

  const tap = (s: string) => !off(s) && onTap?.(s)

  // 한 손가락(마우스)은 이동, 두 손가락은 확대. 거의 움직이지 않고 뗀 탭만 역 선택으로 친다
  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.currentTarget.setPointerCapture(e.pointerId)
    pointers.current.set(e.pointerId, [e.clientX, e.clientY])
    if (pointers.current.size === 1) {
      moved.current = 0
      downOn.current = (e.target as Element).closest('[data-station]')?.getAttribute('data-station') ?? null
    }
  }
  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const p = pointers.current
    const prev = p.get(e.pointerId)
    if (!prev) return
    const a = [...p.values()]
    p.set(e.pointerId, [e.clientX, e.clientY])
    const b = [...p.values()]
    if (b.length === 1) {
      const dx = e.clientX - prev[0]
      const dy = e.clientY - prev[1]
      moved.current += Math.hypot(dx, dy)
      setView((v) => clamp({ k: v.k, x: v.x + dx, y: v.y + dy }))
    } else if (b.length === 2) {
      moved.current = Infinity
      const r = box.current!.getBoundingClientRect()
      const mid = (q: typeof a) => [(q[0][0] + q[1][0]) / 2 - r.left, (q[0][1] + q[1][1]) / 2 - r.top]
      const dist = (q: typeof a) => Math.hypot(q[0][0] - q[1][0], q[0][1] - q[1][1]) || 1
      const [ax, ay] = mid(a)
      const [bx, by] = mid(b)
      setView((v) => zoomAt(v, dist(b) / dist(a), ax, ay, bx, by))
    }
  }
  const onPointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.type === 'pointerup' && pointers.current.size === 1 && moved.current < 6 && downOn.current) tap(downOn.current)
    pointers.current.delete(e.pointerId)
  }

  // still 이면 focus 가 늦게 와도(경로를 받은 뒤) 다시 맞춘다
  const { k, x, y } = still && G && view.k ? fitTo(focus) : view
  const at = (s: string): [number, number] => [x + G!.P[s][0] * k, y + G!.P[s][1] * k]

  // 역 이름 자리 잡기: 선택한 역, priority, 편수 많은 역(rank), 나머지 순으로 정해 둔 쪽부터 여덟 방향(POSITIONS)을 시도하고,
  // 다른 이름, 역 점, 확대 버튼과 겹치거나 화면 밖으로 나가면 그 이름은 숨긴다(확대하면 자리가 생겨 나타난다).
  // 선택한 역 이름은 자리가 없어도 정해 둔 쪽에 그린다
  const labels: { s: string; x: number; y: number; anchor: 'start' | 'end' | 'middle' }[] = []
  if (G && k) {
    const [cw, ch] = size.current
    const radius = (s: string) => (s === dep || s === arr ? 6 : 4)
    const taken: Box[] = shown.map((s) => {
      const [a, b] = at(s)
      const r = radius(s)
      return [a - r, b - r, a + r, b + r]
    })
    taken.push([0, ch - 20, 190, ch]) // 출처 표시
    if (!still) taken.push([cw - 50, 0, cw, 128]) // 확대 버튼
    // 흐린 역은 이름 자리를 맨 나중에 잡는다(자리가 남으면 보인다)
    const order = [dep, arr, ...priority, ...G.rank, ...shown]
    for (const s of new Set([...order.filter((s) => !off(s)), ...order.filter(off)])) {
      if (!shown.includes(s) || rest(s)) continue
      const [a, b] = at(s)
      if (a < 0 || b < 0 || a > cw || b > ch) continue
      const w = textWidth(s)
      const sides = [...new Set<Pos>([G.side[s] ?? 'r', ...POSITIONS])]
      const boxOf = (side: Pos, gap = 0): Box => {
        const l = label(side, radius(s) + gap)
        const left = l.anchor === 'start' ? a + l.dx : l.anchor === 'end' ? a + l.dx - w : a - w / 2
        return [left, b + l.dy - FONT - 1, left + w, b + l.dy + 4] // 글자 줄 높이(약 16px)
      }
      const fits = (r: Box) => r[0] >= 2 && r[1] >= 2 && r[2] <= cw - 2 && r[3] <= ch - 2 && !taken.some((t) => hit(t, r))
      const clash = (d: Pos) => taken.filter((t) => hit(t, boxOf(d))).length
      // 바로 옆 여덟 자리가 다른 역 점에 다 막히면(역이 몰린 곳) 한 칸, 두 칸 더 떨어진 자리도 본다
      let place: [Pos, number] | undefined
      for (const gap of GAPS) {
        const side = sides.find((d) => fits(boxOf(d, gap)))
        if (side) {
          place = [side, gap]
          break
        }
      }
      if (!place && (s === dep || s === arr)) place = [sides.reduce((m, d) => (clash(d) < clash(m) ? d : m)), 0]
      if (!place) continue
      const l = label(place[0], radius(s) + place[1])
      taken.push(boxOf(...place))
      labels.push({ s, x: a + l.dx, y: b + l.dy, anchor: l.anchor })
    }
  }
  const frame = { borderRadius: token.borderRadiusLG, background: token.colorFillQuaternary }
  const colorOf = (s: string) => (s === dep ? token.colorPrimary : s === arr ? token.colorError : undefined)
  const cursor = (s: string) => (!onTap ? undefined : off(s) ? 'not-allowed' : 'pointer')

  if (!G) {
    return (
      <Flex align="center" justify="center" style={{ ...frame, height }}>
        {error ? <Result status="warning" title="지도를 불러오지 못했습니다" subTitle={`${error} 창을 닫고 다시 열어 보세요.`} /> : <Spin />}
      </Flex>
    )
  }
  return (
    <div style={{ position: 'relative' }}>
      <div
        ref={attach}
        {...(!still && { onPointerDown, onPointerMove, onPointerUp, onPointerCancel: onPointerUp })}
        style={{ ...frame, height, overflow: 'hidden', userSelect: 'none', ...(!still && { touchAction: 'none', cursor: 'grab' }) }}
      >
        {k > 0 && (
          <svg width="100%" height="100%" style={{ display: 'block' }}>
            <g transform={`translate(${x},${y}) scale(${k})`}>
              <g transform={G.boundary} fill="none" stroke={token.colorBorder} strokeLinejoin="round">
                <path d={G.provinces} vectorEffect="non-scaling-stroke" strokeWidth={1} />
                <path d={G.metros} vectorEffect="non-scaling-stroke" strokeWidth={1} strokeDasharray="3 3" />
              </g>
              {G.lines.map((line, i) => (
                <polyline
                  key={i} points={line.map((s) => G!.P[s].join(',')).join(' ')} vectorEffect="non-scaling-stroke"
                  fill="none" stroke={token.colorTextQuaternary} strokeWidth={2.5} strokeLinecap="round" strokeLinejoin="round"
                />
              ))}
              {highlights.map((h, i) => (
                <polyline
                  key={`hl${i}`} points={h.track.filter((s) => G!.P[s]).map((s) => G!.P[s].join(',')).join(' ')}
                  vectorEffect="non-scaling-stroke" fill="none" stroke={token.colorPrimary} strokeOpacity={h.opacity}
                  strokeWidth={h.width} strokeLinecap="round" strokeLinejoin="round"
                />
              ))}
            </g>
            {[...shown.filter((s) => !colorOf(s)), ...shown.filter((s) => colorOf(s))].map((s) => {
              const [a, b] = at(s)
              const color = colorOf(s)
              const on = stops?.has(s)
              return (
                <g
                  key={s} data-station={s}
                  {...(onTap && { role: 'button', tabIndex: 0, 'aria-label': s, 'aria-disabled': off(s) })}
                  className="station-dot"
                  style={{ cursor: cursor(s), opacity: off(s) || rest(s) ? 0.3 : 1 }}
                  onKeyDown={(e) => {
                    if (e.key !== 'Enter' && e.key !== ' ') return
                    e.preventDefault()
                    tap(s)
                  }}
                >
                  <title>{s}</title>
                  <circle cx={a} cy={b} r={12} fill="transparent" />
                  <circle
                    cx={a} cy={b} r={color ? 6 : 4}
                    fill={color ?? token.colorBgContainer} stroke={color ?? (on ? token.colorPrimary : token.colorText)}
                    strokeWidth={on ? 2.5 : 1.5}
                  />
                </g>
              )
            })}
            {labels.map((l) => {
              const color = colorOf(l.s)
              return (
                <text
                  key={l.s} data-station={l.s} x={l.x} y={l.y} textAnchor={l.anchor} fontSize={FONT}
                  fontWeight={color ? 800 : 500} fill={color ?? token.colorText}
                  stroke={token.colorBgContainer} strokeWidth={3} paintOrder="stroke"
                  style={{ cursor: cursor(l.s), opacity: off(l.s) ? 0.3 : 1 }}
                >
                  {l.s}
                </text>
              )
            })}
          </svg>
        )}
      </div>
      <Typography.Text
        type="secondary" style={{ position: 'absolute', left: 10, bottom: 4, fontSize: 10, pointerEvents: 'none' }}
      >
        역 위치 © OpenStreetMap · 경계 통계청
      </Typography.Text>
      {!still && (
        <Flex vertical gap={6} style={{ position: 'absolute', top: 10, right: 10 }}>
          <Button icon={<PlusOutlined />} aria-label="확대" onClick={() => zoom(1.5)} />
          <Button icon={<MinusOutlined />} aria-label="축소" onClick={() => zoom(1 / 1.5)} />
          <Button icon={<CompressOutlined />} aria-label="전체 보기" onClick={() => setView(fitAll())} />
        </Flex>
      )}
    </div>
  )
}

// 출발→도착 직통 열차 경로를 그린 지도와 경로별 편수 요약
// routeOnly 면 경로 열차가 서는 역만 이름을 그린다(MapView only)
export function RouteMap({ stations, dep, arr, routeOnly, ...rest }: Omit<Parameters<typeof MapView>[0], 'highlights' | 'stops' | 'only'> & {
  routeOnly?: boolean
}) {
  const { token } = theme.useToken()
  const config = useConfig()
  const [paths, setPaths] = useState<RoutePath[] | null>(null)
  useEffect(() => {
    const key = `${dep}→${arr}`
    setPaths(routes.get(key) ?? null)
    if (!dep || !arr || routes.has(key)) return
    let live = true
    api.route(dep, arr).then((r) => {
      routes.set(key, r.paths)
      if (live) setPaths(r.paths)
    }, () => {}) // 경로는 덤이라 못 받으면 그리지 않는다
    return () => {
      live = false
    }
  }, [dep, arr])
  const drawn = (paths ?? []).slice(0, MAX_PATHS)
  const stops = new Set(drawn.flatMap((p) => p.stops))
  // 묶음별 편수를 "KTX 하루 약 37편 · 새마을·무궁화 주 3편" 처럼 쓴다. 기타 묶음은 세부 종류 이름으로
  const groupName = (g: string) => (g === '기타' ? config.trainGroups.find((x) => x.name === g)?.types.join('·') ?? g : g)
  const perDay = (n: number) => (n >= 7 ? `하루 약 ${Math.round(n / 7)}편` : `주 ${n}편`)
  const describe = (p: RoutePath) =>
    Object.entries(p.weekly).sort((a, b) => b[1] - a[1]).map(([g, n]) => `${groupName(g)} ${perDay(n)}`).join(' · ')
  // 편수 많은 것이 굵게, 맨 위에
  const style = (i: number) => ({ width: i ? 3 : 6, opacity: i ? 0.45 : 0.9 })

  return (
    <>
      <MapView
        {...rest} stations={stations} dep={dep} arr={arr}
        highlights={drawn.map((p, i) => ({ track: p.track, ...style(i) })).reverse()}
        stops={stops}
        only={routeOnly && paths ? stops : undefined}
        focus={rest.focus ?? (dep && arr ? [dep, arr, ...drawn.flatMap((p) => p.track)] : undefined)}
      />
      {dep && arr && paths && (
        <Flex vertical gap={4} style={{ marginTop: 10, fontSize: 13 }}>
          {paths.length === 0 ? (
            <Typography.Text type="secondary">시간표에 {dep}→{arr} 직통 열차가 없습니다.</Typography.Text>
          ) : (
            drawn.map((p, i) => (
              <Flex key={i} gap={8} align="center">
                <svg width={22} height={8} style={{ flex: 'none' }} aria-hidden>
                  <line
                    x1={3} y1={4} x2={19} y2={4} stroke={token.colorPrimary} strokeOpacity={style(i).opacity}
                    strokeWidth={style(i).width} strokeLinecap="round"
                  />
                </svg>
                <Typography.Text>{describe(p)}</Typography.Text>
              </Flex>
            ))
          )}
          {paths.length > MAX_PATHS && (
            <Typography.Text type="secondary">그 밖에 경로 {paths.length - MAX_PATHS}개</Typography.Text>
          )}
        </Flex>
      )}
    </>
  )
}

// 지도에서 출발·도착역을 고르는 창
export default function StationMap({ stations, dep, arr, mobile, reachable, onPick, onSwap, onClose }: {
  stations: string[]
  reachable?: string[] // 출발역에서 직통으로 갈 수 있는 역. 도착역을 고를 때 나머지는 흐리게 두고 못 누른다
  dep: string
  arr: string
  mobile: boolean
  onPick: (which: 'dep' | 'arr', station: string) => void
  onSwap: () => void
  onClose: () => void
}) {
  // 출발 → 도착 → 다시 출발 순으로 돈다. 둘 다 정해져 있으면 출발부터 다시 고른다
  const [target, setTarget] = useState<'dep' | 'arr'>(dep && !arr ? 'arr' : 'dep')
  const off = (s: string) => target === 'arr' && !!reachable && s !== dep && !reachable.includes(s)

  return (
    <Modal
      open centered width={mobile ? '100%' : 720} title="지도에서 역 선택" onCancel={onClose}
      footer={<Button type="primary" shape="round" onClick={onClose}>완료</Button>}
    >
      <Flex gap={8} align="center">
        <Segmented
          block value={target} onChange={setTarget} style={{ flex: 1 }}
          options={[
            { value: 'dep', label: `출발 ${dep || '-'}` },
            { value: 'arr', label: `도착 ${arr || '-'}` },
          ]}
        />
        <Button shape="circle" icon={<SwapOutlined />} aria-label="출발역과 도착역 바꾸기" onClick={onSwap} />
      </Flex>
      <Typography.Text type="secondary" style={{ display: 'block', fontSize: 13, margin: '8px 0' }}>
        {target === 'dep' ? '출발역' : '도착역'}을 누르세요. {mobile ? '두 손가락으로 확대' : '휠로 확대'}, 끌어서 이동.
      </Typography.Text>
      <RouteMap
        stations={stations} dep={dep} arr={arr} off={off} focus={[]}
        // 휴대폰에서도 아래 완료 버튼이 화면 안에 들어오게 남는 높이만 쓴다
        height={mobile ? 'max(260px, calc(100dvh - 330px))' : 'min(70vh, 720px)'}
        onTap={(s) => {
          onPick(target, s)
          setTarget(target === 'dep' ? 'arr' : 'dep')
        }}
      />
    </Modal>
  )
}
