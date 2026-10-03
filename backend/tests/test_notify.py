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
