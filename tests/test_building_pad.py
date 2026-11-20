# -*- coding: utf-8 -*-
"""건물 부지 정지(model.building_ground: pad, 2026-11-20) — 경사 지형 위 건물이 묻히거나 뜨지 않는지.
※ Claude(Anthropic의 AI)를 활용하여 작성하였음."""
import copy

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import LineString, Polygon

from sori import build as B
from sori.config import DEFAULTS


def _case(valley=False):
    rast = B.Raster((0.0, 0.0, 100.0, 100.0), 1.0)
    X, Y = rast.centers()
    gnd = (0.2 * X).astype(np.float32)                       # 동쪽으로 20 % 오르막
    if valley:                                               # 건물 남쪽 벽 아래로 꺼진 골(꼭짓점 사이에서 뜨는 경우)
        gnd = gnd - (3.0 * np.exp(-((X - 50) ** 2) / 8.0) * (np.abs(Y - 40) < 3)).astype(np.float32)
    bld = gpd.GeoDataFrame({"bid": [0], "H": [9.0]}, geometry=[Polygon([(40, 40), (60, 40), (60, 60), (40, 60)])])
    road = gpd.GeoDataFrame({"width": [6.0], "lanes": [2]}, geometry=[LineString([(0, 10), (100, 10)])])
    cfg = copy.deepcopy(DEFAULTS)
    cfg["model"]["building_ground"] = "pad"
    return cfg, rast, gnd, bld, road


def _outline(rast, g, poly):
    P = np.asarray(poly.exterior.segmentize(0.5).coords)
    return rast.sample(g, P[:, 0], P[:, 1])


def test_vertex_min_rule():
    """이전 기본(vertex_min): z0 = 외곽선 꼭짓점 지형고 최솟값. 2026-11-20 부터 기본은 follow"""
    cfg, rast, gnd, bld, road = _case()
    assert DEFAULTS["model"]["building_ground"] == "follow"
    B.build_dsm(bld, gnd, rast)
    c = np.asarray(bld.geometry[0].exterior.coords)
    assert bld.z0[0] == pytest.approx(float(np.min(rast.sample(gnd, c[:, 0], c[:, 1]))))


@pytest.mark.parametrize("valley", [False, True])
def test_pad_no_burial_no_floating(valley):
    cfg, rast, gnd, bld, road = _case(valley)
    before = _outline(rast, gnd, bld.geometry[0])
    g2, z0, info = B.grade_building_pads(cfg, bld, road, gnd, rast)
    B.build_dsm(bld, g2, rast, z0)
    z = bld.z0[0]
    after = _outline(rast, g2, bld.geometry[0])
    assert before.max() - z > 3.0                              # 정지 전: 오르막 쪽 벽이 3 m 넘게 묻힘
    assert np.abs(after - z).max() < 1e-3                      # 정지 후: 벽 둘레 지형 = 바닥 높이(묻힘·뜸 없음)
    assert z <= before.min() + 1e-6                            # 바닥은 정지 전 외곽선 최솟값(꼭짓점 사이 포함)
    X, Y = rast.centers()
    far = np.hypot(np.clip(np.abs(X - 50) - 10, 0, None), np.clip(np.abs(Y - 50) - 10, 0, None)) > 5.5
    assert np.array_equal(g2[far], gnd[far])                   # 정지 폭(5 m) 밖 지형 불변
    ring = ~far & ~((np.abs(X - 50) < 11) & (np.abs(Y - 50) < 11))
    assert (g2[ring] <= gnd[ring] + 1e-6).all()                # 1 m 띠 밖은 깎기만


def test_pad_keeps_road_cells():
    cfg, rast, gnd, bld, road = _case()
    bld = gpd.GeoDataFrame({"bid": [0], "H": [9.0]}, geometry=[Polygon([(40, 14), (60, 14), (60, 30), (40, 30)])])
    g2, z0, info = B.grade_building_pads(cfg, bld, road, gnd, rast)
    band = B._road_band(road, rast)
    assert band.any() and np.array_equal(g2[band], gnd[band])  # 도로폭 안(음원 지면) 불변


def test_follow_roof_walls_receivers_on_terrain():
    """follow(기본): 지붕 = 칸마다 지형 + H, 벽면 수음점은 벽 위치 지형부터 — 땅속 바닥·수음점 없음"""
    import pandas as pd
    from sori.bldz import roof_fn
    cfg, rast, gnd, bld, road = _case()
    bld["floors"] = 3; bld["kind"] = "일반"
    dsm, bidr = B.build_dsm(bld, gnd, rast, None, follow=True)
    ins = bidr >= 0
    assert np.allclose(dsm[ins] - gnd[ins], 9.0, atol=1e-4)
    F = B.facade_edges(cfg, bld, bidr, rast, bld.geometry[0].buffer(5), gnd=gnd)
    cfg["receivers"]["center"] = [50.0, 50.0]
    cfg["receivers"]["facade"]["area"] = "radius"; cfg["receivers"]["facade"]["radius"] = 1e6
    FR = B.facade_receivers(cfg, F, bld, gnd, rast)
    wall_g = rast.sample(gnd, FR.x - 0.05 * FR.nx, FR.y - 0.05 * FR.ny)   # 수음점 바로 안쪽 벽 위치
    assert (FR.zg.values >= wall_g - 0.05).all() and (FR.z > FR.zg).all()
    f1 = FR[FR.floor == 1]
    assert np.allclose(f1.z - f1.zg, cfg["receivers"]["facade"]["z_in_floor"])
    rf = roof_fn(bld.iloc[0])
    x, y = 60.0, 60.0                                           # 오르막 쪽 꼭짓점: 지붕 = 그 자리 지형 + H
    assert rf(x, y) == pytest.approx(float(rast.sample(gnd, [x], [y])[0]) + 9.0, abs=0.01)
