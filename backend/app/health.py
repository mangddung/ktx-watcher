"""서비스 상태 등급. 최근 1시간 기록으로 계산한다.

조회 중단(차단 의심, 쿨다운)은 조회할 수 있는 워커 전부가 그럴 때만 critical 이다. 일부만 그러면 warning.
노선 공유는 정상 동작이라 등급을 바꾸지 않고 notes 로만 보여준다(시스템 채널에 알림이 가지 않는다).
"""

import time
from collections import Counter
from datetime import timedelta

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Node, Watch
from app.scheduler import RETRIES, UNDELIVERED_SHOWN
from app.service import MANUAL_PAUSE
from app.state import state


def compute() -> dict:
    s, sch = state.settings, state.scheduler
    now = sch.clock()
    recent = [h for h in sch.history if h[0] >= now - timedelta(hours=1)]
    errors = sum(1 for h in recent if not h[1])
    rate = round(errors * 100 / len(recent)) if recent else 0

    on = [w for w in sch.workers.values() if w.online(now)]
    blocked = [w for w in on if w.block_since and now - w.block_since >= timedelta(minutes=10)]
    cooling = [w for w in on if w.cooling(now)]
    all_cooling = bool(on) and len(cooling) == len(on)
    starting = time.monotonic() - state.started < 60  # 재시작 직후 노드가 다시 poll 해 올 때까지

    critical: list[dict] = []
    title = ""
    if not on and not starting:
        critical.append({"label": "조회 워커", "value": "없음 · 중앙 조회 꺼짐, 노드 오프라인"})
        title = "조회할 곳 없음"
    if on and len(blocked) == len(on):
        mins = int((now - max(w.block_since for w in blocked)).total_seconds() // 60)
        critical.append({"label": "차단 의심", "value": f"{mins}분째 403·대기열 차단"})
        title = "조회 중단 · IP 차단 의심"
    if all_cooling:
        mins = max(1, int((min(w.cooldown_until for w in cooling) - now).total_seconds() // 60))
        critical.append({"label": "다시 시도", "value": f"{mins}분 뒤"})
        title = title or "조회 중단 · 연속 실패"
    if state.selftest and state.selftest["level"] == "critical":
        critical.append({"label": "조회 확인", "value": state.selftest["detail"]})
        title = title or "열차 조회 안 됨"
    if len(recent) >= 4 and rate >= 50:
        critical.append({"label": "실패율", "value": f"{rate}% · 1시간"})
        title = title or "조회 실패율 높음"
    interval = s.scan_interval[1] + sch.cycle_seconds
    if sch.task is not None and sch.task.done():
        critical.append({"label": "스케줄러", "value": "멈춤"})
        title = title or "스케줄러 멈춤"
    elif sch.last_cycle_at and not all_cooling and now - sch.last_cycle_at > timedelta(seconds=interval * 3):
        critical.append({"label": "마지막 조회", "value": f"{int((now - sch.last_cycle_at).total_seconds() // 60)}분 전"})
        title = title or "조회가 멈춘 것 같습니다"
    notes = [{"label": "노선 공유", "value": f"노선이 워커보다 적어 노선 {len(sch.routes)}개를 워커 {len(on)}개가 번갈아 조회"}] \
        if sch.sharing else []
    if critical:
        return {"level": "critical", "title": title, "reasons": critical, "notes": notes, "errors1h": errors, "failRate1h": rate}

    warning: list[dict] = []
    if errors:
        warning.append({"label": "조회 오류", "value": f"{errors}건 · {rate}%"})
    with SessionLocal() as db:
        paused = db.scalar(select(func.count()).select_from(Watch).where(
            Watch.status == "paused", Watch.pause_reason != MANUAL_PAUSE))
    if paused:
        warning.append({"label": "일시정지 감시", "value": f"{paused}개 · 채널 끊김"})
    if lost := sum(1 for at, _, _ in sch.undelivered if at >= now - UNDELIVERED_SHOWN):
        warning.append({"label": "알림 전송 실패", "value": f"{lost}건 · 1시간 (재시도 {RETRIES}회 후 포기)"})
    # 시작 직후 봇이 연결되는 몇 초 동안은 경고하지 않는다(시스템 채널에 WARNING→OK 알림이 괜히 가지 않게)
    if s.bot_token and not (state.bot and state.bot.ready) and time.monotonic() - state.started > 60:
        warning.append({"label": "Discord 봇", "value": "연결 끊김"})
    if state.telegram and not state.telegram.ok:
        warning.append({"label": "Telegram", "value": "폴링 실패"})
    if s.admin_errors:
        warning.append({"label": "설정 오류", "value": f"{len(s.admin_errors)}건 · 로그 확인"})
    if state.selftest and state.selftest["level"] == "warning":
        warning.append({"label": "시간표", "value": state.selftest["detail"]})
    if state.missing_stations:
        warning.append({"label": "역 목록", "value": f"{len(state.missing_stations)}개 역 빠짐"})
    if len(sch.workers) > 1 and (trouble := [f"{w.name} {'쿨다운' if w.cooling(now) else '차단 의심'}" for w in on if w.cooling(now) or w.block_since]):
        warning.append({"label": "조회 워커", "value": ", ".join(trouble)})
    with SessionLocal() as db:
        names = dict(db.execute(select(Node.id, Node.name).where(Node.revoked_at.is_(None))).all())
    remote = {i: sch.workers.get(f"node:{i}") for i in names}
    if not starting and (off := [names[i] for i, w in remote.items() if not (w and w.online(now))]):
        mismatch = [names[i] for i, w in remote.items() if w and w.status(now) == "version"]
        warning.append({"label": "노드", "value": f"{', '.join(off)} 오프라인" + (" (버전 불일치)" if mismatch else "")})
    if same := [ip for ip, n in Counter(w.ip for w in on if w.remote).items() if n > 1]:
        warning.append({"label": "같은 IP 노드", "value": f"{', '.join(same)} · 요청이 한 IP 로 몰림"})
    if warning:
        return {"level": "warning", "title": f"확인 필요 {len(warning)}건", "reasons": warning, "notes": notes,
                "errors1h": errors, "failRate1h": rate}
    return {"level": "ok", "title": "모든 서비스 정상", "reasons": [], "notes": notes, "errors1h": 0, "failRate1h": 0}
