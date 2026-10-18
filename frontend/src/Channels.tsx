import { useState, type ReactNode } from 'react'
import { App, Button, Empty, Flex, Form, Input, Modal, Popconfirm, Tag } from 'antd'
import { ApiOutlined, DeleteOutlined, DiscordOutlined, PlusOutlined, SendOutlined, WarningOutlined } from '@ant-design/icons'
import { api, useConfig, type Channel, type ChannelKind, type Watch } from './api'
import { KIND_LABEL, usable } from './ChannelPicker'
import { ListRow, Panel } from './ui'

const KIND_ICON: Record<ChannelKind, ReactNode> = {
  discord_bot: <DiscordOutlined />,
  discord_webhook: <ApiOutlined />,
  telegram: <SendOutlined />,
}

const WEBHOOK_PREFIXES = ['https://discord.com/api/webhooks/', 'https://discordapp.com/api/webhooks/']

export default function Channels({ channels, watches, onChanged }: {
  channels: Channel[]
  watches: Watch[]
  onChanged: () => Promise<void> | void
}) {
  const config = useConfig()
  const { message } = App.useApp()
  const [adding, setAdding] = useState(false)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState<number | null>(null)
  const [form] = Form.useForm<{ name: string; url: string }>()

  const usedBy = (c: Channel) => watches.filter((w) => w.status !== 'ended' && w.channelIds.includes(c.id))
  const run = async (fn: () => Promise<unknown>, done: string) => {
    try {
      await fn()
      message.success(done)
    } catch (e) {
      message.error(e instanceof Error ? e.message : '요청에 실패했습니다')
    }
    await onChanged()
  }
  const test = async (c: Channel) => {
    setTesting(c.id)
    await run(() => api.testChannel(c.id), `${c.name}(으)로 테스트 메시지를 보냈습니다.`)
    setTesting(null)
  }
  const remove = (c: Channel) => run(() => api.deleteChannel(c.id), `${c.name} 채널을 목록에서 지웠습니다.`)

  const row = (c: Channel, i: number) => {
    const users = usedBy(c)
    return (
      <ListRow
        key={c.id}
        first={i === 0}
        icon={c.broken ? <WarningOutlined /> : KIND_ICON[c.kind]}
        tone={c.broken ? 'error' : 'primary'}
        title={c.name}
        tags={<>
          {c.system && <Tag variant="filled" color="purple" style={{ borderRadius: 6, margin: 0 }}>시스템</Tag>}
          {c.managed && !c.system && <Tag variant="filled" style={{ borderRadius: 6, margin: 0 }}>자동 생성</Tag>}
        </>}
        desc={
          c.broken
            ? '채널을 찾을 수 없습니다. 이 채널을 쓰던 감시는 일시정지했습니다.'
            : c.system
              ? '관리자만 볼 수 있습니다 · 서비스 상태, 오류, 채널 끊김 알림'
              : users.length
                ? `감시 ${users.map((w) => `#${w.id}`).join(', ')}에서 사용 중`
                : '사용 중인 감시 없음'
        }
        actions={<>
          {!c.broken && (
            <Button size="middle" loading={testing === c.id} disabled={testing !== null && testing !== c.id} onClick={() => test(c)}
              aria-label={`${c.name}(으)로 테스트 메시지 보내기`}>
              테스트
            </Button>
          )}
          {(c.kind === 'discord_webhook' || c.broken) && (
            <Popconfirm title="이 채널을 목록에서 지울까요?" okText="지우기" cancelText="취소" okButtonProps={{ danger: true }} onConfirm={() => remove(c)}>
              <Button type="text" size="middle" danger icon={<DeleteOutlined />} aria-label={`${c.name} 지우기`} />
            </Popconfirm>
          )}
        </>}
      />
    )
  }

  const block = (title: string, items: Channel[], opts: { extra?: ReactNode; hint?: ReactNode; empty?: string; danger?: boolean } = {}) => (
    <Panel title={title} count={items.length} countDanger={opts.danger} extra={opts.extra} hint={opts.hint}>
      {items.length ? items.map(row) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={opts.empty ?? '없음'} />}
    </Panel>
  )

  const of = (kind: ChannelKind) => channels.filter((c) => c.kind === kind && usable(c))
  const broken = channels.filter((c) => c.broken)

  return (
    <Flex vertical gap={16}>
      {broken.length > 0 && block('끊긴 채널', broken, { danger: true })}
      {/* 봇이 참여한 텍스트 채널 전체는 여기 늘어놓지 않는다. 감시 추가·재개의 직접 선택 후보로만 쓴다 */}
      {block('시스템', channels.filter((c) => c.system && !c.broken), {
        hint: config.botReady
          ? '봇이 참여한 서버의 텍스트 채널은 감시를 추가할 때 "직접 선택"에서 고를 수 있습니다.'
          : <>Discord 봇이 연결돼 있지 않습니다. <code>.env</code>의 <code>DISCORD_BOT_TOKEN</code>, <code>DISCORD_GUILD_ID</code>와 봇 초대를 확인하세요.</>,
        empty: '봇이 연결되면 시스템 채널을 만듭니다',
      })}
      {block(KIND_LABEL.discord_webhook, of('discord_webhook'), {
        extra: <Button size="middle" icon={<PlusOutlined />} onClick={() => setAdding(true)}>웹훅 추가</Button>,
        empty: '등록한 웹훅이 없습니다',
      })}
      {block(KIND_LABEL.telegram, of('telegram'), {
        hint: config.telegram
          ? 'Telegram 봇을 채널이나 그룹에 추가하면 여기에 자동으로 나타납니다. 채널은 봇을 관리자로 추가해야 합니다.'
          : <>Telegram을 쓰려면 <code>.env</code>에 <code>TELEGRAM_BOT_TOKEN</code>을 넣으세요.</>,
        empty: '찾은 채팅이 없습니다',
      })}

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
              await onChanged()
            } catch (e) {
              message.error(e instanceof Error ? e.message : '웹훅을 추가하지 못했습니다')
            } finally {
              setSaving(false)
            }
          }, () => {}) // 입력 오류는 폼 아래에 이미 보인다
        }
      >
        <Form form={form} layout="vertical" requiredMark={false}>
          <Form.Item name="name" label="이름" rules={[{ required: true, whitespace: true, message: '이름을 입력하세요' }]}>
            <Input placeholder="예: 가족 알림방" maxLength={40} />
          </Form.Item>
          <Form.Item
            name="url"
            label="웹훅 URL"
            extra="Discord 채널 설정 > 연동 > 웹후크에서 복사합니다. 저장한 뒤에는 화면에 다시 보이지 않습니다."
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
