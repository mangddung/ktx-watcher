from app.stations import KTX_STATIONS, STATION_MAP


def test_station_map_data_is_consistent():
    names = [s["name"] for s in STATION_MAP["stations"]]
    assert names == KTX_STATIONS and len(set(names)) == len(names) == 88
    for s in STATION_MAP["stations"]:
        assert 33 < s["lat"] < 39 and 124 < s["lon"] < 132, s
        assert s["label"] in ("r", "l", "t", "b"), s
    for line in STATION_MAP["lines"]:
        assert len(line) >= 2 and set(line) <= set(names), line
    assert {n for line in STATION_MAP["lines"] for n in line} == set(names)  # 선로에 안 이어진 역이 없다
    assert STATION_MAP["provinces"].startswith("M") and STATION_MAP["metros"].startswith("M")
