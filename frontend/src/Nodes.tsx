import { useCallback, useEffect, useState } from 'react'
import { Alert, App, Button, Empty, Flex, Form, Input, Modal, Popconfirm, Skeleton, Tag, Typography } from 'antd'
import { CloudServerOutlined, DeleteOutlined, DesktopOutlined, PlusOutlined, WarningOutlined } from '@ant-design/icons'
import { api, type NodeInfo, type NodeStatus } from './api'
import { ListRow, Panel, StatusDot, fmtStamp, type Tone } from './ui'

const STATUS: Record<NodeStatus, { label: string; tone: Tone }> = {
  online: { label: '온라인', tone: 'success' },
  offline: { label: '오프라인', tone: 'neutral' },
  stopped: { label: '중지됨', tone: 'neutral' }, // 노드가 종료를 알렸다. 경고하지 않는다
  cooldown: { label: '쿨다운', tone: 'warning' },
  blocked: { label: '차단 의심', tone: 'error' },
  version: { label: '버전 불일치', tone: 'error' },
}

// 조회 노드 관리. 중앙(이 서버)과 원격 노드가 코레일 조회를 나눠 맡는다
export default function Nodes() {
  const { message } = App.useApp()
  const [nodes, setNodes] = useState<NodeInfo[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [saving, setSaving] = useState(false)
  const [issued, setIssued] = useState<{ name: string; token: string } | null>(null)
  const [form] = Form.useForm<{ name: string }>()

  const load = useCallback(async () => {
    try {
      setNodes(await api.nodes())
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : '노드 목록을 불러오지 못했습니다')
    }
  }, [])

  useEffect(() => {
    load()
    const t = setInterval(() => !document.hidden && load(), 5000)
    return () => clearInterval(t)
  }, [load])

  const revoke = async (n: NodeInfo) => {
    try {
      await api.revokeNode(n.id!)
      message.success(`${n.name} 노드를 폐기했습니다.`)
    } catch (e) {
      message.error(e instanceof Error ? e.message : '폐기하지 못했습니다')
    }
    await load()
  }

  const row = (n: NodeInfo, i: number) => {
    const s = STATUS[n.status]
    const detail = [
      n.ip ? `IP ${n.ip}` : null,
      n.lastSeen ? `마지막 접속 ${fmtStamp(n.lastSeen)}` : n.local ? null : '아직 접속 없음',
      `1시간 성공 ${n.ok1h} · 실패 ${n.fail1h}`,
    ].filter(Boolean).join(' · ')
    return (
      <ListRow
        key={n.id ?? 'local'}
        first={i === 0}
        icon={n.local ? <DesktopOutlined /> : <CloudServerOutlined />}
        tone={n.status === 'online' ? 'primary' : n.status === 'blocked' || n.status === 'version' ? 'error' : 'neutral'}
        title={n.local ? '중앙(이 서버)' : n.name}
        tags={<>
          <StatusDot tone={s.tone}>{s.label}</StatusDot>
          {n.prefix && <Typography.Text type="secondary" code style={{ fontSize: 12 }}>{n.prefix}…</Typography.Text>}
          {n.sameIp && <Tag variant="filled" color="warning" icon={<WarningOutlined />} style={{ borderRadius: 6, margin: 0 }}>같은 IP</Tag>}
        </>}
        desc={<>
          <span className="num">{detail}</span>
          <br />
          {n.routes.length ? `맡은 노선 ${n.routes.join(', ')}` : '맡은 노선 없음'}
          {n.status === 'version' && n.version && ` · 노드 pykorail ${n.version}, 다시 빌드하세요`}
        </>}
        actions={!n.local && (
          <Popconfirm
            title="이 노드를 폐기할까요?"
            description="토큰이 바로 무효가 됩니다. 다시 붙이려면 새로 발급해야 합니다."
            okText="폐기" cancelText="취소" okButtonProps={{ danger: true }}
            onConfirm={() => revoke(n)}
          >
            <Button type="text" size="middle" danger icon={<DeleteOutlined />} aria-label={`${n.name} 노드 폐기`} />
          </Popconfirm>
        )}
      />
    )
  }

  return (
    <Flex vertical gap={16}>
      {error && <Alert type="error" showIcon title={error} />}
      <Panel
        title="조회 노드"
        count={nodes?.length}
        extra={<Button size="middle" icon={<PlusOutlined />} onClick={() => setAdding(true)}>노드 추가</Button>}
        hint="공인 IP가 다른 PC에 노드를 띄우면 노선을 나눠 조회하고, 노드 수만큼 조회 간격이 줄어듭니다. 같은 공유기 뒤의 노드는 IP가 같아 의미가 없습니다."
      >
        {nodes === null ? (
          error ? null : <Skeleton active avatar paragraph={{ rows: 1 }} />
        ) : nodes.length ? nodes.map(row) : (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="조회할 노드가 없습니다 (LOCAL_SCAN=0이고 붙은 노드도 없음)" />
        )}
      </Panel>

      <Modal
        open={adding}
        title="노드 추가"
        okText="토큰 발급"
        cancelText="취소"
        onCancel={() => setAdding(false)}
        confirmLoading={saving}
        onOk={() =>
          form.validateFields().then(async ({ name }) => {
            setSaving(true)
            try {
              const r = await api.issueNode(name.trim())
              form.resetFields()
              setAdding(false)
              setIssued({ name: name.trim(), token: r.token })
              load()
            } catch (e) {
              message.error(e instanceof Error ? e.message : '토큰을 발급하지 못했습니다')
            } finally {
              setSaving(false)
            }
          }, () => {}) // 입력 오류는 폼 아래에 이미 보인다
        }
      >
        <Form form={form} layout="vertical" requiredMark={false}>
          <Form.Item name="name" label="이름" rules={[{ required: true, whitespace: true, message: '이름을 입력하세요' }]}>
            <Input placeholder="예: 본가 PC" maxLength={40} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        open={issued !== null}
        title={`${issued?.name ?? ''} 노드 토큰`}
        okText="복사했습니다"
        cancelButtonProps={{ style: { display: 'none' } }}
        closable={false}
        maskClosable={false}
        onOk={() => setIssued(null)}
      >
        <Alert type="warning" showIcon title="이 토큰은 지금만 볼 수 있습니다. 닫기 전에 복사해서 노드의 .env.node에 넣으세요." style={{ marginBottom: 12 }} />
        <Typography.Paragraph copyable={{ text: issued?.token }} code style={{ wordBreak: 'break-all' }}>
          NODE_TOKEN={issued?.token}
        </Typography.Paragraph>
        <Typography.Text type="secondary" style={{ fontSize: 13 }}>
          잃어버리면 이 노드를 폐기하고 새로 발급하세요. 노드를 띄우는 방법은 README의 "조회 노드"를 보세요.
        </Typography.Text>
      </Modal>
    </Flex>
  )
}
