"""조합 민감도(검토 의견 B3·B4): 3회 평균 교통량(AVG)으로 측정점 계산값을 조건 조합별로 실제 실행해 구한다.

AI(Claude)를 활용하여 제작하였음.

실행(저장소 루트): python3 field_measurement/2026-10-22/scripts/sensitivity_combo.py [--jobs N]
- SORI 소스코드는 수정하지 않는다. model_compare.py 의 교통량·수음점 정의를 그대로 쓰고 설정만 바꾼다.
- 축(3 × 4 × 2 × 4 = 96개, 사가정로 측정 구간에만 적용. 다른 도로는 기준 조건 그대로)
  · 속도: 사가정로 30 / 40 / 50 km/h(승용차·화물차 같은 값)
  · 차종: 계수 화물차(이륜차 포함, 비율 p)를 lkw1 = 전부 Lkw1 / half_pkw = 절반 Pkw·절반 Lkw1 /
          pkw = 전부 Pkw / lkw2 = 전부 Lkw2
  · 노면: ac11 = 수치지도 아스팔트 → AC11(surface_map) / default = SORI 기본 매핑(아스팔트 → ref)
  · 보정: dk·drefl 켬·끔. D_K 끔 = 신호교차로 자동 판정 차로수를 99로 올려 신호교차로를 없앰
          (dk_rule nearest, 모든 교차점 other → D_K = 0). D_refl 끔 = model.canyon.enabled false
- 기준 조합(v50·lkw1·ac11·dk 켬·drefl 켬)은 model/AVG_v50 과 같은 조건이며 결과가 같아야 한다.
- 결과: results/민감도_조합.csv. 계산 폴더 model_sens/<조합>/ (output 은 git 무시 대상과 같은 취급)
"""
import argparse
import csv
import itertools
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import model_compare as mc  # noqa: E402

OUT = mc.BASE / "model_sens"
SPEEDS = (30.0, 40.0, 50.0)
MIXES = ("lkw1", "half_pkw", "pkw", "lkw2")
SURFACES = ("ac11", "default")
SWITCHES = ((True, True), (False, True), (True, False), (False, False))   # (dk, drefl)


def mix_p(p, mix):
    """계수 화물차 비율 p(%)를 RLS-19 p1(Lkw1)·p2(Lkw2)로 나눈다."""
    return {"lkw1": (p, 0.0), "half_pkw": (p / 2, 0.0), "pkw": (0.0, 0.0), "lkw2": (0.0, p)}[mix]


def write_traffic(d, v, mix):
    rows, info = mc.traffic_rows("AVG", v)
    for r in rows:
        if r.get("basis") == "measured":                  # 사가정로 측정 구간(도로명 행 + UFID 행)
            p1, p2 = mix_p(float(r["p1_day"]), mix)
            r.update(p1_day=round(p1, 2), p2_day=round(p2, 2), p1_night=round(p1, 2), p2_night=round(p2, 2))
    with open(d / "traffic.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=mc.FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in mc.FIELDS})
    return info


def write_config(d, surface, dk, drefl):
    smap = ", ".join(f'"{k}": "{v}"' for k, v in mc.SURFACE_MAP.items())
    junc = "{dk_rule: nearest_signal}" if dk else "{dk_rule: nearest, auto_signal_min_lanes: 99}"
    cfg = f"""# {mc.AI_NOTE}. 조합 민감도(sensitivity_combo.py). 노면 {surface}, D_K {'켬' if dk else '끔'}, D_refl {'켬' if drefl else '끔'}
project: {{name: "사가정로_조합민감도", output_dir: "output"}}
crs: 5186
input:
  map: {{folders: ["{mc.MAPDIR}"]}}
  traffic: {{path: "traffic.csv"}}
  road_alias: {{"전농로25길": "전농로"}}
{f'  surface_map: {{{smap}}}' if surface == 'ac11' else '  # surface_map: SORI 기본 매핑'}
  junctions: {junc}
model:
  canyon: {{enabled: {str(drefl).lower()}}}
receivers:
  center: [205108.0, 553110.0]
  grid: {{enabled: false, size: [480.0, 520.0], step: 2.5, heights: [4.0]}}
  facade: {{enabled: false, radius: 230.0}}
  points: {{path: "../../model/points.csv"}}
"""
    (d / "config.yaml").write_text(cfg, encoding="utf-8")


def run_one(key):
    v, mix, surface, (dk, drefl) = key
    name = f"v{v:.0f}_{mix}_{surface}_dk{int(dk)}_refl{int(drefl)}"
    d = OUT / name
    d.mkdir(parents=True, exist_ok=True)
    info = write_traffic(d, v, mix)
    write_config(d, surface, dk, drefl)
    env = dict(os.environ, PYTHONPATH=str(mc.REPO))
    for args in (["build", "-c", "config.yaml"], ["run", "-c", "config.yaml", "--points"]):
        subprocess.run([sys.executable, "-m", "sori", *args], cwd=d, env=env, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pr = pd.read_csv(d / "output" / "point_result.csv").set_index("point_id")
    rows = []
    for rid in ("P1", "P2", "P2c"):
        pid = mc.RECEIVERS[rid]["point"]
        meas = mc.meas_for("AVG", pid)
        lrt = float(pr.loc[rid, "LrT"])
        rows.append({"조합": name, "속도_km/h": v, "차종": mix, "노면": surface, "D_K": "켬" if dk else "끔",
                     "D_refl": "켬" if drefl else "끔", "수음점": rid, "지점": pid,
                     "Q_양방향_대/h": round(info["Q"], 1), "p_계수화물차_%": round(info["p1"], 1),
                     "측정_LAeq": round(meas, 2), "모델_LrT": round(lrt, 2),
                     "차이(측정-모델)": round(meas - lrt, 2)})
    print("  완료", name, flush=True)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=4, help="동시에 실행할 조합 수")
    a = ap.parse_args()
    keys = list(itertools.product(SPEEDS, MIXES, SURFACES, SWITCHES))
    with ThreadPoolExecutor(a.jobs) as ex:
        rows = [r for rr in ex.map(run_one, keys) for r in rr]
    df = pd.DataFrame(rows)
    with open(mc.BASE / "results" / "민감도_조합.csv", "w", encoding="utf-8-sig", newline="") as f:
        f.write(f"# {mc.AI_NOTE}. AVG 교통량, 조합 {len(keys)}개 실제 실행(단순 합산 아님). "
                "차이 = 측정 LAeq(3회 에너지평균, 반올림 전) − 모델 LrT\n")
        df.to_csv(f, index=False)
    print(df[df.수음점 != "P2c"].to_string())


if __name__ == "__main__":
    main()
