"""열차 시간표(정차역·시각)와 직통 노선. 좌석 조회 전에 화면에 먼저 보여주고, 도착역 목록을 거르는 데 쓴다.

출처는 공공데이터포털 '한국철도공사_열차운행정보'(travelerTrainRunInfo2)다. 전날까지의 실제 운행 기록만 있어서
최근 8주 기록에서 요일마다 평소에 다니는 열차만 골라 그 요일의 시간표로 쓴다(pick_fixed).
명절 임시열차나 하루 운휴는 빠진다. 실제와 다를 수 있고, 좌석 조회 결과가 오면 화면에서 실제 값으로 바뀐다.

앱에 들어 있는 app/data/timetable.json(scripts/build_timetable.py 로 만든 초기 데이터)을 먼저 쓰고,
DATA_GO_KR_KEY 가 있으면 일주일마다 같은 계산을 다시 해 실행 데이터 폴더의 data/timetable.json 에 저장해 그걸 쓴다.
"""

import asyncio
import heapq
import json
import logging
import math
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

from app.state import state
from app.stations import STATION_MAP, TRAIN_GROUPS

log = logging.getLogger(__name__)

SEED = Path(__file__).parent / "data" / "timetable.json"
SAVED = Path("data/timetable.json")  # DB 와 같은 data 폴더(컨테이너 볼륨)
URL = "https://apis.data.go.kr/B551457/run/v2/travelerTrainRunInfo2"
REFRESH_HOURS = 6  # 이 간격으로 확인해서, 만든 지 REBUILD_DAYS 가 지났으면 다시 만든다
REBUILD_DAYS = 7
WEEKS = 8

# 운행정보의 역 이름 중 앱(코레일 조회)과 다르게 적힌 것
ALIAS = {"S수서": "수서", "S동탄": "동탄", "여수엑스포": "여수EXPO", "울산": "울산(통도사)", "진부": "진부(오대산)"}


def kept(no: int) -> bool:
    """시간표에 넣을 열차. 2000번대(ITX-청춘, 관광열차)는 앱의 열차 종류 밖이라 뺀다."""
    return not 2000 <= no < 3000


# 코레일에서 확인한 열차번호 → 종류 표본(scripts/sample_train_types.py). 시간표 열차의 종류를 짐작하는 데만 쓴다
KNOWN: dict[int, str] = {int(k): v for k, v in json.loads(
    (Path(__file__).parent / "data" / "train_types.json").read_text(encoding="utf-8")).items()}
_GROUP = {t: g["name"] for g in TRAIN_GROUPS for t in g["types"]}


def guess(no: int) -> tuple[str, list[str]]:
    """좌석 조회 전에 보여줄 (종류 이름, 묶음). 좌석 조회로 실제 종류가 오면 바뀐다.

    표본에 있는 번호는 그 종류. 처음 보는 번호는 같은 대역(100단위의 앞 50, 뒤 50)의 표본이 한 종류뿐이면 그 종류,
    한 묶음뿐이면 묶음 이름으로 짐작한다(예: 7xx 는 KTX-이음, 1151~ 은 무궁화, 0~49 는 KTX).
    대역에 표본이 없거나 묶음이 섞였으면 번호 규칙을 쓴다.
    """
    if no in KNOWN:
        return KNOWN[no], [_GROUP.get(KNOWN[no], "기타")]
    lo = no // 50 * 50
    near = {t for n, t in KNOWN.items() if lo <= n < lo + 50}
    groups = sorted({_GROUP.get(t, "기타") for t in near})
    if len(near) == 1:
        return near.pop(), groups
    if len(groups) == 1:
        return groups[0], groups
    # ponytail: 표본에서 본 번호 규칙. 1~999, 4000번대 앞쪽(수서행), 9000번대(임시 KTX)는 KTX 계열,
    # 1000번대는 끝 두 자리 50 이상이 무궁화. 코레일이 번호 체계를 바꾸면 scripts/sample_train_types.py 로 표본을 다시 만든다
    if no < 1000 or 4000 <= no < 4100 or no >= 9000:
        return "KTX", ["KTX"]
    if no % 100 >= 50:
        return "무궁화", ["기타"]
    return "ITX", ["ITX", "기타"]


def _minutes(run: date, dt: str | None) -> int | None:
    """운행일 0시부터 몇 분 뒤인지. 자정을 넘으면 1440 이상이 된다."""
    if not dt:
        return None
    t = datetime.strptime(dt[:16], "%Y-%m-%d %H:%M")
    return int((t - datetime.combine(run, datetime.min.time())).total_seconds() // 60)


def build_day(rows: list[dict], stations: set[str]) -> dict[str, list]:
    """운행정보 하루치를 {열차번호: [[역, 도착분, 출발분], ...]} 로 줄인다. 앱의 역만 남긴다."""
    by: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by[r["trn_no"]].append(r)
    out = {}
    for no, rs in by.items():
        if not kept(int(no)):
            continue
        stops = []
        for r in sorted(rs, key=lambda r: int(r["trn_run_sn"])):
            name = ALIAS.get(r["stn_nm"], r["stn_nm"])
            if name in stations:
                run = datetime.strptime(r["run_ymd"], "%Y%m%d").date()
                stops.append([name, _minutes(run, r["trn_arvl_dt"]), _minutes(run, r["trn_dptre_dt"])])
        if len(stops) >= 2:
            out[f"{int(no):03d}"] = stops  # 코레일 조회 결과와 같은 표기("00001" → "001", "01101" → "1101")
    return out


def fetch_day(key: str, day: date) -> list[dict]:
    """하루치 운행정보 전부. 1000행씩 페이지를 넘긴다(하루 약 1만 행, 11번 호출)."""
    out: list[dict] = []
    page = 1
    while True:
        q = urllib.parse.urlencode({"serviceKey": key, "returnType": "json", "pageNo": page, "numOfRows": 1000,
                                    "cond[run_ymd::EQ]": f"{day:%Y%m%d}"})
        with urllib.request.urlopen(f"{URL}?{q}", timeout=30) as r:
            body = json.loads(r.read())["response"]["body"]
        items = body["items"]["item"] or []
        out += items
        if not items or len(out) >= body["totalCount"]:
            return out
        page += 1


def routes(tt: dict) -> dict[str, list[str]]:
    """출발역마다 갈아타지 않고 갈 수 있는 도착역. 한 열차가 A 에 선 뒤 B 에 서면 A→B."""
    out: dict[str, set[str]] = defaultdict(set)
    for d in tt["days"].values():
        for stops in d["trains"].values():
            names = [s[0] for s in stops]
            for i, a in enumerate(names):
                out[a].update(names[i + 1:])
    return {a: sorted(b - {a}) for a, b in out.items()}


def load() -> None:
    """앱에 들어 있는 초기 데이터와 키로 만든 갱신본 중 더 최근에 만든 것을 쓴다. 같은 날 만들었으면 초기 데이터
    (역 목록이 바뀌어 초기 데이터를 다시 만든 경우 앱과 같이 바뀐 쪽)."""
    found = []
    for p in (SEED, SAVED):
        try:
            found.append(json.loads(p.read_text(encoding="utf-8")))
        except FileNotFoundError:
            continue
        except Exception as e:
            log.warning("시간표 파일을 읽지 못했습니다 %s: %s", p, e)
    tt = max(found, key=lambda t: t.get("built", ""), default={"days": {}})
    state.timetable, state.routes = tt, routes(tt)


def static_trains(dep: str, arr: str, start: datetime, end: datetime, basis: str, after: datetime) -> list[dict]:
    """시간표로 만든 조회 결과. 좌석은 아직 모르는 상태('wait')로 둔다."""
    out = []
    run = start.date() - timedelta(days=1)  # 전날 출발해 자정을 넘긴 열차도 본다
    while run <= end.date():
        day = state.timetable["days"].get(str(run.weekday()))
        base = datetime.combine(run, datetime.min.time())
        for no, stops in (day["trains"] if day else {}).items():
            names = [s[0] for s in stops]
            if dep not in names or arr not in names[names.index(dep) + 1:]:
                continue
            d, a = stops[names.index(dep)], stops[names.index(arr)]
            if d[2] is None or a[1] is None:
                continue
            dep_at, arr_at = base + timedelta(minutes=d[2]), base + timedelta(minutes=a[1])
            label, groups = guess(int(no))
            at = dep_at if basis == "dep" else arr_at
            if dep_at < after or not start <= at <= end:
                continue
            out.append({
                "date": f"{dep_at:%Y-%m-%d}", "no": no, "type": None, "label": label, "groups": groups,
                "dep": f"{dep_at:%H:%M}", "arr": f"{arr_at:%H:%M}", "arrDate": f"{arr_at:%Y-%m-%d}",
                "minutes": (a[1] - d[2]), "general": "wait", "special": "wait",
            })
        run += timedelta(days=1)
    return sorted(out, key=lambda t: (t["date"], t["dep"]))


def pick_fixed(samples: list[dict[str, list]]) -> dict[str, list]:
    """한 요일의 여러 주 기록(최근 것부터)에서 평소에 다니는 열차만 고른다.

    절반 이상 다녔고 최근 2주 중 한 번이라도 다녔으면 넣는다(시간표 개정으로 없어진 열차는 바로 빠진다).
    최근 2주 연속 다녔으면 절반이 안 돼도 넣는다(개정으로 새로 생긴 열차는 바로 들어간다).
    정차역·시각은 가장 최근 기록을 쓴다.
    """
    out: dict[str, list] = {}
    recent = samples[:2]
    for no in {n for s in samples for n in s}:
        count = sum(no in s for s in samples)
        in_recent = [no in s for s in recent]
        if (count * 2 >= len(samples) and any(in_recent)) or (len(recent) == 2 and all(in_recent)):
            out[no] = next(s[no] for s in samples if no in s)
    return out


def build(key: str, last: date, stations: set[str], weeks: int = WEEKS) -> dict:
    """last 부터 weeks 주 전까지 하루씩 받아 요일마다 pick_fixed 로 시간표를 만든다(하루 약 11번 호출)."""
    samples: dict[int, list[dict]] = defaultdict(list)
    dates: dict[int, list[str]] = defaultdict(list)
    for i in range(weeks * 7):
        d = last - timedelta(days=i)
        rows = fetch_day(key, d)
        if rows:  # 아직 올라오지 않은 날은 건너뛴다
            samples[d.weekday()].append(build_day(rows, stations))
            dates[d.weekday()].append(d.isoformat())
    return {
        "source": "공공데이터포털 한국철도공사_열차운행정보", "built": last.isoformat(),
        "days": {str(wd): {"from": min(dates[wd]), "to": max(dates[wd]), "samples": len(samples[wd]),
                           "trains": pick_fixed(samples[wd])} for wd in sorted(samples)},
    }


def save(tt: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(tt, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def refresh(key: str, today: date) -> bool:
    """만든 지 REBUILD_DAYS 가 지났으면 어제까지 8주로 다시 만든다. 다시 만들었으면 True."""
    if state.timetable.get("built", "") > (today - timedelta(days=REBUILD_DAYS)).isoformat():
        return False
    tt = build(key, today - timedelta(days=1), set(state.stations))
    if not tt["days"]:
        return False
    save(tt, SAVED)
    state.timetable, state.routes = tt, routes(tt)
    log.info("시간표 갱신: %s 까지 %d주, 열차 %s편(월~일)", tt["built"], WEEKS,
             "/".join(str(len(d["trains"])) for d in tt["days"].values()))
    return True


async def refresh_loop(key: str) -> None:
    from app.db import now_kst

    while True:
        try:
            await asyncio.to_thread(refresh, key, now_kst().date())
        except Exception as e:
            # 오류 문구에 요청 주소(키)가 섞이지 않게 종류와 짧은 설명만 남긴다
            log.warning("시간표 갱신 실패: %s: %s", type(e).__name__, str(e)[:120])
        await asyncio.sleep(REFRESH_HOURS * 3600)


def _graph() -> dict[str, dict[str, float]]:
    """지도 선로(stations.json lines)의 이웃한 역끼리 이은 그래프. 간선 길이는 위경도 거리."""
    pos = {st["name"]: (st["lat"], st["lon"]) for st in STATION_MAP["stations"]}
    g: dict[str, dict[str, float]] = defaultdict(dict)
    for line in STATION_MAP["lines"]:
        for a, b in zip(line, line[1:]):
            g[a][b] = g[b][a] = math.dist(pos[a], pos[b])  # ponytail: 평면 거리. 최단 경로 고르기에만 쓴다
    return g


GRAPH = _graph()
# 지나는 역 하나마다 더하는 거리(위경도 도). 두 정차역 사이에 서지 않고 가는 구간은 대개 역이 적은 고속선이라,
# 거리가 비슷하면 지나는 역이 적은 쪽을 고른다(서울→대전은 영등포·수원 쪽보다 광명·오송 쪽)
HOP = 0.2


@lru_cache(maxsize=4096)
def track(a: str, b: str) -> tuple[str, ...]:
    """선로를 따라 a 에서 b 까지 지나는 역들(a, b 포함). 두 갈래면 짧은 쪽(HOP 참고). 선로로 이어지지 않으면 (a, b)."""
    dist, prev, heap = {a: 0.0}, {}, [(0.0, a)]
    while heap:
        d, u = heapq.heappop(heap)
        if u == b:
            path = [b]
            while path[-1] != a:
                path.append(prev[path[-1]])
            return tuple(reversed(path))
        if d > dist[u]:
            continue
        for v, w in GRAPH[u].items():
            if d + w + HOP < dist.get(v, math.inf):
                dist[v], prev[v] = d + w + HOP, u
                heapq.heappush(heap, (d + w + HOP, v))
    return (a, b)


def route_paths(dep: str, arr: str) -> list[dict]:
    """dep→arr 직통 열차가 지나는 선로 경로. 정차역이 달라도 같은 선로로 가면 한 경로로 묶는다.
    weekly 는 묶음별 주간 편수(요일마다 하루치 시간표를 더한 값). 편수가 많은 경로부터."""
    paths: dict[tuple, dict] = {}
    for d in state.timetable["days"].values():
        for no, stops in d["trains"].items():
            names = [st[0] for st in stops]
            if dep not in names or arr not in names[names.index(dep) + 1:]:
                continue
            seg = names[names.index(dep):names.index(arr) + 1]
            way = [seg[0]]
            for a, b in zip(seg, seg[1:]):
                way += track(a, b)[1:]
            p = paths.setdefault(tuple(way), {"track": way, "stops": set(), "weekly": Counter()})
            p["stops"].update(seg)
            label, _ = guess(int(no))
            p["weekly"][_GROUP.get(label, label)] += 1
    out = [{"track": p["track"], "stops": sorted(p["stops"]), "weekly": dict(p["weekly"])} for p in paths.values()]
    return sorted(out, key=lambda p: -sum(p["weekly"].values()))


def station_rank() -> list[str]:
    """지도에서 역 이름 자리를 먼저 잡는 순서. 주간 정차 편수를 KTX, ITX, 기타 묶음 순으로 비교해 많은 역부터
    (KTX 가 같으면 ITX, 그것도 같으면 기타). 편수가 모두 같으면 stations.json 의 major 역이 먼저."""
    count: dict[str, Counter] = defaultdict(Counter)
    for d in state.timetable["days"].values():
        for no, stops in d["trains"].items():
            label, _ = guess(int(no))
            group = _GROUP.get(label, label)
            for st in stops:
                count[st[0]][group] += 1
    major = {st["name"] for st in STATION_MAP["stations"] if st.get("major")}
    order = [g["name"] for g in TRAIN_GROUPS]
    return sorted(state.stations, key=lambda s: ([-count[s][g] for g in order], s not in major))
