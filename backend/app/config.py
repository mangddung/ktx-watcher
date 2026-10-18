"""환경변수 설정. 값은 모두 .env 에서 온다."""

import os
from dataclasses import dataclass, field
from functools import lru_cache

MAX_ADMINS = 5


def parse_admin_ids(raw: str) -> tuple[tuple[int, ...], list[str]]:
    """쉼표로 구분한 Discord 사용자 ID 를 읽는다. (유효한 ID, 에러 메시지 목록)을 돌려준다.

    숫자가 아니거나 중복이면 건너뛰고, 5명을 넘으면 앞 5명만 쓴다.
    """
    ids: list[int] = []
    errors: list[str] = []
    for part in (p.strip() for p in raw.split(",")):
        if not part:
            continue
        if not part.isdigit() or int(part) > 2**63 - 1:
            errors.append(f"DISCORD_ADMIN_IDS: 숫자가 아닌 값을 건너뜀 ({part[:4]}…)")
            continue
        if int(part) in ids:
            errors.append(f"DISCORD_ADMIN_IDS: 중복 ID 를 건너뜀 ({part})")
            continue
        ids.append(int(part))
    if len(ids) > MAX_ADMINS:
        cut = ", ".join(str(i) for i in ids[MAX_ADMINS:])
        errors.append(f"DISCORD_ADMIN_IDS: 관리자는 최대 {MAX_ADMINS}명, 앞 {MAX_ADMINS}명만 적용: 제외 {cut}")
        ids = ids[:MAX_ADMINS]
    return tuple(ids), errors


def parse_snowflake(name: str, raw: str) -> tuple[int | None, list[str]]:
    """Discord ID(snowflake). 17~20자리 숫자이고 64비트 범위 안이어야 한다."""
    raw = raw.strip()
    if not raw:
        return None, []
    if not raw.isdigit() or not 15 <= len(raw) <= 20 or int(raw) > 2**63 - 1:
        return None, [f"{name}: Discord ID 형식이 아닙니다({len(raw)}자). 서버 이름 우클릭 > 서버 ID 복사 값을 넣으세요"]
    return int(raw), []


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


@dataclass(frozen=True)
class Settings:
    bot_token: str
    client_id: str
    client_secret: str
    guild_id: int | None
    admin_ids: tuple[int, ...]
    admin_errors: list[str] = field(default_factory=list)
    public_base_url: str = "http://localhost:14371"
    session_secret: str = ""
    telegram_token: str = ""
    data_go_kr_key: str = ""  # 선택. 있으면 열차 시간표를 새로 받는다(timetable.py)
    cutoff_minutes: int = 60
    scan_interval: tuple[int, int] = (40, 80)
    page_gap: tuple[int, int] = (1, 2)
    max_fails: int = 10
    block_cooldown_minutes: int = 30
    max_active: int = 10
    max_watch_days: int = 3
    local_scan: bool = True  # 0 이면 중앙은 감시 조회를 하지 않고 노드만 쓴다(웹 수동 조회는 계속 중앙 IP)
    log_level: str = "INFO"
    database_url: str = "sqlite:///data/ktx.db"

    @property
    def secure_cookie(self) -> bool:
        return self.public_base_url.startswith("https://")

    @property
    def redirect_uri(self) -> str:
        return self.public_base_url.rstrip("/") + "/auth/callback"

    def secrets(self) -> list[str]:
        """로그에서 원문 그대로 가릴 값들."""
        return [s for s in (self.bot_token, self.client_secret, self.session_secret, self.telegram_token, self.data_go_kr_key) if s]


@lru_cache
def get_settings() -> Settings:
    env = os.environ.get
    admin_ids, admin_errors = parse_admin_ids(env("DISCORD_ADMIN_IDS", ""))
    guild_id, guild_errors = parse_snowflake("DISCORD_GUILD_ID", env("DISCORD_GUILD_ID", ""))
    return Settings(
        bot_token=env("DISCORD_BOT_TOKEN", "").strip(),
        client_id=env("DISCORD_CLIENT_ID", "").strip(),
        client_secret=env("DISCORD_CLIENT_SECRET", "").strip(),
        guild_id=guild_id,
        admin_ids=admin_ids,
        admin_errors=admin_errors + guild_errors,
        public_base_url=env("PUBLIC_BASE_URL", "http://localhost:14371").strip().rstrip("/"),
        session_secret=env("SESSION_SECRET", "").strip(),
        telegram_token=env("TELEGRAM_BOT_TOKEN", "").strip(),
        data_go_kr_key=env("DATA_GO_KR_KEY", "").strip(),
        cutoff_minutes=_int("WATCH_CUTOFF_MINUTES", 60),
        scan_interval=(_int("SCAN_INTERVAL_MIN", 40), _int("SCAN_INTERVAL_MAX", 80)),
        page_gap=(_int("PAGE_GAP_MIN", 1), _int("PAGE_GAP_MAX", 2)),
        max_fails=_int("MAX_FAILS", 10),
        block_cooldown_minutes=_int("BLOCK_COOLDOWN_MINUTES", 30),
        max_active=_int("MAX_ACTIVE_WATCHES", 10),
        max_watch_days=_int("MAX_WATCH_DAYS", 3),
        local_scan=env("LOCAL_SCAN", "1").strip() != "0",
        log_level=env("LOG_LEVEL", "INFO").strip().upper() or "INFO",
        database_url=env("DATABASE_URL", "sqlite:///data/ktx.db").strip(),
    )


def startup_problems(s: Settings) -> list[str]:
    """이 중 하나라도 있으면 서비스를 시작하지 않는다."""
    problems = []
    if not s.admin_ids:
        problems.append("DISCORD_ADMIN_IDS 에 유효한 관리자 ID 가 없어 아무도 로그인할 수 없습니다")
    if len(s.session_secret) < 32:
        problems.append("SESSION_SECRET 은 32자 이상이어야 합니다 (예: python -c \"import secrets; print(secrets.token_urlsafe(48))\")")
    if not (s.client_id and s.client_secret):
        problems.append("DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET 이 필요합니다 (웹 로그인)")
    return problems
