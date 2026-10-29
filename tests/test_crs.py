# -*- coding: utf-8 -*-
"""좌표 변환(EPSG) 테스트 — PROJ 데이터가 빠지거나 바뀌어 좌표가 조용히 틀어지는 문제를 잡기 위함.
1) 투영 원점: EPSG 정의의 원점 경위도는 가산값(false easting/northing) 좌표가 되어야 함(독립 기준)
2) 기준 사례 중심점: 2026-10-29 pyproj 3.8.0 / PROJ 9.8.1 로 계산해 고정한 값(회귀 기준)
3) pyproj(SORI 좌표 변환)와 rasterio(GDAL, DEM 재투영)의 PROJ 가 같은 답을 내는지
허용 오차 1 mm — 프로젝트 임의 설정.
※ Claude(Anthropic의 AI)를 활용하여 제작하였음."""
from pathlib import Path

import pytest
from pyproj import Transformer

TOL = 0.001          # m, 프로젝트 임의 설정
TOL_DEG = 1e-8       # 도(약 1 mm)

CX, CY = 205108.0, 553110.0   # 기준 사례(가상 교차로) 계산 중심, EPSG:5186


def tr(a, b, x, y):
    return Transformer.from_crs(a, b, always_xy=True).transform(x, y)


def test_proj_data_dir_present():
    import pyproj
    d = Path(pyproj.datadir.get_data_dir())
    assert (d / "proj.db").is_file(), d


@pytest.mark.parametrize("epsg, lon, lat, x, y", [
    (5186, 127.0, 38.0, 200000.0, 600000.0),     # Korea 2000 / Central Belt 2010 (수치지도 1:1,000)
    (5179, 127.5, 38.0, 1000000.0, 2000000.0),   # Korea 2000 / Unified CS (DEM 등)
])
def test_projection_origin(epsg, lon, lat, x, y):
    gx, gy = tr(4326, epsg, lon, lat)
    assert gx == pytest.approx(x, abs=TOL) and gy == pytest.approx(y, abs=TOL)


def test_reference_center_to_wgs84():
    lon, lat = tr(5186, 4326, CX, CY)
    assert lon == pytest.approx(127.05782601294123, abs=TOL_DEG)
    assert lat == pytest.approx(37.57752480127847, abs=TOL_DEG)


def test_reference_center_5186_to_5179_and_back():
    x, y = tr(5186, 5179, CX, CY)
    assert x == pytest.approx(960956.551157946, abs=TOL)
    assert y == pytest.approx(1953219.0614228551, abs=TOL)
    bx, by = tr(5179, 5186, x, y)
    assert bx == pytest.approx(CX, abs=TOL) and by == pytest.approx(CY, abs=TOL)


def test_rasterio_matches_pyproj():
    from rasterio.crs import CRS
    from rasterio.warp import transform
    xs, ys = transform(CRS.from_epsg(5186), CRS.from_epsg(5179), [CX], [CY])
    x, y = tr(5186, 5179, CX, CY)
    assert xs[0] == pytest.approx(x, abs=TOL) and ys[0] == pytest.approx(y, abs=TOL)
