import { useCallback, useEffect, useState } from 'react'
import { Button, Card, Empty, Flex, Segmented, Tag, Typography, theme } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import { api, type LogLevel, type LogLine } from './api'

const LEVEL_COLOR: Record<LogLevel, string> = { DEBUG: 'default', INFO: 'blue', WARNING: 'warning', ERROR: 'error' }
const RANK: Record<LogLevel, number> = { DEBUG: 0, INFO: 1, WARNING: 2, ERROR: 3 }

export default function Logs({ initialMin = 'INFO' }: { initialMin?: LogLevel }) {
  const { token } = theme.useToken()
  const [min, setMin] = useState<LogLevel>(initialMin)
  const [all, setAll] = useState<LogLine[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setAll(await api.logs('DEBUG'))
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : '로그를 불러오지 못했습니다')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const lines = (all ?? []).filter((l) => (RANK[l.level] ?? 3) >= RANK[min]).reverse()

  return (
    <Flex vertical gap={16} style={{ maxWidth: 900 }}>
      <Card styles={{ body: { paddingBlock: 14 } }}>
        <Flex justify="space-between" align="center" wrap gap={12}>
          <Segmented
            shape="round"
            value={min}
            onChange={(v) => setMin(v as LogLevel)}
            options={[
              { value: 'DEBUG', label: '전체' },
              { value: 'INFO', label: '정보 이상' },
              { value: 'WARNING', label: '경고 이상' },
              { value: 'ERROR', label: '오류' },
            ]}
          />
          <Button shape="round" icon={<ReloadOutlined />} loading={loading} onClick={load}>새로고침</Button>
        </Flex>
        <Typography.Paragraph type="secondary" style={{ fontSize: 13, margin: '10px 0 0' }}>
          컨테이너 로그의 최근 1,000줄입니다. 토큰, 웹훅 주소, 채팅 ID, 사용자 ID는 서버에서 가린 뒤 보여줍니다.
        </Typography.Paragraph>
      </Card>

      <Card styles={{ body: { padding: 0 } }}>
        {error ? (
          <Empty style={{ padding: 24 }} description={error} />
        ) : all === null ? (
          <Empty style={{ padding: 24 }} description="불러오는 중" />
        ) : lines.length === 0 ? (
          <Empty style={{ padding: 24 }} description="표시할 로그가 없습니다" />
        ) : (
          lines.map((l, i) => (
            <div
              key={i}
              style={{
                padding: '10px 16px', borderTop: i ? `1px solid ${token.colorBorderSecondary}` : undefined,
                fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, 'D2Coding', monospace", fontSize: 13,
              }}
            >
              <Flex gap={8} align="center" wrap style={{ marginBottom: 2 }}>
                <span style={{ color: token.colorTextTertiary, fontVariantNumeric: 'tabular-nums' }}>{dayjs(l.t).format('MM-DD HH:mm:ss')}</span>
                <Tag color={LEVEL_COLOR[l.level]} variant="filled" style={{ margin: 0, borderRadius: 999, fontFamily: 'inherit' }}>{l.level}</Tag>
                <span style={{ color: token.colorTextSecondary }}>{l.source}</span>
              </Flex>
              <div style={{ wordBreak: 'break-all', color: l.level === 'ERROR' ? token.colorError : undefined }}>{l.msg}</div>
            </div>
          ))
        )}
      </Card>
    </Flex>
  )
}
