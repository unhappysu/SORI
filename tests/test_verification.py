# -*- coding: utf-8 -*-
"""
검증 시험 (pytest).  python -m pytest -q tests
각 시험은 원문 식을 독립적으로 다시 계산하거나 해석해와 비교함.
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import math
import warnings

import numpy as np
import pandas as pd
import pytest

from noisemap import rls19 as R
from noisemap import iso9613 as I
from noisemap.solver import compute

LAM500 = 340.0 / 500.0


# ======================================================================= RLS-19
def test_LW0_table3():
    # 식 (6), 표 3 — v = 50 km/h
    assert R.LW0("Pkw", 50) == pytest.approx(88.0 + 10 * math.log10(1 + 2.5 ** 3.06), abs=1e-9)
    assert R.LW0("Lkw1", 50) == pytest.approx(100.3 + 10 * math.log10(1 + 1.25 ** 4.33), abs=1e-9)
    assert R.LW0("Lkw2", 50) == pytest.approx(105.4 + 10 * math.log10(2.0), abs=1e-9)


def test_LW_prime_eq4():
    M, p1, p2, v = 1000.0, 3.0, 4.0, 50.0
    ref = 10 * math.log10(M) + 10 * math.log10(
        0.93 * 10 ** (0.1 * R.LW0("Pkw", v)) / v + 0.03 * 10 ** (0.1 * R.LW0("Lkw1", v)) / v
        + 0.04 * 10 ** (0.1 * R.LW0("Lkw2", v)) / v) - 30
    assert R.LW_prime(M, p1, p2, v, v, "ref", 0.0) == pytest.approx(ref, abs=1e-9)
    # 보정항은 차종별 LW에 더해지므로 D_K·D_refl 상수는 LW'에 그대로 더해짐
    assert R.LW_prime(M, p1, p2, v, v, "ref", 0.0, 1.5, 0.5) == pytest.approx(ref + 2.0, abs=1e-9)


def test_speed_min_30():
    assert R.LW_prime(100, 3, 4, 20, 20, "ref", 0) == R.LW_prime(100, 3, 4, 30, 30, "ref", 0)


def test_D_K_is_product():                    # 원문 식 (8): KKT · max{1 − x/120; 0}
    assert R.D_K(0, "signal") == 3.0
    assert R.D_K(60, "signal") == pytest.approx(1.5)
    assert R.D_K(130, "signal") == 0.0
    assert R.D_K(10, "other") == 0.0
    assert R.D_K(10, "roundabout") == pytest.approx(2 * (1 - 10 / 120))


def test_D_refl_eq9():
    assert R.D_refl(20, 40) == pytest.approx(1.0)
    assert R.D_refl(30, 30) == 1.6
    assert R.D_refl(20, 120) == 0.0


def test_D_LN_eq7():
    assert R.D_LN("Pkw", 4, 50) == pytest.approx(2 / 10 * 120 / 100)
    assert R.D_LN("Lkw1", -8, 50) == pytest.approx(-4 / -8 * 30 / 10)
    assert R.D_LN("Lkw2", 20, 50) == R.D_LN("Lkw2", 12, 50)       # ±12 % 제한


def test_D_SD_tables():
    assert R.D_SD("AC11", "Pkw", 50) == -2.7 and R.D_SD("AC11", "Lkw", 70) == -2.1
    assert R.D_SD("pflaster_other", "Pkw", 30) == 5.0 and R.D_SD("pflaster_even", "Lkw", 55) == 3.0
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        assert R.D_SD("PA8", "Pkw", 50) == 0.0                 # 원문에 ≤60 값 없음 → 0 + 경고


def test_round_up_3_1():                     # 원문 3.1 'aufzurunden'
    assert R.round_up(60.01) == 61 and R.round_up(60.0) == 60


# ======================================================================= ISO 9613
@pytest.mark.parametrize("T,RH,f,ref", [(10, 70, 500, 1.9), (20, 70, 500, 2.8), (30, 70, 500, 3.1),
                                        (15, 20, 500, 2.7), (15, 50, 500, 2.2), (15, 80, 500, 2.4),
                                        (20, 70, 1000, 5.0), (15, 20, 2000, 28.2)])
def test_alpha_table2(T, RH, f, ref):
    assert I.alpha_iso9613_1(f, T, RH) == pytest.approx(ref, rel=0.01, abs=0.06)   # 표는 반올림값


def test_Agr_general_table3():
    assert I.A_gr_general(500.0, 0.5, 4.0, 50.0, 0.0, 0.0, 0.0) == pytest.approx(-3.0)   # 단단한 지면, q=0
    dp = 300.0; q = 1 - 30 * 4.5 / dp
    assert I.A_gr_general(500.0, 0.5, 4.0, dp, 0.0, 0.0, 0.0) == pytest.approx(-3.0 - 3 * q)
    c = lambda h, d: 1.5 + 14.0 * math.exp(-0.46 * h * h) * (1 - math.exp(-d / 50))
    ref = (-1.5 + c(0.5, 50)) + (-1.5 + c(4.0, 50)) + 0.0
    assert I.A_gr_general(500.0, 0.5, 4.0, 50.0, 1.0, 1.0, 1.0) == pytest.approx(ref)


def test_Dz_eq14():
    z, a, b = 1.0, 20.0, 30.0
    d = a + b - z
    K = math.exp(-1 / 2000 * math.sqrt(a * b * d / (2 * z)))
    ref = 10 * math.log10(3 + 20 / LAM500 * z * K)
    assert I.D_z(z, a, b, d, 0.0, LAM500, 20.0, False, 20.0, 25.0) == pytest.approx(ref)
    assert I.D_z(100.0, a, b, d, 0.0, LAM500, 20.0, False, 20.0, 25.0) == 20.0          # 상한


# ======================================================================= 경로·솔버 시험 장치
def flat_world(n=500, res=1.0):
    gnd = np.zeros((n, n)); dsm = np.zeros((n, n)); bid = -np.ones((n, n), np.int64); G = np.zeros((n, n))
    return gnd, dsm, bid, G, 0.0, float(n), res, n, n


def run_solver(src, rec, world, F=None, order=0, rho=0.8, Rmax=1000.0):
    gnd, dsm, bid, G, x0, y0, res, nx, ny = world
    sx, sy, sz, LW = (np.array(v, float) for v in zip(*src))
    rx, ry, rz = (np.array(v, float) for v in zip(*rec))
    if F is None:
        F = [np.zeros(0)] * 9 + [np.zeros(0, np.int64)]
    bands = np.array([500.0]); lams = np.array([LAM500]); al = np.array([0.0]); w = np.array([1.0])
    Ed, En, *_ = compute(rx, ry, rz, np.zeros(len(rx)), -np.ones(len(rx), np.int64), -np.ones(len(rx), np.int64),
                         order, sx, sy, sz, np.zeros(len(sx)), 10 ** (0.1 * LW), 10 ** (0.1 * LW),
                         np.ones(len(sx), np.bool_), *F, rho, gnd, dsm, bid, G, x0, y0, res, nx, ny, 1.0,
                         Rmax, Rmax, Rmax, Rmax, Rmax, Rmax, 1.5, bands, lams, al, w, False, 20.0, 20.0, 25.0)
    return 10 * np.log10(Ed)


def test_point_source_free_field():
    w = flat_world()
    L = run_solver([(100, 250, 0.5, 100.0)], [(150, 250, 4.0)], w)[0]
    d = math.sqrt(50 ** 2 + 3.5 ** 2)
    assert L == pytest.approx(100 - (20 * math.log10(d) + 11) + 3.0, abs=1e-6)     # Agr = −3 (G=0, q=0)


def test_line_source_vs_analytic():
    """직선도로(±100 m, 1 m 분할) — 해석해 L = LW' + 10 lg[(2/d')·atan(Lh/d')/(2π)] + 0.02 dB(−11+3 vs −10lg2π)"""
    w = flat_world()
    LWp = 80.0
    src = [(250.0 + x, 250.0, 0.5, LWp) for x in np.arange(-99.5, 100.0, 1.0)]
    for dist in (10.0, 20.0, 40.0):
        L = run_solver(src, [(250.0, 250.0 + dist, 4.0)], w)[0]
        dd = math.sqrt(dist ** 2 + 3.5 ** 2)
        ana = LWp + 10 * math.log10(2 / dd * math.atan(100 / dd) / (2 * math.pi)) + (-8.0 + 10 * math.log10(2 * math.pi))
        assert L == pytest.approx(ana, abs=0.05), (dist, L, ana)


def test_barrier_single_diffraction():
    w = flat_world()
    gnd, dsm, bid, G, *_ = w
    dsm[:, 200] = 6.0; bid[:, 200] = 0                    # x=200 m 의 두께 1 m 벽(높이 6 m)
    xs, xr = 150.0, 260.0
    L = run_solver([(xs, 250.0, 0.5, 100.0)], [(xr, 250.0, 4.0)], w)[0]
    # 수동 계산: 회절 모서리 = 벽 상단 앞면 래스터 표본 (x=200.5, z=6)
    ex = 200.5; a = math.hypot(ex - xs, 6 - 0.5); b = math.hypot(xr - ex, 6 - 4.0)
    d = math.hypot(xr - xs, 3.5); z = a + b - d
    K = math.exp(-1 / 2000 * math.sqrt(a * b * d / (2 * z)))
    Dz = min(10 * math.log10(3 + 20 / LAM500 * z * K), 20.0)
    A = 20 * math.log10(d) + 11 + max(-3.0, Dz)
    assert L == pytest.approx(100 - A, abs=1e-6)


def test_first_order_reflection_image_source():
    """수음점 뒤 반사벽(ρ=0.8): L = 10lg(10^0.1Ld + ρ·10^0.1Li)"""
    w = flat_world()
    gnd, dsm, bid, G, *_ = w
    # 벽(건물) : x = 300~301, y = 150~350, 높이 20 m
    dsm[150:350, 300] = 20.0; bid[150:350, 300] = 7
    F = [np.array([300.0]), np.array([150.0]), np.array([300.0]), np.array([350.0]),   # x1,y1,x2,y2
         np.array([-1.0]), np.array([0.0]), np.array([0.0]), np.array([20.0]), np.array([200.0]),
         np.array([7], np.int64)]                                                      # nx,ny,z0,ztop,L,bid
    s = (200.0, 250.0, 0.5, 100.0); r = (280.0, 250.0, 4.0)
    L1 = run_solver([s], [r], w, F, order=1)[0]
    d_dir = math.hypot(80, 3.5); d_img = math.hypot(100 + 20, 3.5)   # 허상: 벽(x=300) 대칭
    Ld = 100 - (20 * math.log10(d_dir) + 11 - 3)
    Li = 100 - (20 * math.log10(d_img) + 11 - 3)
    ref = 10 * math.log10(10 ** (0.1 * Ld) + 0.8 * 10 ** (0.1 * Li))
    assert L1 == pytest.approx(ref, abs=1e-6)
    # 2차 반사: 반대편(x=151)에 평행벽 추가. 1차 2경로 + 2차 2경로(S→151→300→R, S→300→151→R)
    dsm[150:350, 150] = 20.0; bid[150:350, 150] = 8
    F2 = [np.append(F[0], 151.0), np.append(F[1], 350.0), np.append(F[2], 151.0), np.append(F[3], 150.0),
          np.append(F[4], 1.0), np.append(F[5], 0.0), np.append(F[6], 0.0), np.append(F[7], 20.0),
          np.append(F[8], 200.0), np.append(F[9], 8)]

    def Lp(dist, gain):                       # 평지·G=0, 지면항 q 포함(표 3)
        q = 0.0 if dist <= 135 else 1 - 135 / dist
        return 100 - (20 * math.log10(math.hypot(dist, 3.5)) + 11 - 3 - 3 * q) + 10 * math.log10(gain)
    e = lambda *v: 10 * math.log10(sum(10 ** (0.1 * x) for x in v))
    # 허상 거리: 1차 = |S − R'|, R'(300)=320, R'(151)=22 / 2차 = R''=−18(151 다음 300), 578
    ref1 = e(Lp(80, 1), Lp(120, 0.8), Lp(178, 0.8))
    ref2 = e(ref1, Lp(218, 0.64), Lp(378, 0.64))
    assert run_solver([s], [r], w, F2, order=1)[0] == pytest.approx(ref1, abs=1e-6)
    assert run_solver([s], [r], w, F2, order=2)[0] == pytest.approx(ref2, abs=1e-6)


# ======================================================================= 교차로 위상
def test_junction_membership():
    import geopandas as gpd
    from shapely.geometry import LineString
    from noisemap import build, config
    cfg = config._merge(config.DEFAULTS, {"receivers": {"center": [0, 0]}})
    rd = gpd.GeoDataFrame({"lanes": [6, 6, 6, 6, 2, 2], "axis": ["A", "A", "B", "B", "_u1", "_u2"],
                           "fid": range(6)},
                          geometry=[LineString([(-300, 0), (0, 0)]), LineString([(0, 0), (300, 0)]),
                                    LineString([(0, -300), (0, 0)]), LineString([(0, 0), (0, 300)]),
                                    LineString([(-200, 50), (-60, 50)]), LineString([(-60, 50), (-60, 0)])])
    J, reach = build.junctions(cfg, rd)
    sig = J[J.type == "signal"]
    assert len(sig) == 1 and abs(sig.x.iloc[0]) < 1e-6
    # 이면도로(_u1)는 차수 2 절점을 거쳐 (−60,0) T자 교차로에만 속함 → 중앙 신호교차로 미적용
    js = reach[4]
    assert all(J.loc[list(js), "type"] == "other")
    assert set(reach[0]) & set(sig.index)


# ======================================================================= 실제 자료에서 발견된 문제 회귀시험
def test_name_traffic_not_applied_to_narrow_same_name_piece():
    from noisemap import inputs, config
    cfg = config._merge(config.DEFAULTS, {"receivers": {"center": [0, 0]}})
    rec = {c: "" for c in inputs.TRAFFIC_COLS}; rec.update(key="전농로", DTV="45000")
    by_name = {"전농로": rec}
    wide = {"ufid": "A", "rname": "전농로", "axis": "전농로", "lanes": 5, "surface_attr": ""}
    narrow = dict(wide, ufid="B", lanes=1)
    assert inputs.traffic_for_road(cfg, wide, {}, by_name, True, 6)["traffic_src"] == "user"
    t = inputs.traffic_for_road(cfg, narrow, {}, by_name, False, 6)
    assert t["traffic_src"] == "fallback_narrow" and t["M_day"] < 1000
    # 모든 구간이 1차로인 골목이면 도로명 교통량을 그대로 적용
    assert inputs.traffic_for_road(cfg, narrow, {}, by_name, False, 1)["traffic_src"] == "user"
    # UFID 지정은 항상 우선
    assert inputs.traffic_for_road(cfg, narrow, {"B": rec}, by_name, False, 6)["traffic_src"] == "user"


# ======================================================================= 교통량 출처 기록 (S1)
def test_traffic_basis_recorded():
    """실측/가정(basis), M 산정 방식, 메모가 결과로 전달되고 교통량 값 자체는 바뀌지 않음"""
    from noisemap import inputs, config
    cfg = config._merge(config.DEFAULTS, {"receivers": {"center": [0, 0]}})
    road = {"ufid": "A", "rname": "전농로", "axis": "전농로", "lanes": 5, "surface_attr": ""}
    base = {c: "" for c in inputs.TRAFFIC_COLS}
    meas = dict(base, key="전농로", DTV="45000", basis="Measured", note="TOPIS 2026-08")
    t = inputs.traffic_for_road(cfg, road, {}, {"전농로": meas}, True, 6)
    assert (t["traffic_src"], t["traffic_basis"], t["note"]) == ("user", "measured", "TOPIS 2026-08")
    assert (t["M_day_from"], t["M_night_from"]) == ("DTV_x_tab2", "DTV_x_tab2")
    # basis 빈칸 → unspecified, M 직접 입력 → direct (주간만)
    t2 = inputs.traffic_for_road(cfg, road, {}, {"전농로": dict(base, key="전농로", DTV="45000", M_day="2000")}, True, 6)
    assert t2["traffic_basis"] == "unspecified" and (t2["M_day_from"], t2["M_night_from"]) == ("direct", "DTV_x_tab2")
    assert t2["M_day"] == 2000.0 and t2["M_night"] == t["M_night"]
    # 교통량 파일에 없는 도로 → 차로수 가정값
    t3 = inputs.traffic_for_road(cfg, dict(road, rname="", axis="_u"), {}, {}, True, 6)
    assert t3["traffic_src"] == "fallback" and t3["traffic_basis"] == "fallback_lanes" and t3["note"] == ""
    # 출처 열 추가가 교통량 값을 바꾸지 않음 (표 2 gemeinde 주간 계수 0.0575 × DTV)
    assert t["M_day"] == pytest.approx(0.0575 * 45000)
    with pytest.raises(RuntimeError):
        inputs.traffic_for_road(cfg, road, {}, {"전농로": dict(meas, basis="실측")}, True, 6)


# ======================================================================= 구간 길이 진단 (S7)
def test_segment_check_rule():
    """RLS-19 3.2 주석 l ≤ s/2: s < 2l 이면 위반, s = 2l 은 위반 아님. s = 직접 경로 3D 거리"""
    from noisemap.run import segment_check, _segment_summary
    S = pd.DataFrame(dict(x=[0.0, 0.0], y=[0.0, 3.0], z=[0.0, 0.0], l=[4.0, 5.0], main=[True, False]))
    rx = np.array([8.0, 7.9, 100.0]); ry = np.zeros(3); rz = np.zeros(3)
    nv, nvm, ratio = segment_check(S, rx, ry, rz)
    # R1: 간선 s = 8 = 2l(경계, 위반 아님), 이면 s = √73 ≈ 8.54 < 10(위반)
    # R2: 간선 s = 7.9 < 8(위반), 이면 √(7.9²+9) < 10(위반)
    # R3: 반경 2·l_max 안에 음원 없음
    # min_s_over_l 은 모든 근처 음원 중 최소: R1 = min(8/4, √73/5), R2 = min(7.9/4, √71.41/5)
    assert list(nv) == [1, 2, 0] and list(nvm) == [0, 1, 0]
    assert ratio[0] == pytest.approx(math.sqrt(73) / 5) and ratio[1] == pytest.approx(math.sqrt(7.9 ** 2 + 9) / 5)
    assert ratio[2] == math.inf
    s = _segment_summary(nv, nvm, ratio)
    assert (s["receivers"], s["violating"], s["violating_via_main"]) == (3, 2, 1)
    # 높이 차도 s 에 포함(3D): 간선에서 수평 7 m, 높이 4 m → s = √65 ≈ 8.06 ≥ 8 → 간선 위반 아님
    nv2, nvm2, _ = segment_check(S.iloc[:1], np.array([7.0]), np.zeros(1), np.array([4.0]))
    assert nv2[0] == 0
