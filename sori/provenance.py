# -*- coding: utf-8 -*-
"""결과 재현용 실행 환경 기록 — run_info.json 의 `environment`.
소스 식별자(git 커밋·수정 여부, sori/*.py 해시), Python·운영체제, 계산에 쓰는 패키지 판 번호.
어느 항목이든 구하지 못하면 None 으로 두고 계산은 계속함(기록용, 결과값과 무관).
※ Claude(Anthropic의 AI)를 활용하여 제작하였음."""
import hashlib
import platform
import subprocess
import sys
from pathlib import Path

PACKAGES = ("numpy", "numba", "scipy", "pandas", "geopandas", "shapely", "rasterio", "pyproj", "matplotlib",
            "PyYAML")


def _git(here):
    """소스 폴더가 git 저장소이면 {commit, dirty}. 앱(실행 파일)·git 없음·저장소 아님은 None"""
    if getattr(sys, "frozen", False):
        return None
    try:
        def run(*a):
            return subprocess.run(["git", "-C", str(here), *a], capture_output=True, text=True, timeout=5)
        r = run("rev-parse", "HEAD")
        if r.returncode != 0:
            return None
        s = run("status", "--porcelain", "--untracked-files=no")
        return {"commit": r.stdout.strip(), "dirty": bool(s.stdout.strip()) if s.returncode == 0 else None}
    except (OSError, subprocess.SubprocessError):
        return None


def _source_hash(here):
    """sori/*.py 내용의 SHA-256(커밋하지 않은 수정도 구분). 소스가 없는 앱은 None"""
    try:
        srcs = sorted(Path(here).glob("*.py"))
        if not srcs:
            return None
        return hashlib.sha256(b"".join(p.read_bytes() for p in srcs)).hexdigest()
    except OSError:
        return None


def _versions():
    from importlib import metadata
    out = {}
    for name in PACKAGES:
        try:
            out[name] = metadata.version(name)
        except Exception:                           # 앱 묶음에는 배포 정보가 없을 수 있음
            out[name] = None
    return out


def environment():
    here = Path(__file__).resolve().parent
    return {"git": _git(here), "source_sha256": _source_hash(here),
            "python": platform.python_version(), "os": platform.platform(), "machine": platform.machine(),
            "frozen_app": bool(getattr(sys, "frozen", False)), "packages": _versions()}
