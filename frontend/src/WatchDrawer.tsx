import { useEffect, useState } from 'react'
import { Alert, App, Button, Descriptions, Drawer, Empty, Flex, Grid, Tag, Typography } from 'antd'
import { SwapRightOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import { api, useConfig, type Channel, type Watch, type WatchDetail } from './api'
import { KIND_LABEL, ViewerSelect } from './ChannelPicker'
import { rangeLabel } from './WatchList'

const time = (t: number | null) => (t ? dayjs(t).format('M/D(dd) HH:mm:ss') : '-')
const day = (d: string) => dayjs(d).format('M/D(dd)')
const same = (a: string[], b: string[]) => a.length === b.length && [...a].sort().join() === [...b].sort().join()

// 감시 카드를 누르면 열리는 상세. 조건·현황은 보기만 하고, 자동 생성 채널의 열람자만 바꿀 수 있다
export default function WatchDrawer({ w, channels, onClose, onChanged }: {
  w: Watch | null
  channels: Channel[]
  onClose: () => void
  onChanged: () => void
}) {
  const config = useConfig()
  const { message } = App.useApp()
  const mobile = !Grid.useBreakpoint().md
  const [detail, setDetail] = useState<WatchDetail | null>(null)
  const [viewers, setViewers] = useState<string[]>([])
  const [saving, setSaving] = useState(false)

  const id = w?.id
  const lastScan = w?.lastScan

  // 다른 감시를 열면 편집 중이던 열람자를 그 감시 값으로 되돌린다
  useEffect(() => {
    setDetail(null)
    setViewers(w?.viewers ?? [])
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  // 자리 목록은 목록 API 에 없어서 열 때와 조회가 한 번 돌 때마다 받는다
  useEffect(() => {
    if (id == null) return
    api.watch(id).then(setDetail).catch(() => setDetail(null))
  }, [id, lastScan])

  if (!w) return <Drawer open={false} />

  const chs = channels.filter((c) => w.channelIds.includes(c.id))
  const editable = !w.archivedAt && chs.some((c) => c.managed && !c.system && !c.broken)

  const save = async () => {
    setSaving(true)
    try {
      await api.setViewers(w.id, viewers)
      message.success('열람자를 바꿨습니다.')
      onChanged()
    } catch (e) {
      message.error(e instanceof Error ? e.message : '요청에 실패했습니다')
    } finally {
      setSaving(false)
    }
  }

  const tags = (xs: string[]) => (
    <Flex wrap gap={4}>{xs.map((x) => <Tag key={x} style={{ borderRadius: 999, margin: 0 }}>{x}</Tag>)}</Flex>
  )

  return (
    <Drawer
      open
      onClose={onClose}
      size={mobile ? '100%' : 520}
      title={
        <Flex align="center" gap={8}>
          #{w.id} {w.dep} <SwapRightOutlined /> {w.arr}
        </Flex>
      }
    >
      <Flex vertical gap={24}>
        {w.status === 'paused' && <Alert type="warning" showIcon title={w.pauseReason} />}

        <Descriptions title="조건" size="small" column={1} bordered items={[
          { label: '구간', children: rangeLabel(w) },
          { label: '열차 종류', children: tags(w.trainTypes) },
          { label: '좌석', children: tags(w.seats) },
          { label: '인원', children: `${w.passengers}명` },
          {
            label: '대상',
            children: w.trains
              ? tags(w.trains.map((t) => `${day(t.date)} ${t.dep} ${t.no}`))
              : '조건에 맞는 열차 전체',
          },
        ]} />

        <Descriptions title="현황" size="small" column={1} bordered items={[
          {
            label: '상태',
            children: w.status === 'ended'
              ? `${w.endReason === 'manual' ? '수동 종료' : '감시 대상 없음'} · ${time(w.endedAt)}`
              : { creating: '채널 생성 중', active: '감시중', paused: '일시정지' }[w.status],
          },
          { label: '만든 곳', children: `${w.createdVia === 'discord' ? 'Discord 명령' : '웹'} · ${time(w.createdAt)}` },
          { label: '남은 대상', children: w.remaining == null ? '-' : `${w.remaining}편` },
          { label: '조회 / 발견 / 오류', children: `${w.scans.toLocaleString()} / ${w.found} / ${w.errors}` },
          { label: '마지막 조회', children: time(w.lastScan) },
          ...(w.lastError ? [{ label: '마지막 오류', children: <Typography.Text type="danger">{w.lastError}</Typography.Text> }] : []),
          ...(w.archivedAt ? [{ label: '채널 정리', children: time(w.archivedAt) }] : []),
        ]} />

        <div>
          <Typography.Title level={5}>지금 자리 있는 열차</Typography.Title>
          {detail?.available.length ? (
            <Flex vertical gap={6}>
              {detail.available.map((a) => (
                <Flex key={`${a.date}-${a.no}`} justify="space-between">
                  <span>{day(a.date)} {a.no}</span>
                  {tags(a.seats)}
                </Flex>
              ))}
            </Flex>
          ) : (
            <Typography.Text type="secondary">
              {detail ? '알린 뒤 아직 남아 있는 좌석이 없습니다.' : '불러오는 중'}
            </Typography.Text>
          )}
        </div>

        <div>
          <Typography.Title level={5}>채널</Typography.Title>
          {chs.length ? (
            <Flex vertical gap={6}>
              {chs.map((c) => (
                <Flex key={c.id} justify="space-between" gap={8}>
                  <span>{c.name}</span>
                  <Flex gap={4}>
                    <Tag style={{ margin: 0 }}>{c.managed ? '자동 생성' : KIND_LABEL[c.kind]}</Tag>
                    {c.broken && <Tag color="error" style={{ margin: 0 }}>끊김</Tag>}
                  </Flex>
                </Flex>
              ))}
            </Flex>
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={w.archivedAt ? '채널을 정리했습니다' : '채널이 없습니다'} />
          )}
        </div>

        <div>
          <Typography.Title level={5}>열람자</Typography.Title>
          {editable ? (
            <Flex vertical gap={8}>
              <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                자동 생성 채널을 읽을 수 있는 멤버입니다. 관리자는 항상 볼 수 있어 목록에 없습니다.
              </Typography.Text>
              <ViewerSelect value={viewers} onChange={setViewers} />
              <Flex justify="end" gap={8}>
                <Button shape="round" disabled={same(viewers, w.viewers)} onClick={() => setViewers(w.viewers)}>되돌리기</Button>
                <Button
                  type="primary"
                  shape="round"
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
              열람자는 살아 있는 자동 생성 채널에만 있습니다. 직접 고른 채널은 Discord 에서 권한을 관리하세요.
            </Typography.Text>
          )}
        </div>
      </Flex>
    </Drawer>
  )
}
