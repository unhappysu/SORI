# -*- coding: utf-8 -*-
"""
SoundPLAN 계산 조건(SoundPLAN_계산조건_정리.pdf, 2026-11-17)에 맞춘 SORI 결과 ↔ SoundPLAN 비교. 2026-11-19
  실행: .venv/bin/python -I validation/soundplan/sp_compare_1109/sp_settings_1117/compare_sp_settings.py
  입력: ../soundplan/values.csv(SoundPLAN LrD), 각 조건 output/point_result.csv, 이전 조건 ../sagajeong_only/r2_ref
  출력: 비교통계.csv, 비교통계_구간.csv, 조건간_차이.csv, 비교_matched.png
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(HERE, "..", "sagajeong_only"))

import geopandas as gpd                      # noqa: E402
from compare import BINS, figure, load, stats  # noqa: E402  (sagajeong_only/compare.py 재사용)

CONDS = [  # (폴더, 설명)
    (os.path.join(HERE, "..", "sagajeong_only", "r2_ref"), "이전(11-12): D_K·Drefl 켬, 탐색 300 m, 분할 3 m, 지형 평활"),
    (os.path.join(HERE, "sp_refl"), "SP 조건 일치 + SP 반사 규칙(음원 50 m)"),
    (os.path.join(HERE, "matched"), "SP 조건 일치 · 반사면 200 m 근사"),
    (os.path.join(HERE, "r_default"), "SP 조건 일치 · 반사 거리만 SORI 기본"),
    (os.path.join(HERE, "terrain_old"), "SP 조건 일치 · 지형 평활만 이전대로"),
]


def main():
    rows, rows_bin, D = [], [], {}
    for folder, desc in CONDS:
        if not os.path.exists(os.path.join(folder, "output", "point_result.csv")):
            print("결과 없음:", folder); continue
        d = load(folder)
        D[desc] = d.set_index("point_id")["LrT"]
        s = stats(d); P = d.set_index("point_id")
        s.update({"조건": desc, "P1": P.loc["SP_P1", "diff"], "P2": P.loc["SP_P2", "diff"],
                  "P1 SORI": P.loc["SP_P1", "LrT"], "P2 SORI": P.loc["SP_P2", "LrT"],
                  "서쪽 x<205160 평균": d[d.x < 205160]["diff"].mean()})
        rows.append(s)
        g = d[d.point_id.str.startswith("G")]
        for a, b, lab in BINS:
            q = g[(g.LrD >= a) & (g.LrD < b)]
            if len(q):
                t = stats(q); t.update({"조건": desc, "SoundPLAN LrD": lab}); rows_bin.append(t)
        if os.path.basename(folder) in ("matched", "sp_refl"):
            o = os.path.join(folder, "output")
            figure(d, "SoundPLAN 계산 조건 일치(RLS-19 · 반사 2차 · D_K·Drefl 없음 · 기준 노면 · 사가정로만)"
                   + (" + 반사면–음원 50 m" if os.path.basename(folder) == "sp_refl" else ""),
                   os.path.join(HERE, f"비교_{os.path.basename(folder)}.png"),
                   gpd.read_file(os.path.join(o, "buildings.gpkg")), gpd.read_file(os.path.join(o, "roads.gpkg")))
    cols = ["조건", "n", "평균", "중앙", "SD", "RMSE", "MAE", "±1dB", "±2dB", "±3dB", "r", "최소", "최대",
            "P1 SORI", "P1", "P2 SORI", "P2", "서쪽 x<205160 평균"]
    S = pd.DataFrame(rows)[cols].round(2)
    S.to_csv(os.path.join(HERE, "비교통계.csv"), index=False, encoding="utf-8-sig")
    B = pd.DataFrame(rows_bin)[["조건", "SoundPLAN LrD", "n", "평균", "SD", "RMSE", "±2dB", "r"]].round(2)
    B.to_csv(os.path.join(HERE, "비교통계_구간.csv"), index=False, encoding="utf-8-sig")
    # 조건 사이 차이(같은 점)
    base = "SP 조건 일치 + SP 반사 규칙(음원 50 m)"
    rr = []
    for k, v in D.items():
        if k == base or base not in D:
            continue
        e = (D[base] - v).dropna()
        rr.append({"비교": f"SP 반사 규칙 − [{k}]", "n": len(e), "평균": e.mean(), "|차| 95%": e.abs().quantile(0.95),
                   "최소": e.min(), "최대": e.max(), "P1": e.get("SP_P1"), "P2": e.get("SP_P2")})
    R = pd.DataFrame(rr).round(2)
    R.to_csv(os.path.join(HERE, "조건간_차이.csv"), index=False, encoding="utf-8-sig")
    with pd.option_context("display.width", 250, "display.max_columns", 30):
        print(S.to_string(index=False)); print(B.to_string(index=False)); print(R.to_string(index=False))


if __name__ == "__main__":
    main()


# ---- 구역별(차이 지도에서 눈에 띄는 곳). 2026-11-19 추가
ZONES = [("남측 큰 건물 뒤", 205200, 205262, 553060, 553092),
         ("남동 건물 사이 틈", 205285, 205305, 553090, 553120),
         ("북측 건물 뒤(y ≥ 553155, x 205160–205205)", 205160, 205205, 553155, 553190),
         ("서쪽 x<205160", 205100, 205160, 553060, 553190),
         ("도로변 사가정로 영향권(그 외)", None, None, None, None)]


def zones():
    rows = []
    for folder, desc in CONDS:
        if not os.path.exists(os.path.join(folder, "output", "point_result.csv")):
            continue
        d = load(folder); d = d[d.point_id.str.startswith("G")]
        used = np.zeros(len(d), bool)
        for name, x0, x1, y0, y1 in ZONES:
            m = (~used) if x0 is None else ((d.x >= x0) & (d.x <= x1) & (d.y >= y0) & (d.y <= y1)).values
            used |= m
            q = d[m]
            rows.append({"조건": desc, "구역": name, "n": len(q), "평균": q["diff"].mean(), "SD": q["diff"].std(),
                         "최소": q["diff"].min(), "최대": q["diff"].max(), "±2dB": (q["diff"].abs() <= 2).mean() * 100})
    Z = pd.DataFrame(rows).round(2)
    Z.to_csv(os.path.join(HERE, "비교통계_구역.csv"), index=False, encoding="utf-8-sig")
    with pd.option_context("display.width", 250):
        print(Z.to_string(index=False))


if __name__ == "__main__":
    zones()
