import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from pykorail.exceptions import TransportError

from app import db, selftest
from app.config import Settings
from app.health import compute
from app.scanner import TrainInfo
from app.scheduler import Scheduler
from app.state import state

NOW = datetime(2026, 10, 3, 14, 0)
TOMORROW = NOW.date() + timedelta(days=1)


def train(no, hhmm="09:00"):
    d = datetime.combine(TOMORROW, datetime.strptime(hhmm, "%H:%M").time())
    return TrainInfo(date=str(TOMORROW), no=no, type="KTX", dep_at=d, arr_at=d + timedelta(hours=2),
                     general="avail", special="avail")


class FakeGate:
    def station_names(self):
        return {"서울", "부산"}


@pytest.fixture
def env(monkeypatch):
    db.init_engine("sqlite://")
    db.Base.metadata.create_all(db.engine)
    state.settings = Settings(bot_token="", client_id="c", client_secret="s", guild_id=None, admin_ids=(1,),
                              session_secret="x" * 40, block_cooldown_minutes=30)
    state.bot = state.telegram = None
    state.missing_stations, state.selftest = [], None
    state.gate = FakeGate()
    # 서울→부산 내일 09:00 출발 101 만 있는 시간표
    state.timetable = {"days": {str(TOMORROW.weekday()): {"trains": {"101": [["서울", None, 540], ["부산", 660, None]]}}}}
    now = [NOW]
    state.scheduler = Scheduler(scan_day=None, clock=lambda: now[0])
    yield now


def fake_pages(pages):
    """노선 → 첫 페이지(또는 예외). 호출을 기록한다."""
    calls = []

    def scan_pages(gate, dep, arr, day, after, until, pax):
        calls.append((dep, arr))
        page = pages.get((dep, arr), [train("999")])
        if isinstance(page, Exception):
            raise page
        if page:
            yield page

    return scan_pages, calls


def test_probe_ok_when_timetable_train_found(env, monkeypatch):
    scan, calls = fake_pages({("서울", "부산"): [train("101"), train("103", "09:30")]})
    monkeypatch.setattr(selftest.scanner, "scan_pages", scan)
    assert selftest.probe(state.gate, NOW) == ("ok", "테스트 노선 조회 성공")
    assert len(calls) == len(selftest.ROUTES)


def test_probe_block_stops_after_first_route(env, monkeypatch):
    scan, calls = fake_pages({("서울", "부산"): TransportError("HTTP 403: 이용제한 (-2000)")})
    monkeypatch.setattr(selftest.scanner, "scan_pages", scan)
    level, detail = selftest.probe(state.gate, NOW)
    assert level == "critical" and "403" in detail and len(calls) == 1


def test_probe_no_results_is_critical(env, monkeypatch):
    scan, _ = fake_pages({("서울", "부산"): []})
    monkeypatch.setattr(selftest.scanner, "scan_pages", scan)
    assert selftest.probe(state.gate, NOW)[0] == "critical"


def test_probe_timetable_mismatch_is_warning(env, monkeypatch):
    scan, _ = fake_pages({("서울", "부산"): [train("555")]})
    monkeypatch.setattr(selftest.scanner, "scan_pages", scan)
    level, detail = selftest.probe(state.gate, NOW)
    assert level == "warning" and "서울→부산" in detail


def fake_asyncio(now):
    async def sleep(sec):
        now[0] += timedelta(seconds=sec)

    return SimpleNamespace(sleep=sleep, to_thread=asyncio.to_thread)


def test_run_uses_watch_scan_instead_of_probe(env, monkeypatch):
    monkeypatch.setattr(selftest, "asyncio", fake_asyncio(env))
    monkeypatch.setattr(selftest, "probe", lambda *a: pytest.fail("감시 조회가 성공했으면 테스트 조회를 보내지 않는다"))
    state.scheduler.history.append((NOW + timedelta(seconds=5), True, False))
    asyncio.run(selftest.run())
    assert state.selftest is None and state.missing_stations


def test_run_critical_then_retries_after_cooldown(env, monkeypatch):
    monkeypatch.setattr(selftest, "asyncio", fake_asyncio(env))
    results = iter([("critical", "서울→부산 조회 실패"), ("ok", "테스트 노선 조회 성공")])
    probes = []

    def probe(gate, now):
        probes.append(now)
        return next(results)

    monkeypatch.setattr(selftest, "probe", probe)
    asyncio.run(selftest.run())
    assert len(probes) == 2 and probes[1] - probes[0] >= timedelta(minutes=30)
    assert state.selftest is None


def test_health_critical_while_selftest_failed(env):
    state.selftest = {"level": "critical", "detail": "서울→부산 조회 실패"}
    h = compute()
    assert h["level"] == "critical" and h["title"] == "열차 조회 안 됨"
    state.selftest = {"level": "warning", "detail": "시간표에 없는 열차만 조회됨: 서울→부산"}
    assert compute()["level"] == "warning"
