import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Alert, App, Button, Drawer, Empty, Flex, Grid, Skeleton, Tag, Typography, theme } from 'antd'
import { api, useConfig, type Channel, type Watch, type WatchDetail } from './api'
import { KIND_LABEL, ViewerSelect } from './ChannelPicker'
import { STATUS, endLabel, rangeLabel, typeLabels } from './WatchList'
import { RouteMap } from './StationMap'
import { Chip, StatusDot, TrainGlyph, fmtDateTime, fmtDay } from './ui'

const same = (a: string[], b: string[]) => a.length === b.length && [...a].sort().join() === [...b].sort().join()

// 감시 카드를 누르면 열리는 상세. 조건·현황은 보기만 하고, 자동 생성 채널의 열람자만 바꿀 수 있다
export default function WatchDrawer({ w: current, channels, onClose, onChanged }: {
  w: Watch | null
  channels: Channel[]
  onClose: () => void
  onChanged: () => Promise<void> | void
}) {
  const config = useConfig()
  const { token } = theme.useToken()
  const { message } = App.useApp()
  const mobile = !Grid.useBreakpoint().md
  const [detail, setDetail] = useState<WatchDetail | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [viewers, setViewers] = useState<string[]>([])
  const [saving, setSaving] = useState(false)
  // 닫히는 동안에도 내용이 비지 않게 마지막으로 연 감시를 그린다
  const last = useRef<Watch | null>(null)
  if (current) last.current = current
  const w = last.current
  const open = current !== null

  const id = w?.id
  const lastScan = w?.lastScan

  // 다른 감시를 열면 편집 중이던 열람자를 그 감시 값으로 되돌린다
  useEffect(() => {
    setDetail(null)
    setDetailError(null)
    setViewers(w?.viewers ?? [])
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  // 자리 목록은 목록 API 에 없어서 열 때와 조회가 한 번 돌 때마다 받는다
  useEffect(() => {
    if (id == null) return
    api.watch(id).then((d) => {
      setDetail(d)
      setDetailError(null)
    }, (e) => setDetailError(e instanceof Error ? e.message : '불러오지 못했습니다'))
  }, [id, lastScan])

  if (!w) return <Drawer open={false} />

  const chs = channels.filter((c) => w.channelIds.includes(c.id))
  const editable = !w.archivedAt && chs.some((c) => c.managed && !c.system && !c.broken)
  const status = w.archivedAt ? { label: '기록', tone: 'neutral' as const } : STATUS[w.status]

  const save = async () => {
    setSaving(true)
    try {
      await api.setViewers(w.id, viewers)
      await onChanged()
      message.success('열람자를 바꿨습니다.')
    } catch (e) {
      message.error(e instanceof Error ? e.message : '요청에 실패했습니다')
    } finally {
      setSaving(false)
    }
  }

  const section = (title: string, children: ReactNode) => (
    <section>
      <h3 style={{ fontSize: 13, fontWeight: 650, color: token.colorTextSecondary, margin: '0 0 10px' }}>{title}</h3>
      {children}
    </section>
  )
  // 이름표 | 값 두 칸 표
  const facts = (rows: [string, ReactNode][]) => (
    <div style={{ display: 'grid', gridTemplateColumns: '96px 1fr', rowGap: 10, columnGap: 12, fontSize: 14 }}>
      {rows.map(([k, v]) => (
        <div key={k} style={{ display: 'contents' }}>
          <span style={{ color: token.colorTextSecondary }}>{k}</span>
          <div style={{ minWidth: 0 }}>{v}</div>
        </div>
      ))}
    </div>
  )
  const chips = (xs: string[], accent?: boolean) => <Flex wrap gap={6}>{xs.map((x) => <Chip key={x} accent={accent}>{x}</Chip>)}</Flex>

  return (
    <Drawer
      open={open}
      onClose={onClose}
      size={mobile ? '100%' : 540}
      title={<span>감시 #{w.id}</span>}
      extra={<StatusDot tone={status.tone} pulse={w.status === 'active'}>{status.label}</StatusDot>}
    >
      <Flex vertical gap={28}>
        <div>
          <Flex align="center" gap={12}>
            <span style={{ fontSize: 26, fontWeight: 750, letterSpacing: '-0.03em' }}>{w.dep}</span>
            <span className="route-line"><TrainGlyph size={16} /></span>
            <span style={{ fontSize: 26, fontWeight: 750, letterSpacing: '-0.03em' }}>{w.arr}</span>
          </Flex>
          <Typography.Text type="secondary" className="num">{rangeLabel(w)}</Typography.Text>
          {w.status === 'paused' && <Alert type="warning" showIcon title={w.pauseReason ?? '일시정지됨'} style={{ marginTop: 12 }} />}
        </div>

        {section('조건', facts([
          ['열차 종류', chips(typeLabels(config, w.trainTypes))],
          ['좌석', chips(w.seats, true)],
          ['인원', `${w.passengers}명`],
          ['대상', w.trains ? chips(w.trains.map((t) => `${fmtDay(t.date)} ${t.dep} ${t.no}`)) : '조건에 맞는 열차 전체'],
        ]))}

        {section('노선', <RouteMap stations={config.stations} dep={w.dep} arr={w.arr} still routeOnly height={240} />)}

        {section('현황', facts([
          ['상태', w.status === 'ended' ? endLabel(w) : status.label],
          ['만든 곳', `${w.createdVia === 'discord' ? 'Discord 명령' : '웹'} · ${fmtDateTime(w.createdAt)}`],
          ['남은 대상', w.remaining == null ? '-' : `${w.remaining}편`],
          ['조회·발견·오류', <span className="num">{w.scans.toLocaleString()} · {w.found} · {w.errors}</span>],
          ['마지막 조회', <span className="num">{fmtDateTime(w.lastScan)}</span>],
          ...(w.lastError ? [['마지막 오류', <Typography.Text type="danger">{w.lastError}</Typography.Text>] as [string, ReactNode]] : []),
          ...(w.archivedAt ? [['채널 정리', fmtDateTime(w.archivedAt)] as [string, ReactNode]] : []),
        ]))}

        {section('지금 자리 있는 열차', detailError ? (
          <Alert type="error" showIcon title={detailError} />
        ) : !detail ? (
          <Skeleton active title={false} paragraph={{ rows: 2 }} />
        ) : detail.available.length ? (
          <Flex vertical gap={8}>
            {detail.available.map((a) => (
              <Flex key={`${a.date}-${a.no}`} justify="space-between" align="center">
                <span className="num" style={{ fontWeight: 600 }}>{fmtDay(a.date)} {a.no}</span>
                {chips(a.seats, true)}
              </Flex>
            ))}
          </Flex>
        ) : (
          <Typography.Text type="secondary">알린 뒤 아직 남아 있는 좌석이 없습니다.</Typography.Text>
        ))}

        {section('채널', chs.length ? (
          <Flex vertical gap={8}>
            {chs.map((c) => (
              <Flex key={c.id} justify="space-between" gap={8}>
                <span>{c.name}</span>
                <Flex gap={4}>
                  <Tag style={{ margin: 0, borderRadius: 6 }}>{c.managed ? '자동 생성' : KIND_LABEL[c.kind]}</Tag>
                  {c.broken && <Tag color="error" style={{ margin: 0, borderRadius: 6 }}>끊김</Tag>}
                </Flex>
              </Flex>
            ))}
          </Flex>
        ) : (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={w.archivedAt ? '채널을 정리했습니다' : '채널이 없습니다'} />
        ))}

        {section('열람자', editable ? (
          <Flex vertical gap={8}>
            <Typography.Text type="secondary" style={{ fontSize: 13 }}>
              자동 생성 채널을 읽을 수 있는 멤버입니다. 관리자는 항상 볼 수 있어 목록에 없습니다.
            </Typography.Text>
            <ViewerSelect value={viewers} onChange={setViewers} />
            <Flex justify="end" gap={8}>
              <Button disabled={same(viewers, w.viewers)} onClick={() => setViewers(w.viewers)}>되돌리기</Button>
              <Button
                type="primary"
                loading={saving}
                disabled={!config.botReady || same(viewers, w.viewers)}
                onClick={save}
              >
                저장
              </Button>
            </Flex>
            {!config.botReady && <Typography.Text type="warning">Discord 봇이 연결되면 저장할 수 있습니다.</Typography.Text>}
          </Flex>
        ) : (
          <Typography.Text type="secondary">
            열람자는 살아 있는 자동 생성 채널에만 있습니다. 직접 고른 채널은 Discord에서 권한을 관리하세요.
          </Typography.Text>
        ))}
      </Flex>
    </Drawer>
  )
}
