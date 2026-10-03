import asyncio
from datetime import date, datetime, timedelta

import pytest
from pykorail.exceptions import TransportError
from sqlalchemy import select

from app import db, service
from app.config import Settings
from app.health import compute
from app.models import Channel, Watch
from app.notify import ChannelGone, SendFailed
from app.scanner import TrainInfo
from app.scheduler import Scheduler
from app.state import state

NOW = datetime(2026, 9, 27, 12, 0)


class FakeNotifier:
    def __init__(self):
        self.sent: list[tuple[str, str]] = []
        self.gone: set[str] = set()
        self.failing: set[str] = set()  # 일시적 오류(SendFailed)로 실패하는 채널
        self.error: Exception | None = None  # 있으면 모든 전송이 이 예외로 실패한다

    async def send(self, ch, text):
        if ch.target in self.gone:
            raise ChannelGone("Unknown Webhook")
        if ch.target in self.failing:
            raise SendFailed("HTTP 502")
        if self.error:
            raise self.error
        self.sent.append((ch.name, text))
        return str(len(self.sent))

    def found(self):
        return [t for _, t in self.sent if t.startswith("취소표 발견")]


def train(no, dep, minutes=110, general="avail", special="soldout", type_="KTX"):
    d = datetime.strptime(dep, "%Y-%m-%d %H:%M")
    return TrainInfo(date=d.strftime("%Y-%m-%d"), no=no, type=type_, dep_at=d, arr_at=d + timedelta(minutes=minutes),
                     general=general, special=special)


class FakeScan:
    """(출발, 도착, 날짜) 별 가짜 시간표. 호출 횟수를 센다."""

    def __init__(self, trains):
        self.trains = trains
        self.calls = []
        self.error = None

    def __call__(self, gate, dep, arr, day, after, until, pax):
        self.calls.append((dep, arr, day, pax))
        if self.error:
            raise self.error
        return [t for t in self.trains if t.dep_at.date() == day and after <= t.dep_at <= until]


@pytest.fixture
def env():
    db.init_engine("sqlite://")
    db.Base.metadata.create_all(db.engine)
    state.settings = Settings(bot_token="", client_id="c", client_secret="s", guild_id=None, admin_ids=(1,),
                              session_secret="x" * 40, max_fails=3, max_active=3)
    state.stations = ["동대구", "수원", "서울", "부산"]
    state.notifier = FakeNotifier()
    state.bot = None
    state.telegram = None
    state.missing_stations = []
    state.timetable = {"days": {}}
    yield state.notifier


def run(sch, times=1):
    for _ in range(times):
        asyncio.run(sch.cycle())


def make_scheduler(scan, now=NOW):
    sch = Scheduler(scan_day=scan, clock=lambda: now)
    state.scheduler = sch
    return sch


def add_watch(start, end, basis="dep", types=("KTX",), seats=("일반실",), passengers=1, trains=None, status="active",
              dep="동대구", arr="수원"):
    with db.SessionLocal() as s:
        ch = Channel(kind="discord_webhook", name=f"hook{start}{passengers}{dep}", target=f"https://x/{start}{passengers}{dep}")
        w = Watch(dep=dep, arr=arr, start_at=datetime.strptime(start, "%Y-%m-%d %H:%M"),
                  end_at=datetime.strptime(end, "%Y-%m-%d %H:%M"), time_basis=basis, train_types=list(types),
                  seat_classes=list(seats), passengers=passengers, trains=trains, status=status, channels=[ch])
        s.add(w)
        s.commit()
        return w.id


def get(wid):
    with db.SessionLocal() as s:
        return s.get(Watch, wid)


def test_continuous_window_across_days_is_grouped_per_date(env):
    scan = FakeScan([train("101", "2026-09-28 06:00"), train("103", "2026-09-28 08:00"), train("105", "2026-09-28 20:00"),
                     train("107", "2026-09-29 09:00"), train("109", "2026-09-29 14:00")])
    sch = make_scheduler(scan)
    wid = add_watch("2026-09-28 07:00", "2026-09-29 13:00")
    run(sch)
    assert sch.groups == 2 and len(scan.calls) == 2
    msg = env.found()[0]
    assert "103" in msg and "105" in msg and "107" in msg and "101" not in msg and "109" not in msg
    assert get(wid).remaining == 3


def test_overlapping_watches_share_one_request_per_group(env):
    scan = FakeScan([train("101", "2026-09-28 09:00")])
    sch = make_scheduler(scan)
    add_watch("2026-09-28 07:00", "2026-09-28 10:00")
    add_watch("2026-09-28 08:00", "2026-09-28 13:00")
    add_watch("2026-09-28 09:00", "2026-09-28 18:00")
    add_watch("2026-09-28 09:00", "2026-09-28 18:00", passengers=2)  # 인원수가 다르면 따로 조회
    run(sch)
    assert sorted(c[3] for c in scan.calls) == [1, 2]


def test_notify_once_then_again_after_resold(env):
    avail = [train("101", "2026-09-28 09:00")]
    scan = FakeScan(avail)
    sch = make_scheduler(scan)
    add_watch("2026-09-28 07:00", "2026-09-28 12:00")
    run(sch, 2)
    assert len(env.found()) == 1
    scan.trains = [train("101", "2026-09-28 09:00", general="soldout")]
    run(sch)
    scan.trains = avail
    run(sch)
    assert len(env.found()) == 2


def test_seat_class_and_type_filters(env):
    scan = FakeScan([train("101", "2026-09-28 09:00", general="soldout", special="avail"),
                     train("1003", "2026-09-28 10:00", type_="ITX-새마을")])
    sch = make_scheduler(scan)
    add_watch("2026-09-28 07:00", "2026-09-28 12:00", seats=("일반실",))
    run(sch)
    assert env.found() == []


def test_cutoff_passed_ends_watch_without_request(env):
    scan = FakeScan([train("101", "2026-09-28 08:00")])
    sch = make_scheduler(scan, now=datetime(2026, 9, 28, 7, 30))
    wid = add_watch("2026-09-28 07:00", "2026-09-28 08:00")
    run(sch)
    assert scan.calls == []
    w = get(wid)
    assert w.status == "ended" and w.end_reason == "no_targets"
    assert any(t.startswith("감시 종료") for _, t in env.sent)


def test_no_matching_trains_after_scan_ends_watch(env):
    scan = FakeScan([train("1003", "2026-09-28 10:00", type_="ITX-새마을")])
    sch = make_scheduler(scan)
    wid = add_watch("2026-09-28 07:00", "2026-09-28 12:00", types=("KTX",))
    run(sch)
    assert get(wid).status == "ended"


def test_empty_result_keeps_watch_while_timetable_expects_trains(env):
    sch = make_scheduler(FakeScan([]))
    wid = add_watch("2026-09-28 07:00", "2026-09-28 12:00")
    # 9/28 은 월요일. 시간표에는 09:00 동대구 출발 열차가 있다
    state.timetable = {"days": {"0": {"trains": {"101": [["동대구", None, 540], ["수원", 600, None]]}}}}
    run(sch)
    w = get(wid)
    assert w.status == "active" and "0편" in w.last_error  # 코레일 응답 이상일 수 있어 끝내지 않는다
    state.timetable = {"days": {}}
    run(sch)
    assert get(wid).status == "ended"  # 시간표에도 없으면 감시 대상 없음


def test_failed_channel_alone_gets_resent_while_seat_remains(env):
    scan = FakeScan([train("101", "2026-09-28 09:00"), train("103", "2026-09-28 10:00")])
    sch = make_scheduler(scan)
    wid = add_watch("2026-09-28 07:00", "2026-09-28 12:00")
    with db.SessionLocal() as s:
        w = s.get(Watch, wid)
        w.channels.append(Channel(kind="discord_webhook", name="flaky", target="https://x/flaky"))
        s.commit()
    env.failing.add("https://x/flaky")
    run(sch)
    assert [n for n, _ in env.sent] == [get(wid).channels[0].name]  # 다른 채널은 받았다
    env.failing.clear()
    scan.trains = [train("101", "2026-09-28 09:00"), train("103", "2026-09-28 10:00", general="soldout")]
    run(sch, 2)  # 못 받은 채널에만, 그 사이 다시 매진된 103 은 빼고 한 번만 다시 보낸다
    name, text = env.sent[-1]
    assert len(env.sent) == 2 and name == "flaky" and "재전송" in text and "101" in text and "103" not in text


def test_undelivered_alert_gives_up_after_retries_and_warns(env):
    sch = make_scheduler(FakeScan([train("101", "2026-09-28 09:00")]))
    wid = add_watch("2026-09-28 07:00", "2026-09-28 12:00")
    env.failing.add(get(wid).channels[0].target)
    run(sch, 2)  # 최초 전송, 재시도 1회 실패
    assert get(wid).last_error is None and compute()["level"] == "ok"
    run(sch)  # 재시도 2회 실패: 포기하고 경고
    h = compute()
    assert h["level"] == "warning" and any(r["label"] == "알림 전송 실패" for r in h["reasons"])
    assert get(wid).last_error.startswith("알림 전송 실패: ")
    env.failing.clear()
    run(sch)
    assert env.found() == [] and get(wid).last_error.startswith("알림 전송 실패: ")  # 포기한 알림은 보내지 않고 경고는 남긴다


def test_notify_error_is_not_counted_as_scan_failure(env):
    sch = make_scheduler(FakeScan([train("101", "2026-09-28 09:00")]))
    add_watch("2026-09-28 07:00", "2026-09-28 12:00")
    env.error = RuntimeError("boom")
    run(sch)
    assert sch.fails == 0 and all(ok for _, ok, _ in sch.history)


def test_paused_watch_is_not_scanned_but_still_expires(env):
    scan = FakeScan([train("101", "2026-09-28 09:00")])
    sch = make_scheduler(scan)
    wid = add_watch("2026-09-28 07:00", "2026-09-28 12:00", status="paused")
    run(sch)
    assert scan.calls == [] and get(wid).status == "paused"
    sch.clock = lambda: datetime(2026, 9, 28, 11, 30)
    run(sch)
    assert get(wid).status == "ended"


def test_arrival_basis_counts_overnight_arrival(env):
    scan = FakeScan([train("101", "2026-09-28 20:00", minutes=90), train("103", "2026-09-28 23:00", minutes=90)])
    sch = make_scheduler(scan)
    add_watch("2026-09-28 22:00", "2026-09-29 01:00", basis="arr")
    run(sch)
    msg = env.found()[0]
    assert "103" in msg and "101" not in msg


def test_selected_trains_only(env):
    scan = FakeScan([train("101", "2026-09-28 09:00"), train("103", "2026-09-28 10:00")])
    sch = make_scheduler(scan)
    add_watch("2026-09-28 07:00", "2026-09-28 12:00", trains=[{"date": "2026-09-28", "no": "103", "dep": "10:00"}])
    run(sch)
    msg = env.found()[0]
    assert "103" in msg and "101" not in msg


def test_repeated_block_errors_pause_scanning_and_go_critical(env):
    scan = FakeScan([train("101", "2026-09-28 09:00")])
    scan.error = TransportError("코레일 응답을 JSON 으로 읽지 못했습니다: '<html>403 Forbidden'")
    sch = make_scheduler(scan)
    wid = add_watch("2026-09-28 07:00", "2026-09-28 12:00")
    run(sch, 3)
    assert sch.cooldown_until is not None
    assert compute()["level"] == "critical"
    calls = len(scan.calls)
    run(sch)  # 쿨다운 중에는 요청하지 않는다
    assert len(scan.calls) == calls
    assert get(wid).error_count == 3
    sch.clock = lambda: NOW + timedelta(minutes=31)
    scan.error = None
    run(sch)  # 쿨다운이 끝나면 다시 조회한다
    assert len(scan.calls) == calls + 1 and sch.cooldown_until is None


def test_single_error_is_warning(env):
    scan = FakeScan([train("101", "2026-09-28 09:00")])
    sch = make_scheduler(scan)
    add_watch("2026-09-28 07:00", "2026-09-28 12:00")
    scan.error = RuntimeError("timeout")
    run(sch)
    scan.error = None
    run(sch)
    h = compute()
    assert h["level"] == "warning" and h["errors1h"] == 1


def test_gone_channel_pauses_watch(env):
    scan = FakeScan([train("101", "2026-09-28 09:00")])
    sch = make_scheduler(scan)
    wid = add_watch("2026-09-28 07:00", "2026-09-28 12:00")
    env.gone.add(get(wid).channels[0].target)
    run(sch)
    w = get(wid)
    assert w.status == "paused" and "사라졌습니다" in w.pause_reason
    with db.SessionLocal() as s:
        assert s.scalar(select(Channel).where(Channel.id == w.channels[0].id)).broken_at is not None


def test_manual_pause_stops_scan_and_resumes_on_same_channel(env):
    scan = FakeScan([train("101", "2026-09-28 09:00", general="soldout")])
    sch = make_scheduler(scan)
    wid = add_watch("2026-09-28 07:00", "2026-09-28 12:00")
    asyncio.run(service.pause_watch(wid))
    assert get(wid).status == "paused"
    run(sch)
    assert scan.calls == []
    assert compute()["level"] == "ok"  # 수동 일시정지는 서비스 경고가 아니다
    asyncio.run(service.resume_watch(wid))
    w = get(wid)
    assert w.status == "active" and w.pause_reason is None and len(w.channels) == 1
    run(sch)
    assert len(scan.calls) == 1
    assert [t.split(" ")[0:2] for _, t in env.sent] == [["감시", "일시정지"], ["감시", "재개"]]


def test_resume_without_channel_pick_needs_live_channel(env):
    make_scheduler(FakeScan([]))
    wid = add_watch("2026-09-28 07:00", "2026-09-28 12:00")
    asyncio.run(service.pause_watch(wid))
    asyncio.run(service.channel_gone(get(wid).channels[0].id, "Unknown Webhook"))
    with pytest.raises(service.Invalid, match="다시 지정"):
        asyncio.run(service.resume_watch(wid))
    assert get(wid).status == "paused"


def valid_input(**kw):
    base = dict(dep="동대구", arr="수원", start_at=datetime(2026, 9, 28, 7), end_at=datetime(2026, 9, 28, 13), basis="dep",
                train_types=["KTX"], seats=["일반실"], channel=service.ChannelPick("manual", [], []))
    base.update(kw)
    return service.WatchInput(**base)


def test_validation_rules(env):
    add_watch("2026-09-28 07:00", "2026-09-28 12:00")
    with db.SessionLocal() as s:
        ch_id = s.scalar(select(Channel.id))
    pick = service.ChannelPick("manual", [], [ch_id])
    service.validate(valid_input(channel=pick), now=NOW)
    for bad in (dict(end_at=datetime(2026, 9, 28, 6)), dict(end_at=datetime(2026, 10, 1, 7)),
                dict(dep="수원"), dict(arr="강남"), dict(train_types=[]), dict(passengers=0)):
        with pytest.raises(service.Invalid):
            service.validate(valid_input(channel=pick, **bad), now=NOW)
    # 3일(시작일 포함)까지는 된다
    service.validate(valid_input(channel=pick, end_at=datetime(2026, 9, 30, 23, 0)), now=NOW)


def test_max_active_watches(env):
    for i in range(3):
        add_watch(f"2026-09-28 0{i + 1}:00", "2026-09-28 12:00")
    with db.SessionLocal() as s:
        ch_id = s.scalar(select(Channel.id))
    with pytest.raises(service.Invalid, match="최대 3개"):
        service.validate(valid_input(channel=service.ChannelPick("manual", [], [ch_id])), now=NOW)


def test_dedicated_channel_messages_hide_watch_info():
    shared = service.compose("취소표 발견", "#3 동대구→수원 9/28(월) 07:00 ~ 13:00 출발", "", "KTX · 일반실", "KTX 122 …", False)
    dedicated = service.compose("취소표 발견", "#3 동대구→수원 9/28(월) 07:00 ~ 13:00 출발", "", "KTX · 일반실", "KTX 122 …", True)
    assert shared.startswith("취소표 발견 #3 동대구→수원") and "KTX · 일반실" in shared
    assert dedicated == "취소표 발견\nKTX 122 …"
    assert service.compose("감시 종료", "#3 …", " · 수동 종료", "", "", True) == "감시 종료 · 수동 종료"


def test_service_stop_and_start_are_announced_to_active_watches(env):
    active = add_watch("2026-09-28 07:00", "2026-09-28 12:00")
    add_watch("2026-09-28 08:00", "2026-09-28 12:00", status="paused")
    asyncio.run(service.announce_active("감시 중단", " · 서비스 종료"))
    sent = [t for _, t in env.sent]
    assert len(sent) == 1 and sent[0].startswith("감시 중단 #%d" % active) and sent[0].endswith("· 서비스 종료")


def test_found_seats_are_sent_before_later_groups_are_scanned(env):
    """사이클 끝까지 기다리지 않고, 자기 조회가 끝난 감시는 바로 알린다."""
    scan = FakeScan([train("101", "2026-09-28 09:00"),
                     TrainInfo(date="2026-09-28", no="201", type="KTX", dep_at=datetime(2026, 9, 28, 9, 30),
                               arr_at=datetime(2026, 9, 28, 11, 0), general="soldout", special="soldout")])
    sent_at_call = []
    inner = scan.__call__

    def recording(gate, dep, arr, day, after, until, pax):
        sent_at_call.append((dep, len(env.found())))
        return inner(gate, dep, arr, day, after, until, pax) if dep == "동대구" else []

    sch = make_scheduler(recording)
    add_watch("2026-09-28 07:00", "2026-09-28 12:00", dep="동대구", arr="수원")
    add_watch("2026-09-28 07:00", "2026-09-28 12:00", dep="서울", arr="부산")
    run(sch)
    # 동대구 묶음이 먼저 조회되고(정렬), 서울 묶음을 조회할 때는 이미 발견 알림이 나가 있다
    assert sent_at_call == [("동대구", 0), ("서울", 1)]


def test_archive_without_bot_is_rejected_and_keeps_channel(env):
    wid = add_watch("2026-09-28 07:00", "2026-09-28 13:00", status="ended")
    with db.SessionLocal() as s:
        s.get(Watch, wid).channels[0].managed = True
        s.commit()
    with pytest.raises(service.Invalid):
        asyncio.run(service.archive_watch(wid))
    w = get(wid)
    assert w.archived_at is None
    with db.SessionLocal() as s:
        assert len(s.get(Watch, wid).channels) == 1


class FakeBot:
    ready = True

    def __init__(self):
        self.calls = []

    async def set_viewers(self, channel_id, viewers):
        self.calls.append((channel_id, viewers))


def managed_watch():
    wid = add_watch("2026-09-28 07:00", "2026-09-28 13:00")
    with db.SessionLocal() as s:
        ch = s.get(Watch, wid).channels[0]
        ch.kind, ch.target, ch.managed = "discord_bot", "555", True
        s.commit()
    return wid


def viewer_ids(wid):
    with db.SessionLocal() as s:
        return sorted(v.discord_user_id for v in s.get(Watch, wid).viewers)


def test_set_viewers_updates_discord_then_db_and_keeps_existing_member(env):
    wid = managed_watch()
    state.bot = FakeBot()
    asyncio.run(service.set_viewers(wid, [7, 8]))
    asyncio.run(service.set_viewers(wid, [8, 9, 9]))  # 8 은 남기고 7 은 빼고 9 는 추가
    assert state.bot.calls == [(555, [7, 8]), (555, [8, 9, 9])]
    assert viewer_ids(wid) == [8, 9]


def test_set_viewers_without_bot_or_managed_channel_is_rejected(env):
    wid = managed_watch()
    with pytest.raises(service.Invalid):
        asyncio.run(service.set_viewers(wid, [7]))  # 봇 없음
    plain = add_watch("2026-09-28 07:00", "2026-09-28 13:00", dep="서울")
    state.bot = FakeBot()
    with pytest.raises(service.Invalid):
        asyncio.run(service.set_viewers(plain, [7]))  # 웹훅 채널만 있음
    assert viewer_ids(wid) == [] and state.bot.calls == []


class CreatingBot(FakeBot):
    """채널을 만드는 도중에 봇 이벤트(resync)가 같은 채널을 일반 채널로 먼저 넣는 경우를 흉내 낸다."""

    fail = False

    async def create_watch_channel(self, name, viewers):
        if self.fail:
            raise RuntimeError("discord down")
        service.sync_discord_channels([(777, name)], None)
        return 777


def auto_input():
    start = (datetime.now() + timedelta(days=2)).replace(hour=7, minute=0, second=0, microsecond=0)
    return service.WatchInput(dep="동대구", arr="수원", start_at=start, end_at=start + timedelta(hours=6), basis="dep",
                              train_types=["KTX"], seats=["일반실"], channel=service.ChannelPick(mode="auto", viewers=[5]))


def test_auto_channel_reuses_row_inserted_by_resync(env):
    state.bot = CreatingBot()
    w = asyncio.run(service.create_watch(auto_input()))
    with db.SessionLocal() as s:
        rows = s.scalars(select(Channel).where(Channel.target == "777")).all()
        assert len(rows) == 1 and rows[0].managed
        got = s.get(Watch, w.id)
        assert got.status == "active" and [c.target for c in got.channels] == ["777"]
    assert viewer_ids(w.id) == [5]


def test_auto_channel_failure_removes_watch(env):
    state.bot = CreatingBot()
    state.bot.fail = True
    with pytest.raises(RuntimeError):
        asyncio.run(service.create_watch(auto_input()))
    with db.SessionLocal() as s:
        assert s.scalar(select(Watch)) is None


def test_long_found_message_is_split_by_line_without_losing_trains(env):
    wid = add_watch("2026-09-28 07:00", "2026-09-28 13:00")
    lines = [f"KTX-산천 {100 + i}  9/28(월) 07:00→09:30  일반실, 특실" for i in range(120)]
    asyncio.run(service.notify_watch(wid, "취소표 발견", body="\n".join(lines)))
    parts = [t for _, t in env.sent]
    assert len(parts) > 1 and all(len(p) <= 1990 for p in parts)
    got = "\n".join(parts)
    assert all(line in got for line in lines)


def test_auto_channel_start_message_has_watch_info(env):
    state.bot = CreatingBot()
    inp = auto_input()
    inp.train_types = ["KTX", "KTX-산천", "ITX-마음", "무궁화"]
    inp.trains = [{"date": f"{inp.start_at:%Y-%m-%d}", "no": "122", "dep": "07:30"},
                  {"date": f"{inp.start_at:%Y-%m-%d}", "no": "124", "dep": "08:30"}]
    w = asyncio.run(service.create_watch(inp))
    [(name, text)] = env.sent
    assert name.startswith(f"w{w.id}-")  # 자동으로 만든 전용 채널
    lines = text.split("\n")
    assert lines[0] == f"감시 시작 {service.describe(w)}"
    assert lines[1] == "KTX, ITX, 무궁화호 · 일반실 · 1명 · 선택 2편"
    assert lines[2] == f"열차: {service.md(inp.start_at)} 122(07:30), 124(08:30)"  # 같은 날짜는 한 번만


def test_picked_trains_narrow_scan_window_to_their_times():
    from app.scheduler import build_groups

    w = Watch(dep="동대구", arr="수원", start_at=datetime(2026, 9, 28, 0, 0), end_at=datetime(2026, 9, 29, 23, 59),
              time_basis="dep", trains=[{"date": "2026-09-28", "no": "122", "dep": "10:00"},
                                        {"date": "2026-09-28", "no": "126", "dep": "12:30"}])
    groups = build_groups([w], datetime(2026, 9, 27, 12, 0), timedelta(minutes=60))
    [g] = groups.values()  # 고른 열차가 없는 9/29 는 조회하지 않는다
    assert (g.day, g.after, g.until) == (date(2026, 9, 28), datetime(2026, 9, 28, 9, 30), datetime(2026, 9, 28, 13, 0))
