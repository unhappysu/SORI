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
    w.act_split.setChecked(False)                     # 한 창 보기(격자 ↔ 3D 바꿔 보기)
    w._toggle_split(False)
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


def test_home_screen_and_tasks(tmp_path):
    """홈(2026-11-14): 작업 단추 셋 — 데이터 입력 / 입력 지도 확인(수치지도만, 소음 없음) / 지도 확인 · 계산(계산 실행 +
    격자 지도, 건물 클릭 → 3D 파사드 나란히). 입력 → 계산 → 결과 단계 줄은 없음"""
    from PySide6 import QtWidgets
    import reference_case as RC
    import sori_qt as Q
    out = RC.run_case(tmp_path / "case")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = Q.MainWindow()
    assert w.stack.currentIndex() == 0 and w.tb_view.isHidden()
    assert set(w.home.task_buttons) == {"input", "map", "grid"}
    assert not any(b.isEnabled() for b in w.home.task_buttons.values()) and not w.home.recent_actions
    assert not hasattr(w, "btn_go_run") and not hasattr(w, "btn_go_res")
    w.open_config(tmp_path / "case" / "config.yaml", out)
    assert len(w.home.recent_actions) == 1 and all(b.isEnabled() for b in w.home.task_buttons.values())
    w.home.task_buttons["input"].click()                                   # 데이터 입력
    assert w.stack.currentIndex() == 2
    w.show_home()
    w.home.task_buttons["map"].click()                                     # 입력 지도 확인: 소음 없음
    assert w.stack.currentIndex() == 1 and w.map_mode == "input" and w.lbl_mode.text() == "입력 지도 확인"
    assert w.map.ax is not None and not w.map.ax.images and w._grid_now is None
    assert not w.act_split.isVisible() and not w.cb_h.isVisible()          # 결과 단추는 숨김
    assert not w.map3d.isVisibleTo(w)                                      # 3D 창 없이 지도만
    from matplotlib.contour import QuadContourSet
    assert not any(isinstance(c, QuadContourSet) for c in w.map.ax.collections)
    B = w._input_layers()["buildings"]
    g = B.geometry.iloc[0].representative_point()
    w._on_map_click(g.x, g.y)                                              # 건물 클릭 → 수치지도 속성(3D 아님)
    assert w.props_title.text() == "건물(수치지도)" and w.view == "grid" and w.facade_bid is None
    w.show_home()
    w.act_split.setChecked(False); w._toggle_split(False)
    w.home.task_buttons["grid"].click()                                    # 지도 확인 · 계산
    assert w.map_mode == "result" and w.view == "grid" and w.act_split.isChecked()    # 나란히 보기(파사드 함께)
    assert w.map3d.isVisibleTo(w)
    assert w._grid_now is not None and w.docks["계산·결과 표"].isVisible() is not None
    FR, Bb = w.R["facade"], w.R["buildings"]
    bid = int(FR.bid.value_counts().index[0])
    p = Bb[Bb.bid == bid].iloc[0].geometry.representative_point()
    w._on_map_click(p.x, p.y)
    assert w.facade_bid == bid and w.map3d.ax3d is not None                # 건물 클릭 → 오른쪽 3D 파사드
    w.enter_task("section")
    assert w.act_sec.isChecked()
    w.show_home()
    assert w.stack.currentIndex() == 0 and w.tb_view.isHidden()
    w.close()
    app.processEvents()


def test_input_editor_edit_and_save(tmp_path, monkeypatch):
    """입력값 창: 설정 파일 값이 칸에 채워짐 → 고쳐 저장하면 config.yaml 에 반영(원본 .bak), 틀린 값은 저장 안 함"""
    import shutil
    import yaml
    from PySide6 import QtWidgets
    import reference_case as RC
    import sori_qt as Q
    work = tmp_path / "case"
    shutil.copytree(RC.INPUT, work)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = Q.MainWindow()
    w.open_config(work / "config.yaml")
    E = w.editor
    assert E.isEnabled() and E.values()["receivers.grid.step"] == w.cfg["receivers"]["grid"]["step"]
    w.home.btn_input.click()                                               # 입력값 전용 화면(지도·Dock 없음)
    assert w.stack.currentIndex() == 2 and w.tb_view.isHidden()
    E.w["receivers.grid.step"][0].setText("7.5")
    E.w["receivers.grid.heights"][0].setText("1.5, 4")
    w._start_pick_center()                                                 # 지도 화면으로 갔다가
    assert w.stack.currentIndex() == 1 and not w.tb_view.isHidden()
    w._on_map_click(1234.5, 6789.0)                                        # 클릭하면 중심 넣고 입력값 화면으로
    assert E.values()["receivers.center"] == [1234.5, 6789.0] and w.stack.currentIndex() == 2
    E._preview()                                                           # 범위 보기 → 지도 화면
    assert w.stack.currentIndex() == 1 and w._prev_box[:2] == (1234.5, 6789.0)
    w.show_input()
    assert w.save_current()                                                # Cmd/Ctrl+S = 입력값 저장
    raw = yaml.safe_load((work / "config.yaml").read_text(encoding="utf-8"))
    assert raw["receivers"]["grid"]["step"] == 7.5 and raw["receivers"]["grid"]["heights"] == [1.5, 4.0]
    assert raw["receivers"]["center"] == [1234.5, 6789.0]
    assert (work / "config.yaml.bak").exists()
    assert w.cfg["receivers"]["grid"]["step"] == 7.5 and w.stack.currentIndex() == 2   # 저장 뒤 다시 읽고 입력값 화면 유지
    w.enter_task("map")                                                    # 입력 지도 확인(지도 화면)
    assert w.stack.currentIndex() == 1 and w.map.ax is not None
    E.w["propagation.reflection.rho"][0].setText("1.5")                    # ISO 9613-2: 0.2 < ρ ≤ 1 → 저장 거부
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *a, **k: None)
    assert not E.save()
    assert yaml.safe_load((work / "config.yaml").read_text(encoding="utf-8"))["propagation"]["reflection"]["rho"] != 1.5
    w.close()
    app.processEvents()


def test_grid_and_facade_side_by_side(tmp_path):
    """나란히 보기(기본): 건물 클릭 → 왼쪽 격자 그대로 + 빨간 테두리, 오른쪽 3D 파사드. 시간대 바꾸면 둘 다 바뀜"""
    from PySide6 import QtWidgets
    import reference_case as RC
    import sori_qt as Q
    out = RC.run_case(tmp_path / "case")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = Q.MainWindow()
    w.open_results(out)
    assert w.act_split.isChecked()
    FR, B = w.R["facade"], w.R["buildings"]
    bid = int(FR.bid.value_counts().index[0])
    g = B[B.bid == bid].iloc[0].geometry.representative_point()
    w._on_map_click(g.x, g.y)
    assert w.view == "grid" and w.facade_bid == bid and not w.act_back.isEnabled()
    assert w.map.ax is not None and w.map.ax.images                       # 왼쪽 격자 유지
    assert w.map3d.ax3d is not None                                        # 오른쪽 3D
    t = w.map3d.title_text
    assert f"건물 {bid}" in t and "LrT" in t
    w.cb_key.setCurrentIndex(w.cb_key.findData("LrN"))                     # 시간대 → 양쪽 다시 그림
    assert "LrN" in w.map3d.title_text and w.map.ax.images
    assert w.save_map_to(tmp_path / "side.png", w.map3d).exists()
    w.act_split.setChecked(False)                                          # 끄면 오른쪽 숨김
    w._toggle_split(False)
    assert w.facade_bid is None and w.map3d.isHidden()
    w.close()
    app.processEvents()


def test_facade_values_on_grid_map(tmp_path):
    """격자 지도 위에 벽면 값 띠(층별 최댓값)가 겹쳐 그려지고, 끄면 사라지고, 시간대에 따라 값이 바뀜"""
    from PySide6 import QtWidgets
    import reference_case as RC
    import sori_qt as Q
    from sori.maps_hq import facade_segments
    out = RC.run_case(tmp_path / "case")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = Q.MainWindow()
    w.open_results(out)
    assert w.act_fval.isEnabled() and not w.act_fval.isChecked()     # 기본 끔(2026-11-12)

    def overlay():
        return [c for c in w.map.ax.collections if c.get_gid() == "facade_overlay"]

    assert not overlay()
    w.act_fval.setChecked(True)
    w.redraw()
    lc = overlay()
    assert len(lc) == 1
    W = facade_segments(w.R["facade"], w.R["facades"], "LrT")
    assert len(lc[0].get_segments()) == len(W) > 0
    FR = w.R["facade"]                                                 # 띠 값 = 그 벽면 구간 층별 최댓값
    g = FR.groupby(["fid", "piece"]).LrT.max()
    r = W.iloc[0]
    assert abs(r.L - g.loc[(r.fid, r.piece)]) < 1e-9
    bid = int(FR.bid.value_counts().index[0])                         # 띠가 있어도 건물 클릭 → 3D
    p = w.R["buildings"].set_index("bid").geometry.loc[bid].representative_point()
    w._on_map_click(p.x, p.y)
    assert w.facade_bid == bid and len(overlay()) == 1
    w.act_fval.setChecked(False)
    w.redraw()
    assert not overlay()
    w.act_fval.setChecked(True)
    w.cb_key.setCurrentIndex(w.cb_key.findData("LrN"))
    assert len(overlay()) == 1
    w.close()
    app.processEvents()


def test_toolbar_buttons_toggle_split_and_section(tmp_path):
    """도구 줄 단추를 실제로 누르는 경로(QAction.trigger — 인자 없이 부름)로 나란히 보기·단면 선 긋기가 켜지고 꺼짐.
    2026-11-11: 함수가 인자를 꼭 받아 단추를 누르면 TypeError 로 오른쪽 3D 창이 켜지지 않던 오류"""
    from PySide6 import QtWidgets
    import reference_case as RC
    import sori_qt as Q
    out = RC.run_case(tmp_path / "case")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = Q.MainWindow()
    w.resize(1600, 1000)
    w.show()
    w.open_results(out)
    w.enter_task("grid")                                            # 홈 → 격자 소음지도(작업 화면)
    app.processEvents()
    assert w.act_split.isChecked() and w.map3d.isVisible()
    w.act_split.trigger()                                           # 끔
    app.processEvents()
    assert not w.act_split.isChecked() and not w.map3d.isVisible()
    w.act_split.trigger()                                           # 다시 켬
    app.processEvents()
    assert w.act_split.isChecked() and w.map3d.isVisible() and w.map3d.width() > 100
    assert w.map.width() > 3 * w.map3d.width()                      # 건물을 고르기 전: 격자 지도를 크게
    FR, B = w.R["facade"], w.R["buildings"]
    bid = int(FR.bid.value_counts().index[0])
    g = B[B.bid == bid].iloc[0].geometry.representative_point()
    w._on_map_click(g.x, g.y)
    assert w.facade_bid == bid and w.map3d.ax3d is not None and w.view == "grid"
    app.processEvents()
    assert w.map3d.width() > 0.4 * (w.map.width() + w.map3d.width())     # 건물을 고르면 반반
    w.act_sec.trigger()
    assert w.act_sec.isChecked()
    w.act_sec.trigger()
    assert not w.act_sec.isChecked()
    w.close()
    app.processEvents()


def test_split_view_draws_map_only_and_save_keeps_report(tmp_path):
    """화면 격자 지도는 나란히·한 창 모두 지도만 크게(정보란 없음, draw_grid_compact), 지도 저장만 보고서 틀(draw_grid).
    결과가 있으면 지도 확인에 들어갈 때 아래 창을 펴지 않음(2026-11-14 지도 크기)"""
    from PySide6 import QtWidgets
    import reference_case as RC
    import sori_qt as Q
    out = RC.run_case(tmp_path / "case")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = Q.MainWindow()
    w.resize(1600, 1000)
    w.show()
    w.open_config(tmp_path / "case" / "config.yaml", out)
    w.enter_task("grid")
    app.processEvents()

    def titles():
        return " ".join(t.get_text() for t in w.map.fig.texts)
    assert w.act_split.isChecked() and w.map.hq is not None
    assert not w.docks["계산·결과 표"].isVisible()                                    # 결과 있음 → 아래 창 접음
    assert titles().startswith("격자 소음지도 ·") and "SORI Map" not in titles()      # 지도만
    png = w.save_map_to(tmp_path / "saved.png")
    assert png.exists()
    assert titles().startswith("격자 소음지도 ·")                                    # 저장 뒤 화면은 다시 지도만
    w.act_split.trigger()                                                           # 한 창 보기
    app.processEvents()
    assert titles().startswith("격자 소음지도 ·") and "SORI Map" not in titles()      # 한 창도 지도만
    assert w.map.fig._original_dpi == w.map._base_dpi
    saved = []
    orig = w.map.fig.savefig
    w.map.fig.savefig = lambda *a, **k: (saved.append(" ".join(t.get_text() for t in w.map.fig.texts)), orig(*a, **k))
    w.save_map_to(tmp_path / "saved1.png")
    assert "SORI Map" in saved[0] and titles().startswith("격자 소음지도 ·")          # 저장만 보고서 틀
    w.close()
    app.processEvents()


def test_section_line_compute_and_show(tmp_path, monkeypatch):
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
    asked = []

    def pick(parent, title, label, items, cur, editable):                  # 간격 고르기: 자동·2·1·0.5 m + 점 수
        asked.append(items)
        return items[1], True                                                # 2 m
    monkeypatch.setattr(QtWidgets.QInputDialog, "getItem", pick)
    w.act_split.setChecked(True); w._toggle_split(True)
    w.act_sec.setChecked(True)
    w._toggle_section(True)
    w._on_map_click(cx - 100.0, cy + 40.0)
    w._on_map_click(cx + 100.0, cy + 40.0)
    assert len(asked[0]) == 4 and asked[0][0].startswith("자동") and "점" in asked[0][1]
    t0 = time.time()
    while w.worker.isRunning() and time.time() - t0 < 300:
        app.processEvents()
        time.sleep(0.05)
    for _ in range(20):
        app.processEvents()
    D, info = w.R["section"]
    assert info["ds_m"] == info["dz_m"] == 2.0 and w._sec_rate > 0           # 고른 간격으로 계산
    # 나란히 보기: 오른쪽 창에 단면(격자와 같은 색표, 등음선), 왼쪽 격자 지도에 A–B 선
    assert w.view == "grid" and w._section_side and not w.act_sec.isChecked()
    assert w.map3d.ax_sec is not None and w.map3d.ax_sec.collections
    assert w.map3d.title_text.startswith("단면 · A→B")
    assert "section_line" in {l.get_gid() for l in w.map.ax.lines}
    for fn in ("section_result.csv", "section_info.json"):
        assert (out / fn).exists()
    assert w.save_map_to(tmp_path / "section.png", w.map3d).stat().st_size > 1000
    Bb = w.R["buildings"]                                                    # 건물을 누르면 오른쪽은 3D, A–B 선 지움
    bid = int(w.R["facade"].bid.value_counts().index[0])
    p = Bb[Bb.bid == bid].iloc[0].geometry.representative_point()
    w._on_map_click(p.x, p.y)
    assert not w._section_side and w.map3d.ax3d is not None
    assert "section_line" not in {l.get_gid() for l in w.map.ax.lines}
    w.act_split.setChecked(False); w._toggle_split(False)                   # 한 창 보기: 평면 + 단면
    w.open_results(out)                                                     # 다시 열면 '마지막 단면 보기' 가능
    assert w.act_last_sec.isEnabled()
    w.show_section()
    assert w.view == "section" and w.map.ax_sec is not None
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


def test_load_jeonnong_without_and_with_map(tmp_path):
    """전농사거리 예제: 수치지도 없으면 안내 후 False(설정·교통량은 복사됨), zip 을 넣으면 풀고 설정을 엶"""
    import zipfile
    from PySide6 import QtWidgets
    import sori_qt as Q
    from sori import config as C
    from sori.inputs import read_csv_any
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = Q.MainWindow()
    dst = tmp_path / "전농사거리"
    assert w.load_jeonnong(dst, quiet=True) is False
    for f in ("config.yaml", "traffic.csv", "points.csv", "README.md", "traffic/A_현장계수_50kmh.csv"):
        assert (dst / f).exists(), f
    cfg = C.load(dst / "config.yaml")
    assert cfg["input"]["map"]["folders"] == [str((dst / "data" / "map").resolve())]
    assert len(read_csv_any(dst / "traffic.csv")) > 0
    # 국토정보플랫폼 zip 처럼 한글 폴더명(cp949, UTF-8 표시 없음) 안에 SHP 가 든 zip
    z = dst / "data" / "map" / "도엽.zip"
    # zipfile 은 ASCII 가 아닌 이름에 UTF-8 표시를 붙이므로, 같은 길이(8바이트) ASCII 이름으로 만든 뒤 바이트만 바꿈
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("(B010)XXXXXXXX_377051742/N1A_B0010000.shp", b"shp")
    z.write_bytes(z.read_bytes().replace(b"XXXXXXXX", "수치지도".encode("cp949")))
    assert w.load_jeonnong(dst, quiet=True) is True
    assert (dst / "data" / "map" / "도엽" / "(B010)수치지도_377051742" / "N1A_B0010000.shp").exists()
    assert w.cfg_path == (dst / "config.yaml").resolve()
    w.close()
    app.processEvents()


def test_traffic_editor_edit_and_save(tmp_path, monkeypatch):
    """교통량 탭: CSV 가 표로 채워지고 LW′ 가 rls19.LW_prime 과 같음 → 고쳐 저장(원본 .bak, 다른 열 보존),
    틀린 값은 빨간 칸·저장 거부"""
    import shutil
    from PySide6 import QtWidgets
    import reference_case as RC
    import sori_qt as Q
    from sori import rls19
    from sori.inputs import read_csv_any
    work = tmp_path / "case"
    shutil.copytree(RC.INPUT, work)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = Q.MainWindow()
    w.open_config(work / "config.yaml")
    T = w.traffic
    path = Path(w.cfg["input"]["traffic"]["path"])
    before = read_csv_any(path)
    assert T.isEnabled() and T.tbl.rowCount() == len(before)
    col = {c: j for j, (c, *_) in enumerate(Q.TRAFFIC_VIEW)}
    lw_d = len(Q.TRAFFIC_VIEW)
    T.tbl.item(0, col["M_day"]).setText("1200")                          # 주간 M 을 고치면 LW′ 즉시 다시 계산
    T.tbl.item(0, col["v_pkw"]).setText("40")
    rec = T._rec(0)
    exp = Q.lw_prime_row(rec, w.cfg["input"]["traffic"])[0]
    assert T.tbl.item(0, lw_d).text() == f"{exp:.1f}"
    t2 = rls19.TAB2[rec["road_type"] or w.cfg["input"]["traffic"]["default_road_type"]]["day"]
    p1 = float(rec["p1_day"]) if rec["p1_day"] else t2[1]
    p2 = float(rec["p2_day"]) if rec["p2_day"] else t2[2]
    vl = float(rec["v_lkw"]) if rec["v_lkw"] else 40.0
    assert abs(exp - rls19.LW_prime(1200, p1, p2, 40, vl, rec["surface"] or "ref", float(rec["grade"] or 0))) < 1e-9
    w.home.btn_input.click()
    w.input_tabs.setCurrentWidget(T)
    assert w.save_current()                                              # Cmd/Ctrl+S = 교통량 저장
    after = read_csv_any(path)
    assert after.loc[0, "M_day"] == "1200" and len(after) == len(before)
    assert (path.parent / (path.name + ".bak")).exists()
    assert set(before.columns) <= set(after.columns)                     # 원래 열 보존
    T.tbl.item(0, col["DTV"]).setText("많음")                             # 숫자가 아닌 값 → 빨간 칸, 저장 거부
    assert T.tbl.item(0, col["DTV"]).background().color().name() == "#ffd6d6"
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *a, **k: None)
    assert not T.save()
    assert read_csv_any(path).loc[0, "M_day"] == "1200"
    w.close()
    app.processEvents()


def test_lanes_editor_override_and_map(tmp_path):
    """차로 수 탭: 계산한 도로 객체가 표로 → 방향별 차로 수를 넣으면 음원선 위치가 sori.build 와 같게 다시 계산 →
    저장하면 roads_override.csv 생성·설정 파일에 경로, 다시 계산하면 source_lines.csv 에 반영. 지도에서 보기"""
    import yaml
    from PySide6 import QtWidgets
    import reference_case as RC
    import sori_qt as Q
    from sori import config as C, build
    from sori.build import lane_layout, source_offsets
    out = RC.run_case(tmp_path / "case")
    cfgp = tmp_path / "case" / "config.yaml"
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = Q.MainWindow()
    w.open_config(cfgp, out)
    L = w.lanes
    assert L.isEnabled() and L.tbl.rowCount() == len(w.R["roads"])
    two = [i for i in range(L.tbl.rowCount()) if not L._get(i, "oneway") and int(L._get(i, "lanes")) >= 2]
    assert two, "기준 사례에 양방향 다차로 도로가 있어야 함"
    i = two[0]
    n = int(L._get(i, "lanes"))
    r = L._road(i)
    L.tbl.item(i, L._col("lanes_fwd")).setText(str(n))                   # 정방향에 차로를 몰아 줌(합 ≠ 지도 → 노랑, 저장은 됨)
    L.tbl.item(i, L._col("lanes_bwd")).setText("1")
    L.tbl.item(i, L._col("lanes_basis")).setText("checked")
    nd, nb, wl, rule = lane_layout(w.cfg, n, float(r.width), False, n, 1)
    exp = " / ".join(f"{o:+.2f}" for o, _ in source_offsets(nd, nb, wl, False))
    assert rule == "override" and L._get(i, "_off") == exp
    assert L.tbl.item(i, L._col("lanes_fwd")).background().color().name() == "#fff2c2"
    L.tbl.selectRow(i)
    L.map_selected()                                                     # 지도 화면에 도로·음원선
    assert w.stack.currentIndex() == 1 and w._lane_focus is not None and len(w.map.ax.lines) >= 3
    w.show_input()
    w.input_tabs.setCurrentWidget(L)
    assert w.save_current()                                              # Cmd/Ctrl+S
    ovp = tmp_path / "case" / "roads_override.csv"
    raw = yaml.safe_load(cfgp.read_text(encoding="utf-8"))
    assert ovp.exists() and raw["input"]["road_override"]["path"] == "roads_override.csv"
    cfg = C.load(cfgp)                                                   # 다시 계산(build)하면 음원선에 반영
    build.main(cfg)
    import pandas as pd
    sl = pd.read_csv(Path(cfg["project"]["output_dir"]) / "source_lines.csv")
    row = sl[sl.ufid == r.ufid].iloc[0]
    assert row.lanes_fwd_used == n and row.lanes_bwd_used == 1 and row.lane_rule == "override"
    w.close()
    app.processEvents()


def test_ui_helpers_progress_overwrite_export(tmp_path):
    """화면 도우미(2026-11-12 UI 개선): 시간 표기, 단계 묶은 전체 진행률, 덮어쓸 결과 목록, 새 폴더 이름, ZIP 내보내기"""
    import datetime as dt
    import zipfile
    import sori_qt as Q
    assert [Q.fmt_dur(s) for s in (0, 45, 200, 3900)] == ["0초", "45초", "3분 20초", "1시간 05분"]
    cfg = {"receivers": {"grid": {"enabled": True, "heights": [1.5, 4]}, "facade": {"enabled": True},
                         "points": {"path": "p.csv"}}}
    assert Q.count_stages(cfg, ("grid", "facade", "points")) == 4 and Q.count_stages(cfg, ("points",)) == 1
    sp = Q.StageProgress(3)
    f, t = sp("격자 h=4 m", 50, 100, 30)
    assert abs(f - 0.5 / 3) < 1e-9 and t.startswith("1/3 단계 · 격자 h=4 m 50/100 지점") and "이 단계 남은 시간 약 30초" in t
    f, t = sp("건물 벽면", 100, 100, 0)
    assert abs(f - 2 / 3) < 1e-9 and t.startswith("2/3 단계")
    f, t = sp("점 수음점", 3, 3, 0)
    assert f == 1.0 and "3/3 단계" in t and "이 단계" not in t
    assert Q.StageProgress(1)("단면", 1, 4, 90)[1] == "단면 1/4 지점 · 남은 시간 약 1분 30초"

    out = tmp_path / "output"
    out.mkdir()
    assert Q.overwrite_list(out, ("grid", "facade", "points")) == []
    for fn in ("grid_result.csv", "point_result.csv", "HQ_grid_LrT_h4m.png", "run_info.json", "model_rasters.npz"):
        (out / fn).write_text("x", encoding="utf-8")
    assert [r[1] for r in Q.overwrite_list(out, ("grid", "facade", "points"))] == ["grid_result.csv", "point_result.csv"]
    assert Q.overwrite_list(out, ("facade",)) == []                           # 이번에 계산하지 않는 대상은 묻지 않음
    now = dt.datetime(2026, 11, 12, 15, 4)
    p = Q.fresh_out_dir(out, now)
    assert p == tmp_path / "output_20261112_1504" and not p.exists()
    p.mkdir()
    assert Q.fresh_out_dir(out, now).name == "output_20261112_1504_2"

    assert [x.name for x in Q.export_files(out, ["tables", "cond"])] == ["grid_result.csv", "point_result.csv",
                                                                         "run_info.json"]
    z = out / "묶음.zip"                                                     # 결과 폴더 안에 저장해도 자기 자신은 안 넣음
    assert Q.export_zip(out, z, list(Q.EXPORT_GROUPS)) == 4
    assert sorted(zipfile.ZipFile(z).namelist()) == ["HQ_grid_LrT_h4m.png", "grid_result.csv", "point_result.csv",
                                                     "run_info.json"]     # 중간 파일(npz)은 빠짐


def test_collapsible_docks_and_overwrite_choice(tmp_path, monkeypatch):
    """접이식 창: 작업마다 필요한 창만 펴 둠(TASK_DOCKS, 2026-11-12 오후). 도구 줄 단추로 접고 펴며, 홈에 다녀오면 작업 구성으로.
    계산 실행은 아래 창을 다시 폄. 덮어쓰기 물음: 이전 결과가 있으면 묻고 '새 폴더'면 날짜 붙인 폴더로, '취소'면 계산하지 않음"""
    import shutil
    from PySide6 import QtWidgets
    import reference_case as RC
    import sori_qt as Q
    work = tmp_path / "case"
    shutil.copytree(RC.INPUT, work)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = Q.MainWindow()
    w.show()
    w.open_config(work / "config.yaml")
    assert not w.act_folder.isEnabled() and not w.act_export.isEnabled()      # 아직 결과 폴더 없음
    d = w.docks
    shown = lambda: {n for n, x in d.items() if x.isVisible()}               # noqa: E731
    w.enter_task("grid")
    app.processEvents()
    assert shown() == {"속성", "계산·결과 표"}                                # 지도 확인 · 계산: 속성 + 계산 창
    w.act_dock["프로젝트"].trigger()                                          # 폄
    app.processEvents()
    assert shown() == {"속성", "프로젝트", "계산·결과 표"}
    w.show_home()
    assert not shown()
    w.enter_task("map")
    app.processEvents()
    assert shown() == {"프로젝트", "속성"}
    w.enter_task("run")
    app.processEvents()
    assert shown() == {"계산·결과 표"}
    w._toggle_all_docks()                                                     # 하나라도 접혀 있으면 모두 폄
    assert shown() == set(d)
    w._toggle_all_docks()                                                     # 모두 접음(지도 크게)
    app.processEvents()
    assert not shown()

    launched = []
    monkeypatch.setattr(w, "_launch", lambda wk, text, busy=False: launched.append(wk.kw))
    w.start()                                                                 # 결과 없음 → 묻지 않고 시작
    assert len(launched) == 1 and Path(launched[0]["out"]) == work / "output" and launched[0]["stages"] >= 1
    assert d["계산·결과 표"].isVisible() and not w.act_dock["속성"].isChecked()   # 아래 창만 다시 폄
    out = work / "output"
    out.mkdir()
    (out / "grid_result.csv").write_text("x", encoding="utf-8")
    asked = []
    monkeypatch.setattr(w, "_overwrite_choice", lambda o, ow: asked.append(ow) or "cancel")
    w.start()
    assert len(asked) == 1 and asked[0][0][1] == "grid_result.csv" and len(launched) == 1
    monkeypatch.setattr(w, "_overwrite_choice", lambda o, ow: "new")
    w.start()
    new = Path(launched[-1]["out"])
    assert len(launched) == 2 and new.parent == work and new.name.startswith("output_2") and w.ed_out.text() == str(new)
    w.ed_out.setText(str(out))
    monkeypatch.setattr(w, "_overwrite_choice", lambda o, ow: "overwrite")
    w.start()
    assert Path(launched[-1]["out"]) == out

    w.open_results(RC.run_case(tmp_path / "done"))                            # 결과가 있으면 폴더·내보내기 켜짐
    assert w.act_folder.isEnabled() and w.act_export.isEnabled()
    z = w.export_results_to(tmp_path / "묶음", ["tables", "cond"])
    assert z.suffix == ".zip" and z.stat().st_size > 1000
    w.close()
    app.processEvents()


def test_splash_and_input_guide(tmp_path):
    """시작 표지: 차림표·상태 줄 없이 보이고 클릭·키로 홈. 홈 화면 프로젝트 칸은 이름·결과 상태만(경로는 풍선말).
    준비할 파일 안내: 칸 상태(필수 빔 ✗ / 권장 빔 △ / 선택 빔 —), 수치지도 폴더의 건물 레이어 검사, 서식 만들기가 칸에 넣음,
    필수 파일이 빠졌으면 입력값 화면이 안내 탭으로 열림"""
    import shutil
    from PySide6 import QtCore, QtGui, QtWidgets
    from PySide6.QtCore import Qt
    import reference_case as RC
    import sori_qt as Q
    work = tmp_path / "case"
    shutil.copytree(RC.INPUT, work)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = Q.MainWindow()
    w.show_splash()
    w.show()
    app.processEvents()
    assert w.stack.currentWidget() is w.splash and not w.menuBar().isVisible() and not w.statusBar().isVisible()
    assert w.splash.findChild(QtWidgets.QLabel, "title").text() == "SORI"           # 기획안 표지 틀
    QtWidgets.QApplication.sendEvent(w.splash, QtGui.QMouseEvent(
        QtCore.QEvent.MouseButtonRelease, QtCore.QPointF(10, 10), QtCore.QPointF(10, 10),
        Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
    assert w.stack.currentIndex() == 0 and w.menuBar().isVisible() and w.statusBar().isVisible()
    w.show_splash()
    QtWidgets.QApplication.sendEvent(w.splash, QtGui.QKeyEvent(QtCore.QEvent.KeyPress, Qt.Key_Space, Qt.NoModifier))
    assert w.stack.currentIndex() == 0

    w.open_config(work / "config.yaml")
    assert str(work) not in w.home.lbl_proj.text() and str(work) in w.home.lbl_proj.toolTip()
    g = w.guide
    st = lambda k: g.rows[k][0].text()                                        # noqa: E731
    assert st("input.map.folders") == "✓" and "건물 레이어" in g.rows["input.map.folders"][1].text()
    assert st("input.junctions.path") == "—"
    g.set_value("input.traffic.path", "")
    assert st("input.traffic.path") == "△"
    g.set_value("input.map.folders", "없는폴더")
    assert st("input.map.folders") == "✗" and g.refresh() == ["수치지도 폴더"]
    (work / "빈폴더").mkdir()
    g.set_value("input.map.folders", "빈폴더")
    assert st("input.map.folders") == "✗" and "건물 레이어" in g.rows["input.map.folders"][1].text()
    w.show_input()
    assert w.input_tabs.currentWidget() is g                                  # 필수가 빠졌으면 안내 탭부터
    dst = g.make_template("input.junctions.path")
    assert dst == work / "junctions.csv" and dst.read_text(encoding="utf-8").startswith("x,y,type")
    assert w.editor.w["input.junctions.path"][0].edit.text() == "junctions.csv" and st("input.junctions.path") == "✓"
    p = g.make_template("receivers.points.path")
    assert p.read_text(encoding="utf-8-sig").splitlines()[0] == "point_id,x,y,h"
    w.close()
    app.processEvents()


def test_3d_view_builds_once_and_opens(tmp_path, monkeypatch):
    """도구 줄 '3D 보기': 결과 폴더에 noise3d.html 이 없으면 만들어 브라우저로 열고, 있으면 다시 만들지 않고 엶.
    결과가 없으면 단추가 꺼져 있음"""
    from PySide6 import QtGui, QtWidgets
    import reference_case as RC
    import sori_qt as Q
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    opened = []
    monkeypatch.setattr(QtGui.QDesktopServices, "openUrl", lambda u: opened.append(u.toLocalFile()) or True)
    monkeypatch.setattr(Q, "HAS_WEB", False)                 # 브라우저 경로(앱 안 보기는 test_3d_view_in_app)
    w = Q.MainWindow()
    assert not w.act_3d.isEnabled()
    out = RC.run_case(tmp_path / "case")
    w.open_config(tmp_path / "case" / "config.yaml", out)
    assert w.act_3d.isEnabled()
    p = w.show_3d_view(sync=True)
    assert p == out / "noise3d.html" and p.stat().st_size > 100_000 and opened == [str(p)]
    html = p.read_text(encoding="utf-8")
    assert "three" in html.lower() and "주간 LrT" in html
    t0 = p.stat().st_mtime_ns
    assert w.show_3d_view(sync=True) == p and p.stat().st_mtime_ns == t0 and len(opened) == 2   # 있으면 그대로 엶
    import os
    os.utime(p, (p.stat().st_atime - 100, p.stat().st_mtime - 100))   # 결과보다 오래된 뷰어 → 오래됨으로 판정
    assert w._3d_stale(p)
    w.close()
    app.processEvents()


def test_3d_view_in_app(tmp_path, monkeypatch):
    """웹 화면 부품이 있으면 3D 보기는 앱 안 화면(stack 4)에서 열리고 ‘◀ 지도로’로 지도 화면 복귀. 웹 화면은 처음 열 때 만듦.
    (화면 없는 시험에서는 실제 웹 화면 대신 load 만 가로챔)"""
    from PySide6 import QtWidgets
    import reference_case as RC
    import sori_qt as Q
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setattr(Q, "HAS_WEB", True)
    loaded = []
    monkeypatch.setattr(Q.View3DPage, "load", lambda self, p, title: (setattr(self, "path", p), loaded.append(title)))
    w = Q.MainWindow()
    out = RC.run_case(tmp_path / "case")
    w.open_config(tmp_path / "case" / "config.yaml", out)
    w.enter_task("grid")
    p = w.show_3d_view(sync=True)
    assert w.stack.currentWidget() is w.view3d and w.view3d.path == p and loaded and w.tb_view.isHidden()
    w.view3d.back.emit()
    assert w.stack.currentIndex() == 1 and not w.tb_view.isHidden()
    w.close()
    app.processEvents()


def test_3d_viewer_has_no_exceedance(tmp_path):
    """3D 뷰어(noise3d.html)에는 기준 초과 단추·색·문구가 없음(2026-11-13 사용자 요청). 주간·야간 등급 지도만"""
    import reference_case as RC
    from sori import config as C, viewer3d
    out = RC.run_case(tmp_path / "case")
    cfg = C.load(tmp_path / "case" / "config.yaml")
    cfg["project"]["output_dir"] = str(out)
    html = Path(viewer3d.main(cfg)).read_text(encoding="utf-8")
    assert "초과" not in html and '"xstat"' not in html and "LrT_x" not in html
    assert "주간 LrT" in html and "야간 LrN" in html
    assert viewer3d.file_version(out / "noise3d.html") == viewer3d.VIEWER_VERSION
    # 2026-11-14(뷰어 판 3): 벽면 방향별 명암(0.80–1.0)·건물 윤곽선
    D = viewer3d.build_data(cfg)
    assert len(D["fac"]["s"]) == len(D["fac"]["T"]) and 80 <= min(D["fac"]["s"]) <= max(D["fac"]["s"]) <= 100
    assert max(D["fac"]["s"]) - min(D["fac"]["s"]) >= 10                     # 방향이 다른 벽은 밝기가 다름
    assert "EdgesGeometry" in html and "edgesO.visible" in html


def test_old_3d_viewer_rebuilt_automatically(tmp_path, monkeypatch):
    """예전 판 noise3d.html(판 표시 없음, 기준 초과 단추 있음)은 열 때 묻지 않고 지금 판으로 다시 만듦"""
    from PySide6 import QtWidgets
    import reference_case as RC
    import sori_qt as Q
    from sori import viewer3d
    out = RC.run_case(tmp_path / "case")
    (out / "noise3d.html").write_text("<html><body><button>기준 초과</button></body></html>", encoding="utf-8")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = Q.MainWindow()
    w.open_config(tmp_path / "case" / "config.yaml", out)
    monkeypatch.setattr(Q, "HAS_WEB", False)
    monkeypatch.setattr(Q.QtGui.QDesktopServices, "openUrl", staticmethod(lambda u: True))
    asked = []
    monkeypatch.setattr(Q.QtWidgets.QMessageBox, "exec", lambda self: asked.append(1))
    w.show_3d_view(sync=True)
    html = (out / "noise3d.html").read_text(encoding="utf-8")
    assert "초과" not in html and viewer3d.file_version(out / "noise3d.html") == viewer3d.VIEWER_VERSION
    assert not asked
    w.close()
    app.processEvents()


def test_barrier_draw_run_and_diff_map(tmp_path, monkeypatch):
    """방음벽 시뮬레이션(보완 사항 13·5): 지도에 그려 barriers.csv 저장 → 설치 후 계산(옆 ‘_방음벽’ 폴더, 설정 파일은 그대로)
    → 전후 차이 지도·속성. 설치 전 결과 폴더는 바뀌지 않음"""
    import numpy as np
    import pandas as pd
    from PySide6 import QtWidgets
    import reference_case as RC
    import sori_gui as G
    import sori_qt as Q
    out = RC.run_case(tmp_path / "case")
    cfgp = tmp_path / "case" / "config.yaml"
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = Q.MainWindow()
    w.open_config(cfgp, out)
    w.enter_task("grid")
    assert not w.act_diff.isEnabled() and not w.btn_bar_run.isEnabled()
    cx, cy = w.cfg["receivers"]["center"]
    w.show_barrier_panel()
    w.sp_bar_h.setValue(4.0)
    w.cb_bar_type.setCurrentIndex(w.cb_bar_type.findData("none"))
    w._toggle_bar_draw(True)
    assert w._bar_draw == [] and w.btn_bar_draw.text() == "그리기 끝"
    w._on_map_click(cx - 120.0, cy + 30.0)
    w._on_map_click(cx - 40.0, cy + 30.0)
    assert w.props_title.text() != "" and len(w._bar_draw) == 2          # 그리는 중 클릭은 격자점 조회가 아님
    w.map.finished.emit()                                                 # 더블클릭·오른쪽 클릭
    assert w._bar_draw is None and len(w.barriers) == 1 and w.btn_bar_run.isEnabled()
    b = w.barriers[0]
    assert b["id"] == "W1" and b["h"] == 4.0 and b["kind"] == "none" and b["loss"] is None
    assert w.tbl_bar.rowCount() == 1 and w.tbl_bar.item(0, 4).text() == "80.0"
    w.tbl_bar.item(0, 2).setText("강한 반사 저감형")                     # 표에서 반사 종류·손실 고침
    w.tbl_bar.item(0, 3).setText("6")
    assert w.barriers[0]["kind"] == "high" and w.barriers[0]["loss"] == 6.0
    w.tbl_bar.item(0, 3).setText("99")                                   # 범위 밖은 되돌림
    assert w.barriers[0]["loss"] == 6.0 and w.tbl_bar.item(0, 3).text() == "6"
    w.tbl_bar.item(0, 2).setText("반사 없음"); w.tbl_bar.item(0, 3).setText("")
    assert w.barriers[0]["kind"] == "none" and w.barriers[0]["loss"] is None
    bcsv = tmp_path / "case" / "barriers.csv"
    assert bcsv.is_file() and "W1" in bcsv.read_text(encoding="utf-8-sig")
    w.tbl_bar.item(0, 1).setText("5")                                    # 표에서 높이 고침 → 파일에도
    assert w.barriers[0]["h"] == 5.0 and ",5.0,none," in bcsv.read_text(encoding="utf-8-sig")
    w.tbl_bar.item(0, 1).setText("-1")                                   # 잘못된 값은 되돌림
    assert w.barriers[0]["h"] == 5.0 and w.tbl_bar.item(0, 1).text() == "5"
    # 설치 후 계산(스레드 대신 바로 실행)
    seen = {}

    def launch(worker, text, busy=False):
        seen.update(worker.kw)
        r = G.run_job(worker.kw["cfg"], worker.kw["out"], worker.kw["which"], False, method=worker.kw.get("method"),
                      barriers=worker.kw["barriers"], extra_heights=worker.kw["extra_heights"],
                      compare_base=worker.kw["compare_base"], scenario=worker.kw["scenario"])
        w._t0 = 0.0
        w._on_done("run", r)
    monkeypatch.setattr(w, "_launch", launch)
    w.chk["facade"].setChecked(False); w.chk["points"].setChecked(False)
    t_base = (out / "grid_result.csv").stat().st_mtime
    w.start_barrier_run()
    after = Path(str(out) + "_방음벽")
    assert Path(seen["out"]) == after and Path(seen["compare_base"]) == out and seen["scenario"] == "방음벽"
    assert sorted(seen["extra_heights"]) == [4.0, 10.0]
    assert (out / "grid_result.csv").stat().st_mtime == t_base           # 설치 전 결과는 그대로
    assert "barriers" not in cfgp.read_text(encoding="utf-8")            # 설정 파일도 그대로
    assert Path(w.out) == after and w.diff_base == out
    assert (after / "barriers_used.csv").is_file() and (after / "diff_grid.csv").is_file()
    assert w.act_diff.isEnabled() and w.act_diff.isChecked()
    assert w.props_title.text() == "방음벽 설치 전후 차이"
    D = pd.read_csv(after / "diff_grid.csv")
    assert D.dLrT.min() < -3.0 and D.dLrT.max() < 0.05                    # 반사 없음: 낮아지기만
    Dh, key = w._grid_now
    assert "dLrT" in Dh.columns and key == "LrT"                         # 차이 지도를 보는 중
    r = Dh.iloc[int(np.argmin(Dh.dLrT.values))]
    w._on_map_click(float(r.x), float(r.y))
    vals = {w.props.item(i, 0).text(): w.props.item(i, 1).text() for i in range(w.props.rowCount())}
    assert vals["ΔLrT dB"] == f"{r.dLrT:+.1f}" and vals["LrT 기준 dB"] == f"{r.LrT_base:.1f}"
    assert w.save_map_to(tmp_path / "diff.png").stat().st_size > 1000
    w.act_diff.setChecked(False); w._toggle_diff()                        # 끄면 설치 후 소음지도
    assert "dLrT" not in w._grid_now[0].columns
    # 방음벽 결과를 보는 중에 다시 계산해도 기준은 설치 전 폴더
    assert w._barrier_base() == out
    # 결과 폴더를 다시 열어도 기준을 기억(run_conditions.json)
    w.open_results(after)
    assert w.diff_base == out and w.act_diff.isEnabled()
    w.open_results(out)
    assert w.diff_base is None and not w.act_diff.isEnabled()
    w.set_diff_base(after)                                               # 손으로 기준 고르기(반대 방향 차이)
    assert w.act_diff.isChecked() and w._grid_now[0].dLrT.max() > 3.0
    w._del_barrier()                                                     # 선택 없으면 그대로
    w.tbl_bar.selectRow(0); w._del_barrier()
    assert w.barriers == [] and not w.btn_bar_run.isEnabled()
    w.close()
    app.processEvents()


def test_run_with_extra_height_1p5(tmp_path, monkeypatch):
    """‘고시 높이 1.5 m 격자 추가’: 설정 파일은 그대로 두고 이번 계산에만 1.5 m 격자를 더함"""
    import pandas as pd
    from PySide6 import QtWidgets
    import reference_case as RC
    import sori_gui as G
    import sori_qt as Q
    import shutil
    work = tmp_path / "case"
    shutil.copytree(RC.INPUT, work)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = Q.MainWindow()
    w.open_config(work / "config.yaml")
    seen = {}

    def launch(worker, text, busy=False):
        seen.update(worker.kw)
        w._t0 = 0.0
        w._on_done("run", G.run_job(worker.kw["cfg"], worker.kw["out"], worker.kw["which"], False,
                                    extra_heights=worker.kw["extra_heights"]))
    monkeypatch.setattr(w, "_launch", launch)
    w.chk["facade"].setChecked(False); w.chk["points"].setChecked(False)
    w.chk_h15.setChecked(True)
    w.start()
    assert seen["extra_heights"] == [1.5] and seen["stages"] == 3
    assert sorted(pd.read_csv(Path(w.out) / "grid_result.csv").h.unique()) == [1.5, 4.0, 10.0]
    assert [w.cb_h.itemData(i) for i in range(w.cb_h.count())] == [1.5, 4.0, 10.0]
    assert "1.5" not in (work / "config.yaml").read_text(encoding="utf-8")
    assert "receivers.grid.heights" in (Path(w.out) / "config_used.yaml").read_text(encoding="utf-8")
    w.close()
    app.processEvents()


def test_simple_palette_switch():
    """앱 화면 7색 색표(2026-11-14): 바꾸면 격자·3D 뷰어가 같은 목록을 쓰고, 되돌리면 DIN 11색(보고서 그림)"""
    from sori import maps_hq as M, viewer3d as V
    prev = M.set_palette("simple")
    try:
        assert M.palette_name() == "simple" and len(M.DIN_COLS) == len(M.DIN_EDGES) + 1 == 7
        assert V.DIN_EDGES is M.DIN_EDGES and M.din_cmap()[0].N == 7
        assert M.BLD_FC == "#c9c9c9"                                         # 건물이 50 dB 이하 칸과 구분되게
        with pytest.raises(ValueError):
            M.set_palette("rainbow")
    finally:
        M.set_palette(prev)
    assert M.palette_name() == "din" and len(M.DIN_COLS) == 11 and M.BLD_FC == "#f2f2f2"   # 보고서 그림 색 그대로


def test_facade_shade_and_loud_azimuth():
    """3D 파사드 명암(빛 쪽 벽이 밝고 반대쪽이 어두움, 범위 안)·가장 시끄러운 벽 시점(2026-11-14)"""
    import numpy as np
    import pandas as pd
    from sori.plots import loud_azimuth, wall_shade
    az = -60.0
    a = np.radians(az + 40.0)
    lit, dark = wall_shade(np.cos(a), np.sin(a), az, 0.62, 0.38), wall_shade(-np.cos(a), -np.sin(a), az, 0.62, 0.38)
    assert lit == pytest.approx(1.0) and dark == pytest.approx(0.62) and 0.62 < wall_shade(0, 1, az, 0.62, 0.38) < 1
    F = pd.DataFrame({"nx": [1.0, 0.0], "ny": [0.0, 1.0]}, index=pd.Index([10, 11], name="fid"))
    FR = pd.DataFrame({"bid": [5, 5, 6], "fid": [10, 11, 10], "LrT": [60.0, 72.0, 99.0]})
    assert loud_azimuth(FR, F, 5, "LrT", offset=0.0) == pytest.approx(90.0)        # 북쪽 벽(72 dB)이 정면
    assert loud_azimuth(FR, F, 7, "LrT") is None


def test_cursor_value_in_status_bar(tmp_path):
    """상태 줄: 마우스 위치 좌표 + 가까운 격자점 값(2026-11-14). 격자 밖은 좌표만, 건물 안은 '건물 안'"""
    from PySide6 import QtWidgets
    import reference_case as RC
    import sori_qt as Q
    out = RC.run_case(tmp_path / "case")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = Q.MainWindow()
    w.show()
    w.open_config(tmp_path / "case" / "config.yaml", out)
    w.enter_task("grid")
    G = w.R["grid"]; Gh = G[G.h == w.cb_h.currentData()].dropna(subset=["LrT"])
    r = Gh.iloc[len(Gh) // 3]
    w._on_map_move(float(r.x) + 0.4, float(r.y) - 0.4)
    assert w.lbl_xy.text().endswith(f"LrT {r.LrT:.1f} dB")
    w._on_map_move(float(Gh.x.max()) + 500, float(r.y))                   # 격자 밖
    assert "dB" not in w.lbl_xy.text() and w.lbl_xy.text().startswith("X ")
    w.cb_key.setCurrentIndex(1)                                           # 야간으로 바꾸면 야간 값
    w._on_map_move(float(r.x), float(r.y))
    assert w.lbl_xy.text().endswith(f"LrN {r.LrN:.1f} dB")
    w.close()
    app.processEvents()


def test_facade3d_building_numbers_values_and_ghost(tmp_path):
    """3D 파사드(2026-11-14): 건물 번호(대상 = 빨간 상자, 주변 = 흰 상자), 벽면 소음도 숫자(기본 켬, 끄면 없음),
    주변 투명(대상만 색). 두 지도 창의 선택이 같이 바뀌고, 3D 를 보일 때만 단추가 나옴"""
    import matplotlib.colors as mc
    from PySide6 import QtWidgets
    import reference_case as RC
    import sori_qt as Q
    out = RC.run_case(tmp_path / "case")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = Q.MainWindow()
    w.show()
    w.open_config(tmp_path / "case" / "config.yaml", out)
    w.enter_task("grid")
    assert not w.map3d.opt_vals.isVisible()                                  # 건물을 고르기 전에는 없음
    bid = int(w.R["facade"].bid.value_counts().index[0])
    assert w.show_facade(bid)
    app.processEvents()
    texts = lambda: [t for t in w.map3d.ax3d.texts]                          # noqa: E731
    red = [t for t in texts() if t.get_bbox_patch() is not None
           and mc.to_hex(t.get_bbox_patch().get_facecolor()) == "#d00000"]
    assert [t.get_text() for t in red] == [str(bid)]                         # 대상 건물 번호
    nums = [t for t in texts() if t.get_text().isdigit() and t not in red]
    assert len(nums) >= 1                                                    # 주변 건물 번호
    # 격자 지도에도 같은 번호(대상 = 빨간 상자) + 3D 범위 점선 원
    g = [t for t in w.map.ax.texts if t.get_gid() == "bid_label"]
    from sori.plots import bid_label_points, loud_azimuth
    B = w.R["buildings"]
    cand = {str(b) for b, *_ in bid_label_points(B[B.intersects(w._facade_view(bid))], bid, w.FACADE_BID_LABELS)}
    assert {t.get_text() for t in g} <= cand and {t.get_text() for t in red + nums} <= cand   # 같은 후보에서 화면 겹침만 뺌
    assert str(bid) in {t.get_text() for t in g}
    az = loud_azimuth(w.R["facade"], w.R["facades"], bid, "LrT")              # 가장 시끄러운 벽이 보이는 시점
    assert az is not None and abs(((w.map3d.ax3d.azim - az) + 180) % 360 - 180) < 1e-6
    assert [t.get_text() for t in g if mc.to_hex(t.get_bbox_patch().get_facecolor()) == "#d00000"] == [str(bid)]
    assert "facade_view" in {l.get_gid() for l in w.map.ax.lines}
    vals = [t for t in texts() if "." in t.get_text()]
    assert w.map3d.opt_vals.isVisible() and w.map3d.opt_vals.isChecked() and len(vals) > 0
    w.map3d.opt_vals.setChecked(False)                                       # 숫자 끔
    assert not [t for t in texts() if "." in t.get_text()]
    assert not w.map.opt_vals.isChecked()                                    # 다른 창도 같이
    n_solid = len(w.map3d.ax3d.collections)
    w.map3d.opt_ghost.setChecked(True)                                       # 주변 투명
    cols = w.map3d.ax3d.collections
    assert len(cols) == n_solid + 1 and w.map.opt_ghost.isChecked()
    assert any(len(c.get_facecolor()) and c.get_facecolor()[:, 3].max() < 0.1 for c in cols)   # 주변 건물 면은 거의 투명
    w.map3d.opt_ghost.setChecked(False); w.map3d.opt_vals.setChecked(True)
    w.close()
    app.processEvents()


def test_grid_contour_labels_limit_and_facade_ground(tmp_path):
    """2차 개선(2026-11-14): 격자 지도 등음선 dB 숫자·기준선(보기 ▸ 등음선 숫자·기준선, 기본 켬),
    3D 파사드 바닥 소음지도(기본 켬, 끄면 없음, 두 창 같이)"""
    from PySide6 import QtWidgets
    import reference_case as RC
    import sori_qt as Q
    from sori.maps_hq import DIN_EDGES
    out = RC.run_case(tmp_path / "case")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = Q.MainWindow()
    w.resize(1400, 900)
    w.show()
    w.open_config(tmp_path / "case" / "config.yaml", out)
    w.enter_task("grid")
    app.processEvents()
    assert w.act_clab.isChecked() and w._grid_limit("LrT") == 68.0 and w._grid_limit("LrN") == 58.0
    labs = [t.get_text() for t in w.map.ax.texts if t.get_gid() == "contour_label"]
    assert labs and set(labs) <= {str(e) for e in DIN_EDGES} | {"기준 68"}     # 등음선 숫자
    strip = " ".join(t.get_text() for a in w.map.fig.axes for t in a.texts)
    G = w.R["grid"][w.R["grid"].h == w.cb_h.currentData()].LrT
    if G.min() < 68 < G.max():
        assert "기준 68 dB" in strip                                        # 범례 띠에 기준선
    w.act_clab.setChecked(False); w.redraw(); app.processEvents()
    assert not [t for t in w.map.ax.texts if t.get_gid() == "contour_label"]
    assert "기준 68 dB" not in " ".join(t.get_text() for a in w.map.fig.axes for t in a.texts)
    w.act_clab.setChecked(True); w.redraw(); app.processEvents()
    bid = int(w.R["facade"].bid.value_counts().index[0])
    assert w.show_facade(bid)
    app.processEvents()
    gids = lambda: [c.get_gid() for c in w.map3d.ax3d.collections]           # noqa: E731
    assert "ground_grid" in gids() and w.map3d.opt_ground.isChecked()
    assert any("바닥 = 높이" in t.get_text() for t in w.map3d.fig.texts)
    w.map3d.opt_ground.setChecked(False)
    assert "ground_grid" not in gids() and not w.map.opt_ground.isChecked()
    w.map3d.opt_ground.setChecked(True)
    w.close()
    app.processEvents()


def test_section_roads_bands():
    """단면이 지나는 도로 노면 구간(sori_qt.section_roads): 노면 안에서 시작해도 잡히고, 이름이 같은 맞닿은 조각은 합침,
    간선(차로 수 ≥ main_min_lanes)만 간선 표시"""
    import geopandas as gpd
    from shapely.geometry import LineString
    import sori_qt as Q
    R = gpd.GeoDataFrame({"axis": ["큰길", "큰길", "_x", ""], "rname": ["", "", "", "골목"], "lanes": [4, 4, 1, 1],
                          "width": [14.0, 14.0, None, 3.0]},
                         geometry=[LineString([(50, -50), (50, 0)]), LineString([(50, 0), (50, 50)]),
                                   LineString([(1, -20), (1, 20)]), LineString([(80, -20), (80, 20)])])
    info = {"p1": (0.0, 0.0), "p2": (100.0, 0.0)}
    r = Q.section_roads(R, info, 2)
    assert [round(x[0], 3) for x in r] == [0.0, 43.0, 78.5]
    assert abs(r[0][1] - 2.625) < 1e-6 and r[0][2] == "" and not r[0][4]     # A 가 노면 안(폭 = 1차로 × 3.25 m), '_' 이름 뺌
    assert (round(r[1][0], 6), round(r[1][1], 6)) + r[1][2:] == (43.0, 57.0, "큰길", 4, True)   # 두 조각 → 한 구간
    assert r[2][2] == "골목" and not r[2][4]
    assert Q.section_roads(None, info) == []


def test_paths_view_lines_and_road_contribution(tmp_path, monkeypatch):
    """경로 보기(보완 사항 6 1단계): 지점 클릭 → 직접음·1차 반사 경로선, 기여 상위 표, 도로별 기여도"""
    import numpy as np
    from PySide6 import QtWidgets
    import reference_case as RC
    import sori_qt as Q
    from sori import paths as PA
    out = RC.run_case(tmp_path / "case")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = Q.MainWindow()
    w.open_config(tmp_path / "case" / "config.yaml", out)
    w.enter_task("grid")

    def launch(worker, text, busy=False):
        assert worker.kind == "paths"
        w._t0 = 0.0
        assert worker.kw["max_order"] == 3                                 # 격자 결과와 같은 차수(3차까지)
        w._on_done("paths", PA.point_table(worker.kw["cfg"], worker.kw["x"], worker.kw["y"], worker.kw["h"],
                                           max_order=worker.kw["max_order"]))
    monkeypatch.setattr(w, "_launch", launch)
    split0 = w.act_split.isChecked()
    w.act_path.setChecked(True); w._toggle_paths(True)
    G = w.R["grid"]; Gh = G[G.h == w.cb_h.currentData()]
    r = Gh.iloc[len(Gh) // 2]
    w._on_map_click(float(r.x), float(r.y))
    A, info = w.paths
    assert len(A) > 10 and info["h"] == w.cb_h.currentData()
    lat = 10 ** (0.1 * r.LrT_lateral) if "LrT_lateral" in r and np.isfinite(r.LrT_lateral) else 0.0
    assert info["L_day"] == pytest.approx(10 * np.log10(10 ** (0.1 * r.LrT) - lat), abs=0.01)   # 경로 합 = LrT − 측면 회절
    assert set(A.order) <= {0, 1, 2, 3}
    assert w.m_paths.rowCount() == min(w.PATH_TOP, len(A))
    # 경로선은 소음지도 위가 아니라 오른쪽 창(흰 바탕 확대)에, 왼쪽에는 수음점·확대 범위만
    assert w.act_split.isChecked() and w._split_auto == (not split0) and w.map3d.isVisibleTo(w)
    assert [c.get_gid() for c in w.map3d.ax.collections].count("paths") >= 1
    assert "paths" not in [c.get_gid() for c in w.map.ax.collections]
    assert {"paths_rcv", "paths_box"} <= {l.get_gid() for l in w.map.ax.lines}
    assert w.map3d.ax.get_title().startswith("경로 · X ")
    assert w.props_title.text().startswith("경로 · LrT")
    vals = {w.props.item(i, 0).text(): w.props.item(i, 1).text() for i in range(w.props.rowCount())}
    assert "— 도로별 기여" in vals and any(k.startswith("가로") for k in vals)
    w.cb_key.setCurrentIndex(1)                                           # 야간으로 바꾸면 표도 야간
    assert w.props_title.text().startswith("경로 · LrN")
    assert [c.get_gid() for c in w.map3d.ax.collections].count("paths") >= 1    # 다시 그려도 선이 남음
    # 점 수음점 근처를 누르면 그 점의 위치·높이·이름으로
    w.add_point(float(r.x) + 0.3, float(r.y), "PX", 2.5)     # 처음이면 설정에 경로를 넣고 다시 엶 → 격자 화면으로
    w.enter_task("grid")
    if not w.act_path.isChecked():
        w.act_path.setChecked(True); w._toggle_paths(True)
    w._on_map_click(float(r.x), float(r.y))
    assert w.paths[1]["h"] == 2.5 and w._path_name == "PX"
    assert w.map3d.ax.get_title().startswith("경로 · PX")
    assert "points" in {l.get_gid() for l in w.map.ax.lines}
    w.act_path.setChecked(False); w._toggle_paths(False)
    assert w.paths is None and w.m_paths.rowCount() == 0
    assert not {"paths_rcv", "paths_box", "points"} & {l.get_gid() for l in w.map.ax.lines}
    assert w.act_split.isChecked() == split0 and w.map3d.ax is None        # 경로 보기가 켠 나란히 보기는 함께 꺼짐
    # 나란히 보기가 꺼진 상태에서 경로를 보면 켜졌다가, 경로 보기를 끄면 다시 꺼짐
    w.act_split.setChecked(False); w._toggle_split(False)
    w.act_path.setChecked(True); w._toggle_paths(True)
    w._on_map_click(float(r.x), float(r.y))
    assert w.act_split.isChecked() and w._split_auto
    w.act_path.setChecked(False); w._toggle_paths(False)
    assert not w.act_split.isChecked() and not w._split_auto
    w.close()
    app.processEvents()


def test_building_table_overlay_conditions_and_input_tools(tmp_path, monkeypatch):
    """보완 사항 1·4·5·7·11: 건물 단위 표(정렬·CSV·기준 초과), ‘건물별 값’ 겹치기, 성분 고르기, 주야 차이 지도,
    계산 전 예상 점 수, 지도에서 점 수음점 찍기·계산 범위 그리기, 도로 속성, 국내 차종 변환"""
    import numpy as np
    import pandas as pd
    from PySide6 import QtWidgets
    import reference_case as RC
    import sori_qt as Q
    from sori import summary as SM
    out = RC.run_case(tmp_path / "case")
    cfgp = tmp_path / "case" / "config.yaml"
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = Q.MainWindow()
    w.open_config(cfgp, out)
    w.enter_task("grid")
    FR = w.R["facade"]
    # 건물 단위 표
    A = w._bld_all
    assert len(A) == FR.bid.nunique() and w.m_bld.rowCount() == len(A)
    assert A.LrT_max.iloc[0] == pytest.approx(FR.LrT.max()) and list(A.LrT_max) == sorted(A.LrT_max, reverse=True)
    assert set(A["지역"]) == {"A"} and set(A["기준_주"]) == {68.0}
    nx = int((A["초과"] == "초과").sum())
    assert 0 < nx <= len(A)
    w.chk_bld_exc.setChecked(True)
    assert w.m_bld.rowCount() == nx
    w.chk_bld_exc.setChecked(False)
    # 층·건물 종류 거름(보완 사항 7): 1층만이면 최고값이 1층 값의 최댓값, 벽면 표도 1층만
    assert w.cb_bld_floor.itemText(0) == "전체" and w.cb_bld_floor.itemText(1) == "1층"
    w.cb_bld_floor.setCurrentIndex(1)
    F1 = FR[FR.floor == 1]
    assert w._bld_all.LrT_max.max() == pytest.approx(F1.LrT.max()) and len(w._bld_all) == F1.bid.nunique()
    assert set(w.m_facade.df.floor) == {1}
    w.cb_bld_floor.setCurrentIndex(0)
    kind = w.cb_bld_kind.itemData(1)
    w.cb_bld_kind.setCurrentIndex(1)
    assert set(w._bld_all["종류"]) == {kind}
    w.cb_bld_kind.setCurrentIndex(0)
    assert len(w._bld_all) == len(A)
    p = w.save_building_table(tmp_path / "bld.csv")
    assert len(pd.read_csv(p)) == len(A)
    w._bld_row_clicked(w.tv_bld.model().index(0, 0))                      # 줄 클릭 → 그 건물 3D(나란히 보기 오른쪽)
    assert w.facade_bid == int(w.m_bld.df.iloc[w.tv_bld.model().mapToSource(w.tv_bld.model().index(0, 0)).row()].bid)
    vals = [w.props.item(i, 0).text() for i in range(w.props.rowCount())]
    w._show_props(w._summary_props(), "계산 결과 요약")
    vals = [w.props.item(i, 0).text() for i in range(w.props.rowCount())]
    assert "— 건물별 노출" in vals and "기준 초과 건물" in vals
    # 건물별 값 겹치기
    assert w.act_bmax.isEnabled()
    w.act_bmax.setChecked(True); w.redraw()
    assert "bmax" in [c.get_gid() for c in w.map.ax.collections]
    w.act_bmax.setChecked(False); w.redraw()
    assert "bmax" not in [c.get_gid() for c in w.map.ax.collections]
    # 성분 고르기(주간): 직접음만
    assert w.act_comp["direct"].isVisible() and not w.act_comp["refl4p"].isVisible()
    assert w._comps_selected() is None
    for k in ("refl1", "refl2", "refl3", "lateral"):
        w.act_comp[k].setChecked(False)
    w.redraw()
    Gh, key = w._grid_now
    G0 = w.R["grid"]; G0 = G0[G0.h == w.cb_h.currentData()]
    assert np.allclose(Gh.LrT.values, G0.LrT_direct.values, equal_nan=True) and (Gh.LrT.values <= G0.LrT.values + 1e-9).all()
    w.act_comp["refl1"].setChecked(True); w.redraw()
    assert np.allclose(w._grid_now[0].LrT.values, SM.combine(G0, ["direct", "refl1"]), equal_nan=True)
    for a in w.act_comp.values():
        a.setChecked(True)
    w.redraw()
    assert np.allclose(w._grid_now[0].LrT.values, G0.LrT.values)
    # 주야 차이 지도
    w.act_dn.setChecked(True); w.redraw()
    assert w.map.ax is not None and w.save_map_to(tmp_path / "dn.png").stat().st_size > 1000
    w.act_dn.setChecked(False); w.redraw()
    # 계산 전 예상 점 수
    assert "격자" in w.lbl_est.text() and f"{len(w.R['grid']):,}점" in w.lbl_est.text() and "벽면" in w.lbl_est.text()
    w.chk_h15.setChecked(True)
    assert f"{len(w.R['grid']) // 2 * 3:,}점" in w.lbl_est.text()
    w.chk_h15.setChecked(False)
    # 점 수음점 찍기: 파일을 만들고 설정에 경로를 넣음
    cx, cy = w.cfg["receivers"]["center"]
    w.act_pick_pt.setChecked(True); w._toggle_pick_point(True)
    monkeypatch.setattr(QtWidgets.QInputDialog, "getText", staticmethod(lambda *a, **k: ("M1, 1.5", True)))
    w._on_map_click(cx + 31.0, cy + 22.0)
    pp = tmp_path / "case" / "points.csv"
    P = pd.read_csv(pp)
    assert list(P.point_id) == ["M1"] and P.x[0] == pytest.approx(cx + 31.0) and P.h[0] == 1.5
    assert w.cfg["receivers"]["points"]["path"] == str(pp.resolve())
    w.add_point(cx - 20.0, cy - 15.0, "M2", 4.0)
    assert list(pd.read_csv(pp).point_id) == ["M1", "M2"]
    with pytest.raises(ValueError, match="이미 있음"):
        w.add_point(cx, cy, "M2", 4.0)
    # 계산 범위 그리기: 두 모서리 → 입력값 칸
    w.act_pick_pt.setChecked(False)
    w.act_pick_box.setChecked(True); w._toggle_pick_box(True)
    w._on_map_click(cx - 100.0, cy - 60.0)
    w._on_map_click(cx + 80.0, cy + 40.0)
    assert not w.act_pick_box.isChecked()
    assert w.editor.w["receivers.grid.size"][0].text() == "180, 100"
    assert w.editor.w["receivers.center"][0].text() == f"{cx - 10.0:.1f}, {cy - 10.0:.1f}"
    # 입력 지도에서 도로 클릭 → 도로 속성·계산에 쓴 교통량
    w.R.pop("grid"); w._grid_now = None
    rd = w.R["roads"].iloc[0]
    q = rd.geometry.interpolate(0.5, normalized=True)
    w._on_map_click(q.x + 1.0, q.y + 1.0)
    assert w.props_title.text().startswith("도로 ")
    vals = [w.props.item(i, 0).text() for i in range(w.props.rowCount())]
    assert "차로 수" in vals and "M 주간 대/h" in vals
    # 국내 차종 → 차량군
    te = w.traffic
    done = te.apply_kr12(0, day={1: 800, 2: 40, 3: 60, 4: 50, 8: 30, "moto": 20}, hours_day=1.0)
    rec = te._rec(0)
    assert done == ["주간"] and rec["M_day"] == "1000" and rec["p1_day"] == "9" and rec["p2_day"] == "5"
    assert rec["basis"] == "measured" and "12종 차종 변환" in rec["note"]
    w.close()
    app.processEvents()


def test_fast_preview_runs_to_separate_folder(tmp_path, monkeypatch):
    """‘빠른 미리보기’: 격자 간격 2배·반사 1차로 ‘_미리보기’ 폴더에 계산(설정 파일·원래 결과는 그대로)"""
    import json
    import shutil
    import pandas as pd
    from PySide6 import QtWidgets
    import reference_case as RC
    import sori_gui as G
    import sori_qt as Q
    work = tmp_path / "case"
    shutil.copytree(RC.INPUT, work)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = Q.MainWindow()
    w.open_config(work / "config.yaml")
    seen = {}

    def launch(worker, text, busy=False):
        seen.update(worker.kw)
        w._t0 = 0.0
        w._on_done("run", G.run_job(worker.kw["cfg"], worker.kw["out"], worker.kw["which"], False,
                                    preset=worker.kw["preset"]))
    monkeypatch.setattr(w, "_launch", launch)
    w.chk["facade"].setChecked(False); w.chk["points"].setChecked(False)
    w.chk_fast.setChecked(True)
    w.start()
    assert seen["preset"] == "fast" and Path(seen["out"]).name == "output_미리보기"
    Gd = pd.read_csv(Path(seen["out"]) / "grid_result.csv")
    assert Gd.x.drop_duplicates().sort_values().diff().dropna().min() == pytest.approx(20.0)
    assert "LrT_refl3" not in Gd.columns
    rc = json.loads((Path(seen["out"]) / "run_conditions.json").read_text(encoding="utf-8"))
    assert rc["overrides"]["receivers.grid.step"] == 20.0 and rc["overrides"]["propagation.reflection.max_order_grid"] == 1
    assert "step: 10.0" in (work / "config.yaml").read_text(encoding="utf-8")
    w.close()
    app.processEvents()


def test_measured_diff_and_floor_grid(tmp_path, monkeypatch):
    """보완 사항 5 나머지: 측정 − 계산 차이(점 수음점 표·지도), 층별 격자(이번 계산에만, 높이 목록에 층 표시)"""
    import shutil
    import pandas as pd
    from PySide6 import QtWidgets
    import reference_case as RC
    import sori_gui as G
    import sori_qt as Q
    work = tmp_path / "case"
    shutil.copytree(RC.INPUT, work)
    cx, cy = 205108.0, 553110.0
    pd.DataFrame(dict(point_id=["A", "B"], x=[cx + 40.0, cx], y=[cy - 7.0, cy - 45.0], h=[1.5, 4.0])).to_csv(
        work / "points.csv", index=False)
    (work / "measured.csv").write_text("point_id,period,Leq\nA,day,60\nA,night,52\nB,day,58\n", encoding="utf-8")
    t = (work / "config.yaml").read_text(encoding="utf-8")
    t = t.replace("receivers:\n", "receivers:\n  points: {path: points.csv, measured: measured.csv}\n")
    (work / "config.yaml").write_text(t, encoding="utf-8")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = Q.MainWindow()
    w.open_config(work / "config.yaml")
    seen = {}

    def launch(worker, text, busy=False):
        seen.update(worker.kw)
        w._t0 = 0.0
        w._on_done("run", G.run_job(worker.kw["cfg"], worker.kw["out"], worker.kw["which"], False,
                                    extra_heights=worker.kw["extra_heights"]))
    monkeypatch.setattr(w, "_launch", launch)
    w.chk["facade"].setChecked(False)
    w.sp_floors.setValue(2)
    assert w._extra_heights() == [2.8, 5.8]
    w.start()
    assert seen["extra_heights"] == [2.8, 5.8]
    labels = [w.cb_h.itemText(i) for i in range(w.cb_h.count())]
    assert labels == ["2.8 m (1층)", "4 m", "5.8 m (2층)", "10 m"]
    P = pd.read_csv(Path(w.out) / "point_result.csv")
    D = w._meas
    a = D[(D.point_id == "A") & (D.key == "LrT")].iloc[0]
    assert a["diff"] == pytest.approx(P[P.point_id == "A"].LrT.iloc[0] - 60.0)
    assert w.m_points.rowCount() == 3 and "계산−측정 dB" in w.m_points.df.columns
    assert w.m_roads.rowCount() > 0 and set(w.m_roads.df.point_id) <= {"A", "B"}   # 점 도로별 기여 탭
    assert w.act_meas.isEnabled()
    w.enter_task("grid")
    w.act_meas.setChecked(True); w.redraw()
    assert "measured" in [c.get_gid() for c in w.map.ax.collections]
    assert "측정 − 계산(LrT): 2개" in w.lbl_status.text()
    w.close()
    app.processEvents()


def test_barrier_height_study(tmp_path, monkeypatch):
    """보완 사항 12 후보 4: 방음벽 높이별 비교 — 높이마다 계산·차이, 높을수록 더 낮아짐, 표·CSV·탭"""
    import pandas as pd
    from PySide6 import QtWidgets
    import reference_case as RC
    import sori_gui as G
    import sori_qt as Q
    out = RC.run_case(tmp_path / "case")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = Q.MainWindow()
    w.open_config(tmp_path / "case" / "config.yaml", out)
    w.enter_task("grid")
    cx, cy = w.cfg["receivers"]["center"]
    w.barriers = [dict(id="W1", h=3.0, kind="none", loss=None, pts=[(cx - 120.0, cy + 30.0), (cx - 40.0, cy + 30.0)],
                       note="")]
    w._fill_bar_table()
    assert w.btn_bar_study.isEnabled()

    def launch(worker, text, busy=False):
        assert worker.kind == "study"
        T = G.barrier_height_study(worker.kw["cfg"], worker.kw["base"], worker.kw["barriers"], worker.kw["heights"],
                                   worker.kw["which"])
        w._t0 = 0.0
        w._on_done("study", T)
    monkeypatch.setattr(w, "_launch", launch)
    w.chk["facade"].setChecked(False)
    w.start_height_study([2.0, 5.0])
    T = pd.read_csv(Path(str(out) + "_방음벽_높이비교.csv"))
    assert list(T.height_m) == [2.0, 5.0] and list(T.area_m2) == [160.0, 400.0]
    g = [c for c in T.columns if c.startswith("grid_h") and c.endswith("_mean")]
    assert len(g) == 2 and all(T[c].iloc[1] < T[c].iloc[0] <= 0.0 for c in g)       # 높을수록 더 낮아짐
    assert Path(str(out) + "_방음벽2m").is_dir() and Path(str(out) + "_방음벽5m", "diff_grid.csv").is_file()
    assert w.m_study.rowCount() == 2 and "높이 m" in w.m_study.df.columns
    assert w.props_title.text() == "방음벽 높이별 비교"
    assert (out / "grid_result.csv").is_file()                                       # 설치 전 결과는 그대로
    w.close()
    app.processEvents()
