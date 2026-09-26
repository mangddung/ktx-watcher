import io
import logging

from app.logs import Redactor, RedactFilter, RingBuffer

WEBHOOK = "https://discord.com/api/webhooks/123456789012345678/AbCdEf-ghIJkl_mnOPqrs"
BOT_TOKEN = "MTIzNDU2Nzg5MDEyMzQ1Njc4OQ.GaBcDe.abcdefghijklmnopqrstuvwxyz0123456789ab"
TG_TOKEN = "1234567890:AAEabcdefghijklmnopqrstuvwxyz012345"
SECRET = "super-secret-session-value-1234567890"


def make_logger():
    lg = logging.getLogger("test.redact")
    lg.handlers.clear()
    lg.propagate = False
    lg.setLevel(logging.DEBUG)
    out = io.StringIO()
    sh = logging.StreamHandler(out)
    ring = RingBuffer()
    f = RedactFilter(Redactor([SECRET]), keep_traceback=False)
    for h in (sh, ring):
        h.addFilter(f)
        lg.addHandler(h)
    return lg, out, ring


def test_sensitive_values_never_reach_stdout_or_ring():
    lg, out, ring = make_logger()
    lg.info("webhook POST %s 204", WEBHOOK)
    lg.info("token %s and tg https://api.telegram.org/bot%s/sendMessage", BOT_TOKEN, TG_TOKEN)
    lg.info("chat_id=-1001234567890 user=123456789012345678")
    lg.info('"GET /auth/callback?code=abcDEF123&state=zzzYYY HTTP/1.1" 302')
    lg.info("Authorization: Bot %s", BOT_TOKEN)
    lg.info("secret=%s", SECRET)
    text = out.getvalue() + str(list(ring.lines))
    for raw in ("123456789012345678/AbCdEf", BOT_TOKEN, TG_TOKEN, "1001234567890", "abcDEF123", "zzzYYY", SECRET):
        assert raw not in text, raw
    assert "****7890" in text  # chat_id 는 끝 4자리만
    assert "/webhooks/****/****" in text


def test_exception_logs_type_and_message_only():
    lg, out, _ = make_logger()
    try:
        raise ValueError(f"bad {WEBHOOK}")
    except ValueError:
        lg.exception("실패")
    text = out.getvalue()
    assert "Traceback" not in text
    assert "ValueError" in text and "AbCdEf" not in text
