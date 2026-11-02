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


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path, monkeypatch):
    """화면 시험이 사용자의 실제 설정(마지막 설정 파일·결과 폴더, settings.json)을 바꾸지 않게 임시 파일로"""
    import sori_gui as G
    monkeypatch.setattr(G, "settings_path", lambda: tmp_path / "settings.json")


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


def test_building_click_3d_facade_and_save(tmp_path):
    """격자 지도에서 건물 클릭 → 3D 파사드 소음지도(그림 파일 facade_3d 와 같은 그리기 함수), PNG·PDF 저장, 격자로 복귀"""
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
    assert w.view == "facade" and w.view_bid == bid and w.act_back.isEnabled()
    assert w.map.ax3d is not None and w.map.ax is None                     # 3D 축(클릭 신호 없음, 회전용)
    n = int((FR.bid == bid).sum())
    vals = {w.props.item(i, 0).text(): w.props.item(i, 1).text() for i in range(w.props.rowCount())}
    assert vals["수음점"] == f"{n:,}" and vals["LrT 최대 dB"] == f"{FR[FR.bid == bid].LrT.max():.1f}"
    assert w.m_facade.rowCount() == n                                       # 아래 '벽면' 표 = 이 건물 층별 값
    for ext in ("png", "pdf"):
        p = w.save_map_to(tmp_path / f"facade3d.{ext}")
        assert p.exists() and p.stat().st_size > 1000
    w.show_grid()
    assert w.view == "grid" and not w.act_back.isEnabled() and w.map.ax.images
    assert w.save_map_to(tmp_path / "grid.png").exists()
    w.close()
    app.processEvents()


def test_section_line_compute_and_show(tmp_path):
    """단면 선 긋기(두 점 클릭) → 단면 계산(Worker) → 단면 지도 표시·저장, 결과 파일과 다시 열기"""
    import time
    from PySide6 import QtWidgets
    import reference_case as RC
    import sori_qt as Q
    from sori import config as C
    out = RC.run_case(tmp_path / "case")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = Q.MainWindow()
    w.open_config(tmp_path / "case" / "config.yaml", out)
    assert w.map.hq is not None and w.map.ax.collections                    # 설정+결과 → 보고서 수준 지도(등음선 채움)
    assert w.save_map_to(tmp_path / "grid_hq.pdf").stat().st_size > 1000
    cx, cy = C.center(w.cfg)
    w.act_sec.setChecked(True)
    w._toggle_section(True)
    w._on_map_click(cx - 100.0, cy + 40.0)
    w._on_map_click(cx + 100.0, cy + 40.0)
    t0 = time.time()
    while w.worker.isRunning() and time.time() - t0 < 300:
        app.processEvents()
        time.sleep(0.05)
    for _ in range(20):
        app.processEvents()
    assert w.view == "section" and not w.act_sec.isChecked()
    for fn in ("section_result.csv", "section_info.json"):
        assert (out / fn).exists()
    assert w.save_map_to(tmp_path / "section.png").stat().st_size > 1000
    w.open_results(out)                                                     # 다시 열면 '마지막 단면 보기' 가능
    assert w.act_last_sec.isEnabled()
    w.show_section()
    assert w.view == "section"
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


def test_method_select_runs_and_separates_folders(tmp_path):
    """계산 실행 탭의 전파 방법 선택: 설정과 다른 방법이면 결과 폴더 output_<방법>, 계산은 그 방법으로,
    설정 파일은 그대로이고 config_used.yaml·run_conditions.json 에 기록. 결과를 다시 열면 선택 칸이 결과 방법으로"""
    import json
    import shutil
    from PySide6 import QtWidgets
    import reference_case as RC
    import sori_gui as G
    import sori_qt as Q
    from sori import config as C
    work = tmp_path / "case"
    shutil.copytree(RC.INPUT, work)
    src = (work / "config.yaml").read_text(encoding="utf-8")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = Q.MainWindow()
    w.open_config(work / "config.yaml")
    assert w.cb_method.currentData() == "iso9613_2" and Path(w.ed_out.text()) == work / "output"
    w.cb_method.setCurrentIndex(w.cb_method.findData("rls19"))
    out = Path(w.ed_out.text())
    assert out == work / "output_rls19" and w.cfg["propagation_method"] == "rls19"
    G.run_job(work / "config.yaml", out, ("grid",), do_plots=False, method=w.cb_method.currentData())
    ri = json.loads((out / "run_info.json").read_text(encoding="utf-8"))
    assert ri["outputs"]["grid"] == "rls19"
    rc = json.loads((out / "run_conditions.json").read_text(encoding="utf-8"))
    assert rc["propagation_method"] == "rls19" and rc["propagation_method_override"]
    assert C.load(out / "config_used.yaml")["propagation_method"] == "rls19"
    assert (work / "config.yaml").read_text(encoding="utf-8") == src                    # 설정 파일은 그대로
    w.cb_method.setCurrentIndex(w.cb_method.findData("iso9613_2"))                     # 설정 파일 방법 → output
    assert Path(w.ed_out.text()) == work / "output" and "grid" not in w.R
    w.open_results(out)                                                                 # 결과 방법으로 맞춤
    assert w.cb_method.currentData() == "rls19" and w.cfg["propagation_method"] == "rls19"
    assert w.map.hq is not None                                                         # 보고서 지도(방법 일치)
    w.close()
    app.processEvents()
