"""DB 모델. 바꿀 때는 alembic 마이그레이션을 함께 만든다(migrations/versions)."""

from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, now_kst


class Channel(Base):
    __tablename__ = "channel"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))  # discord_webhook | discord_bot | telegram
    name: Mapped[str] = mapped_column(String(100))
    target: Mapped[str] = mapped_column(Text)  # 웹훅 URL | Discord 채널 ID | Telegram chat_id. API 응답에서는 가린다
    managed: Mapped[bool] = mapped_column(Boolean, default=False)  # 봇이 감시용으로 만든 채널
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    broken_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_kst)


class Watch(Base):
    __tablename__ = "watch"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dep: Mapped[str] = mapped_column(String(30))
    arr: Mapped[str] = mapped_column(String(30))
    start_at: Mapped[datetime] = mapped_column(DateTime)  # start_at~end_at 은 하나로 이어진 구간
    end_at: Mapped[datetime] = mapped_column(DateTime)
    time_basis: Mapped[str] = mapped_column(String(3))  # dep | arr
    train_types: Mapped[list] = mapped_column(JSON)
    seat_classes: Mapped[list] = mapped_column(JSON)
    passengers: Mapped[int] = mapped_column(Integer, default=1)
    trains: Mapped[list | None] = mapped_column(JSON, nullable=True)  # [{"date","no","dep"}], null 이면 조건에 맞는 전체
    status: Mapped[str] = mapped_column(String(10), default="active")  # creating(자동 채널 만드는 중) | active | paused | ended
    pause_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_via: Mapped[str] = mapped_column(String(10), default="web")
    scan_count: Mapped[int] = mapped_column(Integer, default=0)
    found_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    remaining: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_kst)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    end_reason: Mapped[str | None] = mapped_column(String(20), nullable=True)  # no_targets | manual
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    channels: Mapped[list[Channel]] = relationship(secondary="watch_channel", lazy="selectin")
    viewers: Mapped[list["WatchViewer"]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class WatchChannel(Base):
    __tablename__ = "watch_channel"

    watch_id: Mapped[int] = mapped_column(ForeignKey("watch.id", ondelete="CASCADE"), primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channel.id", ondelete="CASCADE"), primary_key=True)


class WatchViewer(Base):
    __tablename__ = "watch_viewer"

    watch_id: Mapped[int] = mapped_column(ForeignKey("watch.id", ondelete="CASCADE"), primary_key=True)
    discord_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)


class SentMessage(Base):
    __tablename__ = "sent_message"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    watch_id: Mapped[int] = mapped_column(ForeignKey("watch.id", ondelete="CASCADE"), index=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channel.id", ondelete="CASCADE"))
    message_id: Mapped[str] = mapped_column(String(40))
    is_summary: Mapped[bool] = mapped_column(Boolean, default=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=now_kst)


class WatchSeen(Base):
    """이미 알린 좌석. 다시 매진되면 지워서 다음에 또 알린다."""

    __tablename__ = "watch_seen"
    __table_args__ = (UniqueConstraint("watch_id", "dep_date", "train_no"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    watch_id: Mapped[int] = mapped_column(ForeignKey("watch.id", ondelete="CASCADE"), index=True)
    dep_date: Mapped[str] = mapped_column(String(8))
    train_no: Mapped[str] = mapped_column(String(10))
    seats: Mapped[list] = mapped_column(JSON)


class AdminSession(Base):
    """관리자마다 유효한 세션 하나. 다시 로그인하거나 로그아웃하면 이전 쿠키는 무효가 된다."""

    __tablename__ = "admin_session"

    uid: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    sid: Mapped[str] = mapped_column(String(64))
