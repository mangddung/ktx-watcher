import asyncio
import json

import pytest

from app.models import Channel
from app.notify import Notifier, SendFailed


class HtmlErrorPage:
    """JSON 이 아닌 오류 페이지(Cloudflare 502 등)를 주는 응답."""

    status = 502

    async def json(self, content_type=None):
        raise json.JSONDecodeError("Expecting value", "<html>Bad gateway</html>", 0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class Http:
    def request(self, method, url, **kw):
        return HtmlErrorPage()


def test_non_json_error_page_is_send_failed():
    n = Notifier()
    n.http = Http()
    ch = Channel(kind="discord_webhook", name="hook", target="https://discord.com/api/webhooks/1/t")
    with pytest.raises(SendFailed, match="HTTP 502"):
        asyncio.run(n.send(ch, "테스트"))


class Ok:
    status = 200

    async def json(self, content_type=None):
        return {"id": "9"}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def test_discord_messages_never_ping():
    """알림에는 코레일·노드 오류 문구가 그대로 들어간다. @everyone 이 있어도 울리지 않아야 한다."""
    sent = []

    class Capture:
        def request(self, method, url, **kw):
            sent.append(kw["json"])
            return Ok()

    n = Notifier(bot_token="bot")
    n.http = Capture()
    for ch in (Channel(kind="discord_webhook", name="hook", target="https://discord.com/api/webhooks/1/t"),
               Channel(kind="discord_bot", name="bot", target="123")):
        asyncio.run(n.send(ch, "@everyone 조회 오류"))
    assert [j["allowed_mentions"] for j in sent] == [{"parse": []}] * 2
