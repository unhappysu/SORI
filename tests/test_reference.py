# -*- coding: utf-8 -*-
"""기준 사례(가상 교차로) 회귀 테스트: 현재 코드로 build → run 결과가 저장된 기준값과
허용 오차(기본 0.01 dB, 프로젝트 임의 설정) 안에서 같은지 확인.
기준값을 만든 커밋·환경: tests/reference/synthetic/expected/meta.json
※ Claude(Anthropic의 AI)를 활용하여 제작하였음."""
import json

import pytest

import reference_case as RC


@pytest.fixture(scope="module")
def ref_out(tmp_path_factory):
    return RC.run_case(tmp_path_factory.mktemp("ref"))


def test_reference_meta_recorded():
    meta = json.loads((RC.EXPECTED / "meta.json").read_text(encoding="utf-8"))
    assert len(meta["commit"]) == 40


def test_reference_case_matches(ref_out, sori_tol):
    rows = RC.compare(ref_out, tol_db=sori_tol)
    bad = [r for r in rows if not r[-1]]
    assert not bad, "\n".join(f"{fn} {col}: 최대 차이 {d:.4g} {u} > 허용 {t:g}" for fn, col, u, d, t, _ in bad)


def test_reference_detects_shift(ref_out, tmp_path):
    """비교 함수가 실제로 차이를 잡는지(0.02 dB 를 더한 결과는 실패해야 함)"""
    import shutil
    import pandas as pd
    for fn in RC.SPECS:
        shutil.copy(ref_out / fn, tmp_path / fn)
    g = pd.read_csv(tmp_path / "grid_result.csv")
    g["LrT"] += 0.02
    g.to_csv(tmp_path / "grid_result.csv", index=False)
    rows = RC.compare(tmp_path, tol_db=0.01)
    assert [(fn, col) for fn, col, *_, ok in rows if not ok] == [("grid_result.csv", "LrT")]
