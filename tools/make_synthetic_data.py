# -*- coding: utf-8 -*-
"""
시험용 가상 데이터 생성: 수치지도 v2.0과 같은 파일명·한글 필드·cp949 인코딩,
DEM은 다른 좌표계(EPSG:5179)의 GeoTIFF → 재투영 경로까지 시험.
실제 전농사거리 자료가 아님(코드 동작·검증용).
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import os
import sys

import numpy as np
import geopandas as gpd
from shapely.geometry import LineString, Point, box

CX, CY = 205108.0, 553110.0
CRS = 5186


def terrain(x, y):
    return 30.0 + 0.02 * (x - CX) + 0.01 * (y - CY)


def main(root):
    mp = os.path.join(root, "data", "map", "37705")
    os.makedirs(mp, exist_ok=True)
    # ---------------- 도로: 가로A(동서 6차로), 가로B(남북 6차로), 이름없는 골목 2개
    roads = []
    def add(coords, name, lanes, width, oneway="", surf="아스팔트"):
        roads.append(dict(UFID=f"R{len(roads):04d}", 도로명=name, 차로수=lanes, 도로폭=width,
                          일방통행=oneway, 포장재질=surf, geometry=LineString(coords)))
    add([(CX - 520, CY), (CX - 200, CY)], "가로A", 6, 25)
    add([(CX - 200, CY), (CX, CY)], "가로A", 6, 25)          # 차수 2 절점에서 분할(연장)
    add([(CX, CY), (CX + 520, CY)], "가로A", 6, 25)
    add([(CX, CY - 560), (CX, CY)], "가로B", 6, 25)
    add([(CX, CY), (CX, CY + 560)], "가로B", 6, 25)
    add([(CX + 150, CY), (CX + 150, CY - 260)], "", 1, 4, surf="블록")   # T자 골목(남측)
    add([(CX - 300, CY + 120), (CX - 40, CY + 120)], "", 2, 7)           # 북서측 이면도로(가로B에 접속 안 함)
    add([(CX - 40, CY + 120), (CX, CY + 120)], "", 2, 7)                 # → 가로B와 T자 접속
    gpd.GeoDataFrame(roads, crs=CRS).to_file(os.path.join(mp, "N1L_A0020000.shp"), encoding="cp949")
    # ---------------- 건물: 도로변 가로 블록 + 고층 1동 + 맞벽 2동
    bl = []
    def b(x0, y0, x1, y1, fl, kind="일반주택", name=""):
        bl.append(dict(UFID=f"B{len(bl):04d}", 종류=kind, 층수=fl, 명칭=name, geometry=box(x0, y0, x1, y1)))
    for sx in (-1, 1):
        for sy in (-1, 1):
            for i in range(6):
                x0 = CX + sx * (25 + i * 35); x1 = x0 + sx * 28
                y0 = CY + sy * 20; y1 = y0 + sy * 15
                b(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1), 4 + (i % 3) * 2)
            for i in range(5):
                y0 = CY + sy * (60 + i * 35); y1 = y0 + sy * 25
                x0 = CX + sx * 20; x1 = x0 + sx * 14
                b(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1), 5)
    b(CX + 40, CY + 50, CX + 70, CY + 80, 20, "아파트", "고층동")
    b(CX - 120, CY - 120, CX - 100, CY - 100, 3)                        # 맞벽 쌍
    b(CX - 100, CY - 120, CX - 80, CY - 100, 3)
    b(CX + 60, CY - 90, CX + 64, CY - 86, 1, "무벽건물")                # 제외 대상
    gpd.GeoDataFrame(bl, crs=CRS).to_file(os.path.join(mp, "N1A_B0010000.shp"), encoding="cp949")
    # ---------------- 등고선(1 m 간격 직선 근사) + 표고점
    cl = []
    xs = np.linspace(CX - 900, CX + 900, 181)
    for z in np.arange(10, 51, 1.0):
        # 30 + 0.02 dx + 0.01 dy = z  → dy = (z - 30 - 0.02 dx)/0.01
        ys = CY + (z - 30 - 0.02 * (xs - CX)) / 0.01
        ok = np.abs(ys - CY) < 900
        if ok.sum() > 1:
            cl.append(dict(UFID=f"C{len(cl):04d}", 등고수치=z, geometry=LineString(np.c_[xs[ok], ys[ok]])))
    gpd.GeoDataFrame(cl, crs=CRS).to_file(os.path.join(mp, "N1L_F0010000.shp"), encoding="cp949")
    sp = [dict(UFID=f"S{i}", 수치=round(terrain(x, y), 2), geometry=Point(x, y))
          for i, (x, y) in enumerate([(CX + 300, CY + 300), (CX - 300, CY - 300), (CX + 10, CY - 10)])]
    gpd.GeoDataFrame(sp, crs=CRS).to_file(os.path.join(mp, "N1P_F0020000.shp"), encoding="cp949")
    # ---------------- DEM (EPSG:5179, 30 m)
    import rasterio
    from rasterio.transform import from_origin
    from pyproj import Transformer
    T = Transformer.from_crs(5179, CRS, always_xy=True)
    Tinv = Transformer.from_crs(CRS, 5179, always_xy=True)
    ex, ey = Tinv.transform([CX - 1200, CX + 1200], [CY - 1200, CY + 1200])
    res = 30.0
    xs = np.arange(min(ex), max(ex), res); ys = np.arange(max(ey), min(ey), -res)
    X, Y = np.meshgrid(xs + res / 2, ys - res / 2)
    x5186, y5186 = T.transform(X, Y)
    Z = terrain(x5186, y5186).astype(np.float32)
    os.makedirs(os.path.join(root, "data", "dem"), exist_ok=True)
    with rasterio.open(os.path.join(root, "data", "dem", "dem_5179.tif"), "w", driver="GTiff",
                       height=Z.shape[0], width=Z.shape[1], count=1, dtype="float32", crs="EPSG:5179",
                       transform=from_origin(xs[0], ys[0], res, res), nodata=-9999) as ds:
        ds.write(Z, 1)
    # ---------------- 녹지(다공성 지면)
    gpd.GeoDataFrame(geometry=[box(CX + 120, CY + 60, CX + 220, CY + 200)], crs=CRS).to_file(
        os.path.join(root, "data", "green.gpkg"), driver="GPKG")
    # ---------------- 교통량
    with open(os.path.join(root, "traffic.csv"), "w", encoding="utf-8-sig") as f:
        f.write("key,key_type,road_type,DTV,M_day,M_night,p1_day,p2_day,p1_night,p2_night,v_pkw,v_lkw,surface,note\n")
        f.write("가로A,name,gemeinde,40000,,,,,,,50,50,ref,가상\n")
        f.write("가로B,name,gemeinde,30000,,,5,5,,,50,40,AC11,가상\n")
    with open(os.path.join(root, "config.yaml"), "w", encoding="utf-8") as f:
        f.write(f"""# 시험용 설정 (※ Claude(Anthropic의 AI)를 활용하여 제작하였음)
project: {{name: "가상 교차로(시험)", output_dir: "output"}}
crs: 5186
input:
  map: {{folders: ["data/map"]}}
  dem: {{path: "data/dem/dem_5179.tif", use_as: "auxiliary"}}
  traffic: {{path: "traffic.csv"}}
  ground: {{method: "general", default_G: 0.0, soft_polygons: "data/green.gpkg"}}
receivers:
  center: [{CX}, {CY}]
  grid: {{size: [300.0, 300.0], step: 10.0, heights: [4.0, 10.0]}}
  facade: {{radius: 90.0}}
propagation:
  max_dist_main: 250.0
""")
    print("생성 완료:", root)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "examples/synthetic")
