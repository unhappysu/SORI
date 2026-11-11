# -*- coding: utf-8 -*-
"""방향별 속도 v_pkw_fwd·v_lkw_fwd·v_pkw_bwd·v_lkw_bwd (2026-11-11, SoundPLAN 처럼 방향마다 다른 속도).
fwd = 디지타이즈 방향, bwd = 반대. UFID 행만, 비우면 도로 공통 v_pkw·v_lkw(결과 불변).
※ Claude(Anthropic의 AI)를 활용하여 제작하였음."""
import shutil

import numpy as np
import pandas as pd
import pytest

import reference_case as RC


def _rec(**kw):
    from sori import inputs as I
    r = dict.fromkeys(I.TRAFFIC_COLS, "")
    r.update(key="K", M_day="1000", M_night="200")
    r.update({k: str(v) for k, v in kw.items()})
    return r


def _cfg():
    from sori import config as C
    import copy
    cfg = copy.deepcopy(C.DEFAULTS)
    cfg["receivers"]["center"] = [0.0, 0.0]
    return cfg


ROW = {"ufid": "U1", "rname": "R", "axis": "R", "lanes": 4, "oneway": False, "surface_attr": ""}


def test_inputs_fill_and_source():
    from sori import inputs as I
    cfg = _cfg()
    out = I.traffic_for_road(cfg, ROW, {"U1": _rec(key="U1", v_pkw=40, v_pkw_fwd=30, v_pkw_bwd=50)}, {}, True)
    assert (out["v_pkw_fwd"], out["v_pkw_bwd"]) == (30, 50) and out["dir_speed_src"] == "ufid"
    assert (out["v_lkw_fwd"], out["v_lkw_bwd"]) == (30, 50)          # v_lkw 빈칸 → 그 방향 승용 속도
    out = I.traffic_for_road(cfg, ROW, {"U1": _rec(key="U1", v_pkw=40, v_lkw=35, v_pkw_bwd=50, v_lkw_bwd=45)}, {}, True)
    assert (out["v_pkw_fwd"], out["v_lkw_fwd"], out["v_pkw_bwd"], out["v_lkw_bwd"]) == (40, 35, 50, 45)
    out = I.traffic_for_road(cfg, ROW, {"U1": _rec(key="U1", v_pkw=40)}, {}, True)        # 입력 없음 → 공통
    assert out["dir_speed_src"] == "same" and out["v_pkw_fwd"] == out["v_pkw_bwd"] == out["v_pkw"] == 40
    assert out["v_lkw_fwd"] == out["v_lkw_bwd"] == out["v_lkw"]


def test_name_row_ignored_and_bad_value():
    from sori import inputs as I
    cfg = _cfg()
    out = I.traffic_for_road(cfg, ROW, {}, {"R": _rec(key="R", v_pkw=40, v_pkw_fwd=30)}, True)
    assert out["dir_speed_src"] == "name_key_ignored" and out["v_pkw_fwd"] == 40
    with pytest.raises(RuntimeError, match="v_pkw_bwd"):
        I.traffic_for_road(cfg, ROW, {"U1": _rec(key="U1", v_pkw_bwd=0)}, {}, True)


def test_app_preview_lw_with_direction_speed():
    """앱 교통량 표의 LW′ 미리보기: 방향별 속도가 있으면 두 방향(각 M/2) 에너지 합, 없으면 이전과 같음"""
    pytest.importorskip("PySide6")
    import math
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import sori_qt as Q
    from sori import rls19
    tc = {"default_road_type": "gemeinde", "default_speed_main": 50.0}
    rec = {"key": "U1", "key_type": "ufid", "M_day": "1000", "M_night": "200", "p1_day": "5", "p2_day": "2",
           "p1_night": "5", "p2_night": "2", "v_pkw": "40", "surface": "ref"}
    same = Q.lw_prime_row(rec, tc)[0]
    assert abs(same - rls19.LW_prime(1000, 5, 2, 40, 40, "ref", 0.0)) < 1e-12
    d = Q.lw_prime_row({**rec, "v_pkw_fwd": "30", "v_pkw_bwd": "50"}, tc)[0]
    e = 10 ** (0.1 * rls19.LW_prime(500, 5, 2, 30, 30, "ref", 0.0)) + 10 ** (0.1 * rls19.LW_prime(500, 5, 2, 50, 50, "ref", 0.0))
    assert abs(d - 10 * math.log10(e)) < 1e-9
    assert Q.lw_prime_row({**rec, "key_type": "name", "v_pkw_fwd": "30"}, tc)[0] == same   # 도로명 행은 무시


def _build(tmp_path, method, extra=None):
    from sori import config as C, build
    shutil.copytree(RC.INPUT, tmp_path, dirs_exist_ok=True)
    cfg = C.load(tmp_path / "config.yaml")
    cfg["propagation_method"] = method
    if extra:
        p = cfg["input"]["traffic"]["path"]
        T = pd.read_csv(p, encoding="utf-8-sig", dtype=str, keep_default_na=False)
        T = pd.concat([T, pd.DataFrame([extra])], ignore_index=True).fillna("")
        T.to_csv(p, index=False, encoding="utf-8-sig")
    build.main(cfg)
    out = tmp_path / cfg["project"]["output_dir"]
    return (pd.read_csv(out / "traffic_used.csv", encoding="utf-8-sig"),
            pd.read_csv(out / "sources.csv", encoding="utf-8-sig"))


@pytest.mark.parametrize("method", ["iso9613_2", "rls90"])
def test_build_uses_direction_speed(tmp_path, method):
    """양방향 2개선 도로에 fwd 30 / bwd 50 → 디지타이즈 방향 음원선은 30, 반대는 50 km/h 의 L_W′. 다른 도로 불변"""
    from sori import rls19, rls90
    T0, S0 = _build(tmp_path / "a", method)
    two = S0.groupby("fid").along.nunique()
    fid = int(two[two == 2].index[0])
    ufid = str(T0.set_index("fid").loc[fid, "ufid"])
    base = T0.set_index("fid").loc[fid]
    extra = {"key": ufid, "key_type": "ufid", "M_day": str(base.M_day), "M_night": str(base.M_night),
             "p1_day": str(base.p1_day), "p2_day": str(base.p2_day), "p1_night": str(base.p1_night),
             "p2_night": str(base.p2_night), "v_pkw": "40", "v_pkw_fwd": "30", "v_pkw_bwd": "50",
             "surface": str(base.surface), "road_type": str(base.road_type)}
    T1, S1 = _build(tmp_path / "b", method, extra)
    t = T1.set_index("fid").loc[fid]
    assert t.dir_speed_src == "ufid" and (t.v_pkw_fwd, t.v_pkw_bwd) == (30, 50)
    s = S1[S1.fid == fid]
    for along, v in ((True, 30.0), (False, 50.0)):
        r = s[s.along == along].iloc[0]
        M = t.M_day * r.frac
        g = r.g if along else -r.g
        if method == "rls90":
            p = t.p_day if t.p_day == t.p_day else t.p1_day + t.p2_day
            exp = rls90.LW_prime(M, p, v, v, t.surface, g, r.D_refl)
        else:
            exp = rls19.LW_prime(M, t.p1_day, t.p2_day, v, v, t.surface, g, r.D_K, r.D_refl)
        assert abs(r.LWp_day - exp) < 1e-9
    other = S0.fid != fid                                            # 나머지 도로 음원 불변
    a = S0[other].reset_index(drop=True); b = S1[S1.fid != fid].reset_index(drop=True)
    assert len(a) == len(b) and np.abs(a.LW_day - b.LW_day).max() < 1e-9
