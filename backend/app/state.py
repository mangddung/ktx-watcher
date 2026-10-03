"""실행 중에 공유하는 객체들. main.py 가 시작할 때 채운다."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class State:
    settings: Any = None
    gate: Any = None  # scanner.Gate
    notifier: Any = None  # notify.Notifier
    bot: Any = None  # bot.Bot, 토큰이 없으면 None
    scheduler: Any = None  # scheduler.Scheduler
    telegram: Any = None  # telegram.TelegramPoller, 토큰이 없으면 None
    stations: list[str] = field(default_factory=list)
    missing_stations: list[str] = field(default_factory=list)
    selftest: dict | None = None  # selftest.py 시작 점검 결과 {"level": warning|critical, "detail"}. 정상이면 None
    timetable: dict = field(default_factory=lambda: {"days": {}})  # timetable.py
    routes: dict[str, list[str]] = field(default_factory=dict)  # 출발역 → 직통 도착역
    started: float = 0.0  # time.monotonic() 기준 시작 시각


state = State()
