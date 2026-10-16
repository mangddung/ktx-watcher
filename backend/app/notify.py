"""알림 채널 3종(Discord 웹훅, Discord 봇 채널, Telegram)으로 보낸다. 모두 REST 로 직접 호출한다."""

import asyncio

import aiohttp

from app.models import Channel

DISCORD_API = "https://discord.com/api/v10"
# 알림 문구에는 코레일·노드 오류 문구가 들어간다. @everyone·역할·사용자 멘션이 실제로 울리지 않게 한다
NO_MENTIONS = {"parse": []}


def text_limit(kind: str) -> int:
    """메시지 한 개 최대 글자 수(Discord 2000, Telegram 4096)에서 여유를 뺀 값."""
    return 4000 if kind == "telegram" else 1990


def split(text: str, limit: int) -> list[str]:
    """줄 단위로 limit 이하 조각으로 나눈다. 잘라 버리면 뒤쪽 열차 알림이 사라진다. 한 줄이 limit 보다 길면 그 줄만 자른다."""
    out, cur = [], ""
    for line in text.split("\n"):
        line = line[:limit]
        if cur and len(cur) + 1 + len(line) > limit:
            out.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    out.append(cur)
    return out


WEBHOOK_PREFIXES = ("https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/")


class ChannelGone(Exception):
    """채널이 삭제됐거나 봇이 쫓겨났다. 이 채널을 쓰는 감시는 일시정지한다."""


class SendFailed(Exception):
    """일시적 오류(5xx, 타임아웃 등). 감시는 그대로 둔다."""


class Notifier:
    def __init__(self, bot_token: str = "", telegram_token: str = "") -> None:
        self.bot_token = bot_token
        self.telegram_token = telegram_token
        self.http: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        self.http = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15), headers={"User-Agent": "DiscordBot (ktx-watch, 1.0)"})

    async def close(self) -> None:
        if self.http:
            await self.http.close()

    async def _request(self, method: str, url: str, **kw) -> tuple[int, dict]:
        assert self.http
        for attempt in range(2):
            try:
                async with self.http.request(method, url, **kw) as r:
                    try:
                        body = await r.json(content_type=None) if r.status != 204 else {}
                    except ValueError:  # JSON 이 아닌 본문(Cloudflare 오류 페이지 등)은 상태 코드로만 판단한다
                        body = {}
                    if r.status == 429 and attempt == 0:
                        await asyncio.sleep(float((body or {}).get("retry_after", 1)))
                        continue
                    return r.status, body or {}
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                raise SendFailed(f"{type(e).__name__}: {e}") from e
        return 429, {}

    def _bot_headers(self) -> dict:
        return {"Authorization": f"Bot {self.bot_token}"}

    async def send(self, ch: Channel, text: str) -> str:
        """메시지를 보내고 메시지 ID 를 돌려준다."""
        text = text[:text_limit(ch.kind)]  # 긴 글은 부르는 쪽에서 split 으로 나눠 보낸다
        if ch.kind == "discord_webhook":
            status, body = await self._request(
                "POST", ch.target + "?wait=true", json={"content": text, "allowed_mentions": NO_MENTIONS})
            if status == 404:
                raise ChannelGone("Unknown Webhook")
        elif ch.kind == "discord_bot":
            if not self.bot_token:
                raise SendFailed("DISCORD_BOT_TOKEN 이 없습니다")
            status, body = await self._request(
                "POST", f"{DISCORD_API}/channels/{ch.target}/messages", json={"content": text, "allowed_mentions": NO_MENTIONS},
                headers=self._bot_headers())
            if status in (403, 404):
                raise ChannelGone("Missing Access" if status == 403 else "Unknown Channel")
        elif ch.kind == "telegram":
            if not self.telegram_token:
                raise SendFailed("TELEGRAM_BOT_TOKEN 이 없습니다")
            status, body = await self._request(
                "POST", f"https://api.telegram.org/bot{self.telegram_token}/sendMessage",
                json={"chat_id": ch.target, "text": text})
            desc = str(body.get("description", ""))
            if status in (400, 403) and any(k in desc for k in ("chat not found", "kicked", "not a member", "deactivated")):
                raise ChannelGone(desc)
            if status == 200:
                return str(body["result"]["message_id"])
        else:
            raise SendFailed(f"알 수 없는 채널 종류 {ch.kind}")
        if status >= 300:
            raise SendFailed(f"HTTP {status} {str(body)[:200]}")
        return str(body.get("id", ""))
