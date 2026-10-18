import asyncio
import dataclasses
import hashlib
from collections import Counter

import httpx
import pytest
from fastapi.testclient import TestClient
from pykorail.exceptions import TransportError
from sqlalchemy import select

from app import db, nodes
from app.health import compute
from app.main import app
from app.models import Node
from app.nodes import assign
from app.scheduler import Scheduler
from app.state import state
from test_api import ADMIN, admin_cookie
from test_scheduler import NOW, FakeScan, add_watch, env, get, make_scheduler, run, train  # noqa: F401

A, B, C, D = ("서울", "부산"), ("동대구", "수원"), ("용산", "광주송정"), ("청량리", "강릉")


def test_assign_gives_each_route_one_worker_and_keeps_it():
    sticky = {}
    assert assign({A: 3, B: 1, C: 1}, ["local", "node:1"], sticky, 0) == {A: "local", B: "node:1", C: "node:1"}
    # 노선이 늘어도 맡던 노선은 그대로 두고 새 노선만 한가한 워커에 준다
    got = assign({A: 3, B: 1, C: 1, D: 1}, ["local", "node:1"], sticky, 1)
    assert got == {A: "local", B: "node:1", C: "node:1", D: "node:1"}
    # 워커가 빠지면 그 워커의 노선만 남은 워커로
    assert set(assign({A: 3, B: 1, C: 1, D: 1}, ["local"], sticky, 2).values()) == {"local"}


def test_assign_moves_only_what_is_needed_when_worker_joins():
    sticky = {}
    assign({A: 2, B: 1, C: 1}, ["local"], sticky, 0)
    got = assign({A: 2, B: 1, C: 1}, ["local", "node:1"], sticky, 1)
    assert got == {A: "node:1", B: "local", C: "local"}


def test_fewer_routes_than_workers_rotate_so_each_ip_keeps_its_rate():
    ws = ["local", "node:1", "node:2"]
    assert [assign({A: 1}, ws, {}, t)[A] for t in range(6)] == ws * 2
    ws = ws + ["node:3"]
    turns = [assign({A: 1, B: 1}, ws, {}, t) for t in range(4)]
    assert all(len(set(t.values())) == 2 for t in turns)  # 사이클마다 노선마다 다른 IP
    assert len({t[A] for t in turns}) == 2 and len({t[B] for t in turns}) == 2  # 노선마다 IP 2개만
    assert set(Counter(w for t in turns for w in t.values()).values()) == {2}  # 워커마다 N 사이클(=T)에 R 번


class FakeNode(nodes.Worker):
    """항상 온라인인 원격 워커. 조회는 가짜 scanner 로 한다."""

    remote = True

    def __init__(self, scan, key="node:9", name="B", ip="203.0.113.9"):
        super().__init__(scan)
        self.key, self.name, self.ip = key, name, ip


def test_two_workers_halve_the_interval_with_3s_floor(env, monkeypatch):
    sch = make_scheduler(FakeScan([]))
    monkeypatch.setattr("random.uniform", lambda a, b: 40)
    assert sch.wait_seconds() == 40
    sch.workers["node:9"] = FakeNode(FakeScan([]))
    assert sch.wait_seconds() == 20
    monkeypatch.setattr("random.uniform", lambda a, b: 4)
    assert sch.wait_seconds() == 3


def test_two_workers_split_routes_and_fail_separately(env):
    trains = [train("101", "2026-09-28 09:00")]
    local, remote = FakeScan(trains), FakeScan(trains)
    sch = make_scheduler(local)
    sch.workers["node:9"] = FakeNode(remote)
    w1 = add_watch("2026-09-28 07:00", "2026-09-28 12:00", dep="동대구")
    w2 = add_watch("2026-09-28 07:00", "2026-09-28 12:00", dep="서울")
    run(sch)
    assert len(local.calls) == 1 and len(remote.calls) == 1
    assert {local.calls[0][0], remote.calls[0][0]} == {"동대구", "서울"} and len(env.found()) == 2

    # 노드만 막히면 그 노드가 맡은 감시만 오류로 세고 노드만 쿨다운에 들어간다. 중앙이 살아 있으니 warning
    remote.error = TransportError("403 Forbidden")
    run(sch, 3)
    node = sch.workers["node:9"]
    assert node.cooldown_until and not sch.local.cooldown_until
    bad, good = (w2, w1) if remote.calls[0][0] == "서울" else (w1, w2)
    assert get(bad).error_count == 3 and get(good).error_count == 0
    h = compute()
    assert h["level"] == "warning" and {"label": "조회 워커", "value": "B 쿨다운"} in h["reasons"]

    # 쿨다운 동안은 중앙이 두 노선을 다 맡는다
    calls = len(remote.calls)
    run(sch)
    assert len(remote.calls) == calls and {c[0] for c in local.calls[-2:]} == {"동대구", "서울"}


def test_one_route_two_workers_is_shared_and_shown_only_as_note(env):
    sch = make_scheduler(FakeScan([train("101", "2026-09-28 09:00")]))
    sch.workers["node:9"] = FakeNode(FakeScan([train("101", "2026-09-28 09:00")]))
    add_watch("2026-09-28 07:00", "2026-09-28 12:00")
    run(sch)
    h = compute()
    assert sch.sharing and h["level"] == "ok" and h["notes"][0]["label"] == "노선 공유"


def test_no_worker_is_critical(env):
    state.scheduler = Scheduler(clock=lambda: NOW, local=False)
    h = compute()
    assert h["level"] == "critical" and h["title"] == "조회할 곳 없음"


@pytest.fixture
def admin(env):
    state.settings = dataclasses.replace(state.settings, admin_ids=(int(ADMIN),))
    make_scheduler(FakeScan([]))
    c = TestClient(app)
    c.cookies.set("ktx_session", admin_cookie())
    c.headers["X-Requested-With"] = "ktx"
    return c


def test_token_is_shown_once_and_revoked_token_is_401(admin):
    r = admin.post("/api/nodes", json={"name": "집"})
    node_id, token = r.json()["id"], r.json()["token"]
    assert token.startswith("ktxn_")
    r = admin.get("/api/nodes")
    assert r.json()[0]["local"] and r.json()[1]["prefix"] == token[:9] and token not in r.text  # 목록에는 앞부분만
    with db.SessionLocal() as s:
        assert s.get(Node, node_id).token_hash == hashlib.sha256(token.encode()).hexdigest()
    h = compute()
    assert h["level"] == "warning" and {"label": "노드", "value": "집 오프라인"} in h["reasons"]  # 발급만 하고 붙이지 않은 노드

    node = {"Authorization": f"Bearer {token}"}
    assert admin.post("/api/node/poll", json={"version": "0.0.1"}, headers=node).status_code == 409  # 버전이 다르면 잡을 안 준다
    assert admin.post("/api/node/poll", json={"version": nodes.VERSION}).status_code == 401  # 관리자 세션으로는 못 쓴다
    assert admin.delete(f"/api/nodes/{node_id}").status_code == 200
    assert admin.post("/api/node/poll", json={"version": nodes.VERSION}, headers=node).status_code == 401
    assert [n["local"] for n in admin.get("/api/nodes").json()] == [True]


def serve(trains) -> tuple[dict, dict]:
    """중앙은 노드만 쓰고, 노드 하나가 poll 로 잡을 받아 trains 를 결과로 보낸다. (결과 응답, 잡)."""
    sch = state.scheduler = Scheduler(clock=lambda: NOW, local=False)
    token = nodes.issue("집")["token"]
    headers = {"Authorization": f"Bearer {token}"}

    async def main():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as http:
            async def node():
                job = (await http.post("/api/node/poll", json={"version": nodes.VERSION}, headers=headers)).json()
                body = {"id": job["id"], "trains": [{**dataclasses.asdict(t), "dep_at": t.dep_at.isoformat(),
                                                     "arr_at": t.arr_at.isoformat()} for t in trains]}
                return (await http.post("/api/node/result", json=body, headers=headers)).json(), job

            task = asyncio.create_task(node())
            while not sch.workers:  # 첫 poll 이 워커를 등록할 때까지
                await asyncio.sleep(0.01)
            await sch.cycle()
            return await task

    return asyncio.run(main())


def test_node_takes_job_and_its_result_feeds_the_watch(env):
    wid = add_watch("2026-09-28 07:00", "2026-09-28 12:00")
    res, job = serve([train("101", "2026-09-28 09:00")])
    assert res == {"ok": True} and job["dep"] == "동대구" and job["day"] == "2026-09-28" and job["page_gap"] == [1, 2]
    assert "101" in env.found()[0] and get(wid).error_count == 0
    with db.SessionLocal() as s:
        n = s.scalars(select(Node)).one()
        assert n.last_ip == "127.0.0.1" and n.last_seen_at == NOW


def test_node_result_outside_the_job_is_dropped(env):
    wid = add_watch("2026-09-28 07:00", "2026-09-28 12:00")
    res, _ = serve([train("101", "2026-09-29 09:00")])  # 맡기지 않은 날짜
    assert res == {"ok": False} and env.found() == [] and get(wid).error_count == 1



@pytest.mark.parametrize("field, value", [("no", "@everyone"), ("no", "1234567"), ("type", "**KTX**"), ("type", "<@1>")])
def test_node_train_fields_that_reach_alerts_are_strict(field, value):
    """노드 결과의 열차번호·종류는 알림 문구에 들어간다. 형식이 다르면 받지 않는다(422)."""
    t = {**dataclasses.asdict(train("101", "2026-09-28 09:00")), field: value}
    with pytest.raises(ValueError):
        nodes.TrainIn(**t)
    nodes.TrainIn(**{**t, field: {"no": "00101", "type": "KTX-산천"}[field]})


def test_node_ip_comes_from_cloudflare_header(admin):
    """cloudflared 가 다른 기기에서 백엔드 포트로 바로 넘기면 접속 주소는 도커 게이트웨이다. 실제 IP 는 CF-Connecting-IP 에 있다."""
    node = admin.post("/api/nodes", json={"name": "집"}).json()
    for header, ip in (("203.0.113.7", "203.0.113.7"), ("not-an-ip", "testclient")):
        admin.post("/api/node/poll", json={"version": "0.0.1"},  # 버전이 달라 기다리지 않고 409
                   headers={"Authorization": f"Bearer {node['token']}", "CF-Connecting-IP": header})
        assert state.scheduler.workers[f"node:{node['id']}"].ip == ip
