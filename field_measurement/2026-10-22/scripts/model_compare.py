"""측정 교통량을 noisemap(RLS-19 + ISO 9613-2)에 넣어 측정값과 비교하고 소음지도를 만든다.

AI(Claude)를 활용하여 제작하였음.

실행: python3 field_measurement/2026-10-22/scripts/model_compare.py [--skip-map]
- noisemap 소스코드는 수정하지 않는다. 저장소의 noisemap 패키지를 그대로 호출한다.
- 시나리오: R1·R2·R3(회차별 동시 교통량), AVG(3회 평균). 각 시나리오마다 build → 점 수음점 계산.
- 소음지도: AVG 교통량으로 격자·파사드 전체 계산(run) → plot.

수음점 좌표(수치지도 기준, 측정자가 잰 도로끝선 거리 사용)
- 도로끝선 = 수치지도 보도(A0033320) 안쪽 경계. 사가정로 중심선(UFID …465)에서 북측 10.7 m, 남측 9.2 m.
- 지점1: 중심선 시점에서 s = 88 m(경도 127.0588), 북측으로 10.7 + 4.213 m.
  → 건물 벽까지 약 1.0 m(야장 1.001 m와 일치). 도로명주소 119~127 사이(사가정로 123)와도 맞음.
- 지점2: s = 100 m(경도 127.0589, SK아파트 상가 앞), 남측으로 9.2 + 3.321 m.
  → 건물 벽까지 약 3.0 m(야장 1.13 m로 판독되어 2 m 차이. 상가 차양 등일 가능성, 미확인).
"""
import argparse
import csv
import glob
import json
import os
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

# 측정 교통량(10분, 승용차/화물차) — analyze.py 와 같은 값. 방향은 지도 판정.
UP = {1: (148, 33), 2: (138, 54), 3: (133, 47)}      # 상행(서행) — 지점1 조
DOWN = {1: (180, 76), 2: (180, 68), 3: (163, 50)}    # 하행(동행) — 지점2 조
MEASURED = {1: {1: 68.12, 2: 68.26, 3: 69.20}, 2: {1: 68.92, 2: 68.98, 3: 69.29}}  # 재계산 LAeq,10min
SPEED = 50.0   # 사가정로 규정(최고) 속도 km/h — OSM maxspeed=50, 서울 안전속도 5030

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
RECEIVERS = {"P1": dict(s=88.0, side=+1, curb=None, edge=4.213, h=1.309),
             "P2": dict(s=100.0, side=-1, curb=None, edge=3.321, h=1.312)}


def read_layer(code):
    fs = glob.glob(f"{MAPDIR}/**/*{code}*.shp", recursive=True)
    return pd.concat([gpd.read_file(f, encoding="cp949") for f in fs])


def receiver_points():
    """수치지도 사가정로 중심선·보도 경계로 측정점 좌표를 만든다."""
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
        n = np.array([-t[1], t[0]]) * r["side"]        # +1 = 디지타이즈 방향(서→동) 왼쪽 = 북측

        def first_hit(geom):
            ray = LineString([(q.x, q.y), (q.x + n[0] * 45, q.y + n[1] * 45)])
            x = geom.intersection(ray)
            d = [np.hypot(p.x - q.x, p.y - q.y) for p in getattr(x, "geoms", [x]) if p.geom_type == "Point"]
            return min(d) if d else np.nan

        curb = first_hit(sw)
        off = curb + r["edge"]
        x, y = q.x + n[0] * off, q.y + n[1] * off
        lon, lat = inv.transform(x, y)
        rows.append({"point_id": pid, "x": round(x, 2), "y": round(y, 2), "h": r["h"],
                     "lon": round(lon, 6), "lat": round(lat, 6), "s_along_m": r["s"],
                     "curb_from_centerline_m": round(curb, 2), "receiver_from_centerline_m": round(off, 2),
                     "building_from_receiver_m": round(first_hit(bld) - off, 2)})
    return rows


def saga_ufid_rows(split_fwd_east, q_day, q_night, p1, v=SPEED, surface=""):
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
                        p1_day=p1, p2_day=0, p1_night=p1, p2_night=0, v_pkw=v, v_lkw=v, surface=surface,
                        dir_split_fwd=round(fwd, 4), dir_basis="measured", basis="measured",
                        note=f"현장 계수(동쪽 접근로), 디지타이즈 {'동' if east else '서'}향"))
    return out


# 민감도 확인용 실제 재계산 시나리오(비교 기준은 AVG). 규정 속도 대신 가정값을 쓰므로 주 결과로 쓰지 않는다.
SENS_SCEN = {"AVGs": dict(v=30.0, surface="AC11", pfac=0.5,
                          label="30 km/h + 노면 AC11 + 화물차 절반 Pkw (민감도, 가정)")}


def traffic_rows(scen):
    opt = SENS_SCEN.get(scen, dict(v=SPEED, surface="", pfac=1.0))
    if scen.startswith("AVG"):
        up = np.mean([UP[r] for r in (1, 2, 3)], axis=0)
        dn = np.mean([DOWN[r] for r in (1, 2, 3)], axis=0)
    else:
        r = int(scen[1])
        up, dn = np.array(UP[r], float), np.array(DOWN[r], float)
    q = (up.sum() + dn.sum()) * 6
    p1 = 100 * (up[1] + dn[1]) / (up.sum() + dn.sum()) * opt["pfac"]
    split_east = dn.sum() / (up.sum() + dn.sum())
    qn = round(q * NIGHT_RATIO, 1)
    rows = [dict(key="사가정로", key_type="name", road_type="gemeinde", M_day=round(q, 1), M_night=qn,
                 p1_day=round(p1, 2), p2_day=0, p1_night=round(p1, 2), p2_night=0, v_pkw=opt["v"], v_lkw=opt["v"],
                 surface=opt["surface"],
                 basis="measured",
                 note="현장 계수(16시대, 양방향). 화물차 전부 Lkw1·Lkw2 0 가정. 속도 = 규정 속도. 야간은 가정")]
    rows += saga_ufid_rows(split_east, round(q, 1), qn, round(p1, 2), opt["v"], opt["surface"])
    for name, md, mn, note in OTHER_ROADS:
        rows.append(dict(key=name, key_type="name", road_type="gemeinde", M_day=md, M_night=mn,
                         v_pkw=SPEED, v_lkw=SPEED, basis="assumed", note=note))
    return rows, dict(Q=q, p1=p1, split_east_down=split_east)


FIELDS = ["key", "key_type", "road_type", "DTV", "M_day", "M_night", "p1_day", "p2_day", "p1_night", "p2_night",
          "v_pkw", "v_lkw", "surface", "note", "basis", "dir_split_fwd", "dir_basis"]


def write_config(d, grid):
    cfg = f"""# {AI_NOTE}. baseline/config.yaml 과 같은 조건 + 측정점(점 수음점)
project: {{name: "사가정로_측정비교", output_dir: "output"}}
crs: 5186
input:
  map: {{folders: ["{MAPDIR}"]}}
  traffic: {{path: "traffic.csv"}}
  road_alias: {{"전농로25길": "전농로"}}
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


def sensitivity(model_avg):
    """입력 가정에 따른 사가정로 LW′ 변화(= 수음점 레벨 변화의 근사).

    측정점에서는 사가정로가 지배 음원이므로 전파 조건이 같으면 ΔL ≈ ΔLW′. noisemap.rls19 식을 그대로 쓴다.
    """
    sys.path.insert(0, str(REPO))
    from noisemap import rls19
    up = np.mean([UP[r] for r in (1, 2, 3)], axis=0)
    dn = np.mean([DOWN[r] for r in (1, 2, 3)], axis=0)
    q = (up.sum() + dn.sum()) * 6
    p = 100 * (up[1] + dn[1]) / (up.sum() + dn.sum())
    base = rls19.LW_prime(q, p, 0, SPEED, SPEED, "ref", 0.0)
    cases = [
        ("기준: 규정 속도 50 km/h, 화물차 전부 Lkw1, 노면 ref(Gussasphalt)", q, p, 0, 50, 50, "ref"),
        ("속도 40 km/h", q, p, 0, 40, 40, "ref"),
        ("속도 30 km/h (RLS-19 최저 적용값, 신호 대기·정체 근사)", q, p, 0, 30, 30, "ref"),
        ("화물차 절반을 Pkw로(2.8~3.5 t 소형 화물차 비중 가정)", q, p / 2, 0, 50, 50, "ref"),
        ("화물차 전부 Pkw(p = 0)", q, 0, 0, 50, 50, "ref"),
        ("노면 AC11(밀입도 아스팔트콘크리트, 표 4a)", q, p, 0, 50, 50, "AC11"),
        ("속도 30 km/h + 노면 AC11", q, p, 0, 30, 30, "AC11"),
        ("속도 30 km/h + 노면 AC11 + 화물차 절반 Pkw", q, p / 2, 0, 30, 30, "AC11"),
        ("화물차 전부 Lkw2(상한 확인용)", q, 0, p, 50, 50, "ref"),
    ]
    meas = {pid: 10 * np.log10(np.mean([10 ** (MEASURED[pid][r] / 10) for r in (1, 2, 3)])) for pid in (1, 2)}
    rows = []
    for name, qq, p1, p2, vp, vl, sdt in cases:
        d = rls19.LW_prime(qq, p1, p2, vp, vl, sdt, 0.0) - base
        row = {"가정": name, "ΔLW′_dB": round(d, 1)}
        for pid in (1, 2):
            est = model_avg[pid] + d
            row[f"지점{pid}_모델_추정"] = round(est, 1)
            row[f"지점{pid}_측정"] = round(meas[pid], 1)
            row[f"지점{pid}_차이"] = round(est - meas[pid], 1)
        rows.append(row)
    out = BASE / "results" / "모델_민감도.csv"
    df = pd.DataFrame(rows)
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        f.write(f"# {AI_NOTE}. 3회 평균 교통량 기준, ΔL ≈ ΔLW′ 근사(사가정로 지배)\n")
        df.to_csv(f, index=False)
    print(df.to_string())


def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family": "AppleGothic", "axes.unicode_minus": False, "font.size": 9,
                         "axes.spines.top": False, "axes.spines.right": False})
    return plt


def plot_comparison():
    """회차별 측정 LAeq,10min 과 모델 LrT(동시 교통량) 비교 그림."""
    plt = _plt()
    df_all = pd.read_csv(BASE / "results" / "모델_측정_비교.csv", comment="#")
    df = df_all[~df_all["시나리오"].isin(list(SENS_SCEN))]
    sens_run = df_all[df_all["시나리오"] == "AVGs"].set_index("지점")["차이(모델LrT-측정)"]
    color = {1: "#2a78d6", 2: "#eb6834"}
    fig, ax = plt.subplots(figsize=(8, 4.2))
    scen = ["R1", "R2", "R3", "AVG"]
    for i, pid in enumerate((1, 2)):
        d = df[df["지점"] == pid].set_index("시나리오").loc[scen]
        x = np.arange(4) + (i - 0.5) * 0.24
        ax.plot(x, d["측정_LAeq"], "o", ms=8, color=color[pid], mec="white", mew=1.5, label=f"지점{pid} 측정 LAeq")
        ax.plot(x, d["모델_LrT"], "s", ms=7, mfc="none", mec=color[pid], mew=1.5, label=f"지점{pid} 모델 LrT (50 km/h, ref 노면)")
        for xi, m, c in zip(x, d["측정_LAeq"], d["모델_LrT"]):
            ax.plot([xi, xi], [m, c], color=color[pid], lw=0.8, alpha=0.6)
            ax.annotate(f"+{c - m:.1f}", (xi, (m + c) / 2), textcoords="offset points", xytext=(4, 0), fontsize=7.5,
                        color="#333", va="center")
    ax.set_xticks(range(4), ["1회\n16:15–16:25", "2회\n16:28–16:38", "3회\n16:41–16:51", "3회 평균"])
    ax.set_ylabel("dB(A)")
    ax.set_ylim(60, 82)
    ax.grid(axis="y", color="#e4e4e4", lw=0.6)
    ax.set_title("측정 LAeq,10min 과 모델(RLS-19 + ISO 9613-2) LrT — 같은 시간의 측정 교통량 입력", loc="left", fontsize=10)
    ax.legend(fontsize=7.5, frameon=False, loc="lower right", ncol=2)
    if len(sens_run):
        fig.text(0.01, 0.01, f"민감도 재계산(3회 평균): {SENS_SCEN['AVGs']['label']} → 차이 "
                 f"지점1 {sens_run[1]:+.1f}, 지점2 {sens_run[2]:+.1f} dB", fontsize=7, color="#333")
    fig.text(0.99, 0.01, AI_NOTE, ha="right", fontsize=7, color="#666")
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(BASE / "figures" / "모델_측정_비교.png", dpi=150)
    plt.close(fig)


def plot_zoom_map():
    """측정점 주변 확대 소음지도(격자 h = 4 m, AVG 교통량)."""
    plt = _plt()
    from matplotlib.colors import BoundaryNorm, ListedColormap
    out = MODEL / "AVG" / "output"
    g = pd.read_csv(out / "grid_result.csv")
    hcol = [c for c in g.columns if c.lower() in ("h", "height", "z_rel")]
    if hcol:
        g = g[np.isclose(g[hcol[0]], 4.0)]
    pts = pd.read_csv(MODEL / "points.csv")
    pr = pd.read_csv(out / "point_result.csv") if (out / "point_result.csv").exists() else None
    cx, cy = pts["x"].mean(), pts["y"].mean()
    half = 110.0
    sub = g[(g.x.between(cx - half, cx + half)) & (g.y.between(cy - half, cy + half))]
    xs, ys = np.sort(sub.x.unique()), np.sort(sub.y.unique())
    Z = sub.pivot_table(index="y", columns="x", values="LrT").reindex(index=ys, columns=xs).values
    # 5 dB 단계 구간(소음지도 관례)
    levels = [45, 50, 55, 60, 65, 70, 75, 80, 85]
    cols = ["#c9e8c2", "#8fd18a", "#3fa14b", "#f5e36c", "#f1a33c", "#e2552d", "#b31e2f", "#6e1a5f"]
    cmap, norm = ListedColormap(cols), BoundaryNorm(levels, len(cols))
    fig, ax = plt.subplots(figsize=(8, 7.4))
    im = ax.pcolormesh(xs, ys, Z, cmap=cmap, norm=norm, shading="nearest")
    bld = read_layer("B0010000")
    bld = bld[bld.geometry.intersects(gpd.GeoSeries.from_xy([cx], [cy]).buffer(half * 1.5).iloc[0])]
    bld.plot(ax=ax, facecolor="#d9d9d9", edgecolor="#777", lw=0.4)
    for _, p in pts.iterrows():
        ax.plot(p.x, p.y, marker="o", ms=9, mfc="white", mec="#111", mew=1.6, zorder=5)
        pid = int(p.point_id[1])
        meas = 10 * np.log10(np.mean([10 ** (MEASURED[pid][r] / 10) for r in (1, 2, 3)]))
        mod = float(pr.loc[pr.point_id == p.point_id, "LrT"].iloc[0]) if pr is not None else np.nan
        ax.annotate(f"지점{pid}\n측정 {meas:.1f} / 모델 {mod:.1f} dB\n(h = {p.h} m)", (p.x, p.y),
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
                 "Q 2,540 대/h, 화물차 25.8 %(Lkw1), 50 km/h(규정 속도). 다른 도로는 가정 교통량", loc="left", fontsize=9.5)
    fig.text(0.99, 0.01, f"{AI_NOTE} · 격자 2.5 m · 측정점 표기값은 마이크 높이 1.3 m 계산값", ha="right", fontsize=7, color="#666")
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
    print("수음점:", pts)

    summary = []
    for scen in ("R1", "R2", "R3", "AVG", *SENS_SCEN):
        d = MODEL / scen
        d.mkdir(exist_ok=True)
        rows, info = traffic_rows(scen)
        with open(d / "traffic.csv", "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in FIELDS})
        map_run = scen == "AVG" and not a.skip_map
        write_config(d, grid=map_run)
        if a.only_map and not map_run:
            continue
        nm(["build", "-c", "config.yaml"], d)
        if not a.only_map:
            nm(["run", "-c", "config.yaml", "--points"], d)
            pr = pd.read_csv(d / "output" / "point_result.csv")
            for _, p in pr.iterrows():
                pid = int(str(p["point_id"])[1])
                meas = (10 * np.log10(np.mean([10 ** (MEASURED[pid][r] / 10) for r in (1, 2, 3)]))
                        if scen.startswith("AVG") else MEASURED[pid][int(scen[1])])
                row = {"시나리오": scen, "지점": pid, "Q_양방향_대/h": round(info["Q"], 1),
                       "p_%": round(info["p1"], 1), "하행비율": round(info["split_east_down"], 3),
                       "속도_km/h": SENS_SCEN.get(scen, {}).get("v", SPEED),
                       "노면": SENS_SCEN.get(scen, {}).get("surface", "") or "ref(지도 '아스팔트' 기본 매핑)",
                       "측정_LAeq": round(meas, 1)}
                for c in pr.columns:
                    if c.startswith("LrT") or c in ("zg", "note"):
                        row[f"모델_{c}"] = p[c]
                row["차이(모델LrT-측정)"] = round(float(p["LrT"]) - meas, 1)
                summary.append(row)
        if map_run:
            nm(["run", "-c", "config.yaml"], d)
            nm(["plot", "-c", "config.yaml"], d)

    if summary:
        out = BASE / "results" / "모델_측정_비교.csv"
        df = pd.DataFrame(summary)
        with open(out, "w", encoding="utf-8-sig", newline="") as f:
            f.write(f"# {AI_NOTE}\n")
            df.to_csv(f, index=False)
        print(df.to_string())
        avg = {int(r["지점"]): float(r["모델_LrT"]) for r in summary if r["시나리오"] == "AVG"}
        sensitivity(avg)
        plot_comparison()
    if (MODEL / "AVG" / "output" / "grid_result.csv").exists():
        plot_zoom_map()
        # noisemap 고품질 지도 중 주간(LrT) 결과를 figures/ 로 복사(model/*/output 은 git 무시 대상)
        import shutil
        for src, dst in [("HQ_grid_LrT_h4m", "소음지도_격자_LrT_h4m_AVG"), ("HQ_exceed_LrT_h4m", "소음지도_기준초과_LrT_h4m_AVG"),
                         ("HQ_facade_LrT", "소음지도_벽면_LrT_AVG")]:
            for ext in (".png", ".pdf"):
                f = MODEL / "AVG" / "output" / (src + ext)
                if f.exists():
                    shutil.copyfile(f, BASE / "figures" / (dst + ext))


if __name__ == "__main__":
    main()
