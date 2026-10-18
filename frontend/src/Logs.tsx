import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Empty, Flex, Segmented, Skeleton, theme } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { api, type LogLevel, type LogLine } from './api'
import { Panel, fmtStamp } from './ui'

const LEVEL: Record<LogLevel, { label: string; color: (t: ReturnType<typeof theme.useToken>['token']) => string }> = {
  DEBUG: { label: 'DEBUG', color: (t) => t.colorTextTertiary },
  INFO: { label: 'INFO', color: (t) => t.colorPrimary },
  WARNING: { label: 'WARN', color: (t) => t.colorWarning },
  ERROR: { label: 'ERROR', color: (t) => t.colorError },
}
const RANK: Record<LogLevel, number> = { DEBUG: 0, INFO: 1, WARNING: 2, ERROR: 3 }
const MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, 'D2Coding', monospace"

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
    <Flex vertical gap={16}>
      <Flex justify="space-between" align="center" wrap gap={12}>
        <Segmented
          value={min}
          onChange={(v) => setMin(v as LogLevel)}
          aria-label="로그 등급"
          options={[
            { value: 'DEBUG', label: '전체' },
            { value: 'INFO', label: '정보 이상' },
            { value: 'WARNING', label: '경고 이상' },
            { value: 'ERROR', label: '오류' },
          ]}
        />
        <Button icon={<ReloadOutlined />} loading={loading} onClick={load}>새로고침</Button>
      </Flex>

      {error && <Alert type="error" showIcon title={error} action={<Button size="small" onClick={load}>다시 시도</Button>} />}

      <Panel bodyStyle={{ padding: 0 }}>
        {all === null ? (
          error ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} style={{ padding: 24 }} description="로그를 불러오지 못했습니다" />
            : <div style={{ padding: 20 }}><Skeleton active paragraph={{ rows: 6 }} title={false} /></div>
        ) : lines.length === 0 ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} style={{ padding: 24 }} description="이 등급의 로그가 없습니다" />
        ) : (
          <div role="log" aria-label="서버 로그" style={{ fontFamily: MONO, fontSize: 12.5 }}>
            {lines.map((l, i) => (
              <div
                key={i}
                style={{
                  padding: '9px 18px', borderTop: i ? `1px solid ${token.colorBorderSecondary}` : undefined,
                  boxShadow: l.level === 'ERROR' || l.level === 'WARNING' ? `inset 3px 0 0 ${LEVEL[l.level].color(token)}` : undefined,
                }}
              >
                <Flex gap={10} align="center" wrap style={{ marginBottom: 2 }}>
                  <span className="num" style={{ color: token.colorTextTertiary }}>{fmtStamp(l.t)}</span>
                  <span style={{ color: LEVEL[l.level].color(token), fontWeight: 700, minWidth: 40 }}>{LEVEL[l.level].label}</span>
                  <span style={{ color: token.colorTextSecondary }}>{l.source}</span>
                </Flex>
                <div style={{ wordBreak: 'break-all', color: l.level === 'ERROR' ? token.colorError : undefined, lineHeight: 1.55 }}>{l.msg}</div>
              </div>
            ))}
          </div>
        )}
      </Panel>
    </Flex>
  )
}
