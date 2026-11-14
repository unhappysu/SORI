# -*- coding: utf-8 -*-
"""반사 4차 이상(solver._order_n): 평행 두 벽 사이 허상음원 해석값, 3차 내장 탐색과의 일치, 설정 한도.
※ Claude(Anthropic의 AI)를 활용하여 제작하였음."""
import math

import numpy as np
import pytest

from sori import config
from sori.geometry import MAXO, MAXV, NGEOM
from sori.solver import HULL_N, _order_n, compute_b, facade_neighbors

LAM500 = 340.0 / 500.0


def _scene(n=500):
    gnd = np.zeros((n, n)); dsm = np.zeros((n, n)); bid = -np.ones((n, n), np.int64); G = np.zeros((n, n))
    dsm[150:350, 300] = 20.0; bid[150:350, 300] = 7                  # 벽 A: x = 300
    dsm[150:350, 150] = 20.0; bid[150:350, 150] = 8                  # 벽 B: x = 151 (마주보는 면)
    F = [np.array([300.0, 151.0]), np.array([150.0, 350.0]), np.array([300.0, 151.0]), np.array([350.0, 150.0]),
         np.array([-1.0, 1.0]), np.array([0.0, 0.0]), np.array([0.0, 0.0]), np.array([20.0, 20.0]),
         np.array([200.0, 200.0]), np.array([7, 8], np.int64)]
    return (gnd, dsm, bid, G, 0.0, float(n), 1.0, n, n), F


def _solve(src, rec, order, meth=1, rho=0.8):
    w, F = _scene()
    sx, sy, sz, LW = (np.array(v, float) for v in zip(*src))
    rx, ry, rz = (np.array(v, float) for v in zip(*rec))
    nb = facade_neighbors(*F[:6], 1000.0)
    P = 10 ** (0.1 * LW)
    return compute_b(rx, ry, rz, np.zeros(len(rx)), -np.ones(len(rx), np.int64), -np.ones(len(rx), np.int64), order,
                     sx, sy, sz, np.zeros(len(sx)), P, P, np.ones(len(sx), np.bool_), *F, rho, *w, 1.0,
                     1000.0, 1000.0, 1000.0, 1000.0, 1000.0, 1000.0, 1.5, np.array([500.0]), np.array([LAM500]),
                     np.zeros(1), np.ones(1), False, 20.0, 20.0, 25.0, meth, *nb, False, np.zeros((0, 5)))


def test_parallel_walls_orders_4_to_6_image_sources():
    """S(200) – R(280), 벽 300·151: n 차 허상 거리 = |S − R⁽ⁿ⁾|. 4차 516·676, 5차 716·774, 6차 814·974 m"""
    s = (200.0, 250.0, 0.5, 100.0); r = (280.0, 250.0, 4.0)

    def Lp(dist, gain):                       # 평지·G = 0 (ISO 9613-2 표 3: 135 m 넘으면 중간 영역 q)
        q = 0.0 if dist <= 135 else 1 - 135 / dist
        return 100 - (20 * math.log10(math.hypot(dist, 3.5)) + 11 - 3 - 3 * q) + 10 * math.log10(gain)
    e = lambda *v: 10 * math.log10(sum(10 ** (0.1 * x) for x in v))
    ref3 = e(Lp(80, 1), Lp(120, 0.8), Lp(178, 0.8), Lp(218, 0.64), Lp(378, 0.64), Lp(418, 0.512), Lp(476, 0.512))
    o3 = _solve([s], [r], 3)
    assert 10 * math.log10(o3[0][0]) == pytest.approx(ref3, abs=1e-6) and o3[6][0] == 0.0
    add = {4: (516, 676), 5: (716, 774), 6: (814, 974)}
    ref, parts = ref3, []
    for n in (4, 5, 6):
        parts += [Lp(d, 0.8 ** n) for d in add[n]]
        ref = e(ref3, *parts)
        o = _solve([s], [r], n)
        assert 10 * math.log10(o[0][0]) == pytest.approx(ref, abs=1e-6), n
        assert 10 * math.log10(o[6][0]) == pytest.approx(e(*parts), abs=1e-6), n    # 4차 이상 성분 합
        assert o[5][0] == o3[5][0] and o[4][0] == o3[4][0]                           # 3차 이하 성분은 그대로
    assert _solve([s], [r], 9)[0][0] == _solve([s], [r], MAXO)[0][0]                  # 한도(MAXO) 넘는 차수는 한도까지만


@pytest.mark.parametrize("meth", [0, 1, 2])
def test_order_n_equals_builtin_third_order(meth):
    """일반 탐색을 n = 3 으로 부르면 내장 3차 반사와 같은 에너지(비스듬한 경로 포함, 세 전파 방법)"""
    w, F = _scene()
    sy = np.arange(180.0, 321.0, 7.0); ns = len(sy)
    sx = np.full(ns, 200.0); sz = np.full(ns, 0.5); P = np.full(ns, 1e10)
    xr, yr, zr = 262.0, 233.0, 6.0
    nb = facade_neighbors(*F[:6], 1000.0)
    args = (sx, sy, sz, np.zeros(ns), P, P, np.ones(ns, np.bool_), *F, 0.8, *w, 1.0)
    o = compute_b(np.array([xr]), np.array([yr]), np.array([zr]), np.zeros(1), -np.ones(1, np.int64),
                  -np.ones(1, np.int64), 3, *args, 1000.0, 1000.0, 1000.0, 1000.0, 1000.0, 1000.0, 1.5,
                  np.array([500.0]), np.array([LAM500]), np.zeros(1), np.ones(1), False, 20.0, 20.0, 25.0, meth, *nb,
                  False, np.zeros((0, 5)))
    assert o[5][0] > 0.0
    hd = np.empty(HULL_N); hh = np.empty(HULL_N)
    ed, en = _order_n(3, xr, yr, zr, zr, -1, -1, np.array([0, 1], np.int64), 2, *args, 1000.0, 1000.0, 1000.0, 1.5,
                      np.array([500.0]), np.array([LAM500]), np.zeros(1), np.ones(1), False, 20.0, 20.0, 25.0, meth,
                      *nb, False, np.zeros((0, 5)), hd, hh, np.empty(NGEOM), np.empty(1), np.ones(1, np.bool_),
                      np.empty(MAXV), np.empty(MAXV), np.full(MAXO, -1, np.int64),
                      np.zeros(0, np.int64), np.zeros(0), np.zeros((0, 2 * MAXO)), np.zeros(1, np.int64), np.ones(2))
    assert ed == pytest.approx(o[5][0], rel=1e-12) and en == pytest.approx(ed)


def test_reflection_order_limit_in_config():
    c = config._merge(config.DEFAULTS, {"receivers": {"center": [0, 0]},
                                        "propagation": {"reflection": {"max_order_grid": MAXO}}})
    config._validate(c)
    c["propagation"]["reflection"]["max_order_point"] = MAXO + 1
    with pytest.raises(ValueError, match="max_order_point"):
        config._validate(c)
