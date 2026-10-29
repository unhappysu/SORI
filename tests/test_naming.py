# -*- coding: utf-8 -*-
"""결과 파일명 규칙 {프로젝트}_{시나리오}-{ISO|RLS}_{주간|야간|주야}_{grid|facade|section} 시험.
※ Claude(Anthropic의 AI)를 활용하여 제작하였음."""
import json

import pytest

from sori import config, naming


def _cfg(**upd):
    return config._merge(config.DEFAULTS, {"receivers": {"center": [0, 0]}, **upd})


def test_rule_name_parts():
    c = _cfg(project={"name": "전농사거리", "scenario": "현황"})
    assert naming.rule_name(c, "grid", "LrT", "_h4m") == "전농사거리_현황-ISO_주간_grid_h4m"
    assert naming.rule_name(c, "facade", "LrN") == "전농사거리_현황-ISO_야간_facade"
    assert naming.rule_name(c, "facade") == "전농사거리_현황-ISO_주야_facade"
    c["propagation_method"] = "rls19"
    assert naming.rule_name(c, "grid", "LrN", "_exceed_h4m") == "전농사거리_현황-RLS_야간_grid_exceed_h4m"


def test_rule_name_sanitizes_separators():
    """칸 구분자 '_'·공백·파일명 금지 글자는 '-' 로 → 칸 수가 항상 같음"""
    c = _cfg(project={"name": "내_프로젝트 1", "scenario": "대안/2 (30 km/h)"})
    n = naming.rule_name(c, "grid", "LrT")
    assert n == "내-프로젝트-1_대안-2-(30-km-h)-ISO_주간_grid"
    assert len(n.split("_")) == 4
    with pytest.raises(ValueError):
        naming.rule_name(c, "points")


def test_run_writes_rule_copies(tmp_path):
    import reference_case as RC
    out = RC.run_case(tmp_path)
    info = json.loads((out / "run_info.json").read_text(encoding="utf-8"))
    p = "가상-교차로(시험)_현황-ISO"
    expect = {f"{p}_주야_grid.csv", f"{p}_주야_facade.csv", f"{p}_주간_grid_h4m.tif", f"{p}_야간_grid_h10m.tif"}
    assert expect <= set(info["file_naming"]["files"])
    for fn in expect:
        assert (out / fn).exists()
    assert (out / f"{p}_주야_grid.csv").read_bytes() == (out / "grid_result.csv").read_bytes()
