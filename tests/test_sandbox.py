# -*- coding: utf-8 -*-
"""평지 샌드박스(sori/sandbox.py) — 손계산·해석값과 비교. ※ Claude(Anthropic의 AI)를 활용하여 작성하였음."""
import math

import numpy as np
import pytest

from sori import sandbox as S

A500 = S.alpha_iso9613_1(500, 15, 70) / 1000.0


def _scene(kind, **kw):
    sc = S.default_scene(kind)
    sc.update(kw)
    return sc


def test_point_no_barrier_iso():
    """G = 0, 근거리(q = 0): A_gr = −3 → L = Lw − 20 lg d − 11 + 3 − α d"""
    sc = _scene("point", reflection=False, lateral=False)
    r = S.receiver_table(sc)[0]
    d = math.hypot(30, 1.0)
    assert r["L0"] == pytest.approx(100 - 20 * math.log10(d) - 11 + 3 - A500 * d, abs=1e-9)


def test_point_barrier_iso_hand_calc():
    """얇은 방음벽 1개: z = A + B − d, D_z 식 (14), A_bar = D_z − A_gr 식 (12)"""
    sc = _scene("point", reflection=False, lateral=False)
    r = S.receiver_table(sc)[0]
    d = math.hypot(30, 1.0)
    A, B = math.hypot(10, 3.5), math.hypot(20, 2.5)
    z = A + B - d
    lam = 340 / 500
    Kmet = math.exp(-1 / 2000 * math.sqrt(A * B * d / (2 * z)))
    Dz = 10 * math.log10(3 + 20 / lam * z * Kmet)
    assert r["L"] == pytest.approx(100 - 20 * math.log10(d) - 11 - A500 * d - Dz, abs=1e-9)
    det = S.path_detail(sc)
    assert det["after"]["z"] == pytest.approx(z, abs=1e-9)
    assert det["after"]["section"]["hull"][1] == pytest.approx((10.0, 4.0))


def test_line_source_matches_integral():
    """긴 선음원 = 조각 점음원 합 ≈ ISO 식으로 적분한 값(A_m 의 q 포함)"""
    sc = _scene("line", reflection=False, barriers=[], seg_len=0.5)
    sc["sources"][0].update(y1=-2000, y2=2000, h=1.5)
    L = S.receiver_table(sc)[0]["L"]
    y = np.arange(-2000, 2000, 0.01) + 0.005
    dp = np.hypot(30, y)
    q = np.where(dp <= 90, 0, 1 - 90 / dp)
    I = np.sum(10 ** (0.1 * (80 - 20 * np.log10(dp) - 11 + 3 + 3 * q - A500 * dp)) * 0.01)
    assert L == pytest.approx(10 * np.log10(I), abs=0.01)


def test_reflection_image_source():
    """음원·수음점이 방음벽 같은 쪽: 반사 성분 = 허상음원 거리의 자유음장 값 − 반사 손실"""
    sc = _scene("point", lateral=False)
    sc["receivers"] = [{"name": "R", "x": -10.0, "y": 5.0, "h": 1.5}]
    r = S.receiver_table(sc)[0]
    d = math.hypot(math.hypot(30, 5), 1.0)
    assert r["comp"]["reflection"] == pytest.approx(100 - 20 * math.log10(d) - 11 + 3 - A500 * d - 1.0, abs=1e-9)


def test_absorbing_barrier_no_reflection_and_lateral_short_wall():
    sc = _scene("point", lateral=False)
    sc["barriers"][0]["absorbing"] = True
    sc["receivers"] = [{"name": "R", "x": -10.0, "y": 5.0, "h": 1.5}]
    assert S.receiver_table(sc)[0]["comp"]["reflection"] < -100
    sc = _scene("point", reflection=False)                   # 짧은 벽 → 측면 회절이 IL 을 줄임
    sc["barriers"][0].update(y1=-3, y2=3)
    on = S.receiver_table(sc)[0]
    sc["lateral"] = False
    off = S.receiver_table(sc)[0]
    assert on["comp"]["lateral"] > -100 and on["IL"] < off["IL"]


def test_road_rls19_emission_and_il():
    sc = _scene("road")
    r = S.receiver_table(sc)
    lw = S.road_lw_prime(sc["sources"][0])
    sx, sy, sz, P, g = S.expand_sources(sc)
    assert 10 * math.log10(P.sum() / 200.0) == pytest.approx(lw, abs=1e-9)   # 200 m 도로 전체 파워 = LW′ + 10 lg 200
    assert np.all(sz == 0.5)
    assert r[0]["IL"] > r[1]["IL"] > 0


def test_map_shapes():
    sc = _scene("point")
    sc["grid"].update(res=5.0)
    m = S.compute_map(sc)
    assert m["after"].shape == (len(m["ys"]), len(m["xs"]))
    assert np.nanmax(m["il"]) > 10


def test_gui_barrier_length_and_height_fields():
    """선택 객체 칸: 방음벽 높이·길이(가운데점·방향 유지) 입력"""
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    import sori_sandbox as G
    p = G.SandboxPage()
    p.sel = ("barrier", 0)
    p.refresh(full=False)
    p._spins["_len"].setValue(100.0)
    p._edit_timer.timeout.emit()
    b = p.sc["barriers"][0]
    assert (b["x1"], b["y1"], b["x2"], b["y2"]) == (10.0, -50.0, 10.0, 50.0)
    p._spins["h"].setValue(6.0)
    p._edit_timer.timeout.emit()
    assert b["h"] == 6.0
    p._spins["y2"].setValue(60.0)
    assert p._spins["_len"].value() == pytest.approx(110.0)
