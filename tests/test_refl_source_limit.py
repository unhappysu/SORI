# -*- coding: utf-8 -*-
"""반사면–음원 거리 제한(propagation.reflection.r_refl_source, solver 인자 Rfs; SoundPLAN 'max. reflection
distance to source' 비교용, 2026-11-19): 평행 두 벽 장면에서 허상음원 해석값과 비교.
※ Claude(Anthropic의 AI)를 활용하여 제작하였음."""
import math

import numpy as np
import pytest

from sori import config
from sori.solver import compute_b, facade_neighbors
from test_high_order import LAM500, _scene

S = (200.0, 250.0, 0.5, 100.0)          # 음원: 벽 B(x = 151)에서 49 m, 벽 A(x = 300)에서 100 m
R = (280.0, 250.0, 4.0)                 # 수음점: 벽 A 에서 20 m, 벽 B 에서 129 m


def _solve(order, Rfs, Rf=1000.0):
    w, F = _scene()
    sx, sy, sz, LW = (np.array([v]) for v in S)
    P = 10 ** (0.1 * LW)
    nb = facade_neighbors(*F[:6], 1000.0)
    return compute_b(np.array([R[0]]), np.array([R[1]]), np.array([R[2]]), np.zeros(1), -np.ones(1, np.int64),
                     -np.ones(1, np.int64), order, sx, sy, sz, np.zeros(1), P, P, np.ones(1, np.bool_), *F, 0.8, *w,
                     1.0, 1000.0, 1000.0, Rf, 1000.0, 1000.0, 1000.0, 1.5, np.array([500.0]), np.array([LAM500]),
                     np.zeros(1), np.ones(1), False, 20.0, 20.0, 25.0, 1, *nb, False, np.zeros((0, 5)), Rfs)


def _Lp(dist, gain):                     # test_high_order 와 같은 ISO 9613-2 평지·G = 0 해석값
    q = 0.0 if dist <= 135 else 1 - 135 / dist
    return 100 - (20 * math.log10(math.hypot(dist, 3.5)) + 11 - 3 - 3 * q) + 10 * math.log10(gain)


def _e(*v):
    return 10 * math.log10(sum(10 ** (0.1 * x) for x in v))


@pytest.mark.parametrize("order", [1, 2, 3, 4])
def test_off_and_large_limit_equal_previous(order):
    """Rfs = 0(끔)과 아주 큰 Rfs(수음점 거리 Rf 도 충분히 큼)는 이전 결과와 비트 단위로 같음"""
    a, b = _solve(order, 0.0), _solve(order, 1e6)
    for k in range(7):
        assert a[k][0] == b[k][0]


@pytest.mark.parametrize("order", [1, 2, 3, 4])
def test_limit_keeps_only_reflectors_near_source(order):
    """Rfs = 60 m: 벽 B(음원 49 m)만 반사면이 될 수 있음 → 1차 B 반사(허상 거리 178 m)만 남고,
    두 벽을 모두 지나는 2차 이상 경로와 벽 A(음원 100 m) 1차 반사는 빠짐"""
    o = _solve(order, 60.0)
    assert 10 * math.log10(o[0][0]) == pytest.approx(_e(_Lp(80, 1), _Lp(178, 0.8)), abs=1e-6)
    assert 10 * math.log10(o[3][0]) == pytest.approx(_Lp(178, 0.8), abs=1e-6)       # E1 = 벽 B 1차만
    assert o[4][0] == 0.0 and o[5][0] == 0.0 and o[6][0] == 0.0                      # 2차·3차·4차 이상 없음


def test_limit_requires_every_reflector_near_receiver():
    """Rfs 를 켜면 앞쪽 반사면도 수음점 Rf 안이어야 함: Rf = 50 → 벽 B(수음점 129 m)를 지나는 경로 모두 빠짐.
    끈 상태(이전 동작)에서는 2차의 앞 반사면(B)에 수음점 거리 조건이 없어 2차 성분이 남음"""
    on = _solve(2, 1e6, Rf=50.0)
    off = _solve(2, 0.0, Rf=50.0)
    assert 10 * math.log10(on[3][0]) == pytest.approx(_Lp(120, 0.8), abs=1e-6)      # 벽 A 1차만
    assert on[4][0] == 0.0
    assert off[4][0] > 0.0


def test_config_validation(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("receivers: {center: [0, 0]}\npropagation: {reflection: {r_refl_source: 50}}\n", encoding="utf-8")
    assert config.load(p)["propagation"]["reflection"]["r_refl_source"] == 50
    p.write_text("receivers: {center: [0, 0]}\npropagation: {reflection: {r_refl_source: 0}}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        config.load(p)
    p.write_text("receivers: {center: [0, 0]}\n", encoding="utf-8")
    assert config.load(p)["propagation"]["reflection"]["r_refl_source"] is None
