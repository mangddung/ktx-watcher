"""코레일 조회. DB 나 알림에 의존하지 않는다(테스트에서 가짜 scanner 로 바꿔 끼운다).

모든 코레일 요청은 Gate 하나를 거친다. 스케줄러와 웹 조회가 동시에 요청해도 순서대로, 간격을 두고 나간다.
"""

import logging
import random
import threading
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone

from pykorail import AdultPassenger, Korail, KorailError, NoResultsError
from pykorail.exceptions import NetFunnelError, TransportError

log = logging.getLogger(__name__)

SEAT_STATE = {"11": "avail", "12": "short", "13": "soldout"}  # 그 외 코드는 "none"(좌석 등급 없음)
# "사용자가 많아 접속이 원활하지 않습니다" 응답. 차단이 아니라 잠깐 혼잡이라 코레일 앱처럼 조금 기다렸다 다시 요청한다
BUSY = "S002"
BUSY_RETRIES = 3


@dataclass(frozen=True)
class TrainInfo:
    date: str  # 출발 날짜 YYYY-MM-DD
    no: str
    type: str
    dep_at: datetime
    arr_at: datetime
    general: str  # avail | short | soldout | none
    special: str

    def seats(self) -> set[str]:
        s = set()
        if self.general == "avail":
            s.add("일반실")
        if self.special == "avail":
            s.add("특실")
        return s

    def to_api(self) -> dict:
        d = asdict(self)
        d["dep"] = self.dep_at.strftime("%H:%M")
        d["arr"] = self.arr_at.strftime("%H:%M")
        d["arrDate"] = self.arr_at.strftime("%Y-%m-%d")
        d["minutes"] = int((self.arr_at - self.dep_at).total_seconds() // 60)
        del d["dep_at"], d["arr_at"]
        return d


def is_block_error(e: Exception) -> bool:
    """IP 차단이나 접속 대기 차단으로 보이는 오류."""
    text = str(e)
    return isinstance(e, (NetFunnelError, TransportError)) or "403" in text or "429" in text


class Gate:
    """코레일 요청을 한 줄로 세우고 요청 사이에 난수 간격을 둔다."""

    def __init__(self, page_gap: tuple[int, int]) -> None:
        self.page_gap = page_gap
        self.lock = threading.Lock()
        self.last = 0.0
        self.client: Korail | None = None

    def _wait(self) -> None:
        gap = random.uniform(*self.page_gap)
        wait = self.last + gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)

    def call(self, fn):
        with self.lock:
            for attempt in range(BUSY_RETRIES + 1):
                self._wait()
                try:
                    if self.client is None:
                        self.client = Korail()
                        self.client.trains.validate_stations = False
                    return fn(self.client)
                except NoResultsError:
                    raise
                except KorailError as e:
                    if e.code == BUSY and attempt < BUSY_RETRIES:
                        log.info("코레일 혼잡(%s), 잠깐 뒤 같은 요청을 다시 보냅니다 (%d/%d)", BUSY, attempt + 1, BUSY_RETRIES)
                        continue
                    self.client = None
                    raise
                except Exception:
                    self.client = None  # 세션이 꼬였을 수 있으니 다음 요청에서 새로 만든다
                    raise
                finally:
                    self.last = time.monotonic()

    def station_names(self) -> set[str]:
        return self.call(lambda k: k.stations.names())


def _to_info(t) -> TrainInfo:
    dep_at = datetime.strptime(t.dep_date + t.dep_time[:4], "%Y%m%d%H%M")
    arr_at = datetime.strptime((t.arr_date or t.dep_date) + t.arr_time[:4], "%Y%m%d%H%M")
    if arr_at < dep_at:
        arr_at += timedelta(days=1)
    return TrainInfo(
        # 코레일은 "무궁화호", "새마을호" 로 준다. 앱의 열차 종류(stations.TRAIN_GROUPS)는 "호" 없이 쓴다
        date=dep_at.strftime("%Y-%m-%d"), no=t.train_no, type=t.train_type_name.removesuffix("호"),
        dep_at=dep_at, arr_at=arr_at,
        general=SEAT_STATE.get(t.general_seat, "none"), special=SEAT_STATE.get(t.special_seat, "none"),
    )


KST = timezone(timedelta(hours=9))


def _now() -> datetime:
    return datetime.now(KST).replace(tzinfo=None, microsecond=0)


def scan_pages(gate: Gate, dep: str, arr: str, day: date, after: datetime, until: datetime, passengers: int):
    """day 에 출발하는 열차 중 after 이후 ~ until 까지 출발하는 열차를 코레일 한 페이지(10편)씩 낸다.
    웹 조회는 받는 대로 화면에 보내고, 도중에 연결이 끊기면 다음 페이지를 요청하지 않는다.

    코레일 앱처럼 정시(HH:00) 단위로 조회한다. 앞 페이지와 겹치는 열차는 거른다.
    한 시간에 10편 넘게 다녀 정시로는 다음 열차에 닿지 못하면 마지막 열차 다음 분부터 이어서 조회하고,
    오늘 이미 시작된 시간대는 지금 시각부터 조회한다(지난 시각은 조회할 수 없다).
    """
    cursor = max(after, datetime.combine(day, datetime.min.time()))
    end = min(until, datetime.combine(day, datetime.max.time()))
    last_ask = None
    while cursor <= end:
        ask = cursor.replace(minute=0, second=0, microsecond=0)
        if last_ask is not None and ask <= last_ask:
            ask = cursor
        ask = max(ask, _now())
        last_ask = ask
        try:
            trains = gate.call(lambda k: k.trains.search(
                dep, arr, depart_after=ask, passengers=[AdultPassenger(passengers)], include_no_seats=True))
        except NoResultsError:
            log.debug("[조회] %s→%s %s 이후: 결과 없음", dep, arr, f"{ask:%m/%d %H:%M}")
            return
        infos = [_to_info(t) for t in trains]
        log.debug("[조회] %s→%s %s 이후: %d편 (%s~%s 출발)", dep, arr, f"{ask:%m/%d %H:%M}", len(infos),
                  f"{infos[0].dep_at:%H%M}", f"{infos[-1].dep_at:%H%M}")
        yield [i for i in infos if i.dep_at.date() == day and cursor <= i.dep_at <= end]
        nxt = infos[-1].dep_at + timedelta(minutes=1)
        if nxt.date() != day or (ask >= cursor and nxt <= cursor):
            return  # 하루가 끝났거나, 더 나아가지 못한다
        cursor = max(cursor, nxt)


def scan_day(gate: Gate, dep: str, arr: str, day: date, after: datetime, until: datetime, passengers: int) -> list[TrainInfo]:
    """scan_pages 를 끝까지 모은 결과."""
    found = {i.no: i for page in scan_pages(gate, dep, arr, day, after, until, passengers) for i in page}
    return sorted(found.values(), key=lambda i: i.dep_at)
