# -*- coding: utf-8 -*-
"""
RLS-19 공식 시험과제 TEST-20 v2.1 — 전파 과제 I1~I9, K1~K5 를 SORI 계산 경로(solver.compute, rls19, 2차 반사)로 재현.
장치와 과제 정의: tests/test20_cases.py. 원문: 02_기준원문_참고자료/원문/TEST-20_v2.1_2021-09-16.pdf.
TEST-20 합격 기준은 시험 설정에서 시험결과(Prüfergebnis)와 ±0.01 dB(소수 둘째 자리 반올림) — SORI 는 지형·건물을
래스터로 다루므로 이 시험은 프로젝트 허용 오차 TOL_DB = 0.05 dB(프로젝트 임의 설정)로 판정하고, 엄격 기준 통과 여부는
tools/test20_report.py 로 표를 만들어 기록함(TECHNICAL_NOTES 6절).
기준 범위(Referenzergebnis)는 '기준 설정'(DIN ISO 17534-1)의 결과이며 SORI 는 시험 설정(가는 구간)으로 계산하므로
판정에 쓰지 않음(I9 는 시험결과 자체가 기준 범위 밖).
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import pytest

import test20_cases as T

TOL_DB = 0.05
PARAMS = [(c, k) for c in T.CASES for k in c.receivers]


@pytest.fixture(scope="module")
def results():
    return {c.name: T.run(c) for c in T.CASES}


@pytest.mark.parametrize("case, rec", PARAMS, ids=[f"{c.name}-{k}" for c, k in PARAMS])
def test_test20_immission(results, case, rec):
    got = results[case.name][rec]["L"]
    pruef = case.expected[rec][0]
    assert abs(got - pruef) <= TOL_DB, f"{case.name} {rec}: SORI {got:.2f}, 시험결과 {pruef:.2f}"
