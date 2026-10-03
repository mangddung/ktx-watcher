import { useEffect, useState, type ReactNode } from 'react'
import { Button, Flex, Modal, Result, Spin, Tag, Typography, theme } from 'antd'
import { api, useConfig, type Train, type TrainRun } from './api'
import { MapView } from './StationMap'

// 조회한 열차 한 편의 시종착과 정차역. 지도에 전체 운행을 옅게, 고른 구간(dep→arr)을 진하게 그린다
export default function TrainRoute({ train, name, dep, arr, mobile, onClose }: {
  train: Train
  name: ReactNode // 열차 종류 이름
  dep: string
  arr: string
  mobile: boolean
  onClose: () => void
}) {
  const { token } = theme.useToken()
  const config = useConfig()
  const [run, setRun] = useState<TrainRun | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    api.trainRun(train.no, train.date, dep, train.dep).then(setRun, (e) => setError(e instanceof Error ? e.message : '불러오지 못했습니다'))
  }, [train, dep])

  const names = run?.stops.map((s) => s.name) ?? []
  const [i, j] = [names.indexOf(dep), names.lastIndexOf(arr)]
  const inSeg = (n: number) => i <= n && n <= j
  const segment = run ? run.track.slice(run.track.indexOf(dep), run.track.lastIndexOf(arr) + 1) : []

  return (
    <Modal
      open centered width={mobile ? '100%' : 720} onCancel={onClose}
      title={<>{name} {train.no} 운행 정보</>}
      footer={<Button type="primary" shape="round" onClick={onClose}>닫기</Button>}
    >
      {error ? (
        <Result status="info" title={error} subTitle="임시·명절 열차는 시간표에 없을 수 있습니다." />
      ) : !run ? (
        <Flex justify="center" style={{ padding: 40 }}><Spin /></Flex>
      ) : (
        <Flex vertical gap={12}>
          <Typography.Text strong style={{ fontSize: 16 }}>
            {names[0]} → {names[names.length - 1]}
          </Typography.Text>
          <MapView
            stations={config.stations} dep={dep} arr={arr} focus={run.track} priority={names} stops={new Set(names)} only={new Set(names)}
            highlights={[{ track: run.track, width: 3, opacity: 0.35 }, { track: segment, width: 6, opacity: 0.9 }]}
            height={mobile ? '40vh' : 360}
          />
          <Flex vertical>
            {run.stops.map((s, n) => (
              <Flex
                key={s.name} justify="space-between" align="center"
                style={{ padding: '4px 8px', borderRadius: 6, background: inSeg(n) ? token.colorPrimaryBg : undefined }}
              >
                <Flex gap={8} align="center">
                  <Typography.Text strong={inSeg(n)} type={inSeg(n) ? undefined : 'secondary'}>{s.name}</Typography.Text>
                  {n === i && <Tag color="blue" style={{ margin: 0 }}>출발</Tag>}
                  {n === j && <Tag color="red" style={{ margin: 0 }}>도착</Tag>}
                </Flex>
                <Typography.Text type={inSeg(n) ? undefined : 'secondary'} style={{ fontVariantNumeric: 'tabular-nums' }}>
                  {[s.arr, s.dep].filter(Boolean).join(' - ')}
                </Typography.Text>
              </Flex>
            ))}
          </Flex>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            평소 운행 시간표 기준이며 이 앱의 역(KTX 정차역)만 보여줍니다. 실제 시각·시종착역과 다를 수 있습니다.
          </Typography.Text>
        </Flex>
      )}
    </Modal>
  )
}
