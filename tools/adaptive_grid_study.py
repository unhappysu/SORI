# -*- coding: utf-8 -*-
"""
적응 격자(삼각망 보간) 모의 실험 — 보완 사항 2번 검토. 계산 엔진은 바꾸지 않음.
이미 계산한 촘촘한 격자 결과(grid_result.csv)를 '정답'으로 두고, 적응 격자로 계산했다면 몇 점을 계산했어야 하고
나머지를 삼각망(TIN, Delaunay 1차 보간)으로 채웠을 때 오차가 얼마인지 셈.

적응 규칙(모의)
  1) 거친 격자(간격 coarse, 정답 격자의 부분집합)를 모두 계산
  2) 단계마다 간격을 반으로: 이웃 계산점(같은 칸의 네 꼭짓점) 값의 범위가 thr dB 를 넘거나,
     칸이 건물(계산 제외 점)·간선 노면 띠에 닿으면 그 칸 안의 새 점을 계산
  3) 가장 촘촘한 간격(정답 격자 간격)까지 반복. 계산하지 않은 점은 계산점의 삼각망 1차 보간
  python tools/adaptive_grid_study.py <결과 폴더> <내보낼 폴더> [--coarse 4] [--thr 1 2 3] [--key LrT]
  --coarse 는 정답 격자 간격의 배수(2의 거듭제곱: 2, 4, 8)
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


def load_grid(out, key):
    G = pd.read_csv(Path(out) / "grid_result.csv")
    G = G[np.isclose(G.h, G.h.min())]
    d = np.load(Path(out) / "model_rasters.npz")
    gx, gy, ins = d["gx"], d["gy"], d["ginside"]
    step = float(gx[1] - gx[0])
    A = np.full((len(gy), len(gx)), np.nan)
    A[np.round((G.y - gy[0]) / step).astype(int), np.round((G.x - gx[0]) / step).astype(int)] = G[key].values
    return A, ins.astype(bool), gx, gy, step


def road_mask(out, gx, gy, step, min_lanes=2):
    """간선 노면 띠(도로폭/2 + 격자 간격) 안 격자점"""
    import geopandas as gpd
    from rasterio import features
    from rasterio.transform import from_origin
    rd = gpd.read_file(Path(out) / "roads.gpkg")
    rd = rd[rd["lanes"] >= min_lanes]
    shapes = [(g.buffer(max(float(w) if np.isfinite(w) else 6.0, 4.0) / 2 + step), 1)
              for g, w in zip(rd.geometry, rd["width"].astype(float))]
    tr = from_origin(gx[0] - step / 2, gy[-1] + step / 2, step, step)
    m = features.rasterize(shapes, out_shape=(len(gy), len(gx)), transform=tr, fill=0, dtype="uint8")
    return m[::-1].astype(bool)


def simulate(A, inside, road, coarse, thr):
    """반환: 계산한 점 표시(bool 배열)"""
    ny, nx = A.shape
    comp = np.zeros_like(inside)
    s = coarse
    comp[::s, ::s] = True
    near = inside | road                                        # 건물·간선 띠에 닿는 칸은 늘 세분
    while s > 1:
        h = s // 2
        for i0 in range(0, ny, s):
            for j0 in range(0, nx, s):
                i1, j1 = min(i0 + s, ny - 1), min(j0 + s, nx - 1)
                corners = A[[i0, i0, i1, i1], [j0, j1, j0, j1]]
                v = corners[np.isfinite(corners)]
                rng = (v.max() - v.min()) if len(v) >= 2 else np.inf
                if rng > thr or not len(v) or near[i0:i1 + 1, j0:j1 + 1].any():
                    comp[i0:i1 + 1:h, j0:j1 + 1:h] = True
        s = h
    return comp & ~inside


def evaluate(A, inside, comp, gx, gy):
    from scipy.interpolate import LinearNDInterpolator
    X, Y = np.meshgrid(gx, gy)
    ok = np.isfinite(A) & ~inside
    c = comp & ok
    f = LinearNDInterpolator(np.c_[X[c], Y[c]], A[c])
    rest = ok & ~c
    est = f(X[rest], Y[rest])
    err = est - A[rest]
    err = err[np.isfinite(err)]
    n = int(ok.sum())
    return {"points_total": n, "points_computed": int(c.sum()), "computed_pct": round(100 * c.sum() / n, 1),
            "err_abs_p50": round(float(np.median(np.abs(err))), 3) if len(err) else 0.0,
            "err_abs_p95": round(float(np.quantile(np.abs(err), 0.95)), 3) if len(err) else 0.0,
            "err_abs_max": round(float(np.abs(err).max()), 2) if len(err) else 0.0,
            "over_1dB_pct_of_all": round(100 * (np.abs(err) > 1.0).sum() / n, 2),
            "over_3dB_pct_of_all": round(100 * (np.abs(err) > 3.0).sum() / n, 3)}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("out"); ap.add_argument("dest")
    ap.add_argument("--coarse", type=int, nargs="+", default=[4, 8])
    ap.add_argument("--thr", type=float, nargs="+", default=[1.0, 2.0, 3.0])
    ap.add_argument("--key", default="LrT"); ap.add_argument("--label", default="")
    a = ap.parse_args(argv)
    A, inside, gx, gy, step = load_grid(a.out, a.key)
    road = road_mask(a.out, gx, gy, step)
    rows = []
    for cs in a.coarse:
        for t in a.thr:
            comp = simulate(A, inside, road, cs, t)
            r = dict(label=a.label, step_m=step, coarse_m=step * cs, thr_dB=t, **evaluate(A, inside, comp, gx, gy))
            rows.append(r)
            print(r, flush=True)
        # 세분 기준 없이(건물·도로만) — 값 범위 기준의 몫을 보려고
        comp = simulate(A, inside, road, cs, np.inf)
        rows.append(dict(label=a.label, step_m=step, coarse_m=step * cs, thr_dB=None,
                         **evaluate(A, inside, comp, gx, gy)))
        print(rows[-1], flush=True)
    dest = Path(a.dest)
    dest.mkdir(parents=True, exist_ok=True)
    T = pd.DataFrame(rows)
    f = dest / "adaptive_grid_study.csv"
    if f.exists():
        T = pd.concat([pd.read_csv(f), T], ignore_index=True)
    T.to_csv(f, index=False, encoding="utf-8-sig")
    return T


if __name__ == "__main__":
    main()
