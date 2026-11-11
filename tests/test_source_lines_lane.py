# -*- coding: utf-8 -*-
"""차로마다 음원선 model.source_lines: lane (2026-11-11, SoundPLAN 비교 모델처럼 차로별 음원선).
기본 direction 은 RLS-19 Bild 6 / RLS-90 Bild 10(방향마다 1개선) 그대로.
※ Claude(Anthropic의 AI)를 활용하여 제작하였음."""
import copy
import math
import shutil

import numpy as np
import pandas as pd
import pytest

import reference_case as RC


def test_lane_offsets_geometry_and_shares():
    from sori.build import lane_source_offsets
    L = lane_source_offsets(3, 2, 3.0, False, p_fwd=0.6)
    assert [round(o, 6) for o, a, f in L] == [-6.0, -3.0, 0.0, 3.0, 6.0]        # 5차로를 중심선 대칭, 정방향 = 오른쪽(−)
    S = lane_source_offsets(2, 2, 3.0, False)
    assert [round(o, 6) for o, a, f in S] == [-4.5, -1.5, 1.5, 4.5]             # 대칭이면 경계 = 중심선
    assert [a for _, a, _ in L] == [True] * 3 + [False] * 2
    assert abs(sum(f for _, a, f in L if a) - 0.6) < 1e-12 and abs(sum(f for _, a, f in L if not a) - 0.4) < 1e-12
    O = lane_source_offsets(2, 0, 3.25, True, oneway_dir=-1)                      # 일방통행: 중앙 대칭, 전량
    assert [round(o, 6) for o, _, _ in O] == [-1.625, 1.625] and all(a is False for _, a, _ in O)
    assert abs(sum(f for *_, f in O) - 1.0) < 1e-12


def test_invalid_value_rejected():
    from sori import config as C
    cfg = copy.deepcopy(C.DEFAULTS)
    cfg["model"]["source_lines"] = "per_lane"
    cfg["receivers"]["center"] = [0.0, 0.0]
    with pytest.raises(ValueError, match="source_lines"):
        C._validate(cfg)


def _build(tmp_path, mode, method="iso9613_2"):
    from sori import config as C, build
    shutil.copytree(RC.INPUT, tmp_path, dirs_exist_ok=True)
    cfg = C.load(tmp_path / "config.yaml")
    cfg["model"]["source_lines"] = mode
    cfg["propagation_method"] = method
    build.main(cfg)
    out = tmp_path / cfg["project"]["output_dir"]
    return (pd.read_csv(out / "sources.csv", encoding="utf-8-sig"),
            pd.read_csv(out / "source_lines.csv", encoding="utf-8-sig"))


@pytest.mark.parametrize("method", ["iso9613_2", "rls90"])
def test_lane_mode_conserves_power_and_counts_lines(tmp_path, method):
    """차로별: 음원선 수 = 차로 수, 도로별 M 합(= 음향 파워 합, 경사·속도 같을 때)이 방향별 1개선과 같음"""
    S0, L0 = _build(tmp_path / "d", "direction", method)
    S1, L1 = _build(tmp_path / "l", "lane", method)
    two = L1[(L1.oneway == False) & (L1.lane_rule != "single_centre")]   # noqa: E712
    r = two.iloc[0]
    n = len(str(r.lines_used).split(";"))
    assert r.source_lines == "lane" and n == r.lanes_fwd_used + r.lanes_bwd_used
    assert (L0.source_lines == "direction").all()
    # 도로·주간: 점음원 파워 합(10^(LW/10))이 같음 — 같은 M, 같은 속도, 경사 부호만 방향별
    for fid in two.fid.head(5):
        e0 = (10 ** (0.1 * S0[S0.fid == fid].LW_day)).sum()
        e1 = (10 ** (0.1 * S1[S1.fid == fid].LW_day)).sum()
        assert abs(10 * math.log10(e1 / e0)) < 0.05             # 차로별 구간 길이(offset_curve)·경사 표본 차이만
