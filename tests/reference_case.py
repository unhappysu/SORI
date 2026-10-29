# -*- coding: utf-8 -*-
"""
회귀 테스트용 기준 사례(가상 교차로) — 계산 실행·결과 추출·기준값 비교.
입력: tests/reference/synthetic/input (tools/make_synthetic_data.py 로 만든 자료를 고정해 둔 것.
      DEM 이 EPSG:5179 라서 build 단계에서 EPSG:5186 으로 재투영 → PROJ 데이터 누락 시 z 가 틀어짐)
기준값: tests/reference/synthetic/expected/*.csv, meta.json (만든 커밋·환경 기록)
pytest(tests/test_reference.py)와 tools/reference.py 가 함께 사용함.
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import math
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / "tests" / "reference" / "synthetic"
INPUT = REF / "input"
EXPECTED = REF / "expected"

# 허용 오차 기본값 — 프로젝트 임의 설정(표준에서 정한 값 아님). pytest --sori-tol / 환경변수 SORI_TOL 로 변경
TOL_DB = 0.01
TOL_M = 0.001

# 파일별 (정렬·대조용 좌표 열, 높이 열[m], 소음 열[dB])
SPECS = {
    "sources.csv": (["x", "y"], ["zg", "z"], ["LW_day", "LW_night"]),
    "grid_result.csv": (["x", "y", "h"], ["z"], ["LrT", "LrN", "LrT_direct"]),
    "facade_result.csv": (["x", "y", "floor"], ["z", "zg"], ["LrT", "LrN", "LrT_direct"]),
}


def run_case(work):
    """입력을 work 폴더에 복사하고 build → run(격자·파사드) 실행. 결과 폴더 경로를 돌려줌"""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from sori import config as C, build, run
    work = Path(work)
    shutil.copytree(INPUT, work, dirs_exist_ok=True)
    cfg = C.load(work / "config.yaml")
    build.main(cfg)
    run.main(cfg, ("grid", "facade"))
    return Path(cfg["project"]["output_dir"])


def extract(out_dir):
    """결과 폴더에서 비교할 열만 골라 같은 순서로 정렬한 표를 돌려줌"""
    res = {}
    for fn, (keys, zs, dbs) in SPECS.items():
        df = pd.read_csv(Path(out_dir) / fn, encoding="utf-8-sig")
        df = df[keys + zs + dbs].sort_values(keys, kind="mergesort").reset_index(drop=True)
        res[fn] = df
    return res


def compare(out_dir, tol_db=TOL_DB, tol_m=TOL_M, expected_dir=EXPECTED):
    """기준값과 비교 → [(파일, 열, 단위, 최대 차이, 허용 오차, 통과)] 목록"""
    got = extract(out_dir)
    rows = []
    for fn, (keys, zs, dbs) in SPECS.items():
        e = pd.read_csv(Path(expected_dir) / fn, encoding="utf-8-sig")
        g = got[fn]
        if len(e) != len(g):
            rows.append((fn, "행 수", "-", float(abs(len(e) - len(g))), 0.0, False))
            continue
        for col in keys + zs + dbs:
            unit, tol = ("dB", tol_db) if col in dbs else ("m", tol_m)
            a, b = e[col].to_numpy(float), g[col].to_numpy(float)
            nan_ok = np.array_equal(np.isnan(a), np.isnan(b))
            m = ~np.isnan(a) & ~np.isnan(b)
            dmax = float(np.max(np.abs(a[m] - b[m]))) if m.any() else 0.0
            ok = nan_ok and dmax <= tol
            rows.append((fn, col, unit, dmax if nan_ok else math.inf, tol, ok))
    return rows


def save_expected(out_dir, expected_dir=EXPECTED):
    expected_dir = Path(expected_dir)
    expected_dir.mkdir(parents=True, exist_ok=True)
    for fn, df in extract(out_dir).items():
        df.to_csv(expected_dir / fn, index=False, encoding="utf-8-sig", float_format="%.12g")
