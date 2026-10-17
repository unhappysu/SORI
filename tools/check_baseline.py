# -*- coding: utf-8 -*-
"""
기준선 비교 — 현재 작업 트리의 코드로 전농사거리 기준선을 임시 폴더에 다시 만들고
baseline/README.md 에 기록된 SHA-256 과 비교함. 저장소 안에는 아무것도 쓰지 않음.

  python tools/check_baseline.py           # build + 반경 150 m 격자·파사드 계산 (약 1~2분)
  python tools/check_baseline.py --keep    # 임시 폴더를 지우지 않고 경로 출력

비교 규칙(baseline/README.md):
  sources.csv, lv_base.csv : 바이트 단위 동일(SHA-256)
  traffic_used.csv         : 기준선에 있는 열(기존 열)만 같으면 됨(S1 이후 열이 추가됨)
종료 코드: 0 = 모두 같음, 1 = 차이 있음, 2 = 실행 오류
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BL = os.path.join(ROOT, "baseline")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def expected_hashes():
    """baseline/README.md 의 파일 표에서 `파일명` … `64자리 해시` 를 읽음"""
    txt = open(os.path.join(BL, "README.md"), encoding="utf-8").read()
    return dict(re.findall(r"^\| `([\w.]+)` \|.*`([0-9a-f]{64})` \|\s*$", txt, flags=re.M))


def run(cmd, cwd, env):
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stdout[-2000:] + r.stderr[-4000:])
        raise SystemExit(f"실행 실패: {' '.join(cmd)}")


def main():
    ap = argparse.ArgumentParser(description="전농사거리 기준선 비교")
    ap.add_argument("--keep", action="store_true", help="임시 폴더를 남김")
    a = ap.parse_args()
    exp = expected_hashes()
    for k in ("sources.csv", "lv_base.csv", "config.yaml", "traffic.csv"):
        if k not in exp:
            raise SystemExit(f"baseline/README.md 에 {k} 해시가 없음")
    # 기준선 입력이 바뀌지 않았는지부터 확인
    for k in ("config.yaml", "traffic.csv"):
        if sha256(os.path.join(BL, k)) != exp[k]:
            raise SystemExit(f"baseline/{k} 가 README 기록과 다름 — 기준선 입력이 바뀜")
    tmp = tempfile.mkdtemp(prefix="noisemap_baseline_")
    try:
        for k in ("config.yaml", "traffic.csv"):
            shutil.copy(os.path.join(BL, k), tmp)
        env = dict(os.environ, PYTHONPATH=ROOT + os.pathsep + os.environ.get("PYTHONPATH", ""))
        print(f"[1/2] build (임시 폴더 {tmp})"); sys.stdout.flush()
        run([sys.executable, "-m", "noisemap", "build", "-c", "config.yaml"], tmp, env)
        print("[2/2] 반경 150 m 격자·파사드 계산"); sys.stdout.flush()
        run([sys.executable, os.path.join(BL, "make_lv_base.py"), "output", "config.yaml", "lv_base.csv", "150"], tmp, env)
        ok = True
        for name, path in (("sources.csv", os.path.join(tmp, "output", "sources.csv")),
                           ("lv_base.csv", os.path.join(tmp, "lv_base.csv"))):
            got = sha256(path); same = got == exp[name]; ok &= same
            print(f"  {name:16s} {'동일' if same else '다름'}  기준 {exp[name][:16]}…  현재 {got[:16]}…")
        import pandas as pd
        t0 = pd.read_csv(os.path.join(BL, "traffic_used.csv"))
        t1 = pd.read_csv(os.path.join(tmp, "output", "traffic_used.csv"))
        cols = list(t0.columns)
        same = set(cols) <= set(t1.columns) and t0.equals(t1[cols]); ok &= same
        added = [c for c in t1.columns if c not in cols]
        print(f"  {'traffic_used.csv':16s} 기존 열 {'동일' if same else '다름'}  (추가 열 {len(added)}개: {', '.join(added)})")
        print("결과:", "기준선과 같음" if ok else "기준선과 다름 — 의도한 변경인지 확인하고 차이를 보고할 것")
        return 0 if ok else 1
    finally:
        if a.keep:
            print("임시 폴더:", tmp)
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
