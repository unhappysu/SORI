# -*- coding: utf-8 -*-
"""
세분 격자(삼각망 보간) 검토 — 지금 2.5 m 격자가 건물·도로 경계에서 놓치는 값과, 경계만 더 촘촘히 계산하는
적응 격자의 효과. 계산 엔진은 바꾸지 않음(sori.run._run 으로 점만 계산).

방법
  1) 구역(한 변 size m)을 2.5 m 의 1/4 = 0.625 m 간격으로 모두 계산 → 정답(같은 엔진·같은 높이)
  2) 방식마다 '계산했어야 할 점'만 정답에서 골라 나머지를 삼각망(Delaunay 1차) 보간(삼각망 밖은 가장 가까운 점)
     - 균일 2.5 m(지금), 균일 1.25 m
     - 적응: 2.5 m 에서 시작해 칸(네 꼭짓점)이 건물·간선 노면 띠에 닿거나 네 값 범위가 thr dB 를 넘으면
       칸 안을 반 간격으로 계산, 0.625 m 까지 되풀이
  3) 건물 밖 정답 점에서 오차(보간 − 정답) 통계, 건물에서 5 m 안쪽은 따로
  python tools/fine_grid_study.py <config.yaml> <내보낼 폴더> --patch 이름 x0 y0 [--patch …] [--size 100]
  x0·y0 는 구역 왼쪽 아래(가까운 2.5 m 격자점에 맞춤). 정답은 <내보낼 폴더>/truth_<이름>.npz 로 남겨 다시 쓰기
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sori import config as C, run as RUN  # noqa: E402

FINE = 4                                     # 2.5 m 를 4 등분(0.625 m)


def truth(cfg, d, S, F, x0, y0, size, h, step, path):
    if Path(path).exists():
        z = np.load(path)
        return z["X"], z["Y"], z["V"], z["ins"]
    f = step / FINE
    n = int(round(size / f)) + 1
    X, Y = np.meshgrid(x0 + np.arange(n) * f, y0 + np.arange(n) * f)
    zg, ins, out = RUN.point_ground(d, X.ravel(), Y.ravel())
    m = ~ins & ~out
    k = int(m.sum())
    order = int(cfg["propagation"]["reflection"]["max_order_grid"])
    r = RUN._run(cfg, d, S, F, X.ravel()[m], Y.ravel()[m], zg[m] + h, zg[m], np.full(k, -1, np.int64),
                 np.full(k, -1, np.int64), order, f"정답 {path.name}")
    V = np.full(X.size, np.nan)
    V[m] = r["LrT"]
    V, ins = V.reshape(X.shape), (ins | out).reshape(X.shape)
    np.savez_compressed(path, X=X, Y=Y, V=V, ins=ins)
    return X, Y, V, ins


def road_mask(cfg, X, Y, step):
    """간선 노면 띠(도로폭/2 + 격자 간격) 안 점 — tools/adaptive_grid_study.py 와 같은 규칙"""
    import geopandas as gpd
    from shapely.geometry import Point
    from shapely.prepared import prep
    rd = gpd.read_file(Path(cfg["project"]["output_dir"]) / "roads.gpkg")
    rd = rd[rd["lanes"] >= int(cfg["model"]["main_min_lanes"])]
    band = prep(gpd.GeoSeries([g.buffer(max(float(w) if np.isfinite(w) else 6.0, 4.0) / 2 + step)
                               for g, w in zip(rd.geometry, rd["width"].astype(float))]).union_all())
    return np.array([band.contains(Point(x, y)) for x, y in zip(X.ravel(), Y.ravel())]).reshape(X.shape)


def adaptive(V, near, thr):
    """계산한 점 표시(정답 격자 위 bool). 시작 = FINE 칸마다(2.5 m), 칸을 반씩"""
    n = V.shape[0]
    comp = np.zeros(V.shape, bool)
    s = FINE
    comp[::s, ::s] = True
    while s > 1:
        h = s // 2
        for i in range(0, n - 1, s):
            for j in range(0, n - 1, s):
                i1, j1 = min(i + s, n - 1), min(j + s, n - 1)
                c = V[[i, i, i1, i1], [j, j1, j, j1]]
                hit = near[i:i1 + 1, j:j1 + 1].any()
                big = thr is not None and np.isfinite(c).sum() >= 2 and np.nanmax(c) - np.nanmin(c) > thr
                if hit or big:
                    comp[i:i1 + 1:h, j:j1 + 1:h] = True
        s = h
    return comp


def interp(X, Y, V, comp):
    from scipy.interpolate import LinearNDInterpolator, NearestNDInterpolator
    k = comp & np.isfinite(V)
    P = np.c_[X[k], Y[k]]
    out = LinearNDInterpolator(P, V[k])(X, Y)
    bad = np.isnan(out)
    out[bad] = NearestNDInterpolator(P, V[k])(X[bad], Y[bad])
    return out


def stats(err, m):
    e = np.abs(err[m])
    return dict(mean=float(e.mean()), p95=float(np.percentile(e, 95)), max=float(e.max()),
                gt1=float((e > 1).mean() * 100), gt3=float((e > 3).mean() * 100))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("outdir")
    ap.add_argument("--patch", nargs=3, action="append", metavar=("NAME", "X0", "Y0"), required=True)
    ap.add_argument("--size", type=float, default=100.0)
    ap.add_argument("--h", type=float, default=4.0)
    a = ap.parse_args()
    cfg = C.load(a.config)
    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    d, S, F, _ = RUN.load_model(cfg["project"]["output_dir"])
    step = float(cfg["receivers"]["grid"]["step"])
    gx, gy = d["gx"], d["gy"]
    rows = []
    for name, xs, ys in a.patch:
        x0 = float(gx[np.argmin(np.abs(gx - float(xs)))]); y0 = float(gy[np.argmin(np.abs(gy - float(ys)))])
        X, Y, V, ins = truth(cfg, d, S, F, x0, y0, a.size, a.h, step, out / f"truth_{name}.npz")
        near = ins | road_mask(cfg, X, Y, step)
        ok = np.isfinite(V)
        from scipy.ndimage import distance_transform_edt
        dist = distance_transform_edt(~ins) * step / FINE                      # 건물까지 거리(m)
        zones = {"전체": ok, "건물 5 m 안": ok & (dist <= 5.0), "건물 5 m 밖": ok & (dist > 5.0)}
        uni = lambda k: np.logical_and.outer(np.arange(V.shape[0]) % k == 0, np.arange(V.shape[1]) % k == 0)  # noqa: E731
        ways = {"균일 2.5 m(지금)": uni(FINE), "균일 1.25 m": uni(FINE // 2)}
        for thr in (None, 2.0, 1.0):
            ways[f"적응: 건물·도로{'' if thr is None else f' + {thr:g} dB'} → 0.625 m"] = adaptive(V, near, thr)
        n25 = int((uni(FINE) & ok).sum())
        maps = {}
        for w, comp in ways.items():
            E = interp(X, Y, V, comp) - V
            maps[w] = E
            for z, m in zones.items():
                rows.append(dict(구역=name, 방식=w, 범위=z, 계산점=int((comp & ok).sum()), 배율=round((comp & ok).sum() / n25, 2),
                                 점수=int(m.sum()), **{k: round(v, 2) for k, v in stats(E, m).items()}))
        np.savez_compressed(out / f"errors_{name}.npz", X=X, Y=Y, V=V, ins=ins,
                            **{f"E{i}": maps[w] for i, w in enumerate(ways)})
        json.dump(list(ways), open(out / "ways.json", "w", encoding="utf-8"), ensure_ascii=False)
    T = pd.DataFrame(rows)
    T.to_csv(out / "fine_grid_study.csv", index=False, encoding="utf-8-sig")
    print(T.to_string(index=False))


if __name__ == "__main__":
    main()
