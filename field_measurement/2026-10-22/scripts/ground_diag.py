"""지면효과 진단(검토 의견 B2): ISO 9613-2 일반법의 원거리 q 항과 RLS-19식 지면 감쇠가 측정점·격자에 주는 영향.

AI(Claude)를 활용하여 제작하였음.

실행(저장소 루트): python3 field_measurement/2026-10-22/scripts/ground_diag.py   (약 10분, 격자·벽면 포함 3회 계산)
- 저장소 sori 소스는 수정하지 않는다. 비교 조건은 model/AVG_v50(3회 평균 교통량, 50 km/h, AC11, 신호교차로 보정)과 같다.
  · base: 저장소 sori 그대로(일반법 7.3.1, 전역 G = 0, q 항 포함)
  · q0  : 진단용 사본 model_diag/pkg_q0/sori 에서 A_gr_general 의 q 를 0 으로 고정(원거리 Am = −3q 제거)
  · alt : 저장소 sori 그대로 설정만 input.ground.method = alternative(ISO 식 10 + D_Ω).
          ISO 식 (10) Agr = 4.8 − (2hm/d)(17 + 300/d) 는 RLS-19 식 (14) Dgr = 4.8 − (hm/s)(34 + 600/s) 와 같은 식이고,
          D_Ω 는 RLS-19 Ddiv 의 10 lg 2π(반공간)에 대응한다. 차폐 시 결합(ISO 식 12, RLS-19 식 11 max{Dgr; Dz})은 같지 않다.
- 결과: results/지면효과_진단.csv(측정점 3곳 LrT, 격자·벽면 차이 분포)
"""
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import model_compare as mc  # noqa: E402

OUT = mc.BASE / "model_diag"
SRC = mc.MODEL / "AVG_v50"


def prepare():
    pkg = OUT / "pkg_q0"
    if pkg.exists():
        shutil.rmtree(pkg)
    shutil.copytree(mc.REPO / "sori", pkg / "sori", ignore=shutil.ignore_patterns("__pycache__"))
    p = pkg / "sori" / "iso9613.py"
    s = p.read_text(encoding="utf-8")
    old = "    q = 0.0 if dp <= lim else 1.0 - lim / dp\n"
    assert s.count(old) == 1, "iso9613.A_gr_general 의 q 식이 예상과 다름"
    p.write_text(s.replace(old, "    q = 0.0  # 진단용 사본(B2): 원거리 q 항 제거\n"), encoding="utf-8")
    for name in ("base", "q0", "alt"):
        d = OUT / name
        d.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SRC / "traffic.csv", d / "traffic.csv")
        cfg = (SRC / "config.yaml").read_text(encoding="utf-8").replace('"../points.csv"', '"../../model/points.csv"')
        if name == "alt":
            cfg = cfg.replace("  junctions: {dk_rule: nearest_signal}\n",
                              "  junctions: {dk_rule: nearest_signal}\n  ground: {method: alternative}\n")
            assert "alternative" in cfg
        (d / "config.yaml").write_text(cfg, encoding="utf-8")
    return pkg


def run(name, pythonpath):
    d = OUT / name
    env = {"PYTHONPATH": str(pythonpath), "PATH": "/usr/bin:/bin"}
    import os
    env = dict(os.environ, **env)
    for args in (["build", "-c", "config.yaml"], ["run", "-c", "config.yaml"]):
        print(f"  $ [{name}] python -m sori", " ".join(args), flush=True)
        subprocess.run([sys.executable, "-m", "sori", *args], cwd=d, env=env, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    pkg = prepare()
    run("base", mc.REPO)
    run("q0", pkg)
    run("alt", mc.REPO)
    rd = {n: OUT / n / "output" for n in ("base", "q0", "alt")}
    pt = {n: pd.read_csv(rd[n] / "point_result.csv").set_index("point_id") for n in rd}
    rows = []
    for rid in ("P1", "P2", "P2c"):
        b = float(pt["base"].loc[rid, "LrT"])
        for n in ("base", "q0", "alt"):
            v = float(pt[n].loc[rid, "LrT"])
            rows.append({"구분": "측정점", "대상": rid, "조건": n, "LrT": round(v, 2), "기준대비_dB": round(v - b, 2),
                         "p5": "", "p50": "", "p95": "", "최소": "", "최대": ""})
    for kind, f in (("격자 h4m", "grid_result.csv"), ("벽면", "facade_result.csv")):
        base = pd.read_csv(rd["base"] / f).LrT
        for n in ("q0", "alt"):
            d = pd.read_csv(rd[n] / f).LrT - base
            p5, p50, p95 = np.percentile(d, [5, 50, 95])
            rows.append({"구분": kind, "대상": f"전체 {len(d)}점", "조건": n, "LrT": "", "기준대비_dB": "",
                         "p5": round(p5, 2), "p50": round(p50, 2), "p95": round(p95, 2),
                         "최소": round(d.min(), 2), "최대": round(d.max(), 2)})
    with open(mc.BASE / "results" / "지면효과_진단.csv", "w", encoding="utf-8-sig", newline="") as fh:
        fh.write(f"# {mc.AI_NOTE}. AVG_v50 조건. base = 일반법(G=0, q 포함), q0 = q 항 제거(진단용 사본), "
                 "alt = 대안법(ISO 식 10 = RLS-19 식 14 형태 + D_Ω). 기준대비 = 조건 − base\n")
        pd.DataFrame(rows).to_csv(fh, index=False)
    print(pd.DataFrame(rows).to_string())


if __name__ == "__main__":
    main()
