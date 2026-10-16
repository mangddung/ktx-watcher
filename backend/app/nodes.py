"""조회 워커. 중앙(로컬 워커)과 원격 노드가 코레일 조회를 나눠 맡는다(NODE_PLAN.md).

워커마다 연속 실패·쿨다운·차단 의심을 따로 센다. 노선(출발역, 도착역)은 assign 으로 워커에 배정한다.
원격 노드는 /api/node/poll 로 잡을 기다렸다가 /api/node/result 로 결과를 보낸다(노드가 가져가는 방식이라 노드에 포트가 필요 없다).
노드 토큰은 관리자가 웹에서 발급한다. DB 에는 sha256 만 둔다(256비트 난수라 느린 해시가 필요 없다).
"""

import asyncio
import hashlib
import logging
import secrets
from collections import deque
from datetime import date, datetime, timedelta
from importlib.metadata import version
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db import SessionLocal, now_kst
from app.models import Node
from app.scanner import TrainInfo
from app.state import state

log = logging.getLogger(__name__)

VERSION = version("pykorail")  # 노드와 중앙이 같아야 한다. 코레일이 옛 클라이언트를 막은 적이 있다(pykorail 0.1.1)
PREFIX = "ktxn_"
POLL_WAIT = 25  # 잡이 없을 때 poll 응답을 붙잡아 두는 시간(초)
OFFLINE_AFTER = timedelta(seconds=90)  # 이만큼 poll 이 없으면 오프라인
JOB_TIMEOUT = 120  # 잡을 맡기고 이 시간(초) 안에 결과가 없으면 실패로 보고 오프라인 처리
SAVE_EVERY = timedelta(minutes=1)  # last_seen_at 을 DB 에 쓰는 간격(poll 마다 쓰지 않는다)
MAX_TRAINS = 300  # 노드 결과 하나의 열차 수 상한(하루 한 노선에 이보다 많은 열차는 없다)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class NodeError(Exception):
    """노드가 보고한 조회 실패, 응답 시간 초과, 폐기."""

    def __init__(self, message: str, block: bool = False) -> None:
        super().__init__(message)
        self.block = block


class Worker:
    """조회를 맡는 IP 하나. 기본은 중앙(로컬 워커)."""

    key = "local"
    name = "중앙"
    remote = False
    ip: str | None = None

    def __init__(self, scan_day=None) -> None:
        self.scan_day = scan_day
        self.fails = 0  # 연속으로 실패한 사이클 수
        self.cooldown_until: datetime | None = None
        self.block_since: datetime | None = None
        self.history: deque[tuple[datetime, bool, bool]] = deque(maxlen=5000)  # (시각, 성공, 차단 의심)

    def record(self, now: datetime, ok: bool, block: bool) -> None:
        self.history.append((now, ok, block))
        if block:
            self.block_since = self.block_since or now
        elif ok:
            self.block_since = None

    def online(self, now: datetime) -> bool:
        return True

    def cooling(self, now: datetime) -> bool:
        return self.cooldown_until is not None and now < self.cooldown_until

    def status(self, now: datetime) -> str:
        """online | offline | version(버전 불일치) | cooldown | blocked"""
        if not self.online(now):
            return "offline"
        if self.cooling(now):
            return "cooldown"
        return "blocked" if self.block_since else "online"

    async def scan(self, g) -> list[TrainInfo]:
        # 중앙 IP 요청은 웹 수동 조회와 같은 Gate 로 한 줄로 선다
        return await asyncio.to_thread(self.scan_day, state.gate, g.dep, g.arr, g.day, g.after, g.until, g.passengers)


class RemoteWorker(Worker):
    remote = True

    def __init__(self, node_id: int, name: str) -> None:
        super().__init__()
        self.node_id = node_id
        self.key = f"node:{node_id}"
        self.name = name
        self.version: str | None = None
        self.last_poll: datetime | None = None
        self.saved_at: datetime | None = None
        self.job: dict | None = None
        self.result: asyncio.Future | None = None
        self.wake = asyncio.Event()  # 잡이 있는 동안 켜져 있다. poll 이 이걸 기다린다

    def online(self, now: datetime) -> bool:
        return self.version == VERSION and self.last_poll is not None and now - self.last_poll < OFFLINE_AFTER

    def status(self, now: datetime) -> str:
        if self.version and self.version != VERSION:
            return "version"
        return super().status(now)

    async def scan(self, g) -> list[TrainInfo]:
        """잡을 걸어 두고 노드가 결과를 보낼 때까지 기다린다. 노드는 결과를 보내기 전까지 같은 잡을 다시 받는다
        (poll 응답이 중간에 끊겨도 잡이 사라지지 않는다)."""
        self.result = asyncio.get_running_loop().create_future()
        self.job = {"id": secrets.token_hex(8), "dep": g.dep, "arr": g.arr, "day": g.day.isoformat(),
                    "after": g.after.isoformat(), "until": g.until.isoformat(), "passengers": g.passengers,
                    "page_gap": list(state.settings.page_gap)}
        self.wake.set()
        try:
            return await asyncio.wait_for(self.result, JOB_TIMEOUT)
        except TimeoutError:
            self.last_poll = None  # 죽은 노드로 본다. 다시 poll 하면 돌아온다
            raise NodeError(f"노드가 {JOB_TIMEOUT}초 안에 결과를 보내지 않았습니다") from None
        finally:
            self.job = self.result = None
            self.wake.clear()


def assign(load: dict[tuple[str, str], int], workers: list[str], sticky: dict, turn: int) -> dict[tuple[str, str], str]:
    """노선 → 워커 key. load 는 노선별 조회 묶음 수, workers 는 지금 조회할 수 있는 워커(정렬), turn 은 사이클 번호.

    노선 수 ≥ 워커 수: 노선마다 워커 하나(같은 노선을 여러 IP 가 조회하면 분산 매크로로 보일 수 있다).
    한번 맡은 노선은 그 워커가 계속 맡고(sticky 를 고쳐 쓴다), 새 노선과 빠진 워커의 노선만 가장 한가한 워커에 준다.
    부하 편차가 2배를 넘을 때만(새 워커가 들어온 때 등) 노선을 옮긴다.
    노선 수 < 워커 수: 간격이 우선이라 노선을 공유한다. 사이클마다 워커를 순번으로 R 개씩 써서
    워커마다 T 동안 R 번 조회한다(IP 하나당 속도는 단일 서버와 같다). R 이 N 의 약수면 노선마다 N/R 개 IP 만 조회한다.
    """
    if not workers or not load:
        return {}
    if len(load) < len(workers):
        routes = sorted(load)
        return {r: workers[(turn * len(routes) + i) % len(workers)] for i, r in enumerate(routes)}
    for r in [r for r, w in sticky.items() if r not in load or w not in workers]:
        del sticky[r]
    busy = dict.fromkeys(workers, 0)
    for r, w in sticky.items():
        busy[w] += load[r]
    for r in sorted((r for r in load if r not in sticky), key=lambda r: (-load[r], r)):
        w = min(workers, key=busy.get)
        sticky[r] = w
        busy[w] += load[r]
    while True:
        hi, lo = max(workers, key=busy.get), min(workers, key=busy.get)
        movable = [r for r, w in sticky.items() if w == hi and load[r] < busy[hi] - busy[lo]]
        if busy[hi] <= 2 * busy[lo] or not movable:
            return dict(sticky)
        r = max(movable, key=load.get)  # 차이를 줄이는 가장 큰 노선 하나만 옮긴다(옮길 때마다 편차가 줄어 끝난다)
        sticky[r] = lo
        busy[hi] -= load[r]
        busy[lo] += load[r]


# ---- 노드 관리(관리자 API 가 부른다) ----

def issue(name: str) -> dict:
    """원문 토큰은 이 응답에만 있다."""
    token = PREFIX + secrets.token_urlsafe(32)
    with SessionLocal() as db:
        n = Node(name=name, token_hash=hash_token(token), token_prefix=token[:9])
        db.add(n)
        db.commit()
    log.info("노드 #%d %s 토큰 발급", n.id, name)
    return {"id": n.id, "token": token}


def revoke(node_id: int) -> bool:
    """폐기한 노드는 다음 요청부터 401. 맡고 있던 잡은 실패로 끝나고 다음 사이클에 다른 워커가 맡는다."""
    with SessionLocal() as db:
        n = db.get(Node, node_id)
        if not n or n.revoked_at:
            return False
        n.revoked_at = now_kst()
        db.commit()
    wk = state.scheduler.workers.pop(f"node:{node_id}", None)
    if wk and wk.result and not wk.result.done():
        wk.result.set_exception(NodeError("노드를 폐기했습니다"))
    log.info("노드 #%d %s 폐기", node_id, n.name)
    return True


# ---- 노드가 부르는 API. 관리자 세션이 아니라 노드 토큰만 받는다 ----

router = APIRouter(prefix="/api/node")


def node_auth(request: Request) -> Node:
    token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not token.startswith(PREFIX):
        raise HTTPException(401, "노드 토큰이 필요합니다")
    with SessionLocal() as db:
        node = db.scalar(select(Node).where(Node.token_hash == hash_token(token), Node.revoked_at.is_(None)))
    if not node:
        raise HTTPException(401, "노드 토큰이 없거나 폐기되었습니다")
    return node


def seen(node: Node, request: Request) -> RemoteWorker:
    """요청한 노드의 워커(없으면 만든다). 마지막 접속 시각과 IP 를 남긴다."""
    sch = state.scheduler
    wk = sch.workers.get(f"node:{node.id}")
    if wk is None:
        wk = sch.workers[f"node:{node.id}"] = RemoteWorker(node.id, node.name)
    now = sch.clock()
    ip = request.client.host if request.client else None  # nginx 가 CF-Connecting-IP 나 접속 주소로 채운다
    wk.last_poll = now
    if ip != wk.ip or not wk.saved_at or now - wk.saved_at >= SAVE_EVERY:
        if ip != wk.ip:
            log.info("노드 %s 접속 IP %s", wk.name, ip)
        wk.ip, wk.saved_at = ip, now
        with SessionLocal() as db:
            row = db.get(Node, node.id)
            row.last_seen_at, row.last_ip = now, ip
            db.commit()
    return wk


class PollIn(BaseModel):
    version: str = Field(max_length=40)


@router.post("/poll")
async def poll(body: PollIn, request: Request, node: Node = Depends(node_auth)):
    """잡이 생길 때까지 POLL_WAIT 초 기다린다. 없으면 204. heartbeat 를 겸한다."""
    wk = seen(node, request)
    if body.version != VERSION:
        if wk.version != body.version:
            log.warning("노드 %s pykorail %s, 중앙 %s: 잡을 주지 않습니다", wk.name, body.version, VERSION)
        wk.version = body.version
        raise HTTPException(409, f"pykorail 버전이 중앙({VERSION})과 다릅니다({body.version}). 노드를 다시 빌드하세요")
    wk.version = body.version
    try:
        await asyncio.wait_for(wk.wake.wait(), POLL_WAIT)
    except TimeoutError:
        return Response(status_code=204)
    return wk.job or Response(status_code=204)


class TrainIn(BaseModel):
    date: str = Field(max_length=10)
    no: str = Field(pattern=r"^\d{1,6}$")
    type: str = Field(max_length=20, pattern=r"^[\w-]+$")  # KTX-산천, ITX-새마을 …
    dep_at: datetime
    arr_at: datetime
    general: Literal["avail", "short", "soldout", "none"]
    special: Literal["avail", "short", "soldout", "none"]


class ResultIn(BaseModel):
    id: str = Field(max_length=32)
    trains: list[TrainIn] = Field(default_factory=list, max_length=MAX_TRAINS)
    error: str | None = Field(default=None, max_length=300)
    block: bool = False


@router.post("/result")
async def result(body: ResultIn, request: Request, node: Node = Depends(node_auth)):
    wk = seen(node, request)
    job, fut = wk.job, wk.result
    if not job or job["id"] != body.id or fut.done():
        return {"ok": False}  # 시간이 지나 끝난 잡
    if body.error:
        fut.set_exception(NodeError(body.error, body.block))
        return {"ok": True}
    # 노드 결과는 신뢰 경계 밖이다. 맡긴 날짜·시각 범위를 벗어난 열차가 있으면 결과를 통째로 버린다
    day, after, until = date.fromisoformat(job["day"]), datetime.fromisoformat(job["after"]), datetime.fromisoformat(job["until"])
    if any(t.dep_at.tzinfo or t.arr_at.tzinfo or t.date != job["day"] or t.dep_at.date() != day
           or not after <= t.dep_at <= until or t.arr_at < t.dep_at for t in body.trains):
        fut.set_exception(NodeError("노드 결과가 맡긴 조회 범위를 벗어났습니다"))
        log.warning("노드 %s 결과가 조회 범위를 벗어나 버립니다", wk.name)
        return {"ok": False}
    fut.set_result([TrainInfo(**t.model_dump()) for t in body.trains])
    return {"ok": True}
