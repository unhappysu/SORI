# -*- coding: utf-8 -*-
"""
지도(항공사진) 확인용 도로 목록 생성 — 수치지도 도로중심선(A002)을 읽기만 함.
  python tools/make_review_lists.py                      # baseline/config.yaml 의 수치지도·계산 중심 사용
  python tools/make_review_lists.py -c config.yaml -r 300 -o docs/review

출력(utf-8-sig CSV, 좌표 EPSG:5186 + 위경도, 디지타이즈 방위각 0 = 북, 시계방향):
  arterials.csv        반경 안 2차로 이상 객체. lanes_fwd·lanes_bwd 칸을 채우면 roads_override.csv 로 사용 가능
  oneway.csv           반경 안 일방통행 객체. oneway_dir(+1 = 디지타이즈 방향이 진행방향, −1 = 반대) 칸
  median_samples.csv   분리대유무 '유' 객체(지도 전체, 가까운 순) + 반경 안 4차로 이상 '무' 대표(도로명별 1개)
  oneway_multilane.csv 2차로 이상 일방통행 객체(지도 전체) — 램프·측도 여부 확인용
default_fwd_bwd 는 현재 기본 규칙(odd_lane_rule: ceil → 5 = 3+3)과 major_fwd(5 = 3+2)를 함께 보여 줌.
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import argparse
import math
import os
import sys

import geopandas as gpd
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from noisemap import config as C, inputs as I                    # noqa: E402

OV_COLS = ["ufid", "lanes_fwd", "lanes_bwd", "lanes_basis", "oneway_dir", "oneway_dir_basis", "note"]


def main(argv=None):
    ap = argparse.ArgumentParser(description="지도 확인용 도로 목록")
    ap.add_argument("-c", "--config", default=os.path.join(ROOT, "baseline", "config.yaml"))
    ap.add_argument("-r", "--radius", type=float, default=300.0, help="계산 중심에서 반경(m)")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "docs", "review"))
    a = ap.parse_args(argv)
    cfg = C.load(a.config)
    cx, cy = C.center(cfg)
    F = cfg["input"]["map"]["fields"]
    g = I.read_layer(cfg, "road")                                   # 지도 전체(영역 제한 없음)
    g = g.explode(index_parts=False)
    g = g[g.geometry.type == "LineString"].copy()
    from pyproj import Transformer
    tf = Transformer.from_crs(cfg["crs"], 4326, always_xy=True)
    g["dist_ctr_m"] = g.geometry.distance(gpd.points_from_xy([cx], [cy], crs=cfg["crs"])[0]).round(1)
    g["length_m"] = g.length.round(1)
    rows = []
    for geom in g.geometry:
        (x0, y0), (x1, y1) = geom.coords[0], geom.coords[-1]
        m = geom.interpolate(0.5, normalized=True)
        lon, lat = tf.transform(m.x, m.y)
        rows.append(dict(mid_x=round(m.x, 1), mid_y=round(m.y, 1), mid_lat=round(lat, 6), mid_lon=round(lon, 6),
                         start_x=round(x0, 1), start_y=round(y0, 1), end_x=round(x1, 1), end_y=round(y1, 1),
                         digitize_bearing_deg=round((math.degrees(math.atan2(x1 - x0, y1 - y0)) + 360) % 360)))
    g = pd.concat([g.reset_index(drop=True), pd.DataFrame(rows)], axis=1)
    g["ufid"] = g[F["uid"]].astype(str)
    g["도로명"] = g.get(F["road_name"], "")
    g["차로수"] = pd.to_numeric(g.get(F["road_lanes"]), errors="coerce")
    g["도로폭"] = pd.to_numeric(g.get(F["road_width"]), errors="coerce")
    g["일방통행"] = g.get(F["road_oneway"], "")
    g["분리대유무"] = g.get("분리대유무", "")
    info = ["도로명", "차로수", "도로폭", "일방통행", "분리대유무", "length_m", "dist_ctr_m", "mid_x", "mid_y", "mid_lat",
            "mid_lon", "start_x", "start_y", "end_x", "end_y", "digitize_bearing_deg"]
    near = g[g.dist_ctr_m <= a.radius]
    oneway_vals = set(cfg["input"]["map"]["oneway_values"])

    def ov_frame(df, rule_col=True):
        o = df.copy()
        for c in OV_COLS[1:]:
            o[c] = ""
        if rule_col:
            o["default_fwd_bwd"] = [f"ceil {math.ceil(n / 2)}+{math.ceil(n / 2)} / major_fwd {math.ceil(n / 2)}+{int(n) // 2}"
                                    if w not in oneway_vals else f"일방 {int(n)}+0" for n, w in zip(o["차로수"], o["일방통행"])]
        return o[OV_COLS + info + (["default_fwd_bwd"] if rule_col else [])]

    os.makedirs(a.out, exist_ok=True)
    art = near[near["차로수"] >= 2].sort_values(["도로명", "dist_ctr_m"])
    ov_frame(art).to_csv(os.path.join(a.out, "arterials.csv"), index=False, encoding="utf-8-sig")
    one = near[near["일방통행"].isin(oneway_vals)].sort_values(["도로명", "dist_ctr_m"])
    ov_frame(one, rule_col=False).to_csv(os.path.join(a.out, "oneway.csv"), index=False, encoding="utf-8-sig")
    yes = g[g["분리대유무"] == "유"].sort_values("dist_ctr_m").assign(sample="median_yes")
    no = near[(near["분리대유무"] == "무") & (near["차로수"] >= 4)].sort_values("dist_ctr_m").drop_duplicates("도로명")
    pd.concat([yes, no.head(5).assign(sample="median_no")])[["sample", "ufid"] + info].to_csv(
        os.path.join(a.out, "median_samples.csv"), index=False, encoding="utf-8-sig")
    ml = g[g["일방통행"].isin(oneway_vals) & (g["차로수"] >= 2)].sort_values("dist_ctr_m")
    ov_frame(ml).to_csv(os.path.join(a.out, "oneway_multilane.csv"), index=False, encoding="utf-8-sig")
    print(f"반경 {a.radius:g} m: 2차로 이상 {len(art)}개 {art['도로명'].value_counts().to_dict()}")
    print(f"반경 {a.radius:g} m: 일방통행 {len(one)}개 {one['도로명'].value_counts().to_dict()}")
    print(f"분리대 '유' {len(yes)}개(반경 안 {int((yes.dist_ctr_m <= a.radius).sum())}개), 2차로 이상 일방통행 {len(ml)}개")
    print("출력:", a.out)


if __name__ == "__main__":
    main()
