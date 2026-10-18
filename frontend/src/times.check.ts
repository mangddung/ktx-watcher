// anchorTimes 확인: node src/times.check.ts (Node 22.18+ 는 .ts 를 바로 실행한다)
import { anchorTimes } from './times.ts'

const eq = (got: unknown, want: unknown, what: string) => {
  if (JSON.stringify(got) !== JSON.stringify(want)) throw new Error(`${what}: ${JSON.stringify(got)} != ${JSON.stringify(want)}`)
}
const flat = (rows: ReturnType<typeof anchorTimes>) =>
  rows.map((r) => [r.arr && `${r.arr.time}${r.arr.exact ? '' : '*'}`, r.dep && `${r.dep.time}${r.dep.exact ? '' : '*'}`])

// 395 열차 10/14 운행 기록(6분 지연)을 코레일 예정 17:58→18:59 에 맞춘다
const stops = [
  { name: '서울', arr: null, dep: '16:34' },
  { name: '김천구미', arr: '18:04', dep: '18:05' },
  { name: '동대구', arr: '18:25', dep: '18:28' },
  { name: '포항', arr: '19:04', dep: null },
]
eq(flat(anchorTimes(stops, '김천구미', '포항', { dep: '17:58', arr: '18:59' })),
  [[null, '16:27*'], ['17:57*', '17:58'], ['18:19*', '18:22*'], ['18:59', null]], '양 끝은 코레일 값, 사이는 비율, 밖은 옮김')

// 좌석 조회 전(코레일 값 없음)은 시간표 그대로 전부 근사값
eq(flat(anchorTimes(stops, '김천구미', '포항', null)),
  [[null, '16:34*'], ['18:04*', '18:05*'], ['18:25*', '18:28*'], ['19:04*', null]], '코레일 값 없음')

// 자정을 넘는 열차
const night = [
  { name: 'A', arr: null, dep: '23:52' },
  { name: 'B', arr: '00:30', dep: '00:32' },
  { name: 'C', arr: '01:10', dep: null },
]
eq(flat(anchorTimes(night, 'A', 'C', { dep: '23:45', arr: '01:00' })),
  [[null, '23:45'], ['00:22*', '00:23*'], ['01:00', null]], '자정 넘김')

console.log('times.check ok')
