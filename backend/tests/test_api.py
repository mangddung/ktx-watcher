import json
from base64 import b64encode

import itsdangerous
import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.models import AdminSession

ADMIN = "111111111111111111"


def session_cookie(data: dict) -> str:
    # Starlette SessionMiddleware 와 같은 방식으로 서명한다
    signer = itsdangerous.TimestampSigner("x" * 48)
    return signer.sign(b64encode(json.dumps(data).encode())).decode()


def admin_cookie(sid: str = "current") -> str:
    """현재 세션으로 등록된 관리자 쿠키"""
    with db.SessionLocal() as s:
        s.merge(AdminSession(uid=int(ADMIN), sid=sid))
        s.commit()
    return session_cookie({"uid": ADMIN, "sid": sid})


@pytest.fixture
def client():
    db.init_engine("sqlite://")
    db.Base.metadata.create_all(db.engine)
    return TestClient(app, follow_redirects=False)  # lifespan(스케줄러, 봇)은 띄우지 않는다


def test_no_session_is_401(client):
    for path in ("/api/watches", "/api/logs", "/api/health", "/api/me", "/api/map"):
        assert client.get(path).status_code == 401


def test_non_admin_session_is_403(client):
    client.cookies.set("ktx_session", session_cookie({"uid": "999999999999999999"}))
    assert client.get("/api/watches").status_code == 403


def test_admin_can_read_and_state_change_needs_header(client):
    client.cookies.set("ktx_session", admin_cookie())
    assert client.get("/api/watches").status_code == 200
    assert client.get("/api/logs?level=WARNING").status_code == 200
    assert client.post("/api/watches/1/end").status_code == 403  # X-Requested-With 없음


def test_only_latest_session_is_valid(client):
    old = admin_cookie("old")
    admin_cookie("new")  # 다시 로그인하면 이전 쿠키는 끊긴다
    client.cookies.set("ktx_session", old)
    assert client.get("/api/watches").status_code == 401
    client.cookies.set("ktx_session", session_cookie({"uid": ADMIN}))  # sid 없는 예전 쿠키
    assert client.get("/api/watches").status_code == 401


def test_logout_revokes_session(client):
    cookie = admin_cookie()
    client.cookies.set("ktx_session", cookie)
    assert client.post("/auth/logout").status_code == 200
    client.cookies.set("ktx_session", cookie)  # 로그아웃 전에 복사해 둔 쿠키
    assert client.get("/api/watches").status_code == 401


def test_map_returns_stations_lines_and_boundaries(client):
    client.cookies.set("ktx_session", admin_cookie())
    body = client.get("/api/map").json()
    assert body["stations"][0] == {"name": "서울", "lat": 37.5546, "lon": 126.9708, "label": "r", "major": True}
    assert body["lines"] and body["provinces"] and body["metros"] and isinstance(body["rank"], list)


def test_oauth_state_mismatch_is_rejected(client):
    client.cookies.set("ktx_session", session_cookie({"oauth_state": "expected"}))
    r = client.get("/auth/callback?code=abc&state=other")
    assert r.status_code == 302 and r.headers["location"] == "/?login=invalid"


def test_oauth_user_lookup_failure_redirects_instead_of_500(client, monkeypatch):
    from app import auth

    class Resp:
        def __init__(self, status, body):
            self.status, self.body = status, body

        async def json(self):
            return self.body

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class Http:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def post(self, *a, **kw):
            return Resp(200, {"access_token": "t"})

        def get(self, *a, **kw):
            return Resp(401, {"message": "401: Unauthorized", "code": 0})  # id 가 없는 본문

    monkeypatch.setattr(auth.aiohttp, "ClientSession", Http)
    client.cookies.set("ktx_session", session_cookie({"oauth_state": "s"}))
    r = client.get("/auth/callback?code=abc&state=s")
    assert r.status_code == 302 and r.headers["location"] == "/?login=failed"


def test_login_redirects_to_discord_with_state(client):
    r = client.get("/auth/login")
    assert r.status_code == 302
    assert r.headers["location"].startswith("https://discord.com/oauth2/authorize?")
    assert "state=" in r.headers["location"] and "scope=identify" in r.headers["location"]


def test_webhook_url_is_masked_in_responses(client):
    client.cookies.set("ktx_session", admin_cookie())
    h = {"X-Requested-With": "ktx"}
    url = "https://discord.com/api/webhooks/123456789012345678/secretTokenValue"
    r = client.post("/api/channels/webhook", json={"name": "가족", "url": url}, headers=h)
    assert r.status_code == 200 and "secretTokenValue" not in r.text
    assert "secretTokenValue" not in client.get("/api/channels").text
    assert client.post("/api/channels/webhook", json={"name": "x", "url": "https://evil.example/hook"}, headers=h).status_code == 400


def test_watch_detail_lists_available_seats_and_viewer_ids_are_validated(client):
    from datetime import datetime

    from app.models import Watch, WatchSeen

    with db.SessionLocal() as s:
        w = Watch(dep="서울", arr="부산", start_at=datetime(2026, 9, 28, 7), end_at=datetime(2026, 9, 28, 13),
                  time_basis="dep", train_types=["KTX"], seat_classes=["일반실"])
        s.add(w)
        s.flush()
        s.add(WatchSeen(watch_id=w.id, dep_date="20260928", train_no="101", seats=["일반실"]))
        s.commit()
        wid = w.id
    client.cookies.set("ktx_session", admin_cookie())
    r = client.get(f"/api/watches/{wid}")
    assert r.status_code == 200 and r.json()["available"] == [{"date": "2026-09-28", "no": "101", "seats": ["일반실"]}]
    assert client.get("/api/watches/999").status_code == 404
    h = {"X-Requested-With": "ktx"}
    assert client.put(f"/api/watches/{wid}/viewers", json={"viewers": ["abc"]}, headers=h).status_code == 400
    assert client.put(f"/api/watches/{wid}/viewers", json={"viewers": ["1"]}, headers=h).status_code == 400  # 자동 채널 없음


def test_search_streams_each_page(client, monkeypatch):
    from datetime import datetime, timedelta

    from app import scanner
    from app.state import state

    day = (datetime.now() + timedelta(days=3)).replace(hour=0, minute=0, second=0, microsecond=0)

    def train(no, hhmm):
        dep = day.replace(hour=int(hhmm[:2]), minute=int(hhmm[2:]))
        return scanner.TrainInfo(date=f"{day:%Y-%m-%d}", no=no, type="KTX", dep_at=dep, arr_at=dep + timedelta(hours=2),
                                 general="avail", special="soldout")

    def fake_pages(gate, dep, arr, d, after, until, passengers):
        yield [train("1", "0700"), train("2", "0800")]
        yield [train("3", "1900")]  # 끝(12:00) 이후라 걸러진다

    monkeypatch.setattr(scanner, "scan_pages", fake_pages)
    monkeypatch.setattr(state, "stations", ["서울", "부산"], raising=False)
    client.cookies.set("ktx_session", admin_cookie())
    r = client.post("/api/trains/search", headers={"X-Requested-With": "ktx"}, json={
        "dep": "서울", "arr": "부산", "start": f"{day:%Y-%m-%dT00:00}", "end": f"{day:%Y-%m-%dT12:00}",
        "basis": "dep", "passengers": 1})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = [json.loads(line[6:]) for line in r.text.split("\n\n") if line]
    assert "timetable" in events[0]
    assert [[t["no"] for t in e["trains"]] for e in events[1:3]] == [["1", "2"], []]
    assert events[3] == {"done": True}


def test_pause_and_resume_accept_empty_body(client):
    client.cookies.set("ktx_session", admin_cookie())
    h = {"X-Requested-With": "ktx"}
    # 본문 없이도 422(형식 오류)가 아니라 서비스 검증(400)까지 간다
    assert client.post("/api/watches/99/pause", headers=h).status_code == 400
    assert client.post("/api/watches/99/resume", headers=h).status_code == 400
    # 모르는 채널 지정 방식은 검증을 건너뛰고 채널 없이 재개되지 않게 형식 단계에서 막는다
    assert client.post("/api/watches/99/resume", headers=h, json={"mode": "x"}).status_code == 422
