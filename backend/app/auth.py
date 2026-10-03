"""Discord OAuth2 로그인과 관리자 확인. 권한 확인은 모든 API 에서 백엔드가 한다."""

import logging
import secrets
from urllib.parse import urlencode

import aiohttp
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.db import SessionLocal
from app.models import AdminSession
from app.state import state

log = logging.getLogger(__name__)
router = APIRouter()

AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
TOKEN_URL = "https://discord.com/api/oauth2/token"
ME_URL = "https://discord.com/api/users/@me"


def avatar_url(user: dict) -> str | None:
    if not user.get("avatar"):
        return None
    return f"https://cdn.discordapp.com/avatars/{user['id']}/{user['avatar']}.png?size=64"


@router.get("/auth/login")
async def login(request: Request):
    s = state.settings
    request.session["oauth_state"] = st = secrets.token_urlsafe(24)
    q = urlencode({"client_id": s.client_id, "redirect_uri": s.redirect_uri, "response_type": "code",
                   "scope": "identify", "state": st, "prompt": "none"})
    return RedirectResponse(f"{AUTHORIZE_URL}?{q}", status_code=302)


@router.get("/auth/callback")
async def callback(request: Request, code: str = "", error: str = ""):
    # 모듈 이름 state 와 겹치지 않게 쿼리의 state 는 직접 읽는다
    got_state = request.query_params.get("state", "")
    expected = request.session.pop("oauth_state", None)
    if error or not code:
        return RedirectResponse("/?login=cancelled", status_code=302)
    if not expected or not secrets.compare_digest(expected, got_state):
        log.warning("로그인 거부: state 불일치")
        return RedirectResponse("/?login=invalid", status_code=302)
    s = state.settings
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as http:
            async with http.post(TOKEN_URL, data={
                "client_id": s.client_id, "client_secret": s.client_secret, "grant_type": "authorization_code",
                "code": code, "redirect_uri": s.redirect_uri,
            }) as r:
                if r.status != 200:
                    log.warning("로그인 실패: 토큰 교환 HTTP %s", r.status)
                    return RedirectResponse("/?login=failed", status_code=302)
                token = (await r.json())["access_token"]
            async with http.get(ME_URL, headers={"Authorization": f"Bearer {token}"}) as r:
                if r.status != 200:
                    log.warning("로그인 실패: 사용자 조회 HTTP %s", r.status)
                    return RedirectResponse("/?login=failed", status_code=302)
                user = await r.json()
        uid = int(user["id"])
    except (aiohttp.ClientError, KeyError, TimeoutError, ValueError) as e:
        log.warning("로그인 실패: %s", type(e).__name__)
        return RedirectResponse("/?login=failed", status_code=302)
    if uid not in s.admin_ids:
        log.warning("로그인 거부 (관리자 아님) user=%s", uid)
        return RedirectResponse("/?login=denied", status_code=302)
    sid = secrets.token_urlsafe(32)
    with SessionLocal() as db:
        db.merge(AdminSession(uid=uid, sid=sid))  # 이전 세션(다른 기기 포함)은 여기서 무효가 된다
        db.commit()
    request.session.clear()
    request.session.update({"uid": str(uid), "sid": sid, "name": user.get("global_name") or user.get("username", ""),
                            "avatar": avatar_url(user)})
    log.info("로그인 user=%s", uid)
    return RedirectResponse("/", status_code=302)


@router.post("/auth/logout")
async def logout(request: Request):
    uid, sid = request.session.get("uid"), request.session.get("sid")
    if uid and sid:
        with SessionLocal() as db:
            db.query(AdminSession).filter_by(uid=int(uid), sid=sid).delete()
            db.commit()
    request.session.clear()
    return {"ok": True}


def require_admin(request: Request) -> int:
    """관리자 세션이 없거나 현재 세션이 아니면 401, 관리자 목록에서 빠졌으면 403. 상태를 바꾸는 요청은 커스텀 헤더를 요구한다(CSRF)."""
    uid = request.session.get("uid")
    if not uid:
        raise HTTPException(401, "로그인이 필요합니다")
    if int(uid) not in state.settings.admin_ids:
        request.session.clear()
        raise HTTPException(403, "관리자가 아닙니다")
    with SessionLocal() as db:
        row = db.get(AdminSession, int(uid))
    if not row or not secrets.compare_digest(row.sid, request.session.get("sid", "")):
        request.session.clear()
        raise HTTPException(401, "다른 곳에서 로그인했거나 로그아웃되었습니다")
    if request.method not in ("GET", "HEAD") and request.headers.get("x-requested-with") != "ktx":
        raise HTTPException(403, "잘못된 요청입니다")
    return int(uid)
