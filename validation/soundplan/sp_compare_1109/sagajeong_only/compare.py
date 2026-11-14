# -*- coding: utf-8 -*-
"""
SoundPLAN(11-09 받음) ↔ SORI 비교 — 사가정로만 음원(그 밖의 도로 교통량 0) 조건. 2026-11-12
  실행: .venv/bin/python validation/soundplan/sp_compare_1109/sagajeong_only/compare.py
  입력: ../soundplan/values.csv(SoundPLAN LrD), 각 조건 폴더 output/point_result.csv,
        비교용 이전 결과(모든 도로 음원) ../rls19_dir_lane_r2_ref, _r3_ref, _r3
  출력: 비교_<조건>.png(산점도 + 차이 지도), 비교통계.csv, 비교통계_구간.csv
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
sys.path.insert(0, ROOT)

import geopandas as gpd                      # noqa: E402
import matplotlib                            # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt              # noqa: E402
from matplotlib.colors import TwoSlopeNorm   # noqa: E402
from sori.maps_hq import set_font            # noqa: E402

UP = os.path.join(HERE, "..")
CONDS = [  # (이름, 새 결과 폴더, 이전 결과 폴더(모든 도로), 설명)
    ("r2_ref", "r2_ref", "rls19_dir_lane_r2_ref", "반사 2차 · 기준 노면"),
    ("r3_ref", "r3_ref", "rls19_dir_lane_r3_ref", "반사 3차 · 기준 노면"),
    ("r3_ac11", "r3_ac11", "rls19_dir_lane_r3", "반사 3차 · 노면 AC11"),
]
BINS = [(75, 99, "≥ 75 (도로변)"), (70, 75, "70–75"), (65, 70, "65–70"), (60, 65, "60–65"), (0, 60, "< 60")]


def load(folder):
    sp = pd.read_csv(os.path.join(UP, "soundplan", "values.csv"), encoding="utf-8-sig")
    so = pd.read_csv(os.path.join(folder, "output", "point_result.csv"), encoding="utf-8-sig")
    d = sp.merge(so[["point_id", "x", "y", "LrT"]], on="point_id", how="inner")
    d = d[np.isfinite(d.LrT)].copy()
    d["diff"] = d.LrT - d.LrD
    return d


def stats(d):
    e = d["diff"]
    r = np.corrcoef(d.LrD, d.LrT)[0, 1] if len(d) > 2 else np.nan
    return {"n": len(d), "평균": e.mean(), "중앙": e.median(), "SD": e.std(ddof=1), "RMSE": np.sqrt((e ** 2).mean()),
            "MAE": e.abs().mean(), "±1dB": (e.abs() <= 1).mean() * 100, "±2dB": (e.abs() <= 2).mean() * 100,
            "±3dB": (e.abs() <= 3).mean() * 100, "r": r, "최소": e.min(), "최대": e.max()}


def figure(d, title, png, bld, roads):
    set_font()
    fig = plt.figure(figsize=(18, 7.6))
    ax = fig.add_axes([0.05, 0.09, 0.33, 0.8])
    g = d[d.point_id.str.startswith("G")]
    p = d[d.point_id.str.startswith("SP_")]
    ax.scatter(g.LrD, g.LrT, s=12, alpha=0.7, color="#3b75b4")
    lo, hi = 45, 90
    ax.plot([lo, hi], [lo, hi], "k-", lw=1)
    for o in (-2, 2):
        ax.plot([lo, hi], [lo + o, hi + o], "k:", lw=0.8)
    for _, q in p.iterrows():
        ax.scatter(q.LrD, q.LrT, s=110, color="#d62728", zorder=5)
        ax.annotate(f"{q.point_id[3:]} {q['diff']:+.2f}", (q.LrD, q.LrT), xytext=(8, -12), textcoords="offset points",
                    fontsize=11)
    ax.set_xlim(lo, hi); ax.set_ylim(lo, hi); ax.set_aspect("equal"); ax.grid(alpha=0.3)
    ax.set_xlabel("SoundPLAN LrD [dB(A)]", fontsize=12); ax.set_ylabel("SORI LrT [dB(A)]", fontsize=12)
    s = stats(d)
    ax.set_title(f"{title}\nn={s['n']}, 평균 {s['평균']:+.2f}, SD {s['SD']:.2f}, RMSE {s['RMSE']:.2f} dB, "
                 f"±2 dB 안 {s['±2dB']:.0f} %", fontsize=11.5)
    ax2 = fig.add_axes([0.44, 0.09, 0.47, 0.8])
    x0, x1, y0, y1 = 205115, 205310, 553060, 553185
    bld.cx[x0:x1, y0:y1].plot(ax=ax2, fc="#e6e6e6", ec="#555", lw=0.5, zorder=1)
    roads.cx[x0:x1, y0:y1].plot(ax=ax2, color="#999", lw=0.8, zorder=2)
    sc = ax2.scatter(g.x, g.y, c=g["diff"], cmap="RdBu_r", norm=TwoSlopeNorm(0, -8, 8), s=46, marker="s", zorder=3)
    gx = pd.read_csv(os.path.join(UP, "points_sp.csv"), encoding="utf-8-sig")
    miss = gx[gx.point_id.str.startswith("G") & ~gx.point_id.isin(g.point_id)]
    ax2.scatter(miss.x, miss.y, marker="x", s=14, color="k", lw=1, zorder=4, label="SORI 건물 안(값 없음)")
    ax2.scatter(p.x, p.y, s=120, facecolors="none", edgecolors="#d00", lw=2, zorder=5)
    ax2.set_xlim(x0, x1); ax2.set_ylim(y0, y1); ax2.set_aspect("equal")
    ax2.set_xlabel("Easting [m]"); ax2.set_ylabel("Northing [m]")
    ax2.set_title("차이 지도(빨강 = SORI가 높음)", fontsize=13)
    ax2.legend(loc="lower left", fontsize=9)
    cb = fig.colorbar(sc, ax=ax2, fraction=0.035, pad=0.02)
    cb.set_label("SORI − SoundPLAN [dB]", fontsize=11)
    fig.text(0.99, 0.01, "※ 본 그림은 Claude(Anthropic의 AI)를 활용하여 작성하였음.", ha="right", fontsize=8, color="0.4")
    fig.savefig(png, dpi=150)
    plt.close(fig)


def main():
    rows, rows_bin = [], []
    for name, new, old, desc in CONDS:
        for tag, folder in (("사가정로만", os.path.join(HERE, new)), ("모든 도로(11-11)", os.path.join(UP, old))):
            if not os.path.exists(os.path.join(folder, "output", "point_result.csv")):
                print("결과 없음:", folder); continue
            d = load(folder)
            s = stats(d)
            P = d.set_index("point_id")
            s.update({"조건": desc, "음원": tag,
                      "P1": P.loc["SP_P1", "diff"] if "SP_P1" in P.index else np.nan,
                      "P2": P.loc["SP_P2", "diff"] if "SP_P2" in P.index else np.nan,
                      "서쪽 x<205160 평균": d[d.x < 205160]["diff"].mean(),
                      "서쪽 n": int((d.x < 205160).sum())})
            rows.append(s)
            g = d[d.point_id.str.startswith("G")]
            for a, b, lab in BINS:
                q = g[(g.LrD >= a) & (g.LrD < b)]
                if len(q):
                    t = stats(q); t.update({"조건": desc, "음원": tag, "SoundPLAN LrD": lab}); rows_bin.append(t)
            if tag == "사가정로만":
                o = os.path.join(folder, "output")
                bld = gpd.read_file(os.path.join(o, "buildings.gpkg"))
                roads = gpd.read_file(os.path.join(o, "roads.gpkg"))
                figure(d, f"RLS-19 · 서 30/동 50 km/h · 3+2차로 차로별 · {desc} · 사가정로만 음원",
                       os.path.join(HERE, f"비교_{name}.png"), bld, roads)
    S = pd.DataFrame(rows)
    cols = ["조건", "음원", "n", "평균", "중앙", "SD", "RMSE", "MAE", "±1dB", "±2dB", "±3dB", "r", "최소", "최대",
            "P1", "P2", "서쪽 n", "서쪽 x<205160 평균"]
    S[cols].round(2).to_csv(os.path.join(HERE, "비교통계.csv"), index=False, encoding="utf-8-sig")
    B = pd.DataFrame(rows_bin)
    B[["조건", "음원", "SoundPLAN LrD", "n", "평균", "SD", "RMSE", "±2dB", "r"]].round(2).to_csv(
        os.path.join(HERE, "비교통계_구간.csv"), index=False, encoding="utf-8-sig")
    with pd.option_context("display.width", 250, "display.max_columns", 30):
        print(S[cols].round(2).to_string(index=False))
        print(B[["조건", "음원", "SoundPLAN LrD", "n", "평균", "SD", "RMSE", "±2dB", "r"]].round(2).to_string(index=False))


if __name__ == "__main__":
    main()
