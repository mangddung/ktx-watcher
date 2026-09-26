from datetime import datetime

import pytest

from app.bot import at_time


@pytest.mark.parametrize("text,hm", [("07:30", (7, 30)), ("0730", (7, 30)), ("730", (7, 30)), ("7", (7, 0)),
                                     (" 23:59 ", (23, 59)), ("0000", (0, 0))])
def test_at_time_ok(text, hm):
    assert at_time("2026-09-28", text) == datetime(2026, 9, 28, *hm)


@pytest.mark.parametrize("text", ["", "24:00", "0760", "7:5x", "12345", "ab"])
def test_at_time_bad(text):
    with pytest.raises(ValueError):
        at_time("2026-09-28", text)
