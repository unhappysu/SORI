# -*- coding: utf-8 -*-
"""
데이터 파일·사용자 폴더 경로 헬퍼 — 개발 실행(python -m sori)과 PyInstaller 실행(SORI.app / SORI.exe) 공통.
  resource_path("sori", "templates", "config_example.yaml")
    개발: 저장소 루트 기준 / PyInstaller: sys._MEIPASS(onedir 의 _internal 폴더) 기준
  user_data_dir()   쓰기 가능한 사용자 폴더(numba 캐시 등). 프로그램 폴더 안에는 쓰지 않음
  default_output_dir()  결과 기본 저장 위치(문서/SORI)
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import os
import sys
from pathlib import Path

APP_NAME = "SORI"


def is_frozen():
    return bool(getattr(sys, "frozen", False))


def base_dir():
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent.parent


def resource_path(*parts):
    return base_dir().joinpath(*parts)


def user_data_dir():
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    p = root / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def default_output_dir():
    return Path.home() / "Documents" / APP_NAME


def setup_numba_cache():
    """앱(동결 실행)에서는 numba 캐시를 사용자 폴더에 둠 — 프로그램 폴더는 쓰기 금지일 수 있음.
    sori 의 계산 모듈을 import 하기 전에 불러야 함. 개발 실행에서는 기존 동작(소스 옆 __pycache__) 유지."""
    if is_frozen() and "NUMBA_CACHE_DIR" not in os.environ:
        os.environ["NUMBA_CACHE_DIR"] = str(user_data_dir() / "numba_cache")
        if "numba" in sys.modules:       # sori/__init__.py 가 numba 를 먼저 import 함 → 환경변수 다시 읽게 함
            from numba.core import config
            config.reload_config()
