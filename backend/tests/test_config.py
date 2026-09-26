from app.config import Settings, parse_admin_ids, startup_problems


def test_more_than_five_admins_keeps_first_five():
    ids, errors = parse_admin_ids("1,2,3,4,5,6,7")
    assert ids == (1, 2, 3, 4, 5)
    assert any("앞 5명만 적용" in e and "6, 7" in e for e in errors)


def test_invalid_and_duplicate_ids_are_skipped():
    ids, errors = parse_admin_ids(" 12, abc, 12 ,34,")
    assert ids == (12, 34)
    assert len(errors) == 2


def test_no_valid_admin_stops_startup():
    ids, _ = parse_admin_ids("abc")
    s = Settings(bot_token="", client_id="c", client_secret="s", guild_id=None, admin_ids=ids, session_secret="x" * 40)
    assert any("DISCORD_ADMIN_IDS" in p for p in startup_problems(s))


def test_guild_id_must_be_a_discord_snowflake():
    from app.config import parse_snowflake

    assert parse_snowflake("G", "123456789012345678") == (123456789012345678, [])
    assert parse_snowflake("G", "") == (None, [])
    for bad in ("99999999999999999999", "12345", "abc"):
        gid, errors = parse_snowflake("G", bad)
        assert gid is None and errors
