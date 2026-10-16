# -*- coding: utf-8 -*-
"""사용자 입력 데이터 로드: 수치지도(SHP), DEM(래스터), 교통량(CSV), 교차로(CSV), 다공성 지면(SHP/GPKG)
※ Claude(Anthropic의 AI)를 활용하여 제작하였음."""
import glob
import logging
import math
import os

import geopandas as gpd
import numpy as np
import pandas as pd

from . import rls19

log = logging.getLogger("noisemap")


# ------------------------------------------------------------------ 수치지도
def find_files(folders, pattern):
    out = []
    for fd in folders:
        out += glob.glob(os.path.join(fd, "**", pattern), recursive=True)
    return sorted(set(out))


def read_layer(cfg, key, region=None):
    m = cfg["input"]["map"]
    files = find_files(m["folders"], m["layers"][key])
    if not files:
        log.warning("수치지도 레이어 '%s' (%s) 파일 없음", key, m["layers"][key])
        return None
    frames = []
    for f in files:
        g = gpd.read_file(f, encoding=m["encoding"])
        if g.crs is None:
            g = g.set_crs(cfg["crs"])
        elif g.crs.to_epsg() != cfg["crs"]:
            g = g.to_crs(cfg["crs"])
        g["_src"] = os.path.basename(os.path.dirname(f))
        frames.append(g)
    g = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=cfg["crs"])
    uid = m["fields"]["uid"]
    if uid in g.columns:
        g = g.drop_duplicates(uid)
    if region is not None:
        g = g[g.intersects(region)]
    log.info("레이어 %-8s : 파일 %d개, 객체 %d개", key, len(files), len(g))
    return g.reset_index(drop=True)


def _num(s, default):
    return pd.to_numeric(s, errors="coerce").fillna(default)


def prepare_buildings(cfg, bld):
    m = cfg["input"]["map"]; F = m["fields"]
    if bld is None or len(bld) == 0:
        raise RuntimeError("건물 레이어가 없음 — input.map 설정을 확인")
    if F["building_kind"] in bld.columns:
        bld = bld[~bld[F["building_kind"]].isin(m["exclude_building_kinds"])]
    bld = bld.explode(index_parts=False)
    bld = bld[bld.geometry.type == "Polygon"]
    bld = bld[bld.area > m["min_building_area"]].copy()
    floors = _num(bld[F["building_floors"]], 1).clip(lower=1) if F["building_floors"] in bld else 1
    bld["floors"] = np.asarray(floors, dtype=int) if not np.isscalar(floors) else 1
    if F["building_height"] and F["building_height"] in bld.columns:
        h = _num(bld[F["building_height"]], np.nan)
        bld["H"] = np.where(h > 0, h, bld["floors"] * cfg["model"]["floor_height"])
        bld["H_src"] = np.where(h > 0, "height_field", "floors")
    else:
        bld["H"] = bld["floors"] * cfg["model"]["floor_height"]
        bld["H_src"] = "floors"
    bld["kind"] = bld[F["building_kind"]].astype(str) if F["building_kind"] in bld else ""
    bld["bname"] = bld[F["building_name"]].fillna("").astype(str) if F["building_name"] in bld else ""
    bld = bld.reset_index(drop=True)
    bld["bid"] = np.arange(len(bld))
    return bld


def prepare_roads(cfg, road):
    m = cfg["input"]["map"]; F = m["fields"]
    if road is None or len(road) == 0:
        raise RuntimeError("도로중심선 레이어가 없음 — input.map 설정을 확인")
    road = road.explode(index_parts=False)
    road = road[road.geometry.type == "LineString"].copy().reset_index(drop=True)
    road["lanes"] = _num(road.get(F["road_lanes"]), 1).clip(lower=1).astype(int)
    road["width"] = _num(road.get(F["road_width"]), 0.0)
    nm = road[F["road_name"]] if F["road_name"] in road else pd.Series([""] * len(road))
    road["rname"] = nm.fillna("").astype(str).str.strip()
    alias = cfg["input"]["road_alias"] or {}
    road["axis"] = [alias.get(n, n) if n else f"_ufid_{u}" for n, u in
                    zip(road["rname"], road.get(F["uid"], pd.Series(range(len(road)))))]
    ow = road[F["road_oneway"]] if F["road_oneway"] in road else pd.Series([""] * len(road))
    road["oneway"] = ow.isin(m["oneway_values"]).values
    sf = road[F["road_surface"]] if F["road_surface"] in road else pd.Series([""] * len(road))
    road["surface_attr"] = sf.fillna("").astype(str)
    road["ufid"] = road[F["uid"]].astype(str) if F["uid"] in road else road.index.astype(str)
    road["fid"] = np.arange(len(road))
    return road


# ------------------------------------------------------------------ DEM
def sample_dem(path, xs, ys, crs):
    """DEM 래스터를 (xs, ys)[계산 CRS]에서 쌍선형 표본추출. 무효값은 NaN"""
    import rasterio
    from pyproj import Transformer
    from scipy.ndimage import map_coordinates
    with rasterio.open(path) as ds:
        arr = ds.read(1).astype(np.float64)
        if ds.nodata is not None:
            arr[arr == ds.nodata] = np.nan
        if ds.crs is None:
            raise RuntimeError(f"DEM '{path}' 에 좌표계 정보가 없음")
        T = Transformer.from_crs(crs, ds.crs, always_xy=True)
        X, Y = T.transform(np.asarray(xs, float), np.asarray(ys, float))
        inv = ~ds.transform
        col, row = inv * (X, Y)
    valid = np.isfinite(arr)
    a0 = np.where(valid, arr, 0.0)
    v = map_coordinates(a0, [np.asarray(row) - 0.5, np.asarray(col) - 0.5], order=1, mode="nearest")
    w = map_coordinates(valid.astype(float), [np.asarray(row) - 0.5, np.asarray(col) - 0.5],
                        order=1, mode="constant", cval=0.0)
    out = np.where(w > 0.99, v, np.nan)
    out[(out < -500) | (out > 9000)] = np.nan
    return out


# ------------------------------------------------------------------ 교통량
TRAFFIC_COLS = ["key", "key_type", "road_type", "DTV", "M_day", "M_night", "p1_day", "p2_day",
                "p1_night", "p2_night", "v_pkw", "v_lkw", "surface", "grade", "note", "basis"]
TRAFFIC_BASIS = ("measured", "assumed")     # basis 열: 실측 / 가정. 빈칸 → unspecified


def read_csv_any(path):
    for enc in ("utf-8-sig", "cp949"):
        try:
            return pd.read_csv(path, encoding=enc, dtype=str, keep_default_na=False)
        except UnicodeDecodeError:
            continue
    raise RuntimeError(f"CSV 인코딩을 읽을 수 없음: {path}")


def load_traffic(cfg):
    p = cfg["input"]["traffic"]["path"]
    if not p or not os.path.exists(p):
        log.warning("교통량 파일 없음(%s) → 모든 도로에 차로수 기준 가정값 사용", p)
        return {}, {}
    df = read_csv_any(p)
    miss = [c for c in ("key",) if c not in df.columns]
    if miss:
        raise RuntimeError(f"교통량 파일에 필수 열 {miss} 없음. 템플릿: python -m noisemap template")
    for c in TRAFFIC_COLS:
        if c not in df.columns:
            df[c] = ""
    by_ufid, by_name = {}, {}
    for _, r in df.iterrows():
        key = r["key"].strip()
        if not key or key.startswith("#"):
            continue
        rec = {c: r[c].strip() for c in TRAFFIC_COLS}
        if not (rec["DTV"] or rec["M_day"] or rec["M_night"]):
            log.warning("교통량 '%s': DTV/M 값이 비어 있어 무시(가정값 사용)", key)
            continue
        kt = rec["key_type"].lower() or "name"
        (by_ufid if kt == "ufid" else by_name)[key] = rec
    log.info("교통량 파일: 도로명 %d건, UFID %d건", len(by_name), len(by_ufid))
    return by_ufid, by_name


def _f(rec, k):
    v = rec.get(k, "") if rec else ""
    try:
        return float(v) if str(v).strip() != "" else None
    except ValueError:
        raise RuntimeError(f"교통량 파일 값 오류: {k}='{v}'")


def traffic_for_road(cfg, row, by_ufid, by_name, main, name_max_lanes=99):
    """도로 1개 객체의 주/야 M, p1, p2, 속도, 노면 결정. 반환 dict + 출처 표기
    도로명으로 찾은 교통량은 name_min_lanes 이상 구간에만 적용(그 도로명에 다차로 구간이 있을 때).
    같은 이름의 좁은 접속로·측도에 간선 교통량이 배정되는 것을 막기 위함. UFID 지정은 항상 우선."""
    tcfg = cfg["input"]["traffic"]
    rec = by_ufid.get(row["ufid"])
    src = "user" if rec else "fallback"
    if rec is None:
        rec = by_name.get(row["rname"]) or by_name.get(row["axis"])
        src = "user" if rec else "fallback"
        nml = int(tcfg.get("name_min_lanes", 2))
        if rec is not None and row["lanes"] < nml <= name_max_lanes:
            rec, src = None, "fallback_narrow"
    rtype = (rec.get("road_type") if rec else "") or tcfg["default_road_type"]
    if rtype not in rls19.TAB2:
        raise RuntimeError(f"road_type '{rtype}' 은 {list(rls19.TAB2)} 중 하나여야 함")
    t2 = rls19.TAB2[rtype]
    dtv = _f(rec, "DTV")
    if dtv is None and rec and _f(rec, "M_day") is None:
        raise RuntimeError(f"교통량 '{rec['key']}': DTV 또는 M_day/M_night 중 하나는 입력해야 함")
    if rec is None:
        dtv = tcfg["fallback_dtv_by_lanes"].get(int(min(max(row["lanes"], 1), 6)), 1000.0)
    basis = (rec.get("basis", "") if rec else "").strip().lower()
    if rec is not None and basis and basis not in TRAFFIC_BASIS:
        raise RuntimeError(f"교통량 '{rec['key']}': basis '{basis}' 는 {TRAFFIC_BASIS} 중 하나(또는 빈칸)여야 함")
    out, m_from = {}, {}
    for per in ("day", "night"):
        Mf, p1s, p2s = t2[per]
        M = _f(rec, f"M_{per}")
        m_from[per] = "direct" if M is not None else "DTV_x_tab2"     # 직접 입력 / DTV × 표 2 계수
        if M is None:
            M = Mf * dtv
        p1 = _f(rec, f"p1_{per}"); p2 = _f(rec, f"p2_{per}")
        out[f"M_{per}"] = M
        out[f"p1_{per}"] = p1s if p1 is None else p1
        out[f"p2_{per}"] = p2s if p2 is None else p2
    vdef = tcfg["default_speed_main"] if main else tcfg["default_speed_minor"]
    out["v_pkw"] = _f(rec, "v_pkw") or vdef
    out["v_lkw"] = _f(rec, "v_lkw") or out["v_pkw"]
    sf = (rec.get("surface") if rec else "") or cfg["input"]["surface_map"].get(row["surface_attr"], "ref")
    if sf not in rls19.SURFACES:
        raise RuntimeError(f"surface '{sf}' 는 {rls19.SURFACES} 중 하나여야 함")
    out["surface"] = sf
    out["grade"] = _f(rec, "grade")                    # 사용자 지정 종단경사 % (디지타이즈 방향 기준). 없으면 지형에서 산정
    out["road_type"] = rtype
    out["traffic_src"] = src
    # 출처 기록(결과값에는 영향 없음): 실측/가정 구분, M 산정 방식, 메모
    out["traffic_basis"] = "fallback_lanes" if rec is None else (basis or "unspecified")
    out["M_day_from"] = m_from["day"]
    out["M_night_from"] = m_from["night"]
    out["note"] = rec.get("note", "") if rec else ""
    return out


# ------------------------------------------------------------------ 교차로 / 지면
def load_junctions(cfg):
    p = cfg["input"]["junctions"]["path"]
    if not p:
        return None
    if not os.path.exists(p):
        raise RuntimeError(f"교차로 파일 없음: {p}")
    df = read_csv_any(p)
    for c in ("x", "y", "type"):
        if c not in df.columns:
            raise RuntimeError("교차로 파일 열: x, y, type(signal/roundabout/other)")
    df["x"] = df["x"].astype(float); df["y"] = df["y"].astype(float)
    bad = set(df["type"]) - set(rls19.KKT)
    if bad:
        raise RuntimeError(f"교차로 type 오류 {bad}")
    return df


def load_soft_ground(cfg):
    p = cfg["input"]["ground"]["soft_polygons"]
    if not p:
        return None
    g = gpd.read_file(p)
    if g.crs is None:
        g = g.set_crs(cfg["crs"])
    return g.to_crs(cfg["crs"])


def dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])
