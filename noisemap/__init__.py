# -*- coding: utf-8 -*-
"""
noisemap — 도로교통소음 3D 소음지도 계산 도구
  음원(방출) : RLS-19 (FGSV 052, Ausgabe 2019, Korrektur 04.03.2020) 원문
  전파       : ISO 9613-2:1996 (+ 대기흡수계수 ISO 9613-1:1993)

※ 본 소프트웨어는 Claude(Anthropic의 AI)를 활용하여 제작하였음.
   계산 결과의 검토·검증 책임은 사용자에게 있음.
"""
__version__ = "1.0.0"
AI_NOTICE = "본 소프트웨어는 Claude(Anthropic의 AI)를 활용하여 제작하였음."

try:                                    # numba가 없으면 순수 Python으로 동작(매우 느림)
    from numba import njit, prange      # noqa: F401
    HAVE_NUMBA = True
except ImportError:                     # pragma: no cover
    HAVE_NUMBA = False

    def njit(*args, **kwargs):
        if args and callable(args[0]):
            return args[0]
        return lambda f: f

    prange = range
