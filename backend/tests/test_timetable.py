from datetime import date, datetime

from app import timetable
from app.state import state


def row(no, sn, name, arr, dep, day="20260914"):
    return {"trn_no": no, "trn_run_sn": str(sn), "stn_nm": name, "run_ymd": day,
            "trn_arvl_dt": arr and f"2026-09-14 {arr}:00.0", "trn_dptre_dt": dep and f"2026-09-14 {dep}:00.0"}


ROWS = [
    row("00101", 1, "서울", None, "06:00"), row("00101", 2, "광명", "06:15", "06:16"),
    row("00101", 3, "울산", "08:20", "08:21"), row("00101", 4, "부산", "08:40", None),
    row("02051", 1, "용산", None, "07:00"), row("02051", 2, "청량리", "07:20", None),  # ITX-청춘: 뺀다
    row("09001", 1, "서울", None, "07:00"), row("09001", 2, "부산", "09:40", None),
]
STATIONS = {"서울", "광명", "울산(통도사)", "부산", "용산", "청량리"}


def test_build_day_keeps_our_stations_with_alias():
    day = timetable.build_day(ROWS, STATIONS)
    assert day["101"] == [["서울", None, 360], ["광명", 375, 376], ["울산(통도사)", 500, 501], ["부산", 520, None]]


def test_routes_are_forward_only():
    tt = {"days": {"0": {"date": "2026-09-14", "trains": timetable.build_day(ROWS, STATIONS)}}}
    r = timetable.routes(tt)
    assert r["서울"] == ["광명", "부산", "울산(통도사)"]
    assert "서울" not in r.get("부산", [])


def test_static_trains_uses_same_weekday_and_time_window(monkeypatch):
    monkeypatch.setattr(timetable, "KNOWN", {})
    monkeypatch.setattr(state, "timetable", {"days": {"0": {"date": "2026-09-14", "trains": timetable.build_day(ROWS, STATIONS)}}})
    mon = datetime(2026, 9, 28)  # 월요일
    got = timetable.static_trains("광명", "부산", mon.replace(hour=6), mon.replace(hour=7), "dep", mon)
    assert got == [{"date": "2026-09-28", "no": "101", "type": None, "label": "KTX", "groups": ["KTX"], "dep": "06:16", "arr": "08:40",
                    "arrDate": "2026-09-28", "minutes": 144, "general": "wait", "special": "wait"}]
    assert timetable.static_trains("부산", "광명", mon, mon.replace(hour=23), "dep", mon) == []  # 반대 방향
    assert timetable.static_trains("광명", "부산", mon.replace(hour=7), mon.replace(hour=9), "dep", mon) == []  # 시간 밖
    assert timetable.static_trains("광명", "부산", mon.replace(hour=8), mon.replace(hour=9), "arr", mon)  # 도착 기준


def test_seed_file_loads():
    timetable.load()
    assert len(state.timetable["days"]) == 7 and "부산" in state.routes["서울"]


def test_build_day_drops_itx_cheongchun_but_keeps_temporary_numbers():
    day = timetable.build_day(ROWS, STATIONS)
    assert "2051" not in day and "9001" in day  # 임시열차는 pick_fixed 가 거른다


def test_pick_fixed_keeps_regular_trains_only():
    A, B = ["서울", None, 360], ["서울", None, 400]
    weeks = [  # 최근 것부터 8주
        {"1": [B], "new": [A]},
        {"1": [A], "new": [A]},
        {"1": [A], "holiday": [A]},  # 한 번만 다닌 임시열차
        {"1": [A], "gone": [A]},
        {"1": [A], "gone": [A]},
        {"gone": [A]},  # 1번은 이 주 운휴
        {"1": [A], "gone": [A]},
        {"1": [A], "gone": [A]},
    ]
    got = timetable.pick_fixed(weeks)
    assert set(got) == {"1", "new"}  # gone: 절반 넘게 다녔지만 최근 2주에 없음(개정으로 없어짐)
    assert got["1"] == [B]  # 가장 최근 기록의 시각


def test_refresh_rebuilds_weekly_and_saves(monkeypatch, tmp_path):
    from datetime import date

    monkeypatch.setattr(timetable, "SAVED", tmp_path / "timetable.json")
    monkeypatch.setattr(state, "stations", sorted(STATIONS))
    monkeypatch.setattr(state, "timetable", {"built": "2026-09-20", "days": {}})
    fetched = []

    def fake_fetch(key, d):
        fetched.append(d)
        return [] if d == date(2026, 9, 27) else ROWS  # 어제 기록은 아직 없다

    monkeypatch.setattr(timetable, "fetch_day", fake_fetch)
    assert not timetable.refresh("k", date(2026, 9, 26))  # 만든 지 6일: 그대로
    assert fetched == []
    assert timetable.refresh("k", date(2026, 9, 28))  # 8일: 다시 만든다
    assert len(fetched) == 56 and fetched[0] == date(2026, 9, 27)
    tt = state.timetable
    assert tt["built"] == "2026-09-27" and tt["days"]["6"]["samples"] == 7 and tt["days"]["0"]["samples"] == 8
    assert set(tt["days"]["0"]["trains"]) == {"101", "9001"}  # 매주 다녔으니 남는다
    assert (tmp_path / "timetable.json").exists() and state.routes["서울"]


def test_guess_uses_sample_then_block_then_rule(monkeypatch):
    monkeypatch.setattr(timetable, "KNOWN", {101: "KTX-산천", 103: "KTX", 701: "KTX-이음", 1151: "무궁화",
                                              1001: "ITX-새마을", 1007: "ITX-마음", 1201: "새마을"})
    assert timetable.guess(101) == ("KTX-산천", ["KTX"])  # 표본
    assert timetable.guess(705) == ("KTX-이음", ["KTX"])  # 대역이 한 종류
    assert timetable.guess(105) == ("KTX", ["KTX"])  # 대역이 한 묶음(KTX, KTX-산천)
    assert timetable.guess(1003) == ("ITX", ["ITX"])
    assert timetable.guess(1203) == ("새마을", ["기타"])
    assert timetable.guess(1463) == ("무궁화", ["기타"])  # 대역에 표본 없음: 끝 두 자리 50 이상
    assert timetable.guess(1405) == ("ITX", ["ITX", "기타"])
    assert timetable.guess(9045) == ("KTX", ["KTX"])  # 임시 KTX


def test_route_paths_follow_track_and_merge_same_track(monkeypatch):
    trains = timetable.build_day(ROWS, STATIONS)
    other = [["서울", None, 400], ["부산", 560, None]]  # 정차역은 달라도 같은 선로로 간다
    monkeypatch.setattr(state, "timetable", {"days": {"0": {"trains": {**trains, "103": other}},
                                                      "1": {"trains": trains}}})
    [p] = timetable.route_paths("서울", "부산")
    assert p["track"] == ["서울", "광명", "천안아산", "오송", "대전", "김천구미", "서대구", "동대구", "경주", "울산(통도사)", "부산"]
    assert p["stops"] == ["광명", "부산", "서울", "울산(통도사)"]
    assert p["weekly"] == {"KTX": 5}  # 101·9001 이틀씩 + 103. 서울→부산 무정차(103, 9001)도 고속선으로 한 경로
    assert timetable.route_paths("부산", "서울") == []  # 반대 방향 열차 없음


def test_station_rank_compares_ktx_then_itx_then_others(monkeypatch):
    monkeypatch.setattr(timetable, "KNOWN", {})  # 번호 규칙으로: 101 KTX, 1001 ITX, 1151·1153 무궁화(기타)
    monkeypatch.setattr(state, "stations", ["A", "B", "C", "D"])
    stop = lambda *names: [[n, 0, 0] for n in names]  # noqa: E731
    monkeypatch.setattr(state, "timetable", {"days": {"0": {"trains": {
        "101": stop("A", "B"), "1001": stop("C", "B"), "1151": stop("C", "D"), "1153": stop("D", "A")}}}})
    # B: KTX1 ITX1, A: KTX1 기타1, C: ITX1 기타1, D: 기타2
    assert timetable.station_rank() == ["B", "A", "C", "D"]


def test_build_day_pads_numbers_like_korail():
    rows = [row("00014", 1, "서울", None, "06:00"), row("00014", 2, "부산", "08:40", None)]
    assert list(timetable.build_day(rows, STATIONS)) == ["014"]  # 코레일 조회 결과의 열차번호와 같아야 합친다


def test_train_run_finds_whole_train_and_previous_day_after_midnight(monkeypatch):
    late = [["서울", None, 1430], ["광명", 1445, 1446], ["부산", 1600, None]]  # 월요일 23:50 출발, 화요일 00:06 광명
    monkeypatch.setattr(state, "timetable", {"days": {"0": {"trains": {**timetable.build_day(ROWS, STATIONS),
                                                                        "105": late}}}})
    run = timetable.train_run("101", date(2026, 9, 14), "광명", "06:16")
    assert [s["name"] for s in run["stops"]] == ["서울", "광명", "울산(통도사)", "부산"]
    assert run["stops"][0] == {"name": "서울", "arr": None, "dep": "06:00"}
    assert run["track"][:3] == ["서울", "광명", "천안아산"] and run["track"][-1] == "부산"
    assert timetable.train_run("105", date(2026, 9, 15), "광명", "00:06")["stops"][1]["dep"] == "00:06"
    assert timetable.train_run("101", date(2026, 9, 14), "대전", "07:00") is None  # 대전에 서지 않음
    assert timetable.train_run("999", date(2026, 9, 14), "서울", "06:00") is None
