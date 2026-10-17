# -*- coding: utf-8 -*-
"""
계산 모델 구축
  1) 레이어 로드  2) 지형(DTM)·건물(DSM/ID)·지면계수(G) 래스터
  3) 도로 위상(교차로) → 음원선(RLS-19 3.3.1, Bild 6) → 분할(3.2) → LW'(식 4~9)
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

from . import rls19, inputs as I
from .config import grid_bbox, center

log = logging.getLogger("noisemap")


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


def build_dsm(bld, gnd, rast):
    bidr = features.rasterize(((g, int(b)) for g, b in zip(bld.geometry, bld.bid)),
                              out_shape=(rast.ny, rast.nx), transform=rast.transform,
                              fill=-1, dtype="int32", all_touched=False)
    z0 = []
    for g in bld.geometry:                         # 건물 기저 = 외곽선 정점 지형고의 최솟값
        c = np.asarray(g.exterior.coords)
        z0.append(float(np.min(rast.sample(gnd, c[:, 0], c[:, 1]))))
    bld["z0"] = z0
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


def source_offsets(nd_fwd, nd_bwd, wl, oneway, single=False, oneway_dir=1):
    """RLS-19 3.3.1 / Bild 6. 반환: [(횡오프셋 m, 디지타이즈 방향과 같은가)] (+ = 디지타이즈 진행방향 좌측)
    양방향: 도로중심선을 방향 경계로 간주(미확인 가정 — TECHNICAL_NOTES). 일방통행: 중심선 = 차도 중앙.
    oneway_dir = −1: 일방통행 진행방향이 디지타이즈 반대 → 진행방향 우측(바깥)이 디지타이즈 좌측"""
    if single:
        return [(0.0, None)]                       # 1차로 양방향: 중앙 1개선, 방향 미상
    if oneway:
        off = -(nd_fwd * wl / 2 - _d_edge(nd_fwd) * wl)                  # 우측통행 → 바깥 = 우측
        return [(-off, False)] if oneway_dir == -1 else [(off, True)]
    off_f = nd_fwd * wl - _d_edge(nd_fwd) * wl
    off_b = nd_bwd * wl - _d_edge(nd_bwd) * wl
    return [(-off_f, True), (+off_b, False)]       # 우측선 = 디지타이즈 방향, 좌측선 = 반대방향


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
    by_ufid, by_name = I.load_traffic(cfg)
    fgeoms = [LineString([(a, b), (c, d)]) for a, b, c, d in zip(F.x1, F.y1, F.x2, F.y2)]
    ftree = STRtree(fgeoms) if len(fgeoms) else None
    Jxy = J[["x", "y"]].values if len(J) else np.zeros((0, 2))
    Jtype = J["type"].values if len(J) else np.array([])
    rows, troad, lines = [], [], []
    clx, cly = [], []                              # 음원점의 도로중심선 투영점(source_ground: road_profile 용)
    name_max = road.groupby("axis")["lanes"].max().to_dict()
    half = float(mc.get("grade_base", 30.0)) / 2.0
    for _, rd in road.iterrows():
        main = rd["lanes"] >= mc["main_min_lanes"]
        tr = I.traffic_for_road(cfg, rd, by_ufid, by_name, main, name_max.get(rd["axis"], 99))
        troad.append({"fid": rd["fid"], "ufid": rd["ufid"], "name": rd["rname"], "axis": rd["axis"],
                      "lanes": rd["lanes"], **tr})
        nd, nb, wl, rule = lane_layout(cfg, rd["lanes"], rd["width"], rd["oneway"],
                                       rd.get("lanes_fwd"), rd.get("lanes_bwd"))
        odir = -1 if (rd["oneway"] and _finite(rd.get("oneway_dir")) and int(rd.get("oneway_dir")) == -1) else 1
        offs = source_offsets(nd, nb, wl, rd["oneway"], single=(rule == "single_centre"), oneway_dir=odir)
        lines.append({"fid": rd["fid"], "ufid": rd["ufid"], "name": rd["rname"], "lanes": rd["lanes"],
                      "width": rd["width"], "oneway": rd["oneway"], "lanes_fwd_used": nd, "lanes_bwd_used": nb,
                      "lane_rule": rule, "lane_width": wl, "offset_fwd": offs[0][0],
                      "offset_bwd": offs[1][0] if len(offs) > 1 else np.nan,
                      "oneway_dir_used": odir if rd["oneway"] else np.nan})
        p_fwd = float(tr["dir_split_fwd"])          # 방향별 배분(기본 0.5, 방향별 실측일 때만 다름)
        # 음원선별 M 비율: 2개선 = 디지타이즈 방향 p, 반대 1−p / 1개선 = 1(양방향 중앙선은 아래에서 p, 1−p)
        fracs = {True: p_fwd, False: 1.0 - p_fwd} if len(offs) > 1 else None
        seg = mc["segment_main"] if main else mc["segment_minor"]
        my_j = sorted(reach.get(rd["fid"], set()))
        for off, along in offs:
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
                                     frac=(fracs[along] if fracs else 1.0), fid=rd["fid"], x_knot=xk, kt=kt,
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
    S["D_refl"] = [rls19.D_refl(h, w) for h, w in zip(hb, ww)]
    S["D_K"] = [rls19.D_K(x, kt) for x, kt in zip(S.x_knot, S.kt)]
    for per in ("day", "night"):
        lw = []
        for r in S.itertuples():
            t = T.loc[r.fid]
            M = t[f"M_{per}"] * r.frac
            args = (t[f"p1_{per}"], t[f"p2_{per}"], t["v_pkw"], t["v_lkw"], t["surface"])
            if r.along is None:                       # 양방향 1개선: 디지타이즈 방향 p(+경사), 반대 1−p(−경사)
                pf = float(t["dir_split_fwd"])        # 기본 0.5 → M·0.5 = M/2 (비트 단위 동일)
                a = rls19.LW_prime(M * pf, *args, r.g, r.D_K, r.D_refl)
                b = rls19.LW_prime(M * (1.0 - pf), *args, -r.g, r.D_K, r.D_refl)
                lw.append(10 * math.log10(10 ** (0.1 * a) + 10 ** (0.1 * b)))
            else:
                g = r.g if r.along else -r.g          # 반대방향 차선: 경사 부호 반전
                lw.append(rls19.LW_prime(M, *args, g, r.D_K, r.D_refl))
        S[f"LWp_{per}"] = lw
        S[f"LW_{per}"] = S[f"LWp_{per}"] + 10 * np.log10(S.l)        # 점음원 LW = LW' + 10 lg l
    return S, T.reset_index(), pd.DataFrame(lines)


# ====================================================================== 파사드 / 수음점
def facade_edges(cfg, bld, bidr, rast, region, min_len=2.0):
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
                rows.append(dict(x1=x1, y1=y1, x2=x2, y2=y2, nx=nx_, ny=ny_, L=L, bid=b.bid,
                                 z0=b.z0, ztop=b.ztop, H=b.H, floors=int(b.floors), kind=b.kind))
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


def facade_receivers(cfg, F, bld):
    fc = cfg["receivers"]["facade"]; mc = cfg["model"]
    cx, cy = center(cfg)
    excl = cfg["input"]["map"]["facade_exclude_kinds"]
    sel = set(bld[(bld.geometry.distance(Point(cx, cy)) <= fc["radius"]) & (~bld.kind.isin(excl))].bid)
    rows = []
    for fi, f in F[F.bid.isin(sel)].iterrows():
        k = max(1, int(math.ceil(f.L / fc["piece"])))
        for q in range(k):
            a, b_ = q / k, (q + 1) / k
            m = (a + b_) / 2
            x = f.x1 + m * (f.x2 - f.x1) + fc["offset"] * f.nx
            y = f.y1 + m * (f.y2 - f.y1) + fc["offset"] * f.ny
            for fl in range(1, f.floors + 1):
                z = f.z0 + fc["z_in_floor"] + (fl - 1) * mc["floor_height"]
                if z > f.ztop:
                    break
                rows.append(dict(fid=fi, piece=q, a=a, b=b_, x=x, y=y, z=z, zg=f.z0, floor=fl,
                                 bid=f.bid, nx=f.nx, ny=f.ny))
    return pd.DataFrame(rows)


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
    dsm, bidr = build_dsm(bld, gnd, rast)
    Gm = build_G(cfg, rast)
    refl_region = box(*gb).buffer(pc["reflection"]["r_facade"] + pc["reflection"]["r_source_main"])
    F = facade_edges(cfg, bld, bidr, rast, refl_region)
    J, reach = junctions(cfg, road)
    S, T, Ln = build_sources(cfg, road, gnd, rast, F, J, reach)
    xs, ys, gz, inside = grid_receivers(cfg, gnd, bidr, rast)
    FR = facade_receivers(cfg, F, bld) if cfg["receivers"]["facade"]["enabled"] else pd.DataFrame()
    np.savez_compressed(os.path.join(out, "model_rasters.npz"), gnd=gnd, dsm=dsm, bid=bidr, G=Gm,
                        x0=rast.x0, y0=rast.y0, res=rast.res, gx=xs, gy=ys, gz=gz, ginside=inside)
    S.to_csv(os.path.join(out, "sources.csv"), index=False)
    T.drop(columns=["_key"], errors="ignore").to_csv(os.path.join(out, "traffic_used.csv"), index=False,
                                                     encoding="utf-8-sig")
    Ln.to_csv(os.path.join(out, "source_lines.csv"), index=False, encoding="utf-8-sig")   # 도로별 음원선 배치 근거
    F.to_csv(os.path.join(out, "facades.csv"), index_label="fid")
    FR.to_csv(os.path.join(out, "facade_receivers.csv"), index=False)
    J.to_csv(os.path.join(out, "junctions_used.csv"), index=False)
    keep = ["geometry", "bid", "bname", "kind", "floors", "H", "H_src", "z0", "ztop"]
    bld[keep].to_file(os.path.join(out, "buildings.gpkg"), driver="GPKG")
    road[["geometry", "fid", "ufid", "rname", "axis", "lanes", "width", "oneway"]].to_file(
        os.path.join(out, "roads.gpkg"), driver="GPKG")
    nfb = int(T.traffic_src.str.startswith("fallback").sum())
    info = {"terrain": tinfo, "buildings": len(bld), "roads": len(road), "sources": len(S),
            "sources_main": int(S.main.sum()), "facade_edges": len(F), "junctions": len(J),
            "signal_junction_points": int((J.type == "signal").sum()) if len(J) else 0,
            "grid_receivers_per_height": int((~inside).sum()), "facade_receivers": len(FR),
            "roads_with_fallback_traffic": nfb,
            "roads_narrow_same_name": int((T.traffic_src == "fallback_narrow").sum()),
            "canyon_segments": int((S.D_refl > 0).sum())}
    if cfg["input"]["traffic"].get("missing_m", "error") != "error":             # 옵션을 켠 경우에만 기록
        est = T.M_day_from.str.startswith("est_") | T.M_night_from.str.startswith("est_")
        info["traffic_m_estimated"] = {"roads": int(est.sum()), "keys": sorted(set(T._key[est]))}
    if cfg["input"]["traffic"].get("oneway_name_traffic", "apply") != "apply":   # 옵션을 켠 경우에만 기록
        info["roads_oneway_name_fallback"] = int((T.traffic_src == "fallback_oneway").sum())
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
        Jc.to_csv(jp, index=False)
    return path_out, jp
