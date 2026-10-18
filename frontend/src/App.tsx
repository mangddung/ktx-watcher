import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { App as AntApp, Alert, Avatar, Button, ConfigProvider, Dropdown, Flex, Grid, Result, Spin, Typography, theme } from 'antd'
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
type Tab = 'watches' | 'add' | 'channels' | 'nodes' | 'logs'

const THEME_KEY = 'ktx.theme'
const BRAND = '#2458d6'

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
          colorPrimary: BRAND,
          colorInfo: BRAND,
          borderRadius: 10,
          borderRadiusLG: 16,
          borderRadiusSM: 6,
          fontFamily: "'Pretendard Variable', Pretendard, -apple-system, BlinkMacSystemFont, 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif",
          // 다크는 순수한 검정 대신 푸른빛이 도는 짙은 회색. 다크 알고리즘은 파랑을 더 어둡게 만들어서 밝은 파랑을 따로 준다
          colorBgLayout: dark ? '#0c0f15' : '#f3f5f9',
          ...(dark && {
            colorBgContainer: '#141922', colorBgElevated: '#1b2130',
            colorPrimary: '#4a7bf7', colorInfo: '#4a7bf7', colorPrimaryText: '#9db7ff', colorLink: '#9db7ff',
          }),
        },
        components: {
          Card: { bodyPadding: 20, headerFontSize: 15 },
          Button: { fontWeight: 600, primaryShadow: 'none', defaultShadow: 'none', dangerShadow: 'none' },
          Segmented: { trackPadding: 3 },
          Drawer: { footerPaddingBlock: 12 },
        },
      }}
    >
      <AntApp>
        <Shell mode={mode} dark={dark} onMode={changeMode} />
      </AntApp>
    </ConfigProvider>
  )
}

// width: 제목과 본문이 같이 쓰는 가운데 정렬 폭. 입력 화면은 좁게, 카드 목록은 넓게
const PAGES: Record<Tab, { title: string; short: string; desc: string; icon: ReactNode; width: number }> = {
  watches: { title: '감시 목록', short: '감시', desc: '좌석이 나면 고른 채널로 알립니다.', icon: <UnorderedListOutlined />, width: 1056 },
  add: { title: '감시 추가', short: '추가', desc: '구간과 열차 조건을 정하고, 열차를 조회한 뒤 감시를 추가합니다.', icon: <PlusOutlined />, width: 780 },
  channels: { title: '알림 채널', short: '채널', desc: '알림을 받을 Discord·Telegram 채널입니다.', icon: <NotificationOutlined />, width: 780 },
  nodes: { title: '조회 노드', short: '노드', desc: '공인 IP가 다른 PC에서 코레일 조회를 나눠 맡습니다.', icon: <CloudServerOutlined />, width: 780 },
  logs: { title: '로그', short: '로그', desc: '서버 로그의 최근 1,000줄입니다. 토큰·웹훅 주소·ID는 서버에서 가린 뒤 보여줍니다.', icon: <FileTextOutlined />, width: 960 },
}
const TABS = Object.keys(PAGES) as Tab[]

const LOGIN_MESSAGE: Record<string, string> = {
  denied: '관리자로 등록된 Discord 계정이 아닙니다.',
  invalid: '로그인 요청이 만료됐습니다. 다시 시도하세요.',
  failed: 'Discord 로그인에 실패했습니다. 잠시 뒤 다시 시도하세요.',
  cancelled: '로그인을 취소했습니다.',
}

function Shell({ mode, dark, onMode }: { mode: ThemeMode; dark: boolean; onMode: (m: ThemeMode) => void }) {
  const { token } = theme.useToken()
  const { message } = AntApp.useApp()
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
  // 로그인 실패 사유는 주소에서 한 번만 읽고 지운다(새로고침해도 다시 뜨지 않게)
  const [loginReason] = useState(() => {
    const r = new URLSearchParams(location.search).get('login')
    if (r) history.replaceState(null, '', location.pathname)
    return r
  })
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
  }, [me, refresh])

  // index.css 가 쓰는 색. antd 토큰을 그대로 CSS 변수로 내보낸다
  useEffect(() => {
    const root = document.documentElement
    root.dataset.theme = dark ? 'dark' : 'light'
    root.style.colorScheme = dark ? 'dark' : 'light'
    const vars: Record<string, string> = {
      '--bg-layout': token.colorBgLayout, '--bg-container': token.colorBgContainer, '--border': token.colorBorder,
      '--primary': token.colorPrimary, '--primary-text': token.colorPrimaryText, '--primary-bg': token.colorPrimaryBg, '--fill': token.colorFillTertiary,
      '--text-tertiary': token.colorTextTertiary,
    }
    for (const [k, v] of Object.entries(vars)) root.style.setProperty(k, v)
    document.body.style.background = token.colorBgLayout
    document.body.style.color = token.colorText
  }, [dark, token])

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
    const notice = LOGIN_MESSAGE[loginReason ?? '']
    return (
      <main style={{ minHeight: '100dvh', display: 'grid', placeItems: 'center', padding: 16 }}>
        <div style={{ width: '100%', maxWidth: 360 }}>
          <Brand size={52} />
          <Typography.Title level={2} style={{ margin: '20px 0 6px', letterSpacing: '-0.03em' }}>취소표 감시</Typography.Title>
          <Typography.Paragraph type="secondary" style={{ fontSize: 15, marginBottom: 24 }}>
            코레일 취소표를 감시해 Discord·Telegram으로 알려줍니다. 관리자로 등록된 Discord 계정만 로그인할 수 있습니다.
          </Typography.Paragraph>
          {notice && <Alert type="warning" showIcon title={notice} style={{ marginBottom: 16 }} />}
          <Button
            type="primary" block icon={<DiscordOutlined />}
            style={{ background: '#5865F2', height: 52, fontSize: 16 }}
            href="/auth/login"
          >
            Discord로 로그인
          </Button>
        </div>
      </main>
    )
  }

  if (!loggedIn || !config || !watches) {
    return (
      <main style={{ minHeight: '100dvh', display: 'grid', placeItems: 'center', padding: 16 }}>
        {loadError ? (
          <Result status="warning" title="서버에 연결할 수 없습니다" subTitle={loadError}
            extra={<Button type="primary" onClick={() => location.reload()}>다시 시도</Button>} />
        ) : (
          <Spin size="large" />
        )}
      </main>
    )
  }

  const go = (t: Tab) => {
    setLogMin('INFO')
    setTab(t)
  }
  const logout = () => api.logout()
    .then(() => setMe(null))
    .catch((e) => message.error(e instanceof Error ? e.message : '로그아웃하지 못했습니다'))
  const name = me.name ?? '관리자'
  const avatar = (size: number) => (
    <Avatar shape="square" size={size} src={me.avatar ?? undefined} alt={name}
      style={{ background: token.colorPrimary, flex: 'none', borderRadius: size * 0.3 }}>
      {name.slice(0, 1)}
    </Avatar>
  )
  const accountMenu = {
    items: [
      { key: 'who', label: name, disabled: true },
      { type: 'divider' as const },
      { key: 'logout', icon: <LogoutOutlined />, label: '로그아웃' },
    ],
    onClick: ({ key }: { key: string }) => key === 'logout' && logout(),
  }
  const homeButton = (
    <button onClick={() => go('watches')} aria-label="감시 목록으로" style={{ border: 0, background: 'transparent', padding: 0, cursor: 'pointer', color: 'inherit', font: 'inherit' }}>
      <Flex align="center" gap={10}>
        <Brand size={32} />
        <span style={{ fontWeight: 750, fontSize: 16, letterSpacing: '-0.02em' }}>취소표 감시</span>
      </Flex>
    </button>
  )

  const page = PAGES[tab]
  const header = (
    <Flex align="end" justify="space-between" gap={16} wrap style={{ marginBottom: mobile ? 16 : 24 }}>
      <div style={{ minWidth: 0 }}>
        <h1 style={{ margin: 0, fontSize: mobile ? 22 : 28, fontWeight: 750, letterSpacing: '-0.03em', lineHeight: 1.25 }}>{page.title}</h1>
        <Typography.Text type="secondary" style={{ fontSize: mobile ? 13 : 14 }}>{page.desc}</Typography.Text>
      </div>
      {tab === 'watches' && !mobile && (
        <Button type="primary" icon={<PlusOutlined />} onClick={() => go('add')}>감시 추가</Button>
      )}
    </Flex>
  )

  const pages = (
    <ConfigContext.Provider value={config}>
      {header}
      {loadError && (
        <Alert type="error" showIcon title={`서버와 연결이 끊겼습니다: ${loadError}`} style={{ marginBottom: 16 }} />
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

  const skip = <a className="skip-link" href="#main">본문으로 건너뛰기</a>
  const glass = dark ? 'rgba(12, 15, 21, 0.78)' : 'rgba(243, 245, 249, 0.82)'

  // 데스크톱: 위쪽에 로고와 메뉴, 오른쪽 끝에 테마와 계정
  if (!mobile) {
    return (
      <>
        {skip}
        <header
          style={{
            position: 'sticky', top: 0, zIndex: 20, background: glass, backdropFilter: 'saturate(180%) blur(14px)',
            WebkitBackdropFilter: 'saturate(180%) blur(14px)', borderBottom: `1px solid ${token.colorBorderSecondary}`,
          }}
        >
          <Flex align="center" gap={28} style={{ maxWidth: 1120, margin: '0 auto', padding: '0 32px', height: 64 }}>
            {homeButton}
            <nav aria-label="메뉴" style={{ display: 'flex', gap: 4, flex: 1 }}>
              {TABS.map((t) => (
                <button key={t} className="nav-tab" aria-current={t === tab ? 'page' : undefined} onClick={() => go(t)}>
                  {PAGES[t].icon}
                  {screens.lg ? PAGES[t].title : PAGES[t].short}
                </button>
              ))}
            </nav>
            <Dropdown menu={themeMenu} trigger={['click']} placement="bottomRight">
              <Button type="text" shape="circle" icon={themeIcon} aria-label="테마 설정" />
            </Dropdown>
            <Dropdown menu={accountMenu} trigger={['click']} placement="bottomRight">
              <button aria-label={`계정: ${name}`} className="nav-tab" style={{ paddingInline: 6, opacity: 1 }}>{avatar(30)}</button>
            </Dropdown>
          </Flex>
        </header>
        <main id="main" style={{ padding: '32px 32px 64px' }}>
          <div style={{ maxWidth: page.width, margin: '0 auto' }}>{pages}</div>
        </main>
      </>
    )
  }

  // 모바일: 상단 로고와 하단 탭
  return (
    <div style={{ minHeight: '100dvh', paddingBottom: 'calc(96px + env(safe-area-inset-bottom, 0px))' }}>
      {skip}
      <header
        style={{
          position: 'sticky', top: 0, zIndex: 20, paddingTop: 'env(safe-area-inset-top, 0px)', background: glass,
          backdropFilter: 'saturate(180%) blur(14px)', WebkitBackdropFilter: 'saturate(180%) blur(14px)',
          borderBottom: `1px solid ${token.colorBorderSecondary}`,
        }}
      >
        <Flex align="center" gap={8} style={{ padding: '10px 16px' }}>
          <div style={{ flex: 1 }}>{homeButton}</div>
          <Dropdown menu={themeMenu} trigger={['click']}>
            <Button type="text" shape="circle" icon={themeIcon} aria-label="테마 설정" />
          </Dropdown>
          <Dropdown menu={accountMenu} trigger={['click']}>
            <button aria-label={`계정: ${name}`} style={{ border: 0, background: 'transparent', padding: 4, cursor: 'pointer', borderRadius: 12 }}>
              {avatar(34)}
            </button>
          </Dropdown>
        </Flex>
      </header>

      <main id="main" style={{ padding: '20px 16px' }}>{pages}</main>

      <nav
        aria-label="메뉴"
        className="tab-bar"
        style={{
          position: 'fixed', left: 12, right: 12, bottom: 'calc(12px + env(safe-area-inset-bottom, 0px))', zIndex: 20,
          background: token.colorBgElevated, borderRadius: 22, boxShadow: '0 8px 28px rgba(10, 20, 40, 0.16)', padding: 4,
          display: 'flex', border: `1px solid ${token.colorBorderSecondary}`,
        }}
      >
        {TABS.map((t) => {
          const active = t === tab
          return (
            <button
              key={t}
              onClick={() => go(t)}
              aria-current={active ? 'page' : undefined}
              style={{ color: active ? token.colorPrimaryText : token.colorTextSecondary, background: active ? token.colorPrimaryBg : 'transparent' }}
            >
              <span style={{ fontSize: 20 }} aria-hidden>{PAGES[t].icon}</span>
              {PAGES[t].short}
            </button>
          )
        })}
      </nav>
    </div>
  )
}

function Brand({ size }: { size: number }) {
  return (
    <div
      aria-hidden
      style={{
        width: size, height: size, borderRadius: size * 0.3, display: 'inline-grid', placeItems: 'center', flex: 'none',
        background: BRAND, color: '#fff', boxShadow: `inset 0 -${size * 0.08}px 0 rgba(0, 0, 0, 0.18)`,
      }}
    >
      <svg width="58%" height="58%" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="5" y="3" width="14" height="14" rx="4" />
        <path d="M5 11h14M9 21l-2-4M15 21l2-4" />
        <circle cx="9" cy="14" r="0.6" fill="currentColor" />
        <circle cx="15" cy="14" r="0.6" fill="currentColor" />
      </svg>
    </div>
  )
}
