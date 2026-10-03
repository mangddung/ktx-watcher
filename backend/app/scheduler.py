"""감시 스케줄러. 겹치는 조회는 (출발, 도착, 날짜, 인원수) 묶음으로 한 번만 하고, 결과를 감시마다 나눠 쓴다."""

import asyncio
import logging
import random
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from sqlalchemy import select

from app import scanner, service, timetable
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
# 취소표 알림을 못 보낸 채널로 다음 조회 때마다 다시 보내는 횟수. 그래도 못 보내면 포기하고 경고한다
RETRIES = 2
# 포기한 알림을 상태 경고와 감시의 마지막 오류로 보여주는 기간
UNDELIVERED_SHOWN = timedelta(hours=1)


@dataclass
class Unsent:
    """한 감시의 한 채널로 못 보낸 좌석. 다음 조회 때 아직 남아 있는 좌석만 다시 보낸다.
    ponytail: 메모리에만 둔다. 재시작하면 기다리던 재전송은 사라진다. 남기려면 채널별 seen 을 DB 에 둬야 한다"""

    seats: dict[tuple[str, str], set[str]] = field(default_factory=dict)  # (출발일 YYYYMMDD, 열차번호) → 좌석
    tries: int = 0  # 다시 보내다 실패한 횟수
    reason: str = ""


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


def found_line(t: TrainInfo, seats: set[str]) -> str:
    return f"{t.type} {t.no}  {service.md(t.dep_at)} {t.dep_at:%H:%M}→{t.arr_at:%H:%M}  {', '.join(sorted(seats))}"


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
        self.unsent: dict[tuple[int, int], Unsent] = {}  # (감시 id, 채널 id) → 못 보낸 좌석
        self.undelivered: deque[tuple[datetime, int, str]] = deque(maxlen=100)  # 재시도 끝에 포기한 알림 (시각, 감시 id, 사유)

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
        live = {w.id for w in active}
        self.unsent = {k: u for k, u in self.unsent.items() if k[0] in live}  # 멈추거나 끝난 감시의 재전송은 버린다
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
            except Exception as e:
                self.record(False, scanner.is_block_error(e))
                error = f"{type(e).__name__}: {e}"[:300]
                log.error("%s→%s %s 조회 실패: %s (연속 %d/%d)", g.dep, g.arr, f"{g.day:%m/%d}", error,
                          self.fails + 1, s.max_fails)
                break  # 차단일 수 있으니 이번 사이클의 나머지 요청은 보내지 않는다
            self.record(True)
            # 필요한 조회가 모두 끝난 감시는 사이클 끝을 기다리지 않고 바로 비교하고 알린다
            for w in active:
                if w.id in g.watch_ids and w.id not in done and all(k in results for k in keys_of[w.id]):
                    done.add(w.id)
                    await self.evaluate_safe(w, [t for k in keys_of[w.id] for t in results[k]], self.clock(), cutoff)

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
            await self.evaluate_safe(w, [t for k in keys for t in results[k]], now, cutoff)

        self.last_cycle_at = self.clock()
        self.cycle_seconds = int((self.last_cycle_at - started).total_seconds())
        if groups:
            log.info("사이클 완료 %d묶음 %d초, 오류 %s", len(groups), self.cycle_seconds, "있음" if error else "없음")

    async def evaluate_safe(self, w: Watch, trains: list[TrainInfo], now: datetime, cutoff: timedelta) -> None:
        """비교·알림 중 오류는 그 감시만 건너뛴다. 코레일 조회 실패로 세지 않는다."""
        try:
            await self.evaluate(w, trains, now, cutoff)
        except Exception as e:
            log.error("감시 #%d 비교·알림 오류: %s: %s", w.id, type(e).__name__, e)

    async def evaluate(self, w: Watch, trains: list[TrainInfo], now: datetime, cutoff: timedelta) -> None:
        cands = targets(w, trains, now, cutoff)
        # 코레일이 0편을 줬는데 시간표에는 남은 열차가 있으면 응답 이상일 수 있어 끝내지 않는다(시각이 지나면 끝난다)
        expected = 0 if trains else len(
            timetable.static_trains(w.dep, w.arr, w.start_at, w.end_at, w.time_basis, now + cutoff))
        lost = next((m for at, wid, m in reversed(self.undelivered) if wid == w.id and now - at < UNDELIVERED_SHOWN), None)
        lines = []
        fresh: dict[tuple[str, str], set[str]] = {}  # 이번에 새로 난 좌석
        now_avail: dict[tuple[str, str], tuple[TrainInfo, set[str]]] = {}  # 지금 남은 좌석
        with SessionLocal() as db:
            row = db.get(Watch, w.id)
            if row.status != "active":
                return
            seen = {(r.dep_date, r.train_no): r for r in db.scalars(select(WatchSeen).where(WatchSeen.watch_id == w.id))}
            for t in cands:
                avail = t.seats() & set(w.seat_classes)
                key = (t.date.replace("-", ""), t.no)
                now_avail[key] = (t, avail)
                prev = seen.get(key)
                if avail - set(prev.seats if prev else []):
                    lines.append(found_line(t, avail))
                    fresh[key] = avail
                if avail and prev:
                    prev.seats = sorted(avail)
                elif avail:
                    db.add(WatchSeen(watch_id=w.id, dep_date=key[0], train_no=t.no, seats=sorted(avail)))
                elif prev:
                    db.delete(prev)  # 다시 매진: 기록을 지워 다음에 자리가 나면 또 알린다
            row.scan_count += 1
            row.last_scan_at = now
            # 정상 조회로 돌아오면 지난 오류는 지운다. 재시도 끝에 못 보낸 알림은 UNDELIVERED_SHOWN 동안 남긴다
            row.last_error = (f"코레일 조회 결과 0편 (시간표에는 {expected}편)" if expected
                              else f"알림 전송 실패: {lost}" if lost else None)
            row.remaining = len(cands)
            row.found_count += len(lines)
            db.commit()
        failed = {}
        if lines:
            log.info("좌석 발견 #%d %d편", w.id, len(lines))
            failed = await service.notify_watch(w.id, "취소표 발견", body="\n".join(lines))
        await self.resend(w, now_avail, fresh, failed, now)
        if not cands and not expected:
            await service.end_watch(w.id, "no_targets")

    async def resend(self, w: Watch, now_avail: dict, fresh: dict, failed: dict[int, str], now: datetime) -> None:
        """지난 조회 때 못 보낸 채널로 아직 남은 좌석만 다시 보낸다. 이번에 못 보낸 채널은 다음 조회 때 보낸다.
        최초 전송 뒤 RETRIES 번 더 실패하면 포기하고 경고(상태, 감시의 마지막 오류)로 남긴다."""
        for (wid, ch_id), u in list(self.unsent.items()):
            if wid != w.id or ch_id in failed:
                continue
            del self.unsent[(wid, ch_id)]
            # 다시 매진된 좌석과, 이번에 모든 채널로 새로 보낸 열차는 뺀다
            seats = {k: s & now_avail[k][1] for k, s in u.seats.items()
                     if k in now_avail and k not in fresh and s & now_avail[k][1]}
            if not seats:
                continue
            body = "\n".join(found_line(now_avail[k][0], now_avail[k][1]) for k in seats)
            if err := (await service.notify_watch(w.id, "취소표 발견", tail=" · 재전송", body=body, only={ch_id})).get(ch_id):
                self.unsent[(wid, ch_id)] = Unsent(seats, u.tries + 1, err)
        for ch_id, err in failed.items():
            u = self.unsent.get((w.id, ch_id))
            if u:
                u.tries += 1  # 밀린 재전송도 이번에 같이 못 보낸 셈
            else:
                u = self.unsent[(w.id, ch_id)] = Unsent()
            u.seats.update(fresh)
            u.reason = err
        for key, u in list(self.unsent.items()):
            if key[0] == w.id and u.tries >= RETRIES:
                del self.unsent[key]
                self.undelivered.append((now, w.id, u.reason))
                log.error("감시 #%d 취소표 알림을 %d번 다시 보내도 못 보내 포기합니다: %s", w.id, RETRIES, u.reason)
                with SessionLocal() as db:
                    db.get(Watch, w.id).last_error = f"알림 전송 실패: {u.reason}"
                    db.commit()
