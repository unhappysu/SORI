# -*- coding: utf-8 -*-
"""Qt 화면(sori_qt.py) 시험 — 화면 없이(offscreen) 기준 사례 기준값 폴더를 결과로 열어 트리·지도·클릭 확인.
PySide6 가 없으면 건너뜀(그때 앱은 tkinter 화면으로 실행됨).
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

EXP = Path(__file__).resolve().parent / "reference" / "synthetic" / "expected"


def test_open_results_and_click():
    from PySide6 import QtWidgets
    import sori_qt as Q
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = Q.MainWindow()
    w.open_results(EXP)
    G = w.R["grid"]
    assert w.cb_h.count() == G.h.nunique()
    assert w.map.ax is not None and w.map.ax.images            # 격자 결과가 그려짐
    assert w.m_facade.rowCount() == min(200, len(w.R["facade"]))
    Gh = G[G.h == w.cb_h.currentData()]
    r = Gh.iloc[len(Gh) // 2]
    w._on_map_click(float(r.x) + 0.1, float(r.y) - 0.1)       # 가장 가까운 격자점 값이 속성 표에 나옴
    vals = {w.props.item(i, 0).text(): w.props.item(i, 1).text() for i in range(w.props.rowCount())}
    assert vals["LrT dB"] == f"{r.LrT:.1f}"
    w.close()
    app.processEvents()


def test_building_click_facade_map_and_save(tmp_path):
    """격자 지도에서 건물 클릭 → 그 건물 벽면 지도(펼친 그림 칸 수 = 벽면 수음점 수), 펼친 그림 클릭 값, PNG·PDF 저장, 격자로 복귀"""
    from PySide6 import QtWidgets
    import reference_case as RC
    import sori_qt as Q
    out = RC.run_case(tmp_path / "case")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = Q.MainWindow()
    w.open_results(out)
    FR, B = w.R["facade"], w.R["buildings"]
    bid = int(FR.bid.value_counts().index[0])
    g = B[B.bid == bid].iloc[0].geometry.representative_point()
    w._on_map_click(g.x, g.y)
    assert w.view_bid == bid and w.act_back.isEnabled()
    n = int((FR.bid == bid).sum())
    assert len(w.map.ax_elev.collections[0].get_array()) == n
    vals = {w.props.item(i, 0).text(): w.props.item(i, 1).text() for i in range(w.props.rowCount())}
    assert vals["수음점"] == f"{n:,}" and vals["LrT 최대 dB"] == f"{FR[FR.bid == bid].LrT.max():.1f}"
    w._on_elev_click(0.01, 0.5)                                   # 시작점 바로 옆 1층 칸(첫 벽이 계산된 경우)
    for ext in ("png", "pdf"):
        p = w.save_map_to(tmp_path / f"facade.{ext}")
        assert p.exists() and p.stat().st_size > 1000
    w.show_grid()
    assert w.view_bid is None and not w.act_back.isEnabled() and w.map.ax.images
    assert w.save_map_to(tmp_path / "grid.png").exists()
    w.close()
    app.processEvents()


def test_remembered_output_must_match_config(tmp_path):
    """기억된 결과 폴더가 다른 설정(예제 등)의 결과면 쓰지 않고 설정 파일 옆 output 을 씀"""
    import json
    import sori_qt as Q
    cfg = tmp_path / "a" / "config.yaml"
    cfg.parent.mkdir()
    cfg.write_text("x: 1\n", encoding="utf-8")
    other = tmp_path / "b" / "output"
    other.mkdir(parents=True)
    (other / "run_conditions.json").write_text(json.dumps({"config_path": str(tmp_path / "b" / "config.yaml")}),
                                               encoding="utf-8")
    assert Q._out_for(cfg, str(other)) is None
    mine = tmp_path / "c_out"
    mine.mkdir()
    (mine / "run_conditions.json").write_text(json.dumps({"config_path": str(cfg)}), encoding="utf-8")
    assert Q._out_for(cfg, str(mine)) == str(mine)
    assert Q._out_for(cfg, str(cfg.parent / "output")) == str(cfg.parent / "output")   # 아직 계산 전
