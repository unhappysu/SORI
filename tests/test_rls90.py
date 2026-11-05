# -*- coding: utf-8 -*-
"""
RLS-90 (propagation_method: rls90) 검증 시험.
- 음원 L_m,E: 원문 Bild 22 계산 예(DTV 20 000, p 10/20 %, 차로당 L_m,E 67,7 / 63,0 dB(A))와 대조
- 전파: 원문 식 (20)~(27)을 시험 안에서 '원문 형식' L_m,i = L_m,E + D_l + D_s + D_BM + D_B 로 따로 계산해
  SORI 계산기(L_W′eq − D_A 형식) 결과와 비교 → 등가 환산이 항등식인지, 규정 차이(차폐 시 D_BM 없음,
  다중 회절 C 는 작은 쪽, 반사 조건 h_R ≥ 0,3·√a_R, D_E)를 지키는지 확인
같은 저자가 같은 식을 다시 계산한 자기 일관성 확인이 대부분이며, 상용 프로그램(SoundPLAN) 대조가 아님.
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import json
import math

import numpy as np
import pytest

from test_verification import flat_world, run_solver

R90 = 2                                    # solver.METH_RLS90
LG2PI = 10 * math.log10(2 * math.pi)


# ======================================================================= 원문 형식 수계산
def Ds(s):
    """식 (21)"""
    return 11.2 - 20 * math.log10(s) - s / 200


def DBM(hm, s):
    """식 (22)"""
    return min(hm / s * (34 + 600 / s) - 4.8, 0.0)


def Dz(A, B, s, z):
    """식 (25)(27)"""
    return 10 * math.log10(3 + 80 * z * math.exp(-1 / 2000 * math.sqrt(A * B * s / (2 * z))))


def LW_eq(LmE, l=1.0):
    """계산기 점음원 L_W = L_W′eq + 10 lg l,  L_W′eq = L_m,E + 11,2 + 10 lg 2π"""
    return LmE + 11.2 + LG2PI + 10 * math.log10(l)


# ======================================================================= 음원(식 6~9, 표 4)
def test_Lm25_matches_bild22_example():
    """Bild 22: DTV 20 000, A 999(Bundesautobahn, 표 3) — 주간 M = 0,06·DTV, 야간 0,014·DTV, 두 바깥 차로에 반씩.
    p 주간 10 %, 야간 20 % → 차로당 L_m,E 67,7 / 63,0 dB(A) (v = 100 km/h, 기준 노면, D_v 는 표에서 0,0)"""
    from sori import rls90
    assert round(rls90.Lm25(0.06 * 20000 / 2, 10.0), 1) == 67.7
    assert round(rls90.Lm25(0.014 * 20000 / 2, 20.0), 1) == 63.0


def test_Dv_formula_and_speed_limits():
    from sori import rls90
    # p = 0: D_v = L_Pkw − 37,3
    assert rls90.D_v(100, 80, 0.0) == pytest.approx(27.7 + 10 * math.log10(1 + 2.0 ** 3) - 37.3, abs=1e-12)
    # 50/50 km/h, p = 10 %: 식 (8)을 그대로 수계산
    lp = 27.7 + 10 * math.log10(1 + 1.0); ll = 23.1 + 12.5 * math.log10(50)
    ref = lp - 37.3 + 10 * math.log10((100 + (10 ** (0.1 * (ll - lp)) - 1) * 10) / (100 + 8.23 * 10))
    assert rls90.D_v(50, 50, 10.0) == pytest.approx(ref, abs=1e-12)
    assert ref == pytest.approx(-4.14, abs=0.01)
    # 속도 범위: v_Pkw 30~130, v_Lkw 30~80
    assert rls90.D_v(20, 20, 10.0) == rls90.D_v(30, 30, 10.0)
    assert rls90.D_v(150, 100, 10.0) == rls90.D_v(130, 80, 10.0)


@pytest.mark.parametrize("surface, v, ref", [
    ("ref", 50, 0.0), ("AC11", 30, 0.0), ("Waschbeton", 30, 1.0), ("Waschbeton", 45, 1.5), ("Waschbeton", 80, 2.0),
    ("pflaster_even", 30, 2.0), ("pflaster_even", 50, 3.0), ("pflaster_other", 40, 4.5), ("pflaster_other", 60, 6.0),
    ("PA8", 60, 0.0), ("PA8", 70, -3.0)])
def test_DStrO_table4(surface, v, ref):
    from sori import rls90
    assert rls90.D_StrO(surface, v) == ref


def test_DStrO_unknown_surface_rejected():
    from sori import rls90
    with pytest.raises(ValueError, match="표 4"):
        rls90.D_StrO("asphalt?", 50)


@pytest.mark.parametrize("g, ref", [(0, 0.0), (5, 0.0), (-5, 0.0), (6, 0.6), (-8, 1.8), (10, 3.0)])
def test_DStg(g, ref):
    from sori import rls90
    assert rls90.D_Stg(g) == pytest.approx(ref, abs=1e-12)


def test_LW_prime_is_LmE_plus_offset():
    from sori import rls90
    LmE = rls90.Lm_E(600, 10, 50, 50, "Waschbeton", 7)
    assert LmE == pytest.approx(rls90.Lm25(600, 10) + rls90.D_v(50, 50, 10) + 2.0 + 1.2, abs=1e-12)
    assert rls90.LW_prime(600, 10, 50, 50, "Waschbeton", 7, drefl=1.5) == pytest.approx(LmE + 1.5 + 11.2 + LG2PI)


@pytest.mark.parametrize("d, K", [(0, 3), (40, 3), (40.01, 2), (70, 2), (70.5, 1), (100, 1), (100.1, 0), (500, 0)])
def test_K_table2(d, K):
    from sori import rls90
    assert rls90.K_signal(d) == K


def test_Drefl_24a():
    from sori import rls90
    assert rls90.D_refl(10.0, 20.0) == pytest.approx(2.0)
    assert rls90.D_refl(30.0, 20.0) == 3.2                     # 상한
    assert rls90.D_refl(None, None) == 0.0


# ======================================================================= 전파(식 19~27)
def test_method_codes_match():
    from sori import solver
    from sori.propagation import METHODS
    assert METHODS["rls90"].code == solver.METH_RLS90 == R90


@pytest.mark.parametrize("dist", [10.0, 50.0, 300.0])
def test_free_field_equals_teilstueck_formula(dist):
    """평지·차폐 없음: L = L_m,E + D_l + D_s + D_BM (식 20~22), h_m = (0,5 + 4)/2"""
    w = flat_world()
    LmE, l = 60.0, 2.0
    L = run_solver([(100, 250, 0.5, LW_eq(LmE, l))], [(100 + dist, 250, 4.0)], w, meth=R90)[0]
    s = math.hypot(dist, 3.5)
    assert L == pytest.approx(LmE + 10 * math.log10(l) + Ds(s) + DBM(2.25, s), abs=1e-9)


def test_far_distance_ground_term_negative():
    from sori import rls90_propagation as RP
    s = math.hypot(400, 3.5)
    assert RP.D_BM(2.25, s) == pytest.approx(DBM(2.25, s)) and DBM(2.25, s) < -4.0
    assert RP.D_BM(10.0, 20.0) == 0.0                      # 식 (22) ≤ 0


def test_single_diffraction_drops_DBM():
    """차폐: D_B = −D_z, D_BM 은 빠짐('Bei Abschirmung entfällt D_BM'). 먼 음원으로 D_BM 이 크게 음수인 조건"""
    w = flat_world()
    gnd, dsm, bid, G, *_ = w
    dsm[:, 300] = 6.0; bid[:, 300] = 0
    xs, xr = 20.0, 330.0
    LmE = 60.0
    L = run_solver([(xs, 250.0, 0.5, LW_eq(LmE))], [(xr, 250.0, 4.0)], w, meth=R90, Rmax=2000.0)[0]
    ex = 300.5
    A = math.hypot(ex - xs, 5.5); B = math.hypot(xr - ex, 2.0)
    s = math.hypot(xr - xs, 3.5); z = A + B - s
    assert DBM(2.25, s) < -3.0
    assert L == pytest.approx(LmE + Ds(s) - Dz(A, B, s, z), abs=1e-9)


def test_double_diffraction_C_added_to_smaller():
    """다중 회절(Bild 16): z = A + B + C − s, 식 (27) 의 C 는 A·B 중 작은 쪽(여기서는 B)에 더함 — RLS-19 와 반대"""
    w = flat_world()
    gnd, dsm, bid, G, *_ = w
    dsm[:, 200] = 6.0; bid[:, 200] = 0
    dsm[:, 230] = 6.0; bid[:, 230] = 1
    xs, xr = 150.0, 260.0
    LmE = 60.0
    L = run_solver([(xs, 250.0, 0.5, LW_eq(LmE))], [(xr, 250.0, 4.0)], w, meth=R90)[0]
    e1, e2 = 200.5, 230.5
    A = math.hypot(e1 - xs, 5.5); B = math.hypot(xr - e2, 2.0); C = e2 - e1
    s = math.hypot(xr - xs, 3.5); z = A + B + C - s
    assert A > B
    ref = LmE + Ds(s) - Dz(A, B + C, s, z)
    assert L == pytest.approx(ref, abs=1e-9)
    assert abs(ref - (LmE + Ds(s) - Dz(A + C, B, s, z))) > 1e-3        # 큰 쪽에 더한 값과는 다름


def _wall_world(h):
    w = flat_world()
    gnd, dsm, bid, G, *_ = w
    dsm[150:350, 300] = h; bid[150:350, 300] = 7
    F = [np.array([300.0]), np.array([150.0]), np.array([300.0]), np.array([350.0]),
         np.array([-1.0]), np.array([0.0]), np.array([0.0]), np.array([h]), np.array([200.0]),
         np.array([7], np.int64)]
    return w, F


@pytest.mark.parametrize("h, used", [(20.0, True), (3.1, True), (2.9, False), (1.5, False)])
def test_first_order_reflection_DE_and_height_rule(h, used):
    """4.6: 허상음원 L_m,E + D_E(표 7, 매끈한 외벽 −1). 조건 h_R ≥ 0,3·√a_R, a_R = 음원–반사체 100 m → 3,0 m.
    (RLS-19 는 a_R = min(100, 20) → 1,342 m 이고 h_R ≥ 1 m — h = 1,5 m 는 RLS-19 에서만 채택)"""
    w, F = _wall_world(h)
    LmE = 60.0
    s, r = (200.0, 250.0, 0.5, LW_eq(LmE)), (280.0, 250.0, 0.8)
    L = run_solver([s], [r], w, F, order=1, rho=10 ** (-0.1), meth=R90)[0]
    hm = (0.5 + 0.8) / 2
    sd, si = math.hypot(80, 0.3), math.hypot(120, 0.3)
    Ld = LmE + Ds(sd) + DBM(hm, sd)
    Li = LmE - 1.0 + Ds(si) + DBM(hm, si)
    ref = 10 * math.log10(10 ** (0.1 * Ld) + (10 ** (0.1 * Li) if used else 0.0))
    assert L == pytest.approx(ref, abs=1e-9)


def test_terms_relation():
    """terms_rls90: D_A = 20 lg s + 10 lg 2π + s/200 − D_BM + D_z 와 D_s = 11,2 − 20 lg s − s/200 의 관계"""
    from sori import rls90_propagation as RP
    from sori.geometry import NGEOM, G_D, G_HM, G_SHIELD
    g = np.zeros(NGEOM); g[G_D] = 120.0; g[G_HM] = 2.0; g[G_SHIELD] = 0.0
    t = np.zeros(7)
    RP.terms_rls90(g, t)
    assert t[RP.T_DS] == pytest.approx(Ds(120.0))
    assert t[RP.T_DBM] == pytest.approx(DBM(2.0, 120.0))
    assert t[RP.T_DZ] == 0.0
    assert -t[RP.T_DA] == pytest.approx(t[RP.T_DS] + t[RP.T_DBM] - 11.2 - LG2PI, abs=1e-12)


# ======================================================================= 설정·메타데이터·전 과정
def _cfg(**upd):
    from sori import config
    return config._merge(config.DEFAULTS, {"receivers": {"center": [0, 0]}, **upd})


def test_config_validation():
    from sori import config
    from sori.propagation import get_model
    c = _cfg(propagation_method="rls90")
    config._validate(c)
    m = get_model(c)
    assert m.name == "rls90" and m.reflection_gain(c) == pytest.approx(10 ** -0.1)
    with pytest.raises(ValueError, match="D_E"):
        config._validate(_cfg(propagation_method="rls90", propagation={"reflection": {"D_E": 1.0}}))
    with pytest.raises(ValueError, match="단일값"):
        config._validate(_cfg(propagation_method="rls90",
                              propagation={"bands_hz": [500, 1000], "spectrum_dBA": [0, 0]}))


def test_source_offsets_outer_lane_centre():
    """RLS-90 4.3: 음원 = 두 바깥 차로 중앙(차로 수와 무관). RLS-19 Bild 6 은 방향당 3차로 이상에서 안쪽으로 옮김"""
    from sori import build
    o90 = build.source_offsets(3, 3, 3.0, False, edge=build._d_edge_rls90)
    o19 = build.source_offsets(3, 3, 3.0, False)
    assert o90 == [(-7.5, True), (7.5, False)]
    assert o19 == [(-6.0, True), (6.0, False)]


@pytest.fixture(scope="module")
def r90_case(tmp_path_factory):
    import reference_case as RC
    return RC.run_case(tmp_path_factory.mktemp("rls90"), method="rls90")


def test_case_run_info_and_outputs(r90_case):
    import pandas as pd
    info = json.loads((r90_case / "run_info.json").read_text(encoding="utf-8"))
    assert info["propagation_method"] == "rls90"
    assert info["outputs"] == {"grid": "rls90", "facade": "rls90"}
    assert info["emission"].startswith("RLS-90")
    assert json.loads((r90_case / "build_info.json").read_text(encoding="utf-8"))["emission"].startswith("RLS-90")
    G = pd.read_csv(r90_case / "grid_result.csv", encoding="utf-8-sig")
    assert set(np.unique(G.K)) <= {0.0, 1.0, 2.0, 3.0}
    S = pd.read_csv(r90_case / "sources.csv", encoding="utf-8-sig")
    assert (S.D_K == 0).all()
    from sori import rls90
    assert np.allclose(S.LWp_day - S.D_refl - rls90.LW_OFFSET, S.Lm_E_day, atol=1e-9)


def test_case_Lm_E_from_traffic(r90_case):
    """sources.csv 의 L_m,E 를 traffic_used.csv 로 다시 계산(식 6~9, p = p1 + p2)"""
    import pandas as pd
    from sori import rls90
    S = pd.read_csv(r90_case / "sources.csv", encoding="utf-8-sig")
    T = pd.read_csv(r90_case / "traffic_used.csv", encoding="utf-8-sig").set_index("fid")
    for r in S[S.along.notna()].head(20).itertuples():
        t = T.loc[r.fid]
        p = t.p1_day + t.p2_day
        ref = rls90.Lm_E(t.M_day * r.frac, p, t.v_pkw, t.v_lkw, t.surface, r.g)
        assert r.Lm_E_day == pytest.approx(ref, abs=1e-9)


def test_case_blocks_emission_mismatch(r90_case):
    """RLS-90 음원으로 만든 모델에 다른 방법으로 run 하면 오류(음원이 다름 → build 부터)"""
    from sori import config as C, run
    cfg = C.load(r90_case.parent / "config.yaml")
    cfg["propagation_method"] = "rls19"
    with pytest.raises(ValueError, match="build 부터"):
        run.main(cfg, ("grid",))
