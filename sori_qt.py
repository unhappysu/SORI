# -*- coding: utf-8 -*-
"""
SORI 데스크톱 화면 (PySide6 / Qt Widgets) — 화면 개편 1단계 시제품. 설계: docs/화면개편_1단계_설계.md
  구성: 프로젝트 트리(왼쪽) · 지도 창(가운데) · 속성 표(오른쪽) · 실행 관리·기록·결과 표(아래) · 상태 표시줄
  계산 엔진(sori/)은 고치지 않고 sori_gui.run_job / selfcheck 를 그대로 호출함.
  sori_gui.py 가 화면 실행 때 이 모듈을 먼저 쓰고, PySide6 가 없으면 기존 tkinter 화면으로 돌아감.
  지도는 EPSG(설정 crs) 평면 좌표 그대로 그림(배경지도는 2단계).

  python sori_qt.py [--config config.yaml]           Qt 화면 실행
  python sori_qt.py --snapshot 그림.png [--config …] [--out 결과폴더]   화면을 그려 PNG 로 저장하고 끝냄(화면 확인용)

※ 본 소프트웨어는 Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import json
import logging
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402

import sori_gui as G  # noqa: E402  (setup_numba_cache 는 sori_gui import 때 실행됨)
from sori import AI_NOTICE, __version__  # noqa: E402
from sori.resources import default_output_dir, resource_path  # noqa: E402

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

log = logging.getLogger("sori")
ROLE = Qt.UserRole
KEYS = {"LrT": "주간 LrT (06–22시)", "LrN": "야간 LrN (22–06시)"}
TARGETS = {"grid": "격자(소음지도)", "facade": "건물 벽면", "points": "점 수음점"}


# ================================================================ 표 모델(pandas DataFrame → QTableView)
class FrameModel(QtCore.QAbstractTableModel):
    def __init__(self, df=None):
        super().__init__()
        self.df = df if df is not None else pd.DataFrame()

    def set(self, df):
        self.beginResetModel()
        self.df = df if df is not None else pd.DataFrame()
        self.endResetModel()

    def rowCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self.df)

    def columnCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self.df.columns)

    def data(self, idx, role=Qt.DisplayRole):
        if not idx.isValid():
            return None
        v = self.df.iat[idx.row(), idx.column()]
        if role == Qt.DisplayRole:
            if isinstance(v, (float, np.floating)):
                return "" if np.isnan(v) else f"{v:.2f}"
            return str(v)
        if role == Qt.TextAlignmentRole and isinstance(v, (int, float, np.number)):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == ROLE:                                  # 정렬용 원래 값
            return v
        return None

    def headerData(self, i, orient, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        return str(self.df.columns[i]) if orient == Qt.Horizontal else str(i + 1)


def table_view(model):
    v = QtWidgets.QTableView()
    px = QtCore.QSortFilterProxyModel(v)
    px.setSourceModel(model)
    px.setSortRole(ROLE)
    v.setModel(px)
    v.setSortingEnabled(True)
    v.setAlternatingRowColors(True)
    v.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
    return v


# ================================================================ 계산 작업(별도 스레드) ↔ 화면 신호
class Bridge(QtCore.QObject):
    log = QtCore.Signal(int, str)
    prog = QtCore.Signal(float, str)
    done = QtCore.Signal(str, object)
    fail = QtCore.Signal(str)


class SignalLog(logging.Handler):
    def __init__(self, bridge):
        super().__init__()
        self.b = bridge

    def emit(self, rec):
        self.b.log.emit(rec.levelno, self.format(rec))


class Worker(QtCore.QThread):
    """kind = run: run_job(cfg, out, which, plots) / check: selfcheck. 결과는 Bridge 신호로만 전달"""
    def __init__(self, bridge, kind, **kw):
        super().__init__()
        self.b, self.kind, self.kw = bridge, kind, kw

    def run(self):
        try:
            if self.kind == "run":
                def prog(label, done, total, eta):
                    self.b.prog.emit(done / max(total, 1),
                                     f"{label} 계산 {done:,}/{total:,} 지점 · 남은 시간 약 {eta / 60:.1f}분")
                r = G.run_job(self.kw["cfg"], self.kw["out"], self.kw["which"], self.kw["plots"], progress=prog)
                self.b.done.emit("run", r)
            else:
                rp = default_output_dir() / "selfcheck_report.json"
                rp.parent.mkdir(parents=True, exist_ok=True)
                code = G.selfcheck(rp)
                rep = json.loads(rp.read_text(encoding="utf-8"))
                rep["report_path"] = str(rp)
                if code == 0:
                    self.b.done.emit("check", rep)
                else:
                    log.error("자가 점검 실패: %s", rep.get("error") or
                              [r for r in rep.get("crs", []) + rep.get("reference", []) if not r["ok"]])
                    self.b.fail.emit(f"자가 점검 실패. 보고서: {rp}")
        except Exception as e:
            log.error("오류: %s\n%s", e, traceback.format_exc())
            self.b.fail.emit(f"계산 중 오류: {e}")


# ================================================================ 지도 창(matplotlib, 평면 좌표 그대로)
class MapView(QtWidgets.QWidget):
    clicked = QtCore.Signal(float, float)          # 평면 지도(격자 지도, 벽면 지도의 왼쪽 평면) 클릭
    clicked_elev = QtCore.Signal(float, float)     # 벽면 펼친 그림(오른쪽) 클릭: (둘레 거리 m, 층)
    moved = QtCore.Signal(float, float)

    def __init__(self):
        super().__init__()
        from sori.plots import set_font
        set_font()
        self.fig = Figure(figsize=(7, 6), layout="constrained")
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.nav = NavigationToolbar2QT(self.canvas, self)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.nav)
        lay.addWidget(self.canvas, 1)
        self.canvas.mpl_connect("button_press_event", self._click)
        self.canvas.mpl_connect("motion_notify_event", self._move)
        self.ax = None
        self.ax_elev = None
        self.message("설정 파일(config.yaml)을 열거나 결과 폴더를 여세요.")

    def message(self, text):
        self.fig.clear()
        self.ax = self.ax_elev = None
        self.fig.text(0.5, 0.5, text, ha="center", va="center", color="#666")
        self.canvas.draw_idle()

    def draw(self, grid=None, extent=None, buildings=None, roads=None, title="", crs=None):
        """grid = (A 2차원 배열, (xmin, xmax, ymin, ymax)), extent = 계산 범위 사각형"""
        from sori.plots import classes
        self.fig.clear()
        self.ax_elev = None
        ax = self.ax = self.fig.add_subplot(111)
        if grid is not None:
            A, ext = grid
            cmap, norm, labels = classes(30, 5)
            im = ax.imshow(np.ma.masked_invalid(A), origin="lower", extent=ext, cmap=cmap, norm=norm,
                           interpolation="nearest", zorder=1)
            cb = self.fig.colorbar(im, ax=ax, shrink=0.8, pad=0.01)
            cb.set_ticks([hi for _, hi in labels[:-1]])  # 등급 경계(30, 35, … dB)
            cb.set_label("dB(A) · 5 dB 등급")
        if roads is not None and len(roads):
            roads.plot(ax=ax, color="#555", lw=0.8, zorder=2)
        if buildings is not None and len(buildings):
            buildings.plot(ax=ax, facecolor="#e8e8e8" if grid is None else "none", edgecolor="#222", lw=0.5, zorder=3)
        if extent is not None:
            x0, y0, x1, y1 = extent
            ax.plot([x0, x1, x1, x0, x0], [y0, y0, y1, y1, y0], "--", color="#1f5fbf", lw=1, zorder=4)
            mx, my = 0.1 * (x1 - x0), 0.1 * (y1 - y0)        # 처음 보기는 계산 범위 + 10 %
            ax.set_xlim(x0 - mx, x1 + mx)
            ax.set_ylim(y0 - my, y1 + my)
        ax.set_aspect("equal")
        ax.set_title(title, fontsize=10)
        ax.set_xlabel(f"X (m){f' · EPSG:{crs}' if crs else ''}", fontsize=8)
        ax.set_ylabel("Y (m)", fontsize=8)
        ax.tick_params(labelsize=7)
        ax.ticklabel_format(useOffset=False, style="plain")
        self._notice()
        self.canvas.draw_idle()

    def _notice(self):
        """저장한 그림에도 남도록 그림 안에 AI 활용 고지(아래 3 % 를 비워 축 제목과 겹치지 않게)"""
        self.fig.get_layout_engine().set(rect=(0, 0.03, 1, 0.97))
        self.fig.text(0.995, 0.005, f"※ {AI_NOTICE}", ha="right", va="bottom", fontsize=6, color="0.4")

    def draw_facade(self, b, F, FR, key, title, others=None, roads=None, crs=None):
        """건물 하나의 벽면 소음지도. 왼쪽: 평면(벽 번호, 수음 위치별 층 최댓값), 오른쪽: 벽을 둘레 방향으로 펼친 입면
        b: 건물 한 행(GeoSeries 행), F: 그 건물의 벽(facades.csv, fid 순), FR: 그 건물의 벽면 결과(facade_result.csv)
        펼친 순서는 벽 번호(fid) 순 = 건물 외곽선 꼭짓점 순서(build.facades)"""
        from matplotlib.collections import PolyCollection
        from sori.plots import classes
        cmap, norm, labels = classes(30, 5)
        self.fig.clear()
        gs = self.fig.add_gridspec(1, 2, width_ratios=[1, 1.9])
        ax = self.ax = self.fig.add_subplot(gs[0])
        ae = self.ax_elev = self.fig.add_subplot(gs[1])
        # ---- 평면
        x0, y0, x1, y1 = b.geometry.bounds
        m = max(x1 - x0, y1 - y0) * 0.35 + 15.0
        if others is not None and len(others):
            others.plot(ax=ax, facecolor="#f2f2f2", edgecolor="#999", lw=0.4, zorder=1)
        if roads is not None and len(roads):
            roads.plot(ax=ax, color="#bbb", lw=0.8, zorder=1)
        from geopandas import GeoSeries
        GeoSeries([b.geometry]).plot(ax=ax, facecolor="#e8e8e8", edgecolor="#222", lw=1.0, zorder=2)
        P = FR.groupby(["fid", "piece"]).agg(x=("x", "first"), y=("y", "first"), v=(key, "max")).reset_index()
        ax.scatter(P.x, P.y, c=P.v, cmap=cmap, norm=norm, s=18, edgecolors="#333", linewidths=0.3, zorder=3)
        has = set(FR.fid)
        for n, (fid, f) in enumerate(F.iterrows(), 1):
            if fid in has:
                ax.annotate(str(n), ((f.x1 + f.x2) / 2 + 3.5 * f.nx, (f.y1 + f.y2) / 2 + 3.5 * f.ny),
                            ha="center", va="center", fontsize=7, color="#1f5fbf", zorder=4)
        f0 = F.iloc[0]
        ax.plot(f0.x1, f0.y1, marker="o", color="#1f5fbf", ms=5, zorder=4)
        ax.set_xlim(x0 - m, x1 + m)
        ax.set_ylim(y0 - m, y1 + m)
        ax.set_aspect("equal")
        ax.set_title("평면 — 점: 수음 위치(층 최댓값)\n파란 숫자: 벽 번호 · ●: 펼친 그림 시작점", fontsize=8)
        ax.set_xlabel(f"X (m){f' · EPSG:{crs}' if crs else ''}", fontsize=7)
        ax.set_ylabel("Y (m)", fontsize=7)
        ax.tick_params(labelsize=6)
        ax.ticklabel_format(useOffset=False, style="plain")
        # ---- 펼친 입면: 가로 = 시작점부터 둘레 거리(m), 세로 = 층
        s0 = dict(zip(F.index, np.r_[0.0, np.cumsum(F.L.values)[:-1]]))
        Lf = F.L.to_dict()
        polys, vals = [], []
        for r in FR.itertuples():
            a, c = s0[r.fid] + r.a * Lf[r.fid], s0[r.fid] + r.b * Lf[r.fid]
            polys.append([(a, r.floor - 1), (c, r.floor - 1), (c, r.floor), (a, r.floor)])
            vals.append(getattr(r, key))
        pc = PolyCollection(polys, array=np.asarray(vals), cmap=cmap, norm=norm, edgecolors="white", linewidths=0.3)
        ae.add_collection(pc)
        top = int(max(FR.floor.max(), b.get("floors", 1) or 1))
        tot = float(F.L.sum())
        for n, fid in enumerate(F.index, 1):
            ae.axvline(s0[fid], color="#888", lw=0.5, zorder=0)
            if fid in has and Lf[fid] > tot / 60:
                ae.text(s0[fid] + Lf[fid] / 2, top, str(n), ha="center", va="bottom", fontsize=7, color="#1f5fbf",
                        clip_on=False)
        ae.set_xlim(0, tot)
        ae.set_ylim(0, top)
        # 실제 비율(층고 약 3 m)에 가깝게, 너무 납작하거나 길지 않게 0.12~0.9 로 제한
        ae.set_box_aspect(min(0.9, max(0.12, 3.0 * top / max(tot, 1.0))))
        ae.set_yticks(np.arange(top) + 0.5, [f"{i + 1}층" for i in range(top)], fontsize=6)
        ae.set_xlabel("둘레 거리 (m) — ● 시작점부터 벽 번호 순", fontsize=7)
        ae.tick_params(axis="x", labelsize=6)
        ae.set_title("벽면 펼친 그림 (외벽 앞 수음점, 층별) · 빈칸 = 계산하지 않은 벽(맞벽 등)\n", fontsize=8)
        cb = self.fig.colorbar(pc, ax=ae, shrink=0.85, pad=0.01)
        cb.set_ticks([hi for _, hi in labels[:-1]])
        cb.set_label("dB(A) · 5 dB 등급")
        self.fig.suptitle(title, fontsize=10)
        self._notice()
        self.canvas.draw_idle()

    def _click(self, e):
        if e.xdata is None or self.nav.mode:
            return
        if self.ax is not None and e.inaxes is self.ax:
            self.clicked.emit(e.xdata, e.ydata)
        elif self.ax_elev is not None and e.inaxes is self.ax_elev:
            self.clicked_elev.emit(e.xdata, e.ydata)

    def _move(self, e):
        if self.ax is not None and e.inaxes is self.ax and e.xdata is not None:     # 평면 좌표만 표시
            self.moved.emit(e.xdata, e.ydata)


# ================================================================ 주 창
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"SORI v{__version__} — 도로교통소음 예측 (RLS-19 + ISO 9613-2)")
        self.resize(1360, 860)
        self.cfg_path = None      # 열린 설정 파일
        self.cfg = None           # sori.config.load 결과
        self.out = None           # 결과 폴더
        self.R = {}               # 읽어 둔 결과 표·지도 자료
        self.view_bid = None      # None = 격자 지도, 건물 번호 = 그 건물의 벽면 지도
        self.worker = None
        self.bridge = Bridge()
        self.bridge.log.connect(self._on_log)
        self.bridge.prog.connect(self._on_prog)
        self.bridge.done.connect(self._on_done)
        self.bridge.fail.connect(self._on_fail)
        self._build()                     # 기록창을 만든 뒤 기록 연결(글꼴 안내 등 만들기 중 기록은 버림)
        h = SignalLog(self.bridge)
        h.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
        log.addHandler(h)
        log.setLevel(logging.INFO)
        log.propagate = False
        log.info(AI_NOTICE)

    # ------------------------------------------------------------ 화면 만들기
    def _build(self):
        self.map = MapView()
        self.map.clicked.connect(self._on_map_click)
        self.map.clicked_elev.connect(self._on_elev_click)
        self.map.moved.connect(lambda x, y: self.lbl_xy.setText(f"X {x:,.1f}  Y {y:,.1f}"))
        self.setCentralWidget(self.map)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["프로젝트"])
        self.tree.itemClicked.connect(self._on_tree)
        self._dock("프로젝트", self.tree, Qt.LeftDockWidgetArea)

        self.props = QtWidgets.QTableWidget(0, 2)
        self.props.setHorizontalHeaderLabels(["항목", "값"])
        self.props.horizontalHeader().setStretchLastSection(True)
        self.props.verticalHeader().hide()
        self.props.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._dock("속성", self.props, Qt.RightDockWidgetArea)

        self.tabs = QtWidgets.QTabWidget()
        self.tabs.addTab(self._run_panel(), "계산 실행")
        self.logbox = QtWidgets.QPlainTextEdit(readOnly=True)
        self.logbox.setMaximumBlockCount(5000)
        self.tabs.addTab(self.logbox, "기록")
        self.m_points, self.m_terms, self.m_facade = FrameModel(), FrameModel(), FrameModel()
        self.tabs.addTab(table_view(self.m_points), "점 수음점")
        self.tabs.addTab(table_view(self.m_terms), "감쇠항 (path_terms)")
        self.tabs.addTab(table_view(self.m_facade), "벽면 (상위 200)")
        self._dock("계산·결과 표", self.tabs, Qt.BottomDockWidgetArea)

        # 지도 위 도구 줄: 결과 높이·시간대
        tb = self.addToolBar("결과 보기")
        tb.setObjectName("view")
        tb.addWidget(QtWidgets.QLabel(" 격자 높이 "))
        self.cb_h = QtWidgets.QComboBox()
        tb.addWidget(self.cb_h)
        tb.addWidget(QtWidgets.QLabel("  시간대 "))
        self.cb_key = QtWidgets.QComboBox()
        for k, v in KEYS.items():
            self.cb_key.addItem(v, k)
        tb.addWidget(self.cb_key)
        self.cb_h.currentIndexChanged.connect(self.redraw)
        self.cb_key.currentIndexChanged.connect(self.redraw)
        tb.addSeparator()
        self.act_back = tb.addAction("◀ 격자 지도", self.show_grid)
        self.act_back.setEnabled(False)
        self.act_save = tb.addAction("지도 저장…", self.save_map)
        self.act_save.setToolTip("지금 보이는 지도를 PNG·PDF 로 저장(기본 이름은 파일명 규칙)")
        tb.addWidget(QtWidgets.QLabel("   건물을 클릭하면 그 건물의 벽면 소음지도"))

        m = self.menuBar().addMenu("파일")
        m.addAction("설정 파일 열기…", self.pick_cfg, QtGui.QKeySequence.Open)
        m.addAction("결과 폴더 열기…", self.pick_out)
        m.addAction("예제 불러오기(가상 교차로)", self.load_example)
        m.addSeparator()
        m.addAction("지도 저장(PNG·PDF)…", self.save_map, QtGui.QKeySequence.Save)
        m.addAction("결과 폴더를 파인더/탐색기로 보기", lambda: self.out and G.open_folder(self.out))
        m.addAction("끝내기", self.close, QtGui.QKeySequence.Quit)
        m = self.menuBar().addMenu("계산")
        self.act_run = m.addAction("계산 실행", self.start)
        self.act_check = m.addAction("자가 점검", self.start_check)
        m = self.menuBar().addMenu("도움말")
        m.addAction("SORI 정보", lambda: QtWidgets.QMessageBox.about(
            self, "SORI", f"SORI v{__version__}\n도로교통소음 예측 — RLS-19 음원 + ISO 9613-2 / RLS-19 전파\n\n※ {AI_NOTICE}\n"
                          "계산 결과의 검토·검증 책임은 사용자에게 있음."))

        sb = self.statusBar()
        self.lbl_status = QtWidgets.QLabel("준비")
        self.bar = QtWidgets.QProgressBar(maximumWidth=220, maximum=1000, textVisible=False)
        self.lbl_xy = QtWidgets.QLabel("")
        sb.addWidget(self.lbl_status, 1)
        sb.addPermanentWidget(self.lbl_xy)
        sb.addPermanentWidget(self.bar)
        sb.addPermanentWidget(QtWidgets.QLabel(f"※ {AI_NOTICE}"))
        self.resizeDocks([self.findChild(QtWidgets.QDockWidget, "프로젝트")], [260], Qt.Horizontal)
        self.resizeDocks([self.findChild(QtWidgets.QDockWidget, "속성")], [300], Qt.Horizontal)
        self.resizeDocks([self.findChild(QtWidgets.QDockWidget, "계산·결과 표")], [230], Qt.Vertical)

    def _dock(self, title, w, area):
        d = QtWidgets.QDockWidget(title, self)
        d.setObjectName(title)
        d.setWidget(w)
        d.setFeatures(QtWidgets.QDockWidget.DockWidgetMovable | QtWidgets.QDockWidget.DockWidgetFloatable)
        self.addDockWidget(area, d)
        return d

    def _run_panel(self):
        w = QtWidgets.QWidget()
        f = QtWidgets.QGridLayout(w)
        self.ed_cfg = QtWidgets.QLineEdit(readOnly=True, placeholderText="파일 ▸ 설정 파일 열기")
        self.ed_out = QtWidgets.QLineEdit()
        b_out = QtWidgets.QPushButton("찾아보기…")
        b_out.clicked.connect(self._pick_out_dir)
        f.addWidget(QtWidgets.QLabel("설정 파일"), 0, 0)
        f.addWidget(self.ed_cfg, 0, 1, 1, 3)
        f.addWidget(QtWidgets.QLabel("결과 폴더"), 1, 0)
        f.addWidget(self.ed_out, 1, 1, 1, 2)
        f.addWidget(b_out, 1, 3)
        row = QtWidgets.QHBoxLayout()
        self.chk = {}
        for k, v in TARGETS.items():
            self.chk[k] = QtWidgets.QCheckBox(v, checked=True)
            row.addWidget(self.chk[k])
        self.chk_plots = QtWidgets.QCheckBox("그림·3D 뷰어 만들기", checked=False)
        row.addWidget(self.chk_plots)
        row.addStretch(1)
        self.btn_run = QtWidgets.QPushButton("▶ 계산 실행")
        self.btn_run.setDefault(True)
        self.btn_run.clicked.connect(self.start)
        self.btn_check = QtWidgets.QPushButton("자가 점검")
        self.btn_check.clicked.connect(self.start_check)
        row.addWidget(self.btn_run)
        row.addWidget(self.btn_check)
        f.addLayout(row, 2, 0, 1, 4)
        f.addWidget(QtWidgets.QLabel("계산은 주간·야간을 모두 수행함. 전파 방법·반사 차수는 설정 파일 값(속성 창에서 확인)."),
                    3, 0, 1, 4)
        f.setRowStretch(4, 1)
        f.setColumnStretch(1, 1)
        return w

    # ------------------------------------------------------------ 열기
    def pick_cfg(self):
        p, _ = QtWidgets.QFileDialog.getOpenFileName(self, "설정 파일 선택", str(Path.home()),
                                                     "SORI 설정 (*.yaml *.yml);;모든 파일 (*)")
        if p:
            self.open_config(p)

    def pick_out(self):
        p = QtWidgets.QFileDialog.getExistingDirectory(self, "결과 폴더 선택", str(default_output_dir()))
        if p:
            self.open_results(p)

    def _pick_out_dir(self):
        p = QtWidgets.QFileDialog.getExistingDirectory(self, "결과 저장 폴더", self.ed_out.text() or str(Path.home()))
        if p:
            self.ed_out.setText(p)

    def load_example(self):
        """앱에 들어 있는 기준 사례(가상 교차로, 실제 지역 아님)를 문서/SORI 아래에 복사해 엶"""
        import shutil
        dst = default_output_dir() / "예제_가상교차로"
        if not (dst / "config.yaml").exists():
            shutil.copytree(resource_path("tests", "reference", "synthetic", "input"), dst, dirs_exist_ok=True)
        log.info("예제(가상 교차로, 실제 지역 아님): %s", dst)
        self.open_config(dst / "config.yaml", dst / "output")

    def open_config(self, path, out=None):
        from sori import config as C
        path = Path(path).resolve()
        try:
            cfg = C.load(path)
        except Exception as e:
            log.error("설정 파일 오류: %s", e)
            return self._err(f"설정 파일을 읽을 수 없습니다.\n{e}")
        self.cfg_path, self.cfg = path, cfg
        out = Path(out) if out else path.parent / "output"
        self.ed_cfg.setText(str(path))
        self.ed_out.setText(str(out))
        G.save_settings(last_config=path, last_out=out.resolve())   # 설정과 결과 폴더를 짝으로 기억
        log.info("설정 파일: %s", path)
        self.open_results(out, quiet=True)

    def open_results(self, out, quiet=False):
        """결과 폴더의 CSV·래스터·도형을 읽어 트리·지도·표를 채움(없으면 계산 범위만 표시)"""
        out = Path(out)
        self.out = out
        if not self.ed_out.text():
            self.ed_out.setText(str(out))
        R = {}
        try:
            for name, fn in (("grid", "grid_result.csv"), ("facade", "facade_result.csv"),
                             ("points", "point_result.csv"), ("terms", "path_terms.csv")):
                if (out / fn).exists():
                    R[name] = pd.read_csv(out / fn)
            if (out / "facades.csv").exists():
                R["facades"] = pd.read_csv(out / "facades.csv", index_col="fid")
            for name in ("run_info", "build_info", "run_conditions"):
                if (out / f"{name}.json").exists():
                    R[name] = json.loads((out / f"{name}.json").read_text(encoding="utf-8"))
            import geopandas as gpd
            for name in ("buildings", "roads"):
                if (out / f"{name}.gpkg").exists():
                    R[name] = gpd.read_file(out / f"{name}.gpkg")
        except Exception as e:
            log.error("결과 읽기 오류: %s", e)
        if not R and not quiet:
            self._err(f"이 폴더에서 SORI 결과를 찾지 못했습니다.\n{out}")
        self.R = R
        self.view_bid = None
        if self.cfg is None and (out / "config_used.yaml").exists():
            log.info("결과 폴더만 열었음 — 설정은 config_used.yaml 을 속성에 표시(입력 경로는 원래 설정 폴더 기준)")
        self.m_points.set(R.get("points"))
        self.m_terms.set(R.get("terms"))
        fr = R.get("facade")
        self.m_facade.set(fr.sort_values("LrT", ascending=False).head(200).reset_index(drop=True)
                          if fr is not None and len(fr) else None)
        self.cb_h.blockSignals(True)
        self.cb_h.clear()
        if "grid" in R:
            for h in sorted(R["grid"].h.unique()):
                self.cb_h.addItem(f"{h:g} m", float(h))
        self.cb_h.blockSignals(False)
        self._fill_tree()
        self.redraw()
        self._show_props(self._summary_props())
        if R:
            log.info("결과 폴더: %s (%s)", out, ", ".join(k for k in R if k in ("grid", "facade", "points", "terms")))

    # ------------------------------------------------------------ 트리·속성
    def _fill_tree(self):
        t = self.tree
        t.clear()
        name = (self.cfg or {}).get("project", {}).get("name") or (self.out.name if self.out else "프로젝트")
        top = QtWidgets.QTreeWidgetItem([name])
        top.setData(0, ROLE, ("summary", None))
        t.addTopLevelItem(top)
        if self.cfg:
            sc = QtWidgets.QTreeWidgetItem(top, [f"시나리오: {self.cfg['project'].get('scenario') or '현황'}"])
            sc.setData(0, ROLE, ("config", None))
            inp = QtWidgets.QTreeWidgetItem(top, ["입력 자료"])
            i = self.cfg["input"]
            for label, val in (("수치지도 폴더", ", ".join(map(str, i["map"].get("folders") or []))),
                               ("지형(DEM)", i["dem"].get("path")), ("교통량", i["traffic"].get("path")),
                               ("교차로", (i.get("junctions") or {}).get("path")),
                               ("지면 G 다각형", i["ground"].get("soft_polygons"))):
                it = QtWidgets.QTreeWidgetItem(inp, [label])
                it.setData(0, ROLE, ("kv", [(label, val or "(없음)")]))
            for k, v in (("격자 범위·간격", "grid"), ("전파·반사", "prop")):
                it = QtWidgets.QTreeWidgetItem(sc, [k])
                it.setData(0, ROLE, ("config", v))
        res = QtWidgets.QTreeWidgetItem(top, ["실행 결과" + ("" if self.R else " (없음)")])
        res.setData(0, ROLE, ("summary", None))
        if "grid" in self.R:
            g = QtWidgets.QTreeWidgetItem(res, ["격자 소음지도"])
            for h in sorted(self.R["grid"].h.unique()):
                for k in KEYS:
                    it = QtWidgets.QTreeWidgetItem(g, [f"h = {h:g} m · {k}"])
                    it.setData(0, ROLE, ("grid", (float(h), k)))
        for key, label, tab in (("facade", "건물 벽면", 4), ("points", "점 수음점", 2), ("terms", "감쇠항 path_terms", 3)):
            if key in self.R:
                it = QtWidgets.QTreeWidgetItem(res, [f"{label} ({len(self.R[key]):,}행)"])
                it.setData(0, ROLE, ("tab", tab))
        if "run_info" in self.R:
            it = QtWidgets.QTreeWidgetItem(res, ["계산 정보 run_info.json"])
            it.setData(0, ROLE, ("run_info", None))
        t.expandAll()

    def _on_tree(self, item):
        kind, val = item.data(0, ROLE) or (None, None)
        if kind == "grid":
            self.cb_h.setCurrentIndex(self.cb_h.findData(val[0]))
            self.cb_key.setCurrentIndex(self.cb_key.findData(val[1]))
        elif kind == "tab":
            self.tabs.setCurrentIndex(val)
        elif kind == "kv":
            self._show_props(val)
        elif kind == "config":
            self._show_props(self._config_props(val))
        elif kind == "run_info":
            ri = self.R["run_info"]
            self._show_props([(k, json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
                              for k, v in ri.items() if k != "notice"])
        else:
            self._show_props(self._summary_props())

    def _config_props(self, part=None):
        c = self.cfg
        if not c:
            return []
        p, r = c["propagation"], c["receivers"]
        rf = p["reflection"]
        grid = [("격자 중심", r.get("center")), ("격자 크기 m", r["grid"].get("size")), ("격자 간격 m", r["grid"].get("step")),
                ("격자 높이 m", r["grid"].get("heights")), ("벽면 계산", r["facade"].get("enabled")),
                ("벽면 반경 m", r["facade"].get("radius")), ("층 높이 m", c["model"].get("floor_height"))]
        prop = [("전파 방법", c.get("propagation_method") or "iso9613_2"),
                ("반사 차수 격자/벽면/점", f"{rf.get('max_order_grid')} / {rf.get('max_order_facade')} / {rf.get('max_order_point')}"),
                ("반사계수 ρ", rf.get("rho")), ("지면 계산법", c["input"]["ground"].get("method")),
                ("기온 °C", p.get("temperature_C")), ("상대습도 %", p.get("humidity_pct")),
                ("주 탐색 거리 m", p.get("max_dist_main")), ("감쇠항 CSV", p.get("terms_csv"))]
        head = [("설정 파일", self.cfg_path), ("프로젝트", c["project"].get("name")),
                ("시나리오", c["project"].get("scenario") or "현황"), ("좌표계", f"EPSG:{c.get('crs')}")]
        return {"grid": grid, "prop": prop}.get(part) or head + prop + grid

    def _summary_props(self):
        rows = self._config_props()[:4] if self.cfg else []
        rows.append(("결과 폴더", self.out or "(없음)"))
        ri = self.R.get("run_info")
        if ri:
            rows += [("전파(결과)", ri.get("propagation")), ("계산 시각", ri.get("time"))]
            for k, v in (ri.get("results_range") or {}).items():
                rows.append((f"범위 {k}", "  ".join(f"{t} {lo:.1f}~{hi:.1f}" for t, (lo, hi) in v.items())))
        bi = self.R.get("build_info")
        if bi and "roads_with_fallback_traffic" in bi:
            rows.append(("가정 교통량 쓴 도로 조각", bi["roads_with_fallback_traffic"]))
        return rows

    def _show_props(self, rows):
        self.props.setRowCount(0)
        for k, v in rows:
            i = self.props.rowCount()
            self.props.insertRow(i)
            self.props.setItem(i, 0, QtWidgets.QTableWidgetItem(str(k)))
            it = QtWidgets.QTableWidgetItem("" if v is None else str(v))
            it.setToolTip(it.text())
            self.props.setItem(i, 1, it)
        self.props.resizeColumnToContents(0)

    # ------------------------------------------------------------ 지도
    def redraw(self):
        if self.view_bid is not None:
            return self._draw_facade(self.view_bid)
        self.act_back.setEnabled(False)
        R, crs = self.R, (self.cfg or {}).get("crs")
        extent = None
        if self.cfg:
            from sori.config import grid_bbox
            try:
                extent = grid_bbox(self.cfg)
            except Exception:
                extent = None
        h, key = self.cb_h.currentData(), self.cb_key.currentData()
        if "grid" in R and h is not None:
            Gh = R["grid"][R["grid"].h == h]
            xs, ys = np.sort(Gh.x.unique()), np.sort(Gh.y.unique())
            step = float(np.min(np.diff(xs))) if len(xs) > 1 else 1.0
            A = np.full((len(ys), len(xs)), np.nan)
            A[np.searchsorted(ys, Gh.y), np.searchsorted(xs, Gh.x)] = Gh[key]
            ext = (xs[0] - step / 2, xs[-1] + step / 2, ys[0] - step / 2, ys[-1] + step / 2)
            meth = (R.get("run_info") or {}).get("outputs", {}).get("grid", "")
            title = f"격자 소음지도 {KEYS[key]} · 높이 {h:g} m · {meth}  (최대 {np.nanmax(A):.1f} dB)"
            self.map.draw((A, ext), extent, R.get("buildings"), R.get("roads"), title, crs)
            self._grid_now = (Gh, key)
        elif R.get("buildings") is not None or extent is not None:
            self.map.draw(None, extent, R.get("buildings"), R.get("roads"),
                          "입력 지도 · 계산 범위(파란 점선)" + ("" if R else " — 아직 계산 결과 없음"), crs)
            self._grid_now = None
        else:
            self.map.message("설정 파일(config.yaml)을 열거나 결과 폴더를 여세요.")
            self._grid_now = None

    # ------------------------------------------------------------ 벽면 지도
    def _building_at(self, x, y):
        B = self.R.get("buildings")
        if B is None or not len(B):
            return None
        from shapely.geometry import Point
        hit = B[B.geometry.contains(Point(x, y))]
        return None if hit.empty else int(hit.iloc[0].bid)

    def show_facade(self, bid):
        FR = self.R.get("facade")
        if FR is None or self.R.get("facades") is None or not (FR.bid == bid).any():
            self.lbl_status.setText(f"건물 {bid}: 벽면 계산 결과 없음(벽면 계산 반경 밖이거나 벽면 계산을 하지 않음)")
            return False
        self.view_bid = bid
        self.redraw()
        return True

    def show_grid(self):
        self.view_bid = None
        self.redraw()

    def _draw_facade(self, bid):
        R, key = self.R, self.cb_key.currentData()
        B = R["buildings"]
        b = B[B.bid == bid].iloc[0]
        F = R["facades"][R["facades"].bid == bid].sort_index()
        FR = R["facade"][R["facade"].bid == bid]
        x0, y0, x1, y1 = b.geometry.bounds
        near = B.cx[x0 - 80:x1 + 80, y0 - 80:y1 + 80]
        roads = R.get("roads")
        if roads is not None and len(roads):
            roads = roads.cx[x0 - 80:x1 + 80, y0 - 80:y1 + 80]
        name = f" {b.bname}" if isinstance(b.get("bname"), str) and b.bname else ""
        title = (f"건물 {bid}{name} · {b.kind} · {int(b.floors)}층 — 벽면 소음지도 {KEYS[key]}  "
                 f"(최대 {FR[key].max():.1f} dB, 수음점 {len(FR):,}개)")
        self.map.draw_facade(b, F, FR, key, title, near[near.bid != bid], roads, (self.cfg or {}).get("crs"))
        self.act_back.setEnabled(True)
        self.m_facade.set(FR.sort_values(key, ascending=False).reset_index(drop=True))
        rows = [("건물 번호", str(bid)), ("종류", str(b.kind)), ("층수", str(int(b.floors))), ("높이 m", f"{b.H:.1f}"),
                ("벽 수(계산)", str(FR.fid.nunique())), ("수음점", f"{len(FR):,}"),
                (f"{key} 최대 dB", f"{FR[key].max():.1f}"), (f"{key} 최소 dB", f"{FR[key].min():.1f}")]
        lim = ((self.cfg or {}).get("plots") or {}).get("limits") or {}
        if key in lim:
            n = int((FR[key] > float(lim[key])).sum())
            rows.append((f"기준 {lim[key]} dB 초과", f"{n:,}점"))
        self._show_props(rows)
        self.lbl_status.setText(f"건물 {bid} 벽면 소음지도 — 다른 건물을 클릭하면 바뀜, '◀ 격자 지도'로 돌아감")

    def _on_elev_click(self, s, fl):
        """펼친 그림 클릭: 그 위치(둘레 거리 s, 층)의 수음점 값"""
        if self.view_bid is None:
            return
        F = self.R["facades"][self.R["facades"].bid == self.view_bid].sort_index()
        FR = self.R["facade"][self.R["facade"].bid == self.view_bid]
        s0 = dict(zip(F.index, np.r_[0.0, np.cumsum(F.L.values)[:-1]]))
        sa = FR.fid.map(s0) + FR.a * FR.fid.map(F.L)
        sb = FR.fid.map(s0) + FR.b * FR.fid.map(F.L)
        hit = FR[(sa <= s) & (s < sb) & (FR.floor == int(fl) + 1)]
        if hit.empty:
            return
        r = hit.iloc[0]
        n = list(F.index).index(r.fid) + 1
        rows = [("벽 번호", str(n)), ("층", str(int(r.floor))), ("X", f"{r.x:.1f}"), ("Y", f"{r.y:.1f}"), ("Z m", f"{r.z:.2f}"),
                ("LrT dB", f"{r.LrT:.1f}"), ("LrN dB", f"{r.LrN:.1f}")]
        rows += [(c, f"{r[c]:.1f}") for c in FR.columns if c.startswith("LrT_") and c != "LrT_cmp" and pd.notna(r[c])]
        self._show_props(rows)
        self.lbl_status.setText(f"건물 {self.view_bid} 벽 {n} · {int(r.floor)}층: LrT {r.LrT:.1f} dB, LrN {r.LrN:.1f} dB")

    def save_map(self):
        """지금 보이는 지도(격자 또는 벽면)를 파일로 저장. 기본 이름은 결과 파일명 규칙(sori.naming)"""
        if self.map.ax is None:
            return self._err("저장할 지도가 없습니다. 결과를 먼저 여세요.")
        key, h = self.cb_key.currentData(), self.cb_h.currentData()
        kind, extra = ("grid", f"_h{h:g}m" if h is not None else "") if self.view_bid is None \
            else ("facade", f"_건물{self.view_bid}")
        try:
            from sori.naming import rule_name
            name = rule_name(self.cfg, kind, key, extra)
        except Exception:
            name = f"SORI_{kind}_{key}{extra}"
        base = Path(self.out or default_output_dir()) / f"{name}.png"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "지도 저장", str(base), "PNG 그림 (*.png);;PDF 문서 (*.pdf)")
        if path:
            self.save_map_to(path)

    def save_map_to(self, path):
        path = Path(path)
        if path.suffix.lower() not in (".png", ".pdf"):
            path = path.with_suffix(".png")
        self.map.fig.savefig(path, dpi=200)
        log.info("지도 저장: %s", path)
        self.lbl_status.setText(f"지도 저장: {path}")
        return path

    def _on_map_click(self, x, y):
        bid = self._building_at(x, y)
        if bid is not None and self.show_facade(bid):        # 건물 클릭 → 벽면 소음지도
            return
        if self.view_bid is not None or not getattr(self, "_grid_now", None):
            return
        Gh, key = self._grid_now
        i = int(np.argmin((Gh.x.values - x) ** 2 + (Gh.y.values - y) ** 2))
        r = Gh.iloc[i]
        rows = [("격자점 X", f"{r.x:.1f}"), ("격자점 Y", f"{r.y:.1f}"), ("지면 Z m", f"{r.z:.2f}"), ("높이 h m", f"{r.h:g}"),
                ("LrT dB", f"{r.LrT:.1f}"), ("LrN dB", f"{r.LrN:.1f}")]
        rows += [(c, f"{r[c]:.1f}") for c in Gh.columns if c.startswith("LrT_") and pd.notna(r[c])]
        self._show_props(rows)
        self.lbl_status.setText(f"격자점 ({r.x:.1f}, {r.y:.1f}) {key} {r[key]:.1f} dB")

    # ------------------------------------------------------------ 계산 실행
    def start(self):
        if not self.cfg_path:
            return self._err("설정 파일(config.yaml)을 먼저 여세요.")
        out = self.ed_out.text().strip()
        if not out:
            return self._err("결과 폴더를 지정하세요.")
        which = [k for k, c in self.chk.items() if c.isChecked()]
        if not which:
            return self._err("계산 대상을 하나 이상 고르세요.")
        if Path(out).is_dir() and any(Path(out).iterdir()):
            if QtWidgets.QMessageBox.question(self, "SORI", f"결과 폴더에 이미 파일이 있습니다.\n{out}\n"
                                                            "같은 이름의 결과 파일을 덮어쓸까요?") != QtWidgets.QMessageBox.Yes:
                return
        G.save_settings(last_config=self.cfg_path, last_out=Path(out).resolve())
        self.tabs.setCurrentIndex(1)
        self._launch(Worker(self.bridge, "run", cfg=str(self.cfg_path), out=out, which=which,
                            plots=self.chk_plots.isChecked()), "계산 중… (창을 닫지 마세요)")
        self._pending_out = out

    def start_check(self):
        self.tabs.setCurrentIndex(1)
        self._launch(Worker(self.bridge, "check"), "자가 점검 중… (좌표 변환 + 기준 사례 계산, 1~2분)")
        self.bar.setMaximum(0)                            # 진행률 모름 → 움직이는 막대

    def _launch(self, w, text):
        self._busy(True)
        self.bar.setMaximum(1000)
        self.bar.setValue(0)
        self.lbl_status.setText(text)
        self.worker = w
        w.start()

    def _busy(self, on):
        for x in (self.btn_run, self.btn_check, self.act_run, self.act_check):
            x.setEnabled(not on)

    def _on_log(self, level, text):
        self.logbox.appendPlainText(("⚠ " if level >= logging.WARNING else "") + text)

    def _on_prog(self, frac, text):
        self.bar.setValue(int(frac * 1000))
        self.lbl_status.setText(text)

    def _on_done(self, kind, r):
        self._busy(False)
        self.bar.setMaximum(1000)
        self.bar.setValue(1000)
        if kind == "run":
            self.lbl_status.setText(f"계산 완료 ({r['elapsed_s'] / 60:.1f}분). 결과: {self._pending_out}")
            self.open_results(self._pending_out)
        else:
            self.lbl_status.setText(f"자가 점검 통과 — 기준 사례 최대 차이 {r['max_diff_dB']:.2g} dB "
                                    f"(허용 0.01 dB, 프로젝트 임의 설정). 보고서: {r['report_path']}")

    def _on_fail(self, text):
        self._busy(False)
        self.bar.setMaximum(1000)
        self.bar.setValue(0)
        self.lbl_status.setText(text)
        self._err(text + "\n자세한 내용은 '기록' 탭을 확인하세요.")

    def _err(self, text):
        QtWidgets.QMessageBox.critical(self, "SORI", text)

    def closeEvent(self, e):
        if self.worker is not None and self.worker.isRunning():
            if QtWidgets.QMessageBox.question(self, "SORI", "계산이 진행 중입니다. 그래도 끝낼까요?") != QtWidgets.QMessageBox.Yes:
                return e.ignore()
        e.accept()


def _out_for(cfg_path, out):
    """기억된 결과 폴더가 이 설정 파일로 계산한 것인지(run_conditions.json 의 config_path) 확인.
    다른 설정의 결과(예: 예제)면 None → 설정 파일 옆 output 을 씀(다른 프로젝트 결과가 섞여 보이지 않게)"""
    if not out:
        return None
    rc = Path(out) / "run_conditions.json"
    try:
        used = json.loads(rc.read_text(encoding="utf-8")).get("config_path")
        return out if used and Path(used).resolve() == Path(cfg_path).resolve() else None
    except (OSError, ValueError):
        return out if Path(out).resolve() == (Path(cfg_path).resolve().parent / "output") else None


def restore(win, config=None, out=None):
    """--config 로 받은 설정 파일, 없으면 지난번에 쓴 설정 파일을 미리 엶"""
    st = G.load_settings()
    p = Path(config) if config else (Path(st["last_config"]) if st.get("last_config") else None)
    if p and p.is_file():
        win.open_config(p, out or (_out_for(p, st.get("last_out")) if not config else None))
    elif out:
        win.open_results(out)


def main(config=None, out=None, snapshot=None):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
    app.setApplicationName("SORI")
    win = MainWindow()
    win.show()
    restore(win, config, out)
    if snapshot:                                      # 화면 확인용: 그린 뒤 PNG 저장하고 끝냄
        for _ in range(20):
            app.processEvents()
        win.map.canvas.draw()
        app.processEvents()
        win.grab().save(str(snapshot))
        return 0
    return app.exec()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(prog="sori_qt")
    ap.add_argument("--config")
    ap.add_argument("--out")
    ap.add_argument("--snapshot")
    a = ap.parse_args([x for x in sys.argv[1:] if not x.startswith("-psn_")])
    sys.exit(main(a.config, a.out, a.snapshot))
