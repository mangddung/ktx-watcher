"""앱에 넣을 초기 시간표(app/data/timetable.json)를 만든다. 공공데이터포털 키가 필요하다(런타임에는 안 돈다).

DATA_GO_KR_KEY=... python scripts/build_timetable.py [마지막날 YYYY-MM-DD, 기본 어제]
마지막날부터 8주 기록을 받아(약 620번 호출, 2~3분) 요일마다 평소에 다니는 열차만 남긴다(timetable.pick_fixed).
"""

import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import timetable  # noqa: E402
from app.stations import KTX_STATIONS  # noqa: E402

last = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today() - timedelta(days=1)
tt = timetable.build(os.environ["DATA_GO_KR_KEY"].strip(), last, set(KTX_STATIONS))
for wd, d in tt["days"].items():
    print(f"{'월화수목금토일'[int(wd)]} {d['from']}~{d['to']} {d['samples']}일 → 열차 {len(d['trains'])}편")
timetable.save(tt, timetable.SEED)
print(f"{timetable.SEED} {timetable.SEED.stat().st_size // 1024}KB")
