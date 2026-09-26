from datetime import date, datetime, timedelta
from types import SimpleNamespace

from pykorail import NoResultsError

from app import scanner

DAY = date(2026, 9, 28)


class FakeGate:
    """하루 시간표에서 depart_after 이후 10편씩 주는 가짜 코레일. 요청한 시각을 모은다."""

    def __init__(self, times: list[str]):
        self.times = times
        self.asks: list[str] = []

    def call(self, fn):
        return fn(SimpleNamespace(trains=SimpleNamespace(search=self.search)))

    def search(self, dep, arr, depart_after, passengers, include_no_seats):
        self.asks.append(f"{depart_after:%H:%M}")
        hits = [t for t in self.times if t >= f"{depart_after:%H%M}"][:10]
        if not hits:
            raise NoResultsError()
        return [SimpleNamespace(dep_date=f"{DAY:%Y%m%d}", dep_time=t + "00", arr_date=None, arr_time="235900",
                                train_no=str(i), train_type_name="무궁화호", general_seat="11", special_seat="13")
                for i, t in enumerate(hits, start=int(hits[0]))]


def scan(gate, after, until, monkeypatch, now=datetime(2026, 9, 27, 12, 0)):
    monkeypatch.setattr(scanner, "_now", lambda: now)
    pages = list(scanner.scan_pages(gate, "서울", "부산", DAY, after, until, 1))
    return [f"{i.dep_at:%H%M}" for p in pages for i in p], pages


def test_asks_on_the_hour_and_drops_overlap(monkeypatch):
    times = [f"{h:02d}{m:02d}" for h in range(6, 23) for m in (5, 35)]  # 한 시간에 2편
    gate = FakeGate(times)
    got, _ = scan(gate, datetime(2026, 9, 28, 7, 20), datetime(2026, 9, 28, 23, 59), monkeypatch)
    assert got == [t for t in times if t >= "0720"]  # 겹침·누락 없이
    # 10편마다 다음 정시부터. 마지막은 결과 없음으로 끝을 확인한다
    assert gate.asks == ["07:00", "11:00", "15:00", "19:00", "22:00", "22:36"]
    assert all(t.type == "무궁화" for t in _[0])  # 코레일 "무궁화호" → 앱 이름


def test_more_than_ten_an_hour_continues_by_minute(monkeypatch):
    times = [f"08{m:02d}" for m in range(0, 60, 4)] + ["0930"]  # 8시대 15편
    gate = FakeGate(times)
    got, _ = scan(gate, datetime(2026, 9, 28, 8, 0), datetime(2026, 9, 28, 23, 59), monkeypatch)
    assert got == times
    # 8시대가 10편을 넘으면 08:37 부터 이어서, 그다음은 다시 정시
    assert gate.asks == ["08:00", "08:37", "09:00", "09:31"]


def test_current_hour_today_asks_from_now(monkeypatch):
    gate = FakeGate(["1210", "1250", "1310"])
    got, _ = scan(gate, datetime(2026, 9, 28, 12, 31), datetime(2026, 9, 28, 23, 59), monkeypatch,
                  now=datetime(2026, 9, 28, 12, 30))
    assert got == ["1250", "1310"] and gate.asks[0] == "12:30"
