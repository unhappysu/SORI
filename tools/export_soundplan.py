# -*- coding: utf-8 -*-
"""
SORI 계산 입력을 SoundPLAN 으로 옮기기 위한 내보내기(같은 입력으로 두 프로그램을 비교하기 위함).
  python tools/export_soundplan.py <설정 yaml> [--out 폴더]
먼저 같은 설정으로 build 를 실행해 출력 폴더(project.output_dir)에 모델 파일이 있어야 함. propagation_method 는 rls90 권장.

만드는 것(좌표계 = 설정 crs, 보통 EPSG:5186). 셰이프파일 속성 이름은 10자 이하 영문, 글자는 UTF-8(.cpg)
  roads.shp          도로중심선 + SORI 가 실제로 쓴 교통량·속도·노면·차로·음원선 위치·L_m,E(확인용)
  emission_lines.shp SORI 음원선(바깥 차로 중앙 등) — SoundPLAN 음원선 위치와 겹쳐 보기용
  buildings.shp      건물(높이 H, 지반고 z0, 지붕고 ztop)
  signals.shp        신호교차로 축 교차점(RLS-90 표 2 K 기준점)
  receivers.shp      점 수음점(지면 위 높이 h)
  calc_area.shp      격자 계산 영역(사각형)
  terrain_points.csv SORI 지형(DTM) 표본 x, y, z (계산 영역 + 여유, 기본 5 m 간격) — SoundPLAN 높이점 가져오기용
  계산조건.md        SoundPLAN 에 똑같이 넣어야 할 조건표(설정·run_info 에서 자동)
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import argparse
import json
import math
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sori import config as C, rls90  # noqa: E402
from sori.propagation import get_model  # noqa: E402


def _p90(t, per):
    v = t.get(f"p_{per}")
    if v is not None and v == v:
        return float(v)
    return float(t[f"p1_{per}"]) + float(t[f"p2_{per}"])


def _lme(M, p, t):
    """경사 0 기준 L_m,E(식 6). M ≤ 0 이면 NaN"""
    return rls90.Lm_E(M, p, t.v_pkw, t.v_lkw, t.surface, 0.0) if M > 0 else np.nan


def road_table(out, crs):
    import geopandas as gpd
    import pyogrio
    R = pyogrio.read_dataframe(os.path.join(out, "roads.gpkg"), fid_as_index=True)
    T = pd.read_csv(os.path.join(out, "traffic_used.csv"), encoding="utf-8-sig")
    L = pd.read_csv(os.path.join(out, "source_lines.csv"), encoding="utf-8-sig").set_index("fid")
    if len(R) != len(T) or not (R.ufid.astype(str).values == T.ufid.astype(str).values).all():
        raise RuntimeError("roads.gpkg 와 traffic_used.csv 의 도로 순서가 다름 — 같은 build 결과인지 확인")
    rows = []
    for i, ((fid, r), t) in enumerate(zip(R.iterrows(), T.itertuples())):
        if str(t.traffic_src) == "excluded_minor":     # SORI 가 음원에서 뺀 골목길(model.minor_roads: exclude)은 내보내지 않음
            continue
        ln = L.loc[t.fid] if t.fid in L.index else None
        pD, pN = _p90(T.iloc[i], "day"), _p90(T.iloc[i], "night")
        sp = float(t.dir_split_fwd)
        rows.append(dict(
            fid=int(t.fid), ufid=str(t.ufid), name=str(t.name), lanes=int(t.lanes),
            width=float(r.width) if r.width == r.width else np.nan, oneway=int(bool(r.oneway)),
            M_D=float(t.M_day), M_N=float(t.M_night), p_D=round(pD, 3), p_N=round(pN, 3),
            v_pkw=float(t.v_pkw), v_lkw=float(t.v_lkw), surface=str(t.surface),
            DStrO=rls90.D_StrO(t.surface, t.v_pkw), split_fwd=sp,
            # 도로 전체 교통량 기준 L_m,E(경사 0) — SoundPLAN 도로 방출표의 값과 비교용
            LmE_D=round(_lme(t.M_day, pD, t), 2), LmE_N=round(_lme(t.M_night, pN, t), 2),
            ln_fwd=int(ln.lanes_fwd_used) if ln is not None else np.nan,
            ln_bwd=int(ln.lanes_bwd_used) if ln is not None else np.nan,
            lane_w=float(ln.lane_width) if ln is not None else np.nan,
            off_fwd=float(ln.offset_fwd) if ln is not None else np.nan,
            off_bwd=float(ln.offset_bwd) if ln is not None and ln.offset_bwd == ln.offset_bwd else np.nan,
            traf_src=str(t.traffic_src), geometry=r.geometry))
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=crs)


def emission_lines(roads):
    """source_lines.csv 의 오프셋으로 음원선 재구성(build_sources 와 같은 offset_curve)"""
    import geopandas as gpd
    rows = []
    for r in roads.itertuples():
        for side, off, share in (("fwd", r.off_fwd, r.split_fwd if r.oneway == 0 else 1.0),
                                 ("bwd", r.off_bwd, 1.0 - r.split_fwd)):
            if not (off == off):
                continue
            try:
                g = r.geometry if off == 0 else r.geometry.offset_curve(off)
            except Exception:
                continue
            single = side == "fwd" and r.ln_bwd == 0 and r.oneway == 0
            rows.append(dict(fid=r.fid, ufid=r.ufid, side="both" if single else side,
                             M_share=1.0 if single else round(share, 4), offset=off, geometry=g))
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=roads.crs)


def write_shp(gdf, path):
    gdf.to_file(path, driver="ESRI Shapefile", encoding="UTF-8")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config")
    ap.add_argument("--out", help="내보낼 폴더(기본: 출력 폴더/soundplan_export)")
    ap.add_argument("--terrain-step", type=float, default=5.0, help="지형 표본 간격 m (기본 5)")
    ap.add_argument("--terrain-margin", type=float, default=100.0, help="지형 표본 여유 m (기본 100)")
    a = ap.parse_args(argv)
    import geopandas as gpd
    from shapely.geometry import box, Point
    cfg = C.load(a.config)
    os.chdir(Path(a.config).resolve().parent)
    out = cfg["project"]["output_dir"]
    dst = Path(a.out or os.path.join(out, "soundplan_export"))
    dst.mkdir(parents=True, exist_ok=True)
    crs = f"EPSG:{cfg['crs']}"
    bi = json.load(open(os.path.join(out, "build_info.json"), encoding="utf-8"))

    roads = road_table(out, crs)
    write_shp(roads, dst / "roads.shp")
    write_shp(emission_lines(roads), dst / "emission_lines.shp")
    B = gpd.read_file(os.path.join(out, "buildings.gpkg"))
    write_shp(B[["bid", "floors", "H", "z0", "ztop", "geometry"]], dst / "buildings.shp")
    J = pd.read_csv(os.path.join(out, "junctions_used.csv"), encoding="utf-8-sig")
    J = J[J["type"] == "signal"] if len(J) else J
    write_shp(gpd.GeoDataFrame(J[["cluster"]].assign(type="signal") if len(J) else pd.DataFrame({"cluster": []}),
                               geometry=[Point(x, y) for x, y in zip(J.x, J.y)] if len(J) else [], crs=crs),
              dst / "signals.shp")
    gb = C.grid_bbox(cfg)
    write_shp(gpd.GeoDataFrame({"name": ["calc_area"], "h_grid": [float(cfg["receivers"]["grid"]["heights"][0])],
                                "step": [float(cfg["receivers"]["grid"]["step"])]}, geometry=[box(*gb)], crs=crs),
              dst / "calc_area.shp")
    pts = None
    pp = cfg["receivers"].get("points", {}).get("path")
    if pp:
        from sori.run import read_points
        pts = read_points(pp, cfg["receivers"]["points"].get("default_height"))
        write_shp(gpd.GeoDataFrame(pts[["point_id", "h"]], geometry=[Point(x, y) for x, y in zip(pts.x, pts.y)],
                                   crs=crs), dst / "receivers.shp")
    d = np.load(os.path.join(out, "model_rasters.npz"))
    m = a.terrain_margin
    xs = np.arange(gb[0] - m, gb[2] + m + 1e-6, a.terrain_step)
    ys = np.arange(gb[1] - m, gb[3] + m + 1e-6, a.terrain_step)
    X, Y = np.meshgrid(xs, ys)
    from sori.run import point_ground                  # 점 수음점 지면고와 같은 1차 보간
    zg, _, outside = point_ground(d, X.ravel(), Y.ravel())
    pd.DataFrame({"x": X.ravel(), "y": Y.ravel(), "z": np.round(zg, 2)})[~outside].to_csv(
        dst / "terrain_points.csv", index=False)

    model = get_model(cfg)
    rf = cfg["propagation"]["reflection"]
    ri_p = os.path.join(out, "run_info.json")
    ri = json.load(open(ri_p, encoding="utf-8")) if os.path.exists(ri_p) else {}
    near = roads.cx[gb[0] - 50:gb[2] + 50, gb[1] - 50:gb[3] + 50]
    main_rows = near.sort_values("M_D", ascending=False).drop_duplicates("name").head(8)
    lines = [
        "# SoundPLAN 비교용 계산 조건표", "",
        f"※ Claude(Anthropic의 AI)를 활용하여 작성하였음. `tools/export_soundplan.py` 가 `{a.config}` 와 출력 폴더에서 자동 생성.", "",
        "SoundPLAN 계산을 SORI 와 같은 조건으로 맞추기 위한 표. **굵게** 표시한 항목은 결과 차이에 크게 영향을 줌.", "",
        "| 항목 | SORI 값 | SoundPLAN 에서 맞출 곳 |", "|---|---|---|",
        f"| **계산 기준** | {model.combo_label(cfg)} | Calculation properties → Standards: Road = RLS-90 |",
        f"| 음원 표기 | {bi.get('emission', '(build_info 에 없음)')} | — |",
        f"| **반사 차수** | 격자 {rf['max_order_grid']} / 벽면 {rf['max_order_facade']} / 점 {rf['max_order_point']} "
        "(RLS-90 4.6 은 1차) | Reflection order |",
        f"| **반사 손실 D_E** | {float(rf.get('D_E', -1.0)):g} dB (표 7 매끈한 외벽) | 건물 반사 손실(Reflection loss) = 1 dB |",
        f"| 반사면 탐색 거리 | 수음점–벽 {rf['r_facade']:g} m, 음원–벽(간선) {rf['r_source_main']:g} m | "
        "Max. reflection distance to receiver / source |",
        f"| **탐색 반경** | 간선 {cfg['propagation']['max_dist_main']:g} m, 이면도로 {cfg['propagation']['max_dist_minor']:g} m | "
        "Search radius(같은 값 또는 그 이상) |",
        f"| 구간 길이 | 간선 {cfg['model']['segment_main']:g} m, 이면도로 {cfg['model']['segment_minor']:g} m | "
        "Teilstück 길이(자동이면 기록) |",
        "| **교차로 할증 K** | 표 2: 수음점 ~ 가장 가까운 신호 교차점(signals.shp) 40/70/100 m → 3/2/1 dB | "
        "도로 '신호교차로(Traffic lights)' 지정 — 같은 교차점 |",
        "| 음원 위치 | 두 바깥 차로 중앙 0,5 m(emission_lines.shp, roads.shp 의 off_fwd·off_bwd) | "
        "도로 폭·차로 배치(Emission line distance) |",
        "| **교통량** | roads.shp 의 M_D·M_N(대/h, 양방향 합), p_D·p_N(%) | 도로별 시간당 교통량(M)·화물차 비율 직접 입력(DTV 아님) |",
        "| 방향 배분 | roads.shp split_fwd(디지타이즈 방향 비율, 기본 0.5) | 차로별 교통량 |",
        "| 속도 | roads.shp v_pkw·v_lkw | 승용차·화물차 속도 |",
        "| 노면 D_StrO | roads.shp DStrO(dB) | 노면 보정(Road surface) |",
        "| 경사 D_Stg | 지형(DTM)에서 산정, |g| > 5 % 일 때 0,6|g| − 3 | 도로 높이 = 지형 |",
        f"| 격자 | 영역 calc_area.shp, 간격 {cfg['receivers']['grid']['step']:g} m, 높이 {cfg['receivers']['grid']['heights']} m | "
        "Grid map: 같은 영역·간격·높이 |",
        "| 점 수음점 | receivers.shp (h = 지면 위 m) | Receiver: 같은 좌표·높이, 층 높이 정의 확인 |",
        "| 건물 | buildings.shp (H = 높이, z0 지반고) | 건물 높이(절대/상대) 확인 |",
        "| 지형 | terrain_points.csv(SORI DTM 표본) 또는 같은 등고선 | DGM |",
        "| 지면 감쇠 | D_BM 식 (22), h_m = 경로 평균 높이 | (RLS-90 고정) |",
        "| 반올림 | 중간값 반올림 안 함, 비교는 소수 둘째 자리 | 결과 표 소수 자리 늘리기 |",
        "", "## 계산 영역 주변 주요 도로(교통량 큰 순)", "",
        "| 도로명 | 차로 | M_D | M_N | p_D | p_N | v_pkw | 노면 | L_m,E 주간 | L_m,E 야간 | 교통량 출처 |",
        "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in main_rows.itertuples():
        lines.append(f"| {r.name} | {r.lanes} | {r.M_D:.1f} | {r.M_N:.1f} | {r.p_D:g} | {r.p_N:g} | {r.v_pkw:g} | "
                     f"{r.surface} ({r.DStrO:g}) | {r.LmE_D:.1f} | {r.LmE_N:.1f} | {r.traf_src} |")
    lines += ["", "L_m,E 는 도로 전체 교통량·경사 0 기준(식 6). SoundPLAN 도로 방출표(Emission)와 먼저 비교하면 "
              "음원 입력 차이를 전파 차이와 나눠 볼 수 있음.", "",
              "## 파일", "", "| 파일 | 내용 |", "|---|---|",
              "| roads.shp | 도로중심선, 교통량·속도·노면·차로·음원선 오프셋·L_m,E |",
              "| emission_lines.shp | SORI 음원선 |", "| buildings.shp | 건물 |",
              "| signals.shp | 신호 교차점(K 기준) |", "| receivers.shp | 점 수음점 |",
              "| calc_area.shp | 격자 영역 |", "| terrain_points.csv | 지형 표본 |",
              "", f"도로 {len(roads)}개, 건물 {len(B)}개, 신호 교차점 {len(J)}개, 점 수음점 {0 if pts is None else len(pts)}개, "
              f"지형 표본 {X.size}개. 계산 실행 시각: {ri.get('time', '(run 전)')}."]
    (dst / "계산조건.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"내보냄: {dst}")
    return dst


if __name__ == "__main__":
    main()
