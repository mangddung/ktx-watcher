"""DB 연결. 시각은 모두 KST 기준 naive datetime 으로 저장한다(서비스 대상이 한국 열차뿐이라)."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import DeclarativeBase, sessionmaker

KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    return datetime.now(KST).replace(tzinfo=None, microsecond=0)


class Base(DeclarativeBase):
    pass


engine = None
SessionLocal = sessionmaker(expire_on_commit=False)


def init_engine(url: str) -> None:
    global engine
    if url.startswith("sqlite:///") and url != "sqlite:///:memory:":
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    kw = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
    if url in ("sqlite://", "sqlite:///:memory:"):
        kw["poolclass"] = StaticPool  # 테스트용 메모리 DB 를 연결 사이에 공유한다
    engine = create_engine(url, **kw)
    if url.startswith("sqlite"):
        # SQLite 는 기본으로 외래키를 검사하지 않아 ON DELETE CASCADE 가 안 먹는다
        event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
    SessionLocal.configure(bind=engine)
