"""감시 생성·종료·보관·재개와 알림 전송. 웹 API, Discord 봇, 스케줄러가 같이 쓴다."""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select

from app.db import SessionLocal, now_kst
from app.models import Channel, SentMessage, Watch, WatchChannel, WatchViewer
from app.notify import ChannelGone, SendFailed, split, text_limit
from app.stations import SEAT_CLASSES, TRAIN_TYPES
from app.state import state

log = logging.getLogger(__name__)

WEEKDAY = "월화수목금토일"


class Invalid(ValueError):
    """사용자에게 그대로 보여줄 입력 오류."""


@dataclass
class ChannelPick:
    mode: str = "auto"  # auto | manual
    viewers: list[int] = field(default_factory=list)
    channel_ids: list[int] = field(default_factory=list)


@dataclass
class WatchInput:
    dep: str
    arr: str
    start_at: datetime
    end_at: datetime
    basis: str
    train_types: list[str]
    seats: list[str]
    passengers: int = 1
    trains: list[dict] | None = None
    channel: ChannelPick = field(default_factory=ChannelPick)


def md(d: datetime) -> str:
    return f"{d.month}/{d.day}({WEEKDAY[d.weekday()]})"


def short_type(t: str) -> str:
    """알림용 짧은 열차 종류: KTX-산천 → KTX, ITX-마음 → ITX, 무궁화 → 무궁화호."""
    return t.split("-")[0] if t.startswith(("KTX", "ITX")) else f"{t}호"


def describe(w: Watch) -> str:
    kind = "출발" if w.time_basis == "dep" else "도착"
    end = f"{w.end_at:%H:%M}" if w.start_at.date() == w.end_at.date() else f"{md(w.end_at)} {w.end_at:%H:%M}"
    return f"#{w.id} {w.dep}→{w.arr} {md(w.start_at)} {w.start_at:%H:%M} ~ {end} {kind}"


def validate(inp: WatchInput, now: datetime | None = None) -> None:
    s = state.settings
    now = now or now_kst()
    if inp.dep not in state.stations or inp.arr not in state.stations:
        raise Invalid("출발역과 도착역은 KTX 정차역 목록에서 고르세요.")
    if inp.dep == inp.arr:
        raise Invalid("출발역과 도착역이 같습니다.")
    if inp.end_at <= inp.start_at:
        raise Invalid("끝 일시가 시작 일시보다 늦어야 합니다.")
    if (inp.end_at.date() - inp.start_at.date()).days > s.max_watch_days - 1:
        raise Invalid(f"감시 구간은 시작일부터 최대 {s.max_watch_days}일까지입니다.")
    if inp.end_at <= now + timedelta(minutes=s.cutoff_minutes):
        raise Invalid(f"이미 지났거나 출발 {s.cutoff_minutes}분 전이 지난 구간입니다.")
    if inp.basis not in ("dep", "arr"):
        raise Invalid("기준은 출발 또는 도착입니다.")
    if not inp.train_types or not set(inp.train_types) <= set(TRAIN_TYPES):
        raise Invalid("열차 종류를 하나 이상 고르세요.")
    if not inp.seats or not set(inp.seats) <= set(SEAT_CLASSES):
        raise Invalid("좌석 등급을 하나 이상 고르세요.")
    if not 1 <= inp.passengers <= 9:
        raise Invalid("인원수는 1~9명입니다.")
    for t in inp.trains or []:
        if not (re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(t.get("date", ""))) and re.fullmatch(r"\d{1,5}", str(t.get("no", "")))
                and re.fullmatch(r"\d{2}:\d{2}", str(t.get("dep", "")))):
            raise Invalid("선택한 열차 정보가 올바르지 않습니다.")
    with SessionLocal() as db:
        live = db.scalar(select(func.count()).select_from(Watch).where(Watch.status != "ended"))
        if live >= s.max_active:
            raise Invalid(f"진행 중인 감시는 최대 {s.max_active}개까지입니다.")
        if inp.channel.mode == "manual":
            if not inp.channel.channel_ids:
                raise Invalid("알림 채널을 하나 이상 고르세요.")
            chs = db.scalars(select(Channel).where(Channel.id.in_(inp.channel.channel_ids))).all()
            if len(chs) != len(set(inp.channel.channel_ids)) or any(c.is_system or c.broken_at for c in chs):
                raise Invalid("쓸 수 없는 알림 채널이 있습니다.")
        elif inp.channel.mode == "auto":
            if not (state.bot and state.bot.ready):
                raise Invalid("Discord 봇이 연결돼 있지 않아 채널을 자동으로 만들 수 없습니다. 직접 선택을 쓰세요.")
        else:
            raise Invalid("채널 지정 방식이 올바르지 않습니다.")


async def _make_channel(w: Watch, viewers: list[int]) -> int:
    """Discord 에 감시 전용 채널을 만들고 DB 채널 id 를 돌려준다. 열린 DB 트랜잭션 밖에서 불러야 한다:
    SQLite 쓰기 잠금을 쥔 채 Discord 응답을 기다리면 그 사이 봇 이벤트·스케줄러의 쓰기가 'database is locked' 로 실패한다."""
    name = f"w{w.id}-{w.dep}-{w.arr}-{w.start_at:%m%d}"
    target = str(await state.bot.create_watch_channel(name, viewers))
    with SessionLocal() as db:
        # 채널 생성 이벤트(on_guild_channel_create → resync)가 먼저 일반 채널로 넣었을 수 있다
        ch = db.scalar(select(Channel).where(Channel.kind == "discord_bot", Channel.target == target))
        if ch is None:
            ch = Channel(kind="discord_bot", name=name, target=target)
            db.add(ch)
        ch.managed = True
        db.commit()
        return ch.id


def _set_viewers_row(db, w: Watch, viewers: list[int]) -> None:
    w.viewers = []
    db.flush()  # 같은 멤버를 다시 넣을 때 INSERT 가 DELETE 보다 먼저 나가 키가 겹치지 않게 비운 걸 먼저 반영한다
    w.viewers = [WatchViewer(discord_user_id=v) for v in dict.fromkeys(viewers)]


async def create_watch(inp: WatchInput, created_via: str = "web") -> Watch:
    validate(inp)
    auto = inp.channel.mode == "auto"
    with SessionLocal() as db:
        w = Watch(
            dep=inp.dep, arr=inp.arr, start_at=inp.start_at, end_at=inp.end_at, time_basis=inp.basis,
            train_types=inp.train_types, seat_classes=inp.seats, passengers=inp.passengers,
            trains=inp.trains or None, created_via=created_via,
            # 자동 채널은 먼저 id 를 받아야 이름을 지을 수 있다. 채널이 생길 때까지 스케줄러가 건너뛰는 상태로 둔다
            status="creating" if auto else "active",
        )
        w.viewers = [WatchViewer(discord_user_id=v) for v in dict.fromkeys(inp.channel.viewers)]
        if not auto:
            w.channels = list(db.scalars(select(Channel).where(Channel.id.in_(inp.channel.channel_ids))).all())
        db.add(w)
        db.commit()
    if auto:
        try:
            cid = await _make_channel(w, inp.channel.viewers)
        except BaseException:
            with SessionLocal() as db:
                db.delete(db.get(Watch, w.id))
                db.commit()
            raise
        with SessionLocal() as db:
            w = db.get(Watch, w.id)
            w.channels = [db.get(Channel, cid)]
            if w.status == "creating":  # 그 사이 종료됐으면 그대로 둔다
                w.status = "active"
            db.commit()
    log.info("감시 추가 %s (%s)", describe(w), created_via)
    types = ", ".join(dict.fromkeys(short_type(t) for t in inp.train_types))
    target = f"선택 {len(inp.trains)}편" if inp.trains else "조건 전체"
    by_date: dict[str, list[str]] = {}
    for t in inp.trains or []:
        by_date.setdefault(t["date"], []).append(f"{t['no']}({t['dep']})")
    picked = " / ".join(f"{md(datetime.strptime(d, '%Y-%m-%d'))} {', '.join(v)}" for d, v in by_date.items())
    # 시작 알림은 전용 채널에도 감시 정보를 다 보낸다(채널에 남는 첫 메시지가 감시 설명이 된다)
    await notify_watch(w.id, "감시 시작", detail=f"{types} · {', '.join(inp.seats)} · {inp.passengers}명 · {target}",
                       body=f"열차: {picked}" if picked else "", full=True)
    return w


def compose(title: str, label: str, tail: str, detail: str, body: str, dedicated: bool) -> str:
    """감시 전용 채널(자동 생성)은 채널이 곧 감시라서 감시 정보(label, detail)를 빼고 보낸다.
    여러 감시가 같이 쓰는 채널은 어느 감시인지 알 수 있게 붙인다."""
    lines = [f"{title}{tail}" if dedicated else f"{title} {label}{tail}"]
    if detail and not dedicated:
        lines.append(detail)
    if body:
        lines.append(body)
    return "\n".join(lines)


async def notify_watch(watch_id: int, title: str, tail: str = "", detail: str = "", body: str = "",
                       summary: bool = False, full: bool = False) -> int:
    """감시의 모든 채널로 보낸다. 보낸 채널 수를 돌려준다. full 이면 전용 채널에도 감시 정보를 붙인다."""
    with SessionLocal() as db:
        w = db.get(Watch, watch_id)
        channels = [c for c in w.channels if not c.broken_at]
        label = describe(w)
    sent = 0
    for ch in channels:
        try:
            mids = [await state.notifier.send(ch, part)
                    for part in split(compose(title, label, tail, detail, body, ch.managed and not full), text_limit(ch.kind))]
        except ChannelGone as e:
            await channel_gone(ch.id, str(e))
            continue
        except SendFailed as e:
            log.warning("알림 전송 실패 channel=%s %s", ch.name, e)
            continue
        sent += 1
        with SessionLocal() as db:
            db.add_all(SentMessage(watch_id=watch_id, channel_id=ch.id, message_id=m, is_summary=summary) for m in mids if m)
            db.commit()
    return sent


async def announce_active(title: str, tail: str) -> None:
    """진행 중인(active) 감시의 채널마다 알린다. 서비스 시작·종료 때 쓴다. 실패해도 다른 동작을 막지 않는다."""
    with SessionLocal() as db:
        ids = list(db.scalars(select(Watch.id).where(Watch.status == "active")))
    for wid in ids:
        try:
            await notify_watch(wid, title, tail=tail)
        except Exception as e:
            log.warning("감시 #%d 알림 실패 %s: %s", wid, type(e).__name__, e)


async def system(text: str) -> None:
    """시스템 채널(관리자만 보는 채널)로 보낸다. 봇이 없으면 로그로만 남는다."""
    log.info("[시스템] %s", text.replace("\n", " / "))
    if not (state.bot and state.bot.ready):
        return
    try:
        channel_id = await state.bot.ensure_system_channel()
        await state.notifier.send(Channel(kind="discord_bot", name="시스템", target=str(channel_id)), text)
    except Exception as e:  # 시스템 알림 실패가 다른 동작을 막으면 안 된다
        log.warning("시스템 채널 전송 실패 %s", e)


async def end_watch(watch_id: int, reason: str) -> None:
    """종료: 기존 메시지는 그대로 두고 종료 메시지만 보낸다. 자동 생성 채널은 남긴다."""
    with SessionLocal() as db:
        w = db.get(Watch, watch_id)
        if not w or w.status == "ended":
            return
        w.status, w.end_reason, w.ended_at = "ended", reason, now_kst()
        db.commit()
        found, scans = w.found_count, w.scan_count
        label = describe(w)
    why = "감시 대상 없음" if reason == "no_targets" else "수동 종료"
    log.info("감시 종료 %s (%s)", label, why)
    await notify_watch(watch_id, "감시 종료", tail=f" · {why} · 조회 {scans}회, 발견 {found}건", summary=True)


async def archive_watch(watch_id: int) -> None:
    """채널 정리: 자동 생성 채널을 지우고 기록 탭으로 옮긴다. 감시 기록은 지우지 않는다."""
    with SessionLocal() as db:
        w = db.get(Watch, watch_id)
        if not w or w.status != "ended":
            raise Invalid("종료된 감시만 정리할 수 있습니다.")
        if w.archived_at:
            return
        managed = [c for c in w.channels if c.managed and not c.is_system]
        if any(not c.broken_at for c in managed) and not (state.bot and state.bot.ready):
            raise Invalid("Discord 봇이 연결돼 있지 않아 채널을 지울 수 없습니다. 봇이 연결된 뒤 다시 시도하세요.")
        for c in managed:
            if not c.broken_at:
                await state.bot.delete_channel(int(c.target))
            db.delete(c)
        w.archived_at = now_kst()
        db.commit()
    log.info("감시 #%d 채널 정리, 기록으로 이동", watch_id)


async def set_viewers(watch_id: int, viewers: list[int]) -> None:
    """자동 생성 채널의 열람자를 바꾼다. Discord 채널 권한을 먼저 바꾸고, 성공하면 기록한다."""
    with SessionLocal() as db:
        w = db.get(Watch, watch_id)
        if not w:
            raise Invalid("감시가 없습니다.")
        managed = [c for c in w.channels if c.managed and not c.is_system and not c.broken_at]
        if w.archived_at or not managed:
            raise Invalid("열람자는 살아 있는 자동 생성 채널에만 지정할 수 있습니다.")
        if not (state.bot and state.bot.ready):
            raise Invalid("Discord 봇이 연결돼 있지 않아 채널 권한을 바꿀 수 없습니다. 봇이 연결된 뒤 다시 시도하세요.")
        for c in managed:
            await state.bot.set_viewers(int(c.target), viewers)
        _set_viewers_row(db, w, viewers)
        db.commit()
    log.info("감시 #%d 열람자 %d명으로 변경", watch_id, len(set(viewers)))


async def resume_watch(watch_id: int, pick: ChannelPick) -> None:
    with SessionLocal() as db:
        w = db.get(Watch, watch_id)
        if not w or w.status != "paused":
            raise Invalid("일시정지된 감시만 재개할 수 있습니다.")
    if pick.mode == "auto" and not (state.bot and state.bot.ready):
        raise Invalid("Discord 봇이 연결돼 있지 않아 채널을 자동으로 만들 수 없습니다.")
    if pick.mode == "manual":
        with SessionLocal() as db:
            chs = db.scalars(select(Channel).where(Channel.id.in_(pick.channel_ids))).all()
            if not chs or len(chs) != len(set(pick.channel_ids)) or any(c.is_system or c.broken_at for c in chs):
                raise Invalid("쓸 수 없는 알림 채널이 있습니다.")
    cid = await _make_channel(w, pick.viewers) if pick.mode == "auto" else None
    with SessionLocal() as db:
        w = db.get(Watch, watch_id)
        _set_viewers_row(db, w, pick.viewers)
        w.channels = [db.get(Channel, cid)] if cid else list(db.scalars(select(Channel).where(Channel.id.in_(pick.channel_ids))))
        w.status, w.pause_reason = "active", None
        db.commit()
        label = describe(w)
    log.info("감시 재개 %s", label)
    await notify_watch(watch_id, "감시 재개")


async def channel_gone(channel_id: int, reason: str) -> None:
    """채널이 사라지면 끊김으로 표시하고, 그 채널을 쓰는 진행 중 감시를 일시정지한다."""
    with SessionLocal() as db:
        ch = db.get(Channel, channel_id)
        if not ch or ch.broken_at:
            return
        ch.broken_at = now_kst()
        paused = []
        for w in db.scalars(select(Watch).join(WatchChannel).where(WatchChannel.channel_id == channel_id, Watch.status == "active")):
            w.status = "paused"
            w.pause_reason = f"알림 채널 {ch.name} 이(가) 사라졌습니다 ({reason})"
            paused.append(w.id)
        db.commit()
        name = ch.name
    log.warning("채널 끊김 channel=%s (%s), 일시정지 감시 %s", name, reason, paused or "없음")
    await system(f"채널 끊김: {name} ({reason})" + (f"\n일시정지된 감시: {', '.join(f'#{i}' for i in paused)}" if paused else ""))


def find_channel(kind: str, target: str) -> Channel | None:
    with SessionLocal() as db:
        return db.scalar(select(Channel).where(Channel.kind == kind, Channel.target == str(target)))


def sync_discord_channels(channels: list[tuple[int, str]], system_id: int | None) -> list[int]:
    """봇이 볼 수 있는 텍스트 채널을 DB 에 맞춘다. 사라진 채널의 DB id 목록을 돌려준다."""
    seen = {str(i): n for i, n in channels}
    gone = []
    with SessionLocal() as db:
        rows = {c.target: c for c in db.scalars(select(Channel).where(Channel.kind == "discord_bot"))}
        for target, name in seen.items():
            c = rows.get(target)
            if c is None:
                db.add(Channel(kind="discord_bot", name=name, target=target, is_system=target == str(system_id)))
            else:
                c.name = name
                c.is_system = target == str(system_id)
        for target, c in rows.items():
            if target not in seen and not c.broken_at:
                gone.append(c.id)
        db.commit()
    return gone


def upsert_telegram(chat_id: int, title: str) -> None:
    with SessionLocal() as db:
        c = db.scalar(select(Channel).where(Channel.kind == "telegram", Channel.target == str(chat_id)))
        if c is None:
            db.add(Channel(kind="telegram", name=title[:100], target=str(chat_id)))
            log.info("Telegram 채팅 등록 chat_id=%s title=%s", chat_id, title)
        else:
            c.name, c.broken_at = title[:100], None
        db.commit()
