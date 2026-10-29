# -*- coding: utf-8 -*-
"""사용자 입력 데이터 로드: 수치지도(SHP), DEM(래스터), 교통량(CSV), 교차로(CSV), 다공성 지면(SHP/GPKG)
※ Claude(Anthropic의 AI)를 활용하여 제작하였음."""
import glob
import logging
import math
import os
from collections import defaultdict

import geopandas as gpd
import numpy as np
import pandas as pd

from . import rls19

log = logging.getLogger("sori")


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
                "p1_night", "p2_night", "v_pkw", "v_lkw", "surface", "grade", "note", "basis",
                "dir_split_fwd", "dir_basis"]
TRAFFIC_BASIS = ("measured", "assumed")     # basis·dir_basis 열: 실측 / 가정. 빈칸 → unspecified
_dir_warned = set()


def _dir_warn(key, msg):
    if (key, msg) not in _dir_warned:
        _dir_warned.add((key, msg))
        log.warning("교통량 '%s': %s", key, msg)


def _check_csv_rows(text, path):
    """머리글보다 칸이 많은 행 검사. 값이 든 초과 칸(따옴표 없는 쉼표 등)은 줄 번호와 함께 오류,
    빈 초과 칸(끝의 쉼표)만 있으면 잘라 낸 텍스트를 반환, 문제 없으면 None"""
    import csv
    import io
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return None
    n = len(rows[0]); bad, trimmed = [], False
    for i, r in enumerate(rows[1:], start=2):
        if len(r) > n:
            if any(c.strip() for c in r[n:]):
                bad.append((i, len(r)))
            else:
                trimmed = True
    if bad:
        lst = ", ".join(f"{i}행({k}칸)" for i, k in bad[:10]) + (" …" if len(bad) > 10 else "")
        raise RuntimeError(f"CSV '{path}': 머리글은 {n}칸인데 칸이 더 많은 행이 있음 — {lst}. "
                           "메모 등에 쉼표가 있으면 그 칸을 큰따옴표로 묶을 것(예: \"가상, 중심 근처\")")
    if not trimmed:
        return None
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows([rows[0]] + [r[:n] for r in rows[1:]])
    return out.getvalue()


def read_csv_any(path):
    for enc in ("utf-8-sig", "cp949"):
        try:
            with open(path, encoding=enc) as f:
                text = f.read()
        except UnicodeDecodeError:
            continue
        log.info("CSV 읽기: %s (인코딩 %s)", os.path.basename(str(path)), "utf-8" if enc == "utf-8-sig" else enc)
        fixed = _check_csv_rows(text, path)
        if fixed is None:                          # 일반적인 파일: 이전과 같은 방식으로 읽음
            return pd.read_csv(path, encoding=enc, dtype=str, keep_default_na=False)
        import io
        return pd.read_csv(io.StringIO(fixed), dtype=str, keep_default_na=False)
    raise RuntimeError(f"CSV 인코딩을 읽을 수 없음: {path}")


def _missing_m_message(items):
    """items: [(key, 빠진 항목, 입력된 항목)] → 한 번에 보여 줄 오류 문구"""
    lines = [f"교통량 파일: DTV 없이 M_day·M_night 중 하나만 입력된 행 {len(items)}개"]
    lines += [f"  - '{k}': {miss} 없음 ({given} 만 입력)" for k, miss, given in items]
    lines.append("해결: 빠진 M 을 입력하거나, DTV 를 입력하거나, 설정 input.traffic.missing_m: estimate "
                 "(RLS-19 표 2 의 그 도로유형 주·야 계수 비율로 보충)")
    return "\n".join(lines)


def _m_partial(rec):
    """DTV 없이 M 한쪽만 있으면 (빠진 항목, 입력된 항목), 아니면 None"""
    if str(rec.get("DTV", "")).strip():
        return None
    d, n = str(rec.get("M_day", "")).strip(), str(rec.get("M_night", "")).strip()
    if d and not n:
        return "M_night", "M_day"
    if n and not d:
        return "M_day", "M_night"
    return None


def load_traffic(cfg):
    p = cfg["input"]["traffic"]["path"]
    if not p or not os.path.exists(p):
        log.warning("교통량 파일 없음(%s) → 모든 도로에 차로수 기준 가정값 사용", p)
        return {}, {}
    df = read_csv_any(p)
    miss = [c for c in ("key",) if c not in df.columns]
    if miss:
        raise RuntimeError(f"교통량 파일에 필수 열 {miss} 없음. 템플릿: python -m sori template")
    for c in TRAFFIC_COLS:
        if c not in df.columns:
            df[c] = ""
    by_ufid, by_name = {}, {}
    partial = []                                   # DTV 없이 M 한쪽만 있는 행
    for _, r in df.iterrows():
        key = r["key"].strip()
        if not key or key.startswith("#"):
            continue
        rec = {c: r[c].strip() for c in TRAFFIC_COLS}
        if not (rec["DTV"] or rec["M_day"] or rec["M_night"]):
            log.warning("교통량 '%s': DTV/M 값이 비어 있어 무시(가정값 사용)", key)
            continue
        pm = _m_partial(rec)
        if pm:
            partial.append((key, *pm))
        kt = rec["key_type"].lower() or "name"
        (by_ufid if kt == "ufid" else by_name)[key] = rec
    if partial:
        if cfg["input"]["traffic"].get("missing_m", "error") == "estimate":
            for k, miss, given in partial:
                log.warning("교통량 '%s': DTV 없음 → %s 를 %s × (RLS-19 표 2 계수 비율)로 추정(missing_m: estimate)",
                            k, miss, given)
        else:
            raise RuntimeError(_missing_m_message(partial))
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
    via_ufid = rec is not None
    src = "user" if rec else "fallback"
    if rec is None:
        rec = by_name.get(row["rname"]) or by_name.get(row["axis"])
        src = "user" if rec else "fallback"
        nml = int(tcfg.get("name_min_lanes", 2))
        if rec is not None and row["lanes"] < nml <= name_max_lanes:
            rec, src = None, "fallback_narrow"
        # 도로명 교통량(양방향 합)은 같은 이름의 일방통행 객체(램프·측도 등)에 배정하지 않는 옵션. UFID 지정은 우선
        if rec is not None and row.get("oneway", False) and tcfg.get("oneway_name_traffic", "apply") == "fallback":
            rec, src = None, "fallback_oneway"
    rtype = (rec.get("road_type") if rec else "") or tcfg["default_road_type"]
    if rtype not in rls19.TAB2:
        raise RuntimeError(f"road_type '{rtype}' 은 {list(rls19.TAB2)} 중 하나여야 함")
    t2 = rls19.TAB2[rtype]
    dtv = _f(rec, "DTV")
    dtv_in = dtv if rec is not None else None      # 입력 DTV(가정값 도로·DTV 빈칸은 None) → traffic_used.csv DTV_in
    m_in = {per: _f(rec, f"M_{per}") for per in ("day", "night")}
    if rec is not None and dtv is None:
        if m_in["day"] is None and m_in["night"] is None:
            raise RuntimeError(f"교통량 '{rec['key']}': DTV 또는 M_day·M_night 를 입력해야 함")
        pm = _m_partial(rec)
        if pm and tcfg.get("missing_m", "error") != "estimate":
            raise RuntimeError(_missing_m_message([(rec["key"], *pm)]))
    if rec is None:
        dtv = tcfg["fallback_dtv_by_lanes"].get(int(min(max(row["lanes"], 1), 6)), 1000.0)
    basis = (rec.get("basis", "") if rec else "").strip().lower()
    if rec is not None and basis and basis not in TRAFFIC_BASIS:
        raise RuntimeError(f"교통량 '{rec['key']}': basis '{basis}' 는 {TRAFFIC_BASIS} 중 하나(또는 빈칸)여야 함")
    out, m_from = {}, {}
    for per in ("day", "night"):
        Mf, p1s, p2s = t2[per]
        M = m_in[per]
        if M is not None:
            m_from[per] = "direct"                                  # 직접 입력
        elif dtv is not None:
            M = Mf * dtv; m_from[per] = "DTV_x_tab2"                 # DTV × 표 2 계수
        else:                                                        # missing_m: estimate — 반대 시간대 M × 표 2 계수 비
            other = "night" if per == "day" else "day"
            M = m_in[other] * Mf / t2[other][0]; m_from[per] = f"est_from_M_{other}_tab2"
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
    # 방향별 배분(RLS-19 3.3.1 기본은 M 을 두 음원선에 절반씩). 방향별 실측(dir_basis = measured)이고
    # UFID 로 지정한 경우에만 적용 — 같은 도로명의 객체들은 디지타이즈 방향이 같다는 보장이 없음
    split = _f(rec, "dir_split_fwd")
    dbas = (rec.get("dir_basis", "") if rec else "").strip().lower()
    if rec is not None and dbas and dbas not in TRAFFIC_BASIS:
        raise RuntimeError(f"교통량 '{rec['key']}': dir_basis '{dbas}' 는 {TRAFFIC_BASIS} 중 하나(또는 빈칸)여야 함")
    if split is not None and not 0.0 <= split <= 1.0:
        raise RuntimeError(f"교통량 '{rec['key']}': dir_split_fwd={split} 는 0~1 이어야 함")
    p, dsrc = 0.5, "default_half"
    ow = bool(row.get("oneway", False))
    if split is not None:
        if ow:
            dsrc = "oneway_ignored"; _dir_warn(rec["key"], "일방통행 도로에는 dir_split_fwd 를 쓰지 않음(전량)")
        elif not via_ufid:
            dsrc = "name_key_ignored"; _dir_warn(rec["key"], "도로명 지정 행의 dir_split_fwd 는 무시(UFID 로 지정할 것)")
        elif dbas != "measured":
            dsrc = "not_measured_ignored"; _dir_warn(rec["key"], "dir_basis 가 measured 가 아니어서 dir_split_fwd 무시(50:50)")
        else:
            p, dsrc = split, "measured"
    out["dir_split_fwd"] = 1.0 if ow else p
    out["dir_split_src"] = dsrc
    out["DTV_in"] = dtv_in
    out["_key"] = rec["key"] if rec is not None else ""   # 내부용(build_info 집계). traffic_used.csv 에는 쓰지 않음
    return out


# ------------------------------------------------------------------ 도로별 확인값 (roads_override.csv)
OVERRIDE_BASIS = ("checked", "assumed")     # 지도·현장 확인 / 가정. 빈칸 → unspecified


def _int_cell(v, name, key):
    s = str(v).strip()
    if s == "" or s.lower() == "nan":
        return None
    try:
        f = float(s)
    except ValueError:
        raise RuntimeError(f"도로 확인값 '{key}': {name}='{s}' 는 정수여야 함")
    if not math.isfinite(f) or f != int(f):
        raise RuntimeError(f"도로 확인값 '{key}': {name}='{s}' 는 정수여야 함")
    return int(f)


def load_road_override(cfg):
    """도로별 확인값 CSV 읽기·검증 → {ufid: dict}. 경로 미지정이면 None.
    열(대소문자 무관): ufid(필수), lanes_fwd, lanes_bwd, oneway_dir(+1/−1), lanes_basis, oneway_dir_basis, note.
    그 밖의 열은 무시. lanes_fwd = 디지타이즈 방향 차로 수, lanes_bwd = 반대 방향.
    oneway_dir: +1 = 디지타이즈 방향이 진행방향, −1 = 반대."""
    p = (cfg["input"].get("road_override") or {}).get("path")
    if not p:
        return None
    if not os.path.exists(p):
        raise RuntimeError(f"도로 확인값 파일 없음: {p}")
    df = read_csv_any(p)
    cols = {str(c).strip().lower(): c for c in df.columns}
    if "ufid" not in cols:
        raise RuntimeError("도로 확인값 파일에 ufid 열이 없음")
    out = {}
    for _, r in df.iterrows():
        get = lambda k: str(r[cols[k]]).strip() if k in cols else ""
        u = get("ufid")
        if not u or u.startswith("#"):
            continue
        if u in out:
            raise RuntimeError(f"도로 확인값: UFID '{u}' 중복")
        lf, lb = _int_cell(get("lanes_fwd"), "lanes_fwd", u), _int_cell(get("lanes_bwd"), "lanes_bwd", u)
        od = _int_cell(get("oneway_dir"), "oneway_dir", u)
        if lf is not None and lf < 1:
            raise RuntimeError(f"도로 확인값 '{u}': lanes_fwd 는 1 이상")
        if lb is not None and lb < 0:
            raise RuntimeError(f"도로 확인값 '{u}': lanes_bwd 는 0 이상")
        if od is not None and od not in (1, -1):
            raise RuntimeError(f"도로 확인값 '{u}': oneway_dir 는 +1 또는 −1")
        rec = {"lanes_fwd": lf, "lanes_bwd": lb, "oneway_dir": od, "note": get("note")}
        for k, has in (("lanes_basis", lf is not None or lb is not None), ("oneway_dir_basis", od is not None)):
            b = get(k).lower()
            if b and b not in OVERRIDE_BASIS:
                raise RuntimeError(f"도로 확인값 '{u}': {k} '{b}' 는 {OVERRIDE_BASIS} 중 하나(또는 빈칸)여야 함")
            rec[k] = (b or "unspecified") if has else ""
        out[u] = rec
    log.info("도로 확인값 파일: %d건", len(out))
    return out


def apply_road_override(road, ov):
    """도로 객체에 확인값 열을 붙임(계산에는 아직 쓰지 않음 — 음원선 반영은 S4·S5).
    추가 열: lanes_fwd, lanes_bwd(없으면 NaN), lanes_src(override/map), lanes_basis,
             oneway_dir(없으면 NaN), oneway_dir_src(override/digitized_assumed, 양방향은 빈칸), oneway_dir_basis,
             override_note.  반환: (road, 요약 dict)"""
    road = road.copy()
    ov = ov or {}
    n = len(road)
    lf = np.full(n, np.nan); lb = np.full(n, np.nan); od = np.full(n, np.nan)
    lsrc, lbas, osrc, obas, note = [], [], [], [], []
    warn = defaultdict(list)
    seen = set()
    for i, (u, lanes, ow) in enumerate(zip(road["ufid"], road["lanes"], road["oneway"])):
        o = ov.get(u)
        if o is not None:
            seen.add(u)
        has_l = o is not None and (o["lanes_fwd"] is not None or o["lanes_bwd"] is not None)
        if has_l:
            lf[i] = np.nan if o["lanes_fwd"] is None else o["lanes_fwd"]
            lb[i] = np.nan if o["lanes_bwd"] is None else o["lanes_bwd"]
            if o["lanes_fwd"] is None or (o["lanes_bwd"] is None and not ow):
                warn["한쪽 방향 차로 수만 지정"].append(u)
            elif not ow and o["lanes_fwd"] + o["lanes_bwd"] != lanes:
                warn["lanes_fwd + lanes_bwd ≠ 수치지도 차로수"].append(u)
            if ow and (o["lanes_bwd"] or 0) > 0:
                warn["일방통행 도로에 lanes_bwd > 0"].append(u)
            if ow and o["lanes_fwd"] is not None and o["lanes_fwd"] != lanes:
                warn["일방통행 lanes_fwd ≠ 수치지도 차로수"].append(u)
        lsrc.append("override" if has_l else "map")
        lbas.append(o["lanes_basis"] if has_l else "")
        if o is not None and o["oneway_dir"] is not None:
            od[i] = o["oneway_dir"]
            if not ow:
                warn["양방향 도로에 oneway_dir 지정(무시 예정)"].append(u)
        osrc.append(("override" if np.isfinite(od[i]) else "digitized_assumed") if ow else "")
        obas.append(o["oneway_dir_basis"] if (ow and np.isfinite(od[i])) else "")
        note.append(o["note"] if o is not None else "")
    road["lanes_fwd"] = lf; road["lanes_bwd"] = lb; road["lanes_src"] = lsrc; road["lanes_basis"] = lbas
    road["oneway_dir"] = od; road["oneway_dir_src"] = osrc; road["oneway_dir_basis"] = obas
    road["override_note"] = note
    unmatched = sorted(set(ov) - seen)
    if unmatched:
        warn["계산 영역 도로에 없는 UFID"] = unmatched
    for k, v in warn.items():
        log.warning("도로 확인값: %s %d건 (예: %s)", k, len(v), ", ".join(v[:3]))
    info = {"rows": len(ov), "matched_ufid": len(seen), "unmatched_ufid": len(unmatched),
            "roads_lanes_override": int(sum(s == "override" for s in lsrc)),
            "roads_oneway_dir_override": int(np.isfinite(od).sum()),
            "warnings": {k: len(v) for k, v in warn.items()}}
    return road, info


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
