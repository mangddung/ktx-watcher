"""원격 조회 노드. `python -m app.node` 로 띄운다(NODE_PLAN.md).

중앙에 poll 해서 잡(조회 묶음 하나)을 받고, 이 PC 의 IP 로 코레일을 조회해 결과를 돌려준다.
상태가 없다: DB·Discord·설정 검사 없이 env 4개만 쓴다. 중앙이 재시작해도 다시 poll 하면 이어진다.
"""

import asyncio
import dataclasses
import logging
import os
import sys
from datetime import date, datetime

import aiohttp

from app.logs import setup_logging
from app.nodes import POLL_WAIT, PREFIX, VERSION
from app.scanner import Gate, is_block_error, scan_day

log = logging.getLogger("app.node")

RETRY_MIN, RETRY_MAX = 5, 60  # 연결 실패 때 다시 붙는 간격(초). 실패할수록 두 배로 늘린다
REJECTED_WAIT = 60  # 중앙이 거절(토큰, 버전)하면 이만큼 쉬고 다시 묻는다


def run_job(gate: Gate, job: dict) -> dict:
    gate.page_gap = tuple(job["page_gap"])
    try:
        trains = scan_day(gate, job["dep"], job["arr"], date.fromisoformat(job["day"]),
                          datetime.fromisoformat(job["after"]), datetime.fromisoformat(job["until"]), job["passengers"])
    except Exception as e:
        log.error("%s→%s %s 조회 실패: %s: %s", job["dep"], job["arr"], job["day"], type(e).__name__, e)
        return {"id": job["id"], "error": f"{type(e).__name__}: {e}"[:300], "block": is_block_error(e)}
    log.info("%s→%s %s 조회 %d편", job["dep"], job["arr"], job["day"], len(trains))
    return {"id": job["id"], "trains": [{**dataclasses.asdict(t), "dep_at": t.dep_at.isoformat(),
                                         "arr_at": t.arr_at.isoformat()} for t in trains]}


async def main() -> None:
    env = os.environ.get
    url, token = env("CENTRAL_URL", "").strip().rstrip("/"), env("NODE_TOKEN", "").strip()
    cf_id, cf_secret = env("CF_ACCESS_CLIENT_ID", "").strip(), env("CF_ACCESS_CLIENT_SECRET", "").strip()
    setup_logging(env("LOG_LEVEL", "INFO").strip().upper() or "INFO", [token, cf_secret])
    if not url.startswith(("https://", "http://")) or not token.startswith(PREFIX):
        log.critical("CENTRAL_URL(https://...) 과 웹에서 발급한 NODE_TOKEN(%s...) 이 필요합니다", PREFIX)
        sys.exit(1)
    if not url.startswith("https://"):
        log.warning("CENTRAL_URL 이 https 가 아닙니다. 노드 토큰이 암호화되지 않고 오갑니다")
    headers = {"Authorization": f"Bearer {token}"}
    if cf_id and cf_secret:  # Cloudflare Access 서비스 토큰
        headers |= {"CF-Access-Client-Id": cf_id, "CF-Access-Client-Secret": cf_secret}

    gate = Gate((1, 2))  # 간격은 잡마다 중앙이 정해 보낸다
    retry = RETRY_MIN
    log.info("노드 시작: %s (pykorail %s)", url, VERSION)
    async with aiohttp.ClientSession(headers=headers, timeout=aiohttp.ClientTimeout(total=POLL_WAIT + 20)) as http:
        while True:
            try:
                async with http.post(f"{url}/api/node/poll", json={"version": VERSION}, allow_redirects=False) as r:
                    if r.status == 204:
                        retry = RETRY_MIN
                        continue
                    if r.status != 200 or r.content_type != "application/json":
                        # 401·409 는 중앙의 안내, 302·403(HTML)은 Cloudflare Access 가 막은 것
                        log.error("중앙이 거절했습니다 HTTP %s: %s", r.status, (await r.text())[:200])
                        await asyncio.sleep(REJECTED_WAIT)
                        continue
                    job = await r.json()
                retry = RETRY_MIN
                body = await asyncio.to_thread(run_job, gate, job)
                async with http.post(f"{url}/api/node/result", json=body, allow_redirects=False) as r:
                    if r.status != 200:
                        log.warning("결과를 보내지 못했습니다 HTTP %s", r.status)
            except (aiohttp.ClientError, TimeoutError) as e:
                log.warning("중앙에 연결하지 못했습니다: %s: %s. %d초 뒤 다시 시도", type(e).__name__, e, retry)
                await asyncio.sleep(retry)
                retry = min(retry * 2, RETRY_MAX)


if __name__ == "__main__":
    asyncio.run(main())
