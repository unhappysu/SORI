# -*- coding: utf-8 -*-
"""
SoundPLAN 비교 도구(tools/compare_soundplan.py)·내보내기(tools/export_soundplan.py) 시험.
SoundPLAN 결과 대신 SORI 결과에 알려진 차이를 더한 가짜 파일(독일어판 형식: ; 구분, 소수 쉼표 / ESRI ASCII 격자)을
만들어, 도구가 그 차이를 그대로 되찾는지 확인함. 실제 SoundPLAN 파일 형식은 받은 뒤 다시 확인해야 함.
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import compare_soundplan as CS  # noqa: E402


@pytest.fixture
def sori_out(tmp_path):
    """SORI 결과 흉내: 격자 5 m 간격 20×10, 점 수음점 3개"""
    xs, ys = np.meshgrid(np.arange(1000.0, 1100.0, 5.0), np.arange(2000.0, 2050.0, 5.0))
    L = 70 - 0.1 * (ys - 2000) + 0.01 * (xs - 1000)
    pd.DataFrame({"x": xs.ravel(), "y": ys.ravel(), "z": 4.0, "h": 4.0, "LrT": L.ravel(), "LrN": L.ravel() - 4}) \
        .to_csv(tmp_path / "grid_result.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame({"point_id": ["P1", "P2", "Q"], "x": [1010.0, 1050.0, 1090.0], "y": [2010.0, 2020.0, 2040.0],
                  "h": [1.3, 1.3, 4.0], "LrT": [75.0, 72.5, 61.0], "LrN": [71.0, 68.5, 57.0]}) \
        .to_csv(tmp_path / "point_result.csv", index=False, encoding="utf-8-sig")
    return tmp_path


def test_points_by_name_german_format(sori_out, tmp_path):
    """이름으로 맞춤, ; 구분·소수 쉼표, 열 이름 LrD/LrN → Δ = SORI − SP = −0.5 / +0.2"""
    f = tmp_path / "sp_points.txt"
    f.write_text("Name;Höhe;LrD;LrN\nP1;1,3;75,5;70,8\nP2;1,3;73,0;68,3\nQ;4,0;61,5;56,8\n", encoding="latin-1")
    r = CS.main(["--sori", str(sori_out), "--sp-points", str(f), "--out", str(tmp_path / "c1"), "--no-plots"])
    assert r["points"]["day"]["n"] == 3
    assert r["points"]["day"]["mean_diff"] == pytest.approx(-0.5, abs=1e-9)
    assert r["points"]["night"]["mean_diff"] == pytest.approx(0.2, abs=1e-9)
    assert r["points"]["day"]["within_0.5dB_pct"] == 100.0


def test_points_by_coordinates_with_offset(sori_out, tmp_path):
    f = tmp_path / "sp.csv"
    pd.DataFrame({"X": [10.2, 50.0, 90.0], "Y": [10.0, 20.1, 40.0], "Lr,T": [74.0, 72.5, 62.0]}).to_csv(f, index=False)
    r = CS.main(["--sori", str(sori_out), "--sp-points", str(f), "--offset", "1000", "2000",
                 "--out", str(tmp_path / "c2"), "--no-plots"])
    M = pd.read_csv(tmp_path / "c2" / "matched_points.csv", encoding="utf-8-sig")
    assert list(M.point_id) == ["P1", "P2", "Q"]
    assert r["points"]["day"]["mean_diff"] == pytest.approx(0.0, abs=1e-9)
    assert r["points"]["day"]["max_abs"] == pytest.approx(1.0, abs=1e-9)


def test_grid_ascii_and_stats(sori_out, tmp_path):
    """ESRI ASCII(칸 모서리 기준) → 칸 중심이 SORI 격자점과 같도록. 값 = SORI + 1, 빈 칸 −9999"""
    G = pd.read_csv(sori_out / "grid_result.csv")
    Z = (G.pivot(index="y", columns="x", values="LrT").sort_index(ascending=False) + 1.0).to_numpy().copy()
    Z[0, 0] = -9999
    head = f"ncols {Z.shape[1]}\nnrows {Z.shape[0]}\nxllcorner 997.5\nyllcorner 1997.5\ncellsize 5\nNODATA_value -9999\n"
    f = tmp_path / "sp.asc"
    f.write_text(head + "\n".join(" ".join(f"{v:.3f}" for v in row) for row in Z))
    r = CS.main(["--sori", str(sori_out), "--sp-grid", str(f), "--out", str(tmp_path / "c3")])
    g = r["grid"]
    assert g["matched"] == len(G) - 1 and g["day"]["n"] == len(G) - 1
    assert g["day"]["mean_diff"] == pytest.approx(-1.0, abs=1e-6)
    assert g["day"]["sd_diff"] == pytest.approx(0.0, abs=1e-6)
    assert g["day"]["slope"] == pytest.approx(1.0, abs=1e-6)
    assert (tmp_path / "c3" / "차이지도_grid_day.png").exists()
    assert "격자 주간" in (tmp_path / "c3" / "비교요약.md").read_text(encoding="utf-8")
    assert json.loads((tmp_path / "c3" / "stats.json").read_text(encoding="utf-8"))["grid"]["day"]["n"] == len(G) - 1


def test_grid_xyz_semicolon_comma(sori_out, tmp_path):
    G = pd.read_csv(sori_out / "grid_result.csv").iloc[::3]
    lines = ["X;Y;Z;LrD;LrN"] + [f"{x:.2f};{y:.2f};4,0;{a + 0.3:.2f};{b - 0.3:.2f}".replace(".", ",")
                                 for x, y, a, b in zip(G.x, G.y, G.LrT, G.LrN)]
    f = tmp_path / "sp_grid.txt"
    f.write_text("\n".join(lines), encoding="utf-8")
    r = CS.main(["--sori", str(sori_out), "--sp-grid", str(f), "--out", str(tmp_path / "c4"), "--no-plots"])
    assert r["grid"]["day"]["n"] == len(G)
    assert r["grid"]["day"]["mean_diff"] == pytest.approx(-0.3, abs=1e-6)
    assert r["grid"]["night"]["mean_diff"] == pytest.approx(0.3, abs=1e-6)


def test_unrecognised_columns_message(sori_out, tmp_path):
    f = tmp_path / "bad.csv"
    pd.DataFrame({"foo": [1], "bar": [2]}).to_csv(f, index=False)
    with pytest.raises(SystemExit, match="day-col"):
        CS.main(["--sori", str(sori_out), "--sp-points", str(f), "--out", str(tmp_path / "c5")])


def test_export_on_reference_case(tmp_path):
    """합성 기준 사례(rls90 build·run) → 셰이프파일·조건표. 도로 속성이 traffic_used.csv 와 같은지"""
    import geopandas as gpd
    import reference_case as RC
    import export_soundplan as ES
    out = RC.run_case(tmp_path, method="rls90")
    dst = ES.main([str(tmp_path / "config.yaml"), "--out", str(tmp_path / "exp")])
    R = gpd.read_file(dst / "roads.shp")
    T = pd.read_csv(out / "traffic_used.csv", encoding="utf-8-sig")
    T = T[T.traffic_src != "excluded_minor"].reset_index(drop=True)   # 음원에서 뺀 골목길은 내보내지 않음
    assert len(R) == len(T)
    assert np.allclose(R.M_D, T.M_day) and np.allclose(R.p_D, T.p_day, atol=1e-3)
    E = gpd.read_file(dst / "emission_lines.shp")
    assert len(E) >= len(R)
    for f in ("buildings.shp", "signals.shp", "calc_area.shp", "terrain_points.csv", "계산조건.md"):
        assert (dst / f).exists()
    assert "RLS-90" in (dst / "계산조건.md").read_text(encoding="utf-8")
