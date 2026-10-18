import { useCallback, useEffect, useState } from 'react'
import { Alert, App, Button, Card, Empty, Flex, Form, Input, Modal, Popconfirm, Tag, Typography, theme } from 'antd'
import { CloudServerOutlined, DeleteOutlined, DesktopOutlined, PlusOutlined, WarningOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import { api, type NodeInfo, type NodeStatus } from './api'

const STATUS: Record<NodeStatus, { label: string; color: string }> = {
  online: { label: '온라인', color: 'success' },
  offline: { label: '오프라인', color: 'default' },
  stopped: { label: '중지됨', color: 'default' }, // 노드가 종료를 알렸다. 경고하지 않는다
  cooldown: { label: '쿨다운', color: 'warning' },
  blocked: { label: '차단 의심', color: 'error' },
  version: { label: '버전 불일치', color: 'error' },
}

// 조회 노드 관리. 중앙(이 서버)과 원격 노드가 코레일 조회를 나눠 맡는다
export default function Nodes() {
  const { token } = theme.useToken()
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
    load()
  }

  const row = (n: NodeInfo) => {
    const s = STATUS[n.status]
    const detail = [
      n.ip ? `IP ${n.ip}` : null,
      n.lastSeen ? `마지막 poll ${dayjs(n.lastSeen).format('MM/DD HH:mm:ss')}` : n.local ? null : '아직 접속 없음',
      `1시간 성공 ${n.ok1h} · 실패 ${n.fail1h}`,
    ].filter(Boolean).join(' · ')
    return (
      <Flex key={n.id ?? 'local'} align="center" gap={12} style={{ padding: '10px 0', borderTop: `1px solid ${token.colorBorderSecondary}` }}>
        <div
          style={{
            flex: 'none', width: 40, height: 40, borderRadius: 12, display: 'grid', placeItems: 'center', fontSize: 18,
            background: token.colorPrimaryBg, color: token.colorPrimary,
          }}
        >
          {n.local ? <DesktopOutlined /> : <CloudServerOutlined />}
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <Flex wrap gap={6} align="center" style={{ fontWeight: 600 }}>
            <span style={{ wordBreak: 'break-all' }}>{n.name}</span>
            <Tag variant="filled" color={s.color} style={{ borderRadius: 999, margin: 0 }}>{s.label}</Tag>
            {n.prefix && <Typography.Text type="secondary" code style={{ fontSize: 12 }}>{n.prefix}…</Typography.Text>}
            {n.sameIp && (
              <Tag variant="filled" color="warning" icon={<WarningOutlined />} style={{ borderRadius: 999, margin: 0 }}>같은 IP</Tag>
            )}
          </Flex>
          <Typography.Text type="secondary" style={{ fontSize: 13, display: 'block' }}>{detail}</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>
            {n.routes.length ? `맡은 노선 ${n.routes.join(', ')}` : '맡은 노선 없음'}
            {n.status === 'version' && n.version && ` · 노드 pykorail ${n.version}, 다시 빌드하세요`}
          </Typography.Text>
        </div>
        {!n.local && (
          <Popconfirm
            title="이 노드를 폐기할까요?"
            description="토큰이 바로 무효가 됩니다. 다시 붙이려면 새로 발급해야 합니다."
            okText="폐기" cancelText="취소" okButtonProps={{ danger: true }}
            onConfirm={() => revoke(n)}
          >
            <Button shape="circle" size="middle" danger icon={<DeleteOutlined />} aria-label="폐기" />
          </Popconfirm>
        )}
      </Flex>
    )
  }

  return (
    <Flex vertical gap={16} style={{ maxWidth: 760 }}>
      {error && <Alert type="error" showIcon title={error} style={{ borderRadius: 12 }} />}
      <Card
        title={
          <Flex align="center" gap={8}>
            조회 워커
            <Tag variant="filled" style={{ borderRadius: 999, margin: 0 }}>{nodes?.length ?? 0}</Tag>
          </Flex>
        }
        extra={<Button shape="round" size="middle" icon={<PlusOutlined />} onClick={() => setAdding(true)}>노드 추가</Button>}
        styles={{ header: { borderBottom: 0 }, body: { paddingTop: 0 } }}
      >
        <Typography.Paragraph type="secondary" style={{ fontSize: 13 }}>
          공인 IP 가 다른 PC 에 노드를 띄우면 노선을 나눠 조회하고, 워커 수만큼 조회 간격이 줄어듭니다.
          같은 공유기 뒤의 노드는 IP 가 같아 의미가 없습니다.
        </Typography.Paragraph>
        {nodes === null ? null : nodes.length ? nodes.map(row) : (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="조회할 워커가 없습니다 (LOCAL_SCAN=0, 노드 없음)" />
        )}
      </Card>

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
          })
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
        okText="확인"
        cancelButtonProps={{ style: { display: 'none' } }}
        closable={false}
        maskClosable={false}
        onOk={() => setIssued(null)}
      >
        <Alert type="warning" showIcon title="이 토큰은 지금만 볼 수 있습니다. 닫기 전에 복사해 노드의 .env 에 넣으세요." style={{ marginBottom: 12 }} />
        <Typography.Paragraph copyable={{ text: issued?.token }} code style={{ wordBreak: 'break-all' }}>
          NODE_TOKEN={issued?.token}
        </Typography.Paragraph>
        <Typography.Text type="secondary" style={{ fontSize: 13 }}>
          잃어버리면 이 노드를 폐기하고 새로 발급하세요. 노드 실행 방법은 README 의 "조회 노드" 를 보세요.
        </Typography.Text>
      </Modal>
    </Flex>
  )
}
