# -*- coding: utf-8 -*-
"""방향별 화물차 비율 p1_{day,night}_{fwd,bwd}·p2_… (2026-11-19, SoundPLAN 처럼 방향마다 차종 구성이 다름).
fwd = 디지타이즈 방향, bwd = 반대. UFID 행만, 비우면 도로 공통 p1·p2(결과 불변). RLS-19 배출만.
※ Claude(Anthropic의 AI)를 활용하여 제작하였음."""
import math

import pytest

from test_dir_speed import ROW, _build, _cfg, _rec


def test_inputs_fill_and_source():
    from sori import inputs as I
    cfg = _cfg()
    out = I.traffic_for_road(cfg, ROW, {"U1": _rec(key="U1", p1_day=25, p2_day=0, p1_day_fwd=27.1,
                                                   p1_day_bwd=24.2)}, {}, True)
    assert out["dir_p_src"] == "ufid"
    assert (out["p1_day_fwd"], out["p1_day_bwd"], out["p2_day_fwd"], out["p2_day_bwd"]) == (27.1, 24.2, 0, 0)
    assert out["p1_night_fwd"] == out["p1_night_bwd"] == out["p1_night"]       # 야간 빈칸 → 공통
    out = I.traffic_for_road(cfg, ROW, {"U1": _rec(key="U1", p1_day=25)}, {}, True)
    assert out["dir_p_src"] == "same" and out["p1_day_fwd"] == out["p1_day_bwd"] == 25


def test_name_row_ignored_and_bad_value():
    from sori import inputs as I
    cfg = _cfg()
    out = I.traffic_for_road(cfg, ROW, {}, {"R": _rec(key="R", p1_day=25, p1_day_fwd=30)}, True)
    assert out["dir_p_src"] == "name_key_ignored" and out["p1_day_fwd"] == 25
    with pytest.raises(RuntimeError, match="p1_day_bwd"):
        I.traffic_for_road(cfg, ROW, {"U1": _rec(key="U1", p1_day_bwd=120)}, {}, True)
    with pytest.raises(RuntimeError, match="100"):
        I.traffic_for_road(cfg, ROW, {"U1": _rec(key="U1", p1_day_fwd=70, p2_day_fwd=40)}, {}, True)


def test_build_uses_direction_share(tmp_path):
    """양방향 2개선 도로에 fwd p1 30 / bwd p1 10 → 음원선마다 그 방향 비율의 L_W′, 다른 도로 불변"""
    import numpy as np
    from sori import rls19
    T0, S0 = _build(tmp_path / "a", "rls19")
    two = S0.groupby("fid").along.nunique()
    fid = int(two[two == 2].index[0])
    base = T0.set_index("fid").loc[fid]
    extra = {"key": str(base.ufid), "key_type": "ufid", "M_day": str(base.M_day), "M_night": str(base.M_night),
             "p1_day": "20", "p2_day": "0", "p1_night": "20", "p2_night": "0", "v_pkw": "50",
             "p1_day_fwd": "30", "p1_day_bwd": "10", "surface": str(base.surface), "road_type": str(base.road_type)}
    T1, S1 = _build(tmp_path / "b", "rls19", extra)
    t = T1.set_index("fid").loc[fid]
    assert t.dir_p_src == "ufid"
    s = S1[S1.fid == fid]
    for along, p1 in ((True, 30.0), (False, 10.0)):
        r = s[s.along == along].iloc[0]
        g = r.g if along else -r.g
        exp = rls19.LW_prime(t.M_day * r.frac, p1, 0.0, 50.0, 50.0, t.surface, g, r.D_K, r.D_refl)
        assert abs(r.LWp_day - exp) < 1e-9
        rn = rls19.LW_prime(t.M_night * r.frac, 20.0, 0.0, 50.0, 50.0, t.surface, g, r.D_K, r.D_refl)
        assert abs(r.LWp_night - rn) < 1e-9                                       # 야간은 공통 20 %
    a = S0[S0.fid != fid].reset_index(drop=True); b = S1[S1.fid != fid].reset_index(drop=True)
    assert len(a) == len(b) and np.abs(a.LW_day - b.LW_day).max() < 1e-9


def test_app_preview_lw_with_direction_share():
    pytest.importorskip("PySide6")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import sori_qt as Q
    from sori import rls19
    tc = {"default_road_type": "gemeinde", "default_speed_main": 50.0}
    rec = {"key": "U1", "key_type": "ufid", "M_day": "1000", "M_night": "200", "p1_day": "20", "p2_day": "0",
           "p1_night": "20", "p2_night": "0", "v_pkw": "50", "surface": "ref"}
    same = Q.lw_prime_row(rec, tc)[0]
    assert abs(same - rls19.LW_prime(1000, 20, 0, 50, 50, "ref", 0.0)) < 1e-12
    d = Q.lw_prime_row({**rec, "p1_day_fwd": "30", "p1_day_bwd": "10"}, tc)[0]
    e = sum(10 ** (0.1 * rls19.LW_prime(500, p, 0, 50, 50, "ref", 0.0)) for p in (30, 10))
    assert abs(d - 10 * math.log10(e)) < 1e-9
    assert Q.lw_prime_row({**rec, "key_type": "name", "p1_day_fwd": "30"}, tc)[0] == same
