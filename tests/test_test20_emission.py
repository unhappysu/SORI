# -*- coding: utf-8 -*-
"""
RLS-19 공식 시험과제 TEST-20 v2.1(2021-09-16, BASt) — 방출 과제 E1~E7.
원문: 02_기준원문_참고자료/원문/TEST-20_v2.1_2021-09-16.pdf (표 1~8).
합격 기준(TEST-20 3절): 소수 둘째 자리로 반올림한 값이 시험결과와 1/100 dB 넘게 다르지 않음.
반올림은 IEEE 754 짝수 반올림(TEST-20 각주 1) — Python round() 와 같음.
Krad(이륜자동차)는 RLS-19 표 3 아래 주석: Lkw2 의 L_W0, 속도 v_Pkw, D_SD = 0, D_LN 은 식 (7c) + v_Pkw.
SORI 의 음원선 L_W′(rls19.LW_prime)에는 Krad 차량군이 없음 → E7 외곽 1(p_M = 10 %)은 미지원(xfail).
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import math

import pytest

from sori import rls19 as R

TOL = 0.01 + 1e-9


def r2(x):
    return round(x, 2)


def krad_LW(v_pkw, g, dk, drefl):
    """RLS-19 주석(표 3 아래): Lkw2 L_W0(v_Pkw) + D_SD 0 + D_LN 식 (7c)(v_Pkw) + D_K + D_refl"""
    return R.LW0("Lkw2", v_pkw) + R.D_LN("Lkw2", g, v_pkw) + dk + drefl


# ------------------------------------------------------------------ E1 표 1: L_W0, L_W′(1대/h)
E1 = [("Pkw", 30, 94.49, 49.72), ("Pkw", 50, 100.43, 53.44), ("Pkw", 100, 109.42, 59.42), ("Pkw", 130, 112.89, 61.75),
      ("Lkw1", 30, 101.40, 56.63), ("Lkw1", 50, 105.90, 58.91), ("Lkw1", 80, 113.55, 64.51), ("Lkw1", 90, 115.68, 66.13),
      ("Lkw2", 30, 105.74, 60.97), ("Lkw2", 50, 108.41, 61.42), ("Lkw2", 80, 115.78, 66.75), ("Lkw2", 90, 118.10, 68.55),
      ("Krad", 30, 105.74, 60.97), ("Krad", 50, 108.41, 61.42), ("Krad", 100, 120.24, 70.24), ("Krad", 130, 125.69, 74.55)]


@pytest.mark.parametrize("fzg, v, lw0, lwp", E1)
def test_E1_base_value(fzg, v, lw0, lwp):
    """식 (6)·표 3. 20 km/h 는 30 km/h 로(2장, speed_rule). L_W′(1대/h) = L_W0 + 10 lg(1/v) − 30 (식 4, M = 1)"""
    g = "Lkw2" if fzg == "Krad" else fzg
    a = R.LW0(g, R.speed_rule(v))
    assert abs(r2(a) - lw0) <= TOL
    assert abs(r2(a + 10 * math.log10(1 / R.speed_rule(v)) - 30) - lwp) <= TOL
    assert R.LW0(g, R.speed_rule(20)) == R.LW0(g, 30.0)                      # 표 1 의 20 km/h 행


# ------------------------------------------------------------------ E2 표 2·3: D_SD
E2 = [("ref", "Pkw", 50, 0.0), ("ref", "Pkw", 120, 0.0), ("ref", "Lkw", 50, 0.0), ("ref", "Lkw", 80, 0.0),
      ("SMA5_8", "Pkw", 50, -2.6), ("SMA5_8", "Lkw", 50, -1.8), ("SMA8_11", "Pkw", 120, -1.8), ("SMA8_11", "Lkw", 80, -2.0),
      ("AC11", "Pkw", 50, -2.7), ("AC11", "Pkw", 120, -1.9), ("AC11", "Lkw", 50, -1.9), ("AC11", "Lkw", 80, -2.1),
      ("PA11", "Pkw", 120, -4.5), ("PA11", "Lkw", 80, -4.4), ("PA8", "Pkw", 120, -5.5), ("PA8", "Lkw", 80, -5.4),
      ("Waschbeton", "Pkw", 120, -1.4), ("Waschbeton", "Lkw", 80, -2.3), ("LGA", "Pkw", 120, -2.0), ("LGA", "Lkw", 80, -1.5),
      ("LOA", "Pkw", 50, -3.2), ("LOA", "Lkw", 50, -1.0), ("SMA_LA8", "Pkw", 120, -2.8), ("SMA_LA8", "Lkw", 80, -4.6),
      ("DSH_V5", "Pkw", 50, -3.9), ("DSH_V5", "Pkw", 120, -2.8), ("DSH_V5", "Lkw", 50, -0.9), ("DSH_V5", "Lkw", 80, -2.3),
      ("pflaster_even", "Pkw", 30, 1.0), ("pflaster_even", "Pkw", 40, 2.0), ("pflaster_even", "Pkw", 50, 3.0),
      ("pflaster_even", "Pkw", 60, 3.0), ("pflaster_other", "Pkw", 30, 5.0), ("pflaster_other", "Pkw", 40, 6.0),
      ("pflaster_other", "Pkw", 50, 7.0), ("pflaster_other", "Pkw", 60, 7.0)]


@pytest.mark.parametrize("sdt, fzg, v, ref", E2)
def test_E2_surface(sdt, fzg, v, ref):
    assert abs(r2(R.D_SD(sdt, fzg, v)) - ref) <= TOL


@pytest.mark.parametrize("sdt, fzg, v", [("SMA5_8", "Pkw", 120), ("SMA8_11", "Lkw", 50), ("LOA", "Lkw", 80)])
def test_E2_not_provided_warns(sdt, fzg, v):
    """표 2 각주 1: 원문에 값이 없는 조합은 경고(TEST-20: 'entsprechenden Warnhinweis')"""
    R._warned.clear()
    with pytest.warns(UserWarning):
        R.D_SD(sdt, fzg, v)


# ------------------------------------------------------------------ E3 표 4: D_LN (Krad = Lkw2 식 7c, v_Pkw)
E3 = [(-15, "Pkw", 50, 2.00), (-15, "Pkw", 130, 1.00), (-11, "Pkw", 50, 1.67), (-11, "Pkw", 130, 0.83),
      (-11, "Lkw1", 50, 2.62), (-11, "Lkw1", 90, 6.12), (-11, "Lkw2", 50, 4.38), (-11, "Lkw2", 90, 7.88),
      (-2, "Pkw", 30, 0.0), (-2, "Pkw", 100, 0.0), (-2, "Lkw1", 30, 0.0), (-2, "Lkw1", 90, 0.0),
      (-2, "Lkw2", 30, 0.0), (-2, "Lkw2", 90, 0.0), (10, "Pkw", 50, 0.96), (10, "Pkw", 130, 1.60),
      (10, "Lkw1", 50, 4.00), (10, "Lkw1", 90, 7.20), (10, "Lkw2", 50, 4.80), (10, "Lkw2", 90, 8.00),
      (10, "Krad", 50, 4.80), (10, "Krad", 100, 8.80), (15, "Krad", 50, 6.00), (15, "Krad", 130, 14.00)]


@pytest.mark.parametrize("g, fzg, v, ref", E3)
def test_E3_grade(g, fzg, v, ref):
    """식 (7a)~(7c), 경사는 ±12 % 로 제한. Lkw1 −11 %·50 km/h 정확값 21/8 = 2.625 → 짝수 반올림 2.62(TEST-20 각주 2)"""
    assert abs(r2(R.D_LN("Lkw2" if fzg == "Krad" else fzg, g, v)) - ref) <= TOL


# ------------------------------------------------------------------ E4 표 5: D_K
@pytest.mark.parametrize("kt, x, ref", [("signal", 0, 3.0), ("signal", 50, 1.75), ("signal", 200, 0.0),
                                        ("roundabout", 0, 2.0), ("roundabout", 50, 1.17), ("roundabout", 200, 0.0),
                                        ("other", 0, 0.0), ("other", 50, 0.0), ("other", 200, 0.0)])
def test_E4_junction(kt, x, ref):
    assert abs(r2(R.D_K(x, kt)) - ref) <= TOL


# ------------------------------------------------------------------ E5 표 6: D_refl
@pytest.mark.parametrize("h, w, ref", [(1, 6, 0.33), (1, 14, 0.14), (1, 55, 0.04), (1, 200, 0.0),
                                       (5, 6, 1.60), (5, 14, 0.71), (5, 55, 0.18), (5, 200, 0.0),
                                       (10, 6, 1.60), (10, 14, 1.43), (10, 55, 0.36), (10, 200, 0.0),
                                       (20, 6, 1.60), (20, 14, 1.60), (20, 55, 0.73), (20, 200, 0.0)])
def test_E5_multiple_reflection(h, w, ref):
    assert abs(r2(R.D_refl(h, w)) - ref) <= TOL


# ------------------------------------------------------------------ E6 표 7, E7 표 8
# (시나리오, 노면, 경사, D_K, D_refl, {차량군: (v, L_W 기대값)})
SCEN = {
    "innerorts1": ("AC11", -2, R.D_K(200, "other"), R.D_refl(1, 55),
                   {"Pkw": (30, 91.83), "Lkw1": (30, 99.53), "Lkw2": (30, 103.88)}),
    "innerorts2": ("pflaster_other", 10, R.D_K(50, "signal"), R.D_refl(10, 6),
                   {"Pkw": (50, 111.74), "Lkw1": (50, 120.25), "Lkw2": (50, 123.56)}),
    "ausserorts1": ("PA8", -2, R.D_K(200, "other"), R.D_refl(0, 200),
                    {"Pkw": (100, 103.92), "Lkw1": (80, 108.15), "Lkw2": (80, 110.38), "Krad": (100, 120.24)}),
    "ausserorts2": ("ref", 10, 0.0, R.D_refl(10, 55),
                    {"Pkw": (130, 114.85), "Lkw1": (90, 123.24), "Lkw2": (90, 126.46)}),
}


@pytest.mark.parametrize("name, fzg", [(n, f) for n, s in SCEN.items() for f in s[4]])
def test_E6_vehicle_level(name, fzg):
    sdt, g, dk, dr, veh = SCEN[name]
    v, ref = veh[fzg]
    LW = krad_LW(v, g, dk, dr) if fzg == "Krad" else R.LW_vehicle(fzg, v, sdt, g, dk, dr)
    assert abs(r2(LW) - ref) <= TOL


E7 = [("innerorts1", 200, 0, 0, 0, 70.07), ("innerorts2", 200, 3, 4, 0, 90.19),
      ("ausserorts1", 1000, 3, 11, 10, 91.52), ("ausserorts2", 10000, 10, 25, 0, 112.09)]


@pytest.mark.parametrize("name, M, p1, p2, pM, ref", E7)
def test_E7_line_level(name, M, p1, p2, pM, ref):
    sdt, g, dk, dr, veh = SCEN[name]
    if pM:
        pytest.xfail("SORI 의 rls19.LW_prime 에는 Krad(이륜자동차) 차량군이 없음 — 미지원")
    L = R.LW_prime(M, p1, p2, veh["Pkw"][0], veh["Lkw1"][0], sdt, g, dk, dr)
    assert abs(r2(L) - ref) <= TOL


def test_E7_krad_by_hand():
    """참고: Krad 를 별도 차량군(비율 p_M, 속도 v_Pkw)으로 더한 식 (4) 로 외곽 1 을 손계산하면 시험결과와 같은지
    (p_M 이 p2 에 포함되는지 따로인지 TEST-20 에 없으므로 두 해석 중 맞는 쪽을 기록)"""
    sdt, g, dk, dr, veh = SCEN["ausserorts1"]
    vP, vL = veh["Pkw"][0], veh["Lkw1"][0]
    lw = {f: R.LW_vehicle(f, veh[f][0], sdt, g, dk, dr) for f in ("Pkw", "Lkw1", "Lkw2")}
    lk = krad_LW(vP, g, dk, dr)

    def lwp(pP, p1, p2, pM):
        t = (pP / 100 * 10 ** (0.1 * lw["Pkw"]) / vP + p1 / 100 * 10 ** (0.1 * lw["Lkw1"]) / vL
             + p2 / 100 * 10 ** (0.1 * lw["Lkw2"]) / vL + pM / 100 * 10 ** (0.1 * lk) / vP)
        return 10 * math.log10(1000) + 10 * math.log10(t) - 30
    separate = lwp(100 - 3 - 11 - 10, 3, 11, 10)          # Krad 가 Pkw 비율에서 빠짐
    assert abs(r2(separate) - 91.52) <= TOL
