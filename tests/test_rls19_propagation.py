# -*- coding: utf-8 -*-
"""
RLS-19 전파 경로(propagation_method: rls19) 검증 시험.
기대값은 RLS-19 3.5 식 (11)~(17), 3.6·표 8 을 시험 안에서 따로 다시 계산한 값(수계산).
같은 저자가 같은 식을 다시 계산한 자기 일관성 확인이며, 공식 시험과제(TEST-20) 대조가 아님.
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import json
import math

import numpy as np
import pytest

from test_verification import flat_world, run_solver

RLS = 0                                    # solver.METH_RLS19
LG2PI = 10 * math.log10(2 * math.pi)


def D_A_free(s, hm):
    """식 (11)~(14), 차폐 없음"""
    return 20 * math.log10(s) + LG2PI + s / 200 + max(4.8 - hm / s * (34 + 600 / s), 0.0)


def D_z(A, B, s, z):
    """식 (15)(17)"""
    return 10 * math.log10(3 + 80 * z * math.exp(-1 / 2000 * math.sqrt(A * B * s / (2 * z))))


def test_method_codes_match():
    from sori import solver
    from sori.propagation import METHODS
    assert METHODS["rls19"].code == solver.METH_RLS19 == RLS
    assert METHODS["iso9613_2"].code == solver.METH_ISO


def test_rls19_free_field_point():
    """평지·차폐 없음. h_m = F/s_gr: 가시선이 0.5 → 4 m 로 선형이므로 F/s_gr = (0.5 + 4)/2"""
    w = flat_world()
    L = run_solver([(100, 250, 0.5, 100.0)], [(150, 250, 4.0)], w, meth=RLS)[0]
    s = math.hypot(50, 3.5)
    assert L == pytest.approx(100 - D_A_free(s, 2.25), abs=1e-9)


def test_rls19_far_ground_attenuation_positive():
    """먼 거리에서는 D_gr(식 14) > 0 이 적용됨(ISO 일반법 G=0 의 −3 dB 와 다름)"""
    w = flat_world()
    L = run_solver([(50, 250, 0.5, 100.0)], [(450, 250, 4.0)], w, meth=RLS)[0]
    s = math.hypot(400, 3.5)
    dgr = 4.8 - 2.25 / s * (34 + 600 / s)
    assert dgr > 4.0
    assert L == pytest.approx(100 - (20 * math.log10(s) + LG2PI + s / 200 + dgr), abs=1e-9)


def test_rls19_single_diffraction_max_rule():
    """얇은 벽 단일 회절: D_A = D_div + D_atm + max{D_gr; D_z}, D_z 계수 80, 상한 없음"""
    w = flat_world()
    gnd, dsm, bid, G, *_ = w
    dsm[:, 200] = 6.0; bid[:, 200] = 0
    xs, xr = 150.0, 260.0
    L = run_solver([(xs, 250.0, 0.5, 100.0)], [(xr, 250.0, 4.0)], w, meth=RLS)[0]
    ex = 200.5                                            # 회절 모서리 = 벽 상단 래스터 표본
    A = math.hypot(ex - xs, 6 - 0.5); B = math.hypot(xr - ex, 6 - 4.0)
    s = math.hypot(xr - xs, 3.5); z = A + B - s
    dgr = max(4.8 - 2.25 / s * (34 + 600 / s), 0.0)
    dz = D_z(A, B, s, z)
    assert dz > dgr
    assert L == pytest.approx(100 - (20 * math.log10(s) + LG2PI + s / 200 + max(dgr, dz)), abs=1e-9)


def test_rls19_double_diffraction_C_added_to_larger():
    """다중 회절(그림 13): z = A + B + C − s, 식 (17) 에서 C 를 A·B 중 큰 쪽에 더함"""
    w = flat_world()
    gnd, dsm, bid, G, *_ = w
    dsm[:, 200] = 6.0; bid[:, 200] = 0
    dsm[:, 230] = 6.0; bid[:, 230] = 1
    xs, xr = 150.0, 260.0
    L = run_solver([(xs, 250.0, 0.5, 100.0)], [(xr, 250.0, 4.0)], w, meth=RLS)[0]
    e1, e2 = 200.5, 230.5
    A = math.hypot(e1 - xs, 5.5); B = math.hypot(xr - e2, 2.0); C = e2 - e1
    s = math.hypot(xr - xs, 3.5); z = A + B + C - s
    assert A > B
    dz = D_z(A + C, B, s, z)
    dgr = max(4.8 - 2.25 / s * (34 + 600 / s), 0.0)
    assert L == pytest.approx(100 - (20 * math.log10(s) + LG2PI + s / 200 + max(dgr, dz)), abs=1e-9)


def _wall_world(h):
    w = flat_world()
    gnd, dsm, bid, G, *_ = w
    dsm[150:350, 300] = h; bid[150:350, 300] = 7
    F = [np.array([300.0]), np.array([150.0]), np.array([300.0]), np.array([350.0]),
         np.array([-1.0]), np.array([0.0]), np.array([0.0]), np.array([h]), np.array([200.0]),
         np.array([7], np.int64)]
    return w, F


@pytest.mark.parametrize("h, used", [(20.0, True), (1.5, True), (1.2, False), (0.9, False)])
def test_rls19_first_order_reflection_DRV_and_height_rule(h, used):
    """3.6: 허상음원 L_W − D_RV(표 8, 건물 외벽 0.5 dB). 조건 h_R ≥ 1.0 m 이고 h_R ≥ 0.3·√a_R.
    a_R = min(음원–반사점 100 m, 반사점–수음점 20 m) = 20 → 0.3·√20 = 1.342 m.
    수음점을 0.8 m 로 낮춰 반사점 높이(0.75 m)가 모든 벽 높이 안에 들도록 함(기하 조건이 아니라 3.6 조건만 시험)"""
    w, F = _wall_world(h)
    gain = 10 ** (-0.1 * 0.5)
    s, r = (200.0, 250.0, 0.5, 100.0), (280.0, 250.0, 0.8)
    L = run_solver([s], [r], w, F, order=1, rho=gain, meth=RLS)[0]
    hm = (0.5 + 0.8) / 2
    Ld = 100 - D_A_free(math.hypot(80, 0.3), hm)
    Li = 100 - 0.5 - D_A_free(math.hypot(120, 0.3), hm)
    ref = 10 * math.log10(10 ** (0.1 * Ld) + (10 ** (0.1 * Li) if used else 0.0))
    assert L == pytest.approx(ref, abs=1e-9)


def test_rls19_reflection_ignores_iso_size_criterion():
    """ISO 식 (19) 는 rls19 에서 쓰지 않음: 짧은 벽(길이 2 m)도 높이 조건만 맞으면 반사 채택"""
    w, F = _wall_world(20.0)
    F[1] = np.array([249.0]); F[3] = np.array([251.0]); F[8] = np.array([2.0])     # y 249~251, L = 2 m
    s, r = (200.0, 250.0, 0.5, 100.0), (280.0, 250.0, 4.0)
    gain = 10 ** (-0.05)
    L_rls = run_solver([s], [r], w, F, order=1, rho=gain, meth=RLS)[0]
    L_dir = run_solver([s], [r], w, None, order=0, meth=RLS)[0]
    assert L_rls > L_dir + 1.0
    L_iso = run_solver([s], [r], w, F, order=1, rho=0.8, meth=1)[0]
    L_iso_dir = run_solver([s], [r], w, None, order=0, meth=1)[0]
    assert L_iso == pytest.approx(L_iso_dir, abs=1e-12)                  # ISO: 식 (19) 불충족 → 반사 무시


# ======================================================================= 설정·메타데이터·혼용 방지
def _cfg(**upd):
    from sori import config
    return config._merge(config.DEFAULTS, {"receivers": {"center": [0, 0]}, **upd})


def test_config_default_and_validation():
    from sori import config
    from sori.propagation import get_model
    c = _cfg()
    config._validate(c)
    assert get_model(c).name == "iso9613_2"                           # 기본값 = 현행(ISO) 경로
    with pytest.raises(ValueError, match="propagation_method"):
        config._validate(_cfg(propagation_method="iso"))
    with pytest.raises(ValueError, match="rls19"):
        config._validate(_cfg(propagation_method="rls19",
                              propagation={"bands_hz": [500, 1000], "spectrum_dBA": [0, 0]}))
    with pytest.raises(ValueError, match="D_RV"):
        config._validate(_cfg(propagation={"reflection": {"D_RV": -1}}))


def test_model_metadata():
    from sori.propagation import get_model
    iso = get_model(_cfg()).info(_cfg())
    assert iso["propagation_method"] == "iso9613_2"
    assert "어느 표준도 규정하지 않은 프로젝트 구성 방법" in iso["method_label"]
    assert iso["representative_frequency_hz"] == 500.0
    assert any("측면 회절" in x for x in iso["not_implemented"])
    c = _cfg(propagation_method="rls19")
    r = get_model(c).info(c)
    assert r["propagation_method"] == "rls19" and r["representative_frequency_hz"] is None
    assert "표 8" in r["reflection_loss"] and r["D_refl_in_source"] is True


@pytest.fixture(scope="module")
def rls_case(tmp_path_factory):
    import reference_case as RC
    return RC.run_case(tmp_path_factory.mktemp("rls"), method="rls19")


def test_run_info_records_method_and_blocks_mixing(rls_case):
    from sori import config as C, run
    info = json.loads((rls_case / "run_info.json").read_text(encoding="utf-8"))
    assert info["propagation_method"] == "rls19"
    assert info["outputs"] == {"grid": "rls19", "facade": "rls19"}
    assert info["equations"][:2] == ["RLS-19 식 (2)", "식 (11) D_A"]
    cfg = C.load(rls_case.parent / "config.yaml")                     # 기본값 iso9613_2
    with pytest.raises(ValueError, match="다른 전파 방법"):
        run.main(cfg, ("grid",))


def test_rls19_case_differs_from_iso(rls_case, tmp_path):
    """같은 기하·음원에서 전파식만 다르므로 결과가 달라야 함(음원 sources.csv 는 같아야 함)"""
    import pandas as pd
    import reference_case as RC
    exp = pd.read_csv(RC.EXPECTED / "sources.csv", encoding="utf-8-sig")
    got = RC.extract(rls_case)
    assert np.allclose(got["sources.csv"]["LW_day"].to_numpy(), exp["LW_day"].to_numpy(), atol=1e-9)
    g_iso = pd.read_csv(RC.EXPECTED / "grid_result.csv", encoding="utf-8-sig")
    diff = got["grid_result.csv"]["LrT"].to_numpy() - g_iso["LrT"].to_numpy()
    assert np.nanmax(np.abs(diff)) > 0.5
