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


# ======================================================================= 도로별 확인값 (S3)
def test_road_override_read(tmp_path):
    """roads_override.csv 읽기·검증·도로 객체 부착. 계산에는 아직 쓰지 않음(S4·S5에서 반영)"""
    from noisemap import inputs, config
    p = tmp_path / "ov.csv"
    p.write_text("UFID,도로명,lanes_fwd,lanes_bwd,lanes_basis,oneway_dir,oneway_dir_basis,note\n"
                 "A,전농로,3,2,checked,,,항공사진\n"
                 "B,골목,,,,-1,,\n"
                 "C,사가정로,2,2,,,,\n"
                 "Z,없는도로,1,1,,,,\n", encoding="utf-8")
    cfg = config._merge(config.DEFAULTS, {"receivers": {"center": [0, 0]}, "input": {"road_override": {"path": str(p)}}})
    ov = inputs.load_road_override(cfg)
    assert ov["A"]["lanes_fwd"] == 3 and ov["A"]["lanes_bwd"] == 2 and ov["A"]["lanes_basis"] == "checked"
    assert ov["B"]["oneway_dir"] == -1 and ov["B"]["oneway_dir_basis"] == "unspecified" and ov["B"]["lanes_basis"] == ""
    road = pd.DataFrame({"ufid": ["A", "B", "C", "D"], "lanes": [5, 1, 5, 4], "oneway": [False, True, False, True]})
    r, info = inputs.apply_road_override(road, ov)
    assert list(r.lanes_src) == ["override", "map", "override", "map"]
    assert r.lanes_fwd[0] == 3 and r.lanes_bwd[0] == 2 and np.isnan(r.lanes_fwd[1])
    assert r.oneway_dir[1] == -1 and list(r.oneway_dir_src) == ["", "override", "", "digitized_assumed"]
    assert list(r.lanes) == [5, 1, 5, 4]                                   # 수치지도 차로수는 그대로
    assert info["matched_ufid"] == 3 and info["unmatched_ufid"] == 1
    assert info["warnings"]["lanes_fwd + lanes_bwd ≠ 수치지도 차로수"] == 1   # C: 2+2 ≠ 5
    # 파일 미지정 → None, 부착 열은 모두 기본값
    assert inputs.load_road_override(config._merge(config.DEFAULTS, {})) is None
    r0, _ = inputs.apply_road_override(road, None)
    assert set(r0.lanes_src) == {"map"} and r0.lanes_fwd.isna().all()
    # 검증 오류: 중복 UFID, oneway_dir 값, 0차로, basis 값
    for body in ("ufid,lanes_fwd\nA,2\nA,3\n", "ufid,oneway_dir\nA,2\n", "ufid,lanes_fwd\nA,0\n",
                 "ufid,lanes_fwd,lanes_basis\nA,2,확인\n", "ufid,lanes_fwd\nA,2.5\n"):
        p.write_text(body, encoding="utf-8")
        with pytest.raises(RuntimeError):
            inputs.load_road_override(cfg)


# ======================================================================= 음원선 배치 (S4)
def _legacy_source_offsets(n_lanes, width, oneway):
    """S4 이전(커밋 abf64c7) build.source_offsets 를 그대로 옮긴 것 — 기본값 동등성 비교용"""
    wl = float(np.clip(width / n_lanes, 2.75, 3.5)) if width and width > 0 else 3.25
    if oneway:
        nd = n_lanes
    else:
        if n_lanes <= 1:
            return [(0.0, None)]
        nd = int(math.ceil(n_lanes / 2))
    d_edge = 0.5 if nd <= 2 else (1.0 if nd <= 4 else 1.5)
    if oneway:
        return [(-(nd * wl / 2 - d_edge * wl), True)]
    off = nd * wl - d_edge * wl
    return [(-off, True), (+off, False)]


def test_source_offsets_default_equals_legacy():
    """기본 설정·확인값 없음 → 이전 코드와 오프셋이 비트 단위로 같음"""
    from noisemap import build, config
    cfg = config._merge(config.DEFAULTS, {"receivers": {"center": [0, 0]}})
    for n in range(1, 11):
        for w in (0.0, 3.0, 4.8, 7.0, 13.1, 25.0, 40.0):
            for ow in (False, True):
                nd, nb, wl, rule = build.lane_layout(cfg, n, w, ow, np.nan, np.nan)
                new = build.source_offsets(nd, nb, wl, ow, single=(rule == "single_centre"))
                assert new == _legacy_source_offsets(n, w, ow), (n, w, ow)


def test_source_offsets_bild6_and_priority():
    """Bild 6 규칙(방향별 차로 수 → 음원선 위치)과 확인값·설정 우선순위.
    기대값은 Bild 6 문장에서 유도: 중심선(방향 경계)에서 음원선까지 = (nd − d_edge)·wl,
    d_edge = 0.5(1·2차로), 1.0(3·4차로), 1.5(5차로 이상)"""
    from noisemap import build, config
    cfg = config._merge(config.DEFAULTS, {"receivers": {"center": [0, 0]}, "model": {"lane_width": 3.0}})
    lay = lambda *a: build.lane_layout(cfg, *a)
    expect = {1: 0.5, 2: 1.5, 3: 2.0, 4: 3.0, 5: 3.5, 6: 4.5}
    for k, mult in expect.items():                              # 확인값 k + k
        nd, nb, wl, rule = lay(2 * k, 99.0, False, k, k)
        assert (nd, nb, wl, rule) == (k, k, 3.0, "override")
        assert build.source_offsets(nd, nb, wl, False) == [(-mult * 3.0, True), (mult * 3.0, False)]
    nd, nb, wl, _ = lay(5, 0.0, False, 3, 2)                      # 비대칭 3 + 2
    assert build.source_offsets(nd, nb, wl, False) == [(-6.0, True), (4.5, False)]
    # 확인값 없음: 홀수 차로 규칙
    assert lay(5, 0.0, False, np.nan, np.nan)[:2] == (3, 3)      # ceil(현행)
    cfg_m = config._merge(cfg, {"model": {"odd_lane_rule": "major_fwd"}})
    assert build.lane_layout(cfg_m, 5, 0.0, False, np.nan, np.nan)[:2] == (3, 2)
    assert build.lane_layout(cfg_m, 4, 0.0, False, np.nan, np.nan)[:2] == (2, 2)   # 짝수는 규칙 무관
    assert build.lane_layout(cfg_m, 1, 0.0, False, np.nan, np.nan)[3] == "single_centre"
    # 한쪽만 지정한 확인값 → 수치지도 규칙으로
    assert lay(5, 0.0, False, 3, np.nan)[3] == "ceil"
    # 일방통행: 확인값 lanes_fwd 우선, 음원선 1개
    nd, nb, wl, rule = lay(1, 0.0, True, 2, np.nan)
    assert (nd, nb, rule) == (2, 0, "override") and len(build.source_offsets(nd, nb, wl, True)) == 1
    # 차로폭 auto: 도로폭/전체 차로수를 [2.75, 3.5] 로 제한, 도로폭 없으면 3.25
    cfg_a = config._merge(config.DEFAULTS, {"receivers": {"center": [0, 0]}})
    assert build.lane_layout(cfg_a, 4, 12.0, False)[2] == 3.0
    assert build.lane_layout(cfg_a, 5, 30.0, False)[2] == 3.5
    assert build.lane_layout(cfg_a, 4, 0.0, False)[2] == 3.25
    assert build.lane_layout(cfg_a, 5, 15.0, False, 2, 2)[2] == 3.5          # 확인값 합계(4) 기준 15/4 → 3.5


# ======================================================================= 방향별 배분·일방통행 방향 (S5)
def test_dir_split_and_oneway_dir(tmp_path):
    """dir_split_fwd 는 UFID 행·dir_basis = measured 일 때만 적용, oneway_dir = −1 은 음원선 쪽·방향을 뒤집음.
    평지(경사 0) 가상 도로: LW′ ∝ 10 lg M 이므로 두 음원선 LW′ 차 = 10 lg[p/(1−p)]"""
    import geopandas as gpd
    from shapely.geometry import LineString
    from noisemap import build, inputs, config, rls19
    tp = tmp_path / "traffic.csv"
    tp.write_text("key,key_type,DTV,dir_split_fwd,dir_basis\n"
                  "A,ufid,10000,0.7,measured\nB,ufid,10000,0.7,assumed\n도로C,name,10000,0.7,measured\n"
                  "D,ufid,10000,0.7,measured\nE,ufid,4000,0.7,measured\n", encoding="utf-8")
    cfg = config._merge(config.DEFAULTS, {"receivers": {"center": [0, 0]}, "input": {"traffic": {"path": str(tp)}},
                                          "model": {"canyon": {"enabled": False}}})
    spec = [("A", "", 4, 14.0, False, 0), ("B", "", 4, 14.0, False, 50), ("C", "도로C", 4, 14.0, False, 100),
            ("D", "", 1, 4.0, False, 150), ("E", "", 1, 4.0, True, 200), ("F", "", 2, 7.0, True, 250)]
    road = gpd.GeoDataFrame({"ufid": [s[0] for s in spec], "rname": [s[1] for s in spec],
                             "axis": [s[1] or f"_u{s[0]}" for s in spec], "lanes": [s[2] for s in spec],
                             "width": [s[3] for s in spec], "oneway": [s[4] for s in spec], "fid": range(len(spec)),
                             "surface_attr": ""}, geometry=[LineString([(0, s[5]), (100, s[5])]) for s in spec])
    ovr = {u: {"lanes_fwd": None, "lanes_bwd": None, "oneway_dir": -1, "note": "", "lanes_basis": "",
               "oneway_dir_basis": "checked"} for u in ("E", "F")}
    road, _ = inputs.apply_road_override(road, ovr)
    rast = build.Raster((-50.0, -50.0, 150.0, 300.0), 1.0)
    gnd = np.zeros((rast.ny, rast.nx))
    F = pd.DataFrame(columns=["x1", "y1", "x2", "y2", "nx", "ny", "z0", "ztop", "L", "bid"])
    S, T, Ln = build.build_sources(cfg, road, gnd, rast, F, pd.DataFrame(columns=["x", "y", "type"]), {})
    T = T.set_index("ufid"); Ln = Ln.set_index("ufid")
    src = T.dir_split_src.to_dict()
    assert src == {"A": "measured", "B": "not_measured_ignored", "C": "name_key_ignored", "D": "measured",
                   "E": "oneway_ignored", "F": "default_half"}
    assert T.dir_split_fwd.to_dict() == {"A": 0.7, "B": 0.5, "C": 0.5, "D": 0.7, "E": 1.0, "F": 1.0}
    by = lambda u: S[S.fid == list(T.index).index(u)]
    a = by("A")
    assert np.allclose(a[a.along == True].frac, 0.7) and np.allclose(a[a.along == False].frac, 0.3)
    d_lw = a[a.along == True].LWp_day.mean() - a[a.along == False].LWp_day.mean()
    assert d_lw == pytest.approx(10 * math.log10(0.7 / 0.3), abs=1e-9)
    b = by("B")
    assert b[b.along == True].LWp_day.mean() == pytest.approx(b[b.along == False].LWp_day.mean(), abs=1e-12)
    # D: 양방향 1차로 중앙 1개선 — 경사 0 이면 p 와 무관하게 LW′(M) (에너지 합 보존)
    t = T.loc["D"]
    ref = rls19.LW_prime(t.M_day, t.p1_day, t.p2_day, t.v_pkw, t.v_lkw, t.surface, 0.0)
    assert by("D").LWp_day.values == pytest.approx(ref, abs=1e-9)
    # E: 일방 1차로, oneway_dir = −1 → 중앙(오프셋 0) 그대로, 방향 표시만 반대
    e = by("E")
    assert (e.along == False).all() and np.allclose(e.y, 200.0) and Ln.loc["E", "oneway_dir_used"] == -1
    # F: 일방 2차로(차로폭 3.5) → 기본은 디지타이즈 우측 −1.75 m, −1 이면 좌측 +1.75 m
    f = by("F")
    assert (f.along == False).all() and np.allclose(f.y, 251.75) and Ln.loc["F", "offset_fwd"] == pytest.approx(1.75)


# ======================================================================= 일방통행 객체의 도로명 교통량 (S2)
def test_oneway_name_traffic_option():
    """oneway_name_traffic: apply(기본, 현행) | fallback(도로명 교통량을 같은 이름 일방통행에 배정 안 함). UFID 우선"""
    from noisemap import inputs, config
    rec = dict({c: "" for c in inputs.TRAFFIC_COLS}, key="사가정로", DTV="45000")
    by_name = {"사가정로": rec}
    ramp = {"ufid": "R", "rname": "사가정로", "axis": "사가정로", "lanes": 2, "surface_attr": "", "oneway": True}
    main = dict(ramp, ufid="M", lanes=5, oneway=False)
    narrow = dict(ramp, ufid="N", lanes=1)
    base = config._merge(config.DEFAULTS, {"receivers": {"center": [0, 0]}})
    fb = config._merge(base, {"input": {"traffic": {"oneway_name_traffic": "fallback"}}})
    t = inputs.traffic_for_road(base, ramp, {}, by_name, True, 6)              # 기본: 현행대로 배정
    assert t["traffic_src"] == "user" and t["M_day"] == pytest.approx(0.0575 * 45000)
    t = inputs.traffic_for_road(fb, ramp, {}, by_name, True, 6)                # fallback: 차로수 2 가정값
    assert (t["traffic_src"], t["traffic_basis"]) == ("fallback_oneway", "fallback_lanes")
    assert t["M_day"] == pytest.approx(0.0575 * base["input"]["traffic"]["fallback_dtv_by_lanes"][2])
    assert inputs.traffic_for_road(fb, main, {}, by_name, True, 6)["traffic_src"] == "user"        # 양방향은 무관
    assert inputs.traffic_for_road(fb, narrow, {}, by_name, False, 6)["traffic_src"] == "fallback_narrow"
    ufid_rec = dict(rec, key="R", key_type="ufid", DTV="8000")
    t = inputs.traffic_for_road(fb, ramp, {"R": ufid_rec}, by_name, True, 6)   # UFID 지정은 항상 우선
    assert t["traffic_src"] == "user" and t["M_day"] == pytest.approx(0.0575 * 8000)


# ======================================================================= 음원 높이 기준 지면 (S6)
def test_source_ground_option():
    """source_ground: dtm_at_source(기본) = 음원점 위치 DTM, road_profile = 도로중심선 종단 고도.
    횡단 10 % 기울어진 평면 지형 z = 0.1·(y − 100), 중심선 y = 100 의 4차로(차로폭 3.5 → 오프셋 ±5.25 m)"""
    import geopandas as gpd
    from shapely.geometry import LineString
    from noisemap import build, inputs, config
    road = gpd.GeoDataFrame({"ufid": ["A"], "rname": [""], "axis": ["_uA"], "lanes": [4], "width": [14.0],
                             "oneway": [False], "fid": [0], "surface_attr": [""]},
                            geometry=[LineString([(0, 100), (100, 100)])])
    road, _ = inputs.apply_road_override(road, None)
    rast = build.Raster((-20.0, 50.0, 120.0, 150.0), 1.0)
    X, Y = rast.centers()
    gnd = 0.1 * (Y - 100.0)
    F = pd.DataFrame(columns=["x1", "y1", "x2", "y2", "nx", "ny", "z0", "ztop", "L", "bid"])
    J = pd.DataFrame(columns=["x", "y", "type"])
    out = {}
    for mode in ("dtm_at_source", "road_profile"):
        cfg = config._merge(config.DEFAULTS, {"receivers": {"center": [0, 0]}, "input": {"traffic": {"path": None}},
                                              "model": {"canyon": {"enabled": False}, "source_ground": mode}})
        S, _, _ = build.build_sources(cfg, road, gnd, rast, F, J, {})
        out[mode] = S
    a, b = out["dtm_at_source"], out["road_profile"]
    fwd, bwd = (a.along == True).values, (a.along == False).values
    assert np.allclose(a.y[fwd], 94.75) and np.allclose(a.y[bwd], 105.25)          # 위치는 두 방식 같음
    assert np.allclose(a.zg[fwd], -0.525) and np.allclose(a.zg[bwd], 0.525)       # 음원점 위치 DTM
    assert np.allclose(b.zg, 0.0) and np.allclose(b.z, 0.5)                        # 중심선 종단 고도 + 0.5 m
    assert np.allclose(a.z - a.zg, 0.5) and np.allclose(a[["x", "y", "LWp_day"]], b[["x", "y", "LWp_day"]])
