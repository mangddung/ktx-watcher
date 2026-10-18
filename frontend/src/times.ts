// 노선 창의 정차역 시각. 시간표는 공공데이터의 실제 운행 기록이라 그날의 지연이 섞여 있다.
// 코레일 조회가 준 예정 시각에 맞춘다: 출발역 출발·도착역 도착은 코레일 값 그대로(정확),
// 그 사이 역은 시간표의 역 사이 소요 비율로 나누고, 구간 밖 역은 가까운 끝의 차이만큼 옮긴다(근사).
// 코레일 중간역 시각은 구간마다 따로 조회해야 해서(요청이 몇 배) 받지 않는다.

export interface StopTime { name: string; arr: string | null; dep: string | null }
export interface Shown { time: string; exact: boolean }

const toMin = (t: string) => {
  const [h, m] = t.split(':').map(Number)
  return h * 60 + m
}

const fmt = (m: number) => {
  const x = ((Math.round(m) % 1440) + 1440) % 1440
  return `${String(Math.floor(x / 60)).padStart(2, '0')}:${String(x % 60).padStart(2, '0')}`
}

// korail: 조회한 출발역→도착역의 코레일 예정 시각. 없으면(좌석 조회 전 시간표 열차) 전부 근사값으로 둔다
export function anchorTimes(stops: StopTime[], dep: string, arr: string, korail: { dep: string; arr: string } | null) {
  // 자정을 넘어도 계속 늘어나는 분으로 편다(시간표는 HH:MM 이라 00:10 이 23:50 뒤에 온다)
  let last = -1
  let add = 0
  const unwrap = (t: string | null) => {
    if (t == null) return null
    let m = toMin(t) + add
    if (m < last) {
      add += 1440
      m += 1440
    }
    last = m
    return m
  }
  const rows = stops.map((s) => ({ arr: unwrap(s.arr), dep: unwrap(s.dep) }))
  const names = stops.map((s) => s.name)
  const i = names.indexOf(dep)
  const j = names.lastIndexOf(arr)
  const tDep = i >= 0 ? rows[i].dep : null
  const tArr = j > i ? rows[j].arr : null

  const anchored = korail != null && tDep != null && tArr != null && tArr > tDep
  let map = (t: number) => t
  if (anchored) {
    // 코레일 출발을 시간표와 같은 날 기준으로 둔다(둘의 차이는 반나절보다 작다)
    const k0 = toMin(korail.dep) + Math.round((tDep - toMin(korail.dep)) / 1440) * 1440
    const k1 = k0 + (((toMin(korail.arr) - toMin(korail.dep)) % 1440 + 1440) % 1440 || 1440)
    map = (t) => (t < tDep ? t + k0 - tDep : t > tArr ? t + k1 - tArr : k0 + ((t - tDep) * (k1 - k0)) / (tArr - tDep))
  }
  const shown = (t: number | null, exact: boolean): Shown | null => (t == null ? null : { time: fmt(map(t)), exact: anchored && exact })
  return rows.map((r, n) => ({ arr: shown(r.arr, n === j), dep: shown(r.dep, n === i) }))
}
