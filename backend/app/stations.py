"""고정 데이터: KTX 정차역과 지도, 열차 종류 묶음, 좌석 등급.

역 목록과 지도용 위치·선로는 data/stations.json, 지도 경계선은 data/boundaries.json 에 둔다. 역을 더할 때는 stations.json 만 고친다.
역 목록은 scripts/verify_stations.py 로 코레일에 실제 조회해 KTX 계열이 한 편이라도 서는지 확인한 결과다(2026-09-25, 85개 역).
서대구·덕소·남창은 후보에 없어 빠졌던 것을 공공데이터 운행정보(timetable.py)로 찾아 더했다(2026-09-26, 88개 역).
김천·구미·대구·천안·조치원·중리·함안·군북은 KTX 계열이 조회되지 않아 뺐다.
앱이 시작할 때 역 마스터와 다시 대조해 없는 역은 뺀다.
"""

import json
from pathlib import Path

_HERE = Path(__file__).parent / "data"
_MAP = json.loads((_HERE / "stations.json").read_text(encoding="utf-8"))
_BOUNDARIES = json.loads((_HERE / "boundaries.json").read_text(encoding="utf-8"))

KTX_STATIONS: list[str] = [s["name"] for s in _MAP["stations"]]
# 웹 지도용: 역 위치, 선로, 경계선
STATION_MAP: dict = {
    "stations": _MAP["stations"], "lines": _MAP["lines"],
    "provinces": _BOUNDARIES["provinces"], "metros": _BOUNDARIES["metros"],
}

TRAIN_GROUPS: list[dict] = [
    {"name": "KTX", "types": ["KTX", "KTX-산천", "KTX-이음", "KTX-청룡"]},
    {"name": "ITX", "types": ["ITX-새마을", "ITX-마음"]},
    {"name": "기타", "types": ["새마을", "무궁화"]},
]
TRAIN_TYPES = [t for g in TRAIN_GROUPS for t in g["types"]]
SEAT_CLASSES = ["일반실", "특실"]


def group_types(names: list[str]) -> list[str]:
    """묶음 이름(KTX, ITX, 기타) 목록을 세부 열차 종류 목록으로 바꾼다. Discord 봇 모달에서 쓴다."""
    return [t for g in TRAIN_GROUPS if g["name"] in names for t in g["types"]]
