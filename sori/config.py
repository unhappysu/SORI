# -*- coding: utf-8 -*-
"""설정 파일(YAML) 로더. 사용자가 지정하지 않은 항목은 DEFAULTS를 사용함.
※ Claude(Anthropic의 AI)를 활용하여 제작하였음."""
import copy
import logging
import math
from pathlib import Path

import yaml

log = logging.getLogger("sori")

DEFAULTS = {
    "project": {"name": "소음지도", "output_dir": "output",
                "scenario": "현황",                 # 파일명 규칙 {프로젝트}_{시나리오}-{ISO|RLS}_{주간|야간|주야}_{grid|facade|section}
                "file_naming": True},              # 규칙 이름 사본을 만듦(고정 이름 파일은 그대로)
    "crs": 5186,                                   # 계산 좌표계(EPSG). 수치지도 1:1,000 = 5186(중부원점 GRS80)
    "propagation_method": "iso9613_2",             # 전파 계산 방법: iso9613_2(ISO 9613-2, 현행) | rls19(RLS-19 3.5·3.6)
                                                   #   | rls90(RLS-90 — 음원·전파 모두 RLS-90, 1990)
    "input": {
        "map": {                                   # 국토지리정보원 수치지형도 v2.0 (SHP)
            "folders": ["data/map"],               # 압축 해제 폴더(하위 폴더까지 재귀 탐색)
            "encoding": "cp949",
            "layers": {
                "building": "*B0010000*.shp",
                "road": "*A0020000*.shp",
                "contour": "*F0010000*.shp",
                "spot": "*F0020000*.shp",
            },
            "fields": {
                "uid": "UFID",
                "building_floors": "층수",
                "building_height": None,           # 실제 높이(m) 필드가 있으면 지정 → 층수×층고 대신 사용
                "building_kind": "종류",
                "building_name": "명칭",
                "road_name": "도로명",
                "road_lanes": "차로수",
                "road_width": "도로폭",
                "road_oneway": "일방통행",
                "road_surface": "포장재질",
                "contour_z": "등고수치",
                "spot_z": "수치",
            },
            "oneway_values": ["일방통행"],
            "exclude_building_kinds": ["무벽건물"],
            "facade_exclude_kinds": ["가건물"],
            "min_building_area": 4.0,
        },
        "dem": {
            "path": None,                          # GDAL 래스터(GeoTIFF, IMG 등). 좌표계는 파일에서 읽음
            "use_as": "auxiliary",                 # auxiliary: 등고선 공백부 보완 / primary: DEM만 사용
            "aux_min_dist": 30.0,                  # 등고선에서 이 거리(m) 이상 떨어진 DEM 점만 보조로 사용
            "aux_spacing": 20.0,
        },
        "traffic": {
            "path": "traffic.csv",                 # 사용자 교통량 파일(템플릿: sori template)
            "default_road_type": "gemeinde",       # 도로유형(빈칸 보충용): RLS-19 표 2 / RLS-90 표 3(독일 기준)
            "fallback_dtv_by_lanes": {1: 1000, 2: 6000, 3: 12000, 4: 20000, 5: 30000, 6: 40000},  # SORI 가정값(표준에 없음)
            "default_speed_main": 50.0,            # 교통량 파일에 속도가 없을 때
            "default_speed_minor": 30.0,
            "name_min_lanes": 2,                   # 도로명 교통량은 이 차로수 이상 구간에만 적용(같은 이름의 좁은 접속로 제외)
            "oneway_name_traffic": "apply",        # 도로명 교통량을 같은 이름의 일방통행 객체에: apply(현행) | fallback(차로수 가정값)
            "missing_m": "error",                  # DTV 없이 M_day·M_night 중 하나만 있을 때: error(기본) | estimate(표준표 계수 비율로 보충)
        },
        "road_override": {
            "path": None,                          # 도로별 확인값 CSV: ufid, lanes_fwd, lanes_bwd, oneway_dir(+1/−1) …
        },                                         # (S3: 읽기·검증·기록만. 음원선 반영은 S4·S5)
        "junctions": {
            "path": None,                          # x,y,type(signal/roundabout/other) CSV
            "auto_signal_min_lanes": 4,            # 자동 판정: 차로수 ≥ 이 값인 서로 다른 도로축 2개 이상 → auto_type
            "auto_type": "signal",
            "cluster_radius": 25.0,
            "user_match_radius": 30.0,
            "dk_rule": "nearest",                  # D_K 기준 교차점: nearest = 그 음원선이 속한 가장 가까운 교차점(현행)
                                                   # nearest_signal = 위 값과 가장 가까운 signal·roundabout 교차점 기준 값 중 큰 쪽
        },
        "ground": {
            "method": "general",                   # general: ISO 9613-2 7.3.1 / alternative: 7.3.2(식 10)
            "default_G": 0.0,                      # 도시지역(포장) 기본 G
            "soft_polygons": None,                 # 녹지 등 다공성 지면 폴리곤(SHP/GPKG)
            "soft_G": 1.0,
        },
        "barriers": {
            "path": None,                          # 방음벽 CSV: barrier_id, height(지면 위 m), type(reflect/absorb/high/none), refl_loss(dB, 선택), coords("x y; x y; …")
            "piece": 10.0,                         # 반사면·지면고를 나누는 조각 길이(m)
        },
        "road_alias": {},                          # 같은 도로축의 이름 변경 구간 {"전농로25길": "전농로"}
        "surface_map": {"아스팔트": "ref", "아스팔트콘크리트": "ref", "콘크리트": "ref",
                        "블록": "pflaster_other", "소형고압블록": "pflaster_other",
                        "비포장": "pflaster_other"},
    },
    "model": {
        "floor_height": 3.0,
        "source_height": 0.5,                      # RLS-19 3.2
        "source_ground": "dtm_at_source",          # 음원 높이 기준 지면: dtm_at_source(현행, 음원점 위치 DTM) | road_profile(도로중심선 종단 고도)
        "road_surface": "flatten",                 # 차도 면 지형: flatten(기본, 도로폭 안 DTM 을 도로중심선 종단 고도로 — 노면 평탄) | dtm(등고선 DTM 그대로, 2026-11-05 이전 동작)
        "road_surface_smooth_m": 20.0,             # flatten: 중심선 종단 고도 이동평균 길이(m, 등고선 계단 완화)
        "terrain_smooth_m": 5.0,                   # 등고선 삼각망 계단 현상 완화(가우스 σ, m). 0 = 사용 안 함
        "grade_base": 30.0,                        # 종단경사 산정 구간 길이(m, 도로선을 따라)
        "segment_main": 3.0,
        "segment_minor": 5.0,
        "main_min_lanes": 2,                       # 차로수 ≥ 이 값 → 간선(반사 탐색거리 확대)
        "minor_roads": "exclude",                  # 골목길(차로수 < main_min_lanes, 수치지도 1차로) 음원: exclude(기본, 2026-11-08) = 교통량 자료가
                                                   # 없는 골목길은 음원에서 뺌(교통량 파일에 있으면 계산) | include = 차로수 가정 교통량으로 모두 계산(이전 동작)
                                                   # 빼도 도로선은 교차로 판정·차도 면 보정(flatten)에 그대로 씀
        # 음원선 배치(RLS-19 3.3.1, Bild 6). 확인값(input.road_override)이 있으면 그 방향별 차로 수가 우선
        "odd_lane_rule": "ceil",                   # 홀수 차로 양방향: ceil = 양쪽 ⌈n/2⌉(현행) | major_fwd = 디지타이즈 방향 ⌈n/2⌉, 반대 ⌊n/2⌋
        "source_lines": "direction",               # direction = 주행방향마다 1개선(RLS-19 Bild 6 / RLS-90 Bild 10, 현행)
                                                   # | lane = 차로마다 1개선(차로 중앙, 그 방향 M 을 차로 수로 나눔 — SoundPLAN 비교용)
        "lane_width": "auto",                      # auto = 도로폭/차로수를 lane_width_clip 범위로(현행) | 숫자 = 고정 차로폭(m)
        "lane_width_clip": [2.75, 3.5],
        "lane_width_default": 3.25,                # auto 에서 도로폭 값이 없을 때
        "canyon": {"enabled": True, "parallel_tol_deg": 5.0, "probe_offsets": [-5.0, 0.0, 5.0],
                   "max_width": 100.0, "ray_length": 100.0},
    },
    "propagation": {
        "bands_hz": [500],                         # ISO 9613-2 1절 비고 1: A가중 LW만 있으면 500 Hz 감쇠항 사용
        "spectrum_dBA": [0.0],                     # 대역별 A가중 상대레벨(옥타브 계산 시)
        "temperature_C": 15.0,
        "humidity_pct": 70.0,
        "pressure_kPa": 101.325,
        "alpha_dB_per_km": None,                   # 직접 지정 시 ISO 9613-1 계산 대신 사용
        "max_dist_main": 300.0,
        "max_dist_minor": 150.0,
        "profile_step": 1.0,
        "dsm_res": 1.0,
        "barrier": {"C2": 20.0, "dz_max_single": 20.0, "dz_max_double": 25.0},
        "reflection": {"rho": 0.8,                 # iso9613_2: 반사계수 ρ (ISO 식 20)
                       "D_RV": 0.5,                # rls19: 반사 손실 dB/회 (RLS-19 표 8, 건물 외벽·반사성 방음벽)
                       "D_E": -1.0,                # rls90: 반사 보정 dB/회 (RLS-90 표 7: 매끈한 외벽 −1, 요철 외벽 −2)
                       # 반사 차수: 「소음지도의 작성방법」 별표 2 제8호 "3차 이상" 원칙(RLS-19 3.6 은 1·2차)
                       # 0 ~ 6. 4차 이상은 3차와 같은 탐색 규칙의 일반화(solver._order_n) — 계산 시간이 차수마다 크게 늘어남
                       "max_order_grid": 3, "max_order_facade": 3, "max_order_point": 3,
                       # 수음점이 놓인 외벽의 반사 제외: last = 마지막 반사만(RLS-19 1장 Immissionsort 주석, 기본)
                       # all = 모든 반사 순서(2026-10-29 이전 동작, 원래 코드 기준선 비교용)
                       "own_facade": "last",
                       "r_facade": 100.0, "r_source_main": 200.0, "r_source_minor": 50.0,
                       "r_second": 50.0,
                       # 반사면–음원 거리 제한(m). None = 사용 안 함(기본, 이전 동작). 값을 주면 SoundPLAN 'max. reflection
                       # distance to source' 처럼 경로의 모든 반사면이 음원에서 이 거리 안·수음점에서 r_facade 안이어야 함
                       "r_refl_source": None},
        "lateral": {                               # 측면 회절(ISO 9613-2 식 13, sori/lateral.py) — iso9613_2 방법에만 적용
            "enabled": True,                       # 기본 켬(2026-11-13 사용자 결정): 건물·방음벽에 막힌 직접음 경로에 좌우 측면 경로를
                                                   # 더함. rls19·rls90 방법은 켜 있어도 적용하지 않음(원문 규정). false = 2026-11-13 이전 동작
            "max_grow": 6,                         # 측면 경로를 다른 건물이 막을 때 그 건물까지 감싸 다시 돌아가는 횟수
        },
        "cmet_C0": 0.0,                            # ISO 9613-2 8절 기상 보정 C0(dB, 식 22) — 0 = 순풍 조건 L_AT(DW)(기본, 이전과 같음).
                                                   # 0 초과면 장기 평균 L_AT(LT) = L_AT(DW) − C_met. iso9613_2 방법만(지역 기상 통계로 정함, 보통 0~5)
        "skip_radius": 1.5,                        # 래스터 이산화 오차 보정 반경(m)
        "segment_check": True,                     # RLS-19 3.2 주석 l ≤ s/2 진단(결과값 불변) → segment_check.csv
        "terms_csv": False,                        # 점 수음점의 감쇠항별 중간값 → path_terms.csv (직접음·1차 반사, terms.py)
    },
    "receivers": {
        "center": None,                            # [x, y] 계산 중심(교차로 등)
        "grid": {"enabled": True, "bbox": None, "size": [480.0, 520.0], "step": 5.0, "heights": [4.0],
                 # 정밀 격자(경계 세분, sori/refine.py): 칸이 건물·간선 노면 띠에 닿거나 네 꼭짓점 값 범위가 threshold_dB 를
                 # 넘으면 반 간격으로 더 계산, levels 번(2 → step/4). 규칙 격자 결과(grid_result.csv)는 그대로, 그림·GeoTIFF 만 세밀
                 "refine": {"enabled": False, "threshold_dB": 2.0, "levels": 2}},
        "facade": {"enabled": True,
                   "area": "grid",                 # 외벽 계산 건물: grid = 격자 범위(사각형)에 걸치는 건물 전부(2026-11-16 기본),
                                                   #   radius = 격자 중심에서 radius(m) 안 건물(2026-11-15 이전 동작)
                   "radius": 230.0, "piece": 10.0, "z_in_floor": 2.8, "offset": 0.05},
        "points": {"path": None,                   # 점 수음점 CSV: point_id, x, y, h(지면 위 높이 m) — 현장 측정점 비교용
                   "default_height": None,         # h 가 빈 행에 쓸 높이(m). None 이면 그 행은 건너뜀
                   "road_contrib": True,           # 점마다 도로별 기여 → point_road_contrib.csv(경로 보기와 같은 계산, 반사 3차까지)
                   "measured": None},              # 측정값 CSV(측정−계산 차이, summary.measured_compare):
                                                   #   point_id, period(day/night), Leq  또는  point_id, LrT_meas, LrN_meas
    },
    "plots": {"font": None, "dpi": 130, "targets_3d": None, "radius_3d": 105.0,
              "elev_3d": 22.0, "azim_3d": 135.0},
}

# 없앤 설정 항목(이전 설정 파일에 있어도 오류 없이 무시). 2026-11-15: 소음 관리기준 대조(용도지역·기준값) 삭제
OBSOLETE = ("input.zones", "plots.limits")


class Cfg(dict):
    """dict + 점(.) 접근"""
    def __getattr__(self, k):
        try:
            v = self[k]
        except KeyError as e:
            raise AttributeError(k) from e
        return Cfg(v) if isinstance(v, dict) and not isinstance(v, Cfg) else v


def _merge(base, upd):
    out = copy.deepcopy(base)
    for k, v in (upd or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load(path):
    path = Path(path).resolve()
    with open(path, encoding="utf-8") as f:
        user = yaml.safe_load(f) or {}
    c = _merge(DEFAULTS, user)
    base = path.parent

    def rp(p):
        if p is None:
            return None
        p = Path(p)
        return str(p if p.is_absolute() else (base / p).resolve())

    m = c["input"]["map"]
    m["folders"] = [rp(f) for f in m["folders"]]
    for k in ("dem", "traffic", "junctions", "road_override", "barriers"):
        c["input"][k]["path"] = rp(c["input"][k]["path"])
    c["input"]["ground"]["soft_polygons"] = rp(c["input"]["ground"]["soft_polygons"])
    for k in OBSOLETE:                                    # 없앤 항목: 읽기는 하되 쓰지 않음(이전 설정 파일 호환)
        sec, name = k.split(".")
        if name in (c.get(sec) or {}):
            log.warning("설정 %s 는 더 이상 쓰지 않음(소음 기준 대조 기능 삭제, 2026-11-15) — 무시", k)
    c["receivers"]["points"]["path"] = rp(c["receivers"]["points"]["path"])
    c["receivers"]["points"]["measured"] = rp(c["receivers"]["points"].get("measured"))
    c["project"]["output_dir"] = rp(c["project"]["output_dir"])
    c["input"]["traffic"]["fallback_dtv_by_lanes"] = {
        int(k): float(v) for k, v in c["input"]["traffic"]["fallback_dtv_by_lanes"].items()}
    c["_config_path"] = str(path)
    _validate(c)
    return Cfg(c)


def _validate(c):
    p = c["propagation"]
    if len(p["bands_hz"]) != len(p["spectrum_dBA"]):
        raise ValueError("propagation.bands_hz 와 spectrum_dBA 의 길이가 같아야 함")
    if p["alpha_dB_per_km"] is not None and len(p["alpha_dB_per_km"]) != len(p["bands_hz"]):
        raise ValueError("alpha_dB_per_km 길이가 bands_hz 와 같아야 함")
    from .propagation import METHODS
    meth = c.get("propagation_method") or "iso9613_2"
    if meth not in METHODS:
        raise ValueError(f"propagation_method 는 {' 또는 '.join(METHODS)}")
    if meth in ("rls19", "rls90") and len(p["bands_hz"]) != 1:
        raise ValueError(f"propagation_method: {meth} 는 A 가중 단일값 계산 — bands_hz 를 여러 개 지정할 수 없음")
    if float(p["reflection"].get("D_E", -1.0)) > 0.0:
        raise ValueError("RLS-90 표 7: reflection.D_E 는 0 이하(dB, 예: −1)")
    from .geometry import MAXO
    for k in ("max_order_grid", "max_order_facade", "max_order_point"):
        if not 0 <= int(p["reflection"][k]) <= MAXO:
            raise ValueError(f"propagation.reflection.{k} 는 0 ~ {MAXO} (4차 이상은 계산 시간이 차수마다 크게 늘어남)")
    if c["receivers"]["facade"].get("area", "grid") not in ("grid", "radius"):
        raise ValueError("receivers.facade.area 는 grid(격자 범위 안 건물) 또는 radius(중심에서 반경 안 건물)")
    rf = c["receivers"]["grid"].get("refine") or {}
    if not 0.1 <= float(rf.get("threshold_dB", 2.0)) <= 20.0:
        raise ValueError("receivers.grid.refine.threshold_dB 는 0.1 ~ 20 dB")
    if not 1 <= int(rf.get("levels", 2)) <= 3:
        raise ValueError("receivers.grid.refine.levels 는 1 ~ 3 (단계마다 간격이 반으로)")
    lat = p.get("lateral") or {}                  # rls19·rls90 은 켜 있어도 적용 안 함(run.lateral_on) — 기본 켬이라 오류로 막지 않음
    if not 0.0 <= float(p.get("cmet_C0", 0.0) or 0.0) <= 10.0:
        raise ValueError("propagation.cmet_C0 는 0 ~ 10 dB (ISO 9613-2 비고 22: 실제 값은 보통 0 ~ 5 dB)")
    if not 0 <= int(lat.get("max_grow", 6)) <= 20:
        raise ValueError("propagation.lateral.max_grow 는 0 ~ 20")
    if p["reflection"].get("own_facade", "last") not in ("last", "all"):
        raise ValueError("propagation.reflection.own_facade 는 last 또는 all")
    rrs = p["reflection"].get("r_refl_source")
    if rrs is not None and not float(rrs) > 0.0:
        raise ValueError("propagation.reflection.r_refl_source 는 null(사용 안 함) 또는 0 보다 큰 거리(m)")
    if float(p["reflection"]["D_RV"]) < 0.0:
        raise ValueError("RLS-19 표 8: reflection.D_RV 는 0 이상(dB)")
    if not 0.2 < float(p["reflection"]["rho"]) <= 1.0:
        raise ValueError("ISO 9613-2 7.5: 반사계수 rho 는 0.2 초과 1 이하")
    if c["input"]["traffic"]["missing_m"] not in ("error", "estimate"):
        raise ValueError("input.traffic.missing_m 는 error 또는 estimate")
    if c["input"]["traffic"]["oneway_name_traffic"] not in ("apply", "fallback"):
        raise ValueError("input.traffic.oneway_name_traffic 는 apply 또는 fallback")
    if c["input"]["junctions"]["dk_rule"] not in ("nearest", "nearest_signal"):
        raise ValueError("input.junctions.dk_rule 는 nearest 또는 nearest_signal")
    m = c["model"]
    if m["minor_roads"] not in ("exclude", "include"):
        raise ValueError("model.minor_roads 는 exclude 또는 include")
    if m["road_surface"] not in ("dtm", "flatten"):
        raise ValueError("model.road_surface 는 dtm 또는 flatten")
    if m["source_ground"] not in ("dtm_at_source", "road_profile"):
        raise ValueError("model.source_ground 는 dtm_at_source 또는 road_profile")
    if m["odd_lane_rule"] not in ("ceil", "major_fwd"):
        raise ValueError("model.odd_lane_rule 는 ceil 또는 major_fwd")
    if m.get("source_lines", "direction") not in ("direction", "lane"):
        raise ValueError("model.source_lines 는 direction 또는 lane")
    if m["lane_width"] not in ("auto", None) and not (2.0 <= float(m["lane_width"]) <= 5.0):
        raise ValueError("model.lane_width 는 auto 또는 2.0~5.0 m")
    if c["input"]["ground"]["method"] not in ("general", "alternative"):
        raise ValueError("ground.method 는 general 또는 alternative")
    r = c["receivers"]
    if r["center"] is None and r["grid"]["bbox"] is None:
        raise ValueError("receivers.center 또는 receivers.grid.bbox 중 하나는 지정해야 함")


def grid_bbox(c):
    g = c["receivers"]["grid"]
    if g["bbox"] is not None:
        return tuple(float(v) for v in g["bbox"])
    cx, cy = c["receivers"]["center"]
    w, h = g["size"]
    return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


def facade_reach(c):
    """격자 중심에서 외벽 계산 건물이 놓일 수 있는 거리(m, 그림·3D 보기 범위용): area = radius 면 radius,
    grid 면 중심에서 격자 모서리까지"""
    fc = c["receivers"]["facade"]
    if fc.get("area", "grid") == "radius":
        return float(fc["radius"])
    b, (cx, cy) = grid_bbox(c), center(c)
    return max(math.hypot(x - cx, y - cy) for x in (b[0], b[2]) for y in (b[1], b[3]))


def center(c):
    if c["receivers"]["center"] is not None:
        return tuple(float(v) for v in c["receivers"]["center"])
    b = grid_bbox(c)
    return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
