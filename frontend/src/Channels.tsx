import { useState, type ReactNode } from 'react'
import { App, Button, Card, Empty, Flex, Form, Input, Modal, Popconfirm, Tag, Typography, theme } from 'antd'
import { ApiOutlined, DeleteOutlined, DiscordOutlined, PlusOutlined, SendOutlined, WarningOutlined } from '@ant-design/icons'
import { api, useConfig, type Channel, type ChannelKind, type Watch } from './api'
import { KIND_LABEL, usable } from './ChannelPicker'

const KIND_ICON: Record<ChannelKind, ReactNode> = {
  discord_bot: <DiscordOutlined />,
  discord_webhook: <ApiOutlined />,
  telegram: <SendOutlined />,
}

const WEBHOOK_PREFIXES = ['https://discord.com/api/webhooks/', 'https://discordapp.com/api/webhooks/']

export default function Channels({ channels, watches, onChanged }: {
  channels: Channel[]
  watches: Watch[]
  onChanged: () => void
}) {
  const config = useConfig()
  const { token } = theme.useToken()
  const { message } = App.useApp()
  const [adding, setAdding] = useState(false)
  const [saving, setSaving] = useState(false)
  const [form] = Form.useForm<{ name: string; url: string }>()

  const usedBy = (c: Channel) => watches.filter((w) => w.status !== 'ended' && w.channelIds.includes(c.id))
  const run = async (fn: () => Promise<unknown>, done: string) => {
    try {
      await fn()
      message.success(done)
      onChanged()
    } catch (e) {
      message.error(e instanceof Error ? e.message : '요청에 실패했습니다')
      onChanged()
    }
  }
  const remove = (c: Channel) => run(() => api.deleteChannel(c.id), `${c.name} 채널을 목록에서 삭제했습니다.`)

  const row = (c: Channel) => {
    const users = usedBy(c)
    const actions: ReactNode[] = []
    if (!c.broken && c.endedWatchId == null)
      actions.push(
        <Button key="test" shape="round" size="middle" onClick={() => run(() => api.testChannel(c.id), `${c.name}(으)로 테스트 메시지를 보냈습니다.`)}>
          테스트
        </Button>,
      )
    if (c.kind === 'discord_webhook' || c.broken)
      actions.push(
        <Popconfirm key="del" title="이 채널을 목록에서 삭제할까요?" okText="삭제" cancelText="취소" onConfirm={() => remove(c)}>
          <Button shape="circle" size="middle" danger icon={<DeleteOutlined />} aria-label="삭제" />
        </Popconfirm>,
      )
    return (
      <Flex key={c.id} align="center" gap={12} style={{ padding: '10px 0', borderTop: `1px solid ${token.colorBorderSecondary}` }}>
        <div
          style={{
            flex: 'none', width: 40, height: 40, borderRadius: 12, display: 'grid', placeItems: 'center', fontSize: 18,
            background: c.broken ? token.colorErrorBg : token.colorPrimaryBg,
            color: c.broken ? token.colorError : token.colorPrimary,
          }}
        >
          {c.broken ? <WarningOutlined /> : KIND_ICON[c.kind]}
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <Flex wrap gap={6} align="center" style={{ fontWeight: 600 }}>
            <span style={{ wordBreak: 'break-all' }}>{c.name}</span>
            {c.system && <Tag variant="filled" color="purple" style={{ borderRadius: 999, margin: 0 }}>시스템</Tag>}
            {c.managed && !c.system && <Tag variant="filled" style={{ borderRadius: 999, margin: 0 }}>자동 생성</Tag>}
          </Flex>
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>{
            c.broken
              ? '채널을 찾을 수 없습니다. 이 채널을 쓰던 감시는 일시정지되었습니다.'
              : c.endedWatchId != null
                ? `감시 #${c.endedWatchId} 종료됨 · 감시 목록에서 채널 정리를 누르면 삭제됩니다`
                : c.system
                  ? '관리자만 볼 수 있습니다 · 서비스 상태, 오류, 채널 끊김 알림'
                  : users.length
                    ? `감시 ${users.map((w) => `#${w.id}`).join(', ')}에서 사용 중`
                    : '사용 중인 감시 없음'
          }</Typography.Text>
        </div>
        <Flex gap={6}>{actions}</Flex>
      </Flex>
    )
  }

  const block = (title: string, items: Channel[], opts: { extra?: ReactNode; hint?: string; empty?: string; danger?: boolean } = {}) => (
    <Card
      title={
        <Flex align="center" gap={8}>
          {title}
          <Tag variant="filled" color={opts.danger ? 'error' : undefined} style={{ borderRadius: 999, margin: 0 }}>{items.length}</Tag>
        </Flex>
      }
      extra={opts.extra}
      styles={{ header: { borderBottom: 0 }, body: { paddingTop: 0 } }}
    >
      {opts.hint && <Typography.Paragraph type="secondary" style={{ fontSize: 13 }}>{opts.hint}</Typography.Paragraph>}
      {items.length ? items.map(row) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={opts.empty ?? '없음'} />}
    </Card>
  )

  const of = (kind: ChannelKind) => channels.filter((c) => c.kind === kind && usable(c))
  const broken = channels.filter((c) => c.broken)
  const ended = channels.filter((c) => c.endedWatchId != null)

  return (
    <Flex vertical gap={16} style={{ maxWidth: 760 }}>
      {block('시스템', channels.filter((c) => c.system))}
      {block(KIND_LABEL.discord_bot, of('discord_bot'), {
        hint: config.botReady
          ? '봇이 참여한 서버의 텍스트 채널입니다. 감시를 추가할 때 "자동 생성"을 고르면 ktx-감시 카테고리에 전용 채널이 생깁니다.'
          : 'Discord 봇이 연결돼 있지 않습니다. .env 의 DISCORD_BOT_TOKEN, DISCORD_GUILD_ID 와 봇 초대를 확인하세요.',
      })}
      {block(KIND_LABEL.discord_webhook, of('discord_webhook'), {
        extra: <Button shape="round" size="middle" icon={<PlusOutlined />} onClick={() => setAdding(true)}>추가</Button>,
        empty: '등록된 웹훅이 없습니다',
      })}
      {block(KIND_LABEL.telegram, of('telegram'), {
        hint: config.telegram
          ? 'Telegram 봇을 채널이나 그룹에 추가하면 자동으로 등록됩니다. 채널은 봇을 관리자로 추가해야 합니다.'
          : 'Telegram 을 쓰려면 .env 에 TELEGRAM_BOT_TOKEN 을 넣으세요.',
        empty: '감지된 채팅이 없습니다',
      })}
      {broken.length > 0 && block('끊긴 채널', broken, { danger: true })}
      {ended.length > 0 && block('종료된 감시의 채널', ended)}

      <Modal
        open={adding}
        title="Discord 웹훅 추가"
        okText="추가"
        cancelText="취소"
        onCancel={() => setAdding(false)}
        confirmLoading={saving}
        onOk={() =>
          form.validateFields().then(async ({ name, url }) => {
            setSaving(true)
            try {
              await api.addWebhook(name.trim(), url.trim())
              form.resetFields()
              setAdding(false)
              message.success('웹훅을 추가했습니다.')
              onChanged()
            } catch (e) {
              message.error(e instanceof Error ? e.message : '웹훅을 추가하지 못했습니다')
            } finally {
              setSaving(false)
            }
          })
        }
      >
        <Form form={form} layout="vertical" requiredMark={false}>
          <Form.Item name="name" label="이름" rules={[{ required: true, whitespace: true, message: '이름을 입력하세요' }]}>
            <Input placeholder="예: 가족 알림방" maxLength={40} />
          </Form.Item>
          <Form.Item
            name="url"
            label="웹훅 URL"
            rules={[
              { required: true, message: 'URL을 입력하세요' },
              {
                validator: (_, v: string) =>
                  !v || WEBHOOK_PREFIXES.some((p) => v.startsWith(p)) ? Promise.resolve() : Promise.reject(new Error('Discord 웹훅 주소가 아닙니다')),
              },
            ]}
          >
            <Input.Password placeholder="https://discord.com/api/webhooks/..." autoComplete="off" />
          </Form.Item>
        </Form>
      </Modal>
    </Flex>
  )
}
