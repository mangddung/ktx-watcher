"""감시 스케줄러. 겹치는 조회는 (출발, 도착, 날짜, 인원수) 묶음으로 한 번만 하고, 결과를 감시마다 나눠 쓴다."""

import asyncio
import logging
import random
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from sqlalchemy import select

from app import scanner, service
from app.db import SessionLocal, now_kst
from app.models import Watch, WatchSeen
from app.scanner import TrainInfo
from app.state import state

log = logging.getLogger(__name__)

# 도착 기준 감시에서 구간 시작보다 이만큼 앞서 출발하는 열차까지 본다(가장 긴 편도 소요시간보다 넉넉하게).
# ponytail: 고정값. 노선별 최장 소요시간을 조회 결과로 배워 쓰면 페이지 수를 줄일 수 있다
ARR_LOOKBACK = timedelta(hours=6)
# 고른 열차만 감시할 때 고른 출발 시각 앞뒤로 더 보는 폭
PICK_MARGIN = timedelta(minutes=30)


@dataclass
class Group:
    dep: str
    arr: str
    day: date
    passengers: int
    after: datetime
    until: datetime
    watch_ids: set[int] = field(default_factory=set)


def watch_days(w: Watch) -> list[date]:
    return [w.start_at.date() + timedelta(days=i) for i in range((w.end_at.date() - w.start_at.date()).days + 1)]


def has_future_targets(w: Watch, now: datetime, cutoff: timedelta) -> bool:
    """출발 cutoff 전이 아직 안 지난 대상 열차가 남아 있을 수 있는지(조회 없이 시각만으로 판단)."""
    if w.trains:
        return any(datetime.strptime(f"{t['date']} {t['dep']}", "%Y-%m-%d %H:%M") - cutoff > now for t in w.trains)
    return w.end_at - cutoff > now


def build_groups(watches: list[Watch], now: datetime, cutoff: timedelta) -> dict[tuple, Group]:
    groups: dict[tuple, Group] = {}
    earliest = now + cutoff
    for w in watches:
        for d in watch_days(w):
            day_start, day_end = datetime.combine(d, time.min), datetime.combine(d, time(23, 59))
            if w.trains:
                # 고른 열차만 보면 되니 그날 고른 열차의 출발 시각 범위만 조회한다. 시간표(지난 기록)로 고른 열차는
                # 실제 출발 시각이 조금 다를 수 있어 앞뒤로 PICK_MARGIN 을 둔다
                deps = [datetime.strptime(f"{t['date']} {t['dep']}", "%Y-%m-%d %H:%M")
                        for t in w.trains if t["date"] == d.isoformat()]
                if not deps:
                    continue
                after = max(min(deps) - PICK_MARGIN, day_start, earliest)
                until = min(max(deps) + PICK_MARGIN, day_end)
            else:
                start = w.start_at if w.time_basis == "dep" else w.start_at - ARR_LOOKBACK
                after = max(start, day_start, earliest)
                until = min(w.end_at, day_end)
            if after > until:
                continue
            key = (w.dep, w.arr, d, w.passengers)
            g = groups.get(key)
            if g is None:
                groups[key] = Group(w.dep, w.arr, d, w.passengers, after, until, {w.id})
            else:
                g.after, g.until = min(g.after, after), max(g.until, until)
                g.watch_ids.add(w.id)
    return groups


def targets(w: Watch, trains: list[TrainInfo], now: datetime, cutoff: timedelta) -> list[TrainInfo]:
    picked = {(t["date"], t["no"]) for t in w.trains} if w.trains else None
    out = []
    for t in trains:
        at = t.dep_at if w.time_basis == "dep" else t.arr_at
        if (t.type in w.train_types and w.start_at <= at <= w.end_at and t.dep_at - cutoff > now
                and (picked is None or (t.date, t.no) in picked)):
            out.append(t)
    return out


class Scheduler:
    def __init__(self, scan_day=scanner.scan_day, clock=now_kst) -> None:
        self.scan_day = scan_day
        self.clock = clock
        self.fails = 0  # 연속으로 실패한 사이클 수
        self.cooldown_until: datetime | None = None
        self.block_since: datetime | None = None
        self.history: deque[tuple[datetime, bool, bool]] = deque(maxlen=5000)  # (시각, 성공, 차단 의심)
        self.last_cycle_at: datetime | None = None
        self.cycle_seconds = 0
        self.next_cycle_at: datetime | None = None
        self.groups = 0
        self.level = "ok"
        self.task: asyncio.Task | None = None

    def record(self, ok: bool, block: bool = False) -> None:
        now = self.clock()
        self.history.append((now, ok, block))
        if block:
            self.block_since = self.block_since or now
        elif ok:
            self.block_since = None

    async def run(self) -> None:
        s = state.settings
        while True:
            try:
                await self.cycle()
            except Exception:
                log.exception("스케줄러 사이클 오류")
            await self.check_health()
            wait = random.uniform(*s.scan_interval)
            if self.cooldown_until:
                wait = max(wait, (self.cooldown_until - self.clock()).total_seconds())
            self.next_cycle_at = self.clock() + timedelta(seconds=wait)
            await asyncio.sleep(wait)

    async def check_health(self) -> None:
        from app.health import compute

        h = compute()
        if h["level"] != self.level:
            old, self.level = self.level, h["level"]
            await service.system(f"서비스 상태 {old.upper()} → {h['level'].upper()}: {h['title']}")

    async def cycle(self) -> None:
        s = state.settings
        cutoff = timedelta(minutes=s.cutoff_minutes)
        started = self.clock()
        if self.cooldown_until:
            if started < self.cooldown_until:
                return
            log.info("쿨다운 끝, 조회를 다시 시작합니다")
            self.cooldown_until = None

        with SessionLocal() as db:
            watches = list(db.scalars(select(Watch).where(Watch.status.in_(("active", "paused")))))
        for w in [w for w in watches if not has_future_targets(w, started, cutoff)]:
            await service.end_watch(w.id, "no_targets")
            watches.remove(w)
        active = [w for w in watches if w.status == "active"]
        groups = build_groups(active, started, cutoff)
        self.groups = len(groups)

        # 감시가 필요로 하는 조회 묶음. 같은 노선·인원수의 날짜들이 연달아 조회되도록 정렬해서,
        # 여러 날에 걸친 감시도 자기 날짜 조회가 끝나는 즉시 알림을 보낸다
        keys_of = {w.id: [k for k, g in groups.items() if w.id in g.watch_ids] for w in active}
        order = sorted(groups, key=lambda k: (k[0], k[1], k[3], k[2]))
        results: dict[tuple, list[TrainInfo]] = {}
        done: set[int] = set()
        error: str | None = None
        for key in order:
            g = groups[key]
            try:
                results[key] = await asyncio.to_thread(
                    self.scan_day, state.gate, g.dep, g.arr, g.day, g.after, g.until, g.passengers)
                self.record(True)
                # 필요한 조회가 모두 끝난 감시는 사이클 끝을 기다리지 않고 바로 비교하고 알린다
                for w in active:
                    if w.id in g.watch_ids and w.id not in done and all(k in results for k in keys_of[w.id]):
                        done.add(w.id)
                        await self.evaluate(w, [t for k in keys_of[w.id] for t in results[k]], self.clock(), cutoff)
            except Exception as e:
                self.record(False, scanner.is_block_error(e))
                error = f"{type(e).__name__}: {e}"[:300]
                log.error("%s→%s %s 조회 실패: %s (연속 %d/%d)", g.dep, g.arr, f"{g.day:%m/%d}", error,
                          self.fails + 1, s.max_fails)
                break  # 차단일 수 있으니 이번 사이클의 나머지 요청은 보내지 않는다

        if error:
            self.fails += 1
            if self.fails == 1:
                await service.system(f"조회 오류 (재시도 중): {error}")
            if self.fails >= s.max_fails:
                self.cooldown_until = self.clock() + timedelta(minutes=s.block_cooldown_minutes)
                log.error("연속 실패 %d회, %d분 동안 조회를 멈춥니다", self.fails, s.block_cooldown_minutes)
                await service.system(
                    f"조회 중단: 연속 실패 {self.fails}회. {s.block_cooldown_minutes}분 뒤 다시 시도합니다. 마지막 오류: {error}")
                self.fails = 0
        elif groups and self.fails:
            await service.system(f"조회 복구 ({self.fails}회 연속 실패 후)")
            log.info("조회 복구 (%d회 연속 실패 후)", self.fails)
            self.fails = 0

        now = self.clock()
        for w in active:
            if w.id in done:
                continue
            keys = keys_of[w.id]
            if any(k not in results for k in keys):
                if error:
                    with SessionLocal() as db:
                        row = db.get(Watch, w.id)
                        row.error_count += 1
                        row.last_error = error
                        db.commit()
                continue
            await self.evaluate(w, [t for k in keys for t in results[k]], now, cutoff)

        self.last_cycle_at = self.clock()
        self.cycle_seconds = int((self.last_cycle_at - started).total_seconds())
        if groups:
            log.info("사이클 완료 %d묶음 %d초, 오류 %s", len(groups), self.cycle_seconds, "있음" if error else "없음")

    async def evaluate(self, w: Watch, trains: list[TrainInfo], now: datetime, cutoff: timedelta) -> None:
        cands = targets(w, trains, now, cutoff)
        lines = []
        with SessionLocal() as db:
            row = db.get(Watch, w.id)
            if row.status != "active":
                return
            seen = {(r.dep_date, r.train_no): r for r in db.scalars(select(WatchSeen).where(WatchSeen.watch_id == w.id))}
            for t in cands:
                avail = t.seats() & set(w.seat_classes)
                key = (t.date.replace("-", ""), t.no)
                prev = seen.get(key)
                if avail - set(prev.seats if prev else []):
                    lines.append(f"{t.type} {t.no}  {service.md(t.dep_at)} {t.dep_at:%H:%M}→{t.arr_at:%H:%M}  "
                                 f"{', '.join(sorted(avail))}")
                if avail and prev:
                    prev.seats = sorted(avail)
                elif avail:
                    db.add(WatchSeen(watch_id=w.id, dep_date=key[0], train_no=t.no, seats=sorted(avail)))
                elif prev:
                    db.delete(prev)  # 다시 매진: 기록을 지워 다음에 자리가 나면 또 알린다
            row.scan_count += 1
            row.last_scan_at = now
            row.last_error = None  # 정상 조회로 돌아오면 지난 오류는 지운다
            row.remaining = len(cands)
            row.found_count += len(lines)
            db.commit()
        if lines:
            log.info("좌석 발견 #%d %d편", w.id, len(lines))
            await service.notify_watch(w.id, "취소표 발견", body="\n".join(lines))
        if not cands:
            await service.end_watch(w.id, "no_targets")
