"""KTX 정차역 후보를 코레일에 실제로 조회해 확인한다. 역 목록을 바꿀 때 한 번만 돌린다(런타임에는 안 돈다).

python scripts/verify_stations.py 2026-09-30
역마다 허브 역 방향으로 KTX 계열(열차 그룹 100)만 조회해, 한 편이라도 나오면 통과. 요청 사이에 2~4초를 쉰다.
"""

import random
import sys
import time
from datetime import datetime

from pykorail import Korail, NoResultsError

CANDIDATES = [
    "서울", "용산", "영등포", "광명", "수원", "행신", "천안아산", "오송", "대전", "서대전", "김천구미", "김천", "구미",
    "대구", "서대구", "동대구", "경산", "밀양", "물금", "구포", "부산", "경주", "울산(통도사)", "포항",
    "수서", "동탄", "평택지제",
    "공주", "계룡", "논산", "익산", "김제", "정읍", "장성", "광주송정", "나주", "목포",
    "전주", "남원", "곡성", "구례구", "순천", "여천", "여수EXPO",
    "진영", "창원중앙", "창원", "마산", "중리", "함안", "군북", "진주",
    "청량리", "상봉", "덕소", "양평", "서원주", "만종", "횡성", "둔내", "평창", "진부(오대산)", "강릉",
    "원주", "제천", "단양", "풍기", "영주", "안동", "의성", "영천", "태화강", "부전",
    "정동진", "묵호", "동해", "삼척", "울진", "영덕", "북울산", "기장", "남창", "신해운대", "센텀",
    "판교(경기)", "부발", "가남", "감곡장호원", "앙성온천", "충주", "살미", "수안보온천", "연풍", "문경",
    "천안", "조치원",
]
# 지선(중부내륙선, 동해선)은 서울행이 없어서 노선 종점도 허브로 쓴다
HUBS = ["서울", "부산", "청량리", "동대구", "문경", "강릉"]


def main(day: str) -> None:
    k = Korail()
    names = k.stations.names()
    k.trains.validate_stations = False
    ok, fail = [], []
    for s in CANDIDATES:
        if s not in names:
            fail.append((s, "역 마스터에 없음"))
            continue
        found = None
        for hub in [h for h in HUBS if h != s]:
            time.sleep(random.uniform(2, 4))
            try:
                ts = k.trains.search(s, hub, depart_after=datetime.strptime(day, "%Y-%m-%d"), train_type="100", include_no_seats=True)
            except NoResultsError:
                continue
            except Exception as e:
                print(f"  {s}->{hub} 오류 {type(e).__name__}: {e}", flush=True)
                k = Korail()
                k.trains.validate_stations = False
                continue
            ktx = [t for t in ts if t.train_type_name.startswith("KTX")]
            if ktx:
                found = f"{hub} {ktx[0].train_type_name} {ktx[0].train_no}"
                break
        print(f"{'OK  ' if found else 'FAIL'} {s} {found or ''}", flush=True)
        (ok if found else fail).append(s if found else (s, "KTX 없음"))
    print("\nOK", ok)
    print("FAIL", fail)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d"))
