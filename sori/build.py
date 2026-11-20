# -*- coding: utf-8 -*-
"""
계산 모델 구축
  1) 레이어 로드  2) 지형(DTM)·건물(DSM/ID)·지면계수(G) 래스터
  3) 도로 위상(교차로) → 음원선(RLS-19 3.3.1, Bild 6) → 분할(3.2) → LW'(식 4~9)
     propagation_method: rls90 이면 RLS-90 음원: 음원선 = 바깥 차로 중앙(4.3, Bild 10), L_m,E(식 6~9)를
     등가 L_W′ 로 환산(rls90.py), D_refl 식 (24a), 교차로 할증 K 는 수음점 쪽(표 2, run.py)
  4) 반사면(파사드), 격자 수음점(다중 높이), 파사드 수음점(층별)
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import json
import logging
import math
from collections import defaultdict

import numpy as np
import pandas as pd
import geopandas as gpd
from rasterio import features
from rasterio.transform import from_origin
from scipy.interpolate import griddata
from scipy.ndimage import map_coordinates
from scipy.spatial import cKDTree
from shapely import STRtree
from shapely.geometry import box, Point, LineString
from shapely.geometry.polygon import orient

from . import rls19, rls90, inputs as I
from .propagation import method_name, EMISSION_LABEL
from .config import grid_bbox, center

log = logging.getLogger("sori")


# ====================================================================== 래스터
class Raster:
    def __init__(self, bounds, res):
        x0, y0, x1, y1 = bounds
        self.res = res
        self.x0 = math.floor(x0)
        self.y0 = math.ceil(y1)
        self.nx = int(math.ceil((x1 - self.x0) / res))
        self.ny = int(math.ceil((self.y0 - y0) / res))
        self.transform = from_origin(self.x0, self.y0, res, res)

    def centers(self):
        xs = self.x0 + (np.arange(self.nx) + 0.5) * self.res
        ys = self.y0 - (np.arange(self.ny) + 0.5) * self.res
        return np.meshgrid(xs, ys)

    def sample(self, arr, x, y, order=1):
        col = (np.asarray(x, float) - self.x0) / self.res - 0.5
        row = (self.y0 - np.asarray(y, float)) / self.res - 0.5
        return map_coordinates(arr, [row, col], order=order, mode="nearest")

    def ij(self, x, y):
        return int((self.y0 - y) / self.res), int((x - self.x0) / self.res)


def build_terrain(cfg, cont, spot, rast):
    F = cfg["input"]["map"]["fields"]; dcfg = cfg["input"]["dem"]
    X, Y = rast.centers()
    info = {"contour_pts": 0, "spot_pts": 0, "dem_pts": 0, "mode": ""}
    if dcfg["path"] and dcfg["use_as"] == "primary":
        Z = I.sample_dem(dcfg["path"], X.ravel(), Y.ravel(), cfg["crs"]).reshape(X.shape)
        if np.isnan(Z).any():
            Z = np.where(np.isnan(Z), np.nanmean(Z), Z)
        info.update(mode="dem_primary", dem_pts=int(Z.size))
        return Z.astype(np.float32), info
    pts, val = [], []
    if cont is not None and len(cont):
        for geom, z in zip(cont.geometry, pd.to_numeric(cont[F["contour_z"]], errors="coerce")):
            if not np.isfinite(z):
                continue
            for x, y in geom.segmentize(5.0).coords:
                pts.append((x, y)); val.append(z)
        info["contour_pts"] = len(pts)
    if spot is not None and len(spot):
        zz = pd.to_numeric(spot[F["spot_z"]], errors="coerce")
        for geom, z in zip(spot.geometry, zz):
            if np.isfinite(z):
                pts.append((geom.x, geom.y)); val.append(z)
        info["spot_pts"] = len(pts) - info["contour_pts"]
    pts = np.array(pts, float).reshape(-1, 2); val = np.array(val, float)
    if dcfg["path"]:
        sp = dcfg["aux_spacing"]
        gx = np.arange(rast.x0, rast.x0 + rast.nx * rast.res, sp)
        gy = np.arange(rast.y0 - rast.ny * rast.res, rast.y0, sp)
        GX, GY = np.meshgrid(gx, gy)
        dv = I.sample_dem(dcfg["path"], GX.ravel(), GY.ravel(), cfg["crs"])
        use = np.isfinite(dv)
        if len(pts):
            dmin, _ = cKDTree(pts).query(np.c_[GX.ravel(), GY.ravel()])
            use &= dmin > dcfg["aux_min_dist"]
        pts = np.vstack([pts, np.c_[GX.ravel()[use], GY.ravel()[use]]])
        val = np.r_[val, dv[use]]
        info["dem_pts"] = int(use.sum())
    if len(pts) < 3:
        log.warning("지형 자료(등고선·표고점·DEM)가 없음 → 평지(0 m) 가정")
        info["mode"] = "flat"
        return np.zeros(X.shape, np.float32), info
    Z = griddata(pts, val, (X, Y), method="linear")
    Zn = griddata(pts, val, (X, Y), method="nearest")
    info["mode"] = "contours+dem" if info["dem_pts"] else "contours"
    Z = np.where(np.isnan(Z), Zn, Z)
    sm = float(cfg["model"].get("terrain_smooth_m", 0) or 0)
    if sm > 0:                                    # 등고선 삼각망의 평탄 계단(같은 높이 등고선끼리 삼각형) 완화
        from scipy.ndimage import gaussian_filter
        Z = gaussian_filter(Z, sigma=sm / rast.res, mode="nearest")
        info["smooth_m"] = sm
    return Z.astype(np.float32), info


def flatten_road_surface(cfg, road, gnd, rast):
    """차도 면 지형 보정(model.road_surface: flatten). 등고선 DTM 은 노면을 모르므로 도로가 골짜기 바닥·경사면에 놓이면
    차도 안 지형이 음원보다 높아져 차폐로 잡힘(TECHNICAL_NOTES 5a절 8). 수치지도 도로폭 안(도로중심선 ± 도로폭/2) 칸의
    지형을 가장 가까운 도로중심선 점의 종단 고도(중심선 DTM 을 road_surface_smooth_m 이동평균)로 바꿈.
    교차로처럼 띠가 겹치면 가장 가까운 중심선을 따름. 건물 칸은 바꾸지 않음(DSM 은 이 지형으로 다시 만듦).
    반환: (보정 지형, 기록 dict)"""
    mc = cfg["model"]
    win = float(mc.get("road_surface_smooth_m", 20.0) or 0.0)
    step = max(rast.res, 1.0)
    pts, zs, polys = [], [], []
    for _, rd in road.iterrows():
        g = rd.geometry
        w = rd["width"]
        w = float(w) if w is not None and np.isfinite(w) and w > 0 else 3.25 * max(int(rd["lanes"]), 1)
        for line in getattr(g, "geoms", [g]):
            L = line.length
            if L < 0.5:
                continue
            n = max(2, int(math.ceil(L / step)) + 1)
            ss = np.linspace(0.0, L, n)
            P = [line.interpolate(s) for s in ss]
            x = np.array([p.x for p in P]); y = np.array([p.y for p in P])
            z = rast.sample(gnd, x, y)
            k = int(round(win / (L / (n - 1)))) if win > 0 else 0
            if k >= 2:                              # 중심선 종단 이동평균(끝은 창을 줄여서)
                k = min(k, n)
                c = np.cumsum(np.r_[0.0, z])
                h = k // 2
                lo = np.clip(np.arange(n) - h, 0, n); hi = np.clip(np.arange(n) + h + 1, 0, n)
                z = (c[hi] - c[lo]) / (hi - lo)
            pts.append(np.c_[x, y]); zs.append(z)
            polys.append(line.buffer(w / 2.0, cap_style="flat"))
    if not polys:
        return gnd, {"mode": "flatten", "cells": 0}
    band = features.rasterize(((p, 1) for p in polys), out_shape=(rast.ny, rast.nx), transform=rast.transform,
                              fill=0, dtype="uint8", all_touched=False).astype(bool)
    X, Y = rast.centers()
    xy = np.c_[X[band], Y[band]]
    idx = _nearest_lowest_index(np.vstack(pts), xy)
    znew = np.concatenate(zs)[idx].astype(np.float32)
    out = gnd.copy()
    dz = znew - out[band]
    out[band] = znew
    info = {"mode": "flatten", "smooth_m": win, "cells": int(band.sum()),
            "dz_mean": round(float(np.mean(dz)), 3), "dz_abs_p90": round(float(np.percentile(np.abs(dz), 90)), 3),
            "dz_min": round(float(dz.min()), 3), "dz_max": round(float(dz.max()), 3),
            "cells_lowered_over_1m": int((dz < -1.0).sum()), "cells_raised_over_1m": int((dz > 1.0).sum())}
    log.info("차도 면 지형 보정(flatten): %s", info)
    return out, info


def _nearest_lowest_index(pts, xy, k=4, tol=1e-6):
    """xy 마다 가장 가까운 pts 번호. 거리가 같은(차이 tol m 이하) 후보가 여럿이면 가장 작은 번호.
    축에 나란한 도로·정수 좌표에서는 칸 중심이 두 중심선 점에서 정확히 같은 거리인 경우가 많은데, 그때 cKDTree 가
    고르는 점은 CPU 의 부동소수점 끝자리(arm64 FMA 대 x86-64)에 따라 달라짐 → 맥과 Windows 결과가 다름(2026-11-20).
    같은 거리면 번호 규칙으로 정해 어느 CPU 에서나 같은 점을 고름. 동률이 없으면 결과는 이전과 같음"""
    k = min(k, len(pts))
    d, idx = cKDTree(pts).query(xy, k=k)
    if k == 1:
        return idx
    tie = d <= d[:, :1] + tol
    return np.where(tie, idx, np.iinfo(idx.dtype).max).min(axis=1)


def _road_band(road, rast):
    """수치지도 도로폭 안(도로중심선 ± 폭/2) 칸 마스크 — flatten_road_surface 와 같은 띠"""
    polys = []
    for _, rd in road.iterrows():
        w = rd["width"]
        w = float(w) if w is not None and np.isfinite(w) and w > 0 else 3.25 * max(int(rd["lanes"]), 1)
        for line in getattr(rd.geometry, "geoms", [rd.geometry]):
            if line.length >= 0.5:
                polys.append(line.buffer(w / 2.0, cap_style="flat"))
    if not polys:
        return np.zeros((rast.ny, rast.nx), bool)
    return features.rasterize(((p, 1) for p in polys), out_shape=(rast.ny, rast.nx), transform=rast.transform,
                              fill=0, dtype="uint8", all_touched=False).astype(bool)


def _rasterize_bid(bld, rast):
    return features.rasterize(((g, int(b)) for g, b in zip(bld.geometry, bld.bid)),
                              out_shape=(rast.ny, rast.nx), transform=rast.transform,
                              fill=-1, dtype="int32", all_touched=False)


def outline_min_z(bld, gnd, rast, step=None):
    """건물별 외곽선(바깥·안쪽 고리)을 step(m, 기본 0.5 칸) 간격으로 따라간 지형고 최솟값 — 벽 어디서도 바닥이 지형보다 높지 않음"""
    step = 0.5 * rast.res if step is None else step
    z0 = []
    for g in bld.geometry:
        zs = []
        for ring in [g.exterior] + list(g.interiors):
            c = np.asarray(ring.segmentize(step).coords)
            zs.append(np.min(rast.sample(gnd, c[:, 0], c[:, 1])))
        z0.append(float(min(zs)))
    return np.asarray(z0)


def grade_building_pads(cfg, bld, road, gnd, rast):
    """건물 부지 정지(model.building_ground: pad, 2026-11-20). 등고선·DEM 은 건물 부지를 깎은 면을 모르므로, 경사지 건물은
    오르막 쪽 벽이 지형에 묻히고(최솟값 바닥) 긴 벽 아래 지형이 꺼진 곳은 건물이 뜸(꼭짓점만 본 최솟값).
      z0 = 외곽선 0.5 칸 간격 지형고 최솟값(outline_min_z)
      건물 안 = z0, 벽 바로 바깥 띠(1.5 칸, 1 m 격자에서 1.5 m) = z0(이 띠만 쌓기 허용 — 벽 아래가 꺼진 곳의 뜸 방지), 그 바깥 building_pad_m 까지
      원래 지형으로 선형 연결(가장 가까운 건물 기준). 1 m 띠 바깥은 깎기만 함(새 지형 = min(원래, 정지면)) — 없던
      둔덕(차폐)을 만들지 않음. 도로폭 안 칸은 바꾸지 않음(음원 높이 불변) — 도로와 맞닿은 벽은 도로면 아래로 묻힐 수 있음.
    반환: (정지한 지형, z0 배열, 기록 dict)"""
    from scipy.ndimage import distance_transform_edt
    B = float(cfg["model"].get("building_pad_m", 5.0))
    apron = min(max(1.0, 1.5 * rast.res), B)     # 벽 바로 바깥 띠(칸 중심 거리, 모서리 대각선 칸 √2 칸 포함)
    z0 = outline_min_z(bld, gnd, rast)
    bidr = _rasterize_bid(bld, rast)
    if not (bidr >= 0).any():
        return gnd, z0, {"mode": "pad", "cells": 0}
    pos = {int(b): i for i, b in enumerate(bld.bid)}
    zb = np.full(int(bld.bid.max()) + 1, np.nan)
    for b, i in pos.items():
        zb[b] = z0[i]
    dist, (ii, jj) = distance_transform_edt(bidr < 0, sampling=rast.res, return_indices=True)
    near = bidr[ii, jj]
    target = zb[np.clip(near, 0, None)]
    w = np.clip((B - dist) / max(B - apron, 1e-6), 0.0, 1.0)          # 벽 1 m 안 = 1, B 에서 0
    w[dist <= apron] = 1.0
    graded = gnd + w * (target - gnd)
    out = np.where(dist <= B, np.minimum(gnd, graded), gnd)
    out = np.where(dist <= apron, target, out).astype(np.float32)   # 건물 안·벽 바깥 띠는 바닥 높이 그대로(이 띠만 쌓기 허용 — 뜸 방지)
    road_m = _road_band(road, rast)
    out[road_m & (bidr < 0)] = gnd[road_m & (bidr < 0)]
    dz = out - gnd
    ch = np.abs(dz) > 1e-4
    info = {"mode": "pad", "pad_m": B, "cells": int(ch.sum()), "cells_filled": int((dz > 1e-4).sum()),
            "fill_max": round(float(max(dz.max(), 0.0)), 3),
            "cells_in_buildings": int((ch & (bidr >= 0)).sum()),
            "cut_mean": round(float(-dz[dz < -1e-4].mean()), 3) if (dz < -1e-4).any() else 0.0,
            "cut_p90": round(float(np.percentile(-dz[dz < -1e-4], 90)), 3) if (dz < -1e-4).any() else 0.0,
            "cut_max": round(float(-dz.min()), 3),
            "cells_cut_over_1m": int((dz < -1.0).sum()), "cells_cut_over_5m": int((dz < -5.0).sum())}
    log.info("건물 부지 정지(pad): %s", info)
    return out, z0, info


def build_dsm(bld, gnd, rast, z0=None, follow=False):
    """follow=True(model.building_ground: follow): 지붕이 지형을 따라감 — 건물 안 칸마다 그 칸 지형 + H.
    기록용 z0 = 외곽선 꼭짓점 최솟값, ztop = 외곽선 0.5 칸 간격 최댓값 + H"""
    bidr = _rasterize_bid(bld, rast)
    if z0 is None:
        z0 = []
        for g in bld.geometry:                     # 건물 기저 = 외곽선 정점 지형고의 최솟값(model.building_ground: vertex_min)
            c = np.asarray(g.exterior.coords)
            z0.append(float(np.min(rast.sample(gnd, c[:, 0], c[:, 1]))))
    bld["z0"] = list(map(float, z0))
    if follow:
        zmax = []
        for g in bld.geometry:
            c = np.vstack([np.asarray(r.segmentize(0.5 * rast.res).coords) for r in [g.exterior] + list(g.interiors)])
            zmax.append(float(np.max(rast.sample(gnd, c[:, 0], c[:, 1]))))
        bld["ztop"] = np.asarray(zmax) + bld["H"].values
        from .bldz import encode                       # 3D 그리기용 꼭짓점별 지붕 표고(지형 + H)
        bld["ztops"] = [encode(g, lambda x, y: rast.sample(gnd, x, y), float(h)) for g, h in zip(bld.geometry, bld.H)]
        Hb = bld["H"].values.astype(np.float32)
        dsm = np.where(bidr >= 0, gnd + Hb[np.clip(bidr, 0, None)], gnd).astype(np.float32)
        return dsm, bidr.astype(np.int64)
    bld["ztop"] = bld["z0"] + bld["H"]
    top = bld["ztop"].values.astype(np.float32)
    dsm = np.where(bidr >= 0, top[np.clip(bidr, 0, None)], gnd).astype(np.float32)
    return dsm, bidr.astype(np.int64)


def build_G(cfg, rast):
    gc = cfg["input"]["ground"]
    G = np.full((rast.ny, rast.nx), float(gc["default_G"]), np.float32)
    soft = I.load_soft_ground(cfg)
    if soft is not None and len(soft):
        m = features.rasterize(((g, 1) for g in soft.geometry), out_shape=G.shape,
                               transform=rast.transform, fill=0, dtype="uint8")
        G[m == 1] = float(gc["soft_G"])
    return G


# ====================================================================== 도로 위상 / 교차로
def junctions(cfg, road):
    """
    교차로 = 도로중심선 끝점이 3개 이상 모이는 절점, 또는 선이 교차·T자 접속하는 점.
    (차수 2 절점 = 같은 도로의 단순 연장 → 교차로 아님)
    각 도로객체가 '자신이 속한' 교차로 집합(차수 2 절점을 따라 이어진 끝 교차로 + 내부 교차점)을 가짐.
    → RLS-19 3.3.7: x = 그 음원선의 가장 가까운 교차점까지 거리. 다른 도로의 교차로는 적용하지 않음.
    """
    jc = cfg["input"]["junctions"]
    geoms = list(road.geometry)
    n = len(geoms)
    ends = np.array([[g.coords[0], g.coords[-1]] for g in geoms]).reshape(-1, 2)   # 2n
    tree = cKDTree(ends)
    parent = list(range(len(ends)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]; a = parent[a]
        return a
    for a, b in tree.query_pairs(1.0):
        parent[find(a)] = find(b)
    node_of_end = np.array([find(i) for i in range(len(ends))])
    deg = defaultdict(int)
    for nd in node_of_end:
        deg[nd] += 1
    node_xy = {nd: tuple(ends[nd]) for nd in set(node_of_end)}
    jpts = []                                    # (x, y, set(fid))
    node_members = defaultdict(set)
    for i, nd in enumerate(node_of_end):
        node_members[nd].add(i // 2)
    jnode = {}
    for nd, d in deg.items():
        if d >= 3:
            jnode[nd] = len(jpts)
            jpts.append([node_xy[nd][0], node_xy[nd][1], set(node_members[nd])])
    # 교차·T자 접속 (끝점이 아닌 위치)
    st = STRtree(geoms)
    for i, g in enumerate(geoms):
        for k in st.query(g, predicate="intersects"):
            if k <= i:
                continue
            it = g.intersection(geoms[k])
            for p in getattr(it, "geoms", [it]):
                if p.geom_type != "Point":
                    continue
                xy = (p.x, p.y)
                near_end_i = min(I.dist(xy, geoms[i].coords[0]), I.dist(xy, geoms[i].coords[-1])) < 1.0
                near_end_k = min(I.dist(xy, geoms[k].coords[0]), I.dist(xy, geoms[k].coords[-1])) < 1.0
                if near_end_i and near_end_k:
                    continue                        # 끝점끼리 만남 → 절점 차수로 처리됨
                jpts.append([xy[0], xy[1], {i, k}])
    # 차수 2 절점을 따라 연결된 도로객체에 교차로 소속 전파
    adj = defaultdict(list)                        # fid -> 이웃 fid (차수 2 절점 통해)
    for nd, mem in node_members.items():
        if deg[nd] == 2 and len(mem) == 2:
            a, b = sorted(mem)
            adj[a].append(b); adj[b].append(a)
    own = defaultdict(set)                          # fid -> set(junction idx)
    for jidx, (_, _, mem) in enumerate(jpts):
        for f in mem:
            own[f].add(jidx)
    reach = {}
    for f in range(n):
        seen = {f}; stack = [f]; js = set(own[f])
        while stack:
            a = stack.pop()
            for b in adj[a]:
                if b not in seen:
                    seen.add(b); stack.append(b); js |= own[b]
        reach[f] = js
    # 군집 → 유형 판정
    if not jpts:
        return pd.DataFrame(columns=["x", "y", "cluster", "type", "auto"]), reach
    P = np.array([[p[0], p[1]] for p in jpts])
    parent = list(range(len(P)))
    for a, b in cKDTree(P).query_pairs(jc["cluster_radius"]):
        parent[find(a)] = find(b)
    cl = np.array([find(i) for i in range(len(P))])
    lanes = road["lanes"].values; axis = road["axis"].values
    ctype = {}
    for c in set(cl):
        mem = set().union(*[jpts[i][2] for i in np.where(cl == c)[0]])
        big_axes = {axis[f] for f in mem if lanes[f] >= jc["auto_signal_min_lanes"]}
        ctype[c] = jc["auto_type"] if len(big_axes) >= 2 else "other"
    J = pd.DataFrame({"x": P[:, 0], "y": P[:, 1], "cluster": cl,
                      "type": [ctype[c] for c in cl], "auto": True})
    user = I.load_junctions(cfg)
    if user is not None and len(user):
        ctr = J.groupby("cluster")[["x", "y"]].mean()
        tr = cKDTree(ctr.values)
        for _, u in user.iterrows():
            d, k = tr.query([u.x, u.y])
            if d <= jc["user_match_radius"]:
                c = ctr.index[k]
                J.loc[J.cluster == c, "type"] = u["type"]; J.loc[J.cluster == c, "auto"] = False
            else:
                log.warning("사용자 교차로 (%.1f, %.1f) 근처 %g m 이내에 도로 교차점 없음 → 무시",
                            u.x, u.y, jc["user_match_radius"])
    return J, reach


def dk_nearest_signal(px, py, Jxy, Jtype):
    """dk_rule: nearest_signal 의 신호·회전교차로 기준 D_K.
    도로 소속과 무관하게 모든 signal·roundabout 교차점 중 KKT · max(1 − x/120, 0) 이 가장 큰 값.
    반환 (D_K, x, 유형) — 대상 교차점이 없으면 (0, 999, "other")."""
    px = np.asarray(px, float); py = np.asarray(py, float)
    dk = np.zeros(len(px)); xs = np.full(len(px), 999.0); ks = np.full(len(px), "other", dtype=object)
    for t in ("signal", "roundabout"):
        m = np.asarray(Jtype) == t
        if not m.any():
            continue
        d = np.hypot(px[:, None] - Jxy[m, 0][None, :], py[:, None] - Jxy[m, 1][None, :]).min(axis=1)
        v = rls19.KKT[t] * np.clip(1.0 - d / 120.0, 0.0, None)
        better = v > dk
        dk[better] = v[better]; xs[better] = d[better]; ks[better] = t
    return dk, xs, ks


# ====================================================================== 음원선
def _d_edge(nd):
    """Bild 6: 바깥 차로 가장자리에서 음원선까지 거리(차로폭 배수).
    1차로 = 차로 중앙(0.5), 2차로 = 바깥 차로 중앙(0.5), 3~4차로 = 바깥 두 차로 사이 차선(1.0),
    5차로 이상 = 바깥에서 두 번째 차로 중앙(1.5)"""
    return 0.5 if nd <= 2 else (1.0 if nd <= 4 else 1.5)


def _finite(v):
    try:
        return v is not None and math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def lane_layout(cfg, n_lanes, width, oneway, lanes_fwd=None, lanes_bwd=None):
    """방향별 차로 수와 차로폭 결정 (RLS-19 3.3.1: 음원선 위치는 방향별 차로 수로 정함).
    우선순위: 확인값(roads_override: 양방향은 lanes_fwd·lanes_bwd 모두 ≥ 1, 일방통행은 lanes_fwd) → 수치지도 차로수.
    반환: (nd_fwd, nd_bwd, wl, rule)  rule = override | oneway_map | single_centre | map_half | ceil | major_fwd
      nd_fwd = 디지타이즈 방향, nd_bwd = 반대 방향(일방통행·중앙 1개선은 0)"""
    mc = cfg["model"]
    if oneway:
        if _finite(lanes_fwd) and float(lanes_fwd) >= 1:
            nd, nb, rule = int(lanes_fwd), 0, "override"
        else:
            nd, nb, rule = int(n_lanes), 0, "oneway_map"
        total = nd
    else:
        if _finite(lanes_fwd) and _finite(lanes_bwd) and float(lanes_fwd) >= 1 and float(lanes_bwd) >= 1:
            nd, nb, rule = int(lanes_fwd), int(lanes_bwd), "override"
        elif n_lanes <= 1:
            nd, nb, rule = 1, 0, "single_centre"
        elif n_lanes % 2 == 0:
            nd = nb = int(n_lanes // 2); rule = "map_half"
        elif mc.get("odd_lane_rule", "ceil") == "major_fwd":
            nd, nb, rule = int(math.ceil(n_lanes / 2)), int(n_lanes // 2), "major_fwd"
        else:                                      # 현행: 양쪽 모두 ⌈n/2⌉
            nd = nb = int(math.ceil(n_lanes / 2)); rule = "ceil"
        total = (nd + nb) if rule == "override" else n_lanes
    lw = mc.get("lane_width", "auto")
    if lw == "auto" or lw is None:
        lo, hi = mc.get("lane_width_clip", [2.75, 3.5])
        wl = float(np.clip(width / total, lo, hi)) if width and width > 0 else float(mc.get("lane_width_default", 3.25))
    else:
        wl = float(lw)
    return nd, nb, wl, rule


def _d_edge_rls90(nd):
    """RLS-90 4.3 / Bild 10: 음원 = 바깥 차로(äußerer Fahrstreifen) 중앙 → 차로 수와 무관하게 0.5"""
    return 0.5


def source_offsets(nd_fwd, nd_bwd, wl, oneway, single=False, oneway_dir=1, edge=None):
    """RLS-19 3.3.1 / Bild 6. 반환: [(횡오프셋 m, 디지타이즈 방향과 같은가)] (+ = 디지타이즈 진행방향 좌측)
    양방향: 도로중심선을 방향 경계로 간주(미확인 가정 — TECHNICAL_NOTES). 일방통행: 중심선 = 차도 중앙.
    oneway_dir = −1: 일방통행 진행방향이 디지타이즈 반대 → 진행방향 우측(바깥)이 디지타이즈 좌측
    edge: 바깥 가장자리 → 음원선 거리(차로폭 배수) 함수. None = RLS-19 Bild 6(_d_edge), RLS-90 은 _d_edge_rls90"""
    edge = edge or _d_edge
    if single:
        return [(0.0, None)]                       # 1차로 양방향: 중앙 1개선, 방향 미상
    if oneway:
        off = -(nd_fwd * wl / 2 - edge(nd_fwd) * wl)                     # 우측통행 → 바깥 = 우측
        return [(-off, False)] if oneway_dir == -1 else [(off, True)]
    off_f = nd_fwd * wl - edge(nd_fwd) * wl
    off_b = nd_bwd * wl - edge(nd_bwd) * wl
    return [(-off_f, True), (+off_b, False)]       # 우측선 = 디지타이즈 방향, 좌측선 = 반대방향


def lane_source_offsets(nd_fwd, nd_bwd, wl, oneway, p_fwd=0.5, oneway_dir=1):
    """model.source_lines: lane — 차로마다 음원선(차로 중앙, 높이는 같은 0.5 m). 반환: [(횡오프셋, along, M 비율)].
    도로중심선 = 차도 전체의 가운데로 보고(수치지도 도로중심선) 전체 nd + nb 차로를 중심선 기준 대칭으로 펼침:
    오른쪽(−)부터 디지타이즈 방향 nd 개, 그 왼쪽(+)에 반대 방향 nb 개. 그래서 3 + 2 처럼 비대칭이면 방향 경계가
    중심선에서 반 차로 비켜남(방향별 1개선의 '중심선 = 방향 경계' 가정과 다름 — 대칭이면 같음).
    방향 몫(p, 1−p)을 그 방향 차로 수로 똑같이 나눔. 일방통행: 진행방향 차로만, 전량을 나눔.
    RLS-19 Bild 6 / RLS-90 Bild 10 의 방향별 1개선과 다른 프로젝트 선택(SoundPLAN '차로별 음원선' 비교용)"""
    n = nd_fwd + nd_bwd
    pos = [-(n / 2 - 0.5 - j) * wl for j in range(n)]   # j = 0 이 가장 오른쪽(디지타이즈 방향 기준)
    if oneway:
        along = oneway_dir != -1
        return [(o, along, 1.0 / n) for o in pos]
    return ([(o, True, p_fwd / nd_fwd) for o in pos[:nd_fwd]]
            + [(o, False, (1.0 - p_fwd) / nd_bwd) for o in pos[nd_fwd:]])


def detect_canyon(cfg, x, y, tx, ty, ftree, fgeoms, F, zroad):
    """식 (9) 입력 (hBeb, w): 양측 반사면이 도로축과 5° 이내 평행, 모든 탐침선에서 연속(폐쇄 파사드 근사)"""
    cc = cfg["model"]["canyon"]
    tol = math.cos(math.radians(cc["parallel_tol_deg"]))
    nx_, ny_ = -ty, tx
    side_d, side_h = [], []
    for side in (+1, -1):
        ds, hs = [], []
        for a in cc["probe_offsets"]:
            bx, by = x + a * tx, y + a * ty
            ray = LineString([(bx, by), (bx + side * cc["ray_length"] * nx_, by + side * cc["ray_length"] * ny_)])
            best = None
            for k in ftree.query(ray, predicate="intersects"):
                fr = F.iloc[k]
                if (side * nx_) * fr.nx + (side * ny_) * fr.ny >= 0:     # 도로를 향하지 않는 면
                    continue
                p = ray.intersection(fgeoms[k])
                if p.geom_type != "Point":
                    continue
                d = math.hypot(p.x - bx, p.y - by)
                if best is None or d < best[0]:
                    best = (d, k)
            if best is None:
                return None, None
            fr = F.iloc[best[1]]
            if abs((fr.x2 - fr.x1) * tx + (fr.y2 - fr.y1) * ty) / fr.L < tol:
                return None, None
            ds.append(best[0]); hs.append(fr.ztop - zroad)
        side_d.append(float(np.mean(ds))); side_h.append(float(min(hs)))
    w = side_d[0] + side_d[1]
    if w > cc["max_width"]:
        return None, None
    return max(min(side_h), 0.0), w


def build_sources(cfg, road, gnd, rast, F, J, reach):
    mc = cfg["model"]
    is90 = method_name(cfg) == "rls90"             # RLS-90 음원(rls90.py)
    by_ufid, by_name = I.load_traffic(cfg)
    fgeoms = [LineString([(a, b), (c, d)]) for a, b, c, d in zip(F.x1, F.y1, F.x2, F.y2)]
    ftree = STRtree(fgeoms) if len(fgeoms) else None
    Jxy = J[["x", "y"]].values if len(J) else np.zeros((0, 2))
    Jtype = J["type"].values if len(J) else np.array([])
    rows, troad, lines = [], [], []
    clx, cly = [], []                              # 음원점의 도로중심선 투영점(source_ground: road_profile 용)
    name_max = road.groupby("axis")["lanes"].max().to_dict()
    half = float(mc.get("grade_base", 30.0)) / 2.0
    drop_minor = mc.get("minor_roads", "exclude") == "exclude"
    per_lane = mc.get("source_lines", "direction") == "lane"
    for _, rd in road.iterrows():
        main = rd["lanes"] >= mc["main_min_lanes"]
        tr = I.traffic_for_road(cfg, rd, by_ufid, by_name, main, name_max.get(rd["axis"], 99))
        skip = drop_minor and not main and tr["traffic_src"] != "user"
        if skip:                                   # 교통량 자료 없는 골목길: 음원에서 뺌(도로선은 교차로·노면 보정에 남음)
            tr = {**tr, "traffic_src": "excluded_minor", "traffic_basis": "excluded_minor",
                  "std_filled": "", "traffic_std": ""}
        troad.append({"fid": rd["fid"], "ufid": rd["ufid"], "name": rd["rname"], "axis": rd["axis"],
                      "lanes": rd["lanes"], **tr})
        if skip:                                   # traffic_used.csv 에는 남겨 roads.gpkg 와 순서를 맞춤
            continue
        nd, nb, wl, rule = lane_layout(cfg, rd["lanes"], rd["width"], rd["oneway"],
                                       rd.get("lanes_fwd"), rd.get("lanes_bwd"))
        odir = -1 if (rd["oneway"] and _finite(rd.get("oneway_dir")) and int(rd.get("oneway_dir")) == -1) else 1
        offs = source_offsets(nd, nb, wl, rd["oneway"], single=(rule == "single_centre"), oneway_dir=odir,
                              edge=_d_edge_rls90 if is90 else None)
        p_fwd = float(tr["dir_split_fwd"])          # 방향별 배분(기본 0.5, 방향별 실측일 때만 다름)
        if per_lane and rule != "single_centre":    # 차로마다 음원선(model.source_lines: lane)
            plan = lane_source_offsets(nd, nb, wl, rd["oneway"], p_fwd, odir)
        else:
            # 음원선별 M 비율: 2개선 = 디지타이즈 방향 p, 반대 1−p / 1개선 = 1(양방향 중앙선은 아래에서 p, 1−p)
            fracs = {True: p_fwd, False: 1.0 - p_fwd} if len(offs) > 1 else None
            plan = [(off, along, fracs[along] if fracs else 1.0) for off, along in offs]
        lines.append({"fid": rd["fid"], "ufid": rd["ufid"], "name": rd["rname"], "lanes": rd["lanes"],
                      "width": rd["width"], "oneway": rd["oneway"], "lanes_fwd_used": nd, "lanes_bwd_used": nb,
                      "lane_rule": rule, "lane_width": wl, "offset_fwd": offs[0][0],
                      "offset_bwd": offs[1][0] if len(offs) > 1 else np.nan,
                      "oneway_dir_used": odir if rd["oneway"] else np.nan,
                      "source_lines": "lane" if (per_lane and rule != "single_centre") else "direction",
                      # 실제로 쓴 음원선 전부: 오프셋:방향(f/b/c):M 비율 — 차로별이면 여러 개
                      "lines_used": ";".join(f"{o:.3f}:{'c' if a is None else ('f' if a else 'b')}:{fr:.4f}"
                                             for o, a, fr in plan)})
        seg = mc["segment_main"] if main else mc["segment_minor"]
        my_j = sorted(reach.get(rd["fid"], set()))
        for off, along, frac in plan:
            try:
                gl = rd.geometry if off == 0 else rd.geometry.offset_curve(off)
            except Exception:
                continue
            for line in getattr(gl, "geoms", [gl]):
                L = line.length
                if L < 0.5:
                    continue
                # offset_curve 는 방향을 유지(Shapely 2). 안전을 위해 시작점 근접으로 방향 확인
                same_dir = I.dist(line.coords[0], rd.geometry.coords[0]) <= I.dist(line.coords[-1], rd.geometry.coords[0])
                k = max(1, int(math.ceil(L / seg)))
                for q in range(k):
                    s = (q + 0.5) * L / k
                    p = line.interpolate(s)
                    a = line.interpolate(max(s - 1, 0)); b = line.interpolate(min(s + 1, L))
                    tx, ty = b.x - a.x, b.y - a.y
                    tn = math.hypot(tx, ty) or 1.0
                    tx /= tn; ty /= tn
                    if not same_dir:
                        tx, ty = -tx, -ty                     # 디지타이즈 방향 기준 접선
                    # 종단경사: 원 도로선을 따라 ±half m (짧은 객체는 객체 전체) 구간의 높이차
                    cl = rd.geometry
                    sc = cl.project(p)
                    s0, s1 = max(sc - half, 0.0), min(sc + half, cl.length)
                    if tr.get("grade") is not None:
                        g = float(tr["grade"])
                    elif s1 - s0 >= 5.0:
                        pa, pb = cl.interpolate(s0), cl.interpolate(s1)
                        za, zb = rast.sample(gnd, [pa.x, pb.x], [pa.y, pb.y])
                        g = float((zb - za) / (s1 - s0) * 100.0)   # 디지타이즈 방향 기준 경사 %
                    else:
                        g = 0.0
                    if my_j:
                        dj = np.hypot(Jxy[my_j, 0] - p.x, Jxy[my_j, 1] - p.y)
                        kk = int(np.argmin(dj)); xk = float(dj[kk]); kt = Jtype[my_j][kk]
                    else:
                        xk, kt = 999.0, "other"
                    pc = cl.interpolate(sc); clx.append(pc.x); cly.append(pc.y)
                    rows.append(dict(x=p.x, y=p.y, l=L / k, tx=tx, ty=ty, g=g, along=along, main=main,
                                     frac=frac, fid=rd["fid"], x_knot=xk, kt=kt,
                                     lanes=rd["lanes"],
                                     name=rd["rname"]))
    S = pd.DataFrame(rows)
    T = pd.DataFrame(troad).set_index("fid")
    if mc.get("source_ground", "dtm_at_source") == "road_profile":
        # RLS-19 3.2 "0,5 m über dem Boden" 의 지면 = 노면: 도로중심선 종단 고도(경사 산정과 같은 평활 DTM)
        zg = rast.sample(gnd, np.asarray(clx), np.asarray(cly))
    else:                                          # 현행: 음원점 위치의 DTM
        zg = rast.sample(gnd, S.x.values, S.y.values)
    S["zg"] = zg
    S["z"] = zg + mc["source_height"]
    # 다중반사 할증 (간선만 탐색; 이면도로는 할증 0)
    hb, ww = [], []
    for r in S.itertuples():
        if mc["canyon"]["enabled"] and r.main and ftree is not None:
            h, w = detect_canyon(cfg, r.x, r.y, r.tx, r.ty, ftree, fgeoms, F, r.zg)
        else:
            h, w = None, None
        hb.append(h); ww.append(w)
    S["h_beb"] = hb; S["w"] = ww
    S["D_refl"] = [(rls90 if is90 else rls19).D_refl(h, w) for h, w in zip(hb, ww)]
    if is90:
        return _rls90_emission(S, T, lines)
    S["D_K"] = [rls19.D_K(x, kt) for x, kt in zip(S.x_knot, S.kt)]
    if cfg["input"]["junctions"].get("dk_rule", "nearest") == "nearest_signal":
        # 가까운 소도로 접속부(other)가 신호교차로 보정을 가리지 않도록: 신호·회전교차로 기준 값과 큰 쪽
        dk_s, x_s, k_s = dk_nearest_signal(S.x.values, S.y.values, Jxy, Jtype)
        S["D_K_nearest"] = S["D_K"]
        S["x_signal"] = x_s; S["kt_signal"] = k_s
        S["D_K"] = np.maximum(S["D_K"].values, dk_s)
    for per in ("day", "night"):
        lw = []
        for r in S.itertuples():
            t = T.loc[r.fid]
            M = t[f"M_{per}"] * r.frac
            if r.along is None:                       # 양방향 1개선: 디지타이즈 방향 p(+경사), 반대 1−p(−경사)
                pf = float(t["dir_split_fwd"])        # 기본 0.5 → M·0.5 = M/2 (비트 단위 동일)
                a = rls19.LW_prime(M * pf, *_dir_p(t, per, True), *_dir_speeds(t, True), t["surface"], r.g,
                                   r.D_K, r.D_refl)
                b = rls19.LW_prime(M * (1.0 - pf), *_dir_p(t, per, False), *_dir_speeds(t, False), t["surface"],
                                   -r.g, r.D_K, r.D_refl)
                lw.append(10 * math.log10(10 ** (0.1 * a) + 10 ** (0.1 * b)))
            else:
                g = r.g if r.along else -r.g          # 반대방향 차선: 경사 부호 반전
                lw.append(rls19.LW_prime(M, *_dir_p(t, per, r.along), *_dir_speeds(t, r.along), t["surface"], g,
                                         r.D_K, r.D_refl))
        S[f"LWp_{per}"] = lw
        S[f"LW_{per}"] = S[f"LWp_{per}"] + 10 * np.log10(S.l)        # 점음원 LW = LW' + 10 lg l
    return S, T.reset_index(), pd.DataFrame(lines)


def _dir_speeds(t, along):
    """음원선 방향의 (v_Pkw, v_Lkw). along True = 디지타이즈 방향(fwd), False = 반대(bwd).
    방향별 속도가 없는 이전 traffic_used(열 없음·빈칸)는 도로 공통 v_pkw·v_lkw"""
    d = "fwd" if along else "bwd"
    vp, vl = t.get(f"v_pkw_{d}"), t.get(f"v_lkw_{d}")
    vp = t["v_pkw"] if vp is None or vp != vp else vp
    vl = t["v_lkw"] if vl is None or vl != vl else vl
    return vp, vl


def _dir_p(t, per, along):
    """음원선 방향의 (p1, p2) %. along True = 디지타이즈 방향(fwd), False = 반대(bwd).
    방향별 비율이 없는 이전 traffic_used(열 없음·빈칸)는 도로 공통 p1·p2"""
    d = "fwd" if along else "bwd"
    out = []
    for k in (1, 2):
        v = t.get(f"p{k}_{per}_{d}")
        out.append(t[f"p{k}_{per}"] if v is None or v != v else v)
    return out[0], out[1]


def _p90(t, per):
    """RLS-90 maßgebender Lkw-Anteil p(> 2,8 t). inputs.traffic_for_road 가 입력 p → 입력 p1 + p2 → 표 3 순서로 채워 둠.
    p_day 가 없는 이전 traffic_used 만 p1 + p2(RLS-19 Lkw1·Lkw2 = 3,5 t 초과 — 근사, TECHNICAL_NOTES)"""
    v = t.get(f"p_{per}")
    if v is not None and v == v:
        return float(v)
    return float(t[f"p1_{per}"]) + float(t[f"p2_{per}"])


def _rls90_emission(S, T, Ln):
    """RLS-90 음원: 음원선별 L_m,E(식 6~9) → 등가 L_W′(rls90.LW_prime). 교차로 할증은 음원에 넣지 않음(K, 표 2 →
    run.py 가 수음점에 더함). M 은 두 바깥 차로에 나눔(식 7 설명, 방향별 실측 배분이 있으면 그 비율)"""
    S["D_K"] = 0.0
    for per in ("day", "night"):
        lw, lme = [], []
        for r in S.itertuples():
            t = T.loc[r.fid]
            M = t[f"M_{per}"] * r.frac
            p = _p90(t, per)
            if r.along is None:                       # 1차로 양방향 중앙선: 전량(디지타이즈 p, 반대 1−p 합)
                pf = float(t["dir_split_fwd"])
                a = rls90.LW_prime(M * pf, p, *_dir_speeds(t, True), t["surface"], r.g, r.D_refl)
                b = rls90.LW_prime(M * (1.0 - pf), p, *_dir_speeds(t, False), t["surface"], -r.g, r.D_refl)
                v = 10 * math.log10(10 ** (0.1 * a) + 10 ** (0.1 * b))
            else:                                     # D_Stg 는 |g| 라 부호 무관
                v = rls90.LW_prime(M, p, *_dir_speeds(t, r.along), t["surface"], r.g if r.along else -r.g, r.D_refl)
            lw.append(v)
            lme.append(v - r.D_refl - rls90.LW_OFFSET)
        S[f"Lm_E_{per}"] = lme                        # 기록용: 음원선 L_m,E (식 6, D_refl 제외)
        S[f"LWp_{per}"] = lw
        S[f"LW_{per}"] = S[f"LWp_{per}"] + 10 * np.log10(S.l)
    return S, T.reset_index(), pd.DataFrame(Ln)


# ====================================================================== 파사드 / 수음점
def facade_edges(cfg, bld, bidr, rast, region, min_len=2.0, gnd=None):
    """gnd 를 주면(model.building_ground: follow) 벽마다 그 벽을 따라간 지형으로 z0(최저)·ztop(평균 + H)"""
    rows = []
    for b in bld[bld.intersects(region)].itertuples():
        poly = orient(b.geometry, 1.0)
        for ring in [poly.exterior] + list(poly.interiors):
            c = list(ring.coords)
            for (x1, y1), (x2, y2) in zip(c[:-1], c[1:]):
                L = math.hypot(x2 - x1, y2 - y1)
                if L < min_len:
                    continue
                nx_, ny_ = (y2 - y1) / L, -(x2 - x1) / L               # 외향 법선
                party = 0
                for fr in (0.25, 0.5, 0.75):                            # 맞벽(공유벽) 판정: 3점 중 2점
                    mx = x1 + fr * (x2 - x1) + 0.7 * nx_; my = y1 + fr * (y2 - y1) + 0.7 * ny_
                    i, j = rast.ij(mx, my)
                    if 0 <= i < rast.ny and 0 <= j < rast.nx and bidr[i, j] >= 0 and bidr[i, j] != b.bid:
                        party += 1
                if party >= 2:
                    continue
                z0, zt = b.z0, b.ztop
                if gnd is not None:
                    k = max(2, int(math.ceil(L / (0.5 * rast.res))) + 1)
                    t = np.linspace(0.0, 1.0, k)
                    zw = rast.sample(gnd, x1 + t * (x2 - x1), y1 + t * (y2 - y1))
                    z0, zt = float(zw.min()), float(zw.mean()) + float(b.H)
                rows.append(dict(x1=x1, y1=y1, x2=x2, y2=y2, nx=nx_, ny=ny_, L=L, bid=b.bid,
                                 z0=z0, ztop=zt, H=b.H, floors=int(b.floors), kind=b.kind))
    return pd.DataFrame(rows)


def grid_receivers(cfg, gnd, bidr, rast):
    g = cfg["receivers"]["grid"]
    x0, y0, x1, y1 = grid_bbox(cfg)
    xs = np.arange(x0 + g["step"] / 2, x1, g["step"])
    ys = np.arange(y0 + g["step"] / 2, y1, g["step"])
    X, Y = np.meshgrid(xs, ys)
    i = ((rast.y0 - Y) / rast.res).astype(int); j = ((X - rast.x0) / rast.res).astype(int)
    inside = bidr[i, j] >= 0
    zg = rast.sample(gnd, X.ravel(), Y.ravel()).reshape(X.shape)
    return xs, ys, zg, inside


def facade_receivers(cfg, F, bld, gnd=None, rast=None):
    """gnd·rast 를 주면(model.building_ground: follow) 층 높이를 수음점이 붙은 벽 위치의 지형에서 쌓음(땅속 수음점 없음)"""
    fc = cfg["receivers"]["facade"]; mc = cfg["model"]
    cx, cy = center(cfg)
    excl = cfg["input"]["map"]["facade_exclude_kinds"]
    if fc.get("area", "grid") == "radius":                 # 중심에서 반경 안(건물 외곽선까지 거리)
        near = bld.geometry.distance(Point(cx, cy)) <= fc["radius"]
    else:                                                    # 격자 범위(사각형)에 조금이라도 걸치는 건물
        near = bld.geometry.intersects(box(*grid_bbox(cfg)))
    sel = set(bld[near & (~bld.kind.isin(excl))].bid)
    rows = []
    for fi, f in F[F.bid.isin(sel)].iterrows():
        k = max(1, int(math.ceil(f.L / fc["piece"])))
        for q in range(k):
            a, b_ = q / k, (q + 1) / k
            m = (a + b_) / 2
            x = f.x1 + m * (f.x2 - f.x1) + fc["offset"] * f.nx
            y = f.y1 + m * (f.y2 - f.y1) + fc["offset"] * f.ny
            zb, ztop_ = f.z0, f.ztop
            if gnd is not None:                              # 벽 위 지점(수음점 바로 안쪽)의 지형
                zb = float(rast.sample(gnd, [f.x1 + m * (f.x2 - f.x1)], [f.y1 + m * (f.y2 - f.y1)])[0])
                ztop_ = zb + f.H
            for fl in range(1, f.floors + 1):
                z = zb + fc["z_in_floor"] + (fl - 1) * mc["floor_height"]
                if z > ztop_:
                    break
                rows.append(dict(fid=fi, piece=q, a=a, b=b_, x=x, y=y, z=z, zg=zb, floor=fl,
                                 bid=f.bid, nx=f.nx, ny=f.ny))
    return pd.DataFrame(rows)


def barrier_pieces(cfg, gnd, rast):
    """방음벽 선을 piece(m) 이하 조각으로 나눈 표: barrier_id, x1, y1, x2, y2, h, kind, loss, z0, ztop, L.
    z0 = 조각 양 끝 지면고의 낮은 값(반사면 높이 범위용). 차폐는 경로와 만나는 점의 지면고 + h 를 씀(geometry)"""
    rows = []
    piece = float((cfg["input"].get("barriers") or {}).get("piece", 10.0))
    for b in I.load_barriers(cfg):
        for (xa, ya), (xb, yb) in zip(b["pts"][:-1], b["pts"][1:]):
            L = math.hypot(xb - xa, yb - ya)
            if L < 0.05:
                continue
            k = max(1, int(math.ceil(L / piece)))
            for q in range(k):
                x1 = xa + (xb - xa) * q / k; y1 = ya + (yb - ya) * q / k
                x2 = xa + (xb - xa) * (q + 1) / k; y2 = ya + (yb - ya) * (q + 1) / k
                z0 = float(np.min(rast.sample(gnd, [x1, x2], [y1, y2])))
                rows.append(dict(barrier_id=b["id"], x1=x1, y1=y1, x2=x2, y2=y2, h=b["h"],
                                 kind=b["kind"], loss=np.nan if b["loss"] is None else float(b["loss"]),
                                 z0=z0, ztop=z0 + b["h"], L=L / k))
    return pd.DataFrame(rows, columns=["barrier_id", "x1", "y1", "x2", "y2", "h", "kind", "loss", "z0", "ztop", "L"])


def barrier_faces(B, min_len=2.0):
    """방음벽의 양면 반사면(facades.csv 형식, bid = −1: 건물 아님, refl_kind·refl_db = 반사 종류·직접 지정 손실).
    반사 없음(none)·min_len 미만 조각은 만들지 않음. 반사 손실은 run.face_factors 가 방법별로 적용"""
    rows = []
    for b in B.itertuples():
        if b.kind == "none" or b.L < min_len:
            continue
        nx_, ny_ = (b.y2 - b.y1) / b.L, -(b.x2 - b.x1) / b.L
        for sgn, (xa, ya, xb, yb) in ((1.0, (b.x1, b.y1, b.x2, b.y2)), (-1.0, (b.x2, b.y2, b.x1, b.y1))):
            rows.append(dict(x1=xa, y1=ya, x2=xb, y2=yb, nx=sgn * nx_, ny=sgn * ny_, L=b.L, bid=-1,
                             z0=b.z0, ztop=b.ztop, H=b.h, floors=0, kind="방음벽", refl_kind=b.kind, refl_db=b.loss))
    return pd.DataFrame(rows)


def building_outlines(bld):
    """건물 외곽선 배열(측면 회절, sori/lateral.py): bxy[n, 2] = 고리마다 닫힌 좌표, 고리 사이 NaN 줄,
    bptr[건물 번호 + 1] = 건물별 시작 위치(건물 번호 = bid = 행 순서)"""
    rows, ptr = [], [0]
    for g in bld.geometry:
        k = 0
        for part in getattr(g, "geoms", [g]):
            for ring in [part.exterior] + list(part.interiors):
                c = np.asarray(ring.coords)[:, :2]
                if k:
                    rows.append(np.full((1, 2), np.nan))
                rows.append(c)
                k += len(c) + (1 if k else 0)
        ptr.append(ptr[-1] + k)
    return (np.vstack(rows) if rows else np.zeros((0, 2))), np.asarray(ptr, np.int64)


# ====================================================================== 메인
def main(cfg):
    out = cfg["project"]["output_dir"]
    import os
    os.makedirs(out, exist_ok=True)
    gb = grid_bbox(cfg)
    pc = cfg["propagation"]
    margin = pc["max_dist_main"] + 30.0
    region = box(gb[0] - margin, gb[1] - margin, gb[2] + margin, gb[3] + margin)
    bld = I.prepare_buildings(cfg, I.read_layer(cfg, "building", region))
    road = I.prepare_roads(cfg, I.read_layer(cfg, "road", region))
    ov = I.load_road_override(cfg)                 # 도로별 확인값(S3: 읽기·기록만, 계산 미반영)
    road, ov_info = I.apply_road_override(road, ov)
    cont = I.read_layer(cfg, "contour", region.buffer(100))
    spot = I.read_layer(cfg, "spot", region.buffer(100))
    rast = Raster(region.buffer(10).bounds, pc["dsm_res"])
    log.info("래스터 %d x %d (%.1f m)", rast.nx, rast.ny, rast.res)
    gnd, tinfo = build_terrain(cfg, cont, spot, rast)
    rsinfo = None
    if cfg["model"].get("road_surface", "dtm") == "flatten":
        gnd, rsinfo = flatten_road_surface(cfg, road, gnd, rast)
    pinfo = None
    z0 = None
    if cfg["model"].get("building_ground", "follow") == "pad":
        gnd, z0, pinfo = grade_building_pads(cfg, bld, road, gnd, rast)
    follow = cfg["model"].get("building_ground", "follow") == "follow"
    dsm, bidr = build_dsm(bld, gnd, rast, z0, follow)
    Gm = build_G(cfg, rast)
    refl_region = box(*gb).buffer(pc["reflection"]["r_facade"] + pc["reflection"]["r_source_main"])
    F = facade_edges(cfg, bld, bidr, rast, refl_region, gnd=gnd if follow else None)
    J, reach = junctions(cfg, road)
    S, T, Ln = build_sources(cfg, road, gnd, rast, F, J, reach)
    xs, ys, gz, inside = grid_receivers(cfg, gnd, bidr, rast)
    FR = (facade_receivers(cfg, F, bld, *((gnd, rast) if follow else ()))
          if cfg["receivers"]["facade"]["enabled"] else pd.DataFrame())
    BR = barrier_pieces(cfg, gnd, rast)                # 방음벽(선택 입력). 없으면 아래 파일·기록은 이전과 같음
    extra = {}
    bpath = os.path.join(out, "barriers_used.csv")
    if len(BR):
        BF = barrier_faces(BR)                         # 건물 반사면 뒤에 붙임(fid 유지). 음원 쪽 D_refl(협곡) 판정에는 안 씀
        if len(BF):
            F = pd.concat([F, BF], ignore_index=True)
        extra["bars"] = BR[["x1", "y1", "x2", "y2", "h"]].values.astype(np.float64)
        extra["bars_gid"] = pd.factorize(BR.barrier_id)[0].astype(np.int64)     # 조각 → 방음벽 번호(측면 회절)
        BR.to_csv(bpath, index=False, encoding="utf-8-sig")
    elif os.path.exists(bpath):                        # 방음벽을 뺀 뒤 다시 만들 때 이전 기록이 남지 않게
        os.remove(bpath)
    extra["bxy"], extra["bptr"] = building_outlines(bld)          # 측면 회절용 건물 외곽선(계산에 쓰지 않으면 무시)
    np.savez_compressed(os.path.join(out, "model_rasters.npz"), gnd=gnd, dsm=dsm, bid=bidr, G=Gm,
                        x0=rast.x0, y0=rast.y0, res=rast.res, gx=xs, gy=ys, gz=gz, ginside=inside, **extra)
    S.to_csv(os.path.join(out, "sources.csv"), index=False, encoding="utf-8-sig")
    T.drop(columns=["_key"], errors="ignore").to_csv(os.path.join(out, "traffic_used.csv"), index=False,
                                                     encoding="utf-8-sig")
    Ln.to_csv(os.path.join(out, "source_lines.csv"), index=False, encoding="utf-8-sig")   # 도로별 음원선 배치 근거
    F.to_csv(os.path.join(out, "facades.csv"), index_label="fid", encoding="utf-8-sig")
    FR.to_csv(os.path.join(out, "facade_receivers.csv"), index=False, encoding="utf-8-sig")
    J.to_csv(os.path.join(out, "junctions_used.csv"), index=False, encoding="utf-8-sig")
    keep = ["geometry", "bid", "bname", "kind", "floors", "H", "H_src", "z0", "ztop"] + \
        (["ztops"] if "ztops" in bld else [])
    bld[keep].to_file(os.path.join(out, "buildings.gpkg"), driver="GPKG")
    road[["geometry", "fid", "ufid", "rname", "axis", "lanes", "width", "oneway"]].to_file(
        os.path.join(out, "roads.gpkg"), driver="GPKG")
    nfb = int(T.traffic_src.str.startswith("fallback").sum())
    n_minor_x = int((T.traffic_src == "excluded_minor").sum())
    info = {"terrain": tinfo, "buildings": len(bld), "roads": len(road), "sources": len(S),
            "sources_main": int(S.main.sum()), "facade_edges": len(F), "junctions": len(J),
            "signal_junction_points": int((J.type == "signal").sum()) if len(J) else 0,
            "grid_receivers_per_height": int((~inside).sum()), "facade_receivers": len(FR),
            "roads_with_fallback_traffic": nfb,
            "roads_narrow_same_name": int((T.traffic_src == "fallback_narrow").sum()),
            "canyon_segments": int((S.D_refl > 0).sum()),
            "minor_roads": cfg["model"].get("minor_roads", "exclude"), "minor_roads_excluded": n_minor_x,
            "source_lines": cfg["model"].get("source_lines", "direction"),
            "emission": EMISSION_LABEL["rls90" if method_name(cfg) == "rls90" else "rls19"]}
    nstd = int((T.std_filled != "").sum())
    if nstd:                                       # 교통량 빈칸을 독일 기준표로 채운 도로가 있을 때만 기록
        std = I.traffic_std(cfg)
        info["traffic_defaults"] = {"standard": I.TRAFFIC_STD_LABEL[std], "roads": nstd,
                                    "dtv_by_lanes_roads": nfb, "note": I.TRAFFIC_STD_NOTE}
    if cfg["input"]["traffic"].get("missing_m", "error") != "error":             # 옵션을 켠 경우에만 기록
        est = T.M_day_from.str.startswith("est_") | T.M_night_from.str.startswith("est_")
        info["traffic_m_estimated"] = {"roads": int(est.sum()), "keys": sorted(set(T._key[est]))}
    if cfg["input"]["junctions"].get("dk_rule", "nearest") != "nearest" and "D_K_nearest" in S:   # 옵션을 켠 경우에만 기록
        up = S.D_K > S.D_K_nearest
        info["dk_rule"] = {"rule": cfg["input"]["junctions"]["dk_rule"], "sources_raised": int(up.sum()),
                           "dD_K_max": round(float((S.D_K - S.D_K_nearest).max()), 3) if len(S) else 0.0}
    if cfg["input"]["traffic"].get("oneway_name_traffic", "apply") != "apply":   # 옵션을 켠 경우에만 기록
        info["roads_oneway_name_fallback"] = int((T.traffic_src == "fallback_oneway").sum())
    if rsinfo is not None:                         # 옵션을 켠 경우에만 기록
        info["road_surface"] = rsinfo
    if pinfo is not None:
        info["building_ground"] = pinfo
    if len(BR):                                    # 방음벽을 넣은 경우에만 기록
        g = BR.groupby("barrier_id", sort=False)
        info["barriers"] = {"count": int(g.ngroups), "length_m": round(float(BR.L.sum()), 1),
                            "height_m": [float(BR.h.min()), float(BR.h.max())],
                            "types": {k: int(v) for k, v in g.kind.first().value_counts().items()},
                            "reflecting_faces": int((F.bid == -1).sum()),
                            "note": "두께 없는 회절 모서리(경로와 만나는 점의 지면 + 높이). 반사 손실: 반사형 = 건물 외벽, 반사 저감형·"
                                    "강한 반사 저감형 = RLS-19 표 8(3·5 dB)·RLS-90 표 7(−4·−8 dB), ISO 는 RLS-19 값을 ρ 로(프로젝트 선택), "
                                    "none = 반사 없음. 음원 쪽 다중 반사 가산값(D_refl) 판정에는 넣지 않음"}
    if ov is not None:                             # 확인값 파일을 지정한 경우에만 기록(기본 출력은 그대로)
        cols = ["fid", "ufid", "rname", "lanes", "oneway", "lanes_fwd", "lanes_bwd", "lanes_src", "lanes_basis",
                "oneway_dir", "oneway_dir_src", "oneway_dir_basis", "override_note"]
        road[cols].to_csv(os.path.join(out, "road_override_used.csv"), index=False, encoding="utf-8-sig")
        info["road_override"] = ov_info
    with open(os.path.join(out, "build_info.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)
    log.info("모델 구축 완료: %s", info)
    if nfb:
        log.warning("교통량 가정값(fallback) 사용 도로객체 %d개 — output/traffic_used.csv 의 traffic_src 확인", nfb)
    if n_minor_x:
        log.info("골목길(차로수 < %d) 중 교통량 자료가 없는 %d개 객체는 음원에서 뺌(model.minor_roads: exclude)",
                 cfg["model"]["main_min_lanes"], n_minor_x)
    if nstd:
        log.warning("교통량 자료가 없는 항목(M·p)을 표준값(%s)으로 채운 도로객체 %d개 — %s. traffic_used.csv 의 std_filled 열 참고",
                    I.TRAFFIC_STD_LABEL[I.traffic_std(cfg)], nstd, I.TRAFFIC_STD_NOTE)
    return info


def road_table(cfg):
    """계산 영역의 도로 목록(도로명 묶음 + 이름 없는 간선 UFID)과 자동 교차로 군집 표를 반환"""
    gb = grid_bbox(cfg)
    margin = cfg["propagation"]["max_dist_main"] + 30.0
    region = box(gb[0] - margin, gb[1] - margin, gb[2] + margin, gb[3] + margin)
    road = I.prepare_roads(cfg, I.read_layer(cfg, "road", region))
    road["len"] = road.length
    rows = []
    for nm, g in road[road.rname != ""].groupby("rname"):
        rows.append(dict(key=nm, key_type="name", lanes_min=int(g.lanes.min()), lanes_max=int(g.lanes.max()),
                         length=float(g.len.sum())))
    for _, r in road[(road.rname == "") & (road.lanes >= cfg["model"]["main_min_lanes"])].iterrows():
        rows.append(dict(key=r.ufid, key_type="ufid", lanes_min=int(r.lanes), lanes_max=int(r.lanes),
                         length=float(r.len)))
    R = pd.DataFrame(rows, columns=["key", "key_type", "lanes_min", "lanes_max", "length"])
    J, _ = junctions(cfg, road)
    if len(J):
        Jc = J.groupby("cluster").agg(x=("x", "mean"), y=("y", "mean"), type=("type", "first")).reset_index(drop=True)
    else:
        Jc = pd.DataFrame(columns=["x", "y", "type"])
    return R, Jc, road


def template(cfg, path_out):
    """교통량 입력 템플릿(도로명·UFID 목록)과 자동 교차로 목록을 내보냄"""
    R, Jc, _ = road_table(cfg)
    rt = cfg["input"]["traffic"]["default_road_type"]
    df = pd.DataFrame([dict(key=r.key, key_type=r.key_type, road_type=rt,
                            note=f"차로수 {r.lanes_min}~{r.lanes_max}, 연장 {r.length:.0f} m")
                       for r in R.itertuples()], columns=I.TRAFFIC_COLS)
    df.to_csv(path_out, index=False, encoding="utf-8-sig")
    jp = str(path_out).replace(".csv", "_junctions_auto.csv")
    if len(Jc):
        Jc.to_csv(jp, index=False, encoding="utf-8-sig")
    return path_out, jp
