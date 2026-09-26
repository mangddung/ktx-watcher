"""동대구 -> 수원 코레일 취소표 감시 후 디스코드 웹훅 알림."""

import json
import os
import random
import signal
import sys
import time
import traceback
import urllib.request
from datetime import datetime, timedelta, timezone

from pykorail import Korail, NoResultsError

KST = timezone(timedelta(hours=9))
DEP, ARR = "동대구", "수원"
WINDOW_START = datetime(2026, 9, 28, 0, 0, tzinfo=KST)  # 출발 시각 기준
WINDOW_END = datetime(2026, 9, 28, 13, 0, tzinfo=KST)  # 도착 시각 기준
MIN_TRIP = timedelta(hours=1, minutes=30)  # 최단 소요(KTX 약 1시간 50분)보다 여유 있게, 이후 출발은 도착 기준 초과라 조회 생략
EXCLUDE_TYPES = ("무궁화",)
INTERVAL = (40, 80)  # 스캔 간격(초), 평균 60초 난수
PAGE_GAP = (2, 5)  # 한 스캔 안의 페이지 요청 간격(초)
MAX_FAILS = 10  # 연속 실패가 이만큼 쌓이면 중단(크래시) 처리

WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "")
DEBUG_WEBHOOK = os.environ.get("DISCORD_DEBUG_WEBHOOK_URL", "")  # 선택, 비우면 디버그 전송 안 함
WEBHOOK_PREFIXES = ("https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/")


def post(url, text):
    """디스코드 전송. 알림 실패가 감시를 죽이면 안 되므로 예외는 로그로만 남긴다."""
    body = json.dumps({"content": text[:1990]}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "DiscordBot (ktx-alert, 1.0)"},
    )
    try:
        urllib.request.urlopen(req, timeout=10).close()
    except Exception as e:
        print(f"[notify 실패] {type(e).__name__}: {e}", file=sys.stderr)


def notify(text):
    post(WEBHOOK, text)
    print(text)


def debug(log):
    """한 사이클의 디버그 로그를 디버그 웹훅으로 한 번에 전송."""
    if DEBUG_WEBHOOK:
        post(DEBUG_WEBHOOK, "```\n" + "\n".join(log)[-1900:] + "\n```")


def dep_dt(t):
    return datetime.strptime(t.dep_date + t.dep_time[:4], "%Y%m%d%H%M").replace(tzinfo=KST)


def arr_dt(t):
    return datetime.strptime(t.arr_date + t.arr_time[:4], "%Y%m%d%H%M").replace(tzinfo=KST)


def seats(t):
    s = set()
    if t.has_general_seat():
        s.add("일반실")
    if t.has_special_seat():
        s.add("특실")
    return s


def scan(korail, log):
    """구간 내 모든 열차 조회. 한 번에 10편씩 오므로 마지막 출발+1분으로 이어서 조회."""

    def say(msg):
        print(msg)
        log.append(msg)

    now = datetime.now(KST)
    found = {}
    day = WINDOW_START
    while day <= WINDOW_END:
        cursor = max(day, now)
        day_end = min(day + timedelta(days=1), WINDOW_END - MIN_TRIP)
        while cursor <= day_end:
            try:
                trains = korail.trains.search(DEP, ARR, depart_after=cursor, include_no_seats=True)
            except NoResultsError:
                say(f"[조회] {DEP}→{ARR} {cursor:%m/%d %H:%M} 이후: 결과 없음")
                break
            say(f"[조회] {DEP}→{ARR} {cursor:%m/%d %H:%M} 이후: {len(trains)}편 "
                f"({trains[0].dep_time[:4]}~{trains[-1].dep_time[:4]} 출발)")
            for t in trains:
                if arr_dt(t) <= WINDOW_END and dep_dt(t).date() == day.date():
                    found[(t.dep_date, t.train_no)] = t
            nxt = dep_dt(trains[-1]) + timedelta(minutes=1)
            if nxt <= cursor or nxt.date() != day.date():
                break
            cursor = nxt
            time.sleep(random.uniform(*PAGE_GAP))
        day = datetime.combine(day.date() + timedelta(days=1), datetime.min.time(), KST)
    return [t for t in found.values() if not any(x in t.train_type_name for x in EXCLUDE_TYPES)]


def run():
    korail = Korail()
    notified = {}  # (날짜, 열차번호) -> 이미 알린 좌석 종류
    fails = 0
    notify(f"🚄 감시 시작: {DEP}→{ARR} {WINDOW_START:%m/%d %H:%M} ~ {WINDOW_END:%m/%d %H:%M} 도착")
    while datetime.now(KST) < WINDOW_END:
        log = []
        try:
            trains = scan(korail, log)
        except Exception as e:
            fails += 1
            print(traceback.format_exc(), file=sys.stderr)
            log.append(f"[실패 {fails}/{MAX_FAILS}] {type(e).__name__}: {e}")
            debug(log)
            if fails == 1:
                notify(f"⚠️ 조회 오류 (재시도 중): {type(e).__name__}: {e}")
            if fails >= MAX_FAILS:
                raise
            korail = Korail()
            time.sleep(min(60 * 2**fails, 600) + random.uniform(0, 30))
            continue
        if fails:
            notify(f"✅ 조회 복구 ({fails}회 연속 실패 후)")
            fails = 0

        log.append(f"[스캔 완료 {datetime.now(KST):%m/%d %H:%M:%S}] 대상 {len(trains)}편, "
                   f"좌석 있음 {sum(bool(seats(t)) for t in trains)}편")
        print(log[-1])
        debug(log)
        new = []
        for t in trains:
            key = (t.dep_date, t.train_no)
            avail = seats(t)
            if avail - notified.get(key, set()):
                new.append(f"{t.summary()}  →  {', '.join(sorted(avail))}")
            notified[key] = avail  # 다시 매진되면 비워져서 다음에 또 알림
        if new:
            notify("🎫 취소표 발견!\n" + "\n".join(new))
        time.sleep(random.uniform(*INTERVAL))
    notify("🏁 감시 구간 종료, 감시를 마칩니다.")


def on_sigterm(*_):
    notify("⏹️ 감시 중지됨 (컨테이너 종료)")
    sys.exit(0)


if __name__ == "__main__":
    if not WEBHOOK.startswith(WEBHOOK_PREFIXES):
        sys.exit("DISCORD_WEBHOOK_URL 이 없거나 디스코드 웹훅 주소가 아닙니다")
    if DEBUG_WEBHOOK and not DEBUG_WEBHOOK.startswith(WEBHOOK_PREFIXES):
        sys.exit("DISCORD_DEBUG_WEBHOOK_URL 이 디스코드 웹훅 주소가 아닙니다")
    signal.signal(signal.SIGTERM, on_sigterm)
    try:
        run()
    except Exception as e:
        notify(f"🛑 감시 중단 (오류): {type(e).__name__}: {e}")
        raise
