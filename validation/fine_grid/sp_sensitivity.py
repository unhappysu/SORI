# -*- coding: utf-8 -*-
"""
SoundPLAN 비교(사가정로만 음원, 반사 3차·기준 노면 — sp_compare_1109/sagajeong_only/r3_ref)에서 SORI 와 크게 어긋나는
점이 '위치를 조금만 옮겨도 값이 크게 바뀌는 곳'(건물 경계·그림자 경계)에 몰려 있는지 확인. 2026-11-15
  - SoundPLAN 격자점마다 SORI 를 1.25·2.5 m 옮긴 8점(동서남북)에서 다시 계산 → 그 점 둘레 값 범위(위치 민감도)
  - SORI 건물까지 거리, 위치 민감도별로 SORI − SoundPLAN 차이 통계
  실행: .venv/bin/python validation/fine_grid/sp_sensitivity.py   (계산 약 1–2분, 엔진 변경 없음)
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
from sori import config as C, run as RUN  # noqa: E402

COND = ROOT / "validation/soundplan/sp_compare_1109/sagajeong_only/r3_ref"
SP = ROOT / "validation/soundplan/sp_compare_1109/soundplan/values.csv"
OFFS = [(dx * r, dy * r) for r in (1.25, 2.5) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))]


def main():
    import geopandas as gpd
    from shapely.geometry import Point
    cfg = C.load(str(COND / "config.yaml"))
    out = cfg["project"]["output_dir"]
    d, S, F, _ = RUN.load_model(out)
    sp = pd.read_csv(SP, encoding="utf-8-sig")
    so = pd.read_csv(Path(out) / "point_result.csv", encoding="utf-8-sig")
    D = sp.merge(so[["point_id", "x", "y", "h", "LrT"]], on="point_id")
    D = D[np.isfinite(D.LrT) & D.point_id.str.startswith("G")].reset_index(drop=True)
    D["diff"] = D.LrT - D.LrD
    # 옮긴 점 계산(지면 위 같은 높이, 건물 안이면 뺌)
    X = np.concatenate([D.x.values + ox for ox, _ in OFFS])
    Y = np.concatenate([D.y.values + oy for _, oy in OFFS])
    H = np.tile(D.h.values, len(OFFS))
    zg, ins, outs = RUN.point_ground(d, X, Y)
    m = ~ins & ~outs
    k = int(m.sum())
    order = int(cfg["propagation"]["reflection"]["max_order_point"])
    r = RUN._run(cfg, d, S, F, X[m], Y[m], zg[m] + H[m], zg[m], np.full(k, -1, np.int64), np.full(k, -1, np.int64),
                 order, "옮긴 점")
    V = np.full(len(X), np.nan)
    V[m] = r["LrT"]
    V = V.reshape(len(OFFS), len(D))
    c = D.LrT.values
    D["범위_1.25m"] = np.nanmax(np.vstack([c, V[:4]]), 0) - np.nanmin(np.vstack([c, V[:4]]), 0)
    D["범위_2.5m"] = np.nanmax(np.vstack([c, V]), 0) - np.nanmin(np.vstack([c, V]), 0)
    B = gpd.read_file(Path(out) / "buildings.gpkg")
    u = B.geometry.union_all()
    D["건물거리_m"] = [u.distance(Point(x, y)) for x, y in zip(D.x, D.y)]
    D.to_csv(HERE / "sp_sensitivity_points.csv", index=False, encoding="utf-8-sig")

    def st(g):
        e = g["diff"]
        return pd.Series({"n": len(g), "평균": e.mean(), "SD": e.std(ddof=1), "RMSE": np.sqrt((e ** 2).mean()),
                          "|차이|>3dB %": (e.abs() > 3).mean() * 100, "범위2.5m 중앙": g["범위_2.5m"].median()})
    rows = []
    for lab, lo, hi in (("건물 2.5 m 안", 0, 2.5), ("건물 2.5–5 m", 2.5, 5), ("건물 5–10 m", 5, 10), ("건물 10 m 밖", 10, 1e9)):
        rows.append(("건물까지 거리", lab, *st(D[(D["건물거리_m"] >= lo) & (D["건물거리_m"] < hi)])))
    for lab, lo, hi in (("< 1 dB", 0, 1), ("1–3 dB", 1, 3), ("≥ 3 dB", 3, 1e9)):
        rows.append(("2.5 m 옮김 범위", lab, *st(D[(D["범위_2.5m"] >= lo) & (D["범위_2.5m"] < hi)])))
    rows.append(("전체", "", *st(D)))
    T = pd.DataFrame(rows, columns=["구분", "구간", "n", "평균", "SD", "RMSE", "|차이|>3dB %", "범위2.5m 중앙"]).round(2)
    T.to_csv(HERE / "sp_sensitivity.csv", index=False, encoding="utf-8-sig")
    big = D[D["diff"].abs() > 3]
    print(T.to_string(index=False))
    print(f"|차이| > 3 dB 점 {len(big)}개 중 2.5 m 안에서 SORI 값이 그 차이만큼 바뀌는 점: "
          f"{int((big['범위_2.5m'] >= big['diff'].abs()).sum())}개, 건물 5 m 안: {int((big['건물거리_m'] < 5).sum())}개")


if __name__ == "__main__":
    main()
