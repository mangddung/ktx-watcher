"""FastAPI 앱. 시작할 때 설정 검사, DB 마이그레이션, 스케줄러·봇·Telegram·시작 점검(selftest)을 띄운다."""

import asyncio
import logging
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from app import db, nodes, selftest, service, timetable
from app.api import router as api_router
from app.auth import router as auth_router
from app.config import get_settings, startup_problems
from app.logs import setup_logging
from app.notify import Notifier
from app.scanner import Gate
from app.scheduler import Scheduler
from app.stations import KTX_STATIONS
from app.state import state

settings = get_settings()
setup_logging(settings.log_level, settings.secrets())
log = logging.getLogger("app")

for err in settings.admin_errors:
    log.error(err)
if problems := startup_problems(settings):
    for p in problems:
        log.critical(p)
    sys.exit(1)

state.settings = settings
state.stations = list(KTX_STATIONS)
db.init_engine(settings.database_url)


def migrate() -> None:
    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(cfg, "head")


def _log_crash(task: asyncio.Task) -> None:
    """백그라운드 작업이 예외로 끝나면 조용히 사라지지 않게 로그를 남긴다."""
    if not task.cancelled() and task.exception():
        e = task.exception()
        log.error("백그라운드 작업 %s 가 멈췄습니다: %s: %s", task.get_name(), type(e).__name__, e)


async def announce_start() -> None:
    """봇 연결을 기다렸다가(최대 90초) 시스템 채널과 진행 중인 감시 채널에 시작을 알린다.
    재연결이 잦으면 Discord 가 연결을 늦추기도 해서 고정 시간 뒤에 보내면 메시지가 빠진다."""
    if settings.bot_token and settings.guild_id:
        for _ in range(90):
            if state.bot and state.bot.ready:
                break
            await asyncio.sleep(1)
    await service.system("서비스 시작")
    await service.announce_active("감시 재개", " · 서비스 시작")


@asynccontextmanager
async def lifespan(_: FastAPI):
    state.started = time.monotonic()
    await asyncio.to_thread(migrate)
    state.gate = Gate(settings.page_gap)
    state.notifier = Notifier(settings.bot_token, settings.telegram_token)
    await state.notifier.start()
    tasks = []
    if settings.bot_token and settings.guild_id:
        from app.bot import run_bot

        tasks.append(asyncio.create_task(run_bot(), name="discord"))
    else:
        log.warning("DISCORD_BOT_TOKEN 또는 DISCORD_GUILD_ID 가 없어 Discord 봇 없이 동작합니다 (웹훅만 쓸 수 있음)")
    if settings.telegram_token:
        from app.telegram import TelegramPoller

        state.telegram = TelegramPoller(settings.telegram_token)
        tasks.append(asyncio.create_task(state.telegram.run(), name="telegram"))
    timetable.load()
    if settings.data_go_kr_key:
        tasks.append(asyncio.create_task(timetable.refresh_loop(settings.data_go_kr_key), name="timetable"))
    else:
        log.info("DATA_GO_KR_KEY 가 없어 앱에 들어 있는 열차 시간표를 그대로 씁니다")
    state.scheduler = Scheduler(local=settings.local_scan)
    if not settings.local_scan:
        log.info("LOCAL_SCAN=0: 감시 조회는 노드만 합니다")
    state.scheduler.task = asyncio.create_task(state.scheduler.run(), name="scheduler")
    tasks.append(state.scheduler.task)
    tasks.append(asyncio.create_task(selftest.run(), name="selftest"))
    for t in tasks:
        t.add_done_callback(_log_crash)
    log.info("서비스 시작 (역 %d개)", len(state.stations))
    tasks.append(asyncio.create_task(announce_start(), name="announce"))
    yield
    await service.announce_active("감시 중단", " · 서비스 종료")
    await service.system("서비스 종료")
    for t in tasks:
        t.cancel()
    if state.bot:
        await state.bot.close()
    await state.notifier.close()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, session_cookie="ktx_session",
                   max_age=14 * 24 * 3600, same_site="lax", https_only=settings.secure_cookie)
app.include_router(auth_router)
app.include_router(api_router)
app.include_router(nodes.router)
