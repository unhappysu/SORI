# -*- coding: utf-8 -*-
"""골목길(차로수 < main_min_lanes) 음원 제외 옵션 model.minor_roads (2026-11-08)
※ Claude(Anthropic의 AI)를 활용하여 제작하였음."""
import copy
import json
import shutil

import pandas as pd
import pytest

import reference_case as RC


def _build(tmp_path, mode, traffic_extra=None):
    from sori import config as C, build
    shutil.copytree(RC.INPUT, tmp_path, dirs_exist_ok=True)
    cfg = C.load(tmp_path / "config.yaml")
    cfg["model"]["minor_roads"] = mode
    if traffic_extra:
        p = cfg["input"]["traffic"]["path"]
        T = pd.read_csv(p, encoding="utf-8-sig", dtype=str, keep_default_na=False)
        T = pd.concat([T, pd.DataFrame([traffic_extra])], ignore_index=True).fillna("")
        T.to_csv(p, index=False, encoding="utf-8-sig")
    build.main(cfg)
    out = tmp_path / cfg["project"]["output_dir"]
    T = pd.read_csv(out / "traffic_used.csv", encoding="utf-8-sig")
    S = pd.read_csv(out / "sources.csv", encoding="utf-8-sig")
    info = json.loads((out / "build_info.json").read_text(encoding="utf-8"))
    return cfg, T, S, info


def test_default_is_exclude():
    from sori import config as C
    assert C.DEFAULTS["model"]["minor_roads"] == "exclude"


def test_invalid_value_rejected(tmp_path):
    from sori import config as C
    cfg = copy.deepcopy(C.DEFAULTS)
    cfg["model"]["minor_roads"] = "drop"
    cfg["receivers"]["center"] = [0.0, 0.0]
    with pytest.raises(ValueError, match="minor_roads"):
        C._validate(cfg)


def test_exclude_drops_minor_sources_but_keeps_rows(tmp_path):
    """제외: 골목길은 traffic_used 에 excluded_minor 로 남고 sources 에는 없음. 간선 음원은 그대로"""
    cfg, T, S, info = _build(tmp_path / "x", "exclude")
    mml = cfg["model"]["main_min_lanes"]
    ex = T[T.traffic_src == "excluded_minor"]
    assert len(ex) > 0 and (ex.lanes < mml).all()
    assert not S.fid.isin(ex.fid).any()
    assert info["minor_roads"] == "exclude" and info["minor_roads_excluded"] == len(ex)
    assert (ex.std_filled.fillna("") == "").all()                 # 독일 기준표 집계에서도 빠짐
    _, T2, S2, info2 = _build(tmp_path / "i", "include")
    assert (T2.traffic_src != "excluded_minor").all() and info2["minor_roads_excluded"] == 0
    main = T2[T2.lanes >= mml].fid
    a = S[S.fid.isin(main)].reset_index(drop=True)
    b = S2[S2.fid.isin(main)].reset_index(drop=True)
    assert len(a) == len(b) and (a.LW_day - b.LW_day).abs().max() < 1e-9   # 간선 음원 불변
    assert len(S2) > len(S)


def test_minor_with_user_traffic_is_kept(tmp_path):
    """교통량 파일에 UFID 로 지정한 골목길은 제외하지 않음"""
    cfg, T, _, _ = _build(tmp_path / "x", "exclude")
    one = T[T.traffic_src == "excluded_minor"].iloc[0]
    _, T2, S2, _ = _build(tmp_path / "u", "exclude",
                          {"key": str(one.ufid), "key_type": "ufid", "M_day": "120", "M_night": "20", "basis": "measured"})
    r = T2[T2.ufid.astype(str) == str(one.ufid)].iloc[0]
    assert r.traffic_src == "user" and S2.fid.isin([r.fid]).any()
