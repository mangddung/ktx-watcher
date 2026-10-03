"""시작 점검. 백엔드가 뜨고 DELAY 초 뒤에 역 목록을 대조하고 열차 조회가 되는지 확인한다.
코드 수정으로 금방 재시작되면 그 전에 취소돼 코레일 요청이 나가지 않는다.

역 목록은 서명 없는 요청이라 열차 조회가 막혀도 성공한다(2026-10-03 pykorail 0.1.1 차단 때 확인).
그래서 열차 조회를 따로 확인한다. 그 사이 감시 조회가 성공했으면 그걸로 대신하고, 아니면 테스트 노선을 조회한다.
"""

import asyncio
import logging
from datetime import datetime, time, timedelta

from app import scanner, service, timetable
from app.stations import KTX_STATIONS
from app.state import state

log = logging.getLogger(__name__)

DELAY = 30
# 하루 열차가 많아 내일 08시 이후 결과가 0편일 수 없는 노선
ROUTES = [("서울", "부산"), ("용산", "광주송정"), ("청량리", "강릉")]


async def check_stations() -> None:
    """역 마스터와 대조해 없는 역은 목록에서 뺀다. 조회에 실패하면 목록을 그대로 쓴다."""
    try:
        names = await asyncio.to_thread(state.gate.station_names)
    except Exception as e:
        log.warning("역 마스터를 불러오지 못해 역 목록을 그대로 씁니다: %s: %s", type(e).__name__, e)
        return
    state.missing_stations = [s for s in KTX_STATIONS if s not in names]
    if state.missing_stations:
        log.warning("역 마스터에 없는 역을 목록에서 뺍니다: %s", ", ".join(state.missing_stations))
        state.stations = [s for s in KTX_STATIONS if s in names]


def probe(gate, now: datetime) -> tuple[str, str]:
    """테스트 노선마다 내일 08시 이후 한 페이지(요청 1건)를 조회한다. (ok | warning | critical, 설명)."""
    day = now.date() + timedelta(days=1)
    after, until = datetime.combine(day, time(8)), datetime.combine(day, time.max)
    mismatch = []
    for dep, arr in ROUTES:
        try:
            page = next(scanner.scan_pages(gate, dep, arr, day, after, until, 1), None)
        except Exception as e:
            return "critical", f"{dep}→{arr} 조회 실패: {type(e).__name__}: {e}"[:300]  # 차단일 수 있으니 나머지는 보내지 않는다
        if not page:
            return "critical", f"{dep}→{arr} 내일 열차가 하나도 조회되지 않습니다 (응답 이상)"
        # 시간표가 오래됐을 수 있어 조회 실패로 보지 않는다
        expected = {t["no"] for t in timetable.static_trains(dep, arr, after, until, "dep", after)}
        if expected and not expected & {t.no for t in page}:
            mismatch.append(f"{dep}→{arr}")
    if mismatch:
        return "warning", f"시간표에 없는 열차만 조회됨: {', '.join(mismatch)}"
    return "ok", "테스트 노선 조회 성공"


def scan_ok_since(since: datetime) -> bool:
    return any(ok for at, ok, _ in state.scheduler.history if at >= since)


async def report(level: str, detail: str) -> None:
    was = state.selftest
    state.selftest = None if level == "ok" else {"level": level, "detail": detail}
    if level == "critical":
        log.error("[시작 점검] %s", detail)
        if not was or was["level"] != "critical":
            await service.system(f"조회 확인 실패: {detail}")
    elif level == "warning":
        log.warning("[시작 점검] %s", detail)
    else:
        log.info("[시작 점검] %s", detail)
        if was and was["level"] == "critical":
            await service.system("조회 복구")


async def run(delay: float = DELAY, poll: float = 60) -> None:
    """실패하면 block_cooldown_minutes 뒤 테스트 노선만 다시 조회한다. 그 사이 감시 조회가 성공하면 바로 복구로 본다."""
    clock = state.scheduler.clock
    since = clock()
    await asyncio.sleep(delay)
    await check_stations()
    while True:
        if scan_ok_since(since):
            await report("ok", "감시 조회 성공")
            return
        level, detail = await asyncio.to_thread(probe, state.gate, clock())
        await report(level, detail)
        if level != "critical":
            return
        since = clock()
        retry_at = since + timedelta(minutes=state.settings.block_cooldown_minutes)
        while clock() < retry_at and not scan_ok_since(since):
            await asyncio.sleep(poll)
