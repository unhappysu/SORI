# -*- coding: utf-8 -*-
"""
SORI 평지 샌드박스 화면 — 평지에 음원·방음벽·수음점을 놓고 마우스로 옮기며 회절(삽입손실)을 바로 확인.
계산은 sori/sandbox.py (SORI 본 계산과 같은 경로 기하·감쇠 함수).

  python sori_sandbox.py                       화면 실행
  python sori_sandbox.py --scene 장면.json     저장한 장면 열기
  python sori_sandbox.py --snapshot 그림.png [--kind point|line|road]   화면을 그려 PNG 로 저장하고 끝냄(확인용)

조작
  - 점(음원·수음점)·선 끝점(●)·선 가운데(■)를 끌어 옮김. 0.5 m 단위로 맞춤(Shift 누르면 자유)
  - 끄는 동안 수음점 값·단면이 바로 바뀌고, 놓으면 소음지도를 다시 계산
  - 위 도구 줄 '추가' → 지도 클릭(점 1번 / 선 2번)으로 새 객체. 객체를 클릭하면 왼쪽 '선택 객체' 칸에서 값 수정
※ 본 소프트웨어는 Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import argparse
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib import colors as mcolors  # noqa: E402

from sori import sandbox as SB  # noqa: E402
from sori.rls19 import SURFACES  # noqa: E402

try:
    from sori.plots import set_font
    set_font()
except Exception:  # pragma: no cover
    pass
import matplotlib  # noqa: E402
matplotlib.rcParams["axes.unicode_minus"] = False

PICK_PX = 10
SNAP = 0.5
# 소음지도 등급(5 dB) — 국내 소음지도 관례 색과 비슷한 순서
L_LEVELS = [35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85]
L_COLORS = ["#a0d6b4", "#4daf4a", "#b8e186", "#ffffb3", "#fee08b", "#fdae61", "#f46d43", "#d73027",
            "#a50026", "#7b3294"]
IL_LEVELS = [-2, 0, 2, 4, 6, 8, 10, 12, 15, 20, 25]

FIELDS = {   # 선택 객체 편집 칸: (키, 이름, 최소, 최대, 소수)
    "point": [("x", "x [m]", -1e4, 1e4, 1), ("y", "y [m]", -1e4, 1e4, 1), ("h", "높이 [m]", 0, 100, 2),
              ("Lw", "Lw [dB(A)]", 0, 200, 1)],
    "line": [("x1", "x1", -1e4, 1e4, 1), ("y1", "y1", -1e4, 1e4, 1), ("x2", "x2", -1e4, 1e4, 1),
             ("y2", "y2", -1e4, 1e4, 1), ("h", "높이 [m]", 0, 100, 2), ("Lw_per_m", "Lw′ [dB(A)/m]", 0, 200, 1)],
    "road": [("x1", "x1", -1e4, 1e4, 1), ("y1", "y1", -1e4, 1e4, 1), ("x2", "x2", -1e4, 1e4, 1),
             ("y2", "y2", -1e4, 1e4, 1), ("M", "M [대/h]", 0, 1e5, 0), ("p1", "p1 Lkw1 [%]", 0, 100, 1),
             ("p2", "p2 Lkw2 [%]", 0, 100, 1), ("v_pkw", "v Pkw [km/h]", 30, 160, 0),
             ("v_lkw", "v Lkw [km/h]", 30, 120, 0), ("g", "종단경사 [%]", -12, 12, 1),
             ("offset", "음원선 오프셋 [m]", 0, 50, 2)],
    "barrier": [("h", "높이 [m]", 0, 50, 2), ("x1", "x1", -1e4, 1e4, 1), ("y1", "y1", -1e4, 1e4, 1),
                ("x2", "x2", -1e4, 1e4, 1), ("y2", "y2", -1e4, 1e4, 1)],   # 길이 칸은 높이 바로 아래(_rebuild_editor)
    "receiver": [("x", "x [m]", -1e4, 1e4, 1), ("y", "y [m]", -1e4, 1e4, 1), ("h", "높이 [m]", 0, 200, 2)],
}


def _snap(v, free):
    return v if free else round(v / SNAP) * SNAP


class _Status:
    """QStatusBar 대신 쓰는 한 줄 글(앱 안 페이지·단독 창 공통)"""
    def __init__(self, label):
        self.label = label

    def showMessage(self, text):
        self.label.setText(text)


class SandboxPage(QtWidgets.QWidget):
    """샌드박스 화면 본체. SORI 앱(sori_qt) 홈 ▸ '평지 샌드박스' 페이지로도, 단독 창(Sandbox)으로도 씀.
    back 신호 = '⌂ 홈' 단추(앱 안에서만 보임)"""
    back = QtCore.Signal()

    def __init__(self, scene=None, home_button=False):
        super().__init__()
        self._home_button = home_button
        self.sc = scene or SB.default_scene("point")
        self.sel = ("receiver", 0) if self.sc["receivers"] else None   # (분류, 번호)
        self.drag = None            # (분류, 번호, 손잡이, 시작 마우스, 시작 값)
        self.adding = None          # (종류, [클릭 점])
        self.map = None
        self._busy = False
        self._build()
        self.refresh(full=True)

    # ------------------------------------------------------------------ 화면 구성
    def _build(self):
        tb = QtWidgets.QToolBar("도구")
        if self._home_button:
            tb.addAction("⌂ 홈", self.back.emit)
            tb.addSeparator()
            t = QtWidgets.QLabel("평지 샌드박스 ")
            t.setStyleSheet("font-size: 16px; font-weight: 800; color: #222222; padding: 0 6px;")
            tb.addWidget(t)
            tb.addSeparator()
        self.cmb_ex = QtWidgets.QComboBox()
        for k, t in (("point", "① 점음원"), ("line", "② 선음원"), ("road", "③ RLS-19 도로"), ("empty", "빈 평지")):
            self.cmb_ex.addItem(t, k)
        tb.addWidget(QtWidgets.QLabel(" 예제 "))
        tb.addWidget(self.cmb_ex)
        tb.addAction("불러오기", self.load_example)
        tb.addSeparator()
        tb.addWidget(QtWidgets.QLabel(" 추가: "))
        for k, t in (("point", "점음원"), ("line", "선음원"), ("road", "도로"), ("barrier", "방음벽"),
                     ("receiver", "수음점")):
            tb.addAction(t, lambda k=k: self.start_add(k))
        tb.addAction("선택 삭제", self.delete_sel)
        tb.addSeparator()
        tb.addAction("장면 저장…", self.save_scene)
        tb.addAction("장면 열기…", self.open_scene)
        tb.addAction("그림 저장…", self.save_png)
        tb.addAction("지도 CSV…", self.save_csv)

        # 왼쪽: 설정 + 선택 객체
        left = QtWidgets.QWidget()
        lv = QtWidgets.QVBoxLayout(left)
        gb = QtWidgets.QGroupBox("계산 조건")
        f = QtWidgets.QFormLayout(gb)
        self.w_meth = QtWidgets.QComboBox()
        self.w_meth.addItem("ISO 9613-2", "iso9613_2"); self.w_meth.addItem("RLS-19 (3.5)", "rls19")
        self.w_spec = QtWidgets.QComboBox()
        self.w_spec.addItem("A 가중 단일값(500 Hz)", "A500"); self.w_spec.addItem("옥타브 63–8k", "octave")
        self.w_specv = QtWidgets.QLineEdit()
        self.w_specv.setToolTip("옥타브 63·125·250·500·1k·2k·4k·8k Hz 상대 A 가중 레벨 [dB] (쉼표로). 기본값은 가정값")
        self.w_G = self._dspin(0, 1, 2, 0.1)
        self.w_alt = QtWidgets.QCheckBox("ISO 지면 대안법(식 10)")
        self.w_refl = QtWidgets.QCheckBox("방음벽 면 1차 반사")
        self.w_rloss = self._dspin(0, 30, 1, 0.5)
        self.w_lat = QtWidgets.QCheckBox("측면 회절(ISO 식 13)")
        self.w_C2 = self._dspin(1, 80, 0, 1)
        self.w_seg = self._dspin(0.1, 20, 1, 0.5)
        self.w_res = self._dspin(0.5, 20, 1, 0.5)
        self.w_gh = self._dspin(0.1, 100, 1, 0.5)
        self.w_show = QtWidgets.QComboBox()
        for k, t in (("after", "방음벽 설치 후 L"), ("before", "설치 전 L₀"), ("il", "삽입손실 IL = L₀ − L"),
                     ("none", "지도 끔")):
            self.w_show.addItem(t, k)
        for lab, w in (("전파 방법", self.w_meth), ("주파수", self.w_spec), ("옥타브 스펙트럼", self.w_specv),
                       ("지면계수 G", self.w_G), ("", self.w_alt), ("", self.w_refl), ("반사 손실 [dB]", self.w_rloss),
                       ("", self.w_lat), ("C2 (식 14)", self.w_C2), ("선음원 조각 [m]", self.w_seg),
                       ("지도 격자 [m]", self.w_res), ("지도 높이 [m]", self.w_gh), ("지도 표시", self.w_show)):
            f.addRow(lab, w)
        lv.addWidget(gb)
        self.gb_sel = QtWidgets.QGroupBox("선택 객체")
        self.f_sel = QtWidgets.QFormLayout(self.gb_sel)
        lv.addWidget(self.gb_sel)
        lv.addStretch(1)
        sa = QtWidgets.QScrollArea(); sa.setWidget(left); sa.setWidgetResizable(True); sa.setMinimumWidth(300)

        # 가운데: 평면 + 단면
        self.fig = Figure(figsize=(9, 8), layout="constrained")
        gs = self.fig.add_gridspec(2, 2, height_ratios=[3, 1.25], width_ratios=[40, 1])
        self.ax = self.fig.add_subplot(gs[0, 0]); self.cax = self.fig.add_subplot(gs[0, 1])
        self.axs = self.fig.add_subplot(gs[1, :])
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.canvas.setFocusPolicy(Qt.StrongFocus)
        self.canvas.mpl_connect("button_press_event", self.on_press)
        self.canvas.mpl_connect("motion_notify_event", self.on_move)
        self.canvas.mpl_connect("button_release_event", self.on_release)
        self.canvas.mpl_connect("scroll_event", self.on_scroll)

        # 오른쪽: 결과 글
        self.txt = QtWidgets.QPlainTextEdit(); self.txt.setReadOnly(True)
        self.txt.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
        self.txt.setMinimumWidth(380)

        sp = QtWidgets.QSplitter()
        sp.addWidget(sa); sp.addWidget(self.canvas); sp.addWidget(self.txt)
        sp.setStretchFactor(1, 1)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(tb)
        lay.addWidget(sp, 1)
        sl = QtWidgets.QLabel()
        sl.setStyleSheet("padding: 3px 8px; color: #444444; border-top: 1px solid #d0d0d0;")
        lay.addWidget(sl)
        self.status = _Status(sl)
        self.setFocusPolicy(Qt.StrongFocus)
        self._load_settings()
        for w in (self.w_meth, self.w_spec, self.w_show):
            w.currentIndexChanged.connect(self.on_settings)
        for w in (self.w_alt, self.w_refl, self.w_lat):
            w.toggled.connect(self.on_settings)
        for w in (self.w_G, self.w_rloss, self.w_C2, self.w_seg, self.w_res, self.w_gh):
            w.editingFinished.connect(self.on_settings)
        self.w_specv.editingFinished.connect(self.on_settings)

    def _dspin(self, lo, hi, dec, step):
        w = QtWidgets.QDoubleSpinBox(); w.setRange(lo, hi); w.setDecimals(dec); w.setSingleStep(step)
        w.setKeyboardTracking(False)
        return w

    def _load_settings(self):
        sc = self.sc
        blk = [self.w_meth, self.w_spec, self.w_show, self.w_alt, self.w_refl, self.w_lat, self.w_G, self.w_rloss,
               self.w_C2, self.w_seg, self.w_res, self.w_gh, self.w_specv]
        for w in blk:
            w.blockSignals(True)
        self.w_meth.setCurrentIndex(self.w_meth.findData(sc["method"]))
        self.w_spec.setCurrentIndex(self.w_spec.findData(sc.get("spectrum", "A500")))
        self.w_specv.setText(", ".join(f"{v:g}" for v in sc["spectrum_dBA"]))
        self.w_G.setValue(sc["ground_G"]); self.w_alt.setChecked(sc.get("ground_alt", False))
        self.w_refl.setChecked(sc["reflection"]); self.w_rloss.setValue(sc["refl_loss_dB"])
        self.w_lat.setChecked(sc["lateral"]); self.w_C2.setValue(sc["C2"]); self.w_seg.setValue(sc["seg_len"])
        self.w_res.setValue(max(sc["grid"]["res"], 2.0)); self.w_gh.setValue(sc["grid"]["h"])
        self.sc["grid"]["res"] = self.w_res.value()
        for w in blk:
            w.blockSignals(False)
        self._enable()

    def _enable(self):
        iso = self.sc["method"] != "rls19"
        self.w_spec.setEnabled(iso); self.w_alt.setEnabled(iso); self.w_lat.setEnabled(iso)
        self.w_C2.setEnabled(iso)
        self.w_specv.setEnabled(iso and self.sc.get("spectrum") == "octave")

    def on_settings(self, *_):
        sc = self.sc
        sc["method"] = self.w_meth.currentData(); sc["spectrum"] = self.w_spec.currentData()
        try:
            v = [float(t) for t in self.w_specv.text().replace(" ", "").split(",") if t]
            if len(v) == 8:
                sc["spectrum_dBA"] = v
        except ValueError:
            pass
        sc["ground_G"] = self.w_G.value(); sc["ground_alt"] = self.w_alt.isChecked()
        sc["reflection"] = self.w_refl.isChecked(); sc["refl_loss_dB"] = self.w_rloss.value()
        sc["lateral"] = self.w_lat.isChecked(); sc["C2"] = self.w_C2.value(); sc["seg_len"] = self.w_seg.value()
        sc["grid"]["res"] = self.w_res.value(); sc["grid"]["h"] = self.w_gh.value()
        self._enable()
        self.refresh(full=True)

    # ------------------------------------------------------------------ 선택 객체 편집
    def _obj(self, sel=None):
        sel = sel or self.sel
        if sel is None:
            return None
        cat, i = sel
        lst = {"source": self.sc["sources"], "barrier": self.sc["barriers"], "receiver": self.sc["receivers"]}[cat]
        return lst[i] if i < len(lst) else None

    def _rebuild_editor(self):
        while self.f_sel.rowCount():
            self.f_sel.removeRow(0)
        o = self._obj()
        if o is None:
            self.gb_sel.setTitle("선택 객체 (지도에서 클릭)")
            return
        cat = self.sel[0]
        kind = o["type"] if cat == "source" else cat
        self.gb_sel.setTitle(f"선택 객체 — {o.get('name', '')}")
        name = QtWidgets.QLineEdit(o.get("name", ""))
        name.editingFinished.connect(lambda: (o.__setitem__("name", name.text()), self.refresh(full=False)))
        self.f_sel.addRow("이름", name)
        self._spins = {}
        for key, lab, lo, hi, dec in FIELDS[kind]:
            w = self._dspin(lo, hi, dec, 10 ** -dec if dec else 1)
            w.setValue(float(o[key]))
            w.valueChanged.connect(lambda v, k=key: self._set(o, k, v))
            self.f_sel.addRow(lab, w)
            self._spins[key] = w
            if kind == "barrier" and key == "h":
                wl = self._dspin(0.5, 1e4, 1, 1.0)
                wl.setValue(math.hypot(o["x2"] - o["x1"], o["y2"] - o["y1"]))
                wl.setToolTip("가운데점·방향은 그대로 두고 양쪽 끝을 같은 만큼 늘이거나 줄임")
                wl.valueChanged.connect(lambda v: self._set_length(o, v))
                self.f_sel.addRow("길이 [m]", wl)
                self._spins["_len"] = wl
        for key in ("x1", "y1", "x2", "y2"):           # 끝점 칸을 고치면 길이 칸도 맞춤
            if kind == "barrier" and key in self._spins:
                self._spins[key].valueChanged.connect(lambda _v: self._sync_length(o))
        if kind == "road":
            cb = QtWidgets.QComboBox(); cb.addItems(list(SURFACES)); cb.setCurrentText(o.get("surface", "ref"))
            cb.currentTextChanged.connect(lambda t: self._set(o, "surface", t))
            self.f_sel.addRow("노면(표 4)", cb)
            nl = QtWidgets.QComboBox(); nl.addItem("중심선 1개", 1); nl.addItem("방향별 2개(±오프셋)", 2)
            nl.setCurrentIndex(0 if int(o.get("n_lines", 1)) == 1 else 1)
            nl.currentIndexChanged.connect(lambda i: self._set(o, "n_lines", nl.currentData()))
            self.f_sel.addRow("음원선", nl)
            self.f_sel.addRow("LW′ (식 4)", QtWidgets.QLabel(f"{SB.road_lw_prime(o):.2f} dB(A)/m"))
        if kind == "barrier":
            cb = QtWidgets.QCheckBox("흡음형(반사 없음)"); cb.setChecked(o.get("absorbing", False))
            cb.toggled.connect(lambda v: self._set(o, "absorbing", v))
            self.f_sel.addRow("", cb)
        if cat != "receiver":
            off = QtWidgets.QCheckBox("끄기(계산 제외)"); off.setChecked(o.get("off", False))
            off.toggled.connect(lambda v: self._set(o, "off", v))
            self.f_sel.addRow("", off)

    def _set_length(self, o, L):
        """방음벽 길이 L [m]: 가운데점·방향 유지, 끝점을 대칭으로 옮김"""
        cx, cy = (o["x1"] + o["x2"]) / 2, (o["y1"] + o["y2"]) / 2
        dx, dy = o["x2"] - o["x1"], o["y2"] - o["y1"]
        L0 = math.hypot(dx, dy)
        ux, uy = (dx / L0, dy / L0) if L0 > 1e-9 else (0.0, 1.0)
        o["x1"], o["y1"] = cx - ux * L / 2, cy - uy * L / 2
        o["x2"], o["y2"] = cx + ux * L / 2, cy + uy * L / 2
        for k in ("x1", "y1", "x2", "y2"):
            w = self._spins.get(k)
            if w is not None:
                w.blockSignals(True); w.setValue(o[k]); w.blockSignals(False)
        self._set(o, "x1", o["x1"])

    def _sync_length(self, o):
        w = self._spins.get("_len")
        if w is not None:
            w.blockSignals(True); w.setValue(math.hypot(o["x2"] - o["x1"], o["y2"] - o["y1"])); w.blockSignals(False)

    def _set(self, o, k, v):
        o[k] = v
        if getattr(self, "_edit_timer", None) is None:     # 칸을 연달아 바꾸면 마지막 것만 계산(0.25 s)
            self._edit_timer = QtCore.QTimer(self, singleShot=True, interval=250)
            self._edit_timer.timeout.connect(lambda: self.refresh(full=True, editor=False))
        self._edit_timer.start()

    # ------------------------------------------------------------------ 계산·그리기
    def refresh(self, full=True, editor=True):
        t = time.time()
        if full:
            self._compute_map()
        self.draw_plan(map_too=full)
        self.update_results()
        if editor:
            self._rebuild_editor()
        self.canvas.draw_idle()
        if full:
            self.status.showMessage(f"계산 {time.time() - t:.2f} s  ·  음원 조각 {SB.expand_sources(self.sc)[0].size}개"
                                    f"  ·  지도 {0 if self.map is None else self.map['after'].size}점")

    def _compute_map(self):
        show = self.w_show.currentData()
        if show == "none":
            self.map = None
            return
        self.status.showMessage("소음지도 계산 중… (처음 한 번은 컴파일로 수십 초)")
        QtWidgets.QApplication.processEvents()
        self.map = SB.compute_map(self.sc, with_before=show in ("before", "il"))

    def draw_plan(self, map_too=True):
        ax = self.ax
        xl, yl = (ax.get_xlim(), ax.get_ylim()) if getattr(self, "_view_set", False) else (None, None)
        ax.clear()
        show = self.w_show.currentData()
        if self.map is not None and show in self.map:
            Z = self.map[show]
            if show == "il":
                lev = IL_LEVELS
                cmap = matplotlib.colormaps["viridis"].resampled(len(lev) - 1)
                lab = "삽입손실 IL [dB]"
            else:
                lev = L_LEVELS
                cmap = mcolors.ListedColormap(L_COLORS)
                lab = ("L" if show == "after" else "L₀") + " [dB(A)]"
            norm = mcolors.BoundaryNorm(lev, cmap.N)
            cs = ax.contourf(self.map["xs"], self.map["ys"], Z, levels=lev, cmap=cmap, norm=norm, extend="both")
            ax.contour(self.map["xs"], self.map["ys"], Z, levels=lev, colors="k", linewidths=0.3, alpha=0.5)
            self.cax.clear()
            self.fig.colorbar(cs, cax=self.cax, label=lab)
        else:
            self.cax.clear(); self.cax.set_axis_off()
        sc = self.sc
        for i, s in enumerate(sc["sources"]):
            on = self.sel == ("source", i)
            a = 0.35 if s.get("off") else 1.0
            if s["type"] == "point":
                ax.plot(s["x"], s["y"], "*", ms=18 if on else 14, mfc="red", mec="k", alpha=a, zorder=6)
                ax.annotate(f"{s.get('name', '')}\nLw {s['Lw']:.0f}", (s["x"], s["y"]), xytext=(6, 6),
                            textcoords="offset points", fontsize=8)
            else:
                lines = [0.0]
                if s["type"] == "road" and int(s.get("n_lines", 1)) == 2:
                    lines = [s["offset"], -s["offset"]]
                    dx, dy = s["x2"] - s["x1"], s["y2"] - s["y1"]
                    L = math.hypot(dx, dy) or 1
                    w = 2 * s["offset"] + 3.5
                    ax.plot([s["x1"], s["x2"]], [s["y1"], s["y2"]], "-", color="0.55", alpha=0.5 * a,
                            lw=max(1, self._m2pt(w)), solid_capstyle="butt", zorder=2)
                dx, dy = s["x2"] - s["x1"], s["y2"] - s["y1"]
                L = math.hypot(dx, dy) or 1
                nx_, ny_ = -dy / L, dx / L
                for o in lines:
                    ax.plot([s["x1"] + o * nx_, s["x2"] + o * nx_], [s["y1"] + o * ny_, s["y2"] + o * ny_],
                            "--" if s["type"] == "road" else "-", color="red", lw=2.5 if on else 1.6, alpha=a,
                            zorder=5)
                self._handles_line(s, "red", on)
                txt = (f"{s.get('name', '')}  LW′ {SB.road_lw_prime(s):.1f}" if s["type"] == "road"
                       else f"{s.get('name', '')}  Lw′ {s['Lw_per_m']:.0f}")
                ax.annotate(txt, (s["x2"], s["y2"]), xytext=(4, -12), textcoords="offset points", fontsize=8,
                            color="darkred")
        for i, b in enumerate(sc["barriers"]):
            on = self.sel == ("barrier", i)
            ax.plot([b["x1"], b["x2"]], [b["y1"], b["y2"]], "-", color="#1f3b73" if not b.get("absorbing") else
                    "#2e8b57", lw=6 if on else 4, alpha=0.35 if b.get("off") else 1, solid_capstyle="butt", zorder=5)
            self._handles_line(b, "#1f3b73", on)
            Lb = math.hypot(b["x2"] - b["x1"], b["y2"] - b["y1"])
            ax.annotate(f"{b.get('name', '')} h={b['h']:g} m, L={Lb:.1f} m" + (" 흡음" if b.get("absorbing") else ""),
                        ((b["x1"] + b["x2"]) / 2, (b["y1"] + b["y2"]) / 2), xytext=(6, 0),
                        textcoords="offset points", fontsize=8, color="#1f3b73")
        rows = {r["name"]: r for r in getattr(self, "rows", [])}
        for i, r in enumerate(sc["receivers"]):
            on = self.sel == ("receiver", i)
            ax.plot(r["x"], r["y"], "^", ms=13 if on else 10, mfc="white" if not on else "yellow", mec="k", zorder=7)
            v = rows.get(r.get("name"))
            ax.annotate(f"{r.get('name', '')}" + (f"\n{v['L']:.1f} dB" if v else ""), (r["x"], r["y"]),
                        xytext=(7, -14), textcoords="offset points", fontsize=8, fontweight="bold")
        # 선택 수음점의 대표 경로(평면)
        det = getattr(self, "detail", None)
        if det:
            (xs, ys, _), (xr, yr, _) = det["source_point"], det["receiver"]
            ax.plot([xs, xr], [ys, yr], ":", color="k", lw=1, zorder=4)
        if self.adding and self.adding[1]:
            p = self.adding[1][0]
            ax.plot(p[0], p[1], "x", color="m", ms=12, mew=2)
        ax.set_aspect("equal")
        ax.set_xlabel("x [m]"); ax.set_ylabel("y [m]")
        g = sc["grid"]
        if xl is None:
            ax.set_xlim(g["x0"], g["x1"]); ax.set_ylim(g["y0"], g["y1"]); self._view_set = True
        else:
            ax.set_xlim(xl); ax.set_ylim(yl)
        ax.set_title(f"평면 — {self.w_meth.currentText()} · G={sc['ground_G']:g} · 지도 높이 {g['h']:g} m"
                     + ("" if not self.adding else f"   [추가: {self.adding[0]} — 지도 클릭]"), fontsize=10)
        ax.grid(alpha=0.2)

    def _m2pt(self, m):
        """데이터 m → 화면 pt(도로 폭 그리기용)"""
        p0 = self.ax.transData.transform((0, 0)); p1 = self.ax.transData.transform((m, 0))
        return abs(p1[0] - p0[0]) * 72 / self.fig.dpi

    def _handles_line(self, o, c, on):
        if not on:
            return
        self.ax.plot([o["x1"], o["x2"]], [o["y1"], o["y2"]], "o", ms=8, mfc="white", mec=c, zorder=8)
        self.ax.plot((o["x1"] + o["x2"]) / 2, (o["y1"] + o["y2"]) / 2, "s", ms=8, mfc="white", mec=c, zorder=8)

    def update_results(self):
        sc = self.sc
        self.rows = SB.receiver_table(sc)
        self.detail = None
        ri = self.sel[1] if self.sel and self.sel[0] == "receiver" else (0 if sc["receivers"] else None)
        live = [i for i, s in enumerate(sc["sources"]) if not s.get("off")]
        si = self.sel[1] if self.sel and self.sel[0] == "source" else (live[0] if live else None)
        if ri is not None and si is not None and ri < len(sc["receivers"]):
            self.detail = SB.path_detail(sc, ri, si)
            self.detail_ids = (ri, si)
        self.draw_section()
        self.txt.setPlainText(self.report())

    def draw_section(self):
        ax = self.axs
        ax.clear()
        d = self.detail
        if not d:
            ax.text(0.5, 0.5, "수음점·음원을 두면 단면이 나옵니다", ha="center", transform=ax.transAxes)
            return
        sec = d["after"]["section"]
        L = sec["length"]; zs = d["source_point"][2]; zr = d["receiver"][2]
        ax.fill_between([-0.05 * L, 1.05 * L], -1, 0, color="#c8b28e")
        for dd, h, nm in sec["crossings"]:
            ax.plot([dd, dd], [0, h], "-", color="#1f3b73", lw=5, solid_capstyle="butt")
            ax.annotate(f"{nm} {h:g} m", (dd, h), xytext=(3, 3), textcoords="offset points", fontsize=8)
        ax.plot([0, L], [zs, zr], ":", color="0.4", lw=1, label="가시선")
        hx, hy = zip(*sec["hull"])
        ax.plot(hx, hy, "-", color="crimson", lw=1.6, label="회절 경로(고무줄)")
        ax.plot(0, zs, "*", ms=14, mfc="red", mec="k"); ax.plot(L, zr, "^", ms=10, mfc="yellow", mec="k")
        g = d["after"]
        ri, si = self.detail_ids
        ttl = (f"단면 {self.sc['sources'][si].get('name', '')} → {self.sc['receivers'][ri].get('name', '')} "
               f"(가장 가까운 음원점)  d={g['d']:.2f} m")
        if g["shielded"]:
            ttl += f"  ·  경로차 z = A + B (+e) − d = {g['z']:.3f} m"
        else:
            ttl += "  ·  가시선 열림(z ≤ 0, 차폐 없음)"
        ax.set_title(ttl, fontsize=9)
        top = max([zs, zr] + [h for _, h, _ in sec["crossings"]]) * 1.3 + 1
        ax.set_xlim(-0.05 * L, 1.05 * L); ax.set_ylim(-1, top)
        ax.set_xlabel("음원에서 거리 [m]"); ax.set_ylabel("높이 [m]")
        ax.legend(loc="upper right", fontsize=8)
        ax.grid(alpha=0.25)

    def report(self):
        sc = self.sc
        out = []
        meth = "ISO 9613-2" if sc["method"] != "rls19" else "RLS-19 3.5"
        out.append(f"[{meth}]  G={sc['ground_G']:g}  반사={'켬' if sc['reflection'] else '끔'}"
                   f"  측면회절={'켬' if sc['lateral'] and sc['method'] != 'rls19' else '끔'}")
        out.append("")
        out.append(f"{'수음점':<6}{'L':>8}{'L0':>8}{'IL':>7}{'직접':>8}{'반사':>8}{'측면':>8}")
        f = lambda v: f"{v:8.1f}" if v > -100 else "       -"
        for r in self.rows:
            c = r["comp"]
            out.append(f"{r['name']:<6}{r['L']:8.2f}{r['L0']:8.2f}{r['IL']:7.2f}{f(c['direct'])}"
                       f"{f(c['reflection'])}{f(c['lateral'])}")
        out.append("  (L=설치 후, L0=설치 전, 단위 dB(A). 측면 = 측면 회절로 더해진 몫)")
        if self.rows and len(sc["sources"]) > 1:
            out.append("")
            out.append("음원별 기여 (설치 후)")
            for r in self.rows:
                out.append(f"  {r['name']}: " + ", ".join(f"{s.get('name', i)} {v:.1f}" for i, (s, v) in
                                                           enumerate(zip(sc["sources"], r["by_source"])) if v > -100))
        d = self.detail
        if d:
            ri, si = self.detail_ids
            g, g0 = d["after"], d["before"]
            out.append("")
            out.append(f"── 대표 경로 {sc['sources'][si].get('name', '')} → {sc['receivers'][ri].get('name', '')}")
            xs, ys, zs = d["source_point"]
            out.append(f"  음원점 ({xs:.1f}, {ys:.1f}, {zs:.2f})  d = {g['d']:.3f} m  h_m = {g['h_m']:.3f} m")
            if g["shielded"]:
                out.append(f"  차폐: {'다중 모서리' if g['multi_edge'] else '단일 모서리'}  A = {g['A(d_ss)']:.3f}  "
                           f"B = {g['B(d_sr)']:.3f}  e = {g['e']:.3f}")
                out.append(f"  경로차 z = {g['z']:.4f} m")
            else:
                out.append("  가시선이 열려 있음 → 회절 감쇠 없음")
            if sc["method"] == "rls19":
                t, t0 = g["terms"]["A"], g0["terms"]["A"]
                out.append("")
                out.append(f"  {'항':<8}{'설치 후':>10}{'설치 전':>10}")
                for k in t:
                    out.append(f"  {k:<8}{t[k]:10.2f}{t0[k]:10.2f}")
                out.append("  D_A = D_div + D_atm + max{D_gr; D_z} (식 11), D_z = 10 lg(3 + 80 z K_w) (식 15)")
            else:
                out.append("")
                out.append("  옥타브별 (설치 후)  — 식 (14) D_z = 10 lg(3 + C2/λ·C3·z·K_met)")
                out.append(f"  {'f[Hz]':>6}{'N=2z/λ':>8}{'A_gr':>7}{'D_z':>7}{'A_bar':>7}{'A_net':>7}{'ΔA':>7}")
                for fq, t in g["terms"].items():
                    t0 = g0["terms"][fq]
                    N = SB.fresnel_N(g["z"], fq) if g["shielded"] else 0.0
                    out.append(f"  {fq:>6}{N:8.2f}{t['A_gr']:7.2f}{t['D_z']:7.2f}{t['A_bar']:7.2f}"
                               f"{t['A_net']:7.2f}{t['A_net'] - t0['A_net']:7.2f}")
                out.append("  A_bar = D_z − A_gr (식 12, >0 일 때). ΔA = 설치 후 − 설치 전 순감쇠")
                if sc.get("spectrum") == "A500":
                    out.append("  ※ 합계는 A 가중 단일값(500 Hz 행)으로 계산")
        out.append("")
        out.append("※ 직접 경로 계산은 대표(최근접) 음원점 1개 기준. 표의 L 은 모든 조각 합.")
        return "\n".join(out)

    # ------------------------------------------------------------------ 마우스
    def _hit(self, ev):
        """화면 픽셀 기준 가장 가까운 손잡이 → (분류, 번호, 손잡이)"""
        T = self.ax.transData.transform
        best = None
        sc = self.sc

        def cand(cat, i, key, x, y):
            nonlocal best
            px, py = T((x, y))
            dd = math.hypot(px - ev.x, py - ev.y)
            if dd < PICK_PX and (best is None or dd < best[0]):
                best = (dd, (cat, i, key))

        for i, r in enumerate(sc["receivers"]):
            cand("receiver", i, "p", r["x"], r["y"])
        for cat, lst in (("source", sc["sources"]), ("barrier", sc["barriers"])):
            for i, o in enumerate(lst):
                if "x" in o:
                    cand(cat, i, "p", o["x"], o["y"])
                    continue
                cand(cat, i, "p1", o["x1"], o["y1"]); cand(cat, i, "p2", o["x2"], o["y2"])
                cand(cat, i, "mid", (o["x1"] + o["x2"]) / 2, (o["y1"] + o["y2"]) / 2)
                # 선 몸통
                a = np.array(T((o["x1"], o["y1"]))); b = np.array(T((o["x2"], o["y2"])))
                p = np.array([ev.x, ev.y]); ab = b - a
                t = np.clip(np.dot(p - a, ab) / max(np.dot(ab, ab), 1e-9), 0, 1)
                dd = np.linalg.norm(a + t * ab - p)
                if dd < PICK_PX * 0.7 and (best is None or dd + 3 < best[0]):
                    best = (dd + 3, (cat, i, "mid"))
        return best[1] if best else None

    def on_press(self, ev):
        if ev.inaxes is not self.ax or ev.button != 1:
            return
        if self.adding:
            self._add_click(ev.xdata, ev.ydata)
            return
        h = self._hit(ev)
        if h is None:
            return
        cat, i, key = h
        self.sel = (cat, i)
        o = self._obj()
        self.drag = (cat, i, key, (ev.xdata, ev.ydata), dict(o))
        self.refresh(full=False)

    def on_move(self, ev):
        if not self.drag or ev.inaxes is not self.ax or ev.xdata is None:
            return
        cat, i, key, (mx, my), o0 = self.drag
        free = bool(ev.key and "shift" in ev.key)
        dx, dy = ev.xdata - mx, ev.ydata - my
        o = self._obj((cat, i))
        if key == "p":
            o["x"] = _snap(o0["x"] + dx, free); o["y"] = _snap(o0["y"] + dy, free)
        elif key in ("p1", "p2"):
            n = key[1]
            o["x" + n] = _snap(o0["x" + n] + dx, free); o["y" + n] = _snap(o0["y" + n] + dy, free)
        else:
            ddx = _snap(dx, free); ddy = _snap(dy, free)
            for n in "12":
                o["x" + n] = o0["x" + n] + ddx; o["y" + n] = o0["y" + n] + ddy
        now = time.time()
        if now - getattr(self, "_last_move", 0) < 0.04:
            return
        self._last_move = now
        self.draw_plan(map_too=False)
        self.update_results()
        self.canvas.draw_idle()

    def on_release(self, ev):
        if self.drag:
            self.drag = None
            self.refresh(full=True)

    def on_scroll(self, ev):
        if ev.inaxes is not self.ax:
            return
        k = 0.85 if ev.button == "up" else 1 / 0.85
        x0, x1 = self.ax.get_xlim(); y0, y1 = self.ax.get_ylim()
        self.ax.set_xlim(ev.xdata + (x0 - ev.xdata) * k, ev.xdata + (x1 - ev.xdata) * k)
        self.ax.set_ylim(ev.ydata + (y0 - ev.ydata) * k, ev.ydata + (y1 - ev.ydata) * k)
        self.canvas.draw_idle()

    # ------------------------------------------------------------------ 추가·삭제·파일
    def start_add(self, kind):
        self.adding = (kind, [])
        need = "1번" if kind in ("point", "receiver") else "2번(시작·끝)"
        self.status.showMessage(f"{kind} 추가: 지도를 {need} 클릭하세요 (Esc 취소)")
        self.draw_plan(map_too=False); self.canvas.draw_idle()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape and self.adding:
            self.adding = None
            self.status.showMessage("추가 취소")
            self.draw_plan(map_too=False); self.canvas.draw_idle()
        elif e.key() in (Qt.Key_Delete, Qt.Key_Backspace) and not self.txt.hasFocus():
            self.delete_sel()
        else:
            super().keyPressEvent(e)

    def _add_click(self, x, y):
        kind, pts = self.adding
        pts.append((_snap(x, False), _snap(y, False)))
        sc = self.sc
        if kind in ("point", "receiver"):
            (x, y), = pts
            if kind == "point":
                sc["sources"].append({"type": "point", "name": f"S{len(sc['sources']) + 1}", "x": x, "y": y,
                                      "h": 0.5, "Lw": 100.0})
                self.sel = ("source", len(sc["sources"]) - 1)
            else:
                sc["receivers"].append({"name": f"R{len(sc['receivers']) + 1}", "x": x, "y": y, "h": 1.5})
                self.sel = ("receiver", len(sc["receivers"]) - 1)
        elif len(pts) == 2:
            (x1, y1), (x2, y2) = pts
            base = {"x1": x1, "y1": y1, "x2": x2, "y2": y2}
            if kind == "barrier":
                sc["barriers"].append({"name": f"W{len(sc['barriers']) + 1}", **base, "h": 4.0, "absorbing": False})
                self.sel = ("barrier", len(sc["barriers"]) - 1)
            elif kind == "line":
                sc["sources"].append({"type": "line", "name": f"L{len(sc['sources']) + 1}", **base, "h": 0.5,
                                      "Lw_per_m": 80.0})
                self.sel = ("source", len(sc["sources"]) - 1)
            else:
                sc["sources"].append({"type": "road", "name": f"도로{len(sc['sources']) + 1}", **base,
                                      "M": 1500.0, "p1": 5.0, "p2": 5.0, "v_pkw": 60.0, "v_lkw": 60.0,
                                      "surface": "ref", "g": 0.0, "n_lines": 2, "offset": 5.25})
                self.sel = ("source", len(sc["sources"]) - 1)
        else:
            self.draw_plan(map_too=False); self.canvas.draw_idle()
            return
        self.adding = None
        self.refresh(full=True)

    def delete_sel(self):
        if not self.sel:
            return
        cat, i = self.sel
        lst = {"source": self.sc["sources"], "barrier": self.sc["barriers"], "receiver": self.sc["receivers"]}[cat]
        if i < len(lst):
            lst.pop(i)
        self.sel = None
        self.refresh(full=True)

    def load_example(self):
        self.sc = SB.default_scene(self.cmb_ex.currentData())
        self.sel = ("receiver", 0) if self.sc["receivers"] else None
        self._view_set = False
        self._load_settings()
        self.refresh(full=True)

    def save_scene(self):
        p, _ = QtWidgets.QFileDialog.getSaveFileName(self, "장면 저장", "장면.json", "JSON (*.json)")
        if p:
            SB.save_scene(self.sc, p)
            self.status.showMessage(f"저장: {p}")

    def open_scene(self):
        p, _ = QtWidgets.QFileDialog.getOpenFileName(self, "장면 열기", "", "JSON (*.json)")
        if p:
            self.sc = SB.load_scene(p)
            self.sel = ("receiver", 0) if self.sc["receivers"] else None
            self._view_set = False
            self._load_settings()
            self.refresh(full=True)

    def save_png(self):
        p, _ = QtWidgets.QFileDialog.getSaveFileName(self, "그림 저장", "샌드박스.png", "PNG (*.png);;PDF (*.pdf)")
        if p:
            self.fig.savefig(p, dpi=200)
            Path(p).with_suffix(".txt").write_text(self.report(), encoding="utf-8")
            self.status.showMessage(f"저장: {p} (+ 결과 글 .txt)")

    def save_csv(self):
        if self.map is None:
            return
        p, _ = QtWidgets.QFileDialog.getSaveFileName(self, "지도 CSV", "샌드박스_지도.csv", "CSV (*.csv)")
        if not p:
            return
        X, Y = np.meshgrid(self.map["xs"], self.map["ys"])
        cols = [X.ravel(), Y.ravel(), self.map["after"].ravel()]
        head = "x,y,L_after"
        if "before" in self.map:
            cols += [self.map["before"].ravel(), self.map["il"].ravel()]
            head += ",L_before,IL"
        np.savetxt(p, np.column_stack(cols), delimiter=",", header=head, comments="", fmt="%.3f")
        self.status.showMessage(f"저장: {p}")


class Sandbox(QtWidgets.QMainWindow):
    """단독 실행 창(python sori_sandbox.py)"""
    def __init__(self, scene=None):
        super().__init__()
        self.setWindowTitle("SORI 평지 샌드박스 — 방음벽 회절 검토")
        self.page = SandboxPage(scene)
        self.setCentralWidget(self.page)
        self.resize(1600, 950)

    def __getattr__(self, k):                     # 시험·스크립트에서 w.sc, w.rows … 를 그대로 쓰게
        return getattr(self.__dict__["page"] if "page" in self.__dict__ else None, k)


def main(argv=None):
    ap = argparse.ArgumentParser(description="SORI 평지 샌드박스")
    ap.add_argument("--scene")
    ap.add_argument("--kind", default="point", choices=["point", "line", "road", "empty"])
    ap.add_argument("--snapshot", help="화면을 그려 이 PNG 로 저장하고 끝냄")
    ap.add_argument("--show", default="after", choices=["after", "before", "il", "none"])
    a = ap.parse_args(argv)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
    sc = SB.load_scene(a.scene) if a.scene else SB.default_scene(a.kind)
    w = Sandbox(sc)
    if not a.scene:
        w.cmb_ex.setCurrentIndex(w.cmb_ex.findData(a.kind))
    if a.show != "after":
        w.w_show.setCurrentIndex(w.w_show.findData(a.show))
    if a.snapshot:
        w.resize(1600, 950)
        w.show(); app.processEvents()
        w.refresh(full=True); app.processEvents()
        w.grab().save(a.snapshot)
        return 0
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
