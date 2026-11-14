# -*- coding: utf-8 -*-
"""측면 회절 시제품(tools/lateral_impact.py) 수계산 대조 — 평지·G = 0·직사각형 건물 하나.
※ Claude(Anthropic의 AI)를 활용하여 제작하였음."""
import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import lateral_impact as LI  # noqa: E402

LAM = 340.0 / 500.0
S, R = np.array([100.0, 150.0]), np.array([160.0, 150.0])
BOX = np.array([[120.0, 142.5], [135.0, 142.5], [135.0, 157.5], [120.0, 157.5]])     # 폭 15 m(SR 에 수직), 깊이 15 m


def test_chains_left_right_symmetric():
    ch = LI.lateral_chains(S, R, BOX)
    assert len(ch) == 2
    ys = sorted(c[1:-1, 1].mean() for c in ch)
    assert ys == [142.5, 157.5] and all(len(c) == 4 for c in ch)              # 모서리 2개씩(이중 회절)
    assert all((c[0] == S).all() and (c[-1] == R).all() for c in ch)
    assert LI.lateral_chains(S, np.array([127.0, 150.0]), BOX) == []          # R 이 껍질 안(건물 안)이면 없음


def test_lateral_atten_hand_calc():
    zs, zr, hs, hr = 0.5, 4.0, 0.5, 4.0
    d = math.hypot(60.0, 3.5)
    ch = LI.lateral_chains(S, R, BOX)[0]
    G0 = lambda chain, Lh, hs, hr: (0.0, 0.0, 0.0)
    A, z, k = LI.lateral_atten(ch, zs, zr, hs, hr, d, 500.0, LAM, 0.0, G0)
    Lh = math.hypot(20, 7.5) + 15 + math.hypot(25, 7.5)
    L3 = math.hypot(Lh, 3.5)
    e = 15 * L3 / Lh
    r = (5 * LAM / e) ** 2
    C3 = (1 + r) / (1 / 3 + r)
    Dz = min(10 * math.log10(3 + 20 / LAM * C3 * (L3 - d)), 25.0)
    ref = 20 * math.log10(d) + 11 + (-3.0) + Dz                               # A_div(d) + A_gr(G = 0, q = 0) + D_z
    assert k == 2 and z == pytest.approx(L3 - d)
    assert A == pytest.approx(ref, abs=1e-9)
    # 모서리 1개(얇은 벽): C3 = 1, 상한 20 dB
    wall = np.array([[130.0, 145.0], [130.2, 145.0], [130.2, 155.0], [130.0, 155.0]])
    ch1 = [c for c in LI.lateral_chains(S, R, wall)]
    assert all(len(c) == 4 for c in ch1)                                      # 두께 0.2 m 도 꼭짓점 2개
    wall = np.array([[130.0, 145.0], [130.0, 155.0], [130.0001, 150.0]])
    c1 = LI.lateral_chains(S, R, wall)
    A1, z1, k1 = LI.lateral_atten(c1[0], zs, zr, hs, hr, d, 500.0, LAM, 0.0, G0)
    L3 = math.hypot(math.hypot(30, 5) * 2, 3.5)
    assert k1 == 1 and A1 == pytest.approx(20 * math.log10(d) + 11 - 3 + min(10 * math.log10(3 + 20 / LAM * (L3 - d)), 20),
                                            abs=1e-9)


def test_side_of_and_grown_chain_goes_around_blocker():
    """변형 C: 왼쪽 측면 경로를 막는 높은 건물이 있으면 그 건물까지 돌아가는 사슬이 됨"""
    import geopandas as gpd
    from shapely.geometry import Polygon
    ch = LI.lateral_chains(S, R, BOX)
    assert sorted(LI.side_of(S, R, c) for c in ch) == [-1, 1]
    blk = np.array([[105.0, 152.5], [115.0, 152.5], [115.0, 165.0], [105.0, 165.0]])   # S→왼쪽 모서리 구간을 막는 건물
    gpkg = Path(__file__).resolve().parent / "_tmp_lat.gpkg"
    try:
        gpd.GeoDataFrame(dict(ztop=[30.0, 30.0]), geometry=[Polygon(BOX), Polygon(blk)]).to_file(gpkg, driver="GPKG")
        Bd = LI.Buildings(gpkg)
        out = LI.grown_chains(S, R, Bd, [0], 0.5, 4.0)
        left = [c for c in out if LI.side_of(S, R, c) == 1][0]
        assert left[1:-1, 1].max() == 165.0                                          # 막는 건물 바깥 모서리로 돌아감
        right = [c for c in out if LI.side_of(S, R, c) == -1][0]
        assert right[1:-1, 1].min() == 142.5                                        # 반대쪽은 그대로
    finally:
        gpkg.unlink(missing_ok=True)
