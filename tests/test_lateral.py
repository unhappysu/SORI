# -*- coding: utf-8 -*-
"""측면 회절(sori/lateral.py, propagation.lateral) — 수계산(수직·비스듬한 입사, 회전한 건물), 막는 건물 돌아가기,
방음벽 끝, 끄면 결과 불변, 설정 검사, 기준 사례 build → run, 시제품(tools/lateral_impact.py 변형 C)과 대조.
※ Claude(Anthropic의 AI)를 활용하여 제작하였음."""
import math
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from rasterio import features
from rasterio.transform import from_origin
from shapely import affinity
from shapely.geometry import Polygon, box

from sori import build as B, config as C, run as RUN
from sori.solver import compute_b, compute_l, facade_neighbors

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import lateral_impact as LI  # noqa: E402
from reference_case import INPUT  # noqa: E402

LAM = 340.0 / 500.0
N = 300


def world(polys, heights):
    """평지(z = 0)·G = 0, 건물 다각형을 1 m 래스터(DSM·건물 번호)로. 반환 (래스터 묶음, bxy, bptr)"""
    tr = from_origin(0.0, float(N), 1.0, 1.0)
    bid = (features.rasterize(((p, i) for i, p in enumerate(polys)), out_shape=(N, N), transform=tr, fill=-1,
                              dtype="int32").astype(np.int64) if polys else -np.ones((N, N), np.int64))
    gnd = np.zeros((N, N)); G = np.zeros((N, N))
    top = np.asarray(list(heights) + [0.0], float)
    dsm = np.where(bid >= 0, top[np.clip(bid, 0, None)], 0.0)
    import geopandas as gpd
    bxy, bptr = B.building_outlines(gpd.GeoDataFrame(geometry=list(polys)))
    return (gnd, dsm, bid, G, 0.0, float(N), 1.0, N, N), bxy, bptr


def solve(w, bxy, bptr, src, rec, lat, bars=None, bgid=None, grow=6):
    gnd, dsm, bid, G, x0, y0, res, nx, ny = w
    bars = np.zeros((0, 5)) if bars is None else np.asarray(bars, float).reshape(-1, 5)
    bgid = np.arange(len(bars), dtype=np.int64) if bgid is None else np.asarray(bgid, np.int64)
    F = [np.zeros(0)] * 9 + [np.zeros(0, np.int64)]
    one = np.ones(1)
    o = compute_l(np.array([rec[0]]), np.array([rec[1]]), np.array([rec[2]]), np.zeros(1), -np.ones(1, np.int64),
                  -np.ones(1, np.int64), 0, np.array([src[0]]), np.array([src[1]]), np.array([0.5]), np.zeros(1),
                  one, one, np.ones(1, np.bool_), *F, 0.8, gnd, dsm, bid, G, x0, y0, res, nx, ny, 1.0,
                  500.0, 500.0, 500.0, 500.0, 500.0, 500.0, 1.5, np.array([500.0]), np.array([LAM]), np.zeros(1), one,
                  False, 20.0, 20.0, 25.0, 1, np.zeros(1, np.int64), np.zeros(0, np.int64), False, bars,
                  lat, grow, bgid, bxy, bptr, np.ones(len(F[0])))
    return o


def expected(src, rec, verts_list, top_factor):
    """시제품 수계산: 윗모서리 계수 + 좌우 측면 계수(식 13), 차폐 없을 때(A_div + A_gr(−3)) 값으로 상한"""
    S, R = np.array(src[:2]), np.array(rec[:2])
    zs, zr = 0.5, rec[2]
    d = math.hypot(math.hypot(*(R - S)), zr - zs)
    G0 = lambda chain, Lh, hs, hr: (0.0, 0.0, 0.0)
    lat = sum(10 ** (-0.1 * LI.lateral_atten(ch, zs, zr, 0.5, zr, d, 500.0, LAM, 0.0, G0)[0])
              for ch in LI.lateral_chains(S, R, np.vstack(verts_list)))
    free = 10 ** (-0.1 * (20 * math.log10(d) + 11 - 3))
    return min(top_factor + lat, max(free, top_factor)), lat


@pytest.mark.parametrize("case", ["수직", "비스듬", "회전 건물"])
def test_kernel_matches_hand_calc(case):
    rect = box(120.0, 142.5, 135.0, 157.5)                     # 폭 15 m·깊이 15 m, 높이 30 m
    src, rec = (100.0, 150.0), (160.0, 150.0, 4.0)
    if case == "비스듬":
        src, rec = (95.0, 128.0), (162.0, 166.0, 4.0)           # SR 이 건물 면과 약 30° 로 비스듬
    if case == "회전 건물":
        rect = affinity.rotate(rect, 35.0, origin="centroid")
        src, rec = (98.0, 141.0), (161.0, 158.0, 4.0)
    w, bxy, bptr = world([rect], [30.0])
    off = solve(w, bxy, bptr, src, rec, False)
    on = solve(w, bxy, bptr, src, rec, True)
    assert on[2][0] == off[2][0]                                # 직접음(윗모서리) 성분은 그대로
    verts = np.asarray(rect.exterior.coords)[:-1]
    ref, lat = expected(src, rec, [verts], off[0][0])
    assert lat > 0.0
    assert on[0][0] == pytest.approx(ref, rel=1e-9)
    assert on[7][0] == pytest.approx(ref - off[0][0], rel=1e-9)   # Elat = 더해진 에너지
    assert 10 * math.log10(on[0][0] / off[0][0]) > 3.0             # 높고 좁은 건물: 측면 경로가 지배


def test_off_is_identical_and_unshielded_unchanged():
    w, bxy, bptr = world([box(120.0, 142.5, 135.0, 157.5)], [30.0])
    off = solve(w, bxy, bptr, (100.0, 150.0), (160.0, 150.0, 4.0), False)
    b = solve(w, bxy, bptr, (100.0, 150.0), (160.0, 150.0, 4.0), True, grow=0)
    assert off[7][0] == 0.0 and b[7][0] > 0.0
    clear = solve(w, bxy, bptr, (100.0, 200.0), (160.0, 200.0, 4.0), True)    # 건물 옆을 지나는 경로: 차폐 없음
    assert clear[7][0] == 0.0
    low = world([box(120.0, 142.5, 135.0, 157.5)], [1.0])                       # 가시선보다 낮은 건물
    assert solve(*low, (100.0, 150.0), (160.0, 150.0, 4.0), True)[7][0] == 0.0


def test_blocking_neighbour_goes_around():
    """붙은 건물 줄: 왼쪽(위) 측면 경로를 옆 건물이 막으면 그 건물까지 돌아감(경로가 길어져 더 작음).
    max_grow = 0 이면 막힌 쪽은 버림(변형 B)"""
    a = box(120.0, 142.5, 135.0, 157.5)
    nb = box(118.0, 157.5, 137.0, 185.0)                        # 위쪽에 붙은 높은 건물
    w1, bxy1, bptr1 = world([a], [30.0])
    w2, bxy2, bptr2 = world([a, nb], [30.0, 30.0])
    src, rec = (100.0, 150.0), (160.0, 150.0, 4.0)
    alone = solve(w1, bxy1, bptr1, src, rec, True)[7][0]
    grown = solve(w2, bxy2, bptr2, src, rec, True)[7][0]
    cut = solve(w2, bxy2, bptr2, src, rec, True, grow=0)[7][0]
    off = solve(w2, bxy2, bptr2, src, rec, False)[0][0]
    ref, _ = expected(src, rec, [np.asarray(a.exterior.coords)[:-1], np.asarray(nb.exterior.coords)[:-1]], off)
    assert grown == pytest.approx(ref - off, rel=1e-9)          # 두 건물을 감싼 껍질 = 시제품과 같음
    assert cut < grown < alone and cut > 0.0                    # 오른쪽(아래)은 그대로 남음


def test_barrier_end_lateral():
    """방음벽(선분) 끝을 돌아가는 측면 회절: 방음벽 전체 끝점이 껍질 꼭짓점(조각 여러 개여도 같은 방음벽)"""
    w, bxy, bptr = world([], [])
    bars = [[130.0, 140.0, 130.0, 150.0, 5.0], [130.0, 150.0, 130.0, 160.0, 5.0]]
    src, rec = (100.0, 150.3), (160.0, 150.3, 4.0)
    off = solve(w, bxy, bptr, src, rec, False, bars, [0, 0])
    on = solve(w, bxy, bptr, src, rec, True, bars, [0, 0])
    ref, lat = expected(src, rec, [np.array([[130.0, 140.0], [130.0, 160.0], [130.0001, 150.0]])], off[0][0])
    assert lat > 0 and on[0][0] == pytest.approx(ref, rel=1e-6)


def test_default_on_iso_only():
    """기본 켬, ISO 방법에만 적용(RLS-19·RLS-90 은 켜 있어도 적용 안 함 — 원문 규정)"""
    c = C._merge(C.DEFAULTS, {"receivers": {"center": [0, 0]}})
    C._validate(c)
    assert c["propagation"]["lateral"]["enabled"] is True and RUN.lateral_on(c)
    for m in ("rls19", "rls90"):
        c["propagation_method"] = m
        C._validate(c)
        assert not RUN.lateral_on(c)
    c["propagation_method"] = "iso9613_2"
    c["propagation"]["lateral"]["max_grow"] = 99
    with pytest.raises(ValueError, match="max_grow"):
        C._validate(c)


def test_reference_case_lateral_on(tmp_path):
    """기준 사례: 켜면 LrT 가 줄지 않고(차폐 없는 점은 그대로), LrT_lateral 성분·기록. 시제품(변형 C)과 대조"""
    work = tmp_path / "w"
    shutil.copytree(INPUT, work)
    cfg = C.load(work / "config.yaml")
    cfg["receivers"]["facade"]["enabled"] = False
    cfg["receivers"]["grid"]["heights"] = [4.0]
    cfg["propagation"]["lateral"]["enabled"] = False
    B.main(cfg); RUN.main(cfg, ("grid",))
    out = Path(cfg["project"]["output_dir"])
    G0 = pd.read_csv(out / "grid_result.csv")
    cfg["propagation"]["lateral"]["enabled"] = True
    cfg["project"]["output_dir"] = str(tmp_path / "lat")
    B.main(cfg); RUN.main(cfg, ("grid",))
    G1 = pd.read_csv(tmp_path / "lat" / "grid_result.csv")
    assert "LrT_lateral" in G1 and "LrT_lateral" not in G0
    d = G1.LrT.values - G0.LrT.values
    assert d.min() > -1e-9 and d.max() > 1.0
    assert np.allclose(G1.LrT_direct, G0.LrT_direct)
    tot = 10 * np.log10(sum(10 ** (0.1 * G1[c].fillna(-999)) for c in
                            ("LrT_direct", "LrT_refl1", "LrT_refl2", "LrT_refl3", "LrT_lateral")))
    assert np.allclose(tot, G1.LrT, atol=1e-6)                  # 성분 합 = 전체
    import json
    ri = json.load(open(tmp_path / "lat" / "run_info.json", encoding="utf-8"))
    assert ri["lateral_diffraction"].startswith("켬") and any("측면 회절" in x for x in ri["standard_deviations"])
    # 시제품 변형 C 와 직접음 + 측면 에너지 대조(시제품은 건물 교차를 벡터로, 커널은 래스터 표본 + 외곽선 판정)
    from sori.propagation import get_model
    d_, S, F, _ = RUN.load_model(str(tmp_path / "lat"))
    Bd = LI.Buildings(tmp_path / "lat" / "buildings.gpkg")
    pick = G1.assign(dl=d).sort_values("dl").iloc[[-1, -5, -20, len(G1) // 2]]
    for r in pick.itertuples():
        _, _, _, EC = LI.receiver_levels(cfg, d_, S, Bd, r.x, r.y, r.z - r.h, r.h, get_model(cfg), {})
        kern = 10 ** (0.1 * r.LrT_direct) + (10 ** (0.1 * r.LrT_lateral) if np.isfinite(r.LrT_lateral) else 0.0)
        assert 10 * np.log10(kern / EC) == pytest.approx(0.0, abs=0.3)
