# -*- coding: utf-8 -*-
"""pytest 공통 설정: 기준 사례 비교 허용 오차 옵션
  pytest --sori-tol 0.05      (또는 환경변수 SORI_TOL=0.05)
기본 0.01 dB — 프로젝트 임의 설정(표준에서 정한 값 아님).
※ Claude(Anthropic의 AI)를 활용하여 제작하였음."""
import os

import pytest

from reference_case import TOL_DB

# 오른쪽 3D 파사드는 시험에서 matplotlib 으로(화면 없는 시험에서 WebGL 웹 화면을 띄우지 않음). three.js 쪽 시험은
# 따로 WEB_FACADE 를 켜고 웹 화면 load 를 가로챔(test_facade_web_*)
os.environ.setdefault("SORI_WEB_FACADE", "0")


def pytest_addoption(parser):
    parser.addoption("--sori-tol", type=float, default=None,
                     help=f"기준 사례 비교 허용 오차 dB (기본 {TOL_DB}, 프로젝트 임의 설정)")


@pytest.fixture
def sori_tol(request):
    v = request.config.getoption("--sori-tol")
    if v is None:
        v = float(os.environ.get("SORI_TOL", TOL_DB))
    return v
