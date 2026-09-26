import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// dev 에서는 /api, /auth 를 백엔드로 넘긴다. docker compose dev 는 http://backend:8000 을 넣는다
const target = process.env.VITE_API_TARGET ?? 'http://localhost:12970'
// 터널 도메인(PUBLIC_BASE_URL)으로 들어오는 요청을 허용한다(vite 는 모르는 Host 를 막는다)
const publicHost = process.env.PUBLIC_BASE_URL ? new URL(process.env.PUBLIC_BASE_URL).hostname : undefined

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    allowedHosts: publicHost ? [publicHost] : [],
    proxy: {
      '/api': { target, changeOrigin: false },
      '/auth': { target, changeOrigin: false },
    },
    // Windows Docker Desktop bind mount 에서 HMR 이 동작하려면 필요
    watch: { usePolling: true, interval: 300 },
  },
})
