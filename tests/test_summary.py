# -*- coding: utf-8 -*-
"""결과 요약·입력 도우미(sori/summary.py), 두 결과의 차이(sori/compare.py).
※ Claude(Anthropic의 AI)를 활용하여 제작하였음."""
import numpy as np
import pandas as pd
import pytest

from sori import compare, summary as SM


def _fr():
    return pd.DataFrame(dict(bid=[1, 1, 1, 2, 2, 3], fid=[0, 0, 1, 2, 2, 3], piece=[0, 0, 0, 0, 0, 0],
                             floor=[1, 2, 1, 1, 2, 1], LrT=[60.0, 69.5, 66.0, 72.0, 74.0, 50.0],
                             LrN=[52.0, 57.0, 59.0, 61.0, 62.5, 41.0]))


def _bld():
    import geopandas as gpd
    from shapely.geometry import box
    return gpd.GeoDataFrame(dict(bid=[1, 2, 3, 4], bname=["가동", None, "", "x"], kind=["주택", "상가", "주택", "주택"],
                                 floors=[2, 2, 1, 1]),
                            geometry=[box(0, 0, 10, 10), box(20, 0, 30, 10), box(40, 0, 50, 10), box(60, 0, 70, 10)])


def test_building_table_and_zone_limits():
    T = SM.building_table(_fr(), _bld(), zones=pd.Series({1: "A", 2: "B", 3: "A"}))
    assert list(T.bid) == [2, 1, 3]                                    # LrT 최고 내림차순, 결과 없는 건물 4 는 없음
    r1 = T[T.bid == 1].iloc[0]
    assert (r1.LrT_max, r1.LrN_max, r1.최고층, r1.수음점, r1.이름) == (69.5, 59.0, 2, 3, "가동")
    assert (r1.기준_주, r1.기준_야) == (68.0, 58.0) and r1.초과_주 == 1.5 and r1.초과_야 == 1.0 and r1.초과 == "초과"
    r2 = T[T.bid == 2].iloc[0]                                         # 나 지역 73/63: 주간만 초과
    assert (r2.기준_주, r2.기준_야) == (73.0, 63.0) and r2.초과_주 == 1.0 and r2.초과_야 == -0.5 and r2.초과 == "초과"
    assert T[T.bid == 3].iloc[0].초과 == ""
    T2 = SM.building_table(_fr(), _bld(), limits={"LrT": 75, "LrN": 65})
    assert (T2.초과 == "").all() and set(T2.기준_주) == {75.0}
    assert len(SM.building_table(None, _bld())) == 0


def test_zone_code_words():
    assert SM.zone_code("제2종일반주거지역") == "A" and SM.zone_code("자연녹지지역") == "A"
    assert SM.zone_code("일반상업지역") == "B" and SM.zone_code("준공업지역") == "B" and SM.zone_code("미고시지역") == "B"
    assert SM.zone_code("b") == "B" and SM.zone_code("나") == "B" and SM.zone_code("") == "A" and SM.zone_code(None, "B") == "B"


def test_building_zones_from_polygons(tmp_path):
    import geopandas as gpd
    from shapely.geometry import box
    Z = gpd.GeoDataFrame({"용도": ["일반상업지역"]}, geometry=[box(15, -5, 35, 15)], crs=5186)
    Z.to_file(tmp_path / "zones.gpkg", driver="GPKG")
    cfg = {"crs": 5186, "input": {"zones": {"path": str(tmp_path / "zones.gpkg"), "field": "용도", "default": "A"}}}
    z = SM.building_zones(cfg, _bld().set_crs(5186))
    assert dict(z) == {1: "A", 2: "B", 3: "A", 4: "A"}
    cfg["input"]["zones"]["field"] = "없는열"
    with pytest.raises(ValueError, match="없는열"):
        SM.building_zones(cfg, _bld().set_crs(5186))
    assert set(SM.building_zones({"input": {}}, _bld())) == {"A"}


def test_exposure_classes():
    T = SM.building_table(_fr(), _bld())
    E = dict(SM.exposure(T, "LrT"))
    assert E["65 – 70"] == 1 and E["70 – 75"] == 1 and E["45 – 50"] == 1 and sum(E.values()) == 3
    assert sum(n for _l, n in SM.exposure(T.iloc[0:0])) == 0


def test_combine_components_energy_sum():
    G = pd.DataFrame(dict(LrT=[63.0, 60.0], LrT_direct=[60.0, 60.0], LrT_refl1=[60.0, np.nan], LrT_refl2=[np.nan, np.nan]))
    assert SM.available_components(G) == ["direct", "refl1"]
    v = SM.combine(G, ["direct", "refl1"])
    assert v[0] == pytest.approx(63.0103, abs=1e-3) and v[1] == pytest.approx(60.0)
    assert np.isnan(SM.combine(G, ["refl2"])).all()


def test_kr12_to_rls19_groups():
    r = SM.kr_to_rls19({1: 800, 2: 40, 3: 60, 4: 50, 8: 30, "moto": 20}, hours=1.0)
    assert r["M"] == 1000 and r["p1"] == pytest.approx(9.0) and r["p2"] == pytest.approx(5.0)
    assert SM.kr_to_rls19({1: 100}, hours=0.25)["M"] == 400
    assert SM.kr_to_rls19({})["M"] == 0.0
    with pytest.raises(ValueError):
        SM.kr_to_rls19({13: 1})


def test_estimate_points():
    cfg = {"receivers": {"center": [0, 0], "grid": {"enabled": True, "bbox": None, "size": [100.0, 50.0], "step": 5.0,
                                                    "heights": [4.0]}, "facade": {"enabled": True}}}
    e = SM.estimate_points(cfg, extra_heights=[1.5])
    assert e["grid"] == 20 * 10 * 2 and e["facade"] is None and not e["grid_exact"]
    e = SM.estimate_points(cfg, build_info={"grid_receivers_per_height": 150, "facade_receivers": 77})
    assert e["grid"] == 150 and e["facade"] == 77 and e["grid_exact"]
    assert SM.estimate_time(1000, 50.0) == 20.0 and SM.estimate_time(1000, None) is None


def test_diff_tables_and_stats(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    xs = np.arange(0.0, 50.0, 5.0)
    G = pd.DataFrame(dict(x=np.tile(xs, 2), y=0.1 + 0.2, z=1.0, h=np.repeat([1.5, 4.0], len(xs)), LrT=60.0, LrN=50.0))
    G.to_csv(a / "grid_result.csv", index=False)
    G2 = G.assign(LrT=G.LrT - np.tile(np.linspace(0, 9, len(xs)), 2), LrN=G.LrN + 2.0)
    G2 = G2[G2.x < 45.0]                                               # 한쪽에만 있는 점은 비교에서 빠짐
    G2.to_csv(b / "grid_result.csv", index=False)
    D = compare.diff_grid(a, b)
    assert len(D) == 18 and sorted(D.h.unique()) == [1.5, 4.0]
    assert D.dLrT.min() == pytest.approx(-8.0) and (D.dLrN == 2.0).all()
    s = compare.stats(D[D.h == 4.0], "LrT")
    assert s["n"] == 9 and s["lower_3dB"] == 5 and s["higher_1dB"] == 0 and s["min"] == -8.0
    info = compare.write(a, b)
    assert (b / "diff_grid.csv").is_file() and (b / "diff_info.json").is_file() and "h1.5" in info["grid"]
    assert not (b / "diff_facade.csv").exists()
    assert any("격자 4 m LrT" in t for t in compare.summary_lines(info, "LrT"))
    assert compare.diff_grid(a, tmp_path / "none") is None


def test_measured_compare_long_and_wide(tmp_path):
    P = pd.DataFrame(dict(point_id=["P1", "P2", "P3"], x=[1.0, 2.0, 3.0], y=[0.0, 0.0, 0.0], LrT=[70.0, 65.0, np.nan],
                          LrN=[62.0, 57.0, np.nan]))
    f = tmp_path / "m.csv"
    f.write_text("point_id,period,Leq\nP1,day,66\nP1,day,68\nP1,night,60\nP2,주간,64\nP3,day,50\nP9,day,55\n",
                 encoding="utf-8")
    M = SM.read_measured(f)
    p1 = M[(M.point_id == "P1") & (M.key == "LrT")].meas.iloc[0]
    assert p1 == pytest.approx(10 * np.log10((10 ** 6.6 + 10 ** 6.8) / 2))      # 같은 지점·시간대는 에너지 평균
    D = SM.measured_compare(P, M)
    r = D[(D.point_id == "P2")].iloc[0]
    assert (r["key"], r["meas"], r["calc"], r["diff"]) == ("LrT", 64.0, 65.0, 1.0)
    assert D[(D.point_id == "P1") & (D.key == "LrN")]["diff"].iloc[0] == 2.0
    assert np.isnan(D[D.point_id == "P3"]["diff"].iloc[0]) and np.isnan(D[D.point_id == "P9"].calc.iloc[0])
    f.write_text("point_id,LrT_meas\nP1,69\n", encoding="utf-8")
    assert SM.measured_compare(P, SM.read_measured(f))["diff"].tolist() == [1.0]
    f.write_text("point_id,foo\nP1,1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        SM.read_measured(f)


def test_floor_heights():
    assert SM.floor_heights(3) == [2.8, 5.8, 8.8] and SM.floor_heights(0) == []
