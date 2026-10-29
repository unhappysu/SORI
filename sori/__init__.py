# -*- coding: utf-8 -*-
"""
SORI (Simulation of Outdoor Road-noise Impact) — 도로교통소음 3D 소음지도(SORI Map) 계산 도구
  음원(방출) : RLS-19 (FGSV 052, Ausgabe 2019, Korrektur 04.03.2020) 원문
  전파       : ISO 9613-2:1996 (+ 대기흡수계수 ISO 9613-1:1993)

※ 본 소프트웨어는 Claude(Anthropic의 AI)를 활용하여 제작하였음.
   계산 결과의 검토·검증 책임은 사용자에게 있음.
"""
__version__ = "0.1"
AI_NOTICE = "본 소프트웨어는 Claude(Anthropic의 AI)를 활용하여 제작하였음."



def _purge_stale_numba_cache():
    """numba cache=True 는 함수가 있는 파일만 보고 캐시를 고름 — 다른 모듈의 njit 함수(예: geometry.path_geometry)가
    바뀌어도 그것을 부르는 solver.compute 의 캐시는 다시 컴파일되지 않음(2026-10-29 확인). sori/*.py 내용이
    바뀌면 sori/__pycache__ 의 numba 캐시(*.nbi, *.nbc)를 지움. 소스가 없는 실행 파일(앱)·쓰기 불가 폴더는 건너뜀."""
    import hashlib
    from pathlib import Path
    try:
        here = Path(__file__).resolve().parent
        srcs = sorted(here.glob("*.py"))
        if not srcs:
            return
        h = hashlib.sha256(b"".join(p.read_bytes() for p in srcs)).hexdigest()
        cache = here / "__pycache__"
        stamp = cache / "numba_source.sha256"
        if stamp.exists() and stamp.read_text() == h:
            return
        cache.mkdir(exist_ok=True)
        for f in list(cache.glob("*.nbi")) + list(cache.glob("*.nbc")):
            f.unlink()
        stamp.write_text(h)
    except OSError:
        pass


_purge_stale_numba_cache()

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
