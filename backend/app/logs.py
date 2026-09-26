"""로그 설정. stdout 과 웹 UI 용 링 버퍼 양쪽에 가림 필터를 거친 줄만 남긴다."""

import logging
import re
import sys
import time
from collections import deque

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Discord 웹훅 URL 의 ID 와 토큰
    (re.compile(r"(https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api(?:/v\d+)?/webhooks/)[^/\s]+/[\w-]+"), r"\1****/****"),
    # Telegram 봇 토큰 (URL 안 포함)
    (re.compile(r"(?<!\d)\d{6,12}:[A-Za-z0-9_-]{30,}"), "****"),  # bot<토큰> 처럼 글자 뒤에 붙어도 잡는다
    # Discord 봇 토큰
    (re.compile(r"\b[MNO][A-Za-z\d_-]{22,30}\.[A-Za-z\d_-]{5,8}\.[A-Za-z\d_-]{25,45}\b"), "****"),
    # Authorization 헤더
    (re.compile(r"(?i)(authorization['\"]?\s*[:=]\s*['\"]?)(?:bot |bearer )?[^\s'\",}]+"), r"\1****"),
    # OAuth code/state, 세션 쿠키
    (re.compile(r"([?&](?:code|state)=)[^&\s\"']+"), r"\1****"),
    (re.compile(r"(ktx_session=)[^;\s]+"), r"\1****"),
    # Discord 사용자·서버·채널 ID, Telegram chat_id: 끝 4자리만 남긴다
    (re.compile(r"(?<![\w.])-?\d{5,16}(\d{4})(?![\w.])"), r"****\1"),
]


class Redactor:
    def __init__(self, secrets: list[str] | None = None) -> None:
        self.secrets = [s for s in (secrets or []) if len(s) >= 8]

    def __call__(self, text: str) -> str:
        for s in self.secrets:
            text = text.replace(s, "****")
        for pattern, repl in _PATTERNS:
            text = pattern.sub(repl, text)
        return text


class RedactFilter(logging.Filter):
    """메시지를 포맷한 뒤 가린다. DEBUG 가 아니면 스택트레이스 대신 예외 타입과 메시지만 남긴다."""

    def __init__(self, redact: Redactor, keep_traceback: bool) -> None:
        super().__init__()
        self.redact = redact
        self.keep_traceback = keep_traceback

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if record.exc_info and not self.keep_traceback:
            exc = record.exc_info[1]
            if exc is not None:
                msg = f"{msg}: {type(exc).__name__}: {exc}"
            record.exc_info = None
            record.exc_text = None
        elif record.exc_info:
            record.exc_text = self.redact(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        record.msg = self.redact(msg)
        record.args = None
        return True


class RingBuffer(logging.Handler):
    """웹 UI 로그 탭용 최근 로그."""

    def __init__(self, size: int = 1000) -> None:
        super().__init__()
        self.lines: deque[dict] = deque(maxlen=size)

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append({
            "t": int(record.created * 1000),
            "level": record.levelname,
            "source": record.name.removeprefix("app."),
            "msg": record.getMessage(),
        })


ring = RingBuffer()


def setup_logging(level: str, secrets: list[str]) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    f = RedactFilter(Redactor(secrets), keep_traceback=level == "DEBUG")
    out = logging.StreamHandler(sys.stdout)
    out.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.Formatter.converter = time.localtime
    for h in (out, ring):
        h.addFilter(f)
        root.addHandler(h)
    # uvicorn 은 자기 핸들러를 따로 달기 때문에 root 로 모아 가림 필터를 거치게 한다
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
    # 외부 라이브러리의 수다스러운 로그는 경고부터
    for name in ("discord", "aiohttp.access", "sqlalchemy.engine"):
        logging.getLogger(name).setLevel(max(logging.WARNING, logging.getLevelName(level)))
