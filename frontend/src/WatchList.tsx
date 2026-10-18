import { Fragment, useEffect, useState, type KeyboardEvent, type ReactNode } from 'react'
import { App, Button, Empty, Flex, Grid, Modal, Popconfirm, Segmented, Typography, theme } from 'antd'
import {
  CheckCircleFilled, ClockCircleOutlined, CloseCircleFilled, DeleteOutlined, ExclamationCircleFilled, PauseCircleOutlined,
  PlayCircleOutlined, PlusOutlined, RightOutlined, StopOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'
import { api, useConfig, type Channel, type Config, type Health, type Overview, type Watch } from './api'
import ChannelPicker, { emptyPick, type ChannelPick } from './ChannelPicker'
import WatchDrawer from './WatchDrawer'
import { Chip, Panel, StatusDot, TrainGlyph, fmtDateTime, type Tone } from './ui'

interface Props {
  watches: Watch[]
  channels: Channel[]
  health: Health | null
  overview: Overview | null
  onChanged: () => Promise<void> | void
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
    ? `${s.format('M/D(dd) HH:mm')}~${e.format('HH:mm')} ${kind}`
    : `${s.format('M/D(dd) HH:mm')} ~ ${e.format('M/D(dd) HH:mm')} ${kind}`
}

// 묶음의 종류를 다 골랐으면 묶음 이름 하나로 줄인다(KTX, KTX-산천, KTX-이음, KTX-청룡 → KTX 전체)
export function typeLabels(config: Config, types: string[]) {
  return config.trainGroups.flatMap((g) => {
    const on = g.types.filter((t) => types.includes(t))
    return on.length === g.types.length ? [`${g.name} 전체`] : on
  })
}

export const STATUS: Record<Watch['status'], { label: string; tone: Tone }> = {
  active: { label: '감시 중', tone: 'primary' },
  creating: { label: '채널 만드는 중', tone: 'neutral' },
  paused: { label: '일시정지', tone: 'warning' },
  ended: { label: '종료', tone: 'neutral' },
}

export const endLabel = (w: Watch) =>
  `${w.endReason === 'manual' ? '수동 종료' : w.endReason === 'no_targets' ? '감시 대상 없음' : '종료'} · ${fmtDateTime(w.endedAt)}`

function until(t: number) {
  const s = Math.max(0, Math.round((t - Date.now()) / 1000))
  return s < 60 ? `${s}초 뒤` : `${Math.round(s / 60)}분 뒤`
}

// 서버 값은 5초마다 오지만 남은·지난 시간은 받은 시각 기준으로 1초마다 다시 그린다
function Live({ text }: { text: () => string }) {
  const [, tick] = useState(0)
  useEffect(() => {
    const t = setInterval(() => tick((n) => n + 1), 1000)
    return () => clearInterval(t)
  }, [])
  return <>{text()}</>
}

function ago(t: number) {
  const s = Math.max(0, Math.floor((Date.now() - t) / 1000))
  if (s < 60) return `${s}초 전`
  if (s < 3600) return `${Math.floor(s / 60)}분 전`
  if (s < 86400) return `${Math.floor(s / 3600)}시간 전`
  return `${Math.floor(s / 86400)}일 전`
}

const HEALTH: Record<Health['level'], { label: string; tone: Tone }> = {
  ok: { label: '정상', tone: 'success' },
  warning: { label: '주의', tone: 'warning' },
  critical: { label: '심각', tone: 'error' },
}

export default function WatchList({ watches, channels, health, overview, onChanged, onAdd, onLogs }: Props) {
  const config = useConfig()
  const { token } = theme.useToken()
  const { message } = App.useApp()
  const mobile = !Grid.useBreakpoint().md
  const [view, setView] = useState<View>('live')
  const [resuming, setResuming] = useState<Watch | null>(null)
  const [pick, setPick] = useState<ChannelPick>(emptyPick)
  const [busy, setBusy] = useState<string | null>(null) // `${감시 id}:${동작}`. 누른 버튼만 돌고 같은 감시의 버튼은 막는다
  const [openId, setOpenId] = useState<number | null>(null)

  const live = watches.filter((w) => w.status !== 'ended')
  const ended = watches.filter((w) => w.status === 'ended' && !w.archivedAt)
  const archived = watches.filter((w) => w.archivedAt)
  const shown = { live, ended, archived }[view]

  // 서버에 요청하고 성공하면 목록을 새로 받은 뒤 버튼을 푼다(옛 버튼을 한 번 더 누르는 일이 없게). 실패 문구는 서버가 준 그대로
  const act = async (key: string, fn: () => Promise<unknown>, done: string) => {
    setBusy(key)
    try {
      await fn()
      await onChanged()
      message.success(done)
      return true
    } catch (e) {
      message.error(e instanceof Error ? e.message : '요청에 실패했습니다')
      return false
    } finally {
      setBusy(null)
    }
  }

  const resume = async () => {
    if (!resuming) return
    if (pick.mode === 'manual' && !pick.channelIds.length) return message.warning('알림 채널을 하나 이상 지정하세요.')
    if (await act(`${resuming.id}:repick`, () => api.resumeWatch(resuming.id, pick), '감시를 재개했습니다.')) setResuming(null)
  }

  const look = health ? HEALTH[health.level] : null
  const stat = (label: string, value: ReactNode, i: number) => (
    <div key={label} style={{
      padding: mobile ? '12px 14px' : '16px 20px', minWidth: 0,
      borderLeft: (mobile ? i % 2 : i) ? `1px solid ${token.colorBorderSecondary}` : undefined,
      borderTop: mobile && i >= 2 ? `1px solid ${token.colorBorderSecondary}` : undefined,
    }}>
      <div style={{ fontSize: 12.5, color: token.colorTextSecondary, marginBottom: 4 }}>{label}</div>
      <div className="num" style={{ fontSize: mobile ? 18 : 22, fontWeight: 750, letterSpacing: '-0.02em', lineHeight: 1.2 }}>{value}</div>
    </div>
  )

  return (
    <Flex vertical gap={20}>
      <Panel bodyStyle={{ padding: 0 }}>
        <div style={{ display: 'grid', gridTemplateColumns: mobile ? '1fr 1fr' : 'repeat(4, 1fr)' }}>
          {stat('진행 중인 감시', <>{live.length}<span style={{ fontSize: '0.65em', color: token.colorTextTertiary, fontWeight: 600 }}> / {config.maxActive}</span></>, 0)}
          {/* 조회할 감시가 없으면 루프는 돌아도 요청은 없으므로 '-' */}
          {stat('다음 조회', live.some((w) => w.status === 'active') && overview?.nextCycle ? <Live text={() => until(overview.nextCycle!)} /> : '-', 1)}
          {stat('조회 묶음', `${overview?.groups ?? 0}개`, 2)}
          {stat('서비스 상태', look ? (
            <button onClick={onLogs} className="health-banner" aria-label={`서비스 상태 ${look.label}. 경고 로그 보기`}
              style={{ all: 'unset', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 6, borderRadius: 6 }}>
              <StatusDot tone={look.tone} pulse={health!.level !== 'ok'}><span style={{ fontSize: mobile ? 16 : 18 }}>{look.label}</span></StatusDot>
              <RightOutlined style={{ fontSize: 11, color: token.colorTextTertiary }} />
            </button>
          ) : '-', 3)}
        </div>
      </Panel>

      {health && (health.level !== 'ok' || health.notes.length > 0) && <HealthBanner h={health} onClick={onLogs} />}

      <Flex justify="space-between" align="center" gap={12} wrap>
        <Segmented
          value={view}
          onChange={(v) => setView(v as View)}
          aria-label="감시 보기"
          options={[
            { value: 'live', label: `진행 중 ${live.length}` },
            { value: 'ended', label: `종료 ${ended.length}` },
            { value: 'archived', label: `기록 ${archived.length}` },
          ]}
        />
        {view === 'ended' && ended.length > 0 && (
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>채널을 정리하면 기록으로 옮겨집니다.</Typography.Text>
        )}
      </Flex>

      {shown.length === 0 ? (
        <Panel>
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={{
              live: '진행 중인 감시가 없습니다. 구간과 열차를 고르면 좌석이 날 때 알려 드립니다.',
              ended: '종료된 감시가 없습니다.',
              archived: '채널을 정리한 기록이 없습니다.',
            }[view]}
            style={{ padding: '24px 0' }}
          >
            {view === 'live' && <Button type="primary" icon={<PlusOutlined />} onClick={onAdd}>감시 추가</Button>}
          </Empty>
        </Panel>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(min(100%, 380px), 1fr))', gap: 16 }}>
          {shown.map((w) => (
            <WatchCard
              key={w.id}
              w={w}
              channels={channels}
              busy={busy?.startsWith(`${w.id}:`) ? busy.split(':')[1] : null}
              onOpen={() => setOpenId(w.id)}
              onEnd={() => act(`${w.id}:end`, () => api.endWatch(w.id), '감시를 종료했습니다.')}
              // 자동 생성 채널만 지우고 감시 기록은 기록 탭으로 옮겨 계속 볼 수 있게 한다
              onArchive={() => act(`${w.id}:archive`, () => api.archiveWatch(w.id), '자동 생성 채널을 지우고 기록으로 옮겼습니다.')}
              onPause={() => act(`${w.id}:pause`, () => api.pauseWatch(w.id), '감시를 일시정지했습니다.')}
              onResume={() => act(`${w.id}:resume`, () => api.resumeWatch(w.id), '감시를 재개했습니다.')}
              onRepick={() => {
                setPick(emptyPick)
                setResuming(w)
              }}
            />
          ))}
        </div>
      )}

      <Modal
        open={!!resuming}
        title="채널을 다시 지정하고 재개"
        okText="재개"
        cancelText="취소"
        onOk={resume}
        confirmLoading={busy === `${resuming?.id}:repick`}
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

function WatchCard({ w, channels, busy, onOpen, onEnd, onArchive, onPause, onResume, onRepick }: {
  w: Watch
  channels: Channel[]
  busy: string | null
  onOpen: () => void
  onEnd: () => Promise<unknown>
  onArchive: () => Promise<unknown>
  onPause: () => void
  onResume: () => void
  onRepick: () => void
}) {
  const config = useConfig()
  const { token } = theme.useToken()
  const chs = channels.filter((c) => w.channelIds.includes(c.id))
  // 채널이 살아 있으면(수동 일시정지) 그대로 재개하고, 끊겼으면 채널을 다시 지정해야 한다
  const channelsOk = chs.length > 0 && chs.every((c) => !c.broken)
  const status = w.archivedAt ? { label: '기록', tone: 'neutral' as Tone } : STATUS[w.status]
  const rail = { primary: token.colorPrimary, warning: token.colorWarning, neutral: token.colorBorder, success: token.colorSuccess, error: token.colorError }[status.tone]

  const stats: [string, string | number, string?][] = [
    ['남은 대상', w.remaining == null ? '-' : `${w.remaining}편`],
    ['조회', w.scans.toLocaleString()],
    ['발견', w.found, w.found ? token.colorSuccess : undefined],
    ['오류', w.errors, w.errors ? token.colorError : undefined],
  ]
  const open = (e: KeyboardEvent) => {
    if (e.target !== e.currentTarget || (e.key !== 'Enter' && e.key !== ' ')) return
    e.preventDefault()
    onOpen()
  }

  return (
    <article
      className="ticket"
      role="button"
      tabIndex={0}
      aria-label={`감시 #${w.id} ${w.dep}에서 ${w.arr}, ${status.label}. 상세 보기`}
      onClick={onOpen}
      onKeyDown={open}
      style={{ opacity: w.status === 'ended' ? 0.88 : 1 }}
    >
      <span className="ticket-rail" style={{ background: rail }} aria-hidden />
      <div style={{ padding: '18px 20px 16px' }}>
        <Flex justify="space-between" align="center" gap={8}>
          <StatusDot tone={status.tone} pulse={w.status === 'active'}>{status.label}</StatusDot>
          <Typography.Text type="secondary" style={{ fontSize: 12.5 }}>
            #{w.id} · {w.createdVia === 'discord' ? 'Discord 명령' : '웹'}
          </Typography.Text>
        </Flex>

        <Flex align="center" gap={12} style={{ margin: '10px 0 4px' }}>
          <span style={{ fontSize: 24, fontWeight: 750, letterSpacing: '-0.03em', whiteSpace: 'nowrap' }}>{w.dep}</span>
          <span className="route-line"><TrainGlyph size={15} /></span>
          <span style={{ fontSize: 24, fontWeight: 750, letterSpacing: '-0.03em', whiteSpace: 'nowrap' }}>{w.arr}</span>
        </Flex>
        <Typography.Text type="secondary" className="num" style={{ fontSize: 14 }}>
          <ClockCircleOutlined style={{ marginRight: 6 }} />{rangeLabel(w)}
        </Typography.Text>

        <Flex wrap gap={6} style={{ marginTop: 12 }}>
          {typeLabels(config, w.trainTypes).map((t) => <Chip key={t}>{t}</Chip>)}
          {w.seats.map((s) => <Chip key={s} accent>{s}</Chip>)}
          <Chip>{w.passengers}명</Chip>
          <Chip>{w.trains ? `열차 ${w.trains.length}편 선택` : '조건 전체'}</Chip>
        </Flex>

        {w.status === 'paused' && (
          <Flex gap={8} align="start" style={{
            marginTop: 12, padding: '8px 12px', borderRadius: 10, background: token.colorWarningBg, fontSize: 13,
          }}>
            <ExclamationCircleFilled style={{ color: token.colorWarning, marginTop: 3 }} />
            <span>{w.pauseReason ?? '일시정지됨'}</span>
          </Flex>
        )}
      </div>

      <div className="ticket-perf" aria-hidden style={{ marginTop: 'auto' }} />

      <div style={{ padding: '14px 20px 18px' }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8 }}>
          {stats.map(([label, value, color]) => (
            <div key={label}>
              <div style={{ fontSize: 12, color: token.colorTextTertiary }}>{label}</div>
              <div className="num" style={{ fontSize: 17, fontWeight: 700, color }}>{value}</div>
            </div>
          ))}
        </div>

        <div style={{ fontSize: 12.5, color: token.colorTextSecondary, marginTop: 10 }}>
          {w.status === 'ended'
            ? endLabel(w)
            : w.lastScan ? <Live text={() => `마지막 조회 ${ago(w.lastScan!)}`} /> : '첫 조회를 기다리는 중'}
          {w.archivedAt
            ? ` · ${fmtDateTime(w.archivedAt)} 채널 정리`
            : ` · 채널 ${chs.map((c) => c.name).join(', ') || '없음'}`}
        </div>
        {w.lastError && (
          <Typography.Text type="danger" style={{ fontSize: 12, display: 'block', marginTop: 4 }} ellipsis={{ tooltip: w.lastError }}>
            마지막 오류: {w.lastError}
          </Typography.Text>
        )}

        {/* 버튼과 확인 팝업 클릭이 카드 클릭(상세 열기)으로 번지지 않게 막는다 */}
        {!w.archivedAt && (
          <Flex gap={8} wrap style={{ marginTop: 14 }} onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
            {w.status === 'active' && (
              <Button icon={<PauseCircleOutlined />} loading={busy === 'pause'} disabled={!!busy && busy !== 'pause'} onClick={onPause}>
                일시정지
              </Button>
            )}
            {w.status === 'paused' && (
              <Button type="primary" icon={<PlayCircleOutlined />} loading={busy === 'resume'} disabled={!!busy && busy !== 'resume'}
                onClick={channelsOk ? onResume : onRepick}>
                {channelsOk ? '재개' : '채널 지정 후 재개'}
              </Button>
            )}
            {w.status !== 'ended' ? (
              <Popconfirm
                title="감시를 종료할까요?"
                description="보낸 알림은 그대로 두고 종료 메시지를 보냅니다."
                okText="종료" cancelText="취소" okButtonProps={{ danger: true }} onConfirm={onEnd}
              >
                <Button type="text" danger icon={<StopOutlined />} disabled={!!busy} loading={busy === 'end'}>종료</Button>
              </Popconfirm>
            ) : (
              <Popconfirm
                title="채널을 정리할까요?"
                description="자동 생성 채널을 지웁니다. 감시 기록은 기록 탭에 남습니다."
                okText="정리" cancelText="취소" okButtonProps={{ danger: true }} onConfirm={onArchive}
              >
                <Button danger icon={<DeleteOutlined />} disabled={!!busy} loading={busy === 'archive'}>채널 정리</Button>
              </Popconfirm>
            )}
          </Flex>
        )}
      </div>
    </article>
  )
}

// 주의·심각일 때 사유, 등급과 무관한 안내(노선 공유 등). 누르면 로그 탭(경고 이상)으로 간다
function HealthBanner({ h, onClick }: { h: Health; onClick: () => void }) {
  const { token } = theme.useToken()
  const look = {
    ok: { bg: token.colorFillQuaternary, color: token.colorPrimary, icon: <CheckCircleFilled /> },
    warning: { bg: token.colorWarningBg, color: token.colorWarning, icon: <ExclamationCircleFilled /> },
    critical: { bg: token.colorErrorBg, color: token.colorError, icon: <CloseCircleFilled /> },
  }[h.level]

  return (
    <button
      className="health-banner"
      onClick={onClick}
      aria-label={`서비스 상태 ${HEALTH[h.level].label}: ${h.title}. 경고 로그 보기`}
      style={{
        all: 'unset', boxSizing: 'border-box', cursor: 'pointer', display: 'block', width: '100%',
        background: look.bg, borderRadius: token.borderRadiusLG, padding: '14px 18px', color: token.colorText,
        boxShadow: `inset 3px 0 0 ${look.color}`,
      }}
    >
      <Flex align="start" gap={12}>
        <span style={{ fontSize: 18, color: look.color, lineHeight: 1.3 }}>{look.icon}</span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <Flex align="center" gap={8}>
            <span style={{ fontWeight: 700, flex: 1, minWidth: 0 }}>{h.title}</span>
            <span style={{ fontSize: 12.5, color: token.colorTextSecondary, flex: 'none' }}>로그 보기 <RightOutlined style={{ fontSize: 10 }} /></span>
          </Flex>
          {h.level !== 'ok' && (
            <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', columnGap: 12, rowGap: 2, marginTop: 6, fontSize: 13 }}>
              {h.reasons.map((r) => (
                <Fragment key={r.label}>
                  <span style={{ color: token.colorTextSecondary }}>{r.label}</span>
                  <span className="num" style={{ fontWeight: 600 }}>{r.value}</span>
                </Fragment>
              ))}
            </div>
          )}
          {h.notes.map((n) => (
            <div key={n.label} style={{ fontSize: 13, color: token.colorTextSecondary, marginTop: 4 }}>{n.label} · {n.value}</div>
          ))}
        </div>
      </Flex>
    </button>
  )
}
