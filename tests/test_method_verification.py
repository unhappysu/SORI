# -*- coding: utf-8 -*-
"""
전파 방법 검증(작업 4단계): 거리별 수계산, 경계 조건, RLS-19 식 (14) ≡ ISO 식 (10), 감쇠항 CSV 닫힘 확인.
기대값은 각 시험 주석의 계산 과정대로 시험 안에서 따로 계산함(같은 저자의 자기 일관성 확인).
공통 조건: 평탄·단단한 지면(G = 0), 점음원 높이 h_s = 0.5 m, 수음점 높이 h_r = 4 m, 차폐 없음, 반사 없음,
L_W = 100 dB, 500 Hz, α = 0(대기 흡수는 RLS 식 13 만 따로 들어감).
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import math

import numpy as np
import pytest

from sori import iso9613 as I
from sori import rls19_propagation as RP
from test_verification import flat_world, run_solver

HS, HR = 0.5, 4.0
HM = (HS + HR) / 2              # 가시선이 직선이고 지면이 평탄 → h_m = F/s_gr = (h_s + h_r)/2 = 2.25 m


@pytest.mark.parametrize("dp", [10.0, 50.0, 100.0, 200.0])
def test_rls19_distances_hand_calc(dp):
    """RLS-19: s = √(d_p² + 3.5²)
    D_div = 20 lg s + 10 lg 2π(= 7.982)              식 (12)
    D_atm = s/200                                    식 (13)
    D_gr  = max{4.8 − (2.25/s)(34 + 600/s); 0}       식 (14)  (d_p = 10 m 이면 음수 → 0)
    L = 100 − (D_div + D_atm + D_gr)                 식 (11), 차폐 없음
    예) d_p = 50: s = 50.1223, D_div = 33.999 + 7.982 = 41.981, D_atm = 0.251, D_gr = 4.8 − 0.04489·45.971 = 2.737"""
    s = math.hypot(dp, HR - HS)
    ddiv = 20 * math.log10(s) + 10 * math.log10(2 * math.pi)
    dgr = max(4.8 - HM / s * (34 + 600 / s), 0.0)
    if dp == 10.0:
        assert 4.8 - HM / s * (34 + 600 / s) < 0 and dgr == 0.0        # 경계: 음수 → 0
    L = run_solver([(100.0, 250.0, HS, 100.0)], [(100.0 + dp, 250.0, HR)], flat_world(), meth=0)[0]
    assert L == pytest.approx(100 - (ddiv + s / 200 + dgr), abs=1e-9)


@pytest.mark.parametrize("dp", [10.0, 50.0, 100.0, 200.0])
def test_iso_general_distances_hand_calc(dp):
    """ISO 일반법(G = 0): A_div = 20 lg d + 11 (식 7), A_atm = 0 (α = 0),
    A_s = A_r = −1.5 (표 3, 500 Hz, G = 0), A_m = −3q, q = 0 (d_p ≤ 30(h_s + h_r) = 135 m) 또는 1 − 135/d_p,
    A_gr = A_s + A_r + A_m (식 9), D_c = 0. 예) d_p = 200: q = 0.325, A_gr = −3 − 0.975 = −3.975"""
    d = math.hypot(dp, HR - HS)
    q = 0.0 if dp <= 135 else 1 - 135 / dp
    agr = -1.5 - 1.5 - 3 * q
    L = run_solver([(100.0, 250.0, HS, 100.0)], [(100.0 + dp, 250.0, HR)], flat_world(), meth=1)[0]
    assert L == pytest.approx(100 - (20 * math.log10(d) + 11 + agr), abs=1e-9)


@pytest.mark.parametrize("dp", [10.0, 50.0, 100.0, 200.0])
def test_iso_alternative_terms_hand_calc(dp):
    """ISO 대안법 항별 함수: A_gr = max{4.8 − (2h_m/d)(17 + 300/d); 0} (식 10),
    D_Ω = 10 lg{1 + [d_p² + (h_s − h_r)²]/[d_p² + (h_s + h_r)²]} (식 11).
    예) d_p = 10: D_Ω = 10 lg(1 + 112.25/120.25) = 10 lg 1.93347 = 2.8634 dB, A_gr 음수 → 0"""
    d = math.hypot(dp, HR - HS)
    agr = max(4.8 - (2 * HM / d) * (17 + 300 / d), 0.0)
    dom = 10 * math.log10(1 + (dp ** 2 + (HS - HR) ** 2) / (dp ** 2 + (HS + HR) ** 2))
    assert I.A_gr_alternative(HM, d) == pytest.approx(agr, abs=1e-12)
    assert I.D_omega(dp, HS, HR) == pytest.approx(dom, abs=1e-12)
    if dp == 10.0:
        assert dom == pytest.approx(2.8634, abs=1e-4) and agr == 0.0


def test_eq14_equals_eq10():
    """RLS-19 식 (14)  4.8 − (h_m/s)(34 + 600/s)
       ISO    식 (10)  4.8 − (2h_m/d)(17 + 300/d) = 4.8 − (h_m/d)(34 + 600/d)
    → s = d 이면 대수적으로 같은 식(34 = 2·17, 600 = 2·300). 두 식 모두 음수는 0.
    차이는 입력 정의뿐: RLS 는 h_m = F/s_gr(그림 11), ISO 는 그림 3 의 h_m = F/d(근거 미확인 — 코드는 둘 다 F/s_gr)"""
    hm = np.linspace(0.0, 30.0, 61); s = np.geomspace(1.0, 1000.0, 61)
    for a in hm:
        for b in s:
            assert RP.D_gr(a, b) == pytest.approx(I.A_gr_alternative(a, b), abs=1e-12)


def test_no_shielding_when_wall_below_line_of_sight():
    """경계: 장애물이 가시선을 넘지 않으면(z ≤ 0) 차폐 없음 — RLS-19 3.5.5, ISO 식 (18) z ≤ 0.
    벽 x = 140(높이 1 m), 가시선 높이 = 0.5 + 3.5·40.5/100 = 1.92 m > 1 m"""
    w = flat_world()
    gnd, dsm, bid, G, *_ = w
    dsm[:, 140] = 1.0; bid[:, 140] = 0
    for meth in (0, 1):
        L = run_solver([(100.0, 250.0, HS, 100.0)], [(200.0, 250.0, HR)], w, meth=meth)[0]
        L0 = run_solver([(100.0, 250.0, HS, 100.0)], [(200.0, 250.0, HR)], flat_world(), meth=meth)[0]
        assert L == pytest.approx(L0, abs=1e-12)


@pytest.fixture(scope="module", params=["iso9613_2", "rls19"])
def point_case(request, tmp_path_factory):
    import pandas as pd
    import reference_case as RC
    from sori import config as C, run
    work = tmp_path_factory.mktemp(request.param)
    RC.run_case(work, method=request.param)
    pd.DataFrame({"point_id": ["A", "B", "C"], "x": [205108.0, 205140.0, 205060.0],
                  "y": [553140.0, 553100.0, 553070.0], "h": [1.5, 4.0, 4.0]}).to_csv(work / "pts.csv", index=False)
    cfg = C.load(work / "config.yaml")
    cfg["propagation_method"] = request.param
    cfg["receivers"]["points"]["path"] = str(work / "pts.csv")
    cfg["propagation"]["terms_csv"] = True
    run.main(cfg, ("points",))
    out = work / "output"
    return request.param, pd.read_csv(out / "point_result.csv"), pd.read_csv(out / "path_terms.csv")


def test_terms_csv_closure(point_case):
    """path_terms.csv 의 경로별 L_day 를 에너지 합하면 solver 결과(LrT_direct, LrT_refl1)와 같아야 함"""
    meth, P, T = point_case
    assert set(T.propagation_method) == {meth}
    e = lambda v: 10 * np.log10(np.sum(10 ** (0.1 * np.asarray(v))))
    n_refl = 0
    for r in P[P.note.isna()].itertuples():
        t = T[T.point_id == r.point_id]
        assert e(t[t.order == 0].L_day) == pytest.approx(r.LrT_direct, abs=1e-6)
        if (t.order == 1).any():
            assert e(t[t.order == 1].L_day) == pytest.approx(r.LrT_refl1, abs=1e-6)
            n_refl += 1
    assert n_refl > 0                                                    # 반사 경로도 실제로 확인됨
    cols = ["D_div", "D_atm", "D_gr", "D_z", "D_RV", "D_A"] if meth == "rls19" else \
        ["A_div", "A_atm", "A_gr", "D_Omega", "D_z", "A_bar", "refl_dB", "A_net"]
    assert all(c in T.columns for c in cols)
    if meth == "rls19":                                                  # 식 (11) 항 합 확인
        assert np.allclose(T.D_div + T.D_atm + np.maximum(T.D_gr, T.D_z), T.D_A, atol=1e-9)
        assert np.allclose(T[T.order == 1].D_RV, 0.5, atol=1e-12)
    else:                                                                # A − D_c 항 합 확인
        assert np.allclose(T.A_div + T.A_atm + T.A_gr + T.A_bar - T.D_Omega, T.A_net, atol=1e-9)
