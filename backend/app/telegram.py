"""Telegram: 봇이 채팅에 추가·제거되는 것만 폴링으로 감지한다. 명령은 받지 않는다."""

import asyncio
import logging

import aiohttp

from app import service

log = logging.getLogger(__name__)


class TelegramPoller:
    def __init__(self, token: str) -> None:
        self.url = f"https://api.telegram.org/bot{token}/getUpdates"
        self.offset = 0
        self.ok = True

    async def run(self) -> None:
        timeout = aiohttp.ClientTimeout(total=70)
        async with aiohttp.ClientSession(timeout=timeout) as http:
            while True:
                try:
                    async with http.get(self.url, params={
                        "timeout": 50, "offset": self.offset, "allowed_updates": '["my_chat_member"]'}) as r:
                        body = await r.json(content_type=None)
                    if not body.get("ok"):
                        raise RuntimeError(f"HTTP {r.status} {body.get('description', '')}")
                    for u in body["result"]:
                        self.offset = u["update_id"] + 1
                        await self.handle(u.get("my_chat_member"))
                    if not self.ok:
                        log.info("Telegram 폴링 복구")
                    self.ok = True
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    if self.ok:
                        log.warning("Telegram 폴링 실패: %s: %s", type(e).__name__, e)
                    self.ok = False
                    await asyncio.sleep(30)

    async def handle(self, m: dict | None) -> None:
        if not m:
            return
        chat = m["chat"]
        title = chat.get("title") or chat.get("username") or chat.get("first_name") or str(chat["id"])
        status = m["new_chat_member"]["status"]
        if status in ("member", "administrator"):
            service.upsert_telegram(chat["id"], title)
        elif status in ("left", "kicked"):
            c = service.find_channel("telegram", str(chat["id"]))
            if c:
                await service.channel_gone(c.id, "bot was kicked" if status == "kicked" else "left")
