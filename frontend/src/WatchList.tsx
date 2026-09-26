import { Fragment, useEffect, useState } from 'react'
import { Alert, App, Button, Card, Col, Empty, Flex, Grid, Modal, Popconfirm, Row, Segmented, Space, Tag, Typography, theme } from 'antd'
import {
  CheckCircleFilled, CloseCircleFilled, ExclamationCircleFilled, RightOutlined,
  ClockCircleOutlined, DeleteOutlined, PlayCircleOutlined, PlusOutlined, StopOutlined, SwapRightOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'
import { api, useConfig, type Channel, type Health, type Overview, type Watch } from './api'
import ChannelPicker, { emptyPick, type ChannelPick } from './ChannelPicker'
import WatchDrawer from './WatchDrawer'

interface Props {
  watches: Watch[]
  channels: Channel[]
  health: Health | null
  overview: Overview | null
  onChanged: () => void
  onAdd: () => void
  onLogs: () => void
}

type View = 'live' | 'ended' | 'archived'

// 하나로 이어진 감시 구간. 같은 날이면 날짜를 한 번만 쓴다
export function rangeLabel(w: Pick<Watch, 'start' | 'end' | 'basis'>) {
  const s = dayjs(w.start)
  const e = dayjs(w.end)
  const kind = w.basis === 'dep' ? '출발' : '도착'
  return s.isSame(e, 'day')
    ? `${s.format('M월 D일 (dd) HH:mm')}~${e.format('HH:mm')} ${kind}`
    : `${s.format('M/D(dd) HH:mm')} ~ ${e.format('M/D(dd) HH:mm')} ${kind}`
}

function until(t: number) {
  const s = Math.max(0, Math.round((t - Date.now()) / 1000))
  return s < 60 ? `${s}초 뒤` : `${Math.round(s / 60)}분 뒤`
}

// 서버 값은 5초마다 오지만 남은 시간은 1초마다 다시 그린다
function Countdown({ at }: { at: number }) {
  const [, tick] = useState(0)
  useEffect(() => {
    const t = setInterval(() => tick((n) => n + 1), 1000)
    return () => clearInterval(t)
  }, [])
  return <>{until(at)}</>
}

function ago(t: number) {
  const s = Math.round((Date.now() - t) / 1000)
  if (s < 60) return `${s}초 전`
  if (s < 3600) return `${Math.round(s / 60)}분 전`
  if (s < 86400) return `${Math.round(s / 3600)}시간 전`
  return `${Math.round(s / 86400)}일 전`
}

export default function WatchList({ watches, channels, health, overview, onChanged, onAdd, onLogs }: Props) {
  const config = useConfig()
  const { token } = theme.useToken()
  const { message } = App.useApp()
  const mobile = !Grid.useBreakpoint().md
  const [view, setView] = useState<View>('live')
  const [resuming, setResuming] = useState<Watch | null>(null)
  const [pick, setPick] = useState<ChannelPick>(emptyPick)
  const [busy, setBusy] = useState(false)
  const [openId, setOpenId] = useState<number | null>(null)

  const live = watches.filter((w) => w.status !== 'ended')
  const ended = watches.filter((w) => w.status === 'ended' && !w.archivedAt)
  const archived = watches.filter((w) => w.archivedAt)
  const shown = { live, ended, archived }[view]

  // 서버에 요청하고 성공하면 목록을 새로 받는다. 실패 문구는 서버가 준 그대로 보여준다
  const act = async (fn: () => Promise<unknown>, done: string) => {
    setBusy(true)
    try {
      await fn()
      message.success(done)
      onChanged()
      return true
    } catch (e) {
      message.error(e instanceof Error ? e.message : '요청에 실패했습니다')
      return false
    } finally {
      setBusy(false)
    }
  }

  const end = (w: Watch) => act(() => api.endWatch(w.id), '감시를 종료했습니다.')

  // 자동 생성 채널만 지우고 감시 기록은 기록 탭으로 옮겨 계속 볼 수 있게 한다
  const archive = (w: Watch) => act(() => api.archiveWatch(w.id), '자동 생성 채널을 삭제하고 감시를 기록 탭으로 옮겼습니다.')

  const resume = async () => {
    if (!resuming) return
    if (pick.mode === 'manual' && !pick.channelIds.length) return message.warning('알림 채널을 하나 이상 지정하세요.')
    if (await act(() => api.resumeWatch(resuming.id, pick), '감시를 재개했습니다.')) setResuming(null)
  }

  return (
    <Flex vertical gap={16}>
      {health && <HealthBanner h={health} onClick={onLogs} />}
      <Card
        style={{ background: `linear-gradient(135deg, ${token.colorPrimary}, #5b8cff)`, border: 0 }}
        styles={{ body: { color: '#fff' } }}
      >
        <Flex justify="space-between" align="center" wrap gap={12}>
          <div>
            <div style={{ opacity: 0.85, fontSize: 13 }}>진행 중인 감시</div>
            <div style={{ fontSize: 30, fontWeight: 800, lineHeight: 1.2 }}>
              {live.length}
              <span style={{ fontSize: 16, opacity: 0.8, fontWeight: 600 }}> / {config.maxActive}</span>
            </div>
          </div>
          <Flex gap={20} style={{ fontSize: 13 }}>
            <div>
              <div style={{ opacity: 0.85 }}>조회 묶음</div>
              <div style={{ fontSize: 18, fontWeight: 700 }}>{overview?.groups ?? 0}개</div>
            </div>
            <div>
              <div style={{ opacity: 0.85 }}>다음 조회</div>
              <div style={{ fontSize: 18, fontWeight: 700 }}>{/* 조회할 감시가 없으면 루프는 돌아도 요청은 없으므로 '-' */}
                {live.some((w) => w.status === 'active') && overview?.nextCycle ? <Countdown at={overview.nextCycle} /> : '-'}</div>
            </div>
          </Flex>
        </Flex>
      </Card>

      <Flex justify="space-between" align="center" gap={12}>
        <Segmented
          shape="round"
          value={view}
          onChange={(v) => setView(v as View)}
          options={[
            { value: 'live', label: `진행 중 ${live.length}` },
            { value: 'ended', label: `종료됨 ${ended.length}` },
            { value: 'archived', label: `기록 ${archived.length}` },
          ]}
        />
        {/* 모바일은 하단 탭에 추가가 있어서 버튼을 숨긴다 */}
        {!mobile && <Button type="primary" shape="round" icon={<PlusOutlined />} onClick={onAdd}>감시 추가</Button>}
      </Flex>

      {shown.length === 0 ? (
        <Card>
          <Empty description={{ live: '진행 중인 감시가 없습니다', ended: '종료된 감시가 없습니다', archived: '보관된 기록이 없습니다' }[view]}>
            {view === 'live' && <Button type="primary" onClick={onAdd}>감시 추가</Button>}
          </Empty>
        </Card>
      ) : (
        <Row gutter={[16, 16]}>
          {shown.map((w) => (
            <Col key={w.id} xs={24} lg={12}>
              <WatchCard
                w={w}
                channels={channels}
                onOpen={() => setOpenId(w.id)}
                onEnd={() => end(w)}
                onArchive={() => archive(w)}
                onResume={() => {
                  setPick(emptyPick)
                  setResuming(w)
                }}
              />
            </Col>
          ))}
        </Row>
      )}

      <Modal
        open={!!resuming}
        title="채널 다시 지정 후 재개"
        okText="재개"
        cancelText="취소"
        onOk={resume}
        confirmLoading={busy}
        onCancel={() => setResuming(null)}
      >
        <ChannelPicker value={pick} onChange={setPick} channels={channels} />
      </Modal>

      <WatchDrawer
        w={watches.find((w) => w.id === openId) ?? null}
        channels={channels}
        onClose={() => setOpenId(null)}
        onChanged={onChanged}
      />
    </Flex>
  )
}

function WatchCard({ w, channels, onOpen, onEnd, onArchive, onResume }: {
  w: Watch
  channels: Channel[]
  onOpen: () => void
  onEnd: () => void
  onArchive: () => void
  onResume: () => void
}) {
  const { token } = theme.useToken()
  const chs = channels.filter((c) => w.channelIds.includes(c.id))
  const status = {
    active: <Tag color="processing" variant="filled" style={{ borderRadius: 999 }}>감시중</Tag>,
    creating: <Tag variant="filled" style={{ borderRadius: 999 }}>채널 생성 중</Tag>,
    paused: <Tag color="warning" variant="filled" style={{ borderRadius: 999 }}>일시정지</Tag>,
    ended: <Tag variant="filled" style={{ borderRadius: 999 }}>{w.archivedAt ? '기록' : '종료'}</Tag>,
  }[w.status]

  const stats: [string, string | number, string?][] = [
    ['남은 대상', w.remaining == null ? '-' : `${w.remaining}편`],
    ['조회', w.scans.toLocaleString()],
    ['발견', w.found, w.found ? token.colorSuccess : undefined],
    ['오류', w.errors, w.errors ? token.colorError : undefined],
  ]

  return (
    <Card hoverable onClick={onOpen} style={{ height: '100%', opacity: w.status === 'ended' ? 0.85 : 1 }}>
      <Flex vertical gap={12}>
        <Flex justify="space-between" align="start" gap={8}>
          <div>
            <Typography.Text type="secondary" style={{ fontSize: 13 }}>
              #{w.id} · {w.createdVia === 'discord' ? 'Discord 명령' : '웹'}
            </Typography.Text>
            <div style={{ fontSize: 22, fontWeight: 800, display: 'flex', alignItems: 'center', gap: 8 }}>
              {w.dep} <SwapRightOutlined style={{ color: token.colorTextTertiary }} /> {w.arr}
            </div>
            <Typography.Text>
              <ClockCircleOutlined /> {rangeLabel(w)}
            </Typography.Text>
          </div>
          {status}
        </Flex>

        <Flex wrap gap={6}>
          {w.trainTypes.map((t) => <Tag key={t} style={{ borderRadius: 999, margin: 0 }}>{t}</Tag>)}
          {w.seats.map((s) => <Tag key={s} color="blue" variant="filled" style={{ borderRadius: 999, margin: 0 }}>{s}</Tag>)}
          <Tag variant="filled" style={{ borderRadius: 999, margin: 0 }}>{w.passengers}명</Tag>
          <Tag variant="filled" style={{ borderRadius: 999, margin: 0 }}>
            {w.trains ? `선택 ${w.trains.length}편` : '조건 전체'}
          </Tag>
        </Flex>

        {w.status === 'paused' && (
          <Alert type="warning" showIcon style={{ borderRadius: 12 }} title={w.pauseReason} />
        )}

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8 }}>
          {stats.map(([label, value, color]) => (
            <div key={label} style={{ background: token.colorFillQuaternary, borderRadius: 12, padding: '8px 4px', textAlign: 'center' }}>
              <div style={{ fontSize: 12, color: token.colorTextSecondary }}>{label}</div>
              <div style={{ fontSize: 17, fontWeight: 700, color }}>{value}</div>
            </div>
          ))}
        </div>

        <Typography.Text type="secondary" style={{ fontSize: 13 }}>
          {w.status === 'ended'
            ? `${w.endReason === 'manual' ? '수동 종료' : '감시 대상 없음'} · ${dayjs(w.endedAt).format('M/D HH:mm')} 종료`
            : w.lastScan ? `마지막 조회 ${ago(w.lastScan)}` : '첫 조회 대기 중'}
          {w.archivedAt
            ? ` · ${dayjs(w.archivedAt).format('M/D HH:mm')} 채널 정리`
            : ` · 채널 ${chs.map((c) => c.name).join(', ') || '없음'}`}
        </Typography.Text>
        {w.lastError && <Typography.Text type="danger" style={{ fontSize: 12 }}>마지막 오류: {w.lastError}</Typography.Text>}

        {/* 버튼과 확인 팝업 클릭이 카드 클릭(상세 열기)으로 번지지 않게 막는다 */}
        <Space wrap onClick={(e) => e.stopPropagation()}>
          {w.status === 'paused' && (
            <Button type="primary" shape="round" icon={<PlayCircleOutlined />} onClick={onResume}>채널 지정 후 재개</Button>
          )}
          {w.status !== 'ended' ? (
            <Popconfirm
              title="감시를 종료할까요?"
              description="보낸 알림은 그대로 두고 종료 메시지를 보냅니다."
              okText="종료" cancelText="취소" onConfirm={onEnd}
            >
              <Button shape="round" danger icon={<StopOutlined />}>종료</Button>
            </Popconfirm>
          ) : !w.archivedAt && (
            <Popconfirm
              title="채널을 정리할까요?"
              description="자동 생성 채널을 삭제합니다. 감시 기록은 기록 탭에 남습니다."
              okText="정리" cancelText="취소" onConfirm={onArchive}
            >
              <Button shape="round" danger icon={<DeleteOutlined />}>채널 정리</Button>
            </Popconfirm>
          )}
        </Space>
      </Flex>
    </Card>
  )
}

// 서비스 상태 요약. 누르면 로그 탭(경고 이상)으로 간다
function HealthBanner({ h, onClick }: { h: Health; onClick: () => void }) {
  const { token } = theme.useToken()
  const look = {
    ok: { bg: token.colorSuccessBg, border: token.colorSuccessBorder, color: token.colorSuccess, icon: <CheckCircleFilled />, label: '정상' },
    warning: { bg: token.colorWarningBg, border: token.colorWarningBorder, color: token.colorWarning, icon: <ExclamationCircleFilled />, label: 'WARNING' },
    critical: { bg: token.colorErrorBg, border: token.colorErrorBorder, color: token.colorError, icon: <CloseCircleFilled />, label: 'CRITICAL' },
  }[h.level]

  return (
    <button
      className="health-banner"
      onClick={onClick}
      aria-label={`서비스 상태 ${look.label}: ${h.title}. 로그 보기`}
      style={{
        all: 'unset', boxSizing: 'border-box', cursor: 'pointer', display: 'block', width: '100%',
        background: look.bg, border: `1px solid ${look.border}`, borderRadius: token.borderRadiusLG,
        padding: h.level === 'ok' ? '12px 16px' : 16, color: token.colorText,
      }}
    >
      <Flex align="start" gap={12}>
        <span style={{ fontSize: 22, color: look.color, lineHeight: 1 }}>{look.icon}</span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <Flex align="center" gap={8}>
            {h.level !== 'ok' && (
              <span style={{ fontSize: 11, fontWeight: 800, letterSpacing: 0.6, color: look.color, flex: 'none' }}>{look.label}</span>
            )}
            <span style={{ fontWeight: 700, flex: 1, minWidth: 0 }}>{h.title}</span>
            <RightOutlined style={{ color: token.colorTextTertiary, fontSize: 12, flex: 'none' }} />
          </Flex>
          {h.level === 'ok' ? (
            <div style={{ fontSize: 13, color: token.colorTextSecondary }}>최근 1시간 조회 오류 0건</div>
          ) : (
            <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', columnGap: 12, rowGap: 2, marginTop: 6, fontSize: 13 }}>
              {h.reasons.map((r) => (
                <Fragment key={r.label}>
                  <span style={{ color: token.colorTextSecondary }}>{r.label}</span>
                  <span style={{ fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{r.value}</span>
                </Fragment>
              ))}
            </div>
          )}
        </div>
      </Flex>
    </button>
  )
}
