import { useCallback, useEffect, useState, type ReactNode } from 'react'
import {
  App as AntApp, Alert, Avatar, Button, Card, ConfigProvider, Dropdown, Flex, Grid, Menu, Result, Segmented, Space, Spin, Typography, theme,
} from 'antd'
import koKR from 'antd/locale/ko_KR'
import {
  CloudServerOutlined, DesktopOutlined, DiscordOutlined, FileTextOutlined, LogoutOutlined, MoonOutlined, NotificationOutlined, PlusOutlined,
  SunOutlined, UnorderedListOutlined,
} from '@ant-design/icons'
import {
  ApiError, ConfigContext, api, type Channel, type Config, type Health, type LogLevel, type Me, type Overview, type Watch,
} from './api'
import WatchList from './WatchList'
import AddWatch from './AddWatch'
import Channels from './Channels'
import Logs from './Logs'
import Nodes from './Nodes'

type ThemeMode = 'system' | 'light' | 'dark'
export type Tab = 'watches' | 'add' | 'channels' | 'nodes' | 'logs'

const THEME_KEY = 'ktx.theme'

function loadMode(): ThemeMode {
  try {
    const v = localStorage.getItem(THEME_KEY)
    return v === 'light' || v === 'dark' ? v : 'system'
  } catch {
    return 'system'
  }
}

function useSystemDark() {
  const [mq] = useState(() => window.matchMedia('(prefers-color-scheme: dark)'))
  const [dark, setDark] = useState(mq.matches)
  useEffect(() => {
    const on = (e: MediaQueryListEvent) => setDark(e.matches)
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [mq])
  return dark
}

export default function App() {
  const [mode, setMode] = useState<ThemeMode>(loadMode)
  const systemDark = useSystemDark()
  const dark = mode === 'dark' || (mode === 'system' && systemDark)

  const changeMode = (m: ThemeMode) => {
    setMode(m)
    try {
      localStorage.setItem(THEME_KEY, m)
    } catch {
      // 저장 못 해도 이번 세션에는 적용된다
    }
  }

  return (
    <ConfigProvider
      locale={koKR}
      componentSize="large"
      // 가상 스크롤은 모바일 터치 스크롤이 페이지로 새는 문제가 있어 끄고 네이티브 스크롤을 쓴다
      virtual={false}
      theme={{
        algorithm: dark ? theme.darkAlgorithm : theme.defaultAlgorithm,
        token: {
          colorPrimary: '#2458d6',
          borderRadius: 14,
          borderRadiusLG: 20,
          fontFamily: "Pretendard, -apple-system, 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif",
        },
        components: {
          Card: { bodyPadding: 18 },
          Button: { fontWeight: 600 },
          // 다크에서 기본 선택 색(파란 글자)이 파란 배경에 묻혀서 흰 글자로 바꾼다
          Menu: dark ? { itemSelectedColor: '#ffffff', itemSelectedBg: 'rgba(36, 88, 214, 0.55)' } : { itemSelectedBg: '#e8efff' },
        },
      }}
    >
      <AntApp>
        <Shell mode={mode} onMode={changeMode} />
      </AntApp>
    </ConfigProvider>
  )
}

const TITLES: Record<Tab, string> = { watches: '감시 목록', add: '감시 추가', channels: '알림 채널', nodes: '조회 노드', logs: '로그' }

const TABS: { key: Tab; label: string; icon: ReactNode }[] = [
  { key: 'watches', label: '감시', icon: <UnorderedListOutlined /> },
  { key: 'add', label: '추가', icon: <PlusOutlined /> },
  { key: 'channels', label: '채널', icon: <NotificationOutlined /> },
  { key: 'nodes', label: '노드', icon: <CloudServerOutlined /> },
  { key: 'logs', label: '로그', icon: <FileTextOutlined /> },
]

function Shell({ mode, onMode }: { mode: ThemeMode; onMode: (m: ThemeMode) => void }) {
  const { token } = theme.useToken()
  const screens = Grid.useBreakpoint()
  const mobile = !screens.md
  const [me, setMe] = useState<Me | null | undefined>(undefined) // undefined: 확인 중, null: 로그인 안 됨
  const [tab, setTab] = useState<Tab>('watches')
  const [logMin, setLogMin] = useState<LogLevel>('INFO')
  const [config, setConfig] = useState<Config | null>(null)
  const [watches, setWatches] = useState<Watch[] | null>(null)
  const [channels, setChannels] = useState<Channel[]>([])
  const [health, setHealth] = useState<Health | null>(null)
  const [overview, setOverview] = useState<Overview | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const loggedIn = !!me

  const onError = useCallback((e: unknown) => {
    if (e instanceof ApiError && e.status === 401) setMe(null)
    else setLoadError(e instanceof Error ? e.message : '서버에 연결할 수 없습니다')
  }, [])

  const refresh = useCallback(async () => {
    try {
      // config 도 같이 받는다. 봇 연결 상태(botReady)가 페이지를 연 뒤에 바뀔 수 있다
      const [cfg, w, c, h, o] = await Promise.all([api.config(), api.watches(), api.channels(), api.health(), api.overview()])
      setConfig(cfg)
      setWatches(w)
      setChannels(c)
      setHealth(h)
      setOverview(o)
      setLoadError(null)
    } catch (e) {
      onError(e)
    }
  }, [onError])

  useEffect(() => {
    api.me().then(setMe).catch((e) => (e instanceof ApiError && e.status === 401 ? setMe(null) : onError(e)))
  }, [onError])

  useEffect(() => {
    if (!me) return
    refresh()
    // 화면을 보고 있을 때만 5초마다 갱신한다
    const t = setInterval(() => !document.hidden && refresh(), 5000)
    return () => clearInterval(t)
  }, [me, refresh, onError])

  useEffect(() => {
    document.body.style.background = token.colorBgLayout
    document.body.style.margin = '0'
  }, [token.colorBgLayout])

  useEffect(() => {
    window.scrollTo(0, 0)
  }, [tab, loggedIn])

  const themeMenu = {
    selectedKeys: [mode],
    onClick: ({ key }: { key: string }) => onMode(key as ThemeMode),
    items: [
      { key: 'system', icon: <DesktopOutlined />, label: '시스템 설정' },
      { key: 'light', icon: <SunOutlined />, label: '라이트' },
      { key: 'dark', icon: <MoonOutlined />, label: '다크' },
    ],
  }
  const themeIcon = mode === 'light' ? <SunOutlined /> : mode === 'dark' ? <MoonOutlined /> : <DesktopOutlined />

  if (me === null) {
    const reason = new URLSearchParams(location.search).get('login')
    const message = {
      denied: '관리자로 등록된 Discord 계정이 아닙니다.',
      invalid: '로그인 요청이 만료됐습니다. 다시 시도하세요.',
      failed: 'Discord 로그인에 실패했습니다. 잠시 뒤 다시 시도하세요.',
      cancelled: '로그인을 취소했습니다.',
    }[reason ?? '']
    return (
      <div style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', padding: 16 }}>
        <Card style={{ width: '100%', maxWidth: 380, textAlign: 'center' }} styles={{ body: { padding: 32 } }}>
          <Brand size={56} />
          <Typography.Title level={3} style={{ marginTop: 16, marginBottom: 4 }}>취소표 감시</Typography.Title>
          <Typography.Paragraph type="secondary">
            관리자로 등록된 Discord 계정만 로그인할 수 있습니다.
          </Typography.Paragraph>
          {message && <Alert type="warning" showIcon title={message} style={{ marginBottom: 16, textAlign: 'left', borderRadius: 12 }} />}
          <Button
            type="primary" block size="large" icon={<DiscordOutlined />}
            style={{ background: '#5865F2', height: 50 }}
            href="/auth/login"
          >
            Discord로 로그인
          </Button>
        </Card>
      </div>
    )
  }

  if (!loggedIn || !config || !watches) {
    return (
      <div style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', padding: 16 }}>
        {loadError ? (
          <Result status="warning" title="서버에 연결할 수 없습니다" subTitle={loadError}
            extra={<Button type="primary" onClick={() => location.reload()}>다시 시도</Button>} />
        ) : (
          <Spin size="large" />
        )}
      </div>
    )
  }

  const go = (t: Tab) => {
    setLogMin('INFO')
    setTab(t)
  }
  const logout = () => api.logout().finally(() => setMe(null))
  const avatar = (size?: number) => (
    <Avatar size={size} src={me.avatar ?? undefined} style={{ background: token.colorPrimary, flex: 'none' }}>
      {(me.name ?? '관').slice(0, 1)}
    </Avatar>
  )
  const homeButton = (
    <button onClick={() => go('watches')} aria-label="홈으로" style={{ border: 0, background: 'transparent', padding: 0, cursor: 'pointer', color: 'inherit' }}>
      <Space size={10}>
        <Brand size={34} />
        <Typography.Text strong style={{ fontSize: 17 }}>취소표 감시</Typography.Text>
      </Space>
    </button>
  )

  const pages = (
    <ConfigContext.Provider value={config}>
      {loadError && (
        <Alert type="error" showIcon title={`서버와 연결이 끊겼습니다: ${loadError}`} style={{ marginBottom: 16, borderRadius: 12 }} />
      )}
      {tab === 'watches' && (
        <WatchList watches={watches} channels={channels} health={health} overview={overview} onChanged={refresh}
          onAdd={() => go('add')}
          onLogs={() => {
            setLogMin('WARNING')
            setTab('logs')
          }}
        />
      )}
      {tab === 'add' && (
        <AddWatch
          watches={watches} channels={channels} mobile={mobile}
          onCreated={() => {
            refresh()
            go('watches')
          }}
        />
      )}
      {tab === 'channels' && <Channels channels={channels} watches={watches} onChanged={refresh} />}
      {tab === 'nodes' && <Nodes />}
      {tab === 'logs' && <Logs initialMin={logMin} />}
    </ConfigContext.Provider>
  )

  // 데스크톱: 관리 화면에서 흔한 왼쪽 고정 사이드바(로고, 메뉴, 아래쪽에 테마와 계정)
  if (!mobile) {
    return (
      <div style={{ display: 'flex', minHeight: '100vh' }}>
        <aside
          style={{
            width: 232, flex: 'none', position: 'sticky', top: 0, height: '100vh', display: 'flex', flexDirection: 'column',
            background: token.colorBgContainer, borderRight: `1px solid ${token.colorBorderSecondary}`,
          }}
        >
          <div style={{ padding: '20px 20px 16px' }}>{homeButton}</div>
          <Menu
            mode="inline"
            selectedKeys={[tab]}
            onClick={({ key }) => go(key as Tab)}
            items={TABS.map((t) => ({ key: t.key, icon: t.icon, label: TITLES[t.key] }))}
            style={{ borderInlineEnd: 0, flex: 1, padding: '0 8px' }}
          />
          <div style={{ padding: 16, borderTop: `1px solid ${token.colorBorderSecondary}`, display: 'flex', flexDirection: 'column', gap: 12 }}>
            <Segmented
              block
              size="middle"
              value={mode}
              onChange={(v) => onMode(v as ThemeMode)}
              options={[
                { value: 'system', icon: <DesktopOutlined />, title: '시스템 설정' },
                { value: 'light', icon: <SunOutlined />, title: '라이트' },
                { value: 'dark', icon: <MoonOutlined />, title: '다크' },
              ]}
              aria-label="테마"
            />
            <Flex align="center" gap={10}>
              {avatar(32)}
              <Typography.Text ellipsis style={{ flex: 1, minWidth: 0 }}>{me.name ?? '관리자'}</Typography.Text>
              <Button type="text" size="middle" icon={<LogoutOutlined />} onClick={logout} aria-label="로그아웃" title="로그아웃" />
            </Flex>
          </div>
        </aside>
        <main style={{ flex: 1, minWidth: 0 }}>
          <div style={{ maxWidth: 1080, margin: '0 auto', padding: '28px 32px 48px' }}>
            <Typography.Title level={3} style={{ marginTop: 0, marginBottom: 20 }}>{TITLES[tab]}</Typography.Title>
            {pages}
          </div>
        </main>
      </div>
    )
  }

  // 모바일: 상단 로고와 하단 탭
  return (
    <div style={{ minHeight: '100vh', paddingBottom: 96 }}>
      <header
        style={{
          position: 'sticky', top: 'env(safe-area-inset-top, 0px)', zIndex: 10, background: token.colorBgContainer,
          borderBottom: `1px solid ${token.colorBorderSecondary}`,
        }}
      >
        <div style={{ padding: '10px 16px', display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={{ flex: 1 }}>{homeButton}</div>
          <Dropdown menu={themeMenu} trigger={['click']}>
            <Button shape="circle" icon={themeIcon} aria-label="테마 설정" />
          </Dropdown>
          <Dropdown trigger={['click']} menu={{ items: [{ key: 'logout', icon: <LogoutOutlined />, label: '로그아웃' }], onClick: logout }}>
            <span style={{ cursor: 'pointer' }}>{avatar()}</span>
          </Dropdown>
        </div>
      </header>

      <main style={{ padding: 16 }}>{pages}</main>

      <nav
        style={{
          position: 'fixed', left: 12, right: 12, bottom: 'calc(12px + env(safe-area-inset-bottom, 0px))', zIndex: 10,
          background: token.colorBgElevated, borderRadius: 24, boxShadow: token.boxShadowSecondary,
          display: 'flex', border: `1px solid ${token.colorBorderSecondary}`,
        }}
      >
        {TABS.map((t) => {
          const active = t.key === tab
          return (
            <button
              key={t.key}
              onClick={() => go(t.key)}
              style={{
                flex: 1, minHeight: 60, border: 0, background: 'transparent', cursor: 'pointer',
                color: active ? token.colorPrimary : token.colorTextSecondary,
                fontWeight: active ? 700 : 500, fontSize: 12,
                display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 4,
              }}
            >
              <span style={{ fontSize: 20 }}>{t.icon}</span>
              {t.label}
            </button>
          )
        })}
      </nav>
    </div>
  )
}

function Brand({ size }: { size: number }) {
  const { token } = theme.useToken()
  return (
    <div
      style={{
        width: size, height: size, borderRadius: size * 0.32, display: 'inline-grid', placeItems: 'center',
        background: `linear-gradient(135deg, ${token.colorPrimary}, #5b8cff)`, color: '#fff',
        fontWeight: 800, fontSize: size * 0.42,
      }}
    >
      <TrainIcon />
    </div>
  )
}

function TrainIcon() {
  return (
    <svg width="55%" height="55%" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <rect x="5" y="3" width="14" height="14" rx="4" />
      <path d="M5 11h14M9 21l-2-4M15 21l2-4" />
      <circle cx="9" cy="14" r="0.6" fill="currentColor" />
      <circle cx="15" cy="14" r="0.6" fill="currentColor" />
    </svg>
  )
}
