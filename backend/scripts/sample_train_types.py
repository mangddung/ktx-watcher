"""시간표 화면에서 종류를 짐작하는 데 쓰는 열차번호 → 종류 표본(app/data/train_types.json)을 만든다. 런타임에는 안 돈다.

python scripts/sample_train_types.py 2026-09-29
노선별 하루치를 코레일에 조회한다(약 35번 요청, 요청 사이 3~6초). 차단 징후가 보이면 멈추고 받은 데까지 저장한다.
"""

import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.scanner import Gate, is_block_error, scan_pages  # noqa: E402

# 번호 대역마다 열차가 한 편 이상 걸리도록 고른 노선(경부, 호남, 장항, 전라, 중앙, 충북, 동해, 경전, 강릉)
ROUTES = [("서울", "부산"), ("용산", "목포"), ("용산", "익산"), ("용산", "여수EXPO"), ("청량리", "부전"),
          ("대전", "제천"), ("부전", "동해"), ("동대구", "진주"), ("서울", "강릉"), ("익산", "목포")]

day = date.fromisoformat(sys.argv[1])
gate = Gate((3, 6))
seen: dict[str, str] = {}
for dep, arr in ROUTES:
    try:
        for page in scan_pages(gate, dep, arr, day, datetime.combine(day, datetime.min.time()),
                               datetime.combine(day, datetime.max.time()), 1):
            seen.update({t.no: t.type for t in page})
        print(f"{dep}→{arr}: 누적 {len(seen)}편")
    except Exception as e:
        print(f"{dep}→{arr}: 실패 {type(e).__name__}")
        if is_block_error(e):
            print("차단 징후로 멈춥니다")
            break
out = Path(__file__).resolve().parent.parent / "app" / "data" / "train_types.json"
out.write_text(json.dumps({n: seen[n] for n in sorted(seen, key=int)}, ensure_ascii=False, separators=(",", ":")),
               encoding="utf-8")
print(f"{out} {len(seen)}편")
