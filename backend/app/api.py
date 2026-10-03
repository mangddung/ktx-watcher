"""웹 UI 용 REST API. 모든 경로가 관리자 세션을 요구한다."""

import asyncio
import json
import logging
from datetime import date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app import scanner, service, timetable
from app.auth import require_admin
from app.db import SessionLocal, now_kst
from app.health import compute as compute_health
from app.logs import ring
from app.models import Channel, Watch, WatchSeen
from app.notify import WEBHOOK_PREFIXES, ChannelGone, SendFailed
from app.stations import SEAT_CLASSES, STATION_MAP, TRAIN_GROUPS
from app.state import state

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", dependencies=[Depends(require_admin)])

LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}


def ms(d: datetime | None) -> int | None:
    return int(d.timestamp() * 1000) if d else None


def fmt(d: datetime) -> str:
    return d.strftime("%Y-%m-%d %H:%M")


def mask(c: Channel) -> str:
    if c.kind == "discord_webhook":
        return "discord.com/api/webhooks/****"
    return "****" + c.target[-4:]


def channel_json(c: Channel) -> dict:
    return {"id": c.id, "kind": c.kind, "name": c.name, "target": mask(c), "managed": c.managed,
            "system": c.is_system, "broken": c.broken_at is not None}


def watch_json(w: Watch) -> dict:
    return {
        "id": w.id, "dep": w.dep, "arr": w.arr, "start": fmt(w.start_at), "end": fmt(w.end_at), "basis": w.time_basis,
        "trainTypes": w.train_types, "seats": w.seat_classes, "passengers": w.passengers, "trains": w.trains,
        "status": w.status, "pauseReason": w.pause_reason, "channelIds": [c.id for c in w.channels],
        "viewers": [str(v.discord_user_id) for v in w.viewers], "createdVia": w.created_via,
        "scans": w.scan_count, "found": w.found_count, "errors": w.error_count, "remaining": w.remaining,
        "lastScan": ms(w.last_scan_at), "lastError": w.last_error, "endReason": w.end_reason,
        "endedAt": ms(w.ended_at), "archivedAt": ms(w.archived_at), "createdAt": ms(w.created_at),
    }


class PickIn(BaseModel):
    mode: Literal["auto", "manual"] = "auto"
    viewers: list[str] = Field(default_factory=list, max_length=50)
    channelIds: list[int] = Field(default_factory=list, max_length=20)

    def to_pick(self) -> service.ChannelPick:
        if not all(v.isdigit() and len(v) <= 20 for v in self.viewers):
            raise service.Invalid("멤버 ID 가 올바르지 않습니다.")
        return service.ChannelPick(self.mode, [int(v) for v in self.viewers], self.channelIds)


class ViewersIn(BaseModel):
    viewers: list[str] = Field(default_factory=list, max_length=50)


class TrainPick(BaseModel):
    date: str = Field(max_length=10)
    no: str = Field(max_length=5)
    dep: str = Field(max_length=5)


class WatchIn(BaseModel):
    dep: str = Field(max_length=30)
    arr: str = Field(max_length=30)
    start: datetime
    end: datetime
    basis: str = "dep"
    trainTypes: list[str] = Field(max_length=20)
    seats: list[str] = Field(max_length=2)
    passengers: int = 1
    trains: list[TrainPick] | None = Field(default=None, max_length=200)
    channel: PickIn = Field(default_factory=PickIn)


class SearchIn(BaseModel):
    dep: str = Field(max_length=30)
    arr: str = Field(max_length=30)
    start: datetime
    end: datetime
    basis: str = "dep"
    passengers: int = 1


class WebhookIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    url: str = Field(max_length=300)


def _naive(d: datetime) -> datetime:
    # 브라우저는 KST 벽시계 시각을 보낸다. 시간대가 붙어 오면 KST 로 바꾼다
    from app.db import KST

    return d.astimezone(KST).replace(tzinfo=None) if d.tzinfo else d


def invalid_to_400(fn):
    import functools

    @functools.wraps(fn)
    async def wrap(*a, **kw):
        try:
            return await fn(*a, **kw)
        except service.Invalid as e:
            raise HTTPException(400, str(e)) from e

    return wrap


@router.get("/me")
async def me(request: Request):
    return {"id": request.session["uid"], "name": request.session.get("name"), "avatar": request.session.get("avatar")}


@router.get("/config")
async def config():
    s = state.settings
    return {
        "maxActive": s.max_active, "maxWatchDays": s.max_watch_days, "cutoffMinutes": s.cutoff_minutes,
        "stations": state.stations, "trainGroups": TRAIN_GROUPS, "seatClasses": SEAT_CLASSES, "routes": state.routes,
        "botReady": bool(state.bot and state.bot.ready), "telegram": state.telegram is not None,
    }


@router.get("/map")
async def station_map():
    """역 선택 지도 데이터. 고정 데이터라 웹은 한 번만 받아 둔다. rank 는 역 이름을 먼저 보여줄 순서."""
    return {**STATION_MAP, "rank": timetable.station_rank()}


@router.get("/map/route")
async def station_route(dep: str, arr: str):
    """지도에 그릴 dep→arr 직통 열차 경로와 편수(열차 시간표 기준)."""
    if dep not in state.stations or arr not in state.stations or dep == arr:
        raise HTTPException(400, "출발역과 도착역을 확인하세요.")
    return {"paths": timetable.route_paths(dep, arr)}


@router.get("/map/train")
async def train_route(
    date: date, dep: str, no: str = Query(pattern=r"^\d{1,5}$"), time: str = Query(pattern=r"^\d{2}:\d{2}$"),
):
    """date 에 dep 역을 time 에 떠나는 no 열차의 정차역·시각과 선로(열차 시간표 기준)."""
    run = timetable.train_run(f"{int(no):03d}", date, dep, time)
    if run is None:
        raise HTTPException(404, "시간표에 없는 열차입니다.")
    return run


@router.get("/health")
async def health():
    return compute_health()


@router.get("/overview")
async def overview():
    sch = state.scheduler
    return {"groups": sch.groups, "cycleSeconds": sch.cycle_seconds, "lastCycle": ms(sch.last_cycle_at),
            "nextCycle": ms(sch.next_cycle_at)}


@router.post("/trains/search")
@invalid_to_400
async def search(body: SearchIn):
    start, end = _naive(body.start), _naive(body.end)
    if body.dep not in state.stations or body.arr not in state.stations or body.dep == body.arr:
        raise service.Invalid("출발역과 도착역을 확인하세요.")
    if end <= start or (end.date() - start.date()).days > state.settings.max_watch_days - 1:
        raise service.Invalid(f"구간은 시작일부터 최대 {state.settings.max_watch_days}일, 끝이 시작보다 늦어야 합니다.")
    if not 1 <= body.passengers <= 9:
        raise service.Invalid("인원수는 1~9명입니다.")
    now = now_kst()
    lookback = timedelta(hours=6) if body.basis == "arr" else timedelta(0)
    at = (lambda t: t.dep_at) if body.basis == "dep" else (lambda t: t.arr_at)

    # 먼저 시간표(좌석 모름)를 보내고, 코레일 한 페이지(10편)를 받을 때마다 SSE 한 건으로 보낸다.
    # 브라우저가 끊으면 다음 페이지를 요청하지 않는다
    async def events():
        yield _sse({"timetable": timetable.static_trains(body.dep, body.arr, start, end, body.basis,
                                                         now + timedelta(minutes=1))})
        try:
            d = start.date()
            while d <= end.date():
                day_start = datetime.combine(d, datetime.min.time())
                after = max(start - lookback, day_start, now + timedelta(minutes=1))
                until = min(end, datetime.combine(d, datetime.max.time()))
                if after <= until:
                    pages = scanner.scan_pages(state.gate, body.dep, body.arr, d, after, until, body.passengers)
                    while (page := await asyncio.to_thread(next, pages, None)) is not None:
                        yield _sse({"trains": [t.to_api() for t in page if start <= at(t) <= end]})
                d += timedelta(days=1)
            yield _sse({"done": True})
        except Exception as e:
            log.error("열차 조회 실패 %s→%s: %s: %s", body.dep, body.arr, type(e).__name__, e)
            yield _sse({"error": "코레일 조회에 실패했습니다. 잠시 뒤 다시 시도하세요."})

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("/watches")
async def watches():
    with SessionLocal() as db:
        return [watch_json(w) for w in db.scalars(select(Watch).order_by(Watch.id.desc()))]


@router.post("/watches")
@invalid_to_400
async def create(body: WatchIn):
    inp = service.WatchInput(
        dep=body.dep, arr=body.arr, start_at=_naive(body.start).replace(second=0, microsecond=0),
        end_at=_naive(body.end).replace(second=0, microsecond=0), basis=body.basis, train_types=body.trainTypes,
        seats=body.seats, passengers=body.passengers,
        trains=[t.model_dump() for t in body.trains] if body.trains else None, channel=body.channel.to_pick())
    w = await service.create_watch(inp, "web")
    with SessionLocal() as db:
        return watch_json(db.get(Watch, w.id))


@router.get("/watches/{watch_id}")
async def watch_detail(watch_id: int):
    """목록 정보에 '지금 자리 있는 열차'(알렸고 아직 다시 매진되지 않은 좌석)를 더한다."""
    with SessionLocal() as db:
        w = db.get(Watch, watch_id)
        if not w:
            raise HTTPException(404, "감시가 없습니다.")
        seen = db.scalars(select(WatchSeen).where(WatchSeen.watch_id == watch_id)
                          .order_by(WatchSeen.dep_date, WatchSeen.train_no)).all()
        return {**watch_json(w), "available": [
            {"date": f"{r.dep_date[:4]}-{r.dep_date[4:6]}-{r.dep_date[6:]}", "no": r.train_no, "seats": r.seats} for r in seen]}


@router.put("/watches/{watch_id}/viewers")
@invalid_to_400
async def viewers(watch_id: int, body: ViewersIn):
    if not all(v.isdigit() and len(v) <= 20 for v in body.viewers):
        raise HTTPException(400, "멤버 ID 가 올바르지 않습니다.")
    await service.set_viewers(watch_id, [int(v) for v in body.viewers])
    return {"ok": True}


@router.post("/watches/{watch_id}/end")
async def end(watch_id: int):
    await service.end_watch(watch_id, "manual")
    return {"ok": True}


@router.post("/watches/{watch_id}/archive")
@invalid_to_400
async def archive(watch_id: int):
    await service.archive_watch(watch_id)
    return {"ok": True}


@router.post("/watches/{watch_id}/pause")
@invalid_to_400
async def pause(watch_id: int):
    await service.pause_watch(watch_id)
    return {"ok": True}


@router.post("/watches/{watch_id}/resume")
@invalid_to_400
async def resume(watch_id: int, body: PickIn | None = None):
    """본문이 없으면 지금 채널 그대로 재개한다(수동 일시정지). 채널이 끊겼으면 채널을 다시 지정해 보낸다."""
    await service.resume_watch(watch_id, body.to_pick() if body else None)
    return {"ok": True}


@router.get("/channels")
async def channels():
    with SessionLocal() as db:
        rows = db.scalars(select(Channel).order_by(Channel.kind, Channel.name)).all()
        ended = {c.id: w.id for w in db.scalars(select(Watch).where(Watch.status == "ended", Watch.archived_at.is_(None)))
                 for c in w.channels if c.managed}
    return [{**channel_json(c), "endedWatchId": ended.get(c.id)} for c in rows]


@router.post("/channels/webhook")
async def add_webhook(body: WebhookIn):
    if not body.url.startswith(WEBHOOK_PREFIXES):
        raise HTTPException(400, "Discord 웹훅 주소가 아닙니다.")
    with SessionLocal() as db:
        c = Channel(kind="discord_webhook", name=body.name.strip(), target=body.url.strip())
        db.add(c)
        db.commit()
        log.info("웹훅 등록 name=%s", c.name)
        return channel_json(c)


@router.delete("/channels/{channel_id}")
async def delete_channel(channel_id: int):
    with SessionLocal() as db:
        c = db.get(Channel, channel_id)
        if not c:
            raise HTTPException(404, "채널이 없습니다.")
        if not (c.kind == "discord_webhook" or c.broken_at):
            raise HTTPException(400, "웹훅이나 끊긴 채널만 목록에서 지울 수 있습니다.")
        db.delete(c)
        db.commit()
    return {"ok": True}


@router.post("/channels/{channel_id}/test")
async def test_channel(channel_id: int):
    with SessionLocal() as db:
        c = db.get(Channel, channel_id)
    if not c or c.broken_at:
        raise HTTPException(404, "채널이 없습니다.")
    try:
        await state.notifier.send(c, "취소표 감시 테스트 메시지입니다.")
    except ChannelGone as e:
        await service.channel_gone(c.id, str(e))
        raise HTTPException(400, "채널을 찾을 수 없습니다. 끊긴 채널로 표시했습니다.") from e
    except SendFailed as e:
        raise HTTPException(502, "전송에 실패했습니다. 로그를 확인하세요.") from e
    return {"ok": True}


@router.get("/members")
async def members():
    if not (state.bot and state.bot.ready):
        return []
    return state.bot.list_members()


@router.get("/logs")
async def logs(level: str = "INFO"):
    minimum = LEVELS.get(level.upper(), 20)
    return [line for line in ring.lines if LEVELS.get(line["level"], 50) >= minimum][-1000:]
