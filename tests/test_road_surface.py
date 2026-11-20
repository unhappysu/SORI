# -*- coding: utf-8 -*-
"""차도 면 지형 보정(model.road_surface: flatten) 시험.
경사진 가상 지형 위의 직선 도로: 보정하면 도로폭 안 지형이 가로 방향으로 평평해지고(중심선 종단 고도),
도로 밖은 그대로, 음원 높이는 중심선 고도 + 0.5 m. 기본값은 flatten(2026-11-06).
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import copy

import geopandas as gpd
import numpy as np
from shapely.geometry import LineString

from sori import build
from sori.config import DEFAULTS


def _case():
    cfg = copy.deepcopy(DEFAULTS)
    rast = build.Raster((0.0, 0.0, 200.0, 100.0), 1.0)
    X, Y = rast.centers()
    gnd = (0.10 * Y + 0.02 * X).astype(np.float32)          # 가로(y) 10 %, 세로(x) 2 % 경사
    road = gpd.GeoDataFrame({"fid": [0], "lanes": [4], "width": [14.0]},
                            geometry=[LineString([(20, 50), (180, 50)])])
    return cfg, rast, gnd, road


def test_flatten_makes_road_band_level_across_and_keeps_outside():
    cfg, rast, gnd, road = _case()
    cfg["model"]["road_surface_smooth_m"] = 0.0
    out, info = build.flatten_road_surface(cfg, road, gnd, rast)
    for x in (40.5, 100.5, 160.5):
        across = rast.sample(out, [x] * 13, np.arange(44.0, 57.0))          # 중심선 ±6 m (도로폭 14 m 안)
        centre = 0.10 * 50 + 0.02 * x
        assert np.max(np.abs(across - centre)) < 0.06                         # 가로로 평평, 높이 = 중심선 고도
    assert abs(float(rast.sample(out, [100.5], [70.5])[0]) - float(rast.sample(gnd, [100.5], [70.5])[0])) < 1e-6
    assert info["cells"] > 0 and info["dz_min"] < -0.5 and info["dz_max"] > 0.5   # 위쪽은 깎고 아래쪽은 메움


def test_flatten_sources_sit_on_centreline_profile():
    cfg, rast, gnd, road = _case()
    out, _ = build.flatten_road_surface(cfg, road, gnd, rast)
    # 바깥 차로 음원선(중심선 ± 5.25 m) 위치의 지형 = 중심선 지형 → 양쪽 음원 높이가 같음
    for x in (60.5, 120.5):
        a, b, c = rast.sample(out, [x, x, x], [50 - 5.25, 50.0, 50 + 5.25])
        assert abs(a - b) < 0.06 and abs(c - b) < 0.06


def test_default_is_flatten_and_dtm_keeps_terrain():
    """기본은 flatten(2026-11-06 결정). dtm 이면 build 가 지형을 바꾸지 않음(보정 함수를 부르지 않음)"""
    assert DEFAULTS["model"]["road_surface"] == "flatten"


def test_nearest_tie_takes_lowest_index():
    """거리가 같은 후보(차이 1 µm 이하)는 가장 작은 번호 — CPU 부동소수점 끝자리와 상관없이 같은 점(2026-11-20)"""
    pts = np.array([[1.0, 0.0], [0.0, 0.0], [0.5, 0.0], [0.5, 0.0]])
    xy = np.array([[0.5, 1.0],                      # 2·3 번과 같은 거리(완전 동률) → 2
                   [0.5 + 1e-9, 1.0],               # 끝자리만 다른 거의 동률 → 2
                   [0.75, 0.0],                     # 0 번과 2·3 번이 같은 거리 0.25 → 0
                   [0.0, 0.1]])                     # 동률 없음 → 1
    assert build._nearest_lowest_index(pts, xy).tolist() == [2, 2, 0, 1]
    assert build._nearest_lowest_index(pts[:1], xy).tolist() == [0, 0, 0, 0]
