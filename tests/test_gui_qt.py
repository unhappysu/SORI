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
    """홈 화면으로 시작 → 프로젝트를 열면 최근 목록·작업 단추가 켜짐 → 작업 단추로 작업 화면, ⌂ 홈으로 복귀"""
    from PySide6 import QtWidgets
    import reference_case as RC
    import sori_qt as Q
    out = RC.run_case(tmp_path / "case")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = Q.MainWindow()
    assert w.stack.currentIndex() == 0 and w.tb_view.isHidden()
    assert not w.home.tiles["run"].isEnabled() and w.home.tiles["check"].isEnabled()
    w.open_config(tmp_path / "case" / "config.yaml", out)
    assert w.home.lst.count() == 1 and w.home.tiles["run"].isEnabled()
    assert w.home.tiles["grid"].isEnabled() and w.home.tiles["facade"].isEnabled()
    w.home.tiles["facade"].click()
    assert w.stack.currentIndex() == 1 and not w.tb_view.isHidden() and w.act_split.isChecked()
    w.show_home()
    w.home.tiles["grid"].click()
    assert w.stack.currentIndex() == 1 and not w.act_split.isChecked() and w.view == "grid"
    w.show_home()
    w.home.tiles["section"].click()
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
    w.home.tiles["input"].click()                                          # 입력값 전용 화면(지도·Dock 없음)
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
    w.home.tiles["map"].click()                                            # 입력 지도 확인(지도 화면)
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
    t = w.map3d.ax3d.get_title()
    assert f"건물 {bid}" in t and "LrT" in t
    w.cb_key.setCurrentIndex(w.cb_key.findData("LrN"))                     # 시간대 → 양쪽 다시 그림
    assert "LrN" in w.map3d.ax3d.get_title() and w.map.ax.images
    assert w.save_map_to(tmp_path / "side.png", w.map3d).exists()
    w.act_split.setChecked(False)                                          # 끄면 오른쪽 숨김
    w._toggle_split(False)
    assert w.facade_bid is None and w.map3d.isHidden()
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
    w.home.tiles["input"].click()
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
