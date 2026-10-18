// 여러 화면이 같이 쓰는 표시 요소: 패널, 목록 행, 상태 점, 날짜 형식
import type { CSSProperties, ReactNode } from 'react'
import { Card, Flex, Tag, Typography, theme } from 'antd'
import dayjs from 'dayjs'

// 날짜 형식은 이 셋만 쓴다
export const fmtDay = (d: string | number) => dayjs(d).format('M/D(dd)')
export const fmtDateTime = (t: number | null) => (t ? dayjs(t).format('M/D(dd) HH:mm') : '-')
export const fmtStamp = (t: number) => dayjs(t).format('M/D HH:mm:ss')

// 테두리 없는 카드. 제목 옆에 개수, 아래에 설명을 둘 수 있다
export function Panel({ title, count, countDanger, extra, hint, children, style, bodyStyle }: {
  title?: ReactNode
  count?: number
  countDanger?: boolean
  extra?: ReactNode
  hint?: ReactNode
  children?: ReactNode
  style?: CSSProperties
  bodyStyle?: CSSProperties
}) {
  return (
    <Card
      variant="borderless"
      style={{ boxShadow: 'var(--panel-shadow)', ...style }}
      title={title && (
        <Flex align="center" gap={8}>
          {title}
          {count != null && (
            <Tag variant="filled" color={countDanger ? 'error' : undefined} className="num" style={{ margin: 0, borderRadius: 6 }}>{count}</Tag>
          )}
        </Flex>
      )}
      extra={extra}
      styles={{ header: { borderBottom: 0, minHeight: 56 }, body: { paddingTop: title ? 0 : undefined, ...bodyStyle } }}
    >
      {hint && <Typography.Paragraph type="secondary" style={{ fontSize: 13, marginBottom: 8, maxWidth: '62ch' }}>{hint}</Typography.Paragraph>}
      {children}
    </Card>
  )
}

// 아이콘 + 이름·태그 + 설명 + 오른쪽 버튼
export function ListRow({ icon, tone = 'primary', title, tags, desc, actions, first }: {
  icon: ReactNode
  tone?: 'primary' | 'error' | 'neutral'
  title: ReactNode
  tags?: ReactNode
  desc?: ReactNode
  actions?: ReactNode
  first?: boolean
}) {
  const { token } = theme.useToken()
  const tile = {
    primary: [token.colorPrimaryBg, token.colorPrimaryText],
    error: [token.colorErrorBg, token.colorError],
    neutral: [token.colorFillTertiary, token.colorTextSecondary],
  }[tone]
  return (
    <Flex align="center" gap={14} style={{ padding: '12px 0', borderTop: first ? undefined : `1px solid ${token.colorBorderSecondary}` }}>
      <div
        aria-hidden
        style={{
          flex: 'none', width: 40, height: 40, borderRadius: 12, display: 'grid', placeItems: 'center', fontSize: 18,
          background: tile[0], color: tile[1],
        }}
      >
        {icon}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <Flex wrap gap={6} align="center">
          <span style={{ fontWeight: 650, wordBreak: 'break-all' }}>{title}</span>
          {tags}
        </Flex>
        {desc && <div style={{ fontSize: 13, color: token.colorTextSecondary, marginTop: 2 }}>{desc}</div>}
      </div>
      {actions && <Flex gap={6} style={{ flex: 'none' }}>{actions}</Flex>}
    </Flex>
  )
}

export type Tone = 'primary' | 'success' | 'warning' | 'error' | 'neutral'

// 색 점 + 글자. 색만으로 상태를 알리지 않도록 늘 글자를 같이 쓴다
export function StatusDot({ tone, children, pulse }: { tone: Tone; children: ReactNode; pulse?: boolean }) {
  const { token } = theme.useToken()
  const color = {
    primary: token.colorPrimary, success: token.colorSuccess, warning: token.colorWarning, error: token.colorError,
    neutral: token.colorTextQuaternary,
  }[tone]
  // 글자는 다크에서도 읽히는 밝기의 같은 색
  const text = {
    primary: token.colorPrimaryText, success: token.colorSuccessText, warning: token.colorWarningText, error: token.colorErrorText,
    neutral: token.colorTextSecondary,
  }[tone]
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13, fontWeight: 600, color: text }}>
      <span
        aria-hidden
        style={{
          width: 7, height: 7, borderRadius: '50%', background: color, flex: 'none',
          boxShadow: pulse ? `0 0 0 3px ${color}33` : undefined,
        }}
      />
      {children}
    </span>
  )
}

// 감시 카드 노선 사이에 쓰는 작은 열차 아이콘
export function TrainGlyph({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <rect x="5" y="3" width="14" height="14" rx="4" />
      <path d="M5 11h14M9 21l-2-4M15 21l2-4" />
    </svg>
  )
}

// 작은 회색 표(태그 대신 쓰는 조건 표시)
export function Chip({ children, accent }: { children: ReactNode; accent?: boolean }) {
  const { token } = theme.useToken()
  return (
    <span
      style={{
        display: 'inline-flex', alignItems: 'center', height: 24, padding: '0 9px', borderRadius: 7, fontSize: 12.5, fontWeight: 550,
        background: accent ? token.colorPrimaryBg : token.colorFillTertiary, color: accent ? token.colorPrimaryText : token.colorTextSecondary,
        whiteSpace: 'nowrap',
      }}
    >
      {children}
    </span>
  )
}
