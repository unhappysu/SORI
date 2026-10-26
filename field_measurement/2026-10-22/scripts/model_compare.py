"""측정 교통량을 noisemap(RLS-19 + ISO 9613-2)에 넣어 측정값과 비교하고 소음지도를 만든다.

AI(Claude)를 활용하여 제작하였음.

실행: python3 field_measurement/2026-10-22/scripts/model_compare.py [--skip-map | --only-map]
- noisemap 소스코드는 수정하지 않는다. 저장소의 noisemap 패키지를 그대로 호출한다.
- 시나리오: {R1, R2, R3, AVG} × 사가정로 속도 {30, 40, 50} km/h. 폴더 model/<회차>_v<속도>/
  R1~R3 = 회차별 10분 계수 × 6, AVG = 3회 계수 평균 × 6(= 30분 합계 × 2).
- 공통 조건(2026-10-23 사용자 지시)
  · 노면: 수치지도 '아스팔트'·'아스팔트콘크리트' → RLS-19 표 4a AC11(설정 surface_map). 모든 아스팔트 도로에 적용.
  · 교차로 보정: noisemap 설정 input.junctions.dk_rule = nearest_signal(커밋 216882d).
    가장 가까운 신호·회전교차로 기준 D_K = KKT·max(1 − x/120, 0)와 기본 판정 값 중 큰 쪽.
  · 지점2 수음점: 야장 판독값대로 건물 벽에서 1.13 m 앞.
- 소음지도: AVG_v50(규정 속도) 교통량으로 격자·파사드 전체 계산(run) → plot.

교차로 보정 규칙
- noisemap 기본(dk_rule = nearest)은 RLS-19 3.3.7 을 '그 음원선의 가장 가까운 교차점'으로 구현한다. 측정점 부근에서는
  전농로14길(1차로, 폭 3.3 m) 접속부 등 other 유형이 잡혀 전농사거리 신호교차로 보정이 0 dB 가 된다.
- 그래서 dk_rule = nearest_signal 로 계산한다. 비교를 위해 각 시나리오를 기본 규칙으로도 build·점 계산해
  output/point_result_default.csv 로 남긴다(최종 config.yaml·output 은 nearest_signal 결과).
- 이전 판(커밋 bf31537)은 build 뒤 sources.csv 를 고쳐 같은 계산을 했고, 측정점 값은 1e-12 dB 이내로 같다(216882d 보고).

수음점 좌표(수치지도 기준)
- 도로끝선 = 수치지도 보도(A0033320) 안쪽 경계. 사가정로 중심선(UFID …465)에서 북측 10.7 m, 남측 9.2 m.
- 지점1(P1): s = 88 m, 북측 10.7 + 4.213 m → 뒤 건물 벽까지 1.01 m(야장 1.001 m와 일치).
- 지점2(P2): s = 100 m, 남측 건물 벽(중심선에서 15.48 m)에서 1.13 m 앞(야장 판독값) → 도로끝선까지 5.12 m.
  실측 도로끝선 거리 3.321 m 와 1.8 m 어긋난다(수치지도 보도·건물선과 현장이 다름). 참고로 도로끝선 기준 위치(P2c)도 계산.
"""
import argparse
import csv
import glob
import os
import shutil
import subprocess
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import Transformer
from shapely.geometry import LineString
from shapely.ops import linemerge, unary_union

BASE = Path(__file__).resolve().parents[1]
REPO = BASE.parents[1]
MODEL = BASE / "model"
MAPDIR = "/Users/gustnbijou_/Downloads/3-2/소음진동설계/전농사거리_수치지도"
AI_NOTE = "AI(Claude)를 활용하여 제작하였음"

# 측정 교통량(10분, 승용차/화물차) — analyze.py 와 같은 값. 방향은 지도 판정. 화물차에는 이륜차 포함.
UP = {1: (148, 33), 2: (138, 54), 3: (133, 47)}      # 상행(서행) — 지점1 조
DOWN = {1: (180, 76), 2: (180, 68), 3: (163, 50)}    # 하행(동행) — 지점2 조
MEASURED = {1: {1: 68.12, 2: 68.26, 3: 69.20}, 2: {1: 68.92, 2: 68.98, 3: 69.29}}  # 재계산 LAeq,10min
SPEED_LIMIT = 50.0              # 사가정로 규정(최고) 속도 km/h — OSM maxspeed=50, 서울 안전속도 5030
SPEEDS = (30.0, 40.0, 50.0)     # 사가정로 주행속도 시나리오(승용차·화물차 같은 값). 다른 도로는 50 km/h
ROUNDS = ("R1", "R2", "R3", "AVG")
MAP_SCEN = "AVG_v50"
SURFACE_MAP = {"아스팔트": "AC11", "아스팔트콘크리트": "AC11"}

# 측정하지 않은 도로: 기존 시나리오 A_diurnal 과 같은 가정(기준선 DTV × 서울 주간 계수 0.0518)
OTHER_ROADS = [
    ("전농로", 2331.0, 967.5, "기준선 DTV 45,000 가정 × 서울 주·야 계수"),
    ("답십리로", 1813.0, 752.5, "기준선 DTV 35,000 가정 × 서울 주·야 계수"),
    ("서울시립대로", 1554.0, 645.0, "기준선 DTV 30,000 가정 × 서울 주·야 계수"),
]
NIGHT_RATIO = 0.0215 / 0.0518   # 사가정로 야간값은 측정이 없어 서울 주·야 계수 비율로 가정(소음지도 LrN 용, 비교에는 안 씀)

CENTER_UFID = "1000377051742A00210000000000000465"
SEG_IDS = ["1000377051742A00210000000000000466", "1000377051742A00210000000000000467",
           "1000377051742A00210000000000000459", "1000377051742A00210000000000000464", CENTER_UFID]
# side: +1 = 디지타이즈 방향(서→동) 왼쪽 = 북측. edge = 도로끝선에서 거리, wall = 건물 벽에서 거리(둘 중 하나)
RECEIVERS = {
    "P1": dict(point=1, s=88.0, side=+1, edge=4.213, wall=None, h=1.309, label="지점1(도로끝선 4.213 m)"),
    "P2": dict(point=2, s=100.0, side=-1, edge=None, wall=1.13, h=1.312, label="지점2(건물 벽 1.13 m 앞, 야장 판독)"),
    "P2c": dict(point=2, s=100.0, side=-1, edge=3.321, wall=None, h=1.312, label="지점2 참고(도로끝선 3.321 m)"),
}


def read_layer(code):
    fs = glob.glob(f"{MAPDIR}/**/*{code}*.shp", recursive=True)
    return pd.concat([gpd.read_file(f, encoding="cp949") for f in fs])


def receiver_points():
    """수치지도 사가정로 중심선·보도 경계·건물선으로 측정점 좌표를 만든다."""
    roads = read_layer("A0020000")
    line = linemerge(unary_union(list(roads[roads.UFID.isin(SEG_IDS)].geometry)))
    sw = unary_union(list(read_layer("A0033320").geometry)).boundary
    bld = unary_union(list(read_layer("B0010000").geometry)).boundary
    inv = Transformer.from_crs(5186, 4326, always_xy=True)
    rows = []
    for pid, r in RECEIVERS.items():
        q = line.interpolate(r["s"])
        q2 = line.interpolate(r["s"] + 1.0)
        t = np.array([q2.x - q.x, q2.y - q.y])
        t /= np.linalg.norm(t)
        n = np.array([-t[1], t[0]]) * r["side"]

        def first_hit(geom):
            ray = LineString([(q.x, q.y), (q.x + n[0] * 45, q.y + n[1] * 45)])
            x = geom.intersection(ray)
            d = [np.hypot(p.x - q.x, p.y - q.y) for p in getattr(x, "geoms", [x]) if p.geom_type == "Point"]
            return min(d) if d else np.nan

        curb, wall = first_hit(sw), first_hit(bld)
        off = curb + r["edge"] if r["edge"] is not None else wall - r["wall"]
        x, y = q.x + n[0] * off, q.y + n[1] * off
        lon, lat = inv.transform(x, y)
        rows.append({"point_id": pid, "x": round(x, 2), "y": round(y, 2), "h": r["h"],
                     "lon": round(lon, 6), "lat": round(lat, 6), "s_along_m": r["s"],
                     "curb_from_centerline_m": round(curb, 2), "wall_from_centerline_m": round(wall, 2),
                     "receiver_from_centerline_m": round(off, 2),
                     "curb_to_receiver_m": round(off - curb, 2), "receiver_to_wall_m": round(wall - off, 2),
                     "basis": r["label"]})
    return rows


def saga_ufid_rows(split_fwd_east, q_day, q_night, p1, v):
    """전농사거리 동쪽 사가정로(차로 4 이상) 객체에 측정 방향 비율을 UFID 행으로 지정."""
    roads = read_layer("A0020000")
    s = roads[(roads["도로명"] == "사가정로") & (roads["차로수"] >= 4)]
    out = []
    for _, r in s.iterrows():
        c = list(r.geometry.coords)
        xmid = (c[0][0] + c[-1][0]) / 2
        if not (205108 <= xmid <= 205470):        # 교차로 중심 동쪽 ~360 m(음원 탐색 300 m 포함)
            continue
        east = c[-1][0] > c[0][0]                 # 디지타이즈 방향이 동쪽이면 fwd = 하행
        fwd = split_fwd_east if east else 1 - split_fwd_east
        out.append(dict(key=r.UFID, key_type="ufid", road_type="gemeinde", M_day=q_day, M_night=q_night,
                        p1_day=p1, p2_day=0, p1_night=p1, p2_night=0, v_pkw=v, v_lkw=v,
                        dir_split_fwd=round(fwd, 4), dir_basis="measured", basis="measured",
                        note=f"현장 계수(동쪽 접근로), 디지타이즈 {'동' if east else '서'}향"))
    return out


def traffic_rows(rnd, v):
    """회차(R1~R3: 그 회차 10분 계수 × 6, AVG: 3회 평균 10분 계수 × 6)와 사가정로 속도로 교통량 행을 만든다."""
    if rnd == "AVG":
        up = np.mean([UP[r] for r in (1, 2, 3)], axis=0)
        dn = np.mean([DOWN[r] for r in (1, 2, 3)], axis=0)
    else:
        r = int(rnd[1])
        up, dn = np.array(UP[r], float), np.array(DOWN[r], float)
    q = (up.sum() + dn.sum()) * 6
    p1 = 100 * (up[1] + dn[1]) / (up.sum() + dn.sum())
    split_east = dn.sum() / (up.sum() + dn.sum())
    qn = round(q * NIGHT_RATIO, 1)
    rows = [dict(key="사가정로", key_type="name", road_type="gemeinde", M_day=round(q, 1), M_night=qn,
                 p1_day=round(p1, 2), p2_day=0, p1_night=round(p1, 2), p2_night=0, v_pkw=v, v_lkw=v,
                 basis="measured",
                 note=f"현장 계수(16시대, 양방향). 화물차(이륜차 포함) 전부 Lkw1·Lkw2 0 가정. 속도 {v:.0f} km/h. 야간은 가정")]
    rows += saga_ufid_rows(split_east, round(q, 1), qn, round(p1, 2), v)
    for name, md, mn, note in OTHER_ROADS:
        rows.append(dict(key=name, key_type="name", road_type="gemeinde", M_day=md, M_night=mn,
                         v_pkw=SPEED_LIMIT, v_lkw=SPEED_LIMIT, basis="assumed", note=note))
    return rows, dict(Q=q, p1=p1, split_east_down=split_east)


FIELDS = ["key", "key_type", "road_type", "DTV", "M_day", "M_night", "p1_day", "p2_day", "p1_night", "p2_night",
          "v_pkw", "v_lkw", "surface", "note", "basis", "dir_split_fwd", "dir_basis"]


def write_config(d, grid, dk_rule="nearest_signal"):
    smap = ", ".join(f'"{k}": "{v}"' for k, v in SURFACE_MAP.items())
    cfg = f"""# {AI_NOTE}. baseline/config.yaml 과 같은 조건 + 측정점(점 수음점) + 노면 AC11
project: {{name: "사가정로_측정비교", output_dir: "output"}}
crs: 5186
input:
  map: {{folders: ["{MAPDIR}"]}}
  traffic: {{path: "traffic.csv"}}
  road_alias: {{"전농로25길": "전농로"}}
  surface_map: {{{smap}}}
  junctions: {{dk_rule: {dk_rule}}}
receivers:
  center: [205108.0, 553110.0]
  grid: {{enabled: {str(grid).lower()}, size: [480.0, 520.0], step: 2.5, heights: [4.0]}}
  facade: {{enabled: {str(grid).lower()}, radius: 230.0}}
  points: {{path: "../points.csv"}}
plots: {{limits: {{LrT: 68, LrN: 58, label: "도로교통소음 관리기준(주거지역 등) 주간 68 / 야간 58 dB(A)"}}}}
"""
    (d / "config.yaml").write_text(cfg, encoding="utf-8")


def nm(args, cwd):
    env = dict(os.environ, PYTHONPATH=str(REPO))
    print("  $ python -m noisemap", " ".join(args), f"(in {cwd.name})", flush=True)
    subprocess.run([sys.executable, "-m", "noisemap", *args], cwd=cwd, env=env, check=True,
                   stdout=subprocess.DEVNULL)


def meas_for(rnd, pid):
    if rnd == "AVG":
        return 10 * np.log10(np.mean([10 ** (MEASURED[pid][r] / 10) for r in (1, 2, 3)]))
    return MEASURED[pid][int(rnd[1])]


def sensitivity(model_avg50):
    """차종 구성 가정에 따른 사가정로 LW′ 변화(= 수음점 레벨 변화의 근사). 기준: AVG, 50 km/h, AC11."""
    sys.path.insert(0, str(REPO))
    from noisemap import rls19
    up = np.mean([UP[r] for r in (1, 2, 3)], axis=0)
    dn = np.mean([DOWN[r] for r in (1, 2, 3)], axis=0)
    q = (up.sum() + dn.sum()) * 6
    p = 100 * (up[1] + dn[1]) / (up.sum() + dn.sum())
    v = SPEED_LIMIT
    base = rls19.LW_prime(q, p, 0, v, v, "AC11", 0.0)
    cases = [
        ("기준: 50 km/h, AC11, 화물차(이륜차 포함) 전부 Lkw1", p, 0),
        ("화물차 절반을 Pkw로(2.8~3.5 t 소형 화물차 비중 가정)", p / 2, 0),
        ("화물차 전부 Pkw(p = 0)", 0, 0),
        ("화물차 전부 Lkw2(이륜차 비중의 상한, RLS-19는 이륜차를 Lkw2로 봄)", 0, p),
    ]
    rows = []
    for name, p1, p2 in cases:
        d = rls19.LW_prime(q, p1, p2, v, v, "AC11", 0.0) - base
        row = {"가정": name, "ΔLW′_dB": round(d, 1)}
        for pid in (1, 2):
            est = model_avg50[pid] + d
            row[f"지점{pid}_모델_추정"] = round(est, 1)
            row[f"지점{pid}_측정"] = round(meas_for("AVG", pid), 1)
            row[f"지점{pid}_차이"] = round(est - meas_for("AVG", pid), 1)
        rows.append(row)
    df = pd.DataFrame(rows)
    with open(BASE / "results" / "모델_민감도_차종.csv", "w", encoding="utf-8-sig", newline="") as f:
        f.write(f"# {AI_NOTE}. 3회 평균 교통량, 50 km/h, AC11, 신호교차로 보정 반영 결과 기준. ΔL ≈ ΔLW′ 근사(사가정로 지배)\n")
        df.to_csv(f, index=False)
    print(df.to_string())


def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family": "AppleGothic", "axes.unicode_minus": False, "font.size": 9,
                         "axes.spines.top": False, "axes.spines.right": False})
    return plt


def plot_comparison(df):
    """회차별 측정 LAeq,10min 과 모델 LrT(속도 30/40/50) 비교. 지점별 두 패널(같은 세로축)."""
    plt = _plt()
    color = {1: "#2a78d6", 2: "#eb6834"}
    marker = {30.0: "v", 40.0: "D", 50.0: "s"}
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4), sharey=True)
    for ax, (pid, rid) in zip(axes, ((1, "P1"), (2, "P2"))):
        d = df[df["수음점"] == rid]
        x = np.arange(len(ROUNDS))
        m = [d[(d["회차"] == r)]["측정_LAeq"].iloc[0] for r in ROUNDS]
        ax.plot(x, m, "o", ms=9, color=color[pid], mec="white", mew=1.5, label="측정 LAeq,10min", zorder=4)
        for k, v in enumerate(SPEEDS):
            y = [d[(d["회차"] == r) & (d["속도_km/h"] == v)]["모델_LrT"].iloc[0] for r in ROUNDS]
            diff = [d[(d["회차"] == r) & (d["속도_km/h"] == v)]["차이(모델LrT-측정)"].iloc[0] for r in ROUNDS]
            xo = x + (k - 1) * 0.16
            ax.plot(xo, y, marker[v], ms=6.5, mfc="none", mec=color[pid], mew=1.4,
                    label=f"모델 LrT {v:.0f} km/h" + (" (규정 속도)" if v == SPEED_LIMIT else ""))
            for xi, yi, di in zip(xo, y, diff):
                ax.annotate(f"{di:+.1f}", (xi, yi), textcoords="offset points", xytext=(0, 7), fontsize=6.8,
                            ha="center", color="#333")
        ax.set_xticks(x, ["1회", "2회", "3회", "3회 평균"])
        ax.set_title(RECEIVERS[rid]["label"], loc="left", fontsize=9.5)
        ax.grid(axis="y", color="#e4e4e4", lw=0.6)
        ax.set_ylim(62, 80)
    axes[0].set_ylabel("dB(A)")
    axes[0].legend(fontsize=7.5, frameon=False, loc="lower left")
    fig.suptitle("측정 LAeq 와 모델 LrT — 측정 교통량, 노면 AC11, 신호교차로 보정 반영, 사가정로 속도 30/40/50 km/h"
                 " (숫자 = 모델 - 측정)", x=0.01, ha="left", fontsize=10)
    fig.text(0.99, 0.01, AI_NOTE, ha="right", fontsize=7, color="#666")
    fig.tight_layout(rect=(0, 0.03, 1, 0.95))
    fig.savefig(BASE / "figures" / "모델_측정_비교.png", dpi=150)
    plt.close(fig)


def plot_zoom_map():
    """측정점 주변 확대 소음지도(격자 h = 4 m, AVG_v50 교통량)."""
    plt = _plt()
    from matplotlib.colors import BoundaryNorm, ListedColormap
    out = MODEL / MAP_SCEN / "output"
    g = pd.read_csv(out / "grid_result.csv")
    if "h" in g.columns:
        g = g[np.isclose(g["h"], 4.0)]
    pts = pd.read_csv(MODEL / "points.csv")
    pts = pts[pts.point_id.isin(["P1", "P2"])]
    pr = pd.read_csv(out / "point_result.csv")
    cx, cy = pts["x"].mean(), pts["y"].mean()
    half = 110.0
    sub = g[(g.x.between(cx - half, cx + half)) & (g.y.between(cy - half, cy + half))]
    xs, ys = np.sort(sub.x.unique()), np.sort(sub.y.unique())
    Z = sub.pivot_table(index="y", columns="x", values="LrT").reindex(index=ys, columns=xs).values
    levels = [45, 50, 55, 60, 65, 70, 75, 80, 85]   # 5 dB 단계(소음지도 관례)
    cols = ["#c9e8c2", "#8fd18a", "#3fa14b", "#f5e36c", "#f1a33c", "#e2552d", "#b31e2f", "#6e1a5f"]
    cmap, norm = ListedColormap(cols), BoundaryNorm(levels, len(cols))
    fig, ax = plt.subplots(figsize=(8, 7.4))
    im = ax.pcolormesh(xs, ys, Z, cmap=cmap, norm=norm, shading="nearest")
    bld = read_layer("B0010000")
    bld = bld[bld.geometry.intersects(gpd.GeoSeries.from_xy([cx], [cy]).buffer(half * 1.5).iloc[0])]
    bld.plot(ax=ax, facecolor="#d9d9d9", edgecolor="#777", lw=0.4)
    for _, p in pts.iterrows():
        ax.plot(p.x, p.y, marker="o", ms=9, mfc="white", mec="#111", mew=1.6, zorder=5)
        pid = RECEIVERS[p.point_id]["point"]
        mod = float(pr.loc[pr.point_id == p.point_id, "LrT"].iloc[0])
        ax.annotate(f"지점{pid}\n측정 {meas_for('AVG', pid):.1f} / 모델 {mod:.1f} dB\n(h = {p.h} m)", (p.x, p.y),
                    textcoords="offset points", xytext=(10, 14 if pid == 1 else -40), fontsize=8,
                    bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="#999", lw=0.5), zorder=6)
    ax.set_xlim(cx - half, cx + half)
    ax.set_ylim(cy - half, cy + half)
    ax.set_aspect("equal")
    ax.set_xlabel("x (EPSG:5186, m)")
    ax.set_ylabel("y (EPSG:5186, m)")
    cb = fig.colorbar(im, ax=ax, shrink=0.8, ticks=levels)
    cb.set_label("LrT (dB(A)), 격자 높이 4 m")
    ax.set_title("사가정로 측정점 주변 소음지도 — 측정 교통량(16시대 3회 평균) 입력\n"
                 "Q 2,540 대/h, 화물차 25.8 %(이륜차 포함, Lkw1), 50 km/h(규정 속도), 노면 AC11, 신호교차로 보정",
                 loc="left", fontsize=9.5)
    fig.text(0.99, 0.01, f"{AI_NOTE} · 격자 2.5 m · 측정점 표기값은 마이크 높이 1.3 m 계산값 · 다른 도로는 가정 교통량",
             ha="right", fontsize=7, color="#666")
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    fig.savefig(BASE / "figures" / "소음지도_측정점주변_AVG.png", dpi=170)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-map", action="store_true", help="소음지도(격자·파사드) 계산 생략")
    ap.add_argument("--only-map", action="store_true", help="점 비교는 건너뛰고 소음지도만")
    a = ap.parse_args()
    MODEL.mkdir(exist_ok=True)

    pts = receiver_points()
    with open(MODEL / "points.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(pts[0]))
        w.writeheader()
        w.writerows(pts)
    print("수음점:", pd.DataFrame(pts).to_string())

    summary = []
    for rnd in ROUNDS:
        for v in SPEEDS:
            scen = f"{rnd}_v{v:.0f}"
            map_run = scen == MAP_SCEN and not a.skip_map
            if a.only_map and not map_run:
                continue
            d = MODEL / scen
            d.mkdir(exist_ok=True)
            rows, info = traffic_rows(rnd, v)
            with open(d / "traffic.csv", "w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=FIELDS)
                w.writeheader()
                for r in rows:
                    w.writerow({k: r.get(k, "") for k in FIELDS})
            out = d / "output"
            write_config(d, grid=False, dk_rule="nearest")           # noisemap 기본 교차점 판정(비교용)
            nm(["build", "-c", "config.yaml"], d)
            nm(["run", "-c", "config.yaml", "--points"], d)
            shutil.copyfile(out / "point_result.csv", out / "point_result_default.csv")
            write_config(d, grid=map_run)                            # 신호교차로 기준(dk_rule = nearest_signal)
            nm(["build", "-c", "config.yaml"], d)
            nm(["run", "-c", "config.yaml"] + ([] if map_run else ["--points"]), d)
            if map_run:
                nm(["plot", "-c", "config.yaml"], d)
            pr = pd.read_csv(out / "point_result.csv").set_index("point_id")
            pr0 = pd.read_csv(out / "point_result_default.csv").set_index("point_id")
            for rid, rr in RECEIVERS.items():
                pid = rr["point"]
                meas = meas_for(rnd, pid)
                summary.append({
                    "시나리오": scen, "회차": rnd, "속도_km/h": v, "수음점": rid, "지점": pid,
                    "Q_양방향_대/h": round(info["Q"], 1), "p_%": round(info["p1"], 1),
                    "하행비율": round(info["split_east_down"], 3), "노면": "AC11",
                    "측정_LAeq": round(meas, 1), "모델_LrT": round(float(pr.loc[rid, "LrT"]), 2),
                    "모델_LrT_direct": round(float(pr.loc[rid, "LrT_direct"]), 2),
                    "모델_LrT_refl1": round(float(pr.loc[rid, "LrT_refl1"]), 2),
                    "모델_LrT_refl2": round(float(pr.loc[rid, "LrT_refl2"]), 2),
                    "모델_LrT_교차로기본판정": round(float(pr0.loc[rid, "LrT"]), 2),
                    "신호교차로보정_효과_dB": round(float(pr.loc[rid, "LrT"] - pr0.loc[rid, "LrT"]), 2),
                    "차이(모델LrT-측정)": round(float(pr.loc[rid, "LrT"]) - meas, 1)})

    if summary and not a.only_map:            # 비교표·그림은 12개 시나리오가 모두 있을 때만
        df = pd.DataFrame(summary)
        with open(BASE / "results" / "모델_측정_비교.csv", "w", encoding="utf-8-sig", newline="") as f:
            f.write(f"# {AI_NOTE}. 노면 AC11, 신호교차로 보정 반영. 모델_LrT_교차로기본판정 = noisemap 기본(가장 가까운 교차점)\n")
            df.to_csv(f, index=False)
        print(df.to_string())
        avg50 = {RECEIVERS[r]["point"]: float(df[(df.시나리오 == MAP_SCEN) & (df.수음점 == r)]["모델_LrT"].iloc[0])
                 for r in ("P1", "P2")}
        sensitivity(avg50)
        plot_comparison(df)
    if (MODEL / MAP_SCEN / "output" / "grid_result.csv").exists():
        plot_zoom_map()
        # noisemap 고품질 지도 중 주간(LrT) 결과를 figures/ 로 복사(model/*/output 은 git 무시 대상)
        for src, dst in [("HQ_grid_LrT_h4m", "소음지도_격자_LrT_h4m_AVG"), ("HQ_exceed_LrT_h4m", "소음지도_기준초과_LrT_h4m_AVG"),
                         ("HQ_facade_LrT", "소음지도_벽면_LrT_AVG")]:
            for ext in (".png", ".pdf"):
                f = MODEL / MAP_SCEN / "output" / (src + ext)
                if f.exists():
                    shutil.copyfile(f, BASE / "figures" / (dst + ext))


if __name__ == "__main__":
    main()
