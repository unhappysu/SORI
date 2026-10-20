# -*- coding: utf-8 -*-
"""설정 파일(YAML) 로더. 사용자가 지정하지 않은 항목은 DEFAULTS를 사용함.
※ Claude(Anthropic의 AI)를 활용하여 제작하였음."""
import copy
from pathlib import Path

import yaml

DEFAULTS = {
    "project": {"name": "소음지도", "output_dir": "output"},
    "crs": 5186,                                   # 계산 좌표계(EPSG). 수치지도 1:1,000 = 5186(중부원점 GRS80)
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
            "path": "traffic.csv",                 # 사용자 교통량 파일(템플릿: noisemap template)
            "default_road_type": "gemeinde",       # RLS-19 표 2 도로유형(빈칸 보충용)
            "fallback_dtv_by_lanes": {1: 1000, 2: 6000, 3: 12000, 4: 20000, 5: 30000, 6: 40000},
            "default_speed_main": 50.0,            # 교통량 파일에 속도가 없을 때
            "default_speed_minor": 30.0,
            "name_min_lanes": 2,                   # 도로명 교통량은 이 차로수 이상 구간에만 적용(같은 이름의 좁은 접속로 제외)
            "oneway_name_traffic": "apply",        # 도로명 교통량을 같은 이름의 일방통행 객체에: apply(현행) | fallback(차로수 가정값)
            "missing_m": "error",                  # DTV 없이 M_day·M_night 중 하나만 있을 때: error(기본) | estimate(표 2 계수 비율로 보충)
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
        },
        "ground": {
            "method": "general",                   # general: ISO 9613-2 7.3.1 / alternative: 7.3.2(식 10)
            "default_G": 0.0,                      # 도시지역(포장) 기본 G
            "soft_polygons": None,                 # 녹지 등 다공성 지면 폴리곤(SHP/GPKG)
            "soft_G": 1.0,
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
        "terrain_smooth_m": 5.0,                   # 등고선 삼각망 계단 현상 완화(가우스 σ, m). 0 = 사용 안 함
        "grade_base": 30.0,                        # 종단경사 산정 구간 길이(m, 도로선을 따라)
        "segment_main": 3.0,
        "segment_minor": 5.0,
        "main_min_lanes": 2,                       # 차로수 ≥ 이 값 → 간선(반사 탐색거리 확대)
        # 음원선 배치(RLS-19 3.3.1, Bild 6). 확인값(input.road_override)이 있으면 그 방향별 차로 수가 우선
        "odd_lane_rule": "ceil",                   # 홀수 차로 양방향: ceil = 양쪽 ⌈n/2⌉(현행) | major_fwd = 디지타이즈 방향 ⌈n/2⌉, 반대 ⌊n/2⌋
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
        "reflection": {"rho": 0.8, "max_order_grid": 1, "max_order_facade": 2, "max_order_point": 2,
                       "r_facade": 100.0, "r_source_main": 200.0, "r_source_minor": 50.0,
                       "r_second": 50.0},
        "skip_radius": 1.5,                        # 래스터 이산화 오차 보정 반경(m)
        "segment_check": True,                     # RLS-19 3.2 주석 l ≤ s/2 진단(결과값 불변) → segment_check.csv
    },
    "receivers": {
        "center": None,                            # [x, y] 계산 중심(교차로 등)
        "grid": {"enabled": True, "bbox": None, "size": [480.0, 520.0], "step": 5.0, "heights": [4.0]},
        "facade": {"enabled": True, "radius": 230.0, "piece": 10.0, "z_in_floor": 2.8, "offset": 0.05},
        "points": {"path": None,                   # 점 수음점 CSV: point_id, x, y, h(지면 위 높이 m) — 현장 측정점 비교용
                   "default_height": None},        # h 가 빈 행에 쓸 높이(m). None 이면 그 행은 건너뜀
    },
    "plots": {"font": None, "dpi": 130, "targets_3d": None, "radius_3d": 105.0,
              "limits": None,                       # 기준 초과 지도: {LrT: 68, LrN: 58, label: "..."}
              "elev_3d": 22.0, "azim_3d": 135.0},
}


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
    for k in ("dem", "traffic", "junctions", "road_override"):
        c["input"][k]["path"] = rp(c["input"][k]["path"])
    c["input"]["ground"]["soft_polygons"] = rp(c["input"]["ground"]["soft_polygons"])
    c["receivers"]["points"]["path"] = rp(c["receivers"]["points"]["path"])
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
    if not 0.2 < float(p["reflection"]["rho"]) <= 1.0:
        raise ValueError("ISO 9613-2 7.5: 반사계수 rho 는 0.2 초과 1 이하")
    if c["input"]["traffic"]["missing_m"] not in ("error", "estimate"):
        raise ValueError("input.traffic.missing_m 는 error 또는 estimate")
    if c["input"]["traffic"]["oneway_name_traffic"] not in ("apply", "fallback"):
        raise ValueError("input.traffic.oneway_name_traffic 는 apply 또는 fallback")
    m = c["model"]
    if m["source_ground"] not in ("dtm_at_source", "road_profile"):
        raise ValueError("model.source_ground 는 dtm_at_source 또는 road_profile")
    if m["odd_lane_rule"] not in ("ceil", "major_fwd"):
        raise ValueError("model.odd_lane_rule 는 ceil 또는 major_fwd")
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


def center(c):
    if c["receivers"]["center"] is not None:
        return tuple(float(v) for v in c["receivers"]["center"])
    b = grid_bbox(c)
    return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
