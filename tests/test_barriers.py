# -*- coding: utf-8 -*-
"""방음벽(input.barriers): 선분 교차 회절 모서리, 반사면, 입력 파일, 모델 구축·계산 연결.
※ Claude(Anthropic의 AI)를 활용하여 제작하였음."""
import json
import math
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from sori import build as B, config as C, inputs as I, run as RUN
from sori.geometry import NGEOM, G_SHIELD, G_DOUBLE, G_Z, G_D, path_geometry, path_geometry_b
from sori.solver import compute, compute_b

from reference_case import INPUT

LAM500 = 340.0 / 500.0


def _world(n=300):
    return np.zeros((n, n)), np.zeros((n, n)), -np.ones((n, n), np.int64), np.zeros((n, n)), 0.0, float(n), 1.0, n, n


def _geom(bars, src=(100.0, 150.0, 0.5), rec=(160.0, 150.0, 4.0)):
    gnd, dsm, bid, G, x0, y0, res, nx, ny = _world()
    hd = np.empty(8192); hh = np.empty(8192); g = np.empty(NGEOM)
    vx = np.array([src[0], rec[0], 0, 0, 0.0]); vy = np.array([src[1], rec[1], 0, 0, 0.0])
    path_geometry_b(vx, vy, 2, src[2], rec[2], src[2], rec[2], gnd, dsm, bid, G, x0, y0, res, nx, ny, 1.0,
                    -1, np.full(3, -1, np.int64), 1.5, hd, hh, g, np.array(bars, float).reshape(-1, 5))
    return g


def test_barrier_edge_path_difference():
    """두께 없는 모서리 1개: z = d_ss + d_sr − d (ISO 9613-2 식 16, RLS-19 식 16), 단일 회절"""
    g = _geom([[120.3, 100.0, 120.3, 200.0, 5.0]])
    dss = math.hypot(20.3, 4.5); dsr = math.hypot(39.7, 1.0); d = math.hypot(60.0, 3.5)
    assert g[G_SHIELD] == 1.0 and g[G_DOUBLE] == 0.0
    assert g[G_D] == pytest.approx(d)
    assert g[G_Z] == pytest.approx(dss + dsr - d, abs=1e-9)


def test_barrier_below_sight_line_or_not_crossed():
    assert _geom([[120.0, 100.0, 120.0, 200.0, 1.0]])[G_SHIELD] == 0.0        # 가시선(1.67 m)보다 낮음
    assert _geom([[120.0, 160.0, 120.0, 200.0, 5.0]])[G_SHIELD] == 0.0        # 경로와 만나지 않음
    assert _geom([[100.05, 100.0, 100.05, 200.0, 5.0]])[G_SHIELD] == 0.0      # 음원 0.1 m 이내 교차는 뺌


def test_two_barriers_double_and_shared_vertex():
    g = _geom([[120.0, 100.0, 120.0, 200.0, 5.0], [140.0, 100.0, 140.0, 200.0, 5.0]])
    assert g[G_SHIELD] == 1.0 and g[G_DOUBLE] == 1.0
    # 이어진 두 조각의 공유 꼭짓점을 지나는 경로: 모서리 1개로 셈
    g = _geom([[120.0, 100.0, 120.0, 150.0, 5.0], [120.0, 150.0, 120.0, 200.0, 5.0]])
    assert g[G_SHIELD] == 1.0 and g[G_DOUBLE] == 0.0


def test_no_barrier_same_as_before():
    gnd, dsm, bid, G, x0, y0, res, nx, ny = _world()
    dsm[140:160, 125:130] = 8.0; bid[140:160, 125:130] = 0
    hd = np.empty(8192); hh = np.empty(8192); g1 = np.empty(NGEOM); g2 = np.empty(NGEOM)
    vx = np.array([100.0, 160.0, 0, 0, 0]); vy = np.array([150.0, 151.0, 0, 0, 0])
    rb = np.full(3, -1, np.int64)
    path_geometry(vx, vy, 2, 0.5, 4.0, 0.5, 4.0, gnd, dsm, bid, G, x0, y0, res, nx, ny, 1.0, -1, rb, 1.5, hd, hh, g1)
    path_geometry_b(vx, vy, 2, 0.5, 4.0, 0.5, 4.0, gnd, dsm, bid, G, x0, y0, res, nx, ny, 1.0, -1, rb, 1.5, hd, hh,
                    g2, np.zeros((0, 5)))
    assert np.array_equal(g1, g2)


def _solve(bars, F=None, order=0, rec=(160.0, 150.0, 4.0), meth=1):
    gnd, dsm, bid, G, x0, y0, res, nx, ny = _world()
    if F is None:
        F = [np.zeros(0)] * 9 + [np.zeros(0, np.int64)]
    one = np.array([1.0])
    a = (np.array([rec[0]]), np.array([rec[1]]), np.array([rec[2]]), np.zeros(1), -np.ones(1, np.int64),
         -np.ones(1, np.int64), order, np.array([100.0]), np.array([150.0]), np.array([0.5]), np.zeros(1),
         np.array([1e10]), np.array([1e10]), np.ones(1, np.bool_), *F, 0.8, gnd, dsm, bid, G, x0, y0, res, nx, ny, 1.0,
         500.0, 500.0, 500.0, 500.0, 500.0, 500.0, 1.5, np.array([500.0]), np.array([LAM500]), np.zeros(1), one,
         False, 20.0, 20.0, 25.0, meth, np.zeros(len(F[0]) + 1, np.int64), np.zeros(0, np.int64), False)
    if bars is None:
        return 10 * np.log10(compute(*a)[0][0])
    return 10 * np.log10(compute_b(*a, np.array(bars, float).reshape(-1, 5))[0][0])


@pytest.mark.parametrize("meth", [0, 1, 2])
def test_solver_barrier_lowers_level(meth):
    free = _solve(None, meth=meth)
    assert _solve([], meth=meth) == free                                       # 빈 배열 = 이전 계산
    low = _solve([[120.0, 100.0, 120.0, 200.0, 3.0]], meth=meth)
    high = _solve([[120.0, 100.0, 120.0, 200.0, 6.0]], meth=meth)
    assert high < low < free - 5.0


def test_iso_barrier_matches_formula():
    """ISO 9613-2 식 (14): D_z = 10 lg(3 + C2/λ · C3 · z · K_met), 차폐 시 A_gr 는 D_z 에 포함(식 12)"""
    from sori import iso9613 as ISO
    L = _solve([[120.3, 100.0, 120.3, 200.0, 5.0]])
    dss = math.hypot(20.3, 4.5); dsr = math.hypot(39.7, 1.0); d = math.hypot(60.0, 3.5)
    z = dss + dsr - d
    Dz = ISO.D_z(z, dss, dsr, d, 0.0, LAM500, 20.0, False, 20.0, 25.0)
    assert L == pytest.approx(100.0 - (20 * math.log10(d) + 11) - Dz, abs=1e-6)


def test_reflecting_barrier_faces():
    """반사형 방음벽 뒤쪽(음원 쪽) 수음점은 1차 반사로 값이 오르고, 반사 없음(none)은 반사면이 없음"""
    Bp = pd.DataFrame([dict(barrier_id="A", x1=180.0, y1=100.0, x2=180.0, y2=200.0, h=5.0, kind="reflect",
                            loss=np.nan, z0=0.0, ztop=5.0, L=100.0)])
    Fb = B.barrier_faces(Bp)
    assert len(Fb) == 2 and set(Fb.bid) == {-1}
    assert np.allclose(Fb.nx.values, [1.0, -1.0]) or np.allclose(Fb.nx.values, [-1.0, 1.0])
    F = [Fb[c].values.astype(float) for c in ("x1", "y1", "x2", "y2", "nx", "ny", "z0", "ztop", "L")] \
        + [Fb.bid.values.astype(np.int64)]
    bars = Bp[["x1", "y1", "x2", "y2", "h"]].values
    direct = _solve(bars, order=1)
    refl = _solve(bars, F=F, order=1)
    d1 = math.hypot(60.0, 3.5); d2 = math.hypot(100.0, 3.5)                    # 거울 음원 거리 80 + 20
    assert refl - direct == pytest.approx(10 * math.log10(1 + 0.8 * (d1 / d2) ** 2), abs=0.01)
    assert len(B.barrier_faces(Bp.assign(kind="none"))) == 0


@pytest.mark.parametrize("meth,kind,loss,expect_db", [
    ("rls19", "reflect", None, 0.5), ("rls19", "absorb", None, 3.0), ("rls19", "high", None, 5.0),
    ("rls90", "absorb", None, 4.0), ("rls90", "high", None, 8.0), ("iso9613_2", "absorb", None, 3.0),
    ("iso9613_2", "reflect", None, None), ("rls19", "absorb", 7.5, 7.5)])
def test_barrier_reflection_loss_by_type(meth, kind, loss, expect_db):
    """반사 저감형 방음벽: 1차 반사 이득 = 10^(−0.1·손실)(RLS-19 표 8 / RLS-90 표 7 / ISO 는 RLS-19 값),
    반사형은 건물 외벽과 같은 이득(ISO ρ = 0.8, RLS-19 0.5 dB, RLS-90 −1 dB)"""
    from sori import config as Cf
    from sori.propagation import get_model
    from sori.solver import compute_l
    Bp = pd.DataFrame([dict(barrier_id="A", x1=180.0, y1=100.0, x2=180.0, y2=200.0, h=5.0, kind=kind,
                            loss=np.nan if loss is None else loss, z0=0.0, ztop=5.0, L=100.0)])
    Fb = B.barrier_faces(Bp)
    cfg = Cf._merge(Cf.DEFAULTS, {"receivers": {"center": [0, 0]}, "propagation_method": meth})
    fatt = RUN.face_factors(cfg, Fb)
    base = get_model(cfg).reflection_gain(cfg)
    gain = base * fatt[0]
    ref = base if expect_db is None else 10 ** (-0.1 * expect_db)
    assert gain == pytest.approx(ref, rel=1e-12)
    assert np.allclose(fatt, fatt[0])                                          # 양면 같은 값
    # 커널에서 1차 반사 성분 비 = 이득(ISO 직접 계산)
    gnd, dsm, bid, G, x0, y0, res, nx, ny = _world()
    F = [Fb[c].values.astype(float) for c in ("x1", "y1", "x2", "y2", "nx", "ny", "z0", "ztop", "L")] \
        + [Fb.bid.values.astype(np.int64)]
    one = np.ones(1)
    args = (np.array([160.0]), np.array([150.0]), np.array([4.0]), np.zeros(1), -np.ones(1, np.int64),
            -np.ones(1, np.int64), 1, np.array([100.0]), np.array([150.0]), np.array([0.5]), np.zeros(1),
            one, one, np.ones(1, np.bool_), *F, 0.8, gnd, dsm, bid, G, x0, y0, res, nx, ny, 1.0,
            500.0, 500.0, 500.0, 500.0, 500.0, 500.0, 1.5, np.array([500.0]), np.array([LAM500]), np.zeros(1), one,
            False, 20.0, 20.0, 25.0, 1, np.zeros(len(F[0]) + 1, np.int64), np.zeros(0, np.int64), False,
            Bp[["x1", "y1", "x2", "y2", "h"]].values.astype(float), False, 0, np.zeros(1, np.int64),
            np.zeros((0, 2)), np.zeros(1, np.int64))
    e1 = compute_l(*args, np.ones(len(F[0])))[3][0]
    e2 = compute_l(*args, fatt)[3][0]
    assert e1 > 0 and e2 / e1 == pytest.approx(fatt[0], rel=1e-12)


def test_barrier_file_roundtrip_and_errors(tmp_path):
    p = tmp_path / "barriers.csv"
    bars = [dict(id="W1", h=4.0, kind="high", loss=None, pts=[(1.0, 2.0), (3.5, 4.25), (9.0, 9.0)], note="시험"),
            dict(id="W2", h=3.0, kind="absorb", loss=6.5, pts=[(0.0, 0.0), (1.0, 1.0)], note="")]
    I.save_barriers(p, bars)
    cfg = {"input": {"barriers": {"path": str(p)}}}
    got = I.load_barriers(cfg)
    assert got[0]["id"] == "W1" and got[0]["h"] == 4.0 and got[0]["kind"] == "high" and got[0]["loss"] is None
    assert got[0]["pts"] == bars[0]["pts"] and got[1]["loss"] == 6.5
    p.write_text("barrier_id,height,absorbing,coords\nA,3,1,1 2; 3 4\nB,3,0,1 2; 3 4\n", encoding="utf-8")
    assert [b["kind"] for b in I.load_barriers(cfg)] == ["absorb", "reflect"]          # 이전 형식(absorbing 열)
    p.write_text("barrier_id,height,type,coords\nX,3,xyz,1 2; 3 4\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="type"):
        I.load_barriers(cfg)
    assert I.load_barriers({"input": {"barriers": {"path": None}}}) == []
    p.write_text("barrier_id,height,absorbing,coords\nX,0,0,1 2; 3 4\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="height"):
        I.load_barriers(cfg)
    p.write_text("barrier_id,height,absorbing,coords\nX,3,0,1 2\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="2점"):
        I.load_barriers(cfg)


def test_build_and_run_with_barrier(tmp_path):
    """가상 교차로: 방음벽을 넣으면 벽 뒤 격자값이 내려가고, 벽에서 먼 곳은 그대로. 기록 파일 확인"""
    work = tmp_path / "w"
    shutil.copytree(INPUT, work)
    cfg = C.load(work / "config.yaml")
    cfg["receivers"]["facade"]["enabled"] = False
    cfg["receivers"]["grid"]["heights"] = [1.5]
    cfg["propagation"]["reflection"]["max_order_grid"] = 1
    B.main(cfg); RUN.main(cfg, ("grid",))
    out = Path(cfg["project"]["output_dir"])
    base = pd.read_csv(out / "grid_result.csv")
    assert not (out / "barriers_used.csv").exists()
    cx, cy = cfg["receivers"]["center"]
    I.save_barriers(work / "barriers.csv", [dict(id="W1", h=4.0, kind="none", loss=None,
                                                 pts=[(cx - 120.0, cy + 30.0), (cx - 40.0, cy + 30.0)])])
    cfg["input"]["barriers"]["path"] = str(work / "barriers.csv")
    out2 = tmp_path / "out_barrier"
    cfg["project"]["output_dir"] = str(out2)
    info = B.main(cfg); RUN.main(cfg, ("grid",))
    assert info["barriers"]["count"] == 1 and info["barriers"]["length_m"] == 80.0
    assert (out2 / "barriers_used.csv").exists()
    assert json.load(open(out2 / "run_info.json", encoding="utf-8"))["barriers"]["count"] == 1
    aft = pd.read_csv(out2 / "grid_result.csv")
    assert len(aft) == len(base)
    d = aft.LrT.values - base.LrT.values
    assert d.min() < -3.0                                                      # 벽 바로 뒤
    far = (base.x > cx + 60.0).values
    assert np.nanmax(np.abs(d[far])) < 0.05
    assert np.nanmax(d) < 0.05                                                 # 반사 없음: 오르는 곳 없음


def test_point_paths_table_matches_grid_and_roads(tmp_path):
    """경로 표(직접 + 1차 반사)의 합 = 격자 결과의 직접 ⊕ 1차 반사 성분, 도로별 기여도의 합 = 전체"""
    from sori import paths as PA
    work = tmp_path / "w"
    shutil.copytree(INPUT, work)
    cfg = C.load(work / "config.yaml")
    cfg["receivers"]["facade"]["enabled"] = False
    cfg["receivers"]["grid"]["heights"] = [4.0]
    B.main(cfg); RUN.main(cfg, ("grid",))
    G = pd.read_csv(Path(cfg["project"]["output_dir"]) / "grid_result.csv")
    for i in (len(G) // 3, len(G) // 2, 2 * len(G) // 3):
        r = G.iloc[i]
        A, info = PA.point_table(cfg, r.x, r.y, 4.0)
        ref = 10 * np.log10(10 ** (0.1 * r.LrT_direct) + (10 ** (0.1 * r.LrT_refl1) if np.isfinite(r.LrT_refl1) else 0.0))
        assert info["L_day"] == pytest.approx(ref, abs=0.01)
        assert info["L_day_direct"] == pytest.approx(r.LrT_direct, abs=0.01)
        R = PA.by_road(A)
        assert PA.esum(R.L) == pytest.approx(info["L_day"], abs=1e-6) and R.share.sum() == pytest.approx(100.0)
        assert list(R.L) == sorted(R.L, reverse=True)
        T = PA.top_paths(A, n=5)
        assert len(T) == 5 and T.L_day.iloc[0] == A.L_day.max()
        assert T[T.order == 1].refl_x.notna().all()
    with pytest.raises(ValueError, match="건물 안"):
        bx = gpd_centroid(cfg)
        PA.point_table(cfg, bx[0], bx[1], 4.0)


def gpd_centroid(cfg):
    import geopandas as gpd
    b = gpd.read_file(Path(cfg["project"]["output_dir"]) / "buildings.gpkg")
    p = b.geometry.iloc[0].representative_point()
    return p.x, p.y


@pytest.mark.parametrize("meth", ["iso9613_2", "rls19"])
def test_point_paths_higher_orders_match_grid_components(tmp_path, meth):
    """경로 보기 2단계: 차수별 경로 합 = 격자 결과의 LrT_refl2·LrT_refl3 성분, 전체 합 = LrT"""
    from sori import paths as PA
    work = tmp_path / "w"
    shutil.copytree(INPUT, work)
    cfg = C.load(work / "config.yaml")
    cfg["propagation_method"] = meth
    cfg["propagation"]["lateral"]["enabled"] = False                       # 경로 보기에는 측면 회절이 없음
    cfg["receivers"]["facade"]["enabled"] = False
    cfg["receivers"]["grid"]["heights"] = [4.0]
    B.main(cfg); RUN.main(cfg, ("grid",))
    G = pd.read_csv(Path(cfg["project"]["output_dir"]) / "grid_result.csv")
    G = G[np.isfinite(G.LrT_refl2) & np.isfinite(G.LrT_refl3)]
    assert len(G)
    for r in (G.iloc[0], G.iloc[len(G) // 2], G.iloc[-1]):
        A, info = PA.point_table(cfg, r.x, r.y, 4.0, max_order=3)
        assert info["L_day_refl2"] == pytest.approx(r.LrT_refl2, abs=0.01)
        assert info["L_day_refl3"] == pytest.approx(r.LrT_refl3, abs=0.01)
        assert info["L_day"] == pytest.approx(r.LrT, abs=0.01)
        assert info["L_night"] == pytest.approx(r.LrN, abs=0.01)
        h3 = A[A.order == 3]
        assert len(h3) and all(len(p) == 3 for p in h3.refl_pts)
        assert all(p[0] == (a, b) for p, a, b in zip(h3.refl_pts, h3.refl_x, h3.refl_y))
