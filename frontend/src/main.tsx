import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import dayjs from 'dayjs'
import 'dayjs/locale/ko'
// 한글 글꼴. 쓰는 글자 범위의 조각만 받는다(dynamic subset)
import 'pretendard/dist/web/variable/pretendardvariable-dynamic-subset.css'
import App from './App'
import './index.css'

dayjs.locale('ko')

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
