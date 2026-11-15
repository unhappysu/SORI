# -*- coding: utf-8 -*-
"""
정밀 격자(경계 세분, 2026-11-15): 격자 간격(step)으로 계산한 뒤, 칸(네 꼭짓점)이 건물·간선 노면 띠에 닿거나
네 값의 범위가 threshold_dB 를 넘으면 칸 안을 반 간격으로 더 계산 — levels 번(기본 2 → step/4, 2.5 m 격자면 0.625 m).
계산하지 않은 점은 계산점의 삼각망(Delaunay 1차) 보간으로 채워 그림·GeoTIFF 에만 씀.
  - grid_result.csv 는 예전과 같은 규칙 격자(값도 같음). 더 계산한 점은 grid_refined.csv,
    삼각망으로 채운 세밀 배열은 grid_fine_h<높이>m.npz(지도 그리기용)와 grid_<키>_h<높이>m_fine.tif
  - 검토: validation/fine_grid/README.md(전농사거리 두 구역, 2 dB 기준 계산점 4.5–7.7배, 95 % 오차 0.25–0.5 dB)
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import os
import warnings

import numpy as np

KEYS = ("LrT", "LrN")


def opts(cfg):
    """설정 receivers.grid.refine → (켬 여부, 기준 dB, 단계 수)"""
    r = (cfg["receivers"]["grid"].get("refine") or {})
    return bool(r.get("enabled", False)), float(r.get("threshold_dB", 2.0)), int(r.get("levels", 2))


def lattice(gx, gy, levels):
    """세밀 격자 좌표(규칙 격자 간격 / 2^levels). 규칙 격자점은 k 칸마다 겹침"""
    k = 2 ** levels
    f = float(gx[1] - gx[0]) / k
    fx = gx[0] + np.arange((len(gx) - 1) * k + 1) * f
    fy = gy[0] + np.arange((len(gy) - 1) * k + 1) * f
    return fx, fy, k, f


def road_band(out, min_lanes, step, FX, FY):
    """간선 노면 띠(도로폭/2 + 규칙 격자 간격) 안 세밀 격자점 — 검토(tools/fine_grid_study.py)와 같은 규칙"""
    import geopandas as gpd
    from rasterio import features
    from rasterio.transform import from_origin
    p = os.path.join(out, "roads.gpkg")
    if not os.path.exists(p):
        return np.zeros(FX.shape, bool)
    rd = gpd.read_file(p)
    rd = rd[rd["lanes"] >= min_lanes]
    if not len(rd):
        return np.zeros(FX.shape, bool)
    w = rd["width"].astype(float)
    shapes = [(g.buffer(max(float(v) if np.isfinite(v) else 6.0, 4.0) / 2 + step), 1) for g, v in zip(rd.geometry, w)]
    f = FX[0, 1] - FX[0, 0]
    tr = from_origin(FX[0, 0] - f / 2, FY[-1, 0] + f / 2, f, f)
    m = features.rasterize(shapes, out_shape=FX.shape, transform=tr, fill=0, dtype="uint8")
    return m[::-1].astype(bool)                          # 래스터는 북→남, 세밀 격자 행은 남→북


def _window_any(M, s):
    """칸(꼭짓점 간격 s)마다 칸 안(경계 포함) M 이 하나라도 참인지 → (칸 행, 칸 열)"""
    c = np.pad(M.astype(np.int32), ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    i = np.arange(0, M.shape[0] - 1, s)
    j = np.arange(0, M.shape[1] - 1, s)
    I, J = np.meshgrid(i, j, indexing="ij")
    tot = c[I + s + 1, J + s + 1] - c[I, J + s + 1] - c[I + s + 1, J] + c[I, J]
    return tot > 0


def refine(V0, ins, near, k, thr, compute):
    """적응 세분. V0: 규칙 격자 값(LrT, 건물 안 NaN), ins·near: 세밀 격자의 건물 안·(건물|노면 띠) 표시,
    compute(i, j) → {열: 값 배열} 새 점 계산(세밀 격자 색인). 반환 (V 세밀 격자 LrT, 계산한 점 표시, 새 점 [(i, j, 결과, 간격 색인)])"""
    ny, nx = ins.shape
    V = np.full((ny, nx), np.nan)
    V[::k, ::k] = V0
    comp = np.zeros((ny, nx), bool)
    comp[::k, ::k] = True
    new = []
    s = k
    while s > 1:
        h = s // 2
        Vs = V[::s, ::s]
        q = np.stack([Vs[:-1, :-1], Vs[:-1, 1:], Vs[1:, :-1], Vs[1:, 1:]])
        nf = np.isfinite(q).sum(0)
        with warnings.catch_warnings():                  # 꼭짓점이 모두 건물 안인 칸(All-NaN) 경고 숨김
            warnings.simplefilter("ignore", RuntimeWarning)
            rng = np.where(nf >= 2, np.nanmax(q, 0) - np.nanmin(q, 0), 0.0)
        R = _window_any(near, s) | (rng > thr)
        mark = np.zeros((ny, nx), bool)
        nci, ncj = R.shape
        for a in range(3):
            for b in range(3):
                mark[a * h:a * h + (nci - 1) * s + 1:s, b * h:b * h + (ncj - 1) * s + 1:s] |= R
        todo = mark & ~comp & ~ins
        comp |= mark & ~ins
        ii, jj = np.nonzero(todo)
        if len(ii):
            r = compute(ii, jj)
            V[ii, jj] = r["LrT"]
            new.append((ii, jj, r, h))
        s = h
    return V, comp, new


def fill(fx, fy, P, vals, ins):
    """계산점 P(x, y)의 값을 세밀 격자 전체로 삼각망 1차 보간(삼각망 밖은 가장 가까운 점), 건물 안 NaN"""
    from scipy.interpolate import LinearNDInterpolator, NearestNDInterpolator
    from scipy.spatial import Delaunay
    FX, FY = np.meshgrid(fx, fy)
    tri = Delaunay(P)
    out = {}
    for key, v in vals.items():
        A = LinearNDInterpolator(tri, v)(FX, FY)
        bad = np.isnan(A)
        if bad.any():
            A[bad] = NearestNDInterpolator(P, v)(FX[bad], FY[bad])
        A[ins] = np.nan
        out[key] = A.astype(np.float32)
    return out


def fine_path(out, h):
    return os.path.join(out, f"grid_fine_h{h:g}m.npz")


def load_fine(out, h):
    """그리기용 세밀 배열(fx, fy, step, LrT, LrN). 정밀 격자로 계산하지 않았으면 None"""
    p = fine_path(out, h)
    if not os.path.exists(p):
        return None
    z = np.load(p)
    return {k: z[k] for k in z.files}


def clear(out):
    """이전 정밀 격자 결과 지우기(규칙 격자만 다시 계산할 때 예전 세밀 그림이 남지 않게)"""
    for f in os.listdir(out):
        if f == "grid_refined.csv" or (f.startswith("grid_fine_h") and f.endswith(".npz")) or f.endswith("m_fine.tif"):
            os.remove(os.path.join(out, f))
