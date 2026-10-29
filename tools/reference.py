# -*- coding: utf-8 -*-
"""
기준 사례(가상 교차로) 기준값 만들기·비교하기

  python tools/reference.py make              # 현재 코드로 계산해 tests/reference/synthetic/expected 저장 + meta.json
  python tools/reference.py compare <결과폴더>  # 이미 계산된 결과(예: 빌드된 앱으로 계산한 output)를 기준값과 비교
  python tools/reference.py compare --run      # 현재 코드로 임시 폴더에서 다시 계산해 비교
  옵션: --tol-db 0.01 --tol-m 0.001 (프로젝트 임의 설정)

종료 코드: 0 = 모두 허용 오차 이내, 1 = 차이 있음
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import argparse
import datetime as dt
import json
import platform
import subprocess
import sys
import tempfile
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import reference_case as RC  # noqa: E402

PKGS = ["numpy", "scipy", "pandas", "geopandas", "pyogrio", "shapely", "rasterio", "pyproj", "numba", "llvmlite"]


def git(*args):
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def environment():
    import pyproj
    v = {}
    for p in PKGS:
        try:
            v[p] = metadata.version(p)
        except metadata.PackageNotFoundError:
            v[p] = None
    return {"python": platform.python_version(), "os": platform.platform(), "machine": platform.machine(),
            "packages": v, "proj_version": pyproj.proj_version_str}


def cmd_make(a):
    dirty = git("status", "--porcelain", "--", "sori")
    with tempfile.TemporaryDirectory() as tmp:
        out = RC.run_case(tmp)
        RC.save_expected(out)
    meta = {
        "description": "기준 사례(가상 교차로, tests/reference/synthetic/input) 기준값. 실제 지역 자료 아님",
        "created": dt.datetime.now().isoformat(timespec="seconds"),
        "commit": git("rev-parse", "HEAD"),
        "sori_code_uncommitted_changes": bool(dirty),
        "environment": environment(),
        "tolerance_note": f"비교 허용 오차 기본값 {RC.TOL_DB} dB, {RC.TOL_M} m — 프로젝트 임의 설정",
        "notice": "※ Claude(Anthropic의 AI)를 활용하여 제작하였음.",
    }
    (RC.EXPECTED / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"기준값 저장: {RC.EXPECTED} (커밋 {meta['commit']}, sori 미커밋 변경 {meta['sori_code_uncommitted_changes']})")
    return 0


def cmd_compare(a):
    if a.run:
        tmp = tempfile.TemporaryDirectory()
        out = RC.run_case(tmp.name)
    elif a.out:
        out = Path(a.out)
    else:
        raise SystemExit("결과 폴더를 주거나 --run 을 지정할 것")
    rows = RC.compare(out, a.tol_db, a.tol_m)
    worst = {"dB": 0.0, "m": 0.0}
    for fn, col, unit, d, tol, ok in rows:
        print(f"{'통과' if ok else '실패'}  {fn:20s} {col:12s} 최대 차이 {d:.3g} {unit} (허용 {tol:g})")
        if unit in worst:
            worst[unit] = max(worst[unit], d)
    print(f"최대 차이: {worst['dB']:.3g} dB, {worst['m']:.3g} m")
    return 0 if all(r[-1] for r in rows) else 1


def main():
    ap = argparse.ArgumentParser(description="기준 사례 기준값 만들기·비교")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("make")
    c = sub.add_parser("compare")
    c.add_argument("out", nargs="?")
    c.add_argument("--run", action="store_true")
    c.add_argument("--tol-db", type=float, default=RC.TOL_DB)
    c.add_argument("--tol-m", type=float, default=RC.TOL_M)
    a = ap.parse_args()
    return cmd_make(a) if a.cmd == "make" else cmd_compare(a)


if __name__ == "__main__":
    sys.exit(main())
