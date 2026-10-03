import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import {
  App, Button, Card, Checkbox, DatePicker, Empty, Flex, InputNumber, Radio, Table, Tag, TimePicker,
  Typography, theme,
} from 'antd'
import { EnvironmentOutlined, LoadingOutlined, NodeIndexOutlined, SearchOutlined, SwapOutlined } from '@ant-design/icons'
import dayjs, { type Dayjs } from 'dayjs'
import {
  api, groupOf, trainTimes, useConfig, type Basis, type Channel, type Config, type SeatState, type Train, type Watch,
} from './api'
import SearchSelect from './SearchSelect'
import StationMap from './StationMap'
import TrainRoute from './TrainRoute'
import ChannelPicker, { emptyPick, type ChannelPick } from './ChannelPicker'

const SEAT_TAG: Record<SeatState, [string, string]> = {
  avail: ['success', '예약 가능'],
  short: ['warning', '부족'],
  soldout: ['default', '매진'],
  none: ['default', '없음'],
  wait: ['processing', '조회 중'],
}

// 마지막으로 감시를 추가한 역과 열차 종류를 이 브라우저에 기억한다
const FORM_KEY = 'ktx.lastWatchForm'

function loadForm(config: Config) {
  const allTypes = config.trainGroups.flatMap((g) => g.types)
  const fallback = { dep: '', arr: '', types: config.trainGroups[0].types }
  try {
    const v = JSON.parse(localStorage.getItem(FORM_KEY) ?? 'null')
    if (!v) return fallback
    const types = Array.isArray(v.types) ? v.types.filter((t: unknown) => allTypes.includes(t as string)) : []
    return {
      dep: config.stations.includes(v.dep) ? v.dep : '',
      arr: config.stations.includes(v.arr) ? v.arr : '',
      types: types.length ? types : fallback.types,
    }
  } catch {
    return fallback
  }
}

function saveForm(dep: string, arr: string, types: string[]) {
  try {
    localStorage.setItem(FORM_KEY, JSON.stringify({ dep, arr, types }))
  } catch {
    // 저장소를 못 쓰는 환경이면 기억하지 않는다
  }
}

// busy: 조회 중. 조회가 끝났는데 아직 wait 면(오류로 멈춤) 좌석을 모르는 채로 남는다
const seatTag = (s: SeatState, busy: boolean) => (
  <Tag color={SEAT_TAG[s][0]} variant="filled" style={{ borderRadius: 999, margin: 0 }}>
    {s === 'wait' && !busy ? '미확인' : SEAT_TAG[s][1]}
  </Tag>
)

const trainKey = (t: Train) => `${t.date}#${t.no}`

// 좌석 조회 전에는 열차번호로 짐작한 이름을 흐리게 보여준다
const typeName = (t: Train) => t.type ?? <span style={{ opacity: 0.55 }}>{t.label ?? '열차'}</span>

// 같은 열차는 새로 온 값으로 바꾸고 출발 시각 순으로 둔다
const merge = (prev: Train[] | null, page: Train[]) => {
  const m = new Map((prev ?? []).map((t) => [trainKey(t), t]))
  for (const t of page) m.set(trainKey(t), t)
  return [...m.values()].sort((a, b) => `${a.date} ${a.dep}`.localeCompare(`${b.date} ${b.dep}`))
}

export default function AddWatch({ watches, channels, mobile, onCreated }: {
  watches: Watch[]
  channels: Channel[]
  mobile: boolean
  onCreated: () => void
}) {
  const { token } = theme.useToken()
  const { message } = App.useApp()
  const config = useConfig()
  const [saved] = useState(() => loadForm(config))
  const STATION_OPTIONS = useMemo(() => config.stations.map((s) => ({ value: s, label: s })), [config.stations])
  const MAX_WATCH_DAYS = config.maxWatchDays
  const CUTOFF_MIN = config.cutoffMinutes
  const [dep, setDep] = useState(saved.dep)
  const [arr, setArr] = useState(saved.arr)
  const [mapOpen, setMapOpen] = useState(false)
  const [routeOf, setRouteOf] = useState<Train | null>(null) // 운행 정보를 보는 열차
  // 출발역에서 갈아타지 않고 갈 수 있는 도착역. 시간표에 없는 역이면 제한하지 않는다
  const reachable = dep ? config.routes[dep] : undefined
  const ARR_OPTIONS = useMemo(
    () => STATION_OPTIONS.map((o) => ({ ...o, disabled: !!reachable && !reachable.includes(o.value) })),
    [STATION_OPTIONS, reachable],
  )
  const pickDep = (s: string) => {
    setDep(s)
    const ok = config.routes[s]
    if (arr && ok && !ok.includes(arr)) setArr('')
  }
  const swap = () => {
    setDep(arr)
    setArr(dep)
  }
  // 감시 구간: start 일시부터 end 일시까지 하나로 이어진 구간
  const [start, setStart] = useState<Dayjs>(() => dayjs().add(3, 'day').startOf('day'))
  const [end, setEnd] = useState<Dayjs>(() => dayjs().add(3, 'day').hour(23).minute(59).second(0).millisecond(0))
  const [basis, setBasis] = useState<Basis>('dep')
  const [types, setTypes] = useState<string[]>(saved.types)
  const [seats, setSeats] = useState<string[]>(['일반실'])
  const [passengers, setPassengers] = useState(1)
  const [all, setAll] = useState<Train[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [selected, setSelected] = useState<string[]>([])
  const [pick, setPick] = useState<ChannelPick>(emptyPick)
  // 조회한 조건. 감시 추가는 지금 조건으로 조회해서 맞는 열차가 있을 때만 된다(없는 노선·시간대 감시 방지).
  // 열차 종류는 받아온 결과를 화면에서 거르는 값이라 바뀌어도 다시 조회할 필요가 없다
  const [searchedFor, setSearchedFor] = useState('')

  const valid = end.isAfter(start)
  const days = valid ? end.startOf('day').diff(start.startOf('day'), 'day') + 1 : 0
  const hours = end.diff(start, 'hour')
  const lastDay = start.startOf('day').add(MAX_WATCH_DAYS - 1, 'day')

  const setStartPart = (d: Dayjs) => {
    setStart(d)
    // 시작을 옮겨 끝이 허용 범위(시작일~시작일+N-1일)를 벗어나면 끝 날짜를 시작일로 맞춘다
    const last = d.startOf('day').add(MAX_WATCH_DAYS - 1, 'day')
    if (end.isBefore(d, 'day') || end.isAfter(last, 'day')) setEnd(end.year(d.year()).month(d.month()).date(d.date()))
  }

  // 시간표로만 아는 열차(종류 모름)는 열차번호로 짐작한 묶음에 고른 종류가 있으면 보인다
  const inGroup = (t: Train, g: string) => (t.type ? groupOf(config, t.type) === g : !!t.groups?.includes(g))
  const typeOk = (t: Train) => (t.type ? types.includes(t.type)
    : config.trainGroups.some((g) => inGroup(t, g.name) && g.types.some((x) => types.includes(x))))

  // 열차 조건과 시간 범위로 거른 결과. cutoff 이내 출발 열차는 선택할 수 없다.
  const trains = useMemo(() => {
    if (!all) return []
    return all.filter((t) => {
      const at = trainTimes(t)[basis]
      return typeOk(t) && !at.isBefore(start) && !at.isAfter(end)
    })
  }, [all, basis, types, start, end])

  const tooLate = (t: Train) => trainTimes(t).dep.isBefore(dayjs().add(CUTOFF_MIN, 'minute'))
  const iso = (d: Dayjs) => d.format('YYYY-MM-DDTHH:mm')
  const query = JSON.stringify([dep, arr, iso(start), iso(end), basis, passengers])
  const fresh = all !== null && searchedFor === query
  const watchable = trains.filter((t) => !tooLate(t)).length
  const blocked = !fresh ? '열차 조회를 먼저 하세요.'
    : watchable === 0 ? (loading ? '열차를 조회하는 중입니다.' : '조건에 맞는 열차가 없어 감시할 수 없습니다.') : null

  // 진행 중인 조회. 끊으면 서버도 다음 페이지를 요청하지 않는다
  const ctrl = useRef<AbortController | null>(null)
  const stop = () => {
    ctrl.current?.abort()
    ctrl.current = null
    setLoading(false)
  }
  useEffect(() => () => ctrl.current?.abort(), [])
  // 조회 중에 조건을 바꾸면 이전 조건의 결과는 쓸모없으니 끊는다
  useEffect(() => {
    if (ctrl.current && searchedFor !== query) stop()
  }, [query, searchedFor])

  const search = async () => {
    if (!dep || !arr) return message.warning('출발역과 도착역을 고르세요.')
    if (!valid) return message.warning('끝 일시가 시작 일시보다 늦어야 합니다.')
    if (dep === arr) return message.warning('출발역과 도착역이 같습니다.')
    stop()
    const c = new AbortController()
    ctrl.current = c
    setLoading(true)
    setSelected([])
    setAll([])
    setSearchedFor(query)
    try {
      // 시간표를 먼저 보여주고, 코레일 페이지(10편)가 올 때마다 그 열차의 종류와 좌석으로 바꾼다
      const confirmed = new Set<string>()
      await api.search({ dep, arr, start: iso(start), end: iso(end), basis, passengers }, c.signal, {
        timetable: (rows) => setAll(rows),
        trains: (page) => {
          page.forEach((t) => confirmed.add(trainKey(t)))
          setAll((prev) => merge(prev, page))
        },
      })
      // 끝까지 받았으면 코레일에 없는 시간표 열차(그날 다니지 않는 열차)는 뺀다
      setAll((prev) => (prev ?? []).filter((t) => confirmed.has(trainKey(t))))
      setSelected((prev) => prev.filter((k) => confirmed.has(k)))
    } catch (e) {
      if (!c.signal.aborted) message.error(e instanceof Error ? e.message : '조회에 실패했습니다')
    } finally {
      if (ctrl.current === c) {
        ctrl.current = null
        setLoading(false)
      }
    }
  }

  const submit = async () => {
    if (!dep || !arr) return message.warning('출발역과 도착역을 고르세요.')
    if (!valid) return message.warning('끝 일시가 시작 일시보다 늦어야 합니다.')
    if (!types.length || !seats.length) return message.warning('열차 종류와 좌석을 하나 이상 고르세요.')
    if (watches.filter((w) => w.status !== 'ended').length >= config.maxActive)
      return message.error(`진행 중인 감시는 최대 ${config.maxActive}개까지입니다.`)
    if (blocked) return message.warning(blocked)
    if (pick.mode === 'manual' && !pick.channelIds.length) return message.warning('알림 채널을 하나 이상 지정하세요.')
    const picked = (all ?? []).filter((t) => selected.includes(trainKey(t))).map((t) => ({ date: t.date, no: t.no, dep: t.dep }))
    stop() // 조회 중에 감시를 추가하면 남은 조회는 필요 없다
    setSaving(true)
    try {
      const w = await api.createWatch({
        dep, arr, start: iso(start), end: iso(end), basis, trainTypes: types, seats, passengers,
        trains: picked.length ? picked : null, channel: pick,
      })
      saveForm(dep, arr, types)
      message.success(`감시 #${w.id}를 추가했습니다.`)
      onCreated()
    } catch (e) {
      message.error(e instanceof Error ? e.message : '감시를 추가하지 못했습니다')
    } finally {
      setSaving(false)
    }
  }

  const stationBox = (label: string, value: string, set: (v: string) => void, options = STATION_OPTIONS) => (
    <div style={{ flex: 1, minWidth: 0 }}>
      <Typography.Text type="secondary" style={{ fontSize: 13 }}>{label}</Typography.Text>
      <SearchSelect
        id={label === '출발' ? 'dep' : 'arr'}
        className={label === '도착' ? 'station-select align-end' : 'station-select'}
        value={value || undefined}
        onChange={set}
        options={options}
        match={(o, q) => String(o.label).includes(q)}
        searchPlaceholder="역 이름 검색"
        placeholder="역 선택"
        aria-label={`${label}역`}
        variant="borderless"
        suffixIcon={null}
        popupMatchSelectWidth={200}
        notFoundContent="KTX 정차역만 선택할 수 있습니다"
        style={{ width: '100%', height: 44 }}
      />
    </div>
  )

  // 카드를 누르면 선택이 바뀌므로 이 버튼은 누름을 카드로 넘기지 않는다
  const routeButton = (t: Train) => (
    <Button
      type="text" size="small" icon={<NodeIndexOutlined />} aria-label={`${t.no} 운행 정보`}
      onClick={(e) => {
        e.stopPropagation()
        setRouteOf(t)
      }}
      style={{ color: token.colorTextSecondary }}
    >
      노선
    </Button>
  )

  const section = (title: string, children: ReactNode, extra?: ReactNode) => (
    <Card title={title} extra={extra} styles={{ header: { borderBottom: 0 }, body: { paddingTop: 0 } }}>
      {children}
    </Card>
  )

  return (
    <Flex vertical gap={16} style={{ maxWidth: 760 }}>
      <Card styles={{ body: { padding: 20 } }}>
        <Flex align="center" gap={8}>
          {stationBox('출발', dep, pickDep)}
          <Button
            shape="circle" size="large" icon={<SwapOutlined />} aria-label="출발역과 도착역 바꾸기"
            style={{ flex: 'none', background: token.colorPrimaryBg, color: token.colorPrimary, border: 0 }}
            onClick={swap}
          />
          <div style={{ textAlign: 'right', flex: 1, minWidth: 0 }}>{stationBox('도착', arr, setArr, ARR_OPTIONS)}</div>
        </Flex>
        <Button type="text" size="small" icon={<EnvironmentOutlined />} onClick={() => setMapOpen(true)} style={{ marginTop: 4, color: token.colorTextSecondary }}>
          지도에서 선택
        </Button>
        {mapOpen && (
          <StationMap
            stations={config.stations} dep={dep} arr={arr} mobile={mobile} reachable={reachable}
            onPick={(which, s) => (which === 'dep' ? pickDep : setArr)(s)} onSwap={swap} onClose={() => setMapOpen(false)}
          />
        )}
        <div style={{ height: 1, background: token.colorBorderSecondary, margin: '14px 0' }} />
        <Flex vertical gap={10}>
          {([
            ['시작', start, setStartPart, (d: Dayjs) => d.isBefore(dayjs(), 'day') || d.isAfter(dayjs().add(30, 'day'))],
            ['끝', end, setEnd, (d: Dayjs) => d.isBefore(start, 'day') || d.isAfter(lastDay, 'day')],
          ] as const).map(([label, value, set, disabledDate]) => (
            <Flex key={label} align="center" gap={8}>
              <Typography.Text type="secondary" style={{ width: 28, flex: 'none', fontSize: 13 }}>{label}</Typography.Text>
              <DatePicker
                value={value}
                onChange={(d) => d && set(value.year(d.year()).month(d.month()).date(d.date()))}
                format="M/D (dd)"
                allowClear={false}
                inputReadOnly
                disabledDate={disabledDate}
                style={{ flex: 1, minWidth: 0 }}
                aria-label={`${label} 날짜`}
              />
              <TimePicker
                value={value}
                onChange={(t) => t && set(value.hour(t.hour()).minute(t.minute()))}
                // 직접 입력: "07:30" 또는 숫자만 "0730"
                format={['HH:mm', 'HHmm']}
                needConfirm={false}
                allowClear={false}
                style={{ flex: '0 0 104px' }}
                aria-label={`${label} 시각`}
              />
            </Flex>
          ))}
          <Typography.Text type={valid ? 'secondary' : 'danger'} style={{ fontSize: 13 }}>
            {valid
              ? `${start.format('M/D(dd) HH:mm')} ~ ${end.format(start.isSame(end, 'day') ? 'HH:mm' : 'M/D(dd) HH:mm')} 연속 ${hours >= 24 ? `${Math.floor(hours / 24)}일 ${hours % 24}시간` : `${hours}시간`}`
              : '끝 일시가 시작 일시보다 늦어야 합니다'}
          </Typography.Text>
          <Flex gap={12} wrap align="center">
            <Radio.Group
              optionType="button" buttonStyle="solid" value={basis} onChange={(e) => setBasis(e.target.value)}
              options={[{ value: 'dep', label: '출발 시각 기준' }, { value: 'arr', label: '도착 시각 기준' }]}
            />
            <InputNumber
              min={1} max={9} value={passengers} onChange={(v) => setPassengers(v ?? 1)}
              suffix="명" style={{ flex: '0 0 96px' }} aria-label="인원수"
            />
          </Flex>
        </Flex>
      </Card>

      {section('열차 조건', (
        <Flex vertical gap={14}>
          <div>
            <Typography.Text type="secondary" style={{ fontSize: 13 }}>열차 종류</Typography.Text>
            <Flex vertical gap={10} style={{ marginTop: 8 }}>
              {config.trainGroups.map((g) => {
                const on = g.types.filter((t) => types.includes(t))
                const rest = types.filter((t) => !g.types.includes(t))
                return (
                  <Flex key={g.name} gap={8} align="start" wrap>
                    <Checkbox
                      checked={on.length === g.types.length}
                      indeterminate={on.length > 0 && on.length < g.types.length}
                      onChange={(e) => setTypes(e.target.checked ? [...rest, ...g.types] : rest)}
                      style={{ fontWeight: 700, minWidth: 64 }}
                    >
                      {g.name}
                    </Checkbox>
                    <Checkbox.Group
                      value={on}
                      onChange={(v) => setTypes([...rest, ...v])}
                      options={g.types}
                      style={{ rowGap: 8, flex: 1 }}
                    />
                  </Flex>
                )
              })}
            </Flex>
          </div>
          <div>
            <Typography.Text type="secondary" style={{ fontSize: 13 }}>좌석</Typography.Text>
            <div style={{ marginTop: 6 }}>
              <Checkbox.Group value={seats} onChange={setSeats} options={config.seatClasses} />
            </div>
          </div>
          <Button type="primary" size="large" block shape="round" icon={<SearchOutlined />} loading={loading} onClick={search} style={{ height: 50 }}>
            열차 조회
          </Button>
        </Flex>
      ))}

      {all && section(
        '열차 선택',
        <Flex vertical gap={10}>
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>
            선택하지 않으면 조건에 맞는 열차 전체를 감시합니다. 출발 {CUTOFF_MIN}분 전이 지난 열차는 감시하지 않습니다.
          </Typography.Text>
          <Flex gap={8} wrap>
            {config.trainGroups.map((g) => {
              const keys = trains.filter((t) => inGroup(t, g.name) && !tooLate(t)).map(trainKey)
              if (!keys.length) return null
              const all = keys.every((k) => selected.includes(k))
              return (
                <Button
                  key={g.name}
                  shape="round"
                  size="middle"
                  type={all ? 'primary' : 'default'}
                  onClick={() => setSelected(all ? selected.filter((k) => !keys.includes(k)) : [...new Set([...selected, ...keys])])}
                >
                  {g.name} 전체 {keys.length}편
                </Button>
              )
            })}
          </Flex>
          {mobile ? (
            trains.length === 0 ? <Empty description={loading ? '열차를 조회하는 중입니다' : '조건에 맞는 열차가 없습니다'} /> : trains.map((t) => {
                const k = trainKey(t)
                const on = selected.includes(k)
                const off = tooLate(t)
                return (
                  <div
                    key={k}
                    onClick={() => !off && setSelected(on ? selected.filter((n) => n !== k) : [...selected, k])}
                    style={{
                      padding: 14, marginBottom: 8, borderRadius: 16, cursor: off ? 'not-allowed' : 'pointer',
                      opacity: off ? 0.45 : 1,
                      border: `1.5px solid ${on ? token.colorPrimary : token.colorBorderSecondary}`,
                      background: on ? token.colorPrimaryBg : token.colorBgContainer,
                    }}
                  >
                    <Flex justify="space-between" align="center">
                      <Flex gap={10} align="center">
                        <Checkbox checked={on} disabled={off} />
                        <div>
                          <div style={{ fontWeight: 700 }}>
                            {days > 1 && <span style={{ color: token.colorPrimary }}>{dayjs(t.date).format('M/D(dd)')} </span>}
                            {typeName(t)} {t.no}
                          </div>
                          <div style={{ fontSize: 18, fontWeight: 800 }}>{t.dep} <span style={{ color: token.colorTextTertiary }}>-</span> {t.arr}</div>
                        </div>
                      </Flex>
                      <Flex vertical gap={4} align="end" style={{ fontSize: 12 }}>
                        <span>일반 {seatTag(t.general, loading)}</span>
                        <span>특실 {seatTag(t.special, loading)}</span>
                        {routeButton(t)}
                      </Flex>
                    </Flex>
                  </div>
                )
            })
          ) : (
            <Table
              rowKey={trainKey}
              size="middle"
              pagination={false}
              dataSource={trains}
              locale={{ emptyText: loading ? '열차를 조회하는 중입니다' : '조건에 맞는 열차가 없습니다' }}
              rowSelection={{
                selectedRowKeys: selected,
                onChange: (keys) => setSelected(keys as string[]),
                getCheckboxProps: (t) => ({ disabled: tooLate(t) }),
              }}
              columns={[
                ...(days > 1 ? [{ title: '날짜', render: (_: unknown, t: Train) => dayjs(t.date).format('M/D(dd)') }] : []),
                { title: '열차', render: (_, t) => <b>{typeName(t)} {t.no}</b> },
                { title: '출발', dataIndex: 'dep' },
                { title: '도착', dataIndex: 'arr' },
                { title: '소요', render: (_, t) => `${Math.floor(t.minutes / 60)}시간 ${t.minutes % 60}분` },
                { title: '일반실', render: (_, t) => seatTag(t.general, loading) },
                { title: '특실', render: (_, t) => seatTag(t.special, loading) },
                { title: '', width: 88, render: (_, t) => routeButton(t) },
              ]}
            />
          )}
        </Flex>,
        <Tag variant="filled" color="blue" style={{ borderRadius: 999 }}>
          {loading && <LoadingOutlined style={{ marginRight: 6 }} />}
          {selected.length ? `${selected.length}편 선택` : `전체 ${trains.length}편`}
        </Tag>,
      )}

      {routeOf && (
        <TrainRoute
          // 조회한 뒤 역을 바꿨을 수 있어 조회한 조건의 역을 쓴다
          train={routeOf} name={typeName(routeOf)} dep={JSON.parse(searchedFor)[0]} arr={JSON.parse(searchedFor)[1]} mobile={mobile}
          onClose={() => setRouteOf(null)}
        />
      )}

      {section('알림 채널', <ChannelPicker value={pick} onChange={setPick} channels={channels} />)}

      {blocked && (
        <Typography.Text type={fresh && !loading ? 'danger' : 'secondary'} style={{ textAlign: 'center' }}>
          {all && !fresh ? '조건이 바뀌었습니다. 열차를 다시 조회하세요.' : blocked}
        </Typography.Text>
      )}
      <Button
        type="primary" size="large" block shape="round" onClick={submit} loading={saving} disabled={!!blocked}
        style={{ height: 54, fontSize: 16 }}
      >
        감시 추가
      </Button>
    </Flex>
  )
}
