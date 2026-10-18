# -*- coding: utf-8 -*-
"""교통량 시나리오 입력 생성 — 「전농동_교통량_산정근거.docx」(2026-10-07) 값 적용. 기본값·기준선과 무관한 비교용.
  A_diurnal : 기준선 DTV 그대로, 주·야 계수만 서울 28지점 평균(주간 0.0518, 야간 0.0215)으로 → M_day·M_night 직접 입력
  B_doc     : 문서의 DTV(차로당 6,100 대/일 × 대표 차로수) + 같은 주·야 계수
              전농로 30,500 / 사가정로 4차로 구간 24,400, 6차로 이상 객체 36,600(UFID 행)
              답십리로·서울시립대로는 문서에 없음 → 같은 방법으로 확장(6,100 × 5 = 30,500) — 확장은 가정
사용: python make_traffic.py <기준선 traffic_used.csv> <출력 폴더>
※ Claude(Anthropic의 AI)를 활용하여 제작하였음."""
import os
import sys

import pandas as pd

K_DAY, K_NIGHT = 0.0518, 0.0215          # 문서 4절, 서울 28지점 2026-08 평균
COLS = "key,key_type,road_type,DTV,M_day,M_night,p1_day,p2_day,p1_night,p2_night,v_pkw,v_lkw,surface,grade,note,basis".split(",")


def row(key, kt, dtv, note):
    r = dict.fromkeys(COLS, ""); r.update(key=key, key_type=kt, road_type="gemeinde", v_pkw="50", v_lkw="50",
                                          M_day=f"{dtv * K_DAY:.1f}", M_night=f"{dtv * K_NIGHT:.1f}", note=note, basis="assumed")
    return r


def main(tu, out):
    T = pd.read_csv(tu)
    base = {"사가정로": 45000, "전농로": 45000, "답십리로": 35000, "서울시립대로": 30000}
    A = [row(k, "name", v, f"기준선 DTV {v:,} 가정, 주·야 계수 서울 28지점(산정근거 문서 4절)") for k, v in base.items()]
    doc = {"전농로": (30500, "문서 3.4: 6,100 × 5"), "사가정로": (24400, "문서 3.4: 4차로 구간 6,100 × 4"),
           "답십리로": (30500, "문서 방법 확장(가정): 6,100 × 5"), "서울시립대로": (30500, "문서 방법 확장(가정): 6,100 × 5")}
    B = [row(k, "name", v, n) for k, (v, n) in doc.items()]
    six = T[(T.traffic_src == "user") & (T.axis == "사가정로") & (T.lanes >= 6)]
    B += [row(u, "ufid", 36600, "문서 3.4: 사가정로 6차로 구간 6,100 × 6") for u in six.ufid]
    for name, rows in (("A_diurnal", A), ("B_doc", B)):
        os.makedirs(os.path.join(out, name), exist_ok=True)
        pd.DataFrame(rows, columns=COLS).to_csv(os.path.join(out, name, "traffic.csv"), index=False, encoding="utf-8-sig")
    print(f"A {len(A)}행, B {len(B)}행(사가정로 6차로 이상 UFID {len(six)}개)")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
