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
import math
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
try:                                                  # 앱 안 3D 보기(Qt WebEngine, PySide6-WebEngine). 없으면 브라우저로 엶
    from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402
    HAS_WEB = True
except ImportError:                                   # pragma: no cover
    QWebEngineView = None
    HAS_WEB = False
MSG_3D_EMPTY = "건물을 클릭하면\n여기에\n3D 파사드\n소음지도가\n나옵니다."
SPLIT_EMPTY_W = 220      # 나란히 보기에서 건물을 고르기 전 오른쪽 3D 창 폭(px)
ROLE = Qt.UserRole
KEYS = {"LrT": "주간 LrT (06–22시)", "LrN": "야간 LrN (22–06시)"}
# 전농사거리 예제(examples/jeonnong)가 쓰는 국토지리정보원 수치지형도 v2.0 도엽 — 수치지도는 앱에 넣지 않음(재배포 조건 미확인)
JEONNONG_SHEETS = ["377051731", "377051732", "377051733", "377051741", "377051742", "377051743",
                   "377051751", "377051752", "377051753"]


def extract_map_zips(folder):
    """folder 안의 수치지도 zip 을 같은 이름 폴더로 풀기(이미 푼 것은 건너뜀). 한글 파일명(cp949) 보정,
    zip 안의 zip 도 한 단계 더 풂. 푼 zip 개수 반환. 원본 zip 은 지우지 않음"""
    import shutil
    import zipfile
    n = 0
    for z in sorted(Path(folder).rglob("*.zip")):
        dest = z.with_suffix("")
        if dest.exists():
            continue
        root = dest.resolve()
        with zipfile.ZipFile(z) as zf:
            for info in zf.infolist():
                name = info.filename
                if not info.flag_bits & 0x800:                  # UTF-8 표시가 없으면 cp949 로 다시 읽음
                    try:
                        name = name.encode("cp437").decode("cp949")
                    except (UnicodeEncodeError, UnicodeDecodeError):
                        pass
                target = (dest / name).resolve()
                if not target.is_relative_to(root):              # 압축 밖 경로로 쓰는 항목 막음
                    continue
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as a, open(target, "wb") as b:
                    shutil.copyfileobj(a, b)
        n += 1
    return n


def has_building_layer(folder):
    """수치지도 건물 레이어(N1A_B0010000.shp)가 하나라도 있는지"""
    return any(Path(folder).rglob("N1A_B0010000*.shp"))
TARGETS = {"grid": "격자(소음지도)", "facade": "건물 벽면", "points": "점 수음점"}
METHODS = {"iso9613_2": "ISO 9613-2 전파 (RLS-19 음원 + ISO 9613-2, 프로젝트 구성 방법)",
           "rls19": "RLS-19 전파 (RLS-19 3.5·3.6 원문)",
           "rls90": "RLS-90 (음원·전파 모두 RLS-90, 1990 — SoundPLAN RLS-90 비교용)"}


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


# ================================================================ 화면 도우미(계산과 무관 — 화면 없이 시험할 수 있게 따로 둠)
def fmt_dur(s):
    """초 → '45초' / '3분 20초' / '1시간 05분'"""
    s = max(0, int(round(s)))
    if s < 60:
        return f"{s}초"
    if s < 3600:
        return f"{s // 60}분 {s % 60:02d}초"
    return f"{s // 3600}시간 {s % 3600 // 60:02d}분"


def count_stages(cfg, which):
    """계산 단계 수(run.main 이 진행률을 따로 세는 묶음): 격자는 높이마다 1, 벽면 1, 점 수음점 1"""
    r, n = cfg["receivers"], 0
    if "grid" in which and r["grid"]["enabled"]:
        n += len(r["grid"]["heights"])
    if "facade" in which and r["facade"]["enabled"]:
        n += 1
    if "points" in which and (r.get("points") or {}).get("path"):
        n += 1
    return max(n, 1)


class StageProgress:
    """단계별 진행률(label, done, total, eta_s)을 전체 진행률(0~1)과 상태 글로 바꿈.
    단계가 바뀐 것은 label 이 바뀐 것으로 앎. 남은 시간은 지금 단계 것만 알 수 있음(뒤 단계의 점 수·속도는 모름)"""
    def __init__(self, n):
        self.n, self.i, self.label = max(int(n), 1), 0, None

    def __call__(self, label, done, total, eta):
        if label != self.label:
            self.i += self.label is not None
            self.label = label
        i = min(self.i, self.n - 1)
        f = min(done / max(total, 1), 1.0)
        head = f"{i + 1}/{self.n} 단계 · " if self.n > 1 else ""
        left = "이 단계 남은 시간" if i + 1 < self.n else "남은 시간"
        return (i + f) / self.n, f"{head}{label} {done:,}/{total:,} 지점 · {left} 약 {fmt_dur(eta)}"


RESULT_FILES = {"grid": "grid_result.csv", "facade": "facade_result.csv", "points": "point_result.csv"}


def overwrite_list(out, which):
    """이번 계산 대상(which)의 결과 파일 중 이미 있는 것 → [(대상 이름, 파일명, 고친 시각)]. 없으면 빈 목록"""
    import datetime as dt
    rows = []
    for k in which:
        p = Path(out) / RESULT_FILES[k]
        if p.is_file():
            rows.append((TARGETS[k], p.name, dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")))
    return rows


def fresh_out_dir(out, now=None):
    """out 옆의 새 결과 폴더 경로 '이름_YYYYMMDD_HHMM'(이미 있으면 _2, _3 …). 폴더는 만들지 않음"""
    import datetime as dt
    out = Path(out)
    base = f"{out.name}_{(now or dt.datetime.now()).strftime('%Y%m%d_%H%M')}"
    p, i = out.with_name(base), 2
    while p.exists():
        p, i = out.with_name(f"{base}_{i}"), i + 1
    return p


EXPORT_GROUPS = {"tables": ("결과 표 (CSV)", (".csv",)),
                 "maps": ("지도 그림·3D 뷰어 (PNG·PDF·HTML)", (".png", ".pdf", ".html")),
                 "gis": ("GIS 자료 (GeoTIFF·GPKG)", (".tif", ".gpkg")),
                 "cond": ("계산 조건·설정 사본 (JSON·YAML)", (".json", ".yaml", ".yml"))}


def export_files(out, groups):
    """결과 폴더 바로 아래 파일 중 고른 묶음(EXPORT_GROUPS 키)에 드는 것. 계산용 중간 파일(npz)은 어느 묶음에도 없음"""
    ext = {e for g in groups for e in EXPORT_GROUPS[g][1]}
    return sorted(p for p in Path(out).iterdir() if p.is_file() and p.suffix.lower() in ext)


def export_zip(out, dest, groups):
    """고른 묶음을 ZIP 하나로 묶어 dest 에 저장. 넣은 파일 수를 돌려줌(dest 가 결과 폴더 안이어도 자기 자신은 뺌)"""
    import zipfile
    dest = Path(dest)
    files = [p for p in export_files(out, groups) if p.resolve() != dest.resolve()]
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, p.name)
    return len(files)


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
            if self.kind == "section":
                from sori import section
                def prog(label, done, total, eta):
                    self.b.prog.emit(done / max(total, 1), f"단면 계산 {done:,}/{total:,} 점 · 남은 시간 약 {fmt_dur(eta)}")
                D, info, fn = section.main(self.kw["cfg"], self.kw["p1"], self.kw["p2"], self.kw.get("ds"),
                                           self.kw.get("dz"), progress=prog)
                self.b.done.emit("section", (D, info, fn))
            elif self.kind == "study":
                hs = self.kw["heights"]

                def pg(i, n, h):
                    self.b.prog.emit(i / max(n, 1), f"방음벽 높이 비교 {i + 1}/{n} — {h:g} m 계산 중")
                T = G.barrier_height_study(self.kw["cfg"], self.kw["base"], self.kw["barriers"], hs,
                                           self.kw["which"], method=self.kw.get("method"), progress=pg)
                self.b.done.emit("study", T)
            elif self.kind == "paths":
                from sori import paths
                self.b.prog.emit(0.0, "경로 계산 중(직접음·1차 반사)… 처음 한 번은 준비에 시간이 걸림")
                self.b.done.emit("paths", paths.point_table(self.kw["cfg"], self.kw["x"], self.kw["y"], self.kw["h"],
                                                            max_order=self.kw.get("max_order", 1)))
            elif self.kind == "viewer3d":
                from sori import viewer3d
                self.b.prog.emit(0.0, "3D 뷰어 만드는 중(지형·건물·벽면 결과를 한 파일로)…")
                self.b.done.emit("viewer3d", viewer3d.main(self.kw["cfg"]))
            elif self.kind == "run":
                sp = StageProgress(self.kw.get("stages") or 1)

                def prog(label, done, total, eta):
                    self.b.prog.emit(*sp(label, done, total, eta))
                r = G.run_job(self.kw["cfg"], self.kw["out"], self.kw["which"], self.kw["plots"], progress=prog,
                              method=self.kw.get("method"), barriers=self.kw.get("barriers"),
                              extra_heights=self.kw.get("extra_heights"), compare_base=self.kw.get("compare_base"),
                              scenario=self.kw.get("scenario"), preset=self.kw.get("preset"))
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
class MapNav(NavigationToolbar2QT):
    """지도 위 작은 도구: 처음 보기·이동·확대만(뒤로·앞으로·여백·그래프 설정·저장은 빼고, 저장은 도구 줄 ‘저장’).
    좌표 글자는 아래 상태 줄에 나오므로 여기서는 뺌"""
    toolitems = [("처음 보기", "처음 보기로 돌아감", "home", "home"),
                 ("이동", "끌어서 지도 옮기기(다시 누르면 끔)", "move", "pan"),
                 ("확대", "사각형을 그려 확대(다시 누르면 끔)", "zoom_to_rect", "zoom")]

    def __init__(self, canvas, parent):
        super().__init__(canvas, parent, coordinates=False)
        self.setIconSize(QtCore.QSize(16, 16))
        self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.setStyleSheet("QToolBar { background: #ffffff; border: none; padding: 2px 6px; spacing: 2px; }"
                           "QToolButton { font-size: 12px; color: #444444; padding: 3px 8px; border-radius: 5px; }"
                           "QToolButton:hover { background: #f2f2f2; }"
                           f"QToolButton:checked {{ background: #fdecec; color: {RED}; }}")


def section_display(D, key, info, fine=0.5, max_cells=1_500_000):
    """단면 결과 → 그리기 배열(fs, fz, 가림 배열). 계산값은 그대로, 표시만 다듬음(격자 지도 maps_hq.grid_array 와 같은 방식):
    빈 칸(건물 안·지면 아래)을 최근접값으로 메운 뒤 fine(m) 간격으로 3차 보간(둘러싼 계산점 4개 값 범위 안으로 제한),
    가림은 계단 모양 계산점 대신 0.5 m 지형·지붕 단면(info.profile)으로 — 지면 아래·건물 안"""
    from scipy.ndimage import distance_transform_edt, map_coordinates
    ss, zs = np.sort(D.s.unique()), np.sort(D.z.unique())
    A = np.full((len(zs), len(ss)), np.nan)
    A[np.searchsorted(zs, D.z), np.searchsorted(ss, D.s)] = D[key].values
    ds = float(ss[1] - ss[0]) if len(ss) > 1 else 1.0
    dz = float(zs[1] - zs[0]) if len(zs) > 1 else 1.0
    nan = np.isnan(A)
    if nan.all():
        return ss, zs, np.ma.masked_invalid(A)
    idx = distance_transform_edt(nan, return_distances=False, return_indices=True)
    full = A[tuple(idx)]
    L, lo, hi = float(info["length_m"]), zs[0] - dz / 2, zs[-1] + dz / 2
    step = fine
    while (L / step) * ((hi - lo) / step) > max_cells:         # 아주 긴 단면은 표시 간격을 늘림
        step *= 1.5
    fs, fz = np.arange(0.0, L + step / 2, step), np.arange(lo, hi + step / 2, step)
    FS, FZ = np.meshgrid(fs, fz)
    ci, cj = (FZ - zs[0]) / dz, (FS - ss[0]) / ds              # 계산점 색인 좌표
    F = map_coordinates(full, [ci, cj], order=3, mode="nearest")
    i0 = np.clip(np.floor(ci).astype(int), 0, full.shape[0] - 1); j0 = np.clip(np.floor(cj).astype(int), 0, full.shape[1] - 1)
    i1 = np.minimum(i0 + 1, full.shape[0] - 1); j1 = np.minimum(j0 + 1, full.shape[1] - 1)
    q = np.stack([full[i0, j0], full[i0, j1], full[i1, j0], full[i1, j1]])
    F = np.clip(F, q.min(axis=0), q.max(axis=0))
    sp, zg, roof = (np.asarray(v, float) for v in info["profile"])
    zgi = np.interp(fs, sp, zg)
    rf = np.interp(fs, sp, np.nan_to_num(roof, nan=-1e9))
    rf[np.interp(fs, sp, np.isnan(roof).astype(float)) > 0.5] = -1e9
    hide = (FZ < zgi[None, :]) | (FZ <= rf[None, :])
    return fs, fz, np.ma.masked_where(hide, F)


def section_profile(D, key, h):
    """단면 결과에서 지면 위 h(m) 높이의 거리별 값 → (s, 값). 열(s)마다 계산점 표고 사이를 1차 보간,
    그 높이가 건물 안이거나 계산점이 모자라면 NaN"""
    s_out, v_out = [], []
    for s, g in D.dropna(subset=[key]).groupby("s"):
        z, v = g.z.values, g[key].values
        zt = float(g.zg.iloc[0]) + h
        o = np.argsort(z)
        z, v = z[o], v[o]
        s_out.append(s)
        v_out.append(float(np.interp(zt, z, v)) if z[0] - 1e-6 <= zt <= z[-1] + 1e-6 else np.nan)
    return np.asarray(s_out), np.asarray(v_out)


def section_buildings(B, info):
    """단면 선 A→B 가 지나는 건물 → [(bid, s 시작, s 끝, 지붕 표고)] (s 순). 지붕 표고는 단면의 그 구간 지붕 최댓값"""
    from shapely.geometry import LineString, Point
    (x1, y1), (x2, y2) = info["p1"], info["p2"]
    line = LineString([(x1, y1), (x2, y2)])
    sp, _, roof = (np.asarray(v, float) for v in info["profile"])
    out = []
    for b in B[B.intersects(line)].itertuples():
        seg = b.geometry.intersection(line)
        parts = [g for g in getattr(seg, "geoms", [seg]) if g.length > 0]
        if not parts:
            continue
        s = [line.project(Point(c)) for g in parts for c in g.coords]
        s0, s1 = min(s), max(s)
        m = (sp >= s0) & (sp <= s1) & ~np.isnan(roof)
        if m.any():
            out.append((int(b.bid), s0, s1, float(np.nanmax(roof[m]))))
    return sorted(out, key=lambda r: r[1])


class MapView(QtWidgets.QWidget):
    clicked = QtCore.Signal(float, float)          # 평면 지도 클릭(EPSG 평면 좌표)
    moved = QtCore.Signal(float, float)
    finished = QtCore.Signal()                     # 더블클릭·오른쪽 클릭(선 그리기 끝)
    facade_opts = QtCore.Signal()                  # 3D 파사드 보기 선택(소음도 숫자·주변 투명)을 바꿈

    def __init__(self):
        super().__init__()
        from sori.plots import set_font
        set_font()
        self.fig = Figure(figsize=(7, 6), layout="constrained")
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.nav = MapNav(self.canvas, self)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.nav)
        lay.addWidget(self.canvas, 1)
        self.canvas.mpl_connect("button_press_event", self._click)
        self.canvas.mpl_connect("motion_notify_event", self._move)
        # 3D 파사드 보기 선택(2026-11-14): 3D 파사드를 보일 때만 지도 도구 줄에 나옴
        self._opt_sep = self.nav.addSeparator()
        self.opt_vals = self.nav.addAction("소음도 숫자")
        self.opt_vals.setToolTip("확인 대상 건물 벽면에 층별 소음도(dB)를 숫자로 — 보이는 벽마다 한 열")
        self.opt_ghost = self.nav.addAction("주변 투명")
        self.opt_ghost.setToolTip("주변 건물을 투명하게(윤곽선만) 그려 확인 대상 건물만 보기")
        self.opt_vals.setCheckable(True)
        self.opt_vals.setChecked(True)
        self.opt_ghost.setCheckable(True)
        for a in (self.opt_vals, self.opt_ghost):
            a.toggled.connect(lambda _: self.facade_opts.emit())
        self._show_facade_opts(False)
        self._fac_bid = None      # 지금 그린 3D 파사드 건물(같은 건물을 다시 그리면 돌려 본 시점 유지)
        self.ax = None            # 평면 좌표 축(클릭 대상)
        self.ax3d = None          # 3D 파사드 축(마우스로 회전, 클릭 신호 없음)
        self.hq = None            # 보고서 지도 다시 그리기 함수(보고서 지도 보는 중일 때만)
        self.paths_extent = None  # 경로 확대 보기 범위(xmin, ymin, xmax, ymax) — 왼쪽 격자 지도에 점선 사각형으로
        self._base_dpi = 100.0
        self._hq_timer = QtCore.QTimer(self, singleShot=True, interval=250)
        self._hq_timer.timeout.connect(lambda: self.hq and self.hq())
        self.canvas.mpl_connect("resize_event", lambda e: self.hq and self._hq_timer.start())
        self.message("설정 파일(config.yaml)을 열거나 결과 폴더를 여세요.")

    def _show_facade_opts(self, on):
        for a in (self._opt_sep, self.opt_vals, self.opt_ghost):
            a.setVisible(on)

    def _clear(self, hq=None):
        self._show_facade_opts(False)
        self.fig.clear()
        self.fig.patch.set_facecolor("white")
        self.ax = self.ax3d = self.ax_sec = self.ax_prof = None
        self.hq = hq
        if hq is None:                                   # 일반 화면: 화면 해상도·자동 배치
            self._set_dpi(self._base_dpi)
            self.fig.set_layout_engine("constrained")

    def _set_dpi(self, dpi):
        """그림 1 인치당 화면 점 수를 바꿈(창 크기는 그대로) → 같은 창에 그림이 몇 인치로 담기는지가 바뀜"""
        r = self.canvas.device_pixel_ratio
        self.fig._original_dpi = dpi                   # matplotlib Qt 캔버스가 고해상도 화면 배율을 곱할 때 쓰는 값
        self.fig.dpi = dpi * r
        w, h = max(self.canvas.width(), 50) * r, max(self.canvas.height(), 50) * r
        self.fig.set_size_inches(w / self.fig.dpi, h / self.fig.dpi, forward=False)

    def draw_hq_grid(self, C, cfg, key, h, redraw, compact=False, G=None, note=""):
        """보고서 수준 격자 소음지도(maps_hq.draw_grid, 그림 파일 HQ_grid_* 와 같은 그리기).
        보고서 틀은 A3(16.54 × 11.69 in) 기준 글자 크기이므로, 창 안에 A3 이상이 담기도록 화면 dpi 를 맞춤.
        compact=True(앱 화면): 정보란 없이 지도만 창 가득(maps_hq.draw_grid_compact, 화면 dpi 그대로). False 는 지도 저장 때만"""
        from sori.maps_hq import draw_grid, draw_grid_compact
        self._clear(hq=redraw)
        self.fig.set_layout_engine(None)                 # 보고서 틀은 고정 배치
        if compact or G is not None:                     # 조건을 건 표(성분 고르기)는 지도만 크게로만 그림
            self._set_dpi(self._base_dpi)
            self.ax = draw_grid_compact(self.fig, C, cfg, key, h, G=G, note=note)
        else:
            w, hgt = max(self.canvas.width(), 50), max(self.canvas.height(), 50)
            self._set_dpi(max(20.0, min(w / 16.54, hgt / 11.69)))
            self.ax = draw_grid(self.fig, C, cfg, key, h)
        self.canvas.draw_idle()

    def draw_diff(self, C, cfg, D, key, h, redraw, title=None, edges=None, cols=None):
        """전후 차이 지도(sori.compare.draw_diff: 비교 − 기준, 지도만 크게 + 가로 범례)"""
        from sori.compare import draw_diff
        self._clear(hq=redraw)
        self.fig.set_layout_engine(None)
        self._set_dpi(self._base_dpi)
        self.ax = draw_diff(self.fig, C, cfg, D, key, h, title, edges, cols)
        self.fig.text(0.995, 0.005, f"※ {AI_NOTICE}", ha="right", va="bottom", fontsize=6, color="0.4")
        self.canvas.draw_idle()

    def message(self, text):
        self._clear()
        self.fig.text(0.5, 0.5, text, ha="center", va="center", color="#666")
        self.canvas.draw_idle()

    def draw(self, grid=None, extent=None, buildings=None, roads=None, title="", crs=None):
        """grid = (A 2차원 배열, (xmin, xmax, ymin, ymax)), extent = 계산 범위 사각형"""
        from sori.plots import classes
        self._clear()
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

    def draw_input(self, bld, roads, cont, extent, crs, title, fz="등고수치", lanes_col="차로수"):
        """입력 지도(소음 없음): 흰 바탕, 등고선(옅은 갈색, 10 m 마다 굵게·숫자), 도로(차로 수에 따라 굵기),
        건물(옅은 회색 + 진한 테두리), 계산 범위(파란 점선). 처음 보기는 계산 범위 + 10 %"""
        self._clear()
        ax = self.ax = self.fig.add_subplot(111)
        ax.set_facecolor("white")
        if cont is not None and len(cont):
            z = pd.to_numeric(cont[fz], errors="coerce") if fz in cont.columns else pd.Series(np.nan, index=cont.index)
            major = (z % 10 == 0).values
            cont[~major].plot(ax=ax, color="#d9c9ae", lw=0.5, zorder=1)
            cont[major].plot(ax=ax, color="#b89a6e", lw=0.9, zorder=1)
            for g, v in zip(cont.geometry[major], z[major]):
                if g is None or g.is_empty or not np.isfinite(v):
                    continue
                p = g.interpolate(0.5, normalized=True)
                ax.text(p.x, p.y, f"{v:g}", fontsize=6.5, color="#8a6d45", ha="center", va="center", zorder=2,
                        bbox=dict(boxstyle="round,pad=0.1", fc="white", ec="none", alpha=0.8), clip_on=True)
        if roads is not None and len(roads):
            col = lanes_col if lanes_col in roads.columns else ("lanes" if "lanes" in roads.columns else None)
            n = pd.to_numeric(roads[col], errors="coerce").fillna(1).clip(1, 10).values if col else np.ones(len(roads))
            for k in sorted(set(n)):
                roads[n == k].plot(ax=ax, color="#8f8f8f" if k >= 2 else "#b5b5b5", lw=0.8 + 0.7 * k, zorder=3)
        if bld is not None and len(bld):
            bld.plot(ax=ax, facecolor="#ececec", edgecolor="#4a4a4a", lw=0.45, zorder=4)
        if extent is not None:
            x0, y0, x1, y1 = extent
            ax.plot([x0, x1, x1, x0, x0], [y0, y0, y1, y1, y0], "--", color="#1f5fbf", lw=1.2, zorder=6)
            mx, my = 0.1 * (x1 - x0), 0.1 * (y1 - y0)
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

    def draw_facade3d(self, bld, FR, F, T, bid, key, view, fh, elev, azim, title):
        """3D 파사드 소음지도(그림 파일 facade_3d_*.png 와 같은 그리기 함수 sori.plots.draw_facade_3d).
        T: 층별 색을 칠할 건물(보이는 범위 안에서 벽면 결과가 있는 건물), 글자(층별 값)는 클릭한 건물 bid 에만.
        도구 줄 ‘소음도 숫자’(기본 켬)·‘주변 투명’에 따름. 건물 번호: 대상 = 빨간 상자, 주변 = 가까운 30개.
        같은 건물을 다시 그리면(시간대·선택 바꿈) 마우스로 돌려 본 시점을 유지"""
        from sori.maps_hq import din_cmap, legend_strip
        from sori.plots import draw_facade_3d
        if self.ax3d is not None and self._fac_bid == bid:
            elev, azim = float(self.ax3d.elev), float(self.ax3d.azim)
        self._clear()
        self._fac_bid = bid
        self.fig.set_layout_engine(None)
        ax = self.ax3d = self.fig.add_axes([0.0, 0.03, 1.0, 0.86], projection="3d", computed_zorder=False)
        cmap, norm = din_cmap()                          # 격자 지도와 같은 5 dB 등급 색(보고서 facade_3d 그림은 2 dB 그대로)
        vals, ghost = self.opt_vals.isChecked(), self.opt_ghost.isChecked()
        draw_facade_3d(ax, bld, FR, F, T, key, cmap, norm, view, fh, elev, azim, label_bids={bid} if vals else set(),
                       colors=FACADE3D_COLORS, focus=bid, bid_labels=MainWindow.FACADE_BID_LABELS, ghost=ghost, label_box=True, label_gap=10.0)
        bb = bld[bld.bid == bid]                         # 고른 건물: 지붕 테두리를 빨간 선으로(격자 지도의 빨간 테두리와 같은 표시)
        if len(bb):
            g = bb.iloc[0]
            for ring in getattr(g.geometry, "geoms", [g.geometry]):
                xs, ys = ring.exterior.xy
                ax.plot(list(xs), list(ys), [float(g.ztop) + 0.3] * len(xs), color=RED, lw=2.4, zorder=200)
        self.title_text = title                          # 화면 위 제목(시험·확인용)
        self.fig.text(0.015, 0.975, title, fontsize=11, weight="bold", va="center", color="#222222")
        legend_strip(self.fig, [0.01, 0.915, 0.98, 0.04], road=False)
        self.fig.text(0.01, 0.005, "빨간 번호 = 확인 대상 건물, 흰 번호 = 주변 건물" + (" · 주변 투명" if ghost else ""),
                      ha="left", va="bottom", fontsize=7.5, color="#444444")
        self.fig.text(0.995, 0.005, f"※ {AI_NOTICE}", ha="right", va="bottom", fontsize=6, color="0.4")
        self._show_facade_opts(True)
        self.canvas.draw_idle()

    def draw_paths(self, T, info, key, title, bld=None, roads=None, barriers=(), sums=(), n_label=6):
        """수음점 경로 확대 보기(나란히 보기 오른쪽 창, 2026-11-14): 소음지도 없이 흰 바탕에 건물·도로·방음벽과
        경로선 — 노랑 = 직접음, 빨강 = 반사음(실선 1차, 점선 2차 이상). 기여가 큰 경로일수록 굵고 진하게
        (최대값 −15 dB 까지 옅어짐), 상위 n_label 개는 음원 쪽에 기여 dB. 범위는 그린 경로(음원·반사점)와 수음점 + 여백.
        T: paths.top_paths 표, sums: 범례에 붙일 [(성분 이름, dB)] — 직접음·1차·2차 이상 순"""
        from matplotlib.collections import LineCollection
        from matplotlib.lines import Line2D
        self._clear()
        ax = self.ax = self.fig.add_subplot(111)
        ax.set_facecolor("white")
        rx, ry = info["x"], info["y"]
        P = np.asarray([(rx, ry)] + list(zip(T.sx, T.sy)) + [p for r in T.refl_pts for p in r], float)
        (x0, y0), (x1, y1) = P.min(0), P.max(0)
        half = max(x1 - x0, y1 - y0, 60.0) / 2 * 1.12           # 정사각형, 최소 60 m
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        self.paths_extent = (cx - half, cy - half, cx + half, cy + half)
        w = 1.3 * half
        if roads is not None and len(roads):
            r = roads.cx[cx - w:cx + w, cy - w:cy + w]
            if len(r):
                r.plot(ax=ax, color="#c4c4c4", lw=1.4, zorder=2)
        if bld is not None and len(bld):
            b = bld.cx[cx - w:cx + w, cy - w:cy + w]
            if len(b):
                b.plot(ax=ax, facecolor="#ececec", edgecolor="#4a4a4a", lw=0.5, zorder=3)
        for bar in barriers:
            xs, ys = zip(*bar["pts"])
            ax.plot(xs, ys, "-", color="#c2187a", lw=3.0, zorder=4, solid_capstyle="round")
        if len(T):
            rel = np.clip((T[key].values - T[key].max() + 15.0) / 15.0, 0.08, 1.0)
            order = T.order.values
            fade = lambda rgb, a: [tuple(1.0 - (1.0 - c) * (0.45 + 0.55 * v) for c in rgb) for v in a]   # 흰색과 섞어 옅게
            # 반사음 먼저 → 직접음이 위에. 노란 선은 흰 바탕에서 잘 보이게 진한 테두리를 깖
            for m, ls, rgb, edge, z in ((order >= 2, (0, (3, 2)), (0.88, 0.0, 0.0), None, 10),
                                        (order == 1, "solid", (0.88, 0.0, 0.0), None, 11),
                                        (order == 0, "solid", (1.0, 0.78, 0.0), "#9a7400", 13)):
                if not m.any():
                    continue
                segs = [[(r.sx, r.sy)] + list(r.refl_pts) + [(rx, ry)] for r in T[m].itertuples()]
                lw = 0.8 + 3.0 * rel[m]
                if edge is not None:
                    ax.add_collection(LineCollection(segs, colors=edge, linewidths=lw + 0.9, zorder=z - 0.5))
                ax.add_collection(LineCollection(segs, colors=fade(rgb, rel[m]), linewidths=lw,
                                                 linestyles=ls, capstyle="round", zorder=z, gid="paths"))
            rp = [p for r in T[order > 0].itertuples() for p in r.refl_pts]
            if rp:
                ax.plot(*zip(*rp), "o", ms=3.0, mfc="white", mec="#b00000", mew=0.8, zorder=14)
            ax.plot(T.sx, T.sy, "o", ms=3.0, color="#222222", zorder=14)
            done = []
            for r in T.itertuples():                             # 기여 상위: 음원 쪽에 dB(가까운 음원은 하나만)
                if len(done) >= n_label or any((r.sx - a) ** 2 + (r.sy - b) ** 2 < (0.2 * half) ** 2 for a, b in done):
                    continue
                done.append((r.sx, r.sy))
                u = np.array([r.sx - rx, r.sy - ry]) / max(np.hypot(r.sx - rx, r.sy - ry), 1e-6)   # 수음점 반대쪽으로
                ax.annotate(f"{getattr(r, key):.1f}", (r.sx, r.sy), xytext=tuple(12 * u), textcoords="offset points",
                            ha="center", va="center",
                            fontsize=7.5, weight="bold", color="#6b5200" if r.order == 0 else "#b00000", zorder=14,
                            bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="none", alpha=0.85), clip_on=True)
        ax.plot([rx], [ry], "o", ms=11, mfc="#1f3a93", mec="white", mew=1.6, zorder=15)
        ax.annotate(info.get("name") or "수음점", (rx, ry), xytext=(8, -12), textcoords="offset points", fontsize=8.5,
                    weight="bold", color="#1f3a93", zorder=16,
                    bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="#1f3a93", lw=0.6))
        names = dict(sums)
        h = [Line2D([], [], color="#ffcc00", lw=3.5, label="직접음"),
             Line2D([], [], color="#e00000", lw=2.5, label="반사음 1차"),
             Line2D([], [], color="#e00000", lw=2.0, ls=(0, (3, 2)), label="반사음 2차 이상"),
             Line2D([], [], ls="", marker="o", ms=9, mfc="#1f3a93", mec="white", label="수음점"),
             Line2D([], [], ls="", marker="o", ms=4, color="#222222", label="음원(도로 조각)")]
        for a in h[:3]:
            if a.get_label() in names:
                a.set_label(f"{a.get_label()}  {names[a.get_label()]:.1f} dB")
        ax.legend(handles=h, loc="upper left", fontsize=7.5, framealpha=0.92, borderpad=0.5)
        ax.set_xlim(cx - half, cx + half)
        ax.set_ylim(cy - half, cy + half)
        ax.set_aspect("equal")
        ax.set_title(title, fontsize=10)
        ax.set_xlabel("X (m)", fontsize=8)
        ax.set_ylabel("Y (m)", fontsize=8)
        ax.tick_params(labelsize=6.5)
        ax.ticklabel_format(useOffset=False, style="plain")
        self._notice()
        self.canvas.draw_idle()

    def draw_section(self, D, info, key, grid=None, buildings=None, title="", plan=True):
        """단면 소음지도(2026-11-14 다시 그림): 선 위 거리 × 표고 단면을 격자 지도와 같은 색표(maps_hq.din_cmap)로,
        계산점 사이를 보간해 매끄럽게 칠하고 5 dB 등음선. 지형·건물 단면은 0.5 m 지형 단면(info.profile)으로 또렷하게 가림,
        단면이 지나는 건물 지붕 위에 건물 번호. plan=True(한 창 보기): 왼쪽에 평면 격자 지도 + 단면 선 A→B,
        False(나란히 보기 오른쪽 창): 단면만 크게(평면은 왼쪽 격자 지도에 A–B 선)"""
        from sori.maps_hq import BLD_EC, BLD_FC, DIN_EDGES, din_cmap, legend_strip
        cmap, norm = din_cmap()
        self._clear()
        self.fig.set_layout_engine(None)
        (x1, y1), (x2, y2) = info["p1"], info["p2"]
        if plan:
            ax = self.ax = self.fig.add_axes([0.01, 0.06, 0.27, 0.80])
            if grid is not None:
                A, ext = grid
                ax.imshow(np.ma.masked_invalid(A), origin="lower", extent=ext, cmap=cmap, norm=norm,
                          interpolation="bilinear")
            if buildings is not None and len(buildings):
                buildings.plot(ax=ax, facecolor=BLD_FC, edgecolor=BLD_EC, lw=0.3)
            ax.plot([x1, x2], [y1, y2], "-", color="#1f5fbf", lw=2.2)
            for (x, y), t in (((x1, y1), "A"), ((x2, y2), "B")):
                ax.annotate(t, (x, y), xytext=(4, 4), textcoords="offset points", color="#1f5fbf", fontsize=10,
                            weight="bold")
            m = max(abs(x2 - x1), abs(y2 - y1)) * 0.25 + 20
            ax.set_xlim(min(x1, x2) - m, max(x1, x2) + m)
            ax.set_ylim(min(y1, y2) - m, max(y1, y2) + m)
            ax.set_aspect("equal")
            ax.set_xticks([]); ax.set_yticks([])
            ax.set_xlabel(""); ax.set_ylabel("")                     # geopandas 가 붙이는 좌표축 이름 지움
            ax.set_title("평면 — 파란 선 A→B", fontsize=8)
            sx = self.fig.add_axes([0.33, 0.37, 0.64, 0.48])
        else:
            sx = self.fig.add_axes([0.075, 0.37, 0.91, 0.48])
        self.ax_sec = sx
        L = float(info["length_m"])
        ds, dz = float(info["ds_m"]), float(info["dz_m"])
        sp, zg, roof = (np.asarray(v, float) for v in info["profile"])
        ss, zs = np.sort(D.s.unique()), np.sort(D.z.unique())
        lo, hi = zs[0] - dz / 2, zs[-1] + dz / 2
        fs, fz, F = section_display(D, key, info)
        sx.contourf(fs, fz, F, levels=[-200] + DIN_EDGES + [200], cmap=cmap, norm=norm, zorder=1, antialiased=True)
        sx.contour(fs, fz, F, levels=DIN_EDGES, colors="#333333", linewidths=0.3, alpha=0.6, zorder=2)
        sx.fill_between(sp, zg, roof, where=~np.isnan(roof), color=BLD_FC, ec=BLD_EC, lw=0.5, zorder=3)  # 건물 단면
        sx.fill_between(sp, lo, zg, color="#a08868", lw=0, zorder=4)                                    # 지형
        sx.plot(sp, zg, color="#4a3a28", lw=0.9, zorder=5)
        for b, s0, s1, top in section_buildings(buildings, info) if buildings is not None else []:
            if s1 - s0 > 0.02 * L:                                   # 단면 폭이 아주 좁은 건물은 번호 생략
                sx.text((s0 + s1) / 2, min(top + 0.6, hi - 0.5), str(b), ha="center", va="bottom", fontsize=7.5,
                        color="#333333", zorder=8, clip_on=True,
                        bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="#888888", lw=0.4, alpha=0.85))
        sx.set_xlim(0, L)
        sx.set_ylim(lo, hi)
        H = hi - lo
        box = min(0.9 if plan else 1.1, max(0.3, 1.6 * H / L))       # 세로 길이 비(그림 칸 높이 / 폭)
        sx.set_box_aspect(box)
        sx.set_anchor("N")
        ex = box * L / H                                              # 세로 과장 배율(1 = 실제 비율)
        sx.text(0, 1.01, "A", transform=sx.transAxes, color="#1f5fbf", weight="bold")
        sx.text(1, 1.01, "B", transform=sx.transAxes, color="#1f5fbf", weight="bold", ha="right")
        sx.set_xlabel(f"A로부터 거리 (m) — 계산점 간격 가로 {ds:g} m · 세로 {dz:g} m, 반사 {info['reflection_order']}차",
                      fontsize=7.5)
        # 아래: 지면 위 4 m·1.5 m 높이의 A→B 값(기준선·건물 구간) — 단면과 같은 가로축
        self.fig.canvas.draw()                                        # 단면 칸 실제 위치(세로 비 적용 뒤)
        pos = sx.get_position()
        px = self.ax_prof = self.fig.add_axes([pos.x0, 0.075, pos.width, max(0.12, min(0.22, pos.y0 - 0.17))])
        for b, s0, s1, _ in (section_buildings(buildings, info) if buildings is not None else []):
            px.axvspan(s0, s1, color=BLD_FC, alpha=0.55, lw=0, zorder=0)
        for h, ls, lab in ((4.0, "-", "지면 위 4 m"), (1.5, (0, (4, 2)), "지면 위 1.5 m")):
            ps, pv = section_profile(D, key, h)
            if np.isfinite(pv).any():
                px.plot(ps, pv, ls=ls, color="#222222", lw=1.2, label=lab, zorder=3)
        lim = {"LrT": 68.0, "LrN": 58.0}.get(key)
        if lim is not None:
            px.axhline(lim, color="#d00000", lw=0.9, ls=(0, (2, 2)), zorder=2)
            px.text(0.005 * L, lim + 0.4, f"기준 {lim:g} dB", color="#d00000", fontsize=7, va="bottom", ha="left")
        px.set_xlim(0, L)
        px.grid(axis="y", color="#e6e6e6", lw=0.6)
        px.set_ylabel(f"{key} dB", fontsize=7.5)
        px.tick_params(labelsize=7)
        px.legend(fontsize=7, loc="upper right", framealpha=0.85, ncol=2)
        px.set_title("높이별 값(회색 띠 = 건물)", fontsize=7.5, loc="left")
        sx.set_ylabel("표고 (m)" + (f" · 세로 ×{ex:.1f}" if abs(ex - 1) > 0.05 else ""), fontsize=7.5)
        sx.tick_params(labelsize=7)
        self.title_text = title
        self.fig.text(0.01, 0.975, title, fontsize=10.5, weight="bold", va="center", color="#222222")
        legend_strip(self.fig, [0.01, 0.905, 0.98, 0.04], road=False)
        self.fig.text(0.995, 0.005, f"※ {AI_NOTICE}", ha="right", va="bottom", fontsize=6, color="0.4")
        self.canvas.draw_idle()

    def _click(self, e):
        if e.xdata is None or self.nav.mode:
            return
        if self.ax is not None and e.inaxes is self.ax:
            if e.dblclick or e.button == 3:
                return self.finished.emit()
            self.clicked.emit(e.xdata, e.ydata)

    def _move(self, e):
        if self.ax is not None and e.inaxes is self.ax and e.xdata is not None:     # 평면 좌표만 표시
            self.moved.emit(e.xdata, e.ydata)


# ================================================================ 입력값 편집(config.yaml 을 칸에 넣어 고치고 저장)
# (설정 키 경로, 이름, 종류, 선택지·설명) — 종류: text, int, float, float?(비우면 null), floats(쉼표 목록), xy(두 값),
#   choice, bool, file?(파일, 비우면 null), file, dirs(폴더 목록)
ROAD_TYPES = ["gemeinde", "landesstrasse", "bundesstrasse", "autobahn"]
CHOICE_LABELS = {**METHODS,
                 "flatten": "flatten — 도로폭 안 지형을 도로중심선 높이로 평평하게(권장)",
                 "dtm": "dtm — 등고선 지형 그대로(2026-11-05 이전 계산과 같음)",
                 "A": "A — 가 지역(주거·녹지지역 등): 주간 68 / 야간 58 dB(A)",
                 "B": "B — 나 지역(상업·공업지역 등): 주간 73 / 야간 63 dB(A)"}
INPUT_FIELDS = [
    ("프로젝트", [
        ("project.name", "프로젝트 이름", "text", None),
        ("project.scenario", "시나리오", "text", "파일명 규칙에 들어감(예: 현황, 방음벽안)"),
        ("crs", "좌표계 EPSG", "int", "수치지도 1:1,000 = 5186"),
    ]),
    ("입력 파일", [
        ("input.map.folders", "수치지도 폴더 (필수)", "dirs", "국토지리정보원 수치지형도 v2.0 SHP 폴더(여러 개는 ; 로 구분)"),
        ("input.traffic.path", "교통량 CSV (권장)", "file?", "비우면 차로 수 기준 가정 교통량"),
        ("input.traffic.default_road_type", "기본 도로 유형", "choice", ROAD_TYPES),
        ("input.dem.path", "지형 DEM", "file?", "비우면 등고선·표고점만 사용"),
        ("input.junctions.path", "교차로 CSV", "file?", "비우면 자동 판정"),
        ("input.ground.soft_polygons", "녹지 다각형", "file?", "SHP/GPKG, 비우면 없음"),
        ("input.road_override.path", "도로 확인값 CSV", "file?", "방향별 차로 수 등(‘차로 수’ 탭에서 만듦), 비우면 없음"),
        ("input.zones.path", "용도지역 다각형", "file?", "SHP/GPKG — 건물별 소음 기준 대조용, 비우면 모두 기본 지역"),
        ("input.zones.field", "용도지역 이름 열", "text", "예: zone, 용도지역명"),
        ("input.zones.default", "기본 지역", "choice", ["A", "B"]),
    ]),
    ("계산 범위·수음점", [
        ("receivers.center", "격자 중심 X, Y (m)", "xy", "‘지도에서 중심 찍기’로도 넣을 수 있음"),
        ("receivers.grid.size", "격자 크기 가로, 세로 (m)", "xy", None),
        ("receivers.grid.step", "격자 간격 (m)", "float", None),
        ("receivers.grid.heights", "격자 높이 (m)", "floats", "쉼표로 여러 개, 예: 1.5, 4"),
        ("receivers.facade.enabled", "건물 벽면 계산", "bool", None),
        ("receivers.facade.radius", "벽면 계산 반경 (m)", "float", "격자 중심에서"),
        ("receivers.facade.piece", "벽면 수음점 가로 간격 (m)", "float",
         "기본 10. 줄이면 점 수·계산 시간이 늘고(5 m ≈ 1.5배, 2.5 m ≈ 2.7배) 벽면 결과가 달라짐. 세로는 층마다 1점"),
        ("model.floor_height", "층 높이 (m)", "float", "수치지도 층수 × 층 높이 = 건물 높이"),
        ("model.road_surface", "차도 면 지형", "choice", ["flatten", "dtm"]),
        ("model.minor_roads", "골목길(1차로) 음원", "choice", ["exclude", "include"]),
        ("receivers.points.path", "점 수음점 CSV", "file?", "point_id, x, y, h"),
        ("receivers.points.default_height", "점 수음점 기본 높이 (m)", "float?", "h 가 빈 행에 씀"),
        ("receivers.points.measured", "측정값 CSV", "file?", "point_id, period(day/night), Leq — 측정−계산 차이 표·지도"),
    ]),
    ("전파·반사", [
        ("propagation_method", "전파 방법", "choice", list(METHODS)),
        ("input.ground.method", "지면 계산법", "choice", ["general", "alternative"]),
        ("input.ground.default_G", "기본 지면 계수 G", "float", "0 = 포장, 1 = 녹지"),
        ("propagation.temperature_C", "기온 (°C)", "float", None),
        ("propagation.humidity_pct", "상대습도 (%)", "float", None),
        ("propagation.reflection.rho", "반사계수 ρ", "float", "ISO 9613-2 표 4: 창 있는 벽 0.8"),
        ("propagation.reflection.max_order_grid", "반사 차수 — 격자", "int", "0~6 (4차 이상은 계산 시간이 차수마다 크게 늘어남)"),
        ("propagation.reflection.max_order_facade", "반사 차수 — 벽면", "int", "0~6"),
        ("propagation.reflection.max_order_point", "반사 차수 — 점 수음점", "int", "0~6"),
        ("propagation.cmet_C0", "기상 보정 C0 dB (ISO 방법만)", "float",
         "0 = 순풍 조건(기본). 0 초과면 장기 평균 L_AT(LT)(ISO 9613-2 8절, 보통 0~5 dB, 지역 기상 통계로 정함)"),
        ("propagation.lateral.enabled", "측면 회절 (ISO 방법만)", "bool",
         "기본 켬. 건물·방음벽에 막힌 직접음에 좌우로 돌아오는 경로를 더함(ISO 9613-2 식 13). 고층 건물 뒤·이면 지역 값이 오름, "
         "도로변은 거의 그대로. 계산 시간 약 +25 %. RLS-19·RLS-90 방법에서는 켜도 적용 안 함. 끄면 2026-11-13 이전 계산과 같음"),
    ]),
]


def _get(d, path):
    for k in path.split("."):
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def _set(d, path, v):
    ks = path.split(".")
    for k in ks[:-1]:
        if not isinstance(d.get(k), dict):
            d[k] = {}
        d = d[k]
    d[ks[-1]] = v


def _num(t):
    t = t.strip()
    return None if t == "" else float(t)


# ================================================================ 준비할 파일 안내(입력값 화면 첫 탭)
# (설정 키, 이름, 필요도, 무엇에 쓰나, 형식, 구하는 곳, 서식 파일(sori/templates) 또는 None, 고르기 필터)
# 필요도: need = 필수(없으면 계산 안 됨), rec = 권장(없으면 가정값), opt = 선택
INPUT_GUIDE = [
    ("input.map.folders", "수치지도 폴더", "need",
     "건물(높이·모양), 도로(위치·차로 수·이름), 등고선·표고점(지형)을 모두 여기서 읽습니다.",
     "국토지리정보원 수치지형도 v2.0(1:1,000) SHP 를 압축 푼 폴더. 그 안에 건물 *B0010000*.shp, "
     "도로 *A0020000*.shp, 등고선 *F0010000*.shp, 표고점 *F0020000*.shp 가 있어야 합니다. 도엽이 여러 개면 폴더를 ; 로 이어 적습니다.",
     "국토정보플랫폼(map.ngii.go.kr) ▸ 공간정보 ▸ 수치지도 — 계산할 곳의 도엽 번호로 받음", None, None),
    ("input.traffic.path", "교통량 CSV", "rec",
     "도로마다 차가 얼마나 다니는지 — 소음 크기를 정하는 가장 중요한 자료입니다. 없으면 차로 수로 가정한 교통량을 씁니다.",
     "CSV. 한 줄에 도로 하나: key(도로명 또는 UFID), key_type(name / ufid), DTV(하루 교통량) 또는 M_day·M_night(시간당), "
     "p1·p2(화물차 비율 %), v_pkw(속도 km/h) … 빈칸은 표준값. 칸으로 고치려면 ‘교통량’ 탭.",
     "서울시 교통정보(TOPIS), 국가교통DB(KTDB) 교통량 조사, 현장 계수", "traffic_example.csv", "CSV (*.csv)"),
    ("input.dem.path", "지형 DEM", "opt",
     "지형 높이를 보완합니다. 없으면 수치지도의 등고선·표고점만으로 지형을 만듭니다.",
     "래스터 GeoTIFF(.tif) 또는 IMG(.img). 좌표계는 파일에서 읽음.",
     "국토정보플랫폼 DEM(수치표고모델) 등", None, "래스터 (*.tif *.tiff *.img)"),
    ("receivers.points.path", "점 수음점 CSV", "opt",
     "현장 측정점처럼 값을 따로 뽑아 볼 지점. 계산 결과 ‘점 수음점’ 표에 나옵니다.",
     "CSV. 열: point_id(이름), x, y(설정 좌표계, m), h(지면 위 높이 m). 다른 열은 무시합니다.",
     "측정 계획서의 측정점 좌표(지도 앱에서 읽은 위경도는 좌표 변환 필요)", "points", "CSV (*.csv)"),
    ("input.junctions.path", "교차로 CSV", "opt",
     "신호·회전 교차로 가까이에서 더하는 보정(RLS-19 D_K)에 씁니다. 없으면 차로 수로 자동 판정합니다.",
     "CSV. 열: x, y(교차점 좌표), type(signal = 신호, roundabout = 회전교차로), note(메모).",
     "지도에서 교차점 좌표를 읽어 적음", "junctions_example.csv", "CSV (*.csv)"),
    ("input.ground.soft_polygons", "녹지 다각형", "opt",
     "공원·잔디처럼 소리를 흡수하는 땅(지면 계수 G = 1). 없으면 모두 포장(기본 지면 계수)으로 봅니다.",
     "다각형 SHP 또는 GPKG.", "GIS 프로그램(QGIS 등)에서 그려 저장", None, "다각형 (*.shp *.gpkg)"),
    ("input.road_override.path", "도로 확인값 CSV", "opt",
     "수치지도의 차로 수·일방통행 방향이 실제와 다를 때 도로별로 고칩니다.",
     "CSV. 열: ufid, lanes_fwd, lanes_bwd(방향별 차로 수), oneway_dir(+1 / −1) …",
     "‘차로 수’ 탭에서 고치고 저장하면 자동으로 만들어짐", "roads_override_example.csv", "CSV (*.csv)"),
]
NEED_LABEL = {"need": ("필수", "#c0392b"), "rec": ("권장", "#d68910"), "opt": ("선택", "#7f8c8d")}


def guide_status(key, value, base):
    """안내 칸 하나의 상태 → (기호, 색, 글). value = 입력 칸 글(상대 경로는 base 기준)"""
    need = next(g[2] for g in INPUT_GUIDE if g[0] == key)
    parts = [x.strip() for x in str(value or "").split(";") if x.strip()]
    if not parts:
        return {"need": ("✗", "#c0392b", "비어 있음 — 이 파일이 있어야 계산할 수 있습니다"),
                "rec": ("△", "#d68910", "비어 있음 — 차로 수로 가정한 교통량으로 계산합니다"),
                "opt": ("—", "#95a5a6", "쓰지 않음")}[need]
    paths = [Path(x) if Path(x).is_absolute() else Path(base) / x for x in parts]
    miss = [x for x, q in zip(parts, paths) if not q.exists()]
    if miss:
        return "✗", "#c0392b", f"찾을 수 없음: {', '.join(miss)}"
    if key == "input.map.folders":
        n = sum(len(list(q.rglob("*B0010000*.shp"))) for q in paths)
        if not n:
            return "✗", "#c0392b", "폴더는 있지만 건물 레이어(*B0010000*.shp)가 없음 — 압축을 풀었는지 확인"
        return "✓", "#1e8449", f"있음 — 건물 레이어 {n}개(도엽)"
    return "✓", "#1e8449", f"있음 — {paths[0].name}"


class InputGuide(QtWidgets.QWidget):
    """입력값 화면 첫 탭: 무엇을 준비해야 하는지와 지금 상태. 고르기·서식 만들기는 ‘설정값’ 칸에 넣기만 하고 저장은 사용자가"""
    def __init__(self, editor):
        super().__init__()
        self.ed = editor
        self.setObjectName("guide")
        self.setStyleSheet("""
            #guide QFrame#gcard { background: #ffffff; border: 1px solid #d5dfeb; border-radius: 8px; }
            #guide QLabel#gname { font-size: 15px; font-weight: 700; color: #16365c; }
            #guide QLabel#gwhat { font-size: 13px; color: #222; }
            #guide QLabel#gsmall { font-size: 12px; color: #555; }
        """)
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 4)
        self.head = QtWidgets.QLabel()
        self.head.setWordWrap(True)
        self.head.setStyleSheet("font-size: 14px; padding: 6px 2px;")
        v.addWidget(self.head)
        sc = QtWidgets.QScrollArea(widgetResizable=True)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sc.setFrameShape(QtWidgets.QFrame.NoFrame)
        inner = QtWidgets.QWidget()
        inner.setStyleSheet("background: #f5f8fc;")
        il = QtWidgets.QVBoxLayout(inner)
        il.setSpacing(10)
        self.rows = {}
        for key, name, need, what, fmt, src, tpl, _flt in INPUT_GUIDE:
            card = QtWidgets.QFrame(objectName="gcard")
            g = QtWidgets.QGridLayout(card)
            g.setContentsMargins(14, 10, 14, 10)
            g.setHorizontalSpacing(12)
            st = QtWidgets.QLabel()
            st.setFixedWidth(26)
            st.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
            g.addWidget(st, 0, 0, 3, 1)
            top = QtWidgets.QHBoxLayout()
            top.setSpacing(10)
            lab, col = NEED_LABEL[need]
            title = QtWidgets.QLabel(f"{name}&nbsp;&nbsp;<span style='color:white; background-color:{col}; "
                                     f"font-size:11px'>&nbsp;{lab}&nbsp;</span>", objectName="gname")
            title.setTextFormat(Qt.RichText)
            top.addWidget(title)
            msg = QtWidgets.QLabel(objectName="gsmall")
            top.addWidget(msg, 1)
            g.addLayout(top, 0, 1)
            w = QtWidgets.QLabel(what, objectName="gwhat")
            w.setWordWrap(True)
            g.addWidget(w, 1, 1)
            more = QtWidgets.QLabel(f"<b>형식</b> {fmt}<br><b>구하는 곳</b> {src}", objectName="gsmall")
            more.setWordWrap(True)
            more.setTextFormat(Qt.RichText)
            g.addWidget(more, 2, 1)
            bl = QtWidgets.QVBoxLayout()
            b = QtWidgets.QPushButton("폴더 고르기…" if key == "input.map.folders" else "파일 고르기…")
            b.clicked.connect(lambda _=False, k=key: self.pick(k))
            bl.addWidget(b)
            if tpl:
                bt = QtWidgets.QPushButton("서식 만들기")
                bt.setToolTip("설정 파일 폴더에 예시 CSV 를 만들고 칸에 넣음(이미 있으면 그 파일을 씀). 예시 값은 꼭 실제 값으로 바꾸세요")
                bt.clicked.connect(lambda _=False, k=key: self.make_template(k))
                bl.addWidget(bt)
            bl.addStretch(1)
            g.addLayout(bl, 0, 2, 3, 1)
            g.setColumnStretch(1, 1)
            il.addWidget(card)
            self.rows[key] = (st, msg)
        il.addStretch(1)
        sc.setWidget(inner)
        v.addWidget(sc, 1)
        foot = QtWidgets.QHBoxLayout()
        foot.addWidget(QtWidgets.QLabel("고르거나 만든 파일은 ‘설정값’ 탭 칸에 들어갑니다. ‘저장’을 눌러야 설정 파일에 반영됩니다.",
                                        objectName="gsmall"), 1)
        b = QtWidgets.QPushButton("설정값 탭으로 ▶")
        b.clicked.connect(self.go_editor)
        foot.addWidget(b)
        v.addLayout(foot)
        self.setEnabled(False)

    def _text(self, key):
        return self.ed.w[key][0].edit.text()

    def refresh(self):
        if self.ed.path is None:
            self.head.setText("설정 파일을 열면 준비할 파일과 지금 상태가 나옵니다.")
            self.setEnabled(False)
            return
        self.setEnabled(True)
        bad = []
        for key, name, need, *_ in INPUT_GUIDE:
            sym, col, text = guide_status(key, self._text(key), self.ed.path.parent)
            st, msg = self.rows[key]
            st.setText(sym)
            st.setStyleSheet(f"font-size: 20px; font-weight: 700; color: {col};")
            msg.setText(text)
            msg.setStyleSheet(f"color: {col};")
            if sym == "✗":
                bad.append(name)
        self.head.setText(("<b style='color:#c0392b'>먼저 준비할 파일: " + ", ".join(bad) + "</b>" if bad else
                           "<b style='color:#1e8449'>계산에 필요한 파일이 모두 있습니다.</b>")
                          + "  <span style='color:#555'>✓ 있음 · △ 없으면 가정값 · — 쓰지 않음 · ✗ 고쳐야 함</span>")
        return bad

    def pick(self, key):
        base = str(self.ed.path.parent)
        if key == "input.map.folders":
            p = QtWidgets.QFileDialog.getExistingDirectory(self, "수치지도 폴더(SHP 압축 푼 폴더)", base)
        else:
            flt = next(g[7] for g in INPUT_GUIDE if g[0] == key)
            p, _ = QtWidgets.QFileDialog.getOpenFileName(self, "파일 선택", base, f"{flt};;모든 파일 (*)")
        if p:
            self.set_value(key, self.ed._rel(p))

    def set_value(self, key, rel):
        self.ed.w[key][0].edit.setText(rel)             # textChanged → refresh

    def make_template(self, key):
        """서식 CSV 를 설정 폴더에 만들고(있으면 그대로) 칸에 넣음 → 만든 파일 경로"""
        tpl = next(g[6] for g in INPUT_GUIDE if g[0] == key)
        base = self.ed.path.parent
        name = {"points": "points.csv"}.get(tpl, tpl.replace("_example", ""))
        dst = base / name
        if not dst.exists():
            if tpl == "points":
                c = self.ed.w["receivers.center"][0].text().replace(" ", "").split(",")
                x, y = (c + ["0", "0"])[:2]
                dst.write_text(f"point_id,x,y,h\nP1,{x},{y},1.5\n", encoding="utf-8-sig")
            else:
                dst.write_bytes(resource_path("sori", "templates", tpl).read_bytes())
            log.info("서식 만듦: %s (예시 값을 실제 값으로 바꾸세요)", dst)
        self.set_value(key, self.ed._rel(dst))
        return dst

    def go_editor(self):
        tabs = self.parent()
        while tabs is not None and not isinstance(tabs, QtWidgets.QTabWidget):
            tabs = tabs.parent()
        if tabs is not None:
            tabs.setCurrentWidget(self.ed)


class InputEditor(QtWidgets.QWidget):
    """설정 파일의 주요 입력값을 칸으로 보여 주고 고쳐 저장. 저장 전 sori.config.load 로 검사하고, 처음 저장할 때
    원본을 config.yaml.bak 로 남김(YAML 주석은 저장하면 사라짐)"""
    saved = QtCore.Signal(str)
    pick_center = QtCore.Signal()
    preview = QtCore.Signal(object)               # 저장 전 범위 미리보기: (cx, cy, w, h)
    changed = QtCore.Signal()                     # 파일 칸 글이 바뀜(준비할 파일 안내를 다시 맞춤)

    def __init__(self):
        super().__init__()
        self.path = None
        self.w = {}
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.lbl = QtWidgets.QLabel("설정 파일을 열면 입력값을 고칠 수 있습니다.")
        self.lbl.setWordWrap(True)
        lay.addWidget(self.lbl)
        sc = QtWidgets.QScrollArea(widgetResizable=True)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QtWidgets.QWidget()
        il = QtWidgets.QGridLayout(inner)                 # 두 열: 왼쪽 프로젝트·입력 파일, 오른쪽 계산 범위·전파
        il.setHorizontalSpacing(18)
        for i, (title, fields) in enumerate(INPUT_FIELDS):
            gb = QtWidgets.QGroupBox(title)
            fl = QtWidgets.QFormLayout(gb)
            fl.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
            for key, label, kind, extra in fields:
                fl.addRow(label, self._make(key, kind, extra))
            il.addWidget(gb, i % 2, i // 2)
        il.setRowStretch(2, 1)
        il.setColumnStretch(0, 1)
        il.setColumnStretch(1, 1)
        sc.setWidget(inner)
        lay.addWidget(sc, 1)
        row = QtWidgets.QHBoxLayout()
        b = QtWidgets.QPushButton("지도에서 격자 중심 찍기")
        b.setToolTip("지도 화면으로 가서 한 번 클릭하면 그 점이 격자 중심이 되고 이 화면으로 돌아옴")
        b.clicked.connect(self.pick_center.emit)
        row.addWidget(b)
        self.btn_prev = QtWidgets.QPushButton("지도에서 범위 보기")
        self.btn_prev.setToolTip("저장하지 않고 지도 화면에 새 계산 범위(주황 점선)를 그려 봄")
        self.btn_prev.clicked.connect(self._preview)
        self.btn_reset = QtWidgets.QPushButton("되돌리기")
        self.btn_reset.clicked.connect(lambda: self.path and self.load(self.path))
        self.btn_save = QtWidgets.QPushButton("저장 (⌘S)" if sys.platform == "darwin" else "저장 (Ctrl+S)")
        self.btn_save.setToolTip("고친 값을 검사한 뒤 설정 파일(config.yaml)에 씀")
        self.btn_save.setDefault(True)
        self.btn_save.clicked.connect(self.save)
        row.addWidget(self.btn_prev)
        row.addStretch(1)
        for x in (self.btn_reset, self.btn_save):
            x.setMinimumWidth(110)
            row.addWidget(x)
        lay.addLayout(row)
        self.setEnabled(False)

    # 칸 만들기·읽기·쓰기
    def _make(self, key, kind, extra):
        if kind == "choice":
            w = QtWidgets.QComboBox()
            for v in extra:
                w.addItem(CHOICE_LABELS.get(v, v), v)
        elif kind == "bool":
            w = QtWidgets.QCheckBox()
            if isinstance(extra, str):
                w.setToolTip(extra)
        elif kind in ("file?", "file", "dirs"):
            w = QtWidgets.QWidget()
            h = QtWidgets.QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            e = QtWidgets.QLineEdit()
            btn = QtWidgets.QToolButton(text="…")
            btn.clicked.connect(lambda _=False, e=e, k=kind: self._browse(e, k))
            h.addWidget(e, 1)
            h.addWidget(btn)
            w.edit = e
            e.textChanged.connect(lambda _t: self.changed.emit())
            if isinstance(extra, str):
                e.setPlaceholderText(extra)
        else:
            w = QtWidgets.QLineEdit()
            if isinstance(extra, str):
                w.setPlaceholderText(extra)
        if isinstance(extra, str):
            w.setToolTip(extra)
        self.w[key] = (w, kind)
        return w

    def _rel(self, p):
        """설정 파일 폴더 안이면 상대 경로로(파일을 옮겨도 함께 움직이게)"""
        if p in (None, ""):
            return None
        p = Path(p)
        try:
            return p.resolve().relative_to(self.path.parent).as_posix()
        except ValueError:
            return str(p)

    def _browse(self, e, kind):
        start = str(self.path.parent if self.path else Path.home())
        key = next((k for k, (w, _k) in self.w.items() if getattr(w, "edit", None) is e), None)
        flt = next((g[7] for g in INPUT_GUIDE if g[0] == key and g[7]), None)
        if kind == "dirs":
            p = QtWidgets.QFileDialog.getExistingDirectory(self, "수치지도 폴더", start)
        else:
            p, _ = QtWidgets.QFileDialog.getOpenFileName(self, "파일 선택", start,
                                                         f"{flt};;모든 파일 (*)" if flt else "")
        if p:
            e.setText(self._rel(p))

    def load(self, path, cfg=None):
        """설정 파일을 칸에 채움(적지 않은 항목은 기본값으로 보임)"""
        from sori import config as C
        self.path = Path(path).resolve()
        cfg = cfg or C.load(self.path)
        for key, (w, kind) in self.w.items():
            v = _get(cfg, key)
            if kind == "choice":
                w.setCurrentIndex(max(0, w.findData(v or "iso9613_2" if key == "propagation_method" else v)))
            elif kind == "bool":
                w.setChecked(bool(v))
            elif kind == "dirs":
                w.edit.setText("; ".join(self._rel(p) for p in (v or [])))
            elif kind in ("file?", "file"):
                w.edit.setText(self._rel(v) or "")
            elif kind in ("xy", "floats"):
                w.setText(", ".join(f"{float(x):g}" for x in v) if v is not None else "")
            elif kind == "int":
                w.setText("" if v is None else str(int(v)))
            else:
                w.setText("" if v is None else (f"{v:g}" if isinstance(v, float) else str(v)))
        self.lbl.setText(f"설정 파일: {self.path}\n"
                         "고친 뒤 ‘저장’ → 설정 파일에 씀. 결과는 홈 ▸ ‘지도 확인 · 계산’에서 다시 계산해야 바뀜")
        self.lbl.setToolTip(str(self.path))
        self.setEnabled(True)

    def values(self):
        """칸 값 → {키 경로: 값}. 형식이 틀리면 ValueError(칸 이름 포함)"""
        out = {}
        names = {k: lab for _, fs in INPUT_FIELDS for k, lab, _, _ in fs}
        for key, (w, kind) in self.w.items():
            try:
                if kind == "choice":
                    v = w.currentData()
                elif kind == "bool":
                    v = w.isChecked()
                elif kind == "dirs":
                    v = [s.strip() for s in w.edit.text().split(";") if s.strip()]
                elif kind in ("file?", "file"):
                    v = w.edit.text().strip() or None
                elif kind == "xy":
                    v = [float(s) for s in w.text().replace(" ", "").split(",") if s]
                    if len(v) != 2:
                        raise ValueError("두 값을 쉼표로")
                elif kind == "floats":
                    v = [float(s) for s in w.text().replace(" ", "").split(",") if s]
                    if not v:
                        raise ValueError("하나 이상")
                elif kind == "int":
                    v = int(w.text().strip())
                elif kind == "float":
                    v = float(w.text().strip())
                elif kind == "float?":
                    v = _num(w.text())
                else:
                    v = w.text().strip()
            except ValueError as e:
                raise ValueError(f"‘{names[key]}’ 값이 올바르지 않음: {e}") from None
            out[key] = v
        for k in ("max_order_grid", "max_order_facade", "max_order_point"):
            if not 0 <= out[f"propagation.reflection.{k}"] <= 6:
                raise ValueError(f"‘{names['propagation.reflection.' + k]}’ 는 0~6")
        return out

    def _preview(self):
        try:
            v = self.values()
        except ValueError as e:
            return QtWidgets.QMessageBox.warning(self, "SORI — 입력값", str(e))
        (cx, cy), (w, h) = v["receivers.center"], v["receivers.grid.size"]
        self.preview.emit((cx, cy, w, h))

    def set_center(self, x, y):
        self.w["receivers.center"][0].setText(f"{x:.1f}, {y:.1f}")

    def save(self):
        """원본 YAML 에 고친 값만 덮어써 저장. 저장 전에 같은 폴더의 임시 파일로 load 검사"""
        import yaml
        from sori import config as C
        try:
            vals = self.values()
        except ValueError as e:
            QtWidgets.QMessageBox.warning(self, "SORI — 입력값", str(e))
            return False
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        for k, v in vals.items():
            if k == "receivers.center":
                _set(raw, "receivers.grid.bbox", None)        # 중심·크기로 범위를 정함
            _set(raw, k, v)
        text = ("# SORI 화면(입력값)에서 저장함 — 원래 주석은 config.yaml.bak 에 있음\n"
                + yaml.safe_dump(raw, allow_unicode=True, sort_keys=False, default_flow_style=None))
        tmp = self.path.with_name(".sori_check_config.yaml")
        try:
            tmp.write_text(text, encoding="utf-8")
            C.load(tmp)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "SORI — 입력값", f"저장하지 않았습니다. 설정 검사 오류:\n{e}")
            return False
        finally:
            tmp.unlink(missing_ok=True)
        bak = self.path.with_name(self.path.name + ".bak")
        if not bak.exists():
            bak.write_text(self.path.read_text(encoding="utf-8"), encoding="utf-8")
        self.path.write_text(text, encoding="utf-8")
        log.info("입력값 저장: %s (원본 백업 %s)", self.path, bak.name)
        self.saved.emit(str(self.path))
        return True


# ================================================================ 교통량 표 편집(traffic.csv)
# (열, 머리글, 설명, 선택지) — 선택지가 있으면 칸을 고를 때 목록이 나옴
TRAFFIC_VIEW = [
    ("key", "도로명 / UFID", "도로명(수치지도 도로명) 또는 도로 객체 UFID", None),
    ("key_type", "구분", "name = 도로명, ufid = 도로 객체 하나", ["name", "ufid"]),
    ("road_type", "도로 유형", "RLS-19 표 2 / RLS-90 표 3 도로유형(독일 기준). 빈칸 = 설정의 기본 도로 유형", [""] + ROAD_TYPES),
    ("DTV", "DTV 대/일", "일교통량(양방향). M 을 비우면 DTV × 표준표 계수(RLS-19 표 2, RLS-90 계산은 표 3 — 독일 기준, 국내와 다를 수 있음)", None),
    ("M_day", "M 주간 대/h", "주간 06–22시 시간당 교통량(양방향)", None),
    ("M_night", "M 야간 대/h", "야간 22–06시 시간당 교통량(양방향)", None),
    ("p1_day", "p1 주간 %", "중형 화물차(Lkw1) 비율. 빈칸 = RLS-19 표 2(독일 기준)", None),
    ("p2_day", "p2 주간 %", "대형 화물차·이륜차(Lkw2) 비율. 빈칸 = RLS-19 표 2(독일 기준)", None),
    ("p1_night", "p1 야간 %", None, None),
    ("p2_night", "p2 야간 %", None, None),
    ("v_pkw", "속도 승용 km/h", "빈칸 = 설정 기본 속도(간선 50)", None),
    ("v_lkw", "속도 화물 km/h", "빈칸 = 승용 속도", None),
    ("v_pkw_fwd", "정방향 승용 km/h", "UFID 행만: 디지타이즈 방향 승용 속도. 빈칸 = 위 공통 속도", None),
    ("v_lkw_fwd", "정방향 화물 km/h", "UFID 행만: 디지타이즈 방향 화물 속도. 빈칸 = 공통 화물 속도(없으면 정방향 승용)", None),
    ("v_pkw_bwd", "역방향 승용 km/h", "UFID 행만: 디지타이즈 반대 방향 승용 속도. 빈칸 = 위 공통 속도", None),
    ("v_lkw_bwd", "역방향 화물 km/h", "UFID 행만: 반대 방향 화물 속도. 빈칸 = 공통 화물 속도(없으면 역방향 승용)", None),
    ("surface", "노면", "RLS-19 표 4a·4b (빈칸 = 수치지도 포장 속성)", None),   # 선택지는 rls19.SURFACES
    ("grade", "종단경사 %", "빈칸 = 지형에서 산정", None),
    ("basis", "근거", "measured = 실측, assumed = 가정", ["", "measured", "assumed"]),
    ("dir_split_fwd", "정방향 비율", "UFID 행만: 디지타이즈 방향 교통량 비율 0~1", None),
    ("dir_basis", "방향 근거", None, ["", "measured", "assumed"]),
    ("note", "메모", "출처·가정 등", None),
]
NUM_COLS = {"DTV", "M_day", "M_night", "p1_day", "p2_day", "p1_night", "p2_night", "v_pkw", "v_lkw", "grade",
            "dir_split_fwd", "v_pkw_fwd", "v_lkw_fwd", "v_pkw_bwd", "v_lkw_bwd"}
LW_COLS = ["LW′ 주간", "LW′ 야간"]


def lw_prime_row(rec, tcfg):
    """교통량 한 행 → (LW′ 주간, LW′ 야간) dB/m. 양방향 합, 종단경사(입력값 또는 0)만 반영, 교차로·다중반사 보정 전.
    sori.inputs.traffic_for_road 와 같은 규칙(표 2 계수·기본 속도)으로 M·p·v 를 채움. 계산할 수 없으면 ValueError"""
    from sori import rls19

    def f(k):
        v = str(rec.get(k, "") or "").strip()
        return None if v == "" else float(v)
    rtype = (rec.get("road_type") or "").strip() or tcfg.get("default_road_type", "gemeinde")
    if rtype not in rls19.TAB2:
        raise ValueError(f"도로 유형 '{rtype}'")
    t2, dtv = rls19.TAB2[rtype], f("DTV")
    m = {p: f(f"M_{p}") for p in ("day", "night")}
    if dtv is None and m["day"] is None and m["night"] is None:
        raise ValueError("DTV 또는 M 필요")
    v_pkw = f("v_pkw") or float(tcfg.get("default_speed_main", 50.0))
    v_lkw = f("v_lkw") or v_pkw
    # 방향별 속도(UFID 행만 계산에 쓰임 — inputs.traffic_for_road 와 같은 채움 규칙). 둘이 같으면 아래 합은 이전과 같음
    ufid = (rec.get("key_type") or "").strip().lower() == "ufid"
    vd = {}
    for d in ("fwd", "bwd"):
        vp = (f(f"v_pkw_{d}") if ufid else None) or v_pkw
        vd[d] = (vp, (f(f"v_lkw_{d}") if ufid else None) or f("v_lkw") or vp)
    split = f("dir_split_fwd") if ufid and (rec.get("dir_basis") or "").strip().lower() == "measured" else None
    share = {"fwd": 0.5 if split is None else split, "bwd": 0.5 if split is None else 1.0 - split}
    sf = (rec.get("surface") or "").strip() or "ref"
    if sf not in rls19.SURFACES:
        raise ValueError(f"노면 '{sf}'")
    g = f("grade") or 0.0
    out = []
    for per in ("day", "night"):
        Mf, p1s, p2s = t2[per]
        M = m[per]
        if M is None:
            if dtv is not None:
                M = Mf * dtv
            else:
                o = "night" if per == "day" else "day"
                M = m[o] * Mf / t2[o][0]
        p1, p2 = f(f"p1_{per}"), f(f"p2_{per}")
        p1, p2 = (p1s if p1 is None else p1), (p2s if p2 is None else p2)
        if vd["fwd"] == vd["bwd"] == (v_pkw, v_lkw):
            out.append(rls19.LW_prime(M, p1, p2, v_pkw, v_lkw, sf, g))
        else:                                   # 방향별 속도: 두 방향 L_W′ 의 에너지 합(경사는 방향마다 부호 반대)
            e = sum(10 ** (0.1 * rls19.LW_prime(M * share[d], p1, p2, *vd[d], sf, g if d == "fwd" else -g))
                    for d in ("fwd", "bwd") if share[d] > 0)
            out.append(10 * math.log10(e))
    return tuple(out)


class ChoiceDelegate(QtWidgets.QStyledItemDelegate):
    """열마다 정해진 값 중에서 고르는 칸(도로 유형, 노면, 근거 등)"""

    def __init__(self, choices, parent=None):
        super().__init__(parent)
        self.choices = choices

    def createEditor(self, parent, option, index):
        cb = QtWidgets.QComboBox(parent)
        cb.addItems(self.choices)
        return cb

    def setEditorData(self, ed, index):
        ed.setCurrentText(index.data() or "")

    def setModelData(self, ed, model, index):
        model.setData(index, ed.currentText())


class TrafficEditor(QtWidgets.QWidget):
    """교통량 CSV 를 표로 고쳐 저장. 칸을 고치면 그 도로의 LW′(RLS-19 식 4)를 바로 다시 계산해 오른쪽 끝에 보여 줌.
    파일에 있는 다른 열은 그대로 보존. 처음 저장할 때 원본을 traffic.csv.bak 로 남김"""
    saved = QtCore.Signal(str)
    need_file = QtCore.Signal(str)                  # 교통량 파일이 없어 새로 만들었음 → 설정 파일에 경로 넣기

    def __init__(self):
        super().__init__()
        from sori import rls19
        self.path, self.cfg, self.df = None, None, None
        self._busy = False
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.lbl = QtWidgets.QLabel("설정 파일을 열면 교통량 표를 고칠 수 있습니다.")
        self.lbl.setWordWrap(True)
        lay.addWidget(self.lbl)
        self.tbl = QtWidgets.QTableWidget(0, len(TRAFFIC_VIEW) + len(LW_COLS))
        heads = [h for _, h, _, _ in TRAFFIC_VIEW] + LW_COLS
        self.tbl.setHorizontalHeaderLabels(heads)
        for j, (_c, _h, tip, _ch) in enumerate(TRAFFIC_VIEW):
            if tip:
                self.tbl.horizontalHeaderItem(j).setToolTip(tip)
        for j in range(len(LW_COLS)):
            self.tbl.horizontalHeaderItem(len(TRAFFIC_VIEW) + j).setToolTip(
                "RLS-19 식 (4) 음원선 1 m 당 음향파워레벨 dB/m — 양방향 합, 입력한 종단경사만 반영, "
                "교차로·다중반사 보정 전(계산 때는 도로 객체마다 더해짐). 노면이 빈칸이면 여기서는 기준 노면(ref)으로 "
                "보여 주지만, 계산 때는 수치지도 포장 속성을 씀")
        surf = [""] + list(rls19.SURFACES)
        for j, (c, _h, _t, ch) in enumerate(TRAFFIC_VIEW):
            ch = surf if c == "surface" else ch
            if ch:
                self.tbl.setItemDelegateForColumn(j, ChoiceDelegate(ch, self.tbl))
        self.tbl.setAlternatingRowColors(True)
        hh = self.tbl.horizontalHeader()
        hh.setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        for j in range(len(LW_COLS)):                     # 계산 결과 LW′ 는 도로 유형 바로 뒤에 보이게(데이터 열 순서는 그대로)
            hh.moveSection(hh.visualIndex(len(TRAFFIC_VIEW) + j), 3 + j)
        self.tbl.itemChanged.connect(self._changed)
        lay.addWidget(self.tbl, 1)
        row = QtWidgets.QHBoxLayout()
        for text, fn in (("행 추가", self.add_row), ("선택 행 복제", self.dup_rows), ("선택 행 삭제", self.del_rows),
                         ("국내 차종 → 차량군…", self.kr12_dialog)):
            b = QtWidgets.QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        b.setToolTip("현장에서 센 국내 12종 차종별 대수를 RLS-19 차량군(M, p1, p2)으로 바꿔 선택한 행에 넣음(가정 대응표)")
        self.lbl_sum = QtWidgets.QLabel("")
        row.addWidget(self.lbl_sum, 1)
        self.btn_reset = QtWidgets.QPushButton("되돌리기")
        self.btn_reset.clicked.connect(lambda: self.cfg is not None and self.load(self.cfg))
        self.btn_save = QtWidgets.QPushButton("저장 (⌘S)" if sys.platform == "darwin" else "저장 (Ctrl+S)")
        self.btn_save.setToolTip("값을 검사한 뒤 교통량 CSV 에 씀(다른 열은 그대로)")
        self.btn_save.clicked.connect(self.save)
        for x in (self.btn_reset, self.btn_save):
            x.setMinimumWidth(110)
            row.addWidget(x)
        lay.addLayout(row)
        self.setEnabled(False)

    def load(self, cfg):
        """cfg(sori.config.load 결과)의 교통량 파일을 표에 채움. 파일이 없으면 빈 표(저장하면 새 파일)"""
        from sori.inputs import TRAFFIC_COLS, read_csv_any
        self.cfg = cfg
        p = cfg["input"]["traffic"].get("path")
        base = Path(cfg["_config_path"]).parent
        self.path = Path(p) if p else base / "traffic.csv"
        if self.path.exists():
            df = read_csv_any(self.path)
            msg = f"교통량 파일: {self.path}"
        else:
            df = pd.DataFrame(columns=TRAFFIC_COLS)
            msg = (f"교통량 파일이 없습니다 — 저장하면 {self.path} 를 새로 만들고 설정 파일에 넣음 "
                   "(지금은 모든 도로에 차로 수 기준 가정 교통량)")
        for c, *_ in TRAFFIC_VIEW:
            if c not in df.columns:
                df[c] = ""
        self.df = df.fillna("").astype(str)
        self.lbl.setText(msg + "\n도로명 행은 같은 이름의 2차로 이상 구간 전체, UFID 행은 그 도로 객체 하나에 적용 "
                               "(UFID 가 우선). 빈칸은 RLS-19 표 2·설정 기본값. 머리글에 마우스를 올리면 설명")
        self._busy = True
        self.tbl.setRowCount(0)
        for _, r in self.df.iterrows():
            self._append([r.get(c, "") for c, *_ in TRAFFIC_VIEW])
        self._busy = False
        self._recalc_all()
        self.tbl.resizeColumnsToContents()
        self.tbl.setColumnWidth(0, min(self.tbl.columnWidth(0), 260))
        self.tbl.setColumnWidth(len(TRAFFIC_VIEW) - 1, min(self.tbl.columnWidth(len(TRAFFIC_VIEW) - 1), 240))
        for j in range(len(LW_COLS)):
            self.tbl.setColumnWidth(len(TRAFFIC_VIEW) + j, 80)
        self.setEnabled(True)

    def _append(self, vals):
        i = self.tbl.rowCount()
        self.tbl.insertRow(i)
        for j, v in enumerate(vals):
            self.tbl.setItem(i, j, QtWidgets.QTableWidgetItem(str(v)))
        for j in range(len(LW_COLS)):
            it = QtWidgets.QTableWidgetItem("")
            it.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            it.setBackground(QtGui.QColor("#eef3fa"))
            self.tbl.setItem(i, len(TRAFFIC_VIEW) + j, it)
        return i

    def _rec(self, i):
        return {c: (self.tbl.item(i, j).text().strip() if self.tbl.item(i, j) else "")
                for j, (c, *_) in enumerate(TRAFFIC_VIEW)}

    def _row_errors(self, rec):
        """한 행의 형식 오류 [(열, 이유)]"""
        from sori import rls19
        err = []
        for c in NUM_COLS:
            if rec[c]:
                try:
                    float(rec[c])
                except ValueError:
                    err.append((c, "숫자가 아님"))
        if rec["key_type"] not in ("", "name", "ufid"):
            err.append(("key_type", "name 또는 ufid"))
        if rec["road_type"] and rec["road_type"] not in rls19.TAB2:
            err.append(("road_type", "RLS-19 표 2 유형"))
        if rec["surface"] and rec["surface"] not in rls19.SURFACES:
            err.append(("surface", "RLS-19 표 4 노면"))
        for c in ("basis", "dir_basis"):
            if rec[c] not in ("", "measured", "assumed"):
                err.append((c, "measured 또는 assumed"))
        if rec["key"] and not (rec["DTV"] or rec["M_day"] or rec["M_night"]):
            err.append(("DTV", "DTV 또는 M 필요"))
        return err

    def _recalc(self, i):
        rec = self._rec(i)
        bad = dict(self._row_errors(rec)) if rec["key"] else {}
        self._busy = True
        for j, (c, *_) in enumerate(TRAFFIC_VIEW):
            it = self.tbl.item(i, j)
            if it is not None:
                it.setBackground(QtGui.QColor("#ffd6d6") if c in bad else QtGui.QBrush())
                it.setToolTip(bad.get(c, ""))
        lw = (None, None)
        if rec["key"] and not bad:
            try:
                lw = lw_prime_row(rec, self.cfg["input"]["traffic"])
            except (ValueError, ZeroDivisionError):
                pass
        for j, v in enumerate(lw):
            self.tbl.item(i, len(TRAFFIC_VIEW) + j).setText("" if v is None else f"{v:.1f}")
        self._busy = False

    def _recalc_all(self):
        for i in range(self.tbl.rowCount()):
            self._recalc(i)
        self.lbl_sum.setText(f"  {self.tbl.rowCount()}행")

    def _changed(self, item):
        if not self._busy and item.column() < len(TRAFFIC_VIEW):
            self._recalc(item.row())

    def add_row(self):
        i = self._append(["", "name"] + [""] * (len(TRAFFIC_VIEW) - 2))
        self._recalc(i)
        self.tbl.setCurrentCell(i, 0)
        self.tbl.editItem(self.tbl.item(i, 0))
        self.lbl_sum.setText(f"  {self.tbl.rowCount()}행")

    def _sel_rows(self):
        return sorted({ix.row() for ix in self.tbl.selectedIndexes()})

    def apply_kr12(self, i, day=None, night=None, hours_day=1.0, hours_night=1.0):
        """국내 12종 차종별 대수(day·night: {차종: 대수}, 조사 시간 hours) → 행 i 의 M·p1·p2(sori.summary.kr_to_rls19).
        넣은 시간대만 바꾸고 근거를 measured, 메모에 변환 사실을 남김"""
        from sori.summary import kr_to_rls19
        col = {c: j for j, (c, *_) in enumerate(TRAFFIC_VIEW)}
        done = []
        for per, counts, hrs in (("day", day, hours_day), ("night", night, hours_night)):
            if not counts or not sum(float(v or 0) for v in counts.values()):
                continue
            r = kr_to_rls19(counts, hrs)
            for c, v in ((f"M_{per}", r["M"]), (f"p1_{per}", r["p1"]), (f"p2_{per}", r["p2"])):
                self.tbl.item(i, col[c]).setText(f"{v:.1f}".rstrip("0").rstrip("."))
            done.append("주간" if per == "day" else "야간")
        if done:
            self.tbl.item(i, col["basis"]).setText("measured")
            note = self.tbl.item(i, col["note"]).text()
            tag = f"12종 차종 변환({'·'.join(done)}, 가정 대응표)"
            if "12종 차종 변환" not in note:
                self.tbl.item(i, col["note"]).setText((note + " " if note else "") + tag)
            self._recalc(i)
        return done

    def kr12_dialog(self):
        from sori.summary import KR12, KR_EXTRA, KR_NOTE
        rows = self._sel_rows()
        if len(rows) != 1:
            return QtWidgets.QMessageBox.information(self, "SORI — 차종 변환", "값을 넣을 도로 행을 하나 고른 뒤 누르세요.")
        d = QtWidgets.QDialog(self)
        d.setWindowTitle("국내 12종 차종 → RLS-19 차량군")
        lay = QtWidgets.QVBoxLayout(d)
        lab = QtWidgets.QLabel(KR_NOTE + ".\n센 대수와 조사 시간을 넣으면 시간당 교통량 M 과 화물차 비율 p1·p2 로 바꿔 고른 행에 넣습니다(양방향 합).")
        lab.setWordWrap(True)
        lay.addWidget(lab)
        items = [(k, n, g) for k, n, g in KR12] + list(KR_EXTRA)
        t = QtWidgets.QTableWidget(len(items), 3)
        t.setHorizontalHeaderLabels(["차량군", "주간 대수", "야간 대수"])
        t.setVerticalHeaderLabels([n for _k, n, _g in items])
        for i, (_k, _n, g) in enumerate(items):
            it = QtWidgets.QTableWidgetItem(g)
            it.setFlags(Qt.ItemIsEnabled)
            t.setItem(i, 0, it)
            t.setItem(i, 1, QtWidgets.QTableWidgetItem(""))
            t.setItem(i, 2, QtWidgets.QTableWidgetItem(""))
        lay.addWidget(t, 1)
        f = QtWidgets.QHBoxLayout()
        hd = QtWidgets.QDoubleSpinBox(minimum=0.05, maximum=24.0, value=1.0, singleStep=0.25, suffix=" 시간")
        hn = QtWidgets.QDoubleSpinBox(minimum=0.05, maximum=24.0, value=1.0, singleStep=0.25, suffix=" 시간")
        f.addWidget(QtWidgets.QLabel("조사 시간 — 주간"))
        f.addWidget(hd)
        f.addWidget(QtWidgets.QLabel("야간"))
        f.addWidget(hn)
        f.addStretch(1)
        lay.addLayout(f)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        lay.addWidget(bb)
        d.resize(520, 560)
        if d.exec() != QtWidgets.QDialog.Accepted:
            return
        try:
            day = {k: float(t.item(i, 1).text() or 0) for i, (k, _n, _g) in enumerate(items)}
            night = {k: float(t.item(i, 2).text() or 0) for i, (k, _n, _g) in enumerate(items)}
            self.apply_kr12(rows[0], day, night, hd.value(), hn.value())
        except ValueError as e:
            QtWidgets.QMessageBox.warning(self, "SORI — 차종 변환", f"대수는 0 이상의 숫자로 넣으세요.\n{e}")

    def dup_rows(self):
        for i in self._sel_rows():
            self._recalc(self._append([self.tbl.item(i, j).text() for j in range(len(TRAFFIC_VIEW))]))
        self.lbl_sum.setText(f"  {self.tbl.rowCount()}행")

    def del_rows(self):
        for i in reversed(self._sel_rows()):
            self.tbl.removeRow(i)
        self.lbl_sum.setText(f"  {self.tbl.rowCount()}행")

    def table(self):
        """표 → DataFrame(파일에 있던 다른 열은 빈칸으로 보존 — 행을 더하거나 지우면 행 단위로 함께 움직이지 않음)"""
        rows = [self._rec(i) for i in range(self.tbl.rowCount())]
        df = pd.DataFrame(rows, columns=[c for c, *_ in TRAFFIC_VIEW])
        extra = [c for c in self.df.columns if c not in df.columns]
        if extra and len(self.df) == len(df):           # 행 수가 같을 때만 다른 열을 행 순서대로 붙임
            for c in extra:
                df[c] = self.df[c].values
        return df

    def save(self):
        """검사(형식·LW′ 계산·sori.inputs.load_traffic) 뒤 저장. 성공하면 True"""
        import copy
        from sori.inputs import load_traffic
        bad = []
        for i in range(self.tbl.rowCount()):
            rec = self._rec(i)
            if rec["key"]:
                bad += [f"{i + 1}행 ‘{rec['key']}’ {c}: {why}" for c, why in self._row_errors(rec)]
        if bad:
            QtWidgets.QMessageBox.warning(self, "SORI — 교통량", "저장하지 않았습니다. 빨간 칸을 고치세요.\n\n"
                                          + "\n".join(bad[:15]) + ("\n…" if len(bad) > 15 else ""))
            return False
        df = self.table()
        tmp = self.path.with_name(".sori_check_traffic.csv")
        try:
            df.to_csv(tmp, index=False, encoding="utf-8-sig")
            cfg = copy.deepcopy(self.cfg)
            cfg["input"]["traffic"]["path"] = str(tmp)
            load_traffic(cfg)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "SORI — 교통량", f"저장하지 않았습니다. 교통량 검사 오류:\n{e}")
            return False
        finally:
            tmp.unlink(missing_ok=True)
        new = not self.path.exists()
        if not new:
            bak = self.path.with_name(self.path.name + ".bak")
            if not bak.exists():
                bak.write_bytes(self.path.read_bytes())
        df.to_csv(self.path, index=False, encoding="utf-8-sig")
        self.df = df
        log.info("교통량 저장: %s (%d행)", self.path, len(df))
        if new:
            self.need_file.emit(str(self.path))
        self.saved.emit(str(self.path))
        return True


# ================================================================ 차로 수 편집(roads_override.csv) — 음원선 위치(RLS-19 3.3.1)
# (열, 머리글, 편집 가능, 선택지, 설명)
LANE_VIEW = [
    ("ufid", "UFID", False, None, "수치지도 도로 객체 번호"),
    ("rname", "도로명", False, None, None),
    ("oneway", "일방", False, None, "수치지도 일방통행 여부"),
    ("lanes", "지도 차로", False, None, "수치지도 전체 차로 수(양방향 합)"),
    ("width", "도로폭 m", False, None, "수치지도 도로폭"),
    ("lanes_fwd", "정방향 차로", True, None, "디지타이즈 방향(지도에서 화살표 방향) 차로 수. 일방통행은 전체 차로 수"),
    ("lanes_bwd", "역방향 차로", True, None, "반대 방향 차로 수. 일방통행은 비움"),
    ("lanes_basis", "차로 근거", True, ["", "checked", "assumed"], "checked = 지도·현장 확인, assumed = 가정"),
    ("oneway_dir", "일방 진행", True, ["", "1", "-1"], "일방통행만: 1 = 디지타이즈 방향으로 진행, -1 = 반대"),
    ("oneway_dir_basis", "진행 근거", True, ["", "checked", "assumed"], None),
    ("note", "메모", True, None, None),
    ("_rule", "적용", False, None, "override = 확인값, map_half·ceil = 지도 차로 수를 반으로, oneway_map = 일방"),
    ("_wl", "차로폭 m", False, None, "설정 lane_width (auto = 도로폭/차로 수, 2.75~3.5 m)"),
    ("_off", "음원선 위치 m", False, None, "중심선에서 음원선까지 거리(+ = 디지타이즈 방향 왼쪽). RLS-19 3.3.1 그림 6"),
]
LANE_EDIT = [c for c, _h, e, *_ in LANE_VIEW if e]


class LanesEditor(QtWidgets.QWidget):
    """도로 객체별 방향별 차로 수(확인값)를 고쳐 roads_override.csv 로 저장. 고치면 음원선 위치를 바로 다시 계산해 보여 줌
    (계산 엔진 sori.build.lane_layout·source_offsets 그대로). 행을 고르고 ‘지도에서 보기’ → 그 도로와 음원선을 지도에 표시"""
    saved = QtCore.Signal(str)
    need_file = QtCore.Signal(str)
    show_on_map = QtCore.Signal(object)            # (도로 도형, [(오프셋, 정방향?)], 제목)

    def __init__(self):
        super().__init__()
        self.cfg, self.roads, self.path, self.extra = None, None, None, None
        self._busy = False
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.lbl = QtWidgets.QLabel("설정 파일을 열고 한 번 계산하면(도로 객체 roads.gpkg 생김) 차로 수를 고칠 수 있습니다.")
        self.lbl.setWordWrap(True)
        lay.addWidget(self.lbl)
        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel("도로명 찾기"))
        self.ed_find = QtWidgets.QLineEdit(placeholderText="예: 사가정로")
        self.ed_find.textChanged.connect(self._filter)
        top.addWidget(self.ed_find, 1)
        self.chk_minor = QtWidgets.QCheckBox("1차로 도로도 보기")
        self.chk_minor.toggled.connect(self._filter)
        top.addWidget(self.chk_minor)
        self.chk_inside = QtWidgets.QCheckBox("격자 계산 범위 안만")
        self.chk_inside.setChecked(True)
        self.chk_inside.setToolTip("도로 객체는 반사·전파 탐색 때문에 계산 범위보다 넓게 만들어짐 — 끄면 전부")
        self.chk_inside.toggled.connect(self._filter)
        top.addWidget(self.chk_inside)
        self.chk_edited = QtWidgets.QCheckBox("확인값 넣은 도로만")
        self.chk_edited.toggled.connect(self._filter)
        top.addWidget(self.chk_edited)
        lay.addLayout(top)
        self.tbl = QtWidgets.QTableWidget(0, len(LANE_VIEW))
        self.tbl.setHorizontalHeaderLabels([h for _, h, *_ in LANE_VIEW])
        for j, (_c, _h, _e, ch, tip) in enumerate(LANE_VIEW):
            if tip:
                self.tbl.horizontalHeaderItem(j).setToolTip(tip)
            if ch:
                self.tbl.setItemDelegateForColumn(j, ChoiceDelegate(ch, self.tbl))
        self.tbl.setAlternatingRowColors(True)
        self.tbl.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tbl.itemChanged.connect(self._changed)
        self.tbl.cellDoubleClicked.connect(lambda i, j: LANE_VIEW[j][2] or self.map_selected())
        lay.addWidget(self.tbl, 1)
        row = QtWidgets.QHBoxLayout()
        b = QtWidgets.QPushButton("지도에서 보기")
        b.setToolTip("고른 도로와 방향별 음원선(지금 표의 차로 수 기준)을 지도에 표시. 읽기 전용 칸을 두 번 클릭해도 됨")
        b.clicked.connect(self.map_selected)
        row.addWidget(b)
        b = QtWidgets.QPushButton("확인값 지우기")
        b.setToolTip("고른 행의 확인값 칸을 비움 → 수치지도 차로 수로 돌아감")
        b.clicked.connect(self.clear_selected)
        row.addWidget(b)
        self.lbl_sum = QtWidgets.QLabel("")
        row.addWidget(self.lbl_sum, 1)
        self.btn_reset = QtWidgets.QPushButton("되돌리기")
        self.btn_reset.clicked.connect(lambda: self.cfg is not None and self.load(self.cfg, self.roads))
        self.btn_save = QtWidgets.QPushButton("저장 (⌘S)" if sys.platform == "darwin" else "저장 (Ctrl+S)")
        self.btn_save.setToolTip("확인값이 있는 행만 도로 확인값 CSV(roads_override.csv)에 씀")
        self.btn_save.clicked.connect(self.save)
        for x in (self.btn_reset, self.btn_save):
            x.setMinimumWidth(110)
            row.addWidget(x)
        lay.addLayout(row)
        self.setEnabled(False)

    def load(self, cfg, roads):
        """roads: 결과 폴더 roads.gpkg(GeoDataFrame). 확인값 파일이 있으면 그 값을 칸에 채움"""
        from sori.inputs import read_csv_any
        self.cfg, self.roads = cfg, roads
        if roads is None or not len(roads):
            self.tbl.setRowCount(0)
            self.lbl.setText("도로 객체가 아직 없습니다 — 홈 ▸ ‘지도 확인 · 계산’에서 한 번 계산하면(결과 폴더에 roads.gpkg) "
                             "도로별 차로 수를 고칠 수 있습니다.")
            self.setEnabled(False)
            return
        p = (cfg["input"].get("road_override") or {}).get("path")
        base = Path(cfg["_config_path"]).parent
        self.path = Path(p) if p else base / "roads_override.csv"
        ov = {}
        self.extra = pd.DataFrame()
        if self.path.exists():
            df = read_csv_any(self.path)
            df.columns = [str(c).strip().lower() if str(c).strip().lower() in LANE_EDIT + ["ufid"] else c
                          for c in df.columns]
            ov = {r["ufid"].strip(): r for _, r in df.iterrows() if str(r.get("ufid", "")).strip()}
            known = set(roads.ufid)
            self.extra = df[~df["ufid"].str.strip().isin(known)]        # 계산 영역 밖 UFID 행은 그대로 보존
            msg = f"도로 확인값 파일: {self.path} ({len(ov)}행)"
        else:
            msg = f"도로 확인값 파일이 없습니다 — 저장하면 {self.path} 를 새로 만들고 설정 파일에 넣음"
        self.lbl.setText(msg + "\n수치지도는 전체 차로 수만 있어 지금은 반씩 나눔(홀수는 설정 odd_lane_rule). 실제 방향별 "
                               "차로 수를 넣으면 음원선이 그 위치로 옮겨짐(RLS-19 3.3.1). 정·역방향은 도로를 그린 방향 기준 — "
                               "‘지도에서 보기’로 화살표 확인. 저장 뒤 ‘계산 실행’을 다시 해야 결과에 반영")
        from shapely.geometry import box
        from sori.config import grid_bbox
        try:
            inside = set(roads[roads.intersects(box(*grid_bbox(cfg)))].ufid)
        except Exception:
            inside = set(roads.ufid)
        self._inside = inside
        self._busy = True
        self.tbl.setRowCount(0)
        R = roads.sort_values(["rname", "ufid"])
        for _, r in R.iterrows():
            o = ov.get(r.ufid, {})
            i = self.tbl.rowCount()
            self.tbl.insertRow(i)
            vals = {"ufid": r.ufid, "rname": r.rname or r.axis or "", "oneway": "일방" if bool(r.oneway) else "",
                    "lanes": str(int(r.lanes)), "width": "" if pd.isna(r.width) else f"{float(r.width):g}"}
            for c in LANE_EDIT:
                v = o.get(c, "") if len(o) else ""
                vals[c] = "" if pd.isna(v) else str(v).strip()
            for j, (c, _h, ed, *_rest) in enumerate(LANE_VIEW):
                it = QtWidgets.QTableWidgetItem(vals.get(c, ""))
                if not ed:
                    it.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                    if c.startswith("_"):
                        it.setBackground(QtGui.QColor("#eef3fa"))
                self.tbl.setItem(i, j, it)
        self._busy = False
        for i in range(self.tbl.rowCount()):
            self._recalc(i)
        self.tbl.resizeColumnsToContents()
        self.tbl.setColumnWidth(0, min(self.tbl.columnWidth(0), 200))
        self._filter()
        self.setEnabled(True)

    def _col(self, c):
        return next(j for j, (k, *_) in enumerate(LANE_VIEW) if k == c)

    def _get(self, i, c):
        it = self.tbl.item(i, self._col(c))
        return it.text().strip() if it else ""

    def _road(self, i):
        return self.roads[self.roads.ufid == self._get(i, "ufid")].iloc[0]

    def _errors(self, i):
        """[(열, 이유)] — 저장을 막는 오류(빨강)와 경고(노랑, 저장은 됨) 구분: (열, 이유, 막음?)"""
        out = []
        ow = bool(self._get(i, "oneway"))
        vals = {}
        for c in ("lanes_fwd", "lanes_bwd", "oneway_dir"):
            s = self._get(i, c)
            if s:
                try:
                    f = float(s)
                    if f != int(f):
                        raise ValueError
                    vals[c] = int(f)
                except ValueError:
                    out.append((c, "정수", True))
        lf, lb = vals.get("lanes_fwd"), vals.get("lanes_bwd")
        if lf is not None and lf < 1:
            out.append(("lanes_fwd", "1 이상", True))
        if lb is not None and lb < 0:
            out.append(("lanes_bwd", "0 이상", True))
        if "oneway_dir" in vals and vals["oneway_dir"] not in (1, -1):
            out.append(("oneway_dir", "1 또는 -1", True))
        for c in ("lanes_basis", "oneway_dir_basis"):
            if self._get(i, c) not in ("", "checked", "assumed"):
                out.append((c, "checked 또는 assumed", True))
        lanes = int(self._get(i, "lanes") or 0)
        if not ow and (lf is None) != (lb is None):
            out.append(("lanes_bwd" if lb is None else "lanes_fwd", "양방향은 두 방향 모두 넣어야 반영됨", False))
        if not ow and lf is not None and lb is not None and lf + lb != lanes:
            out.append(("lanes_fwd", f"합 {lf + lb} ≠ 지도 차로 {lanes}(반영은 됨)", False))
        if ow and lb:
            out.append(("lanes_bwd", "일방통행은 비움", False))
        if not ow and "oneway_dir" in vals:
            out.append(("oneway_dir", "양방향 도로에는 쓰지 않음", False))
        return out, vals

    def _layout(self, i, vals=None):
        """지금 칸 값으로 (nd_fwd, nd_bwd, 차로폭, 규칙, 오프셋 목록)"""
        from sori.build import lane_layout, source_offsets
        if vals is None:
            vals = self._errors(i)[1]
        r = self._road(i)
        ow = bool(r.oneway)
        w = None if pd.isna(r.width) else float(r.width)
        nd, nb, wl, rule = lane_layout(self.cfg, int(r.lanes), w, ow, vals.get("lanes_fwd"), vals.get("lanes_bwd"))
        single = (not ow) and nb == 0
        offs = source_offsets(nd, nb, wl, ow, single=single, oneway_dir=vals.get("oneway_dir") or 1)
        return nd, nb, wl, rule, offs

    def _recalc(self, i):
        errs, vals = self._errors(i)
        bad = {c: (why, block) for c, why, block in errs}
        self._busy = True
        for c in LANE_EDIT:
            it = self.tbl.item(i, self._col(c))
            if c in bad:
                it.setBackground(QtGui.QColor("#ffd6d6" if bad[c][1] else "#fff2c2"))
                it.setToolTip(bad[c][0])
            else:
                it.setBackground(QtGui.QBrush())
                it.setToolTip("")
        try:
            if any(b for _, b in bad.values()):
                raise ValueError
            nd, nb, wl, rule, offs = self._layout(i, vals)
            self.tbl.item(i, self._col("_rule")).setText(f"{rule} ({nd}+{nb})" if nb else f"{rule} ({nd})")
            self.tbl.item(i, self._col("_wl")).setText(f"{wl:.2f}")
            self.tbl.item(i, self._col("_off")).setText(" / ".join(f"{o:+.2f}" for o, _ in offs))
        except (ValueError, IndexError):
            for c in ("_rule", "_wl", "_off"):
                self.tbl.item(i, self._col(c)).setText("")
        self._busy = False

    def _changed(self, item):
        if not self._busy and LANE_VIEW[item.column()][2]:
            self._recalc(item.row())
            self._count()

    def _edited(self, i):
        return any(self._get(i, c) for c in LANE_EDIT)

    def _count(self):
        n = sum(self._edited(i) for i in range(self.tbl.rowCount()))
        vis = sum(not self.tbl.isRowHidden(i) for i in range(self.tbl.rowCount()))
        self.lbl_sum.setText(f"  보이는 도로 {vis} / 전체 {self.tbl.rowCount()} · 확인값 {n}개")

    def _filter(self, *_):
        q = self.ed_find.text().strip()
        mn = int(self.cfg["model"].get("main_min_lanes", 2)) if self.cfg else 2
        for i in range(self.tbl.rowCount()):
            hide = bool(q) and q not in self._get(i, "rname") and q not in self._get(i, "ufid")
            hide |= not self.chk_minor.isChecked() and int(self._get(i, "lanes") or 0) < mn and not self._edited(i)
            hide |= self.chk_edited.isChecked() and not self._edited(i)
            hide |= (self.chk_inside.isChecked() and self._get(i, "ufid") not in getattr(self, "_inside", set())
                     and not self._edited(i))
            self.tbl.setRowHidden(i, hide)
        self._count()

    def _sel(self):
        return sorted({ix.row() for ix in self.tbl.selectedIndexes()})

    def clear_selected(self):
        for i in self._sel():
            for c in LANE_EDIT:
                self.tbl.item(i, self._col(c)).setText("")

    def map_selected(self):
        rows = self._sel()
        if not rows:
            return
        i = rows[0]
        try:
            nd, nb, wl, rule, offs = self._layout(i)
        except (ValueError, IndexError):
            return
        r = self._road(i)
        self.show_on_map.emit((r.geometry, offs,
                               f"{self._get(i, 'rname')} {r.ufid[-6:]} · {rule} {nd}+{nb}차로 · 차로폭 {wl:.2f} m"))

    def save(self):
        import copy
        from sori.inputs import load_road_override
        bad = []
        for i in range(self.tbl.rowCount()):
            for c, why, block in self._errors(i)[0]:
                if block:
                    bad.append(f"{self._get(i, 'rname')} {self._get(i, 'ufid')[-6:]} {c}: {why}")
        if bad:
            QtWidgets.QMessageBox.warning(self, "SORI — 차로 수", "저장하지 않았습니다. 빨간 칸을 고치세요.\n\n"
                                          + "\n".join(bad[:15]) + ("\n…" if len(bad) > 15 else ""))
            return False
        rows = [{c: self._get(i, c) for c in ["ufid"] + LANE_EDIT} for i in range(self.tbl.rowCount()) if self._edited(i)]
        df = pd.DataFrame(rows, columns=["ufid"] + LANE_EDIT)
        if self.extra is not None and len(self.extra):
            df = pd.concat([df, self.extra.reindex(columns=df.columns).fillna("")], ignore_index=True)
        tmp = self.path.with_name(".sori_check_override.csv")
        try:
            df.to_csv(tmp, index=False, encoding="utf-8-sig")
            cfg = copy.deepcopy(self.cfg)
            cfg["input"].setdefault("road_override", {})["path"] = str(tmp)
            load_road_override(cfg)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "SORI — 차로 수", f"저장하지 않았습니다. 확인값 검사 오류:\n{e}")
            return False
        finally:
            tmp.unlink(missing_ok=True)
        new = not self.path.exists()
        if not new:
            bak = self.path.with_name(self.path.name + ".bak")
            if not bak.exists():
                bak.write_bytes(self.path.read_bytes())
        df.to_csv(self.path, index=False, encoding="utf-8-sig")
        log.info("도로 확인값 저장: %s (%d행)", self.path, len(df))
        if new or not (self.cfg["input"].get("road_override") or {}).get("path"):
            self.need_file.emit(str(self.path))
        self.saved.emit(str(self.path))
        return True


# ================================================================ 홈 화면(프로젝트 고르기 → 작업 고르기)
def recent_projects():
    """최근에 연 설정 파일 [(경로, 프로젝트 이름)], 새것부터. 없어진 파일은 뺌"""
    try:
        rows = json.loads(G.load_settings().get("recent") or "[]")
    except ValueError:
        rows = []
    return [(p, n) for p, n in rows if Path(p).is_file()]


def add_recent(path, name):
    rows = [(p, n) for p, n in recent_projects() if Path(p).resolve() != Path(path).resolve()]
    G.save_settings(recent=json.dumps([(str(path), name)] + rows[:7], ensure_ascii=False))


# 작업마다 처음 펴 둘 창(나머지는 접음 — 도구 줄 오른쪽 ‘창’ 단추나 Ctrl+1·2·3 으로 언제든 폄). 2026-11-12
TASK_DOCKS = {"map": {"프로젝트", "속성"}, "run": {"계산·결과 표"}, "check": {"계산·결과 표"},
              "grid": {"속성"}, "facade": {"속성"}, "section": {"속성"}}
# 지도 확인 · 계산: 결과가 있으면 아래 창을 펴지 않음(지도가 창 높이에 맞춰지므로 아래 창 230 px 만큼 지도가 작아짐 —
# 2026-11-14 전농사거리 1600 × 900 창에서 1.00 → 1.41 px/m). 결과가 없으면 계산부터 하도록 폄(enter_task)


RED = "#d00000"                                   # 앱 기획안 표지(2026-11-12)의 빨강
FACADE3D_COLORS = dict(wall="#e2e2e2", roof="#c9c9c9", ground="#f1f1ee", sky="#ffffff")   # 앱 3D: 소음 색과 겹치지 않는 회색
# 지도 도구 줄: 흰 바탕, 켠 단추는 빨간 글자(기획안 강조색), 묶음 사이 옅은 세로선
# 2026-11-14: 여백을 줄여 높이 53 → 41 px(지도 창 높이 확보), 필요한 폭 1620 → 1406 px(좁은 창에서 오른쪽 ‘창’ 단추가 잘리던 문제 — 더 좁으면 » 단추로)
TOOLBAR_CSS = f"""
QToolBar#view {{ background: #ffffff; border: none; border-bottom: 1px solid #dddddd; padding: 2px 6px; spacing: 1px; }}
QToolBar#view QToolButton {{ font-size: 13px; color: #333333; padding: 3px 7px; border: 1px solid transparent;
                            border-radius: 6px; }}
QToolBar#view QToolButton:hover {{ background: #f2f2f2; }}
QToolBar#view QToolButton:checked {{ color: {RED}; background: #fdecec; border-color: #f2b8b8; font-weight: 700; }}
QToolBar#view QToolButton:disabled {{ color: #b8b8b8; }}
QToolBar#view QToolButton::menu-indicator {{ image: none; width: 0; }}
QToolBar#view QLabel {{ color: #777777; font-size: 12px; padding: 0 2px 0 6px; }}
QToolBar#view QComboBox {{ font-size: 13px; padding: 1px 6px; }}
QToolBar#view::separator {{ background: #e2e2e2; width: 1px; margin: 4px 5px; }}
"""
# 오른쪽 속성 창: 칸 줄 없이 줄마다 옅은 회색, 항목 이름은 회색, 값은 굵게
PROPS_CSS = """
QTableWidget { border: none; font-size: 13px; background: #ffffff; alternate-background-color: #f6f6f6;
               selection-background-color: #fdecec; selection-color: #222222; }
QTableWidget::item { padding: 4px 8px; }
"""
DOCK_CSS = "QDockWidget::title { background: #f4f4f4; padding: 6px 10px; } QDockWidget { font-size: 12px; color: #555555; }"


class View3DPage(QtWidgets.QWidget):
    """앱 안 3D 보기: 위쪽 띠(◀ 지도로 · 제목 · 브라우저로 열기) + 웹 3D 뷰어(noise3d.html, three.js).
    웹 화면은 처음 열 때 만듦(앱 시작이 느려지지 않게)"""
    back = QtCore.Signal()

    def __init__(self):
        super().__init__()
        self.path = None
        self.web = None
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        bar = QtWidgets.QFrame()
        bar.setStyleSheet("QFrame { background: #ffffff; border-bottom: 1px solid #dddddd; }")
        h = QtWidgets.QHBoxLayout(bar)
        h.setContentsMargins(10, 6, 10, 6)
        css = ("QPushButton { font-size: 13px; color: #333333; padding: 5px 12px; border: 1px solid #d0d0d0;"
               " border-radius: 6px; background: #ffffff; } QPushButton:hover { background: #f2f2f2; }")
        b = QtWidgets.QPushButton("◀ 지도로")
        b.setStyleSheet(css)
        b.clicked.connect(self.back.emit)
        h.addWidget(b)
        h.addSpacing(12)
        mark = QtWidgets.QFrame()
        mark.setFixedSize(5, 22)
        mark.setStyleSheet(f"background: {RED}; border: none;")
        h.addWidget(mark)
        self.title = QtWidgets.QLabel("3D 보기")
        self.title.setStyleSheet("font-size: 15px; font-weight: 800; color: #222222; padding-left: 6px; border: none;")
        h.addWidget(self.title)
        hint = QtWidgets.QLabel("왼쪽 끌기 = 돌리기 · 오른쪽 끌기 = 옮기기 · 휠 = 확대")
        hint.setStyleSheet("font-size: 12px; color: #777777; padding-left: 14px; border: none;")
        h.addWidget(hint)
        h.addStretch(1)
        self.btn_ext = QtWidgets.QPushButton("브라우저로 열기")
        self.btn_ext.setStyleSheet(css)
        self.btn_ext.clicked.connect(lambda: self.path and QtGui.QDesktopServices.openUrl(
            QtCore.QUrl.fromLocalFile(str(self.path))))
        h.addWidget(self.btn_ext)
        v.addWidget(bar)
        self.holder = QtWidgets.QVBoxLayout()
        v.addLayout(self.holder, 1)

    def load(self, path, title):
        self.path = Path(path)
        self.title.setText(title)
        if self.web is None:
            self.web = QWebEngineView()
            self.web.loadFinished.connect(self._light)
            self.holder.addWidget(self.web, 1)
        self.web.load(QtCore.QUrl.fromLocalFile(str(self.path)))

    def _light(self, ok):
        """뷰어는 시스템 다크 모드를 따르지만, 앱은 밝은 화면으로 고정 → 뷰어도 밝게"""
        if ok:
            self.web.page().runJavaScript("document.documentElement.dataset.theme='light'")


class SplashPage(QtWidgets.QWidget):
    """앱을 켜면 처음 보이는 표지. 앱 기획안 표지 틀을 따름: 흰 바탕, 가운데 빨간 큰 제목과 양옆 빨간 선,
    아래 굵은 부제·회색 설명, 오른쪽 위 작은 머리글, 아래 회색 선과 소속·판 번호, 왼쪽 아래 AI 활용 고지.
    아무 곳이나 클릭하거나 아무 키나 누르면 홈으로(started 신호)"""
    started = QtCore.Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("splash")
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setAutoFillBackground(True)
        self.setStyleSheet(f"""
            #splash {{ background: #ffffff; }}
            #splash QLabel {{ background: transparent; }}
            #splash QLabel#run {{ font-size: 13px; color: #6b6b6b; letter-spacing: 1px; }}
            #splash QLabel#title {{ font-size: 84px; font-weight: 800; color: {RED}; padding: 0 28px; }}
            #splash QFrame#rule {{ background: {RED}; border: none; }}
            #splash QLabel#sub {{ font-size: 26px; font-weight: 700; color: #333333; }}
            #splash QLabel#desc {{ font-size: 15px; color: #8a8a8a; }}
            #splash QLabel#go {{ font-size: 15px; font-weight: 700; color: {RED}; border: 2px solid {RED};
                                 border-radius: 20px; padding: 8px 30px; background: #ffffff; }}
            #splash QLabel#note {{ font-size: 12px; color: #8a8a8a; }}
            #splash QLabel#who {{ font-size: 13px; color: #333333; }}
            #splash QFrame#foot {{ background: #b1b1b1; border: none; }}
        """)
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(0, 22, 0, 26)
        top = QtWidgets.QHBoxLayout()
        top.setContentsMargins(0, 0, 36, 0)
        top.addStretch(1)
        top.addWidget(QtWidgets.QLabel("SORI · Road Traffic Noise Map", objectName="run"))
        v.addLayout(top)
        v.addStretch(5)
        # 제목 줄: ──── SORI ──── (선은 제목 아래쪽에 맞춤)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(0)
        for i in range(3):
            if i == 1:
                row.addWidget(QtWidgets.QLabel("SORI", objectName="title"), 0, Qt.AlignBottom)
                continue
            ln = QtWidgets.QFrame(objectName="rule")
            ln.setFixedHeight(7)
            ln.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            col = QtWidgets.QVBoxLayout()
            col.addStretch(1)
            col.addWidget(ln)
            col.addSpacing(16)
            row.addLayout(col, 1)
        v.addLayout(row)
        v.addSpacing(26)
        for text, name in (("국내 수치지도로 만드는 도로교통소음 예측 · 소음지도 앱", "sub"),
                           ("SORI = Simulation of Outdoor Road-noise Impact · 우리말 ‘소리’ — "
                            "RLS-19 음원 + ISO 9613-2 / RLS-19 전파, 격자 · 건물 벽면 · 단면 소음지도", "desc")):
            lb = QtWidgets.QLabel(text, objectName=name)
            lb.setAlignment(Qt.AlignHCenter)
            lb.setWordWrap(True)
            v.addWidget(lb)
            v.addSpacing(10)
        v.addSpacing(30)
        self.go = QtWidgets.QLabel("클릭하면 시작합니다", objectName="go")
        v.addWidget(self.go, 0, Qt.AlignHCenter)
        v.addStretch(6)
        bot = QtWidgets.QHBoxLayout()
        bot.setContentsMargins(30, 0, 30, 0)
        left = QtWidgets.QVBoxLayout()
        left.addWidget(QtWidgets.QLabel(f"※ {AI_NOTICE}  계산 결과의 검토·검증 책임은 사용자에게 있음.", objectName="note"))
        left.addSpacing(8)
        ln = QtWidgets.QFrame(objectName="foot")
        ln.setFixedHeight(2)
        left.addWidget(ln)
        bot.addLayout(left, 3)
        bot.addSpacing(40)
        bot.addWidget(QtWidgets.QLabel(f"서울시립대 환경공학부 전현수   |   v{__version__}", objectName="who"), 0, Qt.AlignBottom)
        v.addLayout(bot)
        for lb in self.findChildren(QtWidgets.QLabel):
            lb.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._blink = QtCore.QTimer(self, interval=900)
        self._blink.timeout.connect(self._toggle_go)
        self._on = True

    def _toggle_go(self):
        self._on = not self._on
        eff = self.go.graphicsEffect() or QtWidgets.QGraphicsOpacityEffect(self.go)
        eff.setOpacity(1.0 if self._on else 0.35)
        self.go.setGraphicsEffect(eff)

    def showEvent(self, e):
        self._blink.start()
        self.setFocus()
        super().showEvent(e)

    def hideEvent(self, e):
        self._blink.stop()
        super().hideEvent(e)

    def paintEvent(self, e):                              # 스타일시트 배경을 그리게(QWidget 하위 클래스)
        opt = QtWidgets.QStyleOption()
        opt.initFrom(self)
        qp = QtGui.QPainter(self)
        self.style().drawPrimitive(QtWidgets.QStyle.PE_Widget, opt, qp, self)
        qp.end()

    def mouseReleaseEvent(self, e):
        self.started.emit()

    def keyPressEvent(self, e):
        self.started.emit()


class HomePage(QtWidgets.QWidget):
    """홈(2026-11-14 개편): 작업 단추 셋 — 데이터 입력 / 입력 지도 확인(수치지도만, 소음 없음) / 지도 확인·계산
    (계산 실행 + 격자 소음지도, 건물 클릭 → 3D 파사드). 순서 강제(입력 → 계산 → 결과 단계 줄)는 없앰.
    프로젝트 이름 + 한 줄 상태, 프로젝트 열기·예제·최근 목록은 ‘프로젝트 바꾸기’ 펼침 안에. 자가 점검은 ‘계산’ 차림표"""
    task = QtCore.Signal(str)

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setObjectName("home")
        self.setAutoFillBackground(True)
        self.setStyleSheet(f"""
            #home {{ background: #ffffff; }}
            #home QLabel {{ background: transparent; }}
            #home QLabel#run {{ font-size: 13px; color: #6b6b6b; letter-spacing: 1px; }}
            #home QFrame#bar {{ background: {RED}; border: none; }}
            #home QLabel#name {{ font-size: 34px; font-weight: 800; color: #222222; }}
            #home QLabel#state {{ font-size: 15px; color: #6b6b6b; }}
            #home QToolButton#switch {{ font-size: 14px; color: #333333; border: 1px solid #c8c8c8; border-radius: 6px;
                                        padding: 6px 14px; background: #ffffff; }}
            #home QToolButton#switch:hover {{ background: #f2f2f2; }}
            #home QToolButton#switch::menu-indicator {{ image: none; width: 0; }}
            #home QPushButton#go {{ font-size: 19px; font-weight: 800; color: #ffffff; background: {RED}; border: none;
                                   border-radius: 8px; padding: 16px 22px; text-align: left; }}
            #home QPushButton#go:hover {{ background: #b00000; }}
            #home QPushButton#go:disabled {{ background: #e3e3e3; color: #a8a8a8; }}
            #home QPushButton#go2 {{ font-size: 19px; font-weight: 800; color: {RED}; background: #ffffff;
                                    border: 2px solid {RED}; border-radius: 8px; padding: 14px 20px; text-align: left; }}
            #home QPushButton#go2:hover {{ background: #fdecec; }}
            #home QPushButton#go2:disabled {{ border-color: #d6d6d6; color: #b0b0b0; }}
            #home QLabel#desc {{ font-size: 13px; color: #6b6b6b; }}
            #home QLabel#hint {{ font-size: 14px; color: #8a8a8a; }}
            #home QFrame#foot {{ background: #b1b1b1; border: none; }}
            #home QLabel#tagr {{ font-size: 11px; font-weight: 700; color: #ffffff; background: {RED}; padding: 3px 14px; }}
        """)
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(0, 22, 0, 22)
        top = QtWidgets.QHBoxLayout()
        top.setContentsMargins(0, 0, 36, 0)
        top.addStretch(1)
        top.addWidget(QtWidgets.QLabel("SORI · Road Traffic Noise Map", objectName="run"))
        v.addLayout(top)
        v.addStretch(4)
        mid = QtWidgets.QHBoxLayout()
        mid.addStretch(1)
        col = QtWidgets.QVBoxLayout()
        head = QtWidgets.QHBoxLayout()
        bar = QtWidgets.QFrame(objectName="bar")
        bar.setFixedWidth(7)
        head.addWidget(bar)
        head.addSpacing(14)
        self.lbl_name = QtWidgets.QLabel(objectName="name")
        head.addWidget(self.lbl_name)
        head.addStretch(1)
        col.addLayout(head)
        col.addSpacing(6)
        self.lbl_proj = QtWidgets.QLabel(objectName="state")      # 한 줄 상태(경로는 풍선말)
        col.addWidget(self.lbl_proj)
        col.addSpacing(14)
        self.btn_switch = QtWidgets.QToolButton(objectName="switch", text="프로젝트 바꾸기  ▾")
        self.btn_switch.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.menu = QtWidgets.QMenu(self)
        self.btn_switch.setMenu(self.menu)
        col.addWidget(self.btn_switch, 0, Qt.AlignLeft)
        col.addSpacing(40)
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(22)
        grid.setVerticalSpacing(16)
        self.task_buttons = {}
        for i, (key, text, desc, obj) in enumerate((
                ("input", "데이터 입력", "수치지도 폴더·교통량·차로 수·설정값", "go"),
                ("map", "입력 지도 확인", "수치지도의 건물·도로·등고선과 계산 범위(소음 표시 없음)", "go2"),
                ("grid", "지도 확인 · 계산", "계산 실행, 격자 소음지도 — 건물을 누르면 3D 파사드를 함께", "go2"))):
            b = QtWidgets.QPushButton(text, objectName=obj)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedWidth(250)
            b.clicked.connect(lambda _=False, k=key: self.task.emit(k))
            grid.addWidget(b, i, 0)
            lab = QtWidgets.QLabel(desc, objectName="desc")
            lab.setWordWrap(True)
            grid.addWidget(lab, i, 1)
            self.task_buttons[key] = b
        self.btn_input = self.task_buttons["input"]
        col.addLayout(grid)
        col.addSpacing(14)
        self.lbl_hint = QtWidgets.QLabel(objectName="hint")
        col.addWidget(self.lbl_hint)
        cw = QtWidgets.QWidget()
        cw.setLayout(col)
        cw.setFixedWidth(620)
        mid.addWidget(cw)
        mid.addStretch(1)
        v.addLayout(mid)
        v.addStretch(6)
        bot = QtWidgets.QHBoxLayout()
        bot.setContentsMargins(30, 0, 30, 0)
        ln = QtWidgets.QFrame(objectName="foot")
        ln.setFixedHeight(2)
        bot.addWidget(ln, 1, Qt.AlignVCenter)
        bot.addWidget(QtWidgets.QLabel("HOME", objectName="tagr"))
        v.addLayout(bot)
        self.recent_actions = []
        self.refresh()

    def _fill_menu(self):
        w, m = self.win, self.menu
        m.clear()
        for text, fn in (("설정 파일(config.yaml) 열기…", w.pick_cfg), ("결과 폴더만 열기…", w.pick_out)):
            m.addAction(text, fn)
        m.addSeparator()
        m.addAction("예제: 가상 교차로(실제 지역 아님)", w.load_example)
        m.addAction("예제: 전농사거리(수치지도는 따로 받음)…", w.load_jeonnong)
        self.recent_actions = []
        rec = recent_projects()
        if rec:
            m.addSeparator()
            m.addSection("최근 프로젝트")
            for p, n in rec:
                short = "…/" + "/".join(Path(p).parts[-3:-1])
                a = m.addAction(f"{n}   {short}", lambda p=p: w.open_config(p))
                a.setToolTip(p)
                self.recent_actions.append(a)

    def refresh(self):
        w = self.win
        self._fill_menu()
        if w.cfg is None and not w.out:
            self.lbl_name.setText("프로젝트를 여세요")
            self.lbl_proj.setText("설정 파일(config.yaml)이나 예제를 열면 작업을 고를 수 있습니다.")
            self.lbl_proj.setToolTip("")
            self.btn_switch.setText("프로젝트 열기  ▾")
            for b in self.task_buttons.values():
                b.setEnabled(False)
            self.lbl_hint.setText("")
            return
        R = w.R
        name = (w.cfg or {}).get("project", {}).get("name") or (Path(w.out).name if w.out else "")
        ri = R.get("run_info") or {}
        res = "·".join(lbl for k, lbl in (("grid", "격자"), ("facade", "벽면"), ("points", "점 수음점")) if k in R)
        when = str(ri.get("time") or "")[:16]
        self.lbl_name.setText(name)
        self.lbl_proj.setText((f"계산 결과 있음: {res}" + (f" ({when})" if when else "")) if res else "아직 계산 전")
        self.lbl_proj.setToolTip(f"설정 파일: {w.cfg_path or '(없음 — 결과 폴더만 열림)'}\n결과 폴더: {w.out or '(없음)'}")
        self.btn_switch.setText("프로젝트 바꾸기  ▾")
        self.task_buttons["input"].setEnabled(w.cfg is not None)
        self.task_buttons["map"].setEnabled(w.cfg is not None)
        self.task_buttons["grid"].setEnabled(True)
        self.lbl_hint.setText("" if w.cfg is not None else "결과 폴더만 열렸습니다. 입력하려면 설정 파일을 여세요.")


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
        self.view = "grid"        # grid = 격자 지도, facade = 건물 3D 파사드(view_bid), section = 단면
        self.view_bid = None
        self.sec_pts = []         # 단면 선 긋기 중 찍은 점
        self.barriers = []        # 방음벽 목록(sori.inputs.load_barriers 형식) — 설정 폴더의 barriers.csv
        self._bar_draw = None     # 방음벽 그리는 중 찍은 꼭짓점(그리는 중이 아니면 None)
        self._bar_art = []        # 지도 위 방음벽 그림 요소
        self._box_pts, self._box_art, self._bld_all, self._meas = [], [], None, None
        self.map_mode = "result"  # input = 입력 지도 확인(수치지도만, 소음 없음), result = 지도 확인 · 계산
        self._inlay = None        # 입력 지도 레이어 캐시 (설정 파일, 건물, 도로, 등고선)
        self.diff_base = None     # 전후 차이의 기준(대책 전) 결과 폴더
        self._diff = None         # (기준 폴더, 비교 폴더, 차이 표) — 한 번만 읽음
        self.paths = None         # 경로 보기: (경로 표, 수음점 정보) — 마지막으로 고른 수음점
        self._path_name = None    # 경로 보기 수음점이 점 수음점이면 그 이름
        self._split_auto = False  # 경로 보기가 나란히 보기를 켰음(경로 보기를 끄면 다시 끔)
        self._section_side = False  # 나란히 보기 오른쪽 창에 단면을 그리는 중(왼쪽 격자 지도에는 A–B 선)
        self._sec_rate = None     # 지난 단면 계산 속도(점/초) — 간격 고르기의 예상 시간
        self._path_art = []
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
        self.map.finished.connect(self._finish_barrier)
        self.map.moved.connect(self._on_map_move)
        # 나란히 보기: 왼쪽 격자 지도(self.map), 오른쪽 3D 파사드(self.map3d). 끄면 예전처럼 한 창에서 바꿔 봄
        self.map3d = MapView()
        self.map3d.message(MSG_3D_EMPTY)
        for mv in (self.map, self.map3d):
            mv.facade_opts.connect(lambda mv=mv: self._facade_opts_changed(mv))
        self.facade_bid = None    # 오른쪽 창에 보이는 건물(나란히 보기)
        self.split = QtWidgets.QSplitter(Qt.Horizontal)
        self.split.addWidget(self.map)
        self.split.addWidget(self.map3d)
        self.split.setChildrenCollapsible(False)
        self.split.setSizes([4, 1])                       # 건물을 고르기 전에는 오른쪽을 좁게(_fit_split)
        self.stack = QtWidgets.QStackedWidget()           # 0 = 홈 화면, 1 = 작업 화면(지도)
        self.setCentralWidget(self.stack)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["프로젝트"])
        self.tree.itemClicked.connect(self._on_tree)
        self.docks = {}                                   # 접이식 창(Dock): 이름 → QDockWidget
        self._dock("프로젝트", self.tree, Qt.LeftDockWidgetArea)

        self.props = QtWidgets.QTableWidget(0, 2)
        self.props.setHorizontalHeaderLabels(["항목", "값"])
        self.props.horizontalHeader().setStretchLastSection(True)
        self.props.horizontalHeader().hide()
        self.props.verticalHeader().hide()
        self.props.verticalHeader().setDefaultSectionSize(30)
        self.props.setShowGrid(False)
        self.props.setAlternatingRowColors(True)
        self.props.setWordWrap(False)
        self.props.setStyleSheet(PROPS_CSS)
        self.props.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.props.setFocusPolicy(Qt.NoFocus)
        pw = QtWidgets.QWidget()
        pl = QtWidgets.QVBoxLayout(pw)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(0)
        self.props_title = QtWidgets.QLabel("속성")
        self.props_title.setWordWrap(True)
        self.props_title.setStyleSheet(f"font-size: 15px; font-weight: 800; color: #222222; padding: 10px 12px 8px 10px;"
                                       f"border-left: 5px solid {RED}; background: #ffffff;")
        pl.addWidget(self.props_title)
        pl.addWidget(self.props, 1)
        d_props = self._dock("속성", pw, Qt.RightDockWidgetArea)
        self.editor = InputEditor()
        self.editor.saved.connect(self._on_input_saved)
        self.editor.pick_center.connect(self._start_pick_center)
        self.editor.preview.connect(self._preview_extent)
        self._picking_center = False
        self.input_page = QtWidgets.QWidget()           # 입력값 전용 화면(지도 없음)
        ip = QtWidgets.QVBoxLayout(self.input_page)
        ip.setContentsMargins(24, 14, 24, 10)
        # 위쪽 머리: ⌂ 홈 | 데이터 입력 (2026-11-14: 입력 → 계산 → 결과 단계 줄은 없앰 — 작업은 홈에서 고름)
        hd = QtWidgets.QHBoxLayout()
        bh = QtWidgets.QPushButton("⌂ 홈")
        bh.clicked.connect(self.show_home)
        hd.addWidget(bh)
        hd.addSpacing(14)
        bar = QtWidgets.QFrame()
        bar.setFixedSize(6, 26)
        bar.setStyleSheet(f"background: {RED}; border: none;")
        hd.addWidget(bar)
        tl = QtWidgets.QLabel("데이터 입력")
        tl.setStyleSheet("font-size: 22px; font-weight: 800; color: #222222; padding-left: 8px;")
        hd.addWidget(tl)
        hd.addStretch(1)
        ip.addLayout(hd)
        self.traffic = TrafficEditor()
        self.traffic.saved.connect(lambda p: self.lbl_status.setText(
            f"교통량 저장함: {p} — 결과는 홈 ▸ ‘지도 확인 · 계산’에서 다시 계산해야 바뀜"))
        self.traffic.need_file.connect(self._traffic_new_file)
        self.input_tabs = QtWidgets.QTabWidget()
        self.guide = InputGuide(self.editor)
        self.editor.changed.connect(self.guide.refresh)
        self.input_tabs.addTab(self.guide, "준비할 파일")
        self.input_tabs.addTab(self.editor, "설정값")
        self.input_tabs.addTab(self.traffic, "교통량")
        self.lanes = LanesEditor()
        self.lanes.saved.connect(lambda p: self.lbl_status.setText(
            f"차로 수 저장함: {p} — 음원선 위치는 홈 ▸ ‘지도 확인 · 계산’에서 다시 계산해야 결과에 반영"))
        self.lanes.need_file.connect(self._lanes_new_file)
        self.lanes.show_on_map.connect(self._show_lane_focus)
        self.input_tabs.addTab(self.lanes, "차로 수")
        ip.addWidget(self.input_tabs, 1)

        self.tabs = QtWidgets.QTabWidget()
        self.tabs.addTab(self._run_panel(), "계산 실행")
        self.logbox = QtWidgets.QPlainTextEdit(readOnly=True)
        self.logbox.setMaximumBlockCount(5000)
        self.tabs.addTab(self.logbox, "기록")
        self.m_points, self.m_terms, self.m_facade = FrameModel(), FrameModel(), FrameModel()
        self.m_paths = FrameModel()
        self.m_bld = FrameModel()
        self.m_roads = FrameModel()
        self.m_study = FrameModel()
        self.tabs.addTab(table_view(self.m_points), "점 수음점")
        self.tabs.addTab(table_view(self.m_terms), "감쇠항 (path_terms)")
        self.tabs.addTab(table_view(self.m_roads), "점 도로별 기여")
        self.tabs.addTab(table_view(self.m_facade), "벽면 (상위 200)")
        self.tabs.addTab(self._building_panel(), "건물별")
        self.tabs.addTab(table_view(self.m_paths), "경로 (기여 상위)")
        self.tabs.addTab(table_view(self.m_study), "방음벽 높이 비교")
        self.tabs.addTab(self._barrier_panel(), "방음벽")
        self._dock("계산·결과 표", self.tabs, Qt.BottomDockWidgetArea)

        # 지도 위 도구 줄(2026-11-12 저녁 정돈): 단계 | 보기(높이·시간대) | 보기 방식 | 저장 ▾ | (오른쪽) 창
        tb = self.tb_view = self.addToolBar("결과 보기")
        tb.setObjectName("view")
        tb.setMovable(False)
        tb.setStyleSheet(TOOLBAR_CSS)
        tb.addAction("⌂ 홈", self.show_home).setToolTip("홈 화면(데이터 입력·입력 지도 확인·지도 확인 · 계산)")
        self.lbl_mode = QtWidgets.QLabel("")                # 지금 작업 이름(입력 지도 확인 / 지도 확인 · 계산)
        self.lbl_mode.setStyleSheet(f"font-weight: 800; color: {RED}; padding: 0 10px;")
        tb.addWidget(self.lbl_mode)
        n0 = len(tb.actions())
        tb.addSeparator()
        tb.addWidget(QtWidgets.QLabel("높이"))
        self.cb_h = QtWidgets.QComboBox()
        tb.addWidget(self.cb_h)
        tb.addWidget(QtWidgets.QLabel("시간대"))
        self.cb_key = QtWidgets.QComboBox()
        for k, v in KEYS.items():
            self.cb_key.addItem(v, k)
        tb.addWidget(self.cb_key)
        self.cb_h.currentIndexChanged.connect(self.redraw)
        self.cb_key.currentIndexChanged.connect(self.redraw)
        tb.addSeparator()
        self.act_back = tb.addAction("◀ 격자 지도", self.show_grid)
        self.act_back.setEnabled(False)
        self.act_back.setVisible(False)                   # 한 창 보기에서 3D·단면을 볼 때만 보임(_set_back)
        self.act_split = tb.addAction("나란히 보기", self._toggle_split)
        self.act_split.setCheckable(True)
        self.act_split.setChecked(True)
        self.act_split.setToolTip("켜면 왼쪽 격자 지도는 그대로 두고, 클릭한 건물의 3D 파사드를 오른쪽 창에 함께 표시\n"
                                  "(건물 클릭 → 3D 파사드 소음지도, 마우스로 회전)")
        self.act_fval = tb.addAction("벽면 값", self.redraw)
        self.act_fval.setCheckable(True)
        self.act_fval.setChecked(False)     # 2026-11-12: 기본 끔 — 골목 건물이 빽빽하면 띠가 격자 색을 덮어 지도가 어지러움
        self.act_fval.setToolTip("켜면 격자 지도 위 건물 외벽을 따라 벽면 결과(층별 최댓값)를 같은 색 등급의 띠로 함께 표시")
        self.act_bmax = tb.addAction("건물별 값", self.redraw)
        self.act_bmax.setCheckable(True)
        self.act_bmax.setToolTip("켜면 건물을 벽면(전 층) 최고 레벨 색으로 칠함. 용도지역 기준을 넘는 건물은 굵은 자주색 테두리.\n"
                                 "건물 단위 표(정렬·CSV 저장)는 아래 ‘건물별’ 탭")
        self.act_bmax.setEnabled(False)
        cm = self.menu_cond = QtWidgets.QMenu(self)
        self.act_comp = {}
        from sori.summary import COMPONENTS
        for k, (nm, _c) in COMPONENTS.items():
            a = self.act_comp[k] = cm.addAction(nm)
            a.setCheckable(True)
            a.setChecked(True)
            a.triggered.connect(self.redraw)
        cm.addSeparator()
        self.act_meas = cm.addAction("측정 − 계산 차이 (점 수음점)")
        self.act_meas.setCheckable(True)
        self.act_meas.setEnabled(False)
        self.act_meas.setToolTip("측정값 CSV(입력값 ‘측정값 CSV’)가 있으면 점 수음점마다 계산 − 측정 dB 를 지도에 표시")
        self.act_meas.triggered.connect(self.redraw)
        self.act_dn = cm.addAction("주야 차이 지도 (LrT − LrN)")
        self.act_dn.setCheckable(True)
        self.act_dn.triggered.connect(self.redraw)
        bc = self.btn_cond = QtWidgets.QToolButton(text="보기 조건 ▾")
        bc.setToolTip("성분 고르기: 체크한 성분(직접음·반사 차수)만 에너지 합으로 다시 합쳐 격자 지도에 표시(주간 LrT 만 성분이 있음).\n"
                      "주야 차이 지도: 주간 − 야간")
        bc.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        bc.setMenu(cm)
        tb.addWidget(bc)
        self.act_sec = tb.addAction("단면", self._toggle_section)
        self.act_sec.setCheckable(True)
        self.act_sec.setToolTip("격자 지도에서 두 점(A, B)을 차례로 클릭하면 그 선의 수직 단면 소음지도를 계산")
        self.act_path = tb.addAction("경로", self._toggle_paths)
        self.act_path.setCheckable(True)
        self.act_path.setToolTip("켜고 격자 지도의 한 지점(또는 점 수음점 — 흰 원)을 클릭하면 그 수음점으로 오는 경로를\n"
                                 "오른쪽 창에 흰 바탕으로 확대해 그림(노랑 = 직접음, 빨강 = 반사음, 굵을수록 기여가 큼).\n"
                                 "경로별·도로별 기여 dB 는 아래 표와 속성 창")
        self.act_3d = tb.addAction("3D 보기", self.show_3d_view)
        self.act_3d.setToolTip("지형 위 격자 소음지도와 모든 건물의 층별 벽면 소음을 한 장면으로(three.js 3D 뷰어).\n"
                               "앱 안 3D 화면에서 열림(웹 화면 부품이 없으면 기본 브라우저) — 마우스로 돌리고 확대, 주간·야간 바꿔 보기")
        self.act_3d.setEnabled(False)
        tb.addSeparator()
        self.act_bar = tb.addAction("방음벽", self.show_barrier_panel)
        self.act_bar.setToolTip("방음벽 시뮬레이션: 지도에 방음벽 선을 그리고 높이를 넣어 설치 후를 계산,\n"
                                "설치 전(지금 결과)과의 차이 지도를 봄 — 아래 ‘방음벽’ 탭")
        self.act_diff = tb.addAction("전후 차이", self._toggle_diff)
        self.act_diff.setCheckable(True)
        self.act_diff.setEnabled(False)
        self.act_diff.setToolTip("켜면 지금 결과 − 기준 결과(대책 전)의 차이 지도(파랑 = 낮아짐, 빨강 = 높아짐).\n"
                                 "기준은 방음벽 계산 때 자동으로 정해지고, ‘보기 ▸ 비교 기준 결과 폴더 고르기…’로 바꿀 수 있음")
        tb.addSeparator()
        self._result_tb = tb.actions()[n0:]                # 결과 지도에서만 보이는 단추(입력 지도 확인에서는 숨김)
        out_menu = QtWidgets.QMenu(self)
        self.act_save = out_menu.addAction("지도 그림 저장(PNG·PDF)…", self.save_map)
        self.act_folder = out_menu.addAction("결과 폴더 열기", self.show_out_folder)
        self.act_export = out_menu.addAction("결과 내보내기(ZIP)…", self.export_results)
        for a in (self.act_folder, self.act_export):
            a.setEnabled(False)
        bo = QtWidgets.QToolButton(text="저장 ▾")
        bo.setToolTip("지도 그림 저장, 결과 폴더 열기, 결과 내보내기(ZIP)")
        bo.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        bo.setMenu(out_menu)
        tb.addWidget(bo)
        sp = QtWidgets.QWidget()
        sp.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        sp.setStyleSheet("background: transparent;")
        tb.addWidget(sp)
        tb.addWidget(QtWidgets.QLabel("창"))
        self.act_dock = {}
        for i, (name, short) in enumerate((("프로젝트", "프로젝트"), ("속성", "속성"), ("계산·결과 표", "계산·표")), 1):
            a = self.act_dock[name] = tb.addAction(short)
            a.setCheckable(True)
            a.setChecked(True)
            a.setShortcut(QtGui.QKeySequence(f"Ctrl+{i}"))
            a.setToolTip(f"‘{name}’ 창 접기/펴기 ({a.shortcut().toString(QtGui.QKeySequence.NativeText)})")
            a.triggered.connect(lambda on, n=name: self._set_dock(n, on))

        m = self.menuBar().addMenu("파일")
        m.addAction("홈 화면", self.show_home, QtGui.QKeySequence("Ctrl+Shift+H"))
        m.addSeparator()
        m.addAction("설정 파일 열기…", self.pick_cfg, QtGui.QKeySequence.Open)
        m.addAction("결과 폴더 열기…", self.pick_out)
        m.addAction("예제 불러오기(가상 교차로)", self.load_example)
        m.addAction("전농사거리 불러오기(수치지도는 따로 받음)…", self.load_jeonnong)
        m.addSeparator()
        m.addAction("저장 (입력값 화면: 설정 파일 / 지도 화면: 지도 그림)", self.save_current, QtGui.QKeySequence.Save)
        m.addAction("지도 저장(PNG·PDF)…", self.save_map)
        m.addAction("오른쪽 3D 파사드 저장(PNG·PDF)…", lambda: self.save_map(side=True))
        self.act_last_sec = m.addAction("마지막 단면 보기", self.show_section)
        self.act_last_sec.setEnabled(False)
        m.addSeparator()
        m.addAction("결과 폴더를 파인더/탐색기로 보기", self.show_out_folder)
        m.addAction("결과 내보내기(ZIP)…", self.export_results, QtGui.QKeySequence("Ctrl+E"))
        m.addSeparator()
        m.addAction("끝내기", self.close, QtGui.QKeySequence.Quit)
        m = self.menuBar().addMenu("보기")
        for name, a in self.act_dock.items():
            a2 = m.addAction(f"‘{name}’ 창")              # 도구 줄 단추와 같은 동작(단축키는 단추 쪽에 있음)
            a2.setCheckable(True)
            a2.setChecked(a.isChecked())
            a2.triggered.connect(lambda on, n=name: self._set_dock(n, on))
            a.toggled.connect(a2.setChecked)
        m.addAction("창 모두 접기/펴기 (지도 크게)", self._toggle_all_docks, QtGui.QKeySequence("Ctrl+0"))
        m.addSeparator()
        for a in (self.act_split, self.act_fval, self.act_bmax, self.act_sec, self.act_path, self.act_3d):
            m.addAction(a)
        m.addMenu(self.menu_cond).setText("보기 조건(성분·주야 차이)")
        self.act_3d_re = m.addAction("3D 뷰어 다시 만들기", lambda: self.show_3d_view(rebuild=True))
        self.act_3d_re.setEnabled(False)
        m.addSeparator()
        m.addAction(self.act_bar)
        m.addAction(self.act_diff)
        m.addAction("비교 기준 결과 폴더 고르기…", self.pick_diff_base)
        m = self.menuBar().addMenu("입력")
        m.addAction("입력값 화면", self.show_input)
        m.addSeparator()
        self.act_pick_pt = m.addAction("지도에서 점 수음점(측정점) 찍기", self._toggle_pick_point)
        self.act_pick_pt.setCheckable(True)
        self.act_pick_box = m.addAction("지도에서 계산 범위 그리기(두 모서리 클릭)", self._toggle_pick_box)
        self.act_pick_box.setCheckable(True)
        m.addAction("방음벽 그리기·설치 후 계산…", self.show_barrier_panel)
        m = self.menuBar().addMenu("계산")
        self.act_run = m.addAction("계산 실행", self.start)
        self.act_check = m.addAction("자가 점검", self.start_check)
        m = self.menuBar().addMenu("도움말")
        m.addAction("SORI 정보", lambda: QtWidgets.QMessageBox.about(
            self, "SORI", f"SORI v{__version__}\n도로교통소음 예측 — RLS-19 음원 + ISO 9613-2 / RLS-19 전파\n\n※ {AI_NOTICE}\n"
                          "계산 결과의 검토·검증 책임은 사용자에게 있음."))

        sb = self.statusBar()
        self.lbl_status = QtWidgets.QLabel("준비")
        self.bar = QtWidgets.QProgressBar(minimumWidth=260, maximumWidth=260, maximum=1000, textVisible=True)
        self.bar.setFormat("%p%")
        self.bar.setAlignment(Qt.AlignCenter)
        self.bar.hide()                                   # 계산·점검 중과 끝난 직후에만 보임
        self.lbl_time = QtWidgets.QLabel("")              # 경과 시간(1초마다)
        self.timer = QtCore.QTimer(self, interval=1000)
        self.timer.timeout.connect(self._tick)
        self.lbl_xy = QtWidgets.QLabel("")
        self.lbl_xy.setMinimumWidth(330)                  # 좌표 + 마우스 위치 값("X 205,123.0  Y 553,110.0 · LrT 80.1 dB")
        sb.addWidget(self.lbl_status, 1)
        sb.addPermanentWidget(self.lbl_xy)
        sb.addPermanentWidget(self.lbl_time)
        sb.addPermanentWidget(self.bar)
        sb.addPermanentWidget(QtWidgets.QLabel(f"※ {AI_NOTICE}"))
        self.resizeDocks([self.findChild(QtWidgets.QDockWidget, "프로젝트")], [260], Qt.Horizontal)
        self.resizeDocks([self.findChild(QtWidgets.QDockWidget, "속성")], [300], Qt.Horizontal)
        self.resizeDocks([self.findChild(QtWidgets.QDockWidget, "계산·결과 표")], [230], Qt.Vertical)

        self.home = HomePage(self)
        self.home.task.connect(self._home_task)       # 홈에는 '입력'만(나머지는 입력값 화면 단계 줄)
        self.stack.addWidget(self.home)
        self.stack.addWidget(self.split)
        self.stack.addWidget(self.input_page)            # 2 = 입력값 설정
        self.splash = SplashPage()
        self.splash.started.connect(self.show_home)
        self.stack.addWidget(self.splash)                # 3 = 시작 포스터(main 에서만 보임 — 시험은 홈에서 시작)
        self.view3d = View3DPage()
        self.view3d.back.connect(self._back_from_3d)
        self.stack.addWidget(self.view3d)                # 4 = 앱 안 3D 보기(웹 뷰어)
        self.show_home()

    # ------------------------------------------------------------ 홈 화면 ↔ 작업 화면
    def _work_chrome(self, on):
        """작업 화면에서만 쓰는 창(트리·속성·표 Dock, 지도 도구 줄)을 보이거나 숨김"""
        for n, d in self.docks.items():
            d.setVisible(on and self.act_dock[n].isChecked())      # 사용자가 접어 둔 창은 접힌 채로
        self.tb_view.setVisible(on)

    def show_splash(self):
        """앱을 켤 때의 포스터 화면(차림표·상태 줄도 숨김). 클릭·키 → 홈"""
        self._work_chrome(False)
        self.menuBar().setVisible(False)
        self.statusBar().setVisible(False)
        self.stack.setCurrentIndex(3)
        self.splash.setFocus()

    def show_home(self):
        self.menuBar().setVisible(True)
        self.statusBar().setVisible(True)
        self.home.refresh()
        self._work_chrome(False)
        self.stack.setCurrentIndex(0)
        self.lbl_status.setText("홈 — 작업을 고르세요: 데이터 입력 / 입력 지도 확인 / 지도 확인 · 계산")

    def save_current(self):
        """Cmd/Ctrl+S: 입력값 화면이면 입력값을 설정 파일에, 지도 화면이면 지도 그림을 저장"""
        if self.stack.currentIndex() == 2:
            ed = self.input_tabs.currentWidget()                  # 설정값·교통량·차로 수 탭
            if ed is self.guide:                                  # 안내 탭에서 고른 파일은 설정값 칸에 들어가 있음
                ed = self.editor
            return ed.save() if ed.isEnabled() else None
        if self.stack.currentIndex() == 1:
            return self.save_map()

    def _home_task(self, key):
        self._lane_focus = None                       # 홈에서 들어가면 차로 수 강조 표시는 지움
        self.enter_task(key)

    def show_input(self):
        self.menuBar().setVisible(True)
        self.statusBar().setVisible(True)
        self._work_chrome(False)
        self.stack.setCurrentIndex(2)
        if self.guide.refresh():                          # 빠진 필수 파일이 있으면 안내 탭부터
            self.input_tabs.setCurrentWidget(self.guide)
        self.lbl_status.setText("데이터 입력 — ‘준비할 파일’에서 파일 상태 확인, 칸을 고친 뒤 ‘저장’")

    def enter_task(self, key):
        """홈 화면 작업 단추 → 입력값 화면 또는 지도 작업 화면으로 들어가 그 작업에 맞게 준비"""
        if key == "input":
            return self.show_input()
        self.stack.setCurrentIndex(1)
        self.map_mode = "input" if key == "map" else "result"
        for a in self._result_tb:
            a.setVisible(self.map_mode == "result")
        self.lbl_mode.setText("입력 지도 확인" if key == "map" else "지도 확인 · 계산")
        for n in self.act_dock:                           # 이 작업에 필요한 창만 펴 둠
            self.act_dock[n].setChecked(n in TASK_DOCKS.get(key, set(self.docks)))
        self._work_chrome(True)
        if self.act_sec.isChecked():
            self.act_sec.setChecked(False)
            self.sec_pts = []
        if key in ("facade", "grid") and not self.act_split.isChecked():   # 지도 확인: 건물 클릭 → 3D 파사드를 함께
            self.act_split.setChecked(True)
            self._toggle_split(True)
        self.map3d.setVisible(self.map_mode == "result" and self.act_split.isChecked())   # 입력 지도: 3D 창 없이 지도만
        self._fit_split()
        self.show_grid()                                  # 작업에 맞게(차로 강조 등) 지도를 항상 다시 그림
        if key == "map":
            self._show_props(self._config_props(), "입력 설정")
            self.lbl_status.setText("입력 지도 확인 — 수치지도 건물·도로·등고선과 계산 범위(파란 점선). 건물·도로를 누르면 속성. "
                                    "값을 고치려면 홈 ▸ ‘데이터 입력’")
        elif key == "run":
            self._set_dock("계산·결과 표", True)
            self.tabs.setCurrentIndex(0)
            self.lbl_status.setText("계산 실행 — 아래 '계산 실행' 탭에서 전파 방법·대상을 고르고 ▶ 계산 실행")
        elif key == "grid":
            if "grid" not in self.R:                      # 결과가 없으면 계산부터(아래 창 폄)
                self._set_dock("계산·결과 표", True)
                self.tabs.setCurrentIndex(0)
            self.lbl_status.setText(("격자 소음지도 — 격자점 클릭 → 값, 건물 클릭 → 3D 파사드(오른쪽 창). "
                                     if "grid" in self.R else "아직 계산 결과 없음 — 아래 ‘계산 실행’ 탭에서 ▶ 계산 실행. ")
                                    + "계산·표는 도구 줄 ‘창’ ▸ 계산·결과 표")
        elif key == "facade":
            self.lbl_status.setText("격자 + 3D 파사드 — 왼쪽 지도에서 건물을 클릭하세요")
        elif key == "section":
            self.act_sec.setChecked(True)
            self._toggle_section(True)
        elif key == "check":
            self.start_check()

    def _dock(self, title, w, area):
        d = QtWidgets.QDockWidget(title, self)
        d.setObjectName(title)
        d.setWidget(w)
        d.setFeatures(QtWidgets.QDockWidget.DockWidgetMovable | QtWidgets.QDockWidget.DockWidgetFloatable)
        d.setStyleSheet(DOCK_CSS)
        self.addDockWidget(area, d)
        self.docks[title] = d
        return d

    # ------------------------------------------------------------ 접이식 창(프로젝트·속성·계산·결과 표)
    def _set_dock(self, name, on):
        """창 하나를 접거나 폄(단추·차림표 표시도 맞춤). 작업을 바꾸면 TASK_DOCKS 구성으로 다시 맞춰짐"""
        self.act_dock[name].setChecked(on)
        if self.stack.currentIndex() == 1:                # 홈·입력값 화면에서는 어차피 모두 숨김
            self.docks[name].setVisible(on)

    def _set_back(self, on):
        """‘◀ 격자 지도’ 단추: 한 창 보기에서 3D·단면을 볼 때만 보이고 눌림(보이지 않는 동작은 Qt 가 끈 것으로 봄)"""
        self.act_back.setVisible(on)
        self.act_back.setEnabled(on)

    def _toggle_all_docks(self):
        """창을 모두 접어 지도를 크게, 하나라도 접혀 있으면 모두 폄"""
        on = not all(a.isChecked() for a in self.act_dock.values())
        for n in self.act_dock:
            self._set_dock(n, on)

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
        self.chk_h15 = QtWidgets.QCheckBox("고시 높이 1.5 m 격자 추가", checked=False)
        self.chk_h15.setToolTip("설정 파일의 격자 높이에 더해 지면 위 1.5 m 격자도 계산(설정 파일은 바꾸지 않음).\n"
                                "계산 뒤 지도 도구 줄 ‘높이’에서 1.5 m 를 골라 봄. 격자 계산 시간이 높이 수만큼 늘어남")
        row.addWidget(self.chk_h15)
        row.addWidget(QtWidgets.QLabel("층별 격자"))
        self.sp_floors = QtWidgets.QSpinBox(minimum=0, maximum=30, value=0, suffix=" 층", specialValueText="끔")
        self.sp_floors.setToolTip("1층부터 이 층까지 각 층 높이의 격자를 더 계산(지면 위 2.8 m + 층마다 층 높이 — 벽면 수음점과 같은 규칙).\n"
                                  "설정 파일은 바꾸지 않음. 계산 뒤 도구 줄 ‘높이’에서 층을 골라 봄. 층 수만큼 격자 계산 시간이 늘어남")
        self.sp_floors.valueChanged.connect(self._update_estimate)
        row.addWidget(self.sp_floors)
        self.chk_fast = QtWidgets.QCheckBox("빠른 미리보기", checked=False)
        self.chk_fast.setToolTip("격자 간격 2배·벽면 수음점 간격 2배·반사 1차까지로 빨리 계산해 대략의 분포를 봄.\n"
                                 "결과는 설정값 계산과 다르므로 결과 폴더 이름 끝에 ‘_미리보기’ 를 붙여 따로 저장(설정 파일은 그대로)")
        row.addWidget(self.chk_fast)
        row.addStretch(1)
        self.btn_run = QtWidgets.QPushButton("▶ 계산 실행")
        self.btn_run.setDefault(True)
        self.btn_run.clicked.connect(self.start)
        self.btn_check = QtWidgets.QPushButton("자가 점검")
        self.btn_check.clicked.connect(self.start_check)
        row.addWidget(self.btn_run)
        row.addWidget(self.btn_check)
        f.addLayout(row, 3, 0, 1, 4)
        # 전파 방법: 처음에는 설정 파일 값. 바꾸면 결과 폴더도 방법별로 나눔(한 폴더에 방법이 섞이지 않게)
        self.cb_method = QtWidgets.QComboBox()
        for k, v in METHODS.items():
            self.cb_method.addItem(v, k)
        self.cb_method.setToolTip("설정 파일의 propagation_method 대신 이 방법으로 계산(설정 파일은 바꾸지 않음)")
        self.cb_method.currentIndexChanged.connect(self._on_method)
        f.addWidget(QtWidgets.QLabel("전파 방법"), 2, 0)
        f.addWidget(self.cb_method, 2, 1, 1, 3)
        f.addWidget(QtWidgets.QLabel("계산은 주간·야간을 모두 수행함. 반사 차수는 설정 파일 값(속성 창에서 확인). "
                                     "전파 방법을 설정 파일과 다르게 고르면 결과 폴더 이름 끝에 방법이 붙음."),
                    4, 0, 1, 4)
        self.lbl_est = QtWidgets.QLabel("")              # 계산 전 예상 점 수·시간
        self.lbl_est.setStyleSheet("color: #555555;")
        f.addWidget(self.lbl_est, 5, 0, 1, 4)
        for c in list(self.chk.values()) + [self.chk_h15, self.chk_fast]:
            c.toggled.connect(self._update_estimate)
        f.setRowStretch(6, 1)
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

    def load_jeonnong(self, dst=None, quiet=False):
        """전농사거리 예제(설정·교통량·측정점)를 문서/SORI/전농사거리 에 복사해 엶. 수치지도는 사용자가 받아
        data/map 에 넣음(zip 그대로 넣으면 여기서 풂). 수치지도가 없으면 받는 방법을 알리고 폴더를 엶 → False"""
        import shutil
        dst = Path(dst) if dst else default_output_dir() / "전농사거리"
        if not (dst / "config.yaml").exists():
            shutil.copytree(resource_path("examples", "jeonnong"), dst, dirs_exist_ok=True)
            log.info("전농사거리 예제 복사: %s", dst)
        mp = dst / "data" / "map"
        mp.mkdir(parents=True, exist_ok=True)
        n = extract_map_zips(mp)
        if n:
            log.info("수치지도 압축 %d개를 풀었음: %s", n, mp)
        if not has_building_layer(mp):
            msg = ("전농사거리 수치지도가 아직 없습니다(앱에는 설정·교통량·측정점만 들어 있음).\n\n"
                   "국토정보플랫폼(map.ngii.go.kr) → 공간정보 → 수치지도 → 수치지형도 v2.0(1:1,000, SHP)에서\n"
                   f"도엽 {', '.join(JEONNONG_SHEETS)} 을 받아\n{mp}\n에 넣은 뒤(zip 그대로 가능) "
                   "다시 '파일 ▸ 전농사거리 불러오기' 를 누르세요.")
            log.warning("전농사거리 수치지도 없음: %s", mp)
            if not quiet:
                QtWidgets.QMessageBox.information(self, "SORI — 전농사거리", msg)
                G.open_folder(mp)
            self.lbl_status.setText(f"전농사거리 수치지도를 {mp} 에 넣어 주세요")
            return False
        self.open_config(dst / "config.yaml", dst / "output")
        return True

    def open_config(self, path, out=None):
        from sori import config as C
        path = Path(path).resolve()
        try:
            cfg = C.load(path)
        except Exception as e:
            log.error("설정 파일 오류: %s", e)
            return self._err(f"설정 파일을 읽을 수 없습니다.\n{e}")
        self.cfg_path, self.cfg = path, cfg
        self.method0 = cfg.get("propagation_method") or "iso9613_2"     # 설정 파일의 전파 방법
        self._base_out = path.parent / "output"
        self._set_method(self.method0)
        out = Path(out) if out else self._base_out
        self.ed_cfg.setText(str(path))
        self.ed_out.setText(str(out))
        G.save_settings(last_config=path, last_out=out.resolve())   # 설정과 결과 폴더를 짝으로 기억
        add_recent(path, cfg["project"].get("name") or path.parent.name)
        self.editor.load(path, cfg)
        self.guide.refresh()
        try:
            self.traffic.load(cfg)
        except Exception as e:
            log.error("교통량 표를 읽지 못함: %s", e)
        self._prev_box = None
        self._lane_focus = None
        self._load_barriers()
        log.info("설정 파일: %s", path)
        self.open_results(out, quiet=True)
        try:
            self.lanes.load(cfg, self.R.get("roads"))
        except Exception as e:
            log.error("차로 수 표를 읽지 못함: %s", e)

    def open_results(self, out, quiet=False):
        """결과 폴더의 CSV·래스터·도형을 읽어 트리·지도·표를 채움(없으면 계산 범위만 표시)"""
        out = Path(out)
        self.out = out
        if not self.ed_out.text():
            self.ed_out.setText(str(out))
        R = {}
        try:
            for name, fn in (("grid", "grid_result.csv"), ("facade", "facade_result.csv"),
                             ("points", "point_result.csv"), ("terms", "path_terms.csv"),
                             ("road_contrib", "point_road_contrib.csv")):
                if (out / fn).exists():
                    R[name] = pd.read_csv(out / fn)
            if (out / "facades.csv").exists():
                R["facades"] = pd.read_csv(out / "facades.csv", index_col="fid")
            if (out / "section_result.csv").exists() and (out / "section_info.json").exists():
                R["section"] = (pd.read_csv(out / "section_result.csv"),
                                json.loads((out / "section_info.json").read_text(encoding="utf-8")))
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
        cb = (R.get("run_conditions") or {}).get("compare_base")   # 방음벽 계산 결과면 그 기준(설치 전) 폴더
        self.diff_base = Path(cb) if cb and (Path(cb) / "grid_result.csv").is_file() else None
        self._diff = None
        self.paths = None
        self.m_paths.set(None)
        self.act_diff.setEnabled(self.diff_base is not None and "grid" in R)
        if not self.act_diff.isEnabled():
            self.act_diff.setChecked(False)
        self._fill_bld_filters()
        self._fill_building_table()
        self.act_bmax.setEnabled(len(self.m_bld.df) > 0)
        if not self.act_bmax.isEnabled():
            self.act_bmax.setChecked(False)
        from sori.summary import available_components
        av = available_components(R["grid"]) if "grid" in R else []
        for k, a in self.act_comp.items():
            a.setVisible(k in av)
            a.setChecked(True)
        self.act_dn.setEnabled("grid" in R)
        if "grid" not in R:
            self.act_dn.setChecked(False)
        self.act_fval.setEnabled("facade" in R and "facades" in R)
        self.act_3d.setEnabled("grid" in R and "buildings" in R)
        self.act_3d_re.setEnabled(self.act_3d.isEnabled())
        self.act_folder.setEnabled(out.is_dir())
        self.act_export.setEnabled(out.is_dir() and bool(export_files(out, EXPORT_GROUPS)))
        self.view, self.view_bid = "grid", None
        self.facade_bid = None
        self._section_side = False
        self.map3d.message(MSG_3D_EMPTY)
        self._fit_split()
        ri = R.get("run_info") or {}
        m = set((ri.get("outputs") or {}).values()) or {ri.get("propagation_method")}
        if self.cfg is not None and len(m) == 1 and next(iter(m)) in METHODS:   # 연 결과의 방법으로 맞춤
            self._set_method(next(iter(m)))
        self.act_last_sec.setEnabled("section" in R)
        if self.cfg is None and (out / "config_used.yaml").exists():
            log.info("결과 폴더만 열었음 — 설정은 config_used.yaml 을 속성에 표시(입력 경로는 원래 설정 폴더 기준)")
        self._meas = self._measured_table(R)
        self.m_points.set(self._meas.drop(columns=["key", "diff"]) if self._meas is not None and len(self._meas)
                          else R.get("points"))
        self.act_meas.setEnabled(bool(self._meas is not None and self._meas["diff"].notna().any()))
        if not self.act_meas.isEnabled():
            self.act_meas.setChecked(False)
        self.m_terms.set(R.get("terms"))
        self.m_roads.set(R.get("road_contrib"))
        fr = R.get("facade")
        self.m_facade.set(fr.sort_values("LrT", ascending=False).head(200).reset_index(drop=True)
                          if fr is not None and len(fr) else None)
        self.cb_h.blockSignals(True)
        self.cb_h.clear()
        if "grid" in R:
            for h in sorted(R["grid"].h.unique()):
                self.cb_h.addItem(self._height_label(float(h)), float(h))
        self.cb_h.blockSignals(False)
        self._fill_tree()
        self.redraw()
        self._show_props(self._summary_props(), "계산 결과 요약")
        self._update_estimate()
        if hasattr(self, "home"):
            self.home.refresh()
        if R:
            log.info("결과 폴더: %s (%s)", out, ", ".join(k for k in R if k in ("grid", "facade", "points", "terms")))

    # ------------------------------------------------------------ 3D 보기(웹 뷰어 noise3d.html)
    def _3d_cfg(self):
        """3D 뷰어용 설정: 열린 설정(없으면 결과 폴더의 config_used.yaml)에 결과 폴더만 지금 폴더로"""
        import copy
        from sori import config as C
        used = Path(self.out) / "config_used.yaml"
        if self.cfg is None and not used.exists():
            raise FileNotFoundError("설정 파일이 열려 있지 않고 결과 폴더에 config_used.yaml 도 없음 — 설정 파일을 여세요")
        cfg = copy.deepcopy(self.cfg) if self.cfg is not None else C.load(used)
        cfg["project"]["output_dir"] = str(self.out)
        return cfg

    def show_3d_view(self, rebuild=False, sync=False):
        """결과 폴더의 noise3d.html 을 엶(앱 안 3D 화면 또는 브라우저). 없거나 rebuild 이거나 예전 판 뷰어
        (viewer3d.VIEWER_VERSION 과 다름)면 먼저 만듦(계산 결과는 그대로, 그리기만). 같은 판이면 덮어쓰지 않음 —
        결과보다 오래됐으면 다시 만들지 물음"""
        if not (self.out and "grid" in self.R and "buildings" in self.R):
            return self._err("3D 보기는 격자 결과가 있는 결과 폴더에서 쓸 수 있습니다. 계산을 먼저 실행하세요.")
        p = Path(self.out) / "noise3d.html"
        from sori.viewer3d import VIEWER_VERSION, file_version
        if p.exists() and not rebuild and file_version(p) != VIEWER_VERSION:
            # 예전 판 뷰어(예: 기준 초과 단추가 있던 2026-11-13 이전 파일): 묻지 않고 지금 판으로 다시 만듦
            log.info("3D 뷰어 파일이 예전 판(%d, 지금 %d) — 다시 만듦: %s", file_version(p), VIEWER_VERSION, p)
            rebuild = True
        if p.exists() and not rebuild:
            if not sync and self._3d_stale(p):            # 결과를 다시 계산한 뒤의 예전 뷰어: 다시 만들지 물음
                box = QtWidgets.QMessageBox(self)
                box.setIcon(QtWidgets.QMessageBox.Question)
                box.setWindowTitle("SORI — 3D 보기")
                box.setText("3D 뷰어 파일이 지금 계산 결과보다 오래됐습니다.")
                box.setInformativeText(f"{p}\n다시 만들면 지금 결과로 바뀝니다(수십 초). 계산 결과 파일은 그대로입니다.")
                b_re = box.addButton("다시 만들기", QtWidgets.QMessageBox.AcceptRole)
                box.addButton("예전 것 그대로 보기", QtWidgets.QMessageBox.RejectRole)
                box.setDefaultButton(b_re)
                box.exec()
                if box.clickedButton() is b_re:
                    return self.show_3d_view(rebuild=True)
            return self._open_3d(p)
        try:
            cfg = self._3d_cfg()
        except Exception as e:
            return self._err(f"3D 뷰어를 만들 설정을 읽지 못했습니다.\n{e}")
        if sync:                                          # 시험용: 같은 스레드에서 만들기
            from sori import viewer3d
            return self._open_3d(Path(viewer3d.main(cfg)))
        self._launch(Worker(self.bridge, "viewer3d", cfg=cfg), "3D 뷰어 만드는 중… (수십 초)", busy=True)

    def _3d_stale(self, p):
        """noise3d.html 이 격자·벽면 결과 파일보다 먼저 만들어졌는지"""
        t = p.stat().st_mtime
        return any((Path(self.out) / f).exists() and (Path(self.out) / f).stat().st_mtime > t + 1
                   for f in ("grid_result.csv", "facade_result.csv"))

    def _open_3d(self, p):
        """앱 안 3D 화면(웹 화면 부품이 있을 때) 또는 기본 브라우저로 엶"""
        log.info("3D 뷰어: %s", p)
        if HAS_WEB:
            name = (self.cfg or {}).get("project", {}).get("name") or Path(self.out).parent.name
            self._work_chrome(False)
            self.view3d.load(p, f"3D 보기 · {name}")
            self.stack.setCurrentWidget(self.view3d)
            self.lbl_status.setText("3D 보기 — 위쪽 패널 단추로 주간·야간·지면·주변 건물, ‘◀ 지도로’로 돌아감")
            return p
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(p)))
        self.lbl_status.setText(f"3D 뷰어를 브라우저에서 엶: {p.name} — 마우스로 돌리기·확대, 위쪽 단추로 주간·야간")
        return p

    def _back_from_3d(self):
        self.stack.setCurrentIndex(1)
        self._work_chrome(True)
        self.lbl_status.setText("격자 소음지도")

    # ------------------------------------------------------------ 결과 폴더 보기·내보내기
    def show_out_folder(self):
        if self.out and Path(self.out).is_dir():
            G.open_folder(self.out)
        else:
            self.lbl_status.setText("결과 폴더가 아직 없음 — 계산을 먼저 실행하세요")

    def export_results(self):
        """내보낼 묶음을 고르고 ZIP 파일 이름을 정해 저장"""
        if not (self.out and Path(self.out).is_dir()):
            return self._err("내보낼 결과 폴더가 없습니다. 계산을 먼저 실행하거나 결과 폴더를 여세요.")
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("결과 내보내기 (ZIP)")
        v = QtWidgets.QVBoxLayout(dlg)
        v.addWidget(QtWidgets.QLabel(f"결과 폴더: {self.out}\n묶을 자료를 고르세요. 원본은 그대로 둡니다."))
        chk = {}
        for g, (label, _e) in EXPORT_GROUPS.items():
            n = len(export_files(self.out, [g]))
            chk[g] = QtWidgets.QCheckBox(f"{label} — {n}개", checked=n > 0 and g != "gis", enabled=n > 0)
            v.addWidget(chk[g])
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.button(QtWidgets.QDialogButtonBox.Ok).setText("저장 위치 고르기…")
        bb.button(QtWidgets.QDialogButtonBox.Cancel).setText("취소")
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        groups = [g for g, c in chk.items() if c.isChecked()]
        if not groups:
            return self._err("묶을 자료를 하나 이상 고르세요.")
        import datetime as dt
        name = ((self.cfg or {}).get("project", {}).get("name") or Path(self.out).name).replace("/", "_")
        p, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "ZIP 파일로 저장", str(Path(self.out).parent / f"{name}_결과_{dt.date.today():%Y%m%d}.zip"), "ZIP (*.zip)")
        if p:
            self.export_results_to(p, groups)

    def export_results_to(self, path, groups):
        path = Path(path).with_suffix(".zip")
        try:
            n = export_zip(self.out, path, groups)
        except OSError as e:
            log.error("내보내기 실패: %s", e)
            return self._err(f"내보내지 못했습니다.\n{e}")
        log.info("결과 내보내기: %s (%d개 파일)", path, n)
        self.lbl_status.setText(f"내보냄: {path} ({n}개 파일)")
        return path

    # ------------------------------------------------------------ 전파 방법
    def _set_method(self, m):
        """선택 칸과 메모리의 설정(self.cfg)을 전파 방법 m 으로. 결과 폴더는 바꾸지 않음"""
        self.cb_method.blockSignals(True)
        self.cb_method.setCurrentIndex(max(0, self.cb_method.findData(m)))
        self.cb_method.blockSignals(False)
        if self.cfg is not None:
            self.cfg["propagation_method"] = m

    def method_out(self, m):
        """방법별 결과 폴더: 설정 파일의 방법이면 설정 옆 output, 다르면 output_<방법>"""
        base = self._base_out
        return base if m == self.method0 else base.with_name(f"{base.name}_{m}")

    def _on_method(self, _i):
        m = self.cb_method.currentData()
        if self.cfg is None:
            return
        self.cfg["propagation_method"] = m
        out = self.method_out(m)
        self.ed_out.setText(str(out))
        log.info("전파 방법 %s → 결과 폴더 %s%s", m, out, "" if m == self.method0 else " (설정 파일과 다른 방법)")
        self.open_results(out, quiet=True)

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
                              for k, v in ri.items() if k not in ("notice", "config_resolved")])
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
            for i, w in enumerate(ri.get("standard_deviations") or [], 1):       # 표준 원문과 다른 설정(propagation.py)
                rows.append((f"표준과 다른 설정 {i}", w))
            for k, v in (ri.get("results_range") or {}).items():
                rows.append((f"범위 {k}", "  ".join(f"{t} {lo:.1f}~{hi:.1f}" for t, (lo, hi) in v.items())))
        bi = self.R.get("build_info")
        if bi and "roads_with_fallback_traffic" in bi:
            rows.append(("가정 교통량 쓴 도로 조각", bi["roads_with_fallback_traffic"]))
        return rows + self._exposure_props()

    def _show_props(self, rows, title=None):
        """오른쪽 속성 창: 제목(무엇을 보고 있는지) + 항목(회색) / 값(굵게). 항목 이름이 '—' 로 시작하면 소제목 줄"""
        self.props_title.setText(title or "속성")
        self.props.setRowCount(0)
        bold = QtGui.QFont()
        bold.setBold(True)
        for k, v in rows:
            i = self.props.rowCount()
            self.props.insertRow(i)
            ki = QtWidgets.QTableWidgetItem(str(k))
            ki.setForeground(QtGui.QColor("#777777"))
            self.props.setItem(i, 0, ki)
            it = QtWidgets.QTableWidgetItem("" if v is None else str(v))
            it.setToolTip(it.text())
            it.setFont(bold)
            it.setForeground(QtGui.QColor("#222222"))
            self.props.setItem(i, 1, it)
            if str(k).startswith("—"):                    # 소제목 줄
                ki.setForeground(QtGui.QColor(RED))
                ki.setFont(bold)
        self.props.resizeColumnToContents(0)
        self.props.setColumnWidth(0, min(self.props.columnWidth(0) + 12, 170))

    # ------------------------------------------------------------ 지도
    def redraw(self):
        self._redraw_main()
        if self.paths is not None and self.act_path.isChecked() and self.view == "grid":
            self._show_paths()                            # 시간대를 바꾸면 경로 표·도로별 기여도도 그 시간대로
        if self.act_split.isChecked() and self._section_side and not self._paths_side() and "section" in self.R:
            self._draw_section(self.map3d)                             # 오른쪽 단면도 시간대에 맞춤
        elif self.act_split.isChecked() and self.facade_bid is not None and not self._paths_side():
            self._draw_facade(self.facade_bid, self.map3d)            # 오른쪽 3D 도 시간대에 맞춤

    def _redraw_main(self):
        if self.view == "facade":
            return self._draw_facade(self.view_bid)
        if self.view == "section":
            return self._draw_section()
        self._set_back(False)
        self._redraw_grid()
        self._grid_extras()

    def _grid_extras(self):
        """격자 지도 위에 겹치는 것들(벽면 값 띠, 선택 건물 테두리, 범위 미리보기, 차로 수 강조)"""
        if self.map_mode == "input":                     # 입력 지도: 결과 겹치기 없이 입력 도구만
            self._draw_preview()
            self._draw_lane_focus()
            self._draw_barriers()
            self._draw_pick_box()
            return
        self._facade_overlay()
        self._mark_building()
        self._draw_preview()
        self._draw_lane_focus()
        self._building_overlay()
        self._draw_barriers()
        self._draw_paths()
        self._draw_pick_box()
        self._draw_measured()
        self._draw_section_line()

    def _facade_overlay(self):
        """격자 지도 위 건물 외벽을 따라 벽면 결과(층별 최댓값)를 색 띠로 겹쳐 그림.
        띠 위치·값은 보고서 그림 HQ_facade 와 같은 함수(maps_hq.facade_segments), 색은 지금 격자 지도와 같은 등급"""
        R, ax = self.R, self.map.ax
        if (not self.act_fval.isChecked() or self.view != "grid" or ax is None or self.act_diff.isChecked()
                or R.get("facade") is None or R.get("facades") is None):
            return
        from matplotlib.collections import LineCollection
        from sori.maps_hq import facade_segments
        key = self.cb_key.currentData()
        try:
            W = facade_segments(R["facade"], R["facades"], key).sort_values("L")
        except Exception as e:
            log.warning("벽면 값 띠를 그리지 못함: %s", e)
            return
        if self.map.hq is not None:                      # 보고서 수준 지도: DIN 색 등급
            from sori.maps_hq import din_cmap
            cmap, norm = din_cmap()
        else:                                            # 간단한 지도: 30 dB부터 5 dB 등급
            from sori.plots import classes
            cmap, norm, _ = classes(30, 5)
        segs = np.stack([W[["x1", "y1"]].values, W[["x2", "y2"]].values], axis=1)
        # 굵기는 화면 점 기준(보고서 지도는 화면 dpi 를 낮춰 그리므로 pt 로 주면 1 점 남짓으로 가늘어짐)
        dpi = getattr(self.map.fig, "_original_dpi", None) or self.map.fig.dpi
        lw = 2.2 * 72.0 / dpi
        xl, yl = ax.get_xlim(), ax.get_ylim()
        ax.add_collection(LineCollection(segs, colors="#222", linewidths=lw * 1.6, capstyle="butt", zorder=19))
        ax.add_collection(LineCollection(segs, colors=cmap(norm(W.L.values)), linewidths=lw, capstyle="butt",
                                         zorder=20, gid="facade_overlay"))
        ax.set_xlim(xl)
        ax.set_ylim(yl)
        self.map.canvas.draw_idle()

    FACADE_BID_LABELS = 30                               # 3D 파사드·격자 지도에 번호를 붙일 주변 건물 수

    def _on_map_move(self, x, y):
        """상태 줄 오른쪽: 마우스 위치 좌표 + 가까운 격자점 값(2026-11-14)"""
        v = self._value_at(x, y)
        self.lbl_xy.setText(f"X {x:,.1f}  Y {y:,.1f}" + (f"   ·   {v}" if v else ""))

    def _value_at(self, x, y):
        """마우스 위치에서 가장 가까운 격자점 값 글(지금 보이는 격자 지도 기준: 보통 = LrT·LrN, 전후 차이 = Δ, 주야 차이 =
        LrT − LrN). 격자 밖·입력 지도·3D·단면이면 빈 글, 건물 안 격자점은 "건물 안". 격자 표는 바뀔 때 한 번만 배열로 만듦"""
        g = getattr(self, "_grid_now", None)
        if g is None or self.view != "grid" or self.map_mode == "input":
            return ""
        Gh, key = g
        lut = getattr(self, "_cursor_lut", None)
        if lut is None or lut[0] is not Gh:
            if "d" + key in Gh.columns:
                col, lab = "d" + key, f"Δ{key}"
            elif self.act_dn.isChecked() and {"LrT", "LrN"} <= set(Gh.columns):
                Gh = Gh.assign(_dn=Gh.LrT - Gh.LrN)
                col, lab = "_dn", "LrT − LrN"
            else:
                col, lab = key, key
            xs, ys = np.sort(Gh.x.unique()), np.sort(Gh.y.unique())
            step = float(np.min(np.diff(xs))) if len(xs) > 1 else 1.0
            A = np.full((len(ys), len(xs)), np.nan)
            A[np.searchsorted(ys, Gh.y), np.searchsorted(xs, Gh.x)] = Gh[col].values
            lut = self._cursor_lut = (g[0], xs[0], ys[0], step, A, lab, col.startswith("d") or col == "_dn")
        _, x0, y0, step, A, lab, signed = lut
        j, i = int(round((x - x0) / step)), int(round((y - y0) / step))
        if not (0 <= i < A.shape[0] and 0 <= j < A.shape[1]):
            return ""
        v = A[i, j]
        if not np.isfinite(v):
            return "건물 안"
        return f"{lab} {v:+.1f} dB" if signed else f"{lab} {v:.1f} dB"

    def _facade_view(self, bid):
        """3D 파사드에 그리는 범위: 클릭한 건물 중심 반경(설정 radius_3d, 최대 80 m)"""
        from shapely.geometry import Point
        B = self.R["buildings"]
        c = B[B.bid == bid].iloc[0].geometry.centroid
        pc = (self.cfg or {}).get("plots") or {}
        return Point(c.x, c.y).buffer(min(float(pc.get("radius_3d", 105.0)), 80.0))

    def _mark_building(self):
        """나란히 보기에서 오른쪽에 보이는 건물을 왼쪽 격자 지도에 표시: 빨간 테두리 + 3D 파사드와 같은 건물 번호
        (대상 = 빨간 상자, 주변 = 흰 상자, plots.bid_label_points), 3D 범위는 옅은 점선 원(2026-11-14)"""
        B, ax = self.R.get("buildings"), self.map.ax
        if not self.act_split.isChecked() or self.facade_bid is None or B is None or ax is None or self.view != "grid":
            return
        b = B[B.bid == self.facade_bid]
        if len(b):
            from sori.plots import bid_label_points
            xl, yl = ax.get_xlim(), ax.get_ylim()
            dpi = getattr(self.map.fig, "_original_dpi", None) or self.map.fig.dpi
            k = 72.0 / dpi                                 # 화면 점 기준 크기(보고서 틀은 dpi 를 낮춰 그림)
            b.boundary.plot(ax=ax, color="#e00000", lw=2.2, zorder=30)
            view = self._facade_view(self.facade_bid)
            ax.plot(*view.exterior.xy, ls=(0, (4, 3)), color="#d00000", lw=1.0 * k, alpha=0.6, zorder=29, gid="facade_view")
            self._bid_pts = (ax, k, bid_label_points(B[B.intersects(view)], self.facade_bid, self.FACADE_BID_LABELS))
            self._place_bid_labels()
            if getattr(ax, "_sori_bid_cb", None) is None:  # 확대·이동하면 그 축척에 맞게 번호를 다시 배치
                ax._sori_bid_cb = ax.callbacks.connect("xlim_changed", lambda _a: self._place_bid_labels())
            ax.set_xlim(xl); ax.set_ylim(yl)
            if self.map.hq is not None:                    # 보고서 지도: geopandas 가 붙이는 좌표축 이름(Easting) 지움
                ax.set_xlabel(""); ax.set_ylabel("")
            self.map.canvas.draw_idle()

    def _place_bid_labels(self):
        """격자 지도의 건물 번호(_mark_building): 3D 파사드와 같은 번호 묶음에서 화면에서 겹치는 주변 번호는 뺌
        (대상 번호는 늘 그림). 지금 축척 기준이라 확대하면 더 많이 보임"""
        ax, k, pts = getattr(self, "_bid_pts", (None, 1.0, []))
        for t in getattr(self, "_bid_art", []):
            try:
                t.remove()
            except (ValueError, NotImplementedError, AttributeError):
                pass
        self._bid_art = []
        if ax is None or ax is not self.map.ax or not pts:
            return
        tr = ax.transData
        r = self.map.fig.dpi / 72.0                         # 글자 크기(pt) → 화면 점
        placed = []
        for bid, x, y, _, me in pts:                         # 대상이 맨 앞
            px, py = tr.transform((x, y))
            w, h = (len(str(bid)) * 0.62 + 0.8) * 9 * k * r, 1.9 * 9 * k * r     # 글자 상자 대략 크기
            if not me and any(abs(px - a) < (w + c) / 2 and abs(py - b) < (h + d) / 2 for a, b, c, d in placed):
                continue
            placed.append((px, py, w, h))
            if me:
                t = ax.text(x, y, str(bid), fontsize=12 * k, weight="bold", color="white", ha="center", va="center",
                            zorder=58, gid="bid_label", clip_on=True,
                            bbox=dict(boxstyle="round,pad=0.2", fc=RED, ec="white", lw=0.8))
            else:
                t = ax.text(x, y, str(bid), fontsize=9 * k, color="#333333", ha="center", va="center", zorder=57,
                            gid="bid_label", clip_on=True,
                            bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="#888888", lw=0.4, alpha=0.85))
            self._bid_art.append(t)
        self.map.canvas.draw_idle()

    def _input_layers(self):
        """입력 지도용 수치지도 레이어(건물·도로·등고선, 계산 범위 + 80 m). 설정 파일마다 한 번만 읽음.
        수치지도를 읽지 못하면 결과 폴더의 buildings·roads.gpkg"""
        key = (str(self.cfg_path), str((self.cfg or {}).get("receivers", {}).get("center")),
               str((self.cfg or {}).get("receivers", {}).get("grid", {}).get("size")))
        if self._inlay is not None and self._inlay[0] == key:
            return self._inlay[1]
        L = {"buildings": None, "roads": None, "contours": None, "src": ""}
        if self.cfg is not None:
            from shapely.geometry import box
            from sori import inputs as I
            from sori.config import grid_bbox
            try:
                region = box(*grid_bbox(self.cfg)).buffer(80.0)
                for k, lay in (("buildings", "building"), ("roads", "road"), ("contours", "contour")):
                    L[k] = I.read_layer(self.cfg, lay, region)
                L["src"] = "수치지도"
            except Exception as e:
                log.warning("입력 지도: 수치지도를 읽지 못함 — 결과 폴더의 도형으로 표시: %s", e)
        if L["buildings"] is None and self.R.get("buildings") is not None:
            L.update(buildings=self.R.get("buildings"), roads=self.R.get("roads"), src="결과 폴더(buildings·roads.gpkg)")
        self._inlay = (key, L)
        return L

    def _draw_input_map(self):
        """입력 지도 확인: 흰 바탕에 수치지도 건물·도로·등고선, 계산 범위(파란 점선). 소음 값은 그리지 않음"""
        from sori.config import grid_bbox
        self._grid_now = None
        L = self._input_layers()
        extent = None
        if self.cfg is not None:
            try:
                extent = grid_bbox(self.cfg)
            except Exception:
                extent = None
        if L["buildings"] is None and extent is None:
            return self.map.message("수치지도 폴더를 찾지 못했습니다 — 홈 ▸ ‘데이터 입력’에서 수치지도 폴더를 지정하세요.")
        fz = ((self.cfg or {}).get("input", {}).get("map", {}).get("fields", {}) or {}).get("contour_z", "등고수치")
        lanes = ((self.cfg or {}).get("input", {}).get("map", {}).get("fields", {}) or {}).get("road_lanes", "차로수")
        name = (self.cfg or {}).get("project", {}).get("name") or ""
        self.map.draw_input(L["buildings"], L["roads"], L["contours"], extent, (self.cfg or {}).get("crs"),
                            f"입력 지도 · {name} — {L['src'] or '도형 없음'} · 파란 점선 = 계산 범위", fz, lanes)

    def _redraw_grid(self):
        if self.map_mode == "input":
            return self._draw_input_map()
        R, crs = self.R, (self.cfg or {}).get("crs")
        extent = None
        if self.cfg:
            from sori.config import grid_bbox
            try:
                extent = grid_bbox(self.cfg)
            except Exception:
                extent = None
        h, key = self.cb_h.currentData(), self.cb_key.currentData()
        if "grid" in R and h is not None and self.act_diff.isChecked() and self._draw_diff(h, key):
            return
        if "grid" in R and h is not None and self.act_dn.isChecked() and self._draw_daynight(h):
            return
        if "grid" in R and h is not None:
            Gh = R["grid"][R["grid"].h == h]
            Gsel, note = None, ""
            comps = self._comps_selected()
            if comps is not None and key == "LrT":                  # 성분 고르기: 고른 성분만 에너지 합(주간)
                from sori.summary import COMPONENTS, combine
                Gh = Gh.assign(LrT=combine(Gh, comps))
                Gsel = Gh
                note = " · 성분: " + (" + ".join(COMPONENTS[k][0] for k in comps) if comps else "없음")
            elif comps is not None:
                self.lbl_status.setText("성분 고르기는 주간(LrT)에만 적용 — 야간은 성분이 저장되지 않아 전체 값을 표시")
            self._grid_now = (Gh, key)
            C = self._hq_ctx()
            if C is not None and (Gsel is None or Gsel[key].notna().any()):   # 보고서 수준 지도(maps_hq 와 같은 그리기)
                try:
                    # 화면은 나란히·한 창 모두 지도만 크게, 지도 저장만 보고서 틀(A3). 2026-11-14: 한 창 보기에서 A3 틀을
                    # 창에 맞추느라 dpi 가 약 39 로 내려가 지도가 나란히 보기보다 작고 글자가 읽히지 않던 문제
                    compact = not getattr(self, "_force_report", False)
                    return self.map.draw_hq_grid(C, C.cfg, key, h,     # 창 크기 바뀌면 왼쪽만 다시 그림
                                                 lambda: (self._redraw_grid(), self._grid_extras()), compact=compact,
                                                 G=Gsel, note=note)
                except Exception as e:
                    log.warning("보고서 지도 그리기 실패 — 간단한 지도로 표시: %s", e)
            A, ext = self._grid_array(h, key, Gh)
            meth = (R.get("run_info") or {}).get("outputs", {}).get("grid", "")
            title = f"격자 소음지도 {KEYS[key]} · 높이 {h:g} m · {meth}  (최대 {np.nanmax(A):.1f} dB)"
            self.map.draw((A, ext), extent, R.get("buildings"), R.get("roads"), title, crs)
        elif R.get("buildings") is not None or extent is not None:
            self.map.draw(None, extent, R.get("buildings"), R.get("roads"),
                          "아직 계산 결과 없음 — 아래 ‘계산 실행’ 탭에서 ▶ 계산 실행(파란 점선 = 계산 범위)", crs)
            self._grid_now = None
        else:
            self.map.message("설정 파일(config.yaml)을 열거나 결과 폴더를 여세요.")
            self._grid_now = None

    def _hq_ctx(self):
        """보고서 지도용 자료(maps_hq.Ctx: 건물·도로 띠·교차로·교통량·결과 전파 방법). 결과 폴더별로 한 번만 읽음.
        설정 파일 없이 결과 폴더만 열었거나 필요한 파일이 없으면 None(간단한 지도로 표시)"""
        import copy
        if self.cfg is None or not self.out:
            return None
        out = Path(self.out).resolve()
        if getattr(self, "_ctx", (None, None))[0] == out:
            return self._ctx[1]
        need = ("buildings.gpkg", "roads.gpkg", "junctions_used.csv", "traffic_used.csv", "model_rasters.npz")
        C = None
        if all((out / f).exists() for f in need):
            try:
                from sori.maps_hq import Ctx
                cfg = copy.deepcopy(self.cfg)
                cfg["project"]["output_dir"] = str(out)
                C = Ctx(cfg)
            except Exception as e:
                log.warning("보고서 지도 자료를 읽지 못함 — 간단한 지도로 표시: %s", e)
        self._ctx = (out, C)
        return C

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
        if self.act_split.isChecked():                   # 나란히: 왼쪽 격자는 그대로, 오른쪽에 3D
            if self._section_side:                       # 오른쪽 창의 단면을 3D 로 바꿈(왼쪽 A–B 선도 지움)
                self._section_side = False
                self.facade_bid = None
            prev, self.facade_bid = self.facade_bid, bid
            if prev is None:
                self._fit_split()                        # 처음 고른 건물: 오른쪽 창을 반으로 넓힘
            if prev != bid:
                self._redraw_main()                      # 빨간 테두리 옮김
            self._draw_facade(bid, self.map3d)
            return True
        self.view, self.view_bid = "facade", bid
        self.redraw()
        return True

    def _facade_opts_changed(self, src):
        """3D 파사드 보기 선택(소음도 숫자·주변 투명)을 두 지도 창에 맞추고 보던 건물을 다시 그림"""
        for mv in (self.map, self.map3d):
            if mv is not src:
                for a, b in ((mv.opt_vals, src.opt_vals), (mv.opt_ghost, src.opt_ghost)):
                    a.blockSignals(True)
                    a.setChecked(b.isChecked())
                    a.blockSignals(False)
        if self.act_split.isChecked() and self.facade_bid is not None and not self._paths_side():
            self._draw_facade(self.facade_bid, self.map3d)
        elif self.view == "facade" and self.view_bid is not None:
            self._draw_facade(self.view_bid)

    def _toggle_split(self, on=None):
        # 도구 줄 단추(addAction 의 triggered)는 인자 없이 부름 → 단추의 눌림 상태를 씀(2026-11-11: 인자 필수라
        # 단추를 누르면 TypeError 로 오른쪽 창이 끝내 켜지지 않던 오류)
        if on is None:
            on = self.act_split.isChecked()
        self.map3d.setVisible(on)
        if on:
            if self.view == "facade":                    # 한 창에서 보던 건물을 오른쪽으로 옮김
                self.facade_bid, self.view, self.view_bid = self.view_bid, "grid", None
            self._fit_split()
            self.redraw()
        else:
            bid, self.facade_bid, self._split_auto = self.facade_bid, None, False
            if self._section_side:                       # 한 창 보기로: 단면은 '마지막 단면 보기'로 다시 봄
                self._section_side = False
            self.redraw()
            if bid is not None:
                self.lbl_status.setText(f"한 창 보기 — 건물을 클릭하면 3D 파사드로 바뀜(직전 건물 {bid})")

    def _fit_split(self):
        """나란히 보기 폭: 건물을 고르기 전에는 오른쪽 3D 창을 좁게(안내 글만) 두어 격자 지도를 크게,
        건물을 고르면 반반. 2026-11-12: 처음 열면 빈 3D 창이 화면 절반을 차지해 지도가 작던 문제"""
        if not self.map3d.isVisibleTo(self):
            return
        W = max(self.split.width(), 600)
        wide = self.facade_bid is not None or self._paths_side() or self._section_side
        side = W // 2 if wide else min(SPLIT_EMPTY_W, W // 4)
        self.split.setSizes([W - side, side])

    def show_grid(self):
        self.view, self.view_bid = "grid", None
        self.redraw()

    def show_section(self):
        """단면 보기: 나란히 보기면 오른쪽 창(왼쪽 격자 지도에 A–B 선), 한 창 보기면 평면 + 단면 그림"""
        if "section" not in self.R:
            return
        if self.act_split.isChecked():
            self._section_side = True
            self.view, self.view_bid = "grid", None
            self._fit_split()
            self.redraw()
        else:
            self.view = "section"
            self.redraw()

    def _draw_facade(self, bid, target=None):
        """target: 그릴 지도 창(None = 가운데 창 self.map, 나란히 보기면 self.map3d)"""
        side = target is not None
        target = target or self.map
        R, key = self.R, self.cb_key.currentData()
        B, FR, F = R["buildings"], R["facade"], R["facades"]
        b = B[B.bid == bid].iloc[0]
        pc = (self.cfg or {}).get("plots") or {}
        fh = float(((self.cfg or {}).get("model") or {}).get("floor_height", 3.0))
        view = self._facade_view(bid)
        T = set(int(t) for t in FR.bid[FR.bid.isin(B[B.intersects(view)].bid)].unique())
        T.add(bid)
        FRb = FR[FR.bid == bid]
        name = f" {b.bname}" if isinstance(b.get("bname"), str) and b.bname else ""
        title = f"3D 파사드 · 건물 {bid}{name} · {int(b.floors)}층 · {KEYS[key]}"   # 한 줄(자세한 값은 속성 창)
        target.draw_facade3d(B, FR, F, T, bid, key, view, fh, float(pc.get("elev_3d", 28.0)),
                             float(pc.get("azim_3d", -60.0)), title)
        if not side:
            self._set_back(True)
        self.m_facade.set(FRb.sort_values(key, ascending=False).reset_index(drop=True))
        rows = [("건물 번호", str(bid)), ("종류", str(b.kind)), ("층수", str(int(b.floors))), ("높이 m", f"{b.H:.1f}"),
                ("벽 수(계산)", str(FRb.fid.nunique())), ("수음점", f"{len(FRb):,}"),
                (f"{key} 최대 dB", f"{FRb[key].max():.1f}"), (f"{key} 최소 dB", f"{FRb[key].min():.1f}")]
        lim = pc.get("limits") or {}
        if key in lim:
            rows.append((f"기준 {lim[key]} dB 초과", f"{int((FRb[key] > float(lim[key])).sum()):,}점"))
        rows.append(("— 층별 최대", "dB"))                # 3D 그림 옆 숫자 대신 여기서(위층부터)
        for fl, v in FRb.groupby("floor")[key].max().sort_index(ascending=False).items():
            rows.append((f"{int(fl)}층", f"{v:.1f}"))
        self._show_props(rows, f"건물 {bid}{name}")
        self.lbl_status.setText(f"건물 {bid} 3D 파사드(오른쪽) — 마우스로 끌어 돌려 보기, 다른 건물 클릭하면 바뀜, "
                                f"층별 값 전체는 아래 '벽면' 표" if side else
                                f"건물 {bid} 3D 파사드 — 마우스로 끌어 돌려 보기, '◀ 격자 지도'로 돌아감, "
                                f"층별 값 전체는 아래 '벽면' 표")

    # ------------------------------------------------------------ 단면 소음지도
    def _toggle_section(self, on=None):
        if on is None:                                   # 도구 줄 단추에서 부를 때(인자 없음)
            on = self.act_sec.isChecked()
        self.sec_pts = []
        if on:
            if self.view != "grid":
                self.show_grid()
            self.lbl_status.setText("단면: 격자 지도에서 시작점 A 를 클릭하세요")
        else:
            self.redraw()

    def _section_click(self, x, y):
        self.sec_pts.append((float(x), float(y)))
        ax = self.map.ax
        ax.plot(*zip(*self.sec_pts), "o-", color="#1f5fbf", lw=2, ms=5, zorder=10)
        self.map.canvas.draw_idle()
        if len(self.sec_pts) == 1:
            self.lbl_status.setText("단면: 끝점 B 를 클릭하세요")
            return
        p1, p2 = self.sec_pts
        self.act_sec.setChecked(False)
        self.sec_pts = []
        self.start_section(p1, p2)

    def _section_spacing(self, p1, p2):
        """단면 계산점 간격 고르기(2026-11-14): 자동·2·1·0.5 m 마다 계산점 수와 예상 시간(지난 단면 계산 속도,
        처음이면 지난 격자 계산 속도). 반환 간격(m, None = 자동). 취소하면 False"""
        from sori import section as SEC
        from sori import summary as SM
        try:
            rows = SEC.estimate(str(Path(self.out).resolve()), p1, p2)
        except Exception as e:
            log.warning("단면 점 수 추정 실패 — 자동 간격으로: %s", e)
            return None
        rate = self._sec_rate
        if rate is None:
            rc = self.R.get("run_conditions") or {}
            prev = sum(len(self.R[k]) for k in ("grid", "facade") if k in self.R and k in (rc.get("targets") or []))
            if rc.get("elapsed_s") and prev:
                rate = prev / float(rc["elapsed_s"])
        items = []
        for c, (ds, dz, n) in zip(SEC.SPACING_CHOICES, rows):
            t = SM.estimate_time(n, rate)
            lab = (f"자동 — 가로 {ds:g} m · 세로 {dz:g} m" if c is None else f"{c:g} m 간격") + f"  ·  {n:,}점"
            items.append(lab + (f"  ·  약 {fmt_dur(t)}" if t is not None else ""))
        it, ok = QtWidgets.QInputDialog.getItem(
            self, "단면 계산점 간격", "가로·세로 간격을 고르세요(촘촘할수록 매끄럽고 오래 걸림).\n"
            + ("예상 시간은 지난 단면 계산 속도 기준" if self._sec_rate else "예상 시간은 지난 격자 계산 속도 기준(단면은 보통 더 빠름)"),
            items, 0, False)
        if not ok:
            return False
        return SEC.SPACING_CHOICES[items.index(it)]

    def start_section(self, p1, p2):
        import copy
        import time
        if self.cfg is None or not self.out or not (Path(self.out) / "model_rasters.npz").exists():
            return self._err("단면은 계산한 결과 폴더(모델 파일 model_rasters.npz 포함)와 설정 파일이 열려 있어야 합니다.")
        sp = self._section_spacing(p1, p2)
        if sp is False:
            self._redraw_main()                          # 그리던 선 지움
            return self.lbl_status.setText("단면 계산 취소")
        if self.act_path.isChecked():                    # 오른쪽 창을 단면이 씀
            self.act_path.setChecked(False)
            self._toggle_paths(False)
        cfg = copy.deepcopy(self.cfg)
        cfg["project"]["output_dir"] = str(Path(self.out).resolve())
        self._sec_t0 = time.time()
        self._launch(Worker(self.bridge, "section", cfg=cfg, p1=p1, p2=p2, ds=sp, dz=sp),
                     f"단면 계산 중… A({p1[0]:.0f}, {p1[1]:.0f}) → B({p2[0]:.0f}, {p2[1]:.0f})"
                     + ("" if sp is None else f" · {sp:g} m 간격"))

    def _draw_section_line(self):
        """나란히 보기 오른쪽 창에 단면을 보일 때 왼쪽 격자 지도에 A–B 선"""
        ax = self.map.ax
        if not self._section_side or "section" not in self.R or ax is None or self.view != "grid":
            return
        info = self.R["section"][1]
        (x1, y1), (x2, y2) = info["p1"], info["p2"]
        xl, yl = ax.get_xlim(), ax.get_ylim()
        dpi = getattr(self.map.fig, "_original_dpi", None) or self.map.fig.dpi
        k = 72.0 / dpi
        ax.plot([x1, x2], [y1, y2], "-", color="white", lw=4.5 * k, zorder=53, gid="section_line")
        ax.plot([x1, x2], [y1, y2], "-", color="#1f5fbf", lw=2.4 * k, zorder=54, gid="section_line")
        for (x, y), t in (((x1, y1), "A"), ((x2, y2), "B")):
            ax.annotate(t, (x, y), xytext=(5, 5), textcoords="offset points", color="#1f5fbf", fontsize=13 * k,
                        weight="bold", zorder=55, bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="#1f5fbf", lw=0.6))
        ax.set_xlim(xl); ax.set_ylim(yl)
        self.map.canvas.draw_idle()

    def _draw_section(self, target=None):
        """target: 그릴 지도 창(None = 가운데 창 self.map — 평면 + 단면, 나란히 보기면 self.map3d — 단면만)"""
        side = target is not None
        target = target or self.map
        D, info = self.R["section"]
        key = self.cb_key.currentData()
        grid = self._grid_array(self.cb_h.currentData(), key)
        L = info["length_m"]
        vals = D[key].dropna()
        title = f"단면 · A→B {L:.0f} m · {KEYS[key]} · 최대 {vals.max():.1f} dB"   # 자세한 값은 속성 창
        target.draw_section(D, info, key, grid, self.R.get("buildings"), title, plan=not side)
        if not side:
            self._set_back(True)
        h4 = D[(D.h > 3.5) & (D.h <= 4.5)][key].dropna()
        rows = [("단면 길이 m", f"{L:.1f}"), ("A", f"{info['p1'][0]:.1f}, {info['p1'][1]:.1f}"),
                ("B", f"{info['p2'][0]:.1f}, {info['p2'][1]:.1f}"), ("간격 가로·세로 m", f"{info['ds_m']:g} · {info['dz_m']:g}"),
                ("반사 차수", str(info["reflection_order"])), ("계산점", f"{len(vals):,}"),
                (f"{key} 최대 dB", f"{vals.max():.1f}"), (f"{key} 최소 dB", f"{vals.min():.1f}")]
        if len(h4):
            rows.append((f"지면 위 4 m 부근 {key} 범위", f"{h4.min():.1f} ~ {h4.max():.1f}"))
        self._show_props(rows)
        self.lbl_status.setText("단면 소음지도(오른쪽) — 왼쪽 파란 선 A–B 가 단면 위치, 건물을 누르면 3D 로 바뀜, "
                                "결과 표는 결과 폴더 section_result.csv" if side else
                                "단면 소음지도 — '◀ 격자 지도'로 돌아감, 결과 표는 결과 폴더 section_result.csv")

    def _grid_array(self, h, key, Gh=None):
        R = self.R
        if "grid" not in R or h is None:
            return None
        if Gh is None:
            Gh = R["grid"][R["grid"].h == h]
        xs, ys = np.sort(Gh.x.unique()), np.sort(Gh.y.unique())
        step = float(np.min(np.diff(xs))) if len(xs) > 1 else 1.0
        A = np.full((len(ys), len(xs)), np.nan)
        A[np.searchsorted(ys, Gh.y), np.searchsorted(xs, Gh.x)] = Gh[key]
        return A, (xs[0] - step / 2, xs[-1] + step / 2, ys[0] - step / 2, ys[-1] + step / 2)

    def save_map(self, side=False):
        """지금 보이는 지도(격자 또는 벽면)를 파일로 저장. 기본 이름은 결과 파일명 규칙(sori.naming).
        side=True: 나란히 보기 오른쪽 3D 파사드 창을 저장"""
        mv = self.map3d if side else self.map
        if mv.ax is None and mv.ax3d is None:
            return self._err("저장할 지도가 없습니다. 결과를 먼저 열고" + (" 건물을 클릭하세요." if side else " 여세요."))
        key, h = self.cb_key.currentData(), self.cb_h.currentData()
        kind, extra = (("grid", f"_경로_{self._path_name or 'X{:.0f}_Y{:.0f}'.format(self.paths[1]['x'], self.paths[1]['y'])}")
                       if self._paths_side() else ("section", "") if self._section_side
                       else ("facade", f"_3D_건물{self.facade_bid}")) if side else \
            {"grid": ("grid", f"_h{h:g}m" if h is not None else ""),
             "facade": ("facade", f"_3D_건물{self.view_bid}"),
             "section": ("section", "")}[self.view]
        try:
            from sori.naming import rule_name
            name = rule_name(self.cfg, kind, key, extra)
        except Exception:
            name = f"SORI_{kind}_{key}{extra}"
        base = Path(self.out or default_output_dir()) / f"{name}.png"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "지도 저장", str(base), "PNG 그림 (*.png);;PDF 문서 (*.pdf)")
        if path:
            self.save_map_to(path, mv)

    def save_map_to(self, path, mv=None):
        mv = mv or self.map
        path = Path(path)
        if path.suffix.lower() not in (".png", ".pdf"):
            path = path.with_suffix(".png")
        # 화면의 격자 지도(지도만 크게)는 저장할 때 보고서 틀(A3, 정보란 포함)로 다시 그려 저장
        report = mv is self.map and self.view == "grid" and self._hq_ctx() is not None
        if report:
            self._force_report = True
            self._redraw_main()
        try:
            mv.fig.savefig(path, dpi=200, facecolor=mv.fig.get_facecolor())
        finally:
            if report:
                self._force_report = False
                self._redraw_main()
        log.info("지도 저장: %s", path)
        self.lbl_status.setText(f"지도 저장: {path}")
        return path

    # ------------------------------------------------------------ 입력값 편집
    def _on_input_saved(self, path):
        self.open_config(path, self.out)
        self.show_input()
        self.lbl_status.setText("입력값 저장함 — 결과는 예전 값으로 계산된 것. 새 값으로 보려면 ‘계산 실행’")

    def _traffic_new_file(self, path):
        """교통량 파일을 새로 만들었으면 설정 파일의 교통량 경로에 넣고 설정도 저장"""
        e = self.editor.w["input.traffic.path"][0].edit
        e.setText(self.editor._rel(path))
        if self.editor.save():
            self.input_tabs.setCurrentWidget(self.traffic)

    def _lanes_new_file(self, path):
        """도로 확인값 파일을 새로 만들었으면 설정 파일에 경로를 넣고 설정도 저장(차로 수 탭은 그대로)"""
        self.editor.w["input.road_override.path"][0].edit.setText(self.editor._rel(path))
        if self.editor.save():
            self.input_tabs.setCurrentWidget(self.lanes)

    def _show_lane_focus(self, v):
        """차로 수 탭 ‘지도에서 보기’: 지도 화면에서 그 도로(굵은 회색)·디지타이즈 방향 화살표·음원선(빨강 = 정방향,
        파랑 = 역방향, 보라 = 방향 미상 중앙선)을 그리고 그 도로로 확대"""
        self._lane_focus = v
        self.enter_task("map")
        self.lbl_status.setText(f"{v[2]} — 빨강 = 정방향(그린 방향) 음원선, 파랑 = 역방향, 화살표 = 그린 방향. "
                                "홈 ▸ ‘데이터 입력’으로 돌아가 고침")

    def _draw_lane_focus(self):
        v, ax = getattr(self, "_lane_focus", None), self.map.ax
        if v is None or ax is None or self.view != "grid":
            return
        g, offs, title = v
        x0, y0, x1, y1 = g.bounds
        m = max(x1 - x0, y1 - y0) * 0.6 + 25
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        from shapely.geometry import box
        win = box(cx - m, cy - m, cx + m, cy + m)
        for k, kw in (("buildings", dict(fc="#ececec", ec="#999", lw=0.4, zorder=0.5)),
                      ("roads", dict(color="#bbb", lw=1.0, zorder=0.6))):    # 격자 범위 밖이어도 주변이 보이게(맨 아래)
            G = self.R.get(k)
            if G is not None and len(G):
                G[G.intersects(win)].plot(ax=ax, **kw)
        xs, ys = g.xy if g.geom_type == "LineString" else list(g.geoms)[0].xy
        ax.plot(xs, ys, "-", color="#555", lw=6, alpha=0.35, zorder=41, solid_capstyle="round")
        line = g if g.geom_type == "LineString" else list(g.geoms)[0]
        p0, p1 = line.interpolate(0.45, normalized=True), line.interpolate(0.55, normalized=True)
        ax.annotate("", xy=(p1.x, p1.y), xytext=(p0.x, p0.y), zorder=43,
                    arrowprops=dict(arrowstyle="-|>", color="#222", lw=1.5, mutation_scale=16))
        for off, fwd in offs:
            o = line if off == 0 else line.offset_curve(off)
            if o.is_empty:
                continue
            ox, oy = o.xy if o.geom_type == "LineString" else list(o.geoms)[0].xy
            col = "#7a3fb0" if fwd is None else ("#d62020" if fwd else "#1f5fbf")
            ax.plot(ox, oy, "-", color=col, lw=2.8, zorder=42)
        ax.set_xlim(cx - m, cx + m)
        ax.set_ylim(cy - m, cy + m)
        self.map.canvas.draw_idle()

    def _start_pick_center(self):
        self.enter_task("map")
        self._picking_center = True
        self.lbl_status.setText("지도에서 새 격자 중심을 클릭하세요 — 클릭하면 입력값 화면으로 돌아감")

    def _preview_extent(self, v):
        self._prev_box = v
        if self.stack.currentIndex() != 1:
            self.enter_task("map")                     # 지도 화면에서 보여 줌(그리면서 상자도 덧그림)
        else:
            self._redraw_main()
        cx, cy, w, h = v
        self.lbl_status.setText(f"새 계산 범위(주황 점선) {w:g} × {h:g} m — 저장 전 미리보기, 고치려면 홈 ▸ ‘데이터 입력’")

    def _draw_preview(self):
        """입력값 ‘범위 미리보기’ 상자(주황 점선). 지도를 다시 그려도 남도록 그릴 때마다 덧그림"""
        v, ax = getattr(self, "_prev_box", None), self.map.ax
        if v is None or ax is None or self.view != "grid":
            return
        cx, cy, w, h = v
        x0, y0, x1, y1 = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
        xl, yl = ax.get_xlim(), ax.get_ylim()
        ax.plot([x0, x1, x1, x0, x0], [y0, y0, y1, y1, y0], "--", color="#e07000", lw=2.2, zorder=40)
        ax.plot([cx], [cy], "+", color="#e07000", ms=14, mew=2, zorder=40)
        ax.set_xlim(min(xl[0], x0 - 10), max(xl[1], x1 + 10))     # 새 범위가 밖으로 나가면 보이게 넓힘
        ax.set_ylim(min(yl[0], y0 - 10), max(yl[1], y1 + 10))
        self.map.canvas.draw_idle()

    def _on_map_click(self, x, y):
        if self._picking_center:                                   # 입력값: 격자 중심 찍기
            self._picking_center = False
            self.editor.set_center(x, y)
            self._prev_box = None
            self.show_input()
            self.lbl_status.setText(f"격자 중심 ({x:.1f}, {y:.1f}) 을 넣었음 — ‘저장’해야 설정 파일에 반영")
            return
        if self.act_pick_pt.isChecked() and self.view == "grid":   # 점 수음점 찍기
            return self._pick_point(x, y)
        if self.act_pick_box.isChecked() and self.view == "grid":  # 계산 범위 두 모서리
            return self._pick_box_click(x, y)
        if self._bar_draw is not None and self.view == "grid":     # 방음벽 그리는 중: 꼭짓점 추가
            self._bar_draw.append((float(x), float(y)))
            self._draw_barriers()
            n = len(self._bar_draw)
            self.lbl_status.setText(f"방음벽 꼭짓점 {n}개 — 계속 클릭, 끝내려면 더블클릭·오른쪽 클릭 또는 ‘그리기 끝’")
            return
        if self.map_mode == "input" and self.view == "grid":         # 입력 지도: 건물·도로 속성만(소음 없음)
            return self._input_props(x, y)
        if self.act_sec.isChecked() and self.view == "grid":         # 단면 선 긋기 중
            return self._section_click(x, y)
        if self.act_path.isChecked() and self.view == "grid":        # 경로 보기: 이 지점의 경로 계산
            return self.start_paths(x, y)
        if self.view == "section":
            return
        bid = self._building_at(x, y)
        if bid is not None and self.show_facade(bid):        # 건물 클릭 → 벽면 소음지도
            return
        if self.view == "grid" and not getattr(self, "_grid_now", None):
            return self._road_props(x, y)                          # 입력 지도: 가까운 도로의 속성·교통량
        if self.view != "grid" or not getattr(self, "_grid_now", None):
            return
        Gh, key = self._grid_now
        if "d" + key in Gh.columns:                                # 전후 차이 지도: 기준·비교·차이
            i = int(np.argmin((Gh.x.values - x) ** 2 + (Gh.y.values - y) ** 2))
            r = Gh.iloc[i]
            rows = [("격자점 X", f"{r.x:.1f}"), ("격자점 Y", f"{r.y:.1f}"), ("높이 h m", f"{r.h:g}")]
            for k in ("LrT", "LrN"):
                if "d" + k in Gh.columns:
                    rows += [(f"{k} 기준 dB", f"{r[k + '_base']:.1f}"), (f"{k} 비교 dB", f"{r[k + '_new']:.1f}"),
                             (f"Δ{k} dB", f"{r['d' + k]:+.1f}")]
            self._show_props(rows, f"차이 Δ{key} {r['d' + key]:+.1f} dB")
            self.lbl_status.setText(f"격자점 ({r.x:.1f}, {r.y:.1f}) Δ{key} {r['d' + key]:+.1f} dB (비교 − 기준)")
            return
        i = int(np.argmin((Gh.x.values - x) ** 2 + (Gh.y.values - y) ** 2))
        r = Gh.iloc[i]
        rows = [("격자점 X", f"{r.x:.1f}"), ("격자점 Y", f"{r.y:.1f}"), ("지면 Z m", f"{r.z:.2f}"), ("높이 h m", f"{r.h:g}"),
                ("LrT dB", f"{r.LrT:.1f}"), ("LrN dB", f"{r.LrN:.1f}")]
        rows += [(c, f"{r[c]:.1f}") for c in Gh.columns if c.startswith("LrT_") and pd.notna(r[c])]
        self._show_props(rows, f"격자점 {key} {r[key]:.1f} dB")
        self.lbl_status.setText(f"격자점 ({r.x:.1f}, {r.y:.1f}) {key} {r[key]:.1f} dB")

    # ------------------------------------------------------------ 건물 단위 표·건물별 값·용도지역 기준
    def _building_panel(self):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("층"))
        self.cb_bld_floor = QtWidgets.QComboBox()
        self.cb_bld_floor.setToolTip("고른 층의 벽면 값만으로 건물 최고 레벨을 셈(‘건물별 값’ 지도·아래 벽면 표에도 적용)")
        row.addWidget(self.cb_bld_floor)
        row.addWidget(QtWidgets.QLabel("건물 종류"))
        self.cb_bld_kind = QtWidgets.QComboBox()
        row.addWidget(self.cb_bld_kind)
        self.chk_bld_exc = QtWidgets.QCheckBox("기준 초과 건물만")
        for c in (self.cb_bld_floor, self.cb_bld_kind):
            c.setMinimumWidth(110)
            c.currentIndexChanged.connect(self._fill_building_table)
            c.currentIndexChanged.connect(self._bld_filter_changed)
        self.chk_bld_exc.toggled.connect(self._fill_building_table)
        self.chk_bld_exc.toggled.connect(self._bld_filter_changed)
        row.addWidget(self.chk_bld_exc)
        self.lbl_bld = QtWidgets.QLabel("")
        self.lbl_bld.setStyleSheet("color: #555555;")
        row.addWidget(self.lbl_bld, 1)
        b = QtWidgets.QPushButton("CSV 저장…")
        b.setToolTip("건물 단위 표를 CSV 로 저장(기본: 결과 폴더의 building_summary.csv)")
        b.clicked.connect(self.save_building_table)
        row.addWidget(b)
        lay.addLayout(row)
        self.tv_bld = table_view(self.m_bld)
        self.tv_bld.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tv_bld.clicked.connect(self._bld_row_clicked)
        lay.addWidget(self.tv_bld, 1)
        return w

    def _building_table(self):
        """건물 단위 표(벽면 결과가 있는 건물): 최고 레벨·최고 층·용도지역 기준·초과량(sori.summary.building_table)"""
        from sori import summary as SM
        R = self.R
        if R.get("facade") is None or R.get("buildings") is None or not len(R["facade"]):
            return pd.DataFrame()
        FR = R["facade"]
        fl = self.cb_bld_floor.currentData()
        if fl is not None:                                          # 층 거름: 그 층 벽면 값만
            FR = FR[FR.floor == fl]
        zones = None
        lim = ((self.cfg or {}).get("plots") or {}).get("limits")      # 설정에 기준을 직접 넣었으면 그 값이 우선
        try:
            if self.cfg is not None:
                zones = SM.building_zones(self.cfg, R["buildings"])
        except Exception as e:
            log.warning("용도지역 파일을 읽지 못해 모두 기본 지역으로 봄: %s", e)
        T = SM.building_table(FR, R["buildings"], zones, lim)
        kind = self.cb_bld_kind.currentData()
        return T[T["종류"] == kind].reset_index(drop=True) if kind is not None and len(T) else T

    def _fill_bld_filters(self):
        """층·건물 종류 목록을 결과에 맞게 채움(전체가 첫 항목)"""
        FR, B = self.R.get("facade"), self.R.get("buildings")
        for cb, vals, fmt in ((self.cb_bld_floor, sorted(FR.floor.unique()) if FR is not None and len(FR) else [],
                               lambda v: f"{int(v)}층"),
                              (self.cb_bld_kind, sorted(B[B.bid.isin(FR.bid)].kind.astype(str).unique())
                               if FR is not None and B is not None and len(FR) else [], str)):
            cb.blockSignals(True)
            cb.clear()
            cb.addItem("전체", None)
            for v in vals:
                cb.addItem(fmt(v), int(v) if cb is self.cb_bld_floor else v)
            cb.blockSignals(False)

    def _fill_building_table(self, *_):
        from sori import summary as SM
        T = self._bld_all = self._building_table()
        FR = self.R.get("facade")                                   # 아래 '벽면' 표도 같은 조건(층·종류·초과)
        if FR is not None and len(FR) and self.view != "facade":
            F2 = FR
            if self.cb_bld_floor.currentData() is not None:
                F2 = F2[F2.floor == self.cb_bld_floor.currentData()]
            if self.cb_bld_kind.currentData() is not None or self.chk_bld_exc.isChecked():
                keep = T.bid if not self.chk_bld_exc.isChecked() else T.bid[T["초과"] == "초과"]
                F2 = F2[F2.bid.isin(keep)]
            self.m_facade.set(F2.sort_values("LrT", ascending=False).head(200).reset_index(drop=True))
        if len(T) and self.chk_bld_exc.isChecked():
            T = T[T["초과"] == "초과"].reset_index(drop=True)
        self.m_bld.set(T if len(T) else None)
        A = self._bld_all
        if len(A):
            n = int((A["초과"] == "초과").sum())
            self.lbl_bld.setText(f"건물 {len(A):,}동 · 기준 초과 {n:,}동(주간 {int((A['초과_주'] > 0).sum()):,}, "
                                 f"야간 {int((A['초과_야'] > 0).sum()):,}) — 기준: {SM.ZONE_SOURCE}. 줄을 누르면 그 건물로")
        else:
            self.lbl_bld.setText("벽면 계산 결과가 없습니다.")

    def _bld_filter_changed(self):
        if self.act_bmax.isChecked() and self.view == "grid":
            self.redraw()

    def _bld_row_clicked(self, idx):
        src = self.tv_bld.model().mapToSource(idx)
        if 0 <= src.row() < len(self.m_bld.df):
            bid = int(self.m_bld.df.iloc[src.row()].bid)
            if self.view != "grid" and not self.act_split.isChecked():
                self.show_grid()
            self.show_facade(bid)

    def save_building_table(self, path=None):
        A = getattr(self, "_bld_all", None)
        if A is None or not len(A):
            return self._err("저장할 건물 단위 표가 없습니다(벽면 계산 결과 필요).")
        if not path:
            path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "건물 단위 표 저장",
                                                            str(Path(self.out) / "building_summary.csv"), "CSV (*.csv)")
        if not path:
            return None
        A.to_csv(path, index=False, encoding="utf-8-sig")
        self.lbl_status.setText(f"건물 단위 표 저장: {path}")
        return Path(path)

    def _building_overlay(self):
        """‘건물별 값’: 건물을 벽면 최고 레벨 색으로 칠하고, 기준을 넘는 건물은 굵은 테두리"""
        ax, A, B = self.map.ax, getattr(self, "_bld_all", None), self.R.get("buildings")
        if (not self.act_bmax.isChecked() or self.view != "grid" or ax is None or A is None or not len(A)
                or B is None or self.act_diff.isChecked()):
            return
        key = self.cb_key.currentData()
        if self.map.hq is not None:
            from sori.maps_hq import din_cmap
            cmap, norm = din_cmap()
        else:
            from sori.plots import classes
            cmap, norm, _ = classes(30, 5)
        if self.chk_bld_exc.isChecked():
            A = A[A["초과"] == "초과"]
        E = B.merge(A[["bid", f"{key}_max", "초과_주", "초과_야"]], on="bid")
        if not len(E):
            return
        xl, yl = ax.get_xlim(), ax.get_ylim()
        E.plot(ax=ax, color=[cmap(norm(v)) for v in E[f"{key}_max"]], ec="#3a3a3a", lw=0.35, zorder=14, gid="bmax")
        X = E[E["초과_주" if key == "LrT" else "초과_야"] > 0]
        if len(X):
            X.boundary.plot(ax=ax, color="#8e0055", lw=1.6, zorder=15)
        ax.set_xlabel(""); ax.set_ylabel("")
        ax.set_xlim(xl)
        ax.set_ylim(yl)
        self.map.canvas.draw_idle()

    def _exposure_props(self):
        """건물별 노출(최고 레벨 등급별 건물 수) — 속성 창"""
        from sori import summary as SM
        A = getattr(self, "_bld_all", None)
        if A is None or not len(A):
            return []
        rows = [("— 건물별 노출", "주간 · 야간 (동)")]
        d, n = SM.exposure(A, "LrT"), SM.exposure(A, "LrN")
        for (lab, a), (_l, b) in zip(d, n):
            if a or b:
                rows.append((f"{lab} dB", f"{a:,} · {b:,}"))
        rows.append(("기준 초과 건물", f"{int((A['초과'] == '초과').sum()):,} / {len(A):,}동"))
        return rows

    # ------------------------------------------------------------ 측정 − 계산 차이
    def _measured_table(self, R):
        """점 수음점 결과 + 측정값(receivers.points.measured) → 차이 표(화면 열 + key·diff). 없으면 None"""
        mp = (((self.cfg or {}).get("receivers") or {}).get("points") or {}).get("measured")
        if not mp or R.get("points") is None:
            return None
        from sori import summary as SM
        try:
            D = SM.measured_compare(R["points"], SM.read_measured(mp))
        except Exception as e:
            log.warning("측정값 파일을 읽지 못함(%s): %s", mp, e)
            return None
        v = D["diff"].dropna()
        if len(v):
            log.info("측정 − 계산: %d개 비교, 계산 − 측정 평균 %+.1f dB, 범위 %+.1f ~ %+.1f dB", len(v), v.mean(), v.min(), v.max())
        return pd.DataFrame({"point_id": D.point_id, "x": D.x, "y": D.y, "시간대": D.key, "측정 dB": D.meas,
                             "계산 dB": D.calc, "계산−측정 dB": D["diff"], "key": D.key, "diff": D["diff"]})

    def _draw_measured(self):
        D, ax = getattr(self, "_meas", None), self.map.ax
        if D is None or ax is None or self.view != "grid" or not self.act_meas.isChecked():
            return
        key = self.cb_key.currentData()
        Dk = D[(D.key == key) & D["diff"].notna()]
        if not len(Dk):
            return
        from matplotlib.colors import BoundaryNorm, ListedColormap
        from sori import compare
        cmap = ListedColormap(compare.DIFF_COLS)
        lv = [-500] + compare.DIFF_EDGES + [500]
        norm = BoundaryNorm(lv, len(compare.DIFF_COLS))
        xl, yl = ax.get_xlim(), ax.get_ylim()
        dpi = getattr(self.map.fig, "_original_dpi", None) or self.map.fig.dpi
        k = 72.0 / dpi
        ax.scatter(Dk.x, Dk.y, s=(11 * k) ** 2, c=[cmap(norm(v)) for v in Dk["diff"]], edgecolors="#111111",
                   linewidths=1.2 * k, zorder=55, gid="measured")
        for pid, x, y, dv in zip(Dk.point_id, Dk.x, Dk.y, Dk["diff"]):
            ax.annotate(f"{pid} {dv:+.1f}", (x, y), xytext=(7, 5), textcoords="offset points",
                        fontsize=14 * k, weight="bold", zorder=56,
                        bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="#555555", lw=0.5))
        ax.set_xlim(xl)
        ax.set_ylim(yl)
        v = Dk["diff"]
        self.lbl_status.setText(f"측정 − 계산({key}): {len(v)}개, 계산 − 측정 평균 {v.mean():+.1f} dB "
                                f"(범위 {v.min():+.1f} ~ {v.max():+.1f}) — 파랑 = 계산이 낮음, 빨강 = 계산이 높음")
        self.map.canvas.draw_idle()

    # ------------------------------------------------------------ 보기 조건(성분·주야 차이)
    def _comps_selected(self):
        """고른 성분 목록. 보이는 성분이 모두 체크돼 있으면 None(= 전체 값 그대로)"""
        vis = [k for k, a in self.act_comp.items() if a.isVisible()]
        sel = [k for k in vis if self.act_comp[k].isChecked()]
        return None if len(sel) == len(vis) else sel

    def _draw_daynight(self, h):
        """주야 차이 지도(LrT − LrN). 그리지 못하면 False"""
        C = self._hq_ctx()
        if C is None:
            self.lbl_status.setText("주야 차이 지도는 설정 파일과 결과 폴더가 함께 열려 있어야 함")
            return False
        from sori import compare
        G = self.R["grid"]
        D = G.assign(dLrT=G.LrT - G.LrN)
        Dh = D[D.h == h]
        v = Dh.dLrT.values[np.isfinite(Dh.dLrT.values)]
        try:
            self.map.draw_diff(C, C.cfg, D, "LrT", h, lambda: (self._redraw_grid(), self._grid_extras()),
                               f"주야 차이(LrT − LrN) · 높이 {h:g} m · 평균 {v.mean():.1f} dB · 범위 {v.min():.1f} ~ {v.max():.1f} dB",
                               compare.DN_EDGES, compare.DN_COLS)
        except Exception as e:
            log.warning("주야 차이 지도 그리기 실패: %s", e)
            return False
        self._grid_now = (G[G.h == h], self.cb_key.currentData())
        return True

    # ------------------------------------------------------------ 입력 편의(예상 점 수·점 찍기·범위 그리기·도로 속성)
    def _update_estimate(self, *_):
        """계산 실행 탭: 계산 전 예상 수음점 수와 시간(지난 계산의 속도 기준)"""
        if self.cfg is None:
            return self.lbl_est.setText("")
        from sori import summary as SM
        which = [k for k, c in self.chk.items() if c.isChecked()]
        xh = self._extra_heights()
        try:
            e = SM.estimate_points(self.cfg, which, self.R.get("build_info"), xh)
        except Exception:
            return self.lbl_est.setText("")
        parts, n = [], 0
        if e["grid"]:
            g = e["grid"] // 4 if self.chk_fast.isChecked() else e["grid"]
            parts.append(f"격자 {'' if e['grid_exact'] else '최대 '}{g:,}점")
            n += g
        if "facade" in which and self.cfg["receivers"]["facade"]["enabled"]:
            if e["facade"] is not None:
                fa = e["facade"] // 2 if self.chk_fast.isChecked() else e["facade"]
                parts.append(f"벽면 {fa:,}점")
                n += fa
            else:
                parts.append("벽면은 모델을 만든 뒤 알 수 있음")
        rate, rc, ri = None, self.R.get("run_conditions") or {}, self.R.get("run_info") or {}
        prev = sum(len(self.R[k]) for k in ("grid", "facade") if k in self.R and k in (rc.get("targets") or []))
        if rc.get("elapsed_s") and prev and not rc.get("overrides", {}).get("receivers.grid.step"):
            rate = prev / float(rc["elapsed_s"])
        t = SM.estimate_time(n, rate)
        txt = "예상 수음점: " + (" · ".join(parts) if parts else "없음")
        if t is not None and not self.chk_fast.isChecked():
            txt += f" — 지난 계산 속도({rate:,.0f}점/초, 반사 차수·방법이 같을 때) 기준 약 {fmt_dur(t)}"
        self.lbl_est.setText(txt)

    def _only_pick(self, act):
        """지도 클릭을 쓰는 입력 도구는 한 번에 하나만"""
        for a in (self.act_pick_pt, self.act_pick_box):
            if a is not act:
                a.setChecked(False)
        self._box_pts = []
        if self.stack.currentIndex() != 1 or self.view != "grid":
            self.enter_task("map")

    def _toggle_pick_point(self, on=None):
        on = self.act_pick_pt.isChecked() if on is None else on
        if on:
            if self.cfg is None:
                self.act_pick_pt.setChecked(False)
                return self._err("설정 파일(config.yaml)을 먼저 여세요.")
            self._only_pick(self.act_pick_pt)
            self.lbl_status.setText("점 수음점 찍기 — 지도에서 위치를 클릭하면 이름·높이를 묻고 점 수음점 CSV 에 더함(끄려면 차림표에서 다시 선택)")

    def _points_path(self):
        p = ((self.cfg["receivers"].get("points") or {}).get("path")) if self.cfg is not None else None
        return Path(p) if p else Path(self.cfg_path).parent / "points.csv"

    def _pick_point(self, x, y):
        P = None
        try:
            if self._points_path().is_file():
                from sori.run import read_points
                P = read_points(self._points_path(), 1.5)
        except Exception:
            P = None
        k = (len(P) if P is not None else 0) + 1
        text, ok = QtWidgets.QInputDialog.getText(self, "점 수음점 추가", f"({x:.1f}, {y:.1f})\n이름, 지면 위 높이(m) — 예: P{k}, 1.5",
                                                  text=f"P{k}, 1.5")
        if not ok:
            return
        try:
            pid, h = [t.strip() for t in text.split(",")]
            self.add_point(x, y, pid, float(h))
        except ValueError as e:
            self._err(f"‘이름, 높이’ 형식으로 넣으세요.\n{e}")

    def add_point(self, x, y, pid, h):
        """점 수음점 CSV(point_id, x, y, h)에 한 줄 더함. 파일이 없으면 설정 폴더에 points.csv 를 만들고 설정에 경로를 넣음"""
        p = self._points_path()
        if p.is_file():
            from sori.inputs import read_csv_any
            df = read_csv_any(p)
            cols = {c: next((n for n in names if n in df.columns), None)
                    for c, names in (("point_id", ("point_id", "id", "ID")), ("x", ("x", "x_epsg5186", "X")),
                                     ("y", ("y", "y_epsg5186", "Y")), ("h", ("h", "height_m", "height", "z")))}
            if None in (cols["point_id"], cols["x"], cols["y"]):
                raise ValueError(f"점 수음점 파일의 열을 알 수 없음: {list(df.columns)}")
            if cols["h"] is None:
                df["h"] = ""
                cols["h"] = "h"
            if pid in set(df[cols["point_id"]].astype(str)):
                raise ValueError(f"이름 ‘{pid}’ 가 이미 있음")
        else:
            df = pd.DataFrame(columns=["point_id", "x", "y", "h"])
            cols = {c: c for c in df.columns}
        row = {c: "" for c in df.columns}
        row.update({cols["point_id"]: pid, cols["x"]: f"{x:.2f}", cols["y"]: f"{y:.2f}", cols["h"]: f"{h:g}"})
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        df.to_csv(p, index=False, encoding="utf-8-sig")
        if not (self.cfg["receivers"].get("points") or {}).get("path"):   # 설정에 경로가 없으면 넣고 저장
            self.editor.w["receivers.points.path"][0].edit.setText(self.editor._rel(str(p)))
            self.editor.save()
            self.enter_task("map")
        log.info("점 수음점 추가: %s (%.2f, %.2f) h = %g m → %s", pid, x, y, h, p)
        self.lbl_status.setText(f"점 수음점 {pid} 추가({len(df)}개) — {p.name}. 값은 ‘지도 확인 · 계산’에서 점 수음점을 계산해야 나옴")
        return p

    def _toggle_pick_box(self, on=None):
        on = self.act_pick_box.isChecked() if on is None else on
        self._box_pts = []
        if on:
            if self.cfg is None:
                self.act_pick_box.setChecked(False)
                return self._err("설정 파일(config.yaml)을 먼저 여세요.")
            self._only_pick(self.act_pick_box)
            self.lbl_status.setText("계산 범위 그리기 — 범위의 한 모서리를 클릭하고, 맞은편 모서리를 클릭")
        self._draw_pick_box()

    def _pick_box_click(self, x, y):
        self._box_pts.append((float(x), float(y)))
        if len(self._box_pts) < 2:
            self._draw_pick_box()
            return self.lbl_status.setText("맞은편 모서리를 클릭하세요")
        (x1, y1), (x2, y2) = self._box_pts
        self._box_pts = []
        self.act_pick_box.setChecked(False)
        w, h = round(abs(x2 - x1)), round(abs(y2 - y1))
        if w < 10 or h < 10:
            return self.lbl_status.setText("범위가 너무 작음(10 m 미만) — 다시 그리세요")
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        self.editor.set_center(cx, cy)
        self.editor.w["receivers.grid.size"][0].setText(f"{w:g}, {h:g}")
        self._prev_box = (cx, cy, float(w), float(h))
        self._redraw_main()
        self.lbl_status.setText(f"새 계산 범위 {w:g} × {h:g} m(주황 점선)를 입력값 칸에 넣었음 — 홈 ▸ ‘데이터 입력’에서 ‘저장’해야 설정 파일에 반영")

    def _draw_pick_box(self):
        ax = self.map.ax
        for a in getattr(self, "_box_art", []):
            try:
                a.remove()
            except (ValueError, NotImplementedError, AttributeError):
                pass
        self._box_art = []
        pts = getattr(self, "_box_pts", [])
        if ax is None or self.view != "grid" or not pts:
            return
        xl, yl = ax.get_xlim(), ax.get_ylim()
        self._box_art += ax.plot([pts[0][0]], [pts[0][1]], "+", color="#e07000", ms=16, mew=2.4, zorder=49)
        ax.set_xlim(xl)
        ax.set_ylim(yl)
        self.map.canvas.draw_idle()

    def _input_props(self, x, y):
        """입력 지도 클릭: 건물이면 수치지도 속성(이름·종류·층수·높이), 아니면 가까운 도로"""
        from shapely.geometry import Point
        L = self._input_layers()
        B = L.get("buildings")
        if B is not None and len(B):
            hit = B[B.geometry.contains(Point(x, y))]
            if len(hit):
                f = ((self.cfg or {}).get("input", {}).get("map", {}).get("fields", {}) or {})
                r = hit.iloc[0]
                rows = [(lab, str(r.get(col, "")) if col in hit.columns else "")
                        for lab, col in (("UFID", f.get("uid", "UFID")), ("명칭", f.get("building_name", "명칭")),
                                         ("종류", f.get("building_kind", "종류")), ("층수", f.get("building_floors", "층수")))
                        if col in hit.columns]
                for lab, col in (("명칭", "bname"), ("종류", "kind"), ("층수", "floors"), ("높이 m", "H")):
                    if col in hit.columns:
                        rows.append((lab, f"{r[col]:g}" if isinstance(r[col], float) else str(r[col])))
                rows.append(("바닥 면적 m²", f"{r.geometry.area:.0f}"))
                self._show_props(rows, "건물(수치지도)")
                self.lbl_status.setText(f"건물 ({x:.1f}, {y:.1f}) — 수치지도 속성. 소음 값은 홈 ▸ ‘지도 확인 · 계산’")
                return
        if self.R.get("roads") is not None:               # 계산한 적이 있으면 도로 속성 + 계산에 쓴 교통량
            return self._road_props(x, y)
        R0 = L.get("roads")
        if R0 is not None and len(R0) and "rname" not in R0.columns:
            f = ((self.cfg or {}).get("input", {}).get("map", {}).get("fields", {}) or {})
            d = R0.geometry.distance(Point(x, y))
            i = int(np.argmin(d.values))
            if float(d.values[i]) <= 12.0:
                r = R0.iloc[i]
                rows = [(lab, str(r.get(col, ""))) for lab, col in
                        (("도로명", f.get("road_name", "도로명")), ("UFID", f.get("uid", "UFID")), ("차로 수", f.get("road_lanes", "차로수")),
                         ("도로폭 m", f.get("road_width", "도로폭")), ("일방통행", f.get("road_oneway", "일방통행"))) if col in R0.columns]
                rows.append(("길이 m", f"{r.geometry.length:.1f}"))
                self._show_props(rows, "도로(수치지도)")
                self.lbl_status.setText("도로 — 수치지도 속성. 교통량은 홈 ▸ ‘데이터 입력’ ▸ 교통량 탭")
                return
        return self._road_props(x, y)

    def _road_props(self, x, y):
        """입력 지도에서 도로 가까이를 클릭: 그 도로 객체의 속성과 계산에 쓴 교통량(traffic_used.csv)"""
        Rd = self.R.get("roads")
        if Rd is None or not len(Rd):
            return
        from shapely.geometry import Point
        d = Rd.geometry.distance(Point(x, y))
        i = int(np.argmin(d.values))
        if float(d.values[i]) > 12.0:
            return
        r = Rd.iloc[i]
        name = str(r.get("rname") or "") or "(이름 없음)"
        rows = [("도로명", name), ("UFID", str(r.get("ufid", ""))), ("차로 수", str(r.get("lanes", ""))),
                ("도로폭 m", f"{float(r.get('width')):g}" if pd.notna(r.get("width")) else ""),
                ("일방통행", "예" if bool(r.get("oneway")) else "아니오"), ("길이 m", f"{r.geometry.length:.1f}")]
        tp = Path(self.out) / "traffic_used.csv" if self.out else None
        if tp is not None and tp.is_file():
            try:
                T = pd.read_csv(tp)
                # roads.gpkg 를 읽으면 fid 는 GPKG 기본 키로 빠지므로 UFID 로 맞춤
                t = T[T.ufid.astype(str) == str(r.get("ufid", ""))] if "ufid" in T else T.iloc[0:0]
                if len(t):
                    t = t.iloc[0]
                    rows.append(("— 계산에 쓴 교통량", str(t.get("traffic_src", ""))))
                    for c, lab in (("M_day", "M 주간 대/h"), ("M_night", "M 야간 대/h"), ("p1_day", "p1 주간 %"),
                                   ("p2_day", "p2 주간 %"), ("v_pkw", "속도 km/h")):
                        if c in t and pd.notna(t[c]):
                            rows.append((lab, f"{float(t[c]):g}"))
            except Exception as e:
                log.warning("교통량 기록을 읽지 못함: %s", e)
        self._show_props(rows, f"도로 {name}")
        self.lbl_status.setText(f"도로 {name} · {r.get('lanes', '')}차로 — 교통량은 홈 ▸ ‘데이터 입력’ ▸ 교통량 탭에서 이 도로명(또는 UFID) 행을 고침")

    # ------------------------------------------------------------ 수음점 경로선·도로별 기여도
    PATH_TOP = 40                                         # 지도에 그리는 경로 수(기여 큰 순서)

    def _toggle_paths(self, on=None):
        if on is None:
            on = self.act_path.isChecked()
        if on:
            if self.act_sec.isChecked():
                self.act_sec.setChecked(False)
                self.sec_pts = []
            if self.view != "grid":
                self.show_grid()
            self.lbl_status.setText("경로 보기 — 격자 지도에서 수음점으로 볼 지점을 클릭(높이는 도구 줄 ‘높이’ 값)")
        else:
            self.paths = None
            self.m_paths.set(None)
            self._draw_paths()
            if self._split_auto:                         # 경로 보기가 켠 나란히 보기는 함께 끔
                self._split_auto = False
                self.act_split.setChecked(False)
                self._toggle_split(False)
            elif self.act_split.isChecked():             # 오른쪽 창: 보던 건물 3D 또는 안내 글로
                if self.facade_bid is not None:
                    self._draw_facade(self.facade_bid, self.map3d)
                else:
                    self.map3d.message(MSG_3D_EMPTY)
                self._fit_split()

    def _paths_side(self):
        """오른쪽 창에 경로 확대 보기를 그리는 중인가"""
        return self.paths is not None and self.act_path.isChecked() and self.view == "grid"

    def _point_receivers(self):
        """점 수음점 CSV(point_id, x, y, h) — 없거나 읽지 못하면 None"""
        try:
            if self.cfg is not None and self._points_path().is_file():
                from sori.run import read_points
                return read_points(self._points_path(), 1.5)
        except Exception as e:
            log.warning("점 수음점 파일을 읽지 못함: %s", e)
        return None

    def start_paths(self, x, y):
        if self.worker is not None and self.worker.isRunning():
            return self.lbl_status.setText("다른 계산이 끝난 뒤 다시 클릭하세요")
        if self.cfg is None or not self.out or not (Path(self.out) / "model_rasters.npz").exists():
            return self._err("경로 보기는 계산한 결과 폴더(모델 파일 포함)와 설정 파일이 열려 있어야 합니다.")
        h, self._path_name = self.cb_h.currentData(), None
        P = self._point_receivers()
        if P is not None and len(P) and self.map.ax is not None:   # 점 수음점 근처(화면 폭 1.5 %)를 누르면 그 점·높이로
            xl = self.map.ax.get_xlim()
            d2 = ((P.x - x) ** 2 + (P.y - y) ** 2).values
            i = int(np.argmin(d2))
            if d2[i] <= (0.015 * abs(xl[1] - xl[0])) ** 2:
                x, y, h, self._path_name = float(P.x.iloc[i]), float(P.y.iloc[i]), float(P.h.iloc[i]), str(P.point_id.iloc[i])
        if self._building_at(x, y) is not None:
            return self.lbl_status.setText("건물 안은 수음점으로 고를 수 없음 — 건물 밖 지점을 클릭하세요")
        import copy
        cfg = copy.deepcopy(self.cfg)
        cfg["project"]["output_dir"] = str(Path(self.out).resolve())
        m = next(iter(set(((self.R.get("run_info") or {}).get("outputs") or {}).values())), None)
        if m in METHODS:                                 # 결과를 계산한 전파 방법으로
            cfg["propagation_method"] = m
        # 격자 결과와 같은 반사 차수까지(3차를 넘으면 경로 수가 크게 늘어 3차까지만 선으로)
        mo = min(int(cfg["propagation"]["reflection"]["max_order_grid"]), 3)
        self._launch(Worker(self.bridge, "paths", cfg=cfg, x=float(x), y=float(y), h=float(h if h is not None else 4.0),
                            max_order=mo), "경로 계산 중…", busy=True)

    def _show_paths(self):
        from sori import paths as PA
        A, info = self.paths
        key = "L_day" if self.cb_key.currentData() == "LrT" else "L_night"
        name = "LrT" if key == "L_day" else "LrN"
        T = PA.top_paths(A, key, self.PATH_TOP)
        tab = pd.DataFrame({"순위": np.arange(1, len(T) + 1),
                            "경로": ["직접음" if o == 0 else f"{int(o)}차 반사" for o in T.order],
                            "도로": T.road, f"기여 {name} dB": T[key].round(1), "거리 m": T.d_m.round(1),
                            "경로차 z m": T.z_m.round(3), "음원 X": T.sx.round(1), "음원 Y": T.sy.round(1)})
        self.m_paths.set(tab)
        R = PA.by_road(A, key)
        tot = PA.esum(A[key]) if len(A) else float("nan")
        mo = info.get("max_order", 1)
        rows = ([("점 수음점", self._path_name)] if self._path_name else []) + [("수음점 X, Y", f"{info['x']:.1f}, {info['y']:.1f}"), ("지면 위 높이", f"{info['h']:g} m"),
                (f"{name} 직접 ~ {mo}차 반사", f"{tot:.1f} dB"), ("경로 수", f"{len(A):,} (선은 상위 {len(T)}개)")]
        k = "L_day" if name == "LrT" else "L_night"
        rows += [(f"  {'직접음' if n == 0 else f'{n}차 반사'}", f"{PA.esum(A[k][A.order == n]):.1f} dB")
                 for n in range(0, mo + 1) if (A.order == n).any()]
        g = getattr(self, "_grid_now", None)
        if g is not None and name in g[0].columns and len(g[0]):
            Gh = g[0]
            r = Gh.iloc[int(np.argmin((Gh.x.values - info["x"]) ** 2 + (Gh.y.values - info["y"]) ** 2))]
            rows.append((f"가까운 격자점 {name}(전체)", f"{r[name]:.1f} dB"))
        rows.append(("— 도로별 기여", "dB · 비율"))
        for r in R.head(12).itertuples():
            rows.append((r.road, f"{r.L:.1f} · {r.share:.0f} %"))
        if mo < int(((self.R.get("run_info") or {}).get("reflection") or {}).get("max_order_grid", mo)):
            rows.append(("참고", f"{mo + 1}차 이상 반사 경로는 표·선에 없음"))
        if g is not None and "LrT_lateral" in g[0].columns:
            rows.append(("참고", "측면 회절 경로는 표·선에 없음 — 격자 성분 LrT_lateral(보기 조건 ▾)"))
        self._show_props(rows, f"경로 · {name} {tot:.1f} dB")
        if self.act_split.isChecked():                   # 오른쪽 창: 흰 바탕 확대 경로 보기
            sums = [(lab, PA.esum(A[k][m])) for lab, m in (("직접음", A.order == 0), ("반사음 1차", A.order == 1),
                                                         ("반사음 2차 이상", A.order >= 2)) if m.any()]
            who = self._path_name or f"X {info['x']:.0f}, Y {info['y']:.0f}"
            # 직접음·반사음을 따로 상위 PATH_TOP/2 개씩(한쪽이 크면 다른 쪽 선이 하나도 안 보이므로)
            Ts = PA.top_paths(pd.concat([PA.top_paths(A[A.order == 0], k, self.PATH_TOP // 2),
                                         PA.top_paths(A[A.order > 0], k, self.PATH_TOP // 2)]), k, self.PATH_TOP)
            nd = int((Ts.order == 0).sum())
            self.map3d.draw_paths(Ts, dict(info, name=self._path_name), k,
                                  f"경로 · {who} · 높이 {info['h']:g} m · {name} {tot:.1f} dB\n"
                                  f"선: 직접음 상위 {nd}개 · 반사음 상위 {len(Ts) - nd}개",
                                  self.R.get("buildings"), self.R.get("roads"), self.barriers, sums)
        self._draw_paths()
        self.lbl_status.setText(f"경로 {len(A):,}개 중 직접음·반사음 기여 상위를 오른쪽 창에 그림(노랑 = 직접음, 빨강 = 반사음 — "
                                "실선 1차·점선 2차 이상, 굵을수록 기여가 큼) — "
                                "표는 아래 ‘경로 (기여 상위)’ 탭, 도로별 기여도는 속성 창"
                                + ("" if self.act_split.isChecked() else " · 나란히 보기를 켜면 경로선이 보임"))

    def _draw_paths(self):
        """격자 지도 위 경로 보기 표시: 점 수음점(흰 원·이름), 고른 수음점(노란 원), 오른쪽 확대 범위(점선 사각형).
        경로선은 소음지도 위가 아니라 오른쪽 창(MapView.draw_paths)에 그림(2026-11-14)"""
        ax = self.map.ax
        for a in self._path_art:
            try:
                a.remove()
            except (ValueError, NotImplementedError, AttributeError):
                pass
        self._path_art = []
        if ax is None or self.view != "grid" or not self.act_path.isChecked():
            self.map.canvas.draw_idle()
            return
        xl, yl = ax.get_xlim(), ax.get_ylim()
        dpi = getattr(self.map.fig, "_original_dpi", None) or self.map.fig.dpi
        k = 72.0 / dpi
        P = self._point_receivers()
        if P is not None and len(P):
            self._path_art += ax.plot(P.x, P.y, "o", mfc="white", mec="#1f3a93", mew=1.4 * k, ms=8 * k, zorder=51,
                                      gid="points")
            for pid, x, y in zip(P.point_id, P.x, P.y):
                self._path_art.append(ax.annotate(str(pid), (x, y), xytext=(6, 4), textcoords="offset points",
                                                  fontsize=11 * k, weight="bold", color="#1f3a93", zorder=51,
                                                  clip_on=True))
        if self.paths is not None:
            info = self.paths[1]
            E = self.map3d.paths_extent if self.act_split.isChecked() else None
            if E is not None:
                x0, y0, x1, y1 = E
                self._path_art += ax.plot([x0, x1, x1, x0, x0], [y0, y0, y1, y1, y0], "--", color="#1f3a93",
                                          lw=1.6 * k, zorder=50, gid="paths_box")
            self._path_art += ax.plot([info["x"]], [info["y"]], "o", mfc="#ffd400", mec="#111111", mew=1.2 * k,
                                      ms=10 * k, zorder=52, gid="paths_rcv")
        ax.set_xlim(xl)
        ax.set_ylim(yl)
        self.map.canvas.draw_idle()

    # ------------------------------------------------------------ 방음벽 시뮬레이션·전후 차이
    def _barrier_panel(self):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(w)
        from sori.inputs import BARRIER_TYPES
        t = self.tbl_bar = QtWidgets.QTableWidget(0, 5)
        t.setHorizontalHeaderLabels(["이름", "높이 (m)", "반사 종류", "반사 손실 dB", "길이 (m)"])
        t.horizontalHeaderItem(2).setToolTip("반사형 = 건물 외벽과 같은 반사, 반사 저감형 = RLS-19 표 8 3 dB(RLS-90 표 7 −4 dB), "
                                             "강한 반사 저감형 = 5 dB(−8 dB), 반사 없음 = 반사면을 만들지 않음")
        t.horizontalHeaderItem(3).setToolTip("비우면 반사 종류의 표 값. 숫자를 넣으면 그 손실(dB)로 계산(제품 시험값 등)")
        t.setItemDelegateForColumn(2, ChoiceDelegate(list(BARRIER_TYPES.values()), t))
        t.horizontalHeader().setStretchLastSection(True)
        t.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        t.itemChanged.connect(self._bar_edited)
        t.itemSelectionChanged.connect(self._draw_barriers)
        lay.addWidget(t, 3)
        v = QtWidgets.QVBoxLayout()
        r1 = QtWidgets.QHBoxLayout()
        r1.addWidget(QtWidgets.QLabel("새 방음벽 높이"))
        self.sp_bar_h = QtWidgets.QDoubleSpinBox(minimum=0.5, maximum=30.0, singleStep=0.5, value=3.0, suffix=" m")
        r1.addWidget(self.sp_bar_h)
        self.cb_bar_type = QtWidgets.QComboBox()
        for key, lab in BARRIER_TYPES.items():
            self.cb_bar_type.addItem(lab, key)
        self.cb_bar_type.setToolTip(t.horizontalHeaderItem(2).toolTip())
        r1.addWidget(self.cb_bar_type)
        r1.addStretch(1)
        v.addLayout(r1)
        r2 = QtWidgets.QHBoxLayout()
        self.btn_bar_draw = QtWidgets.QPushButton("＋ 지도에 그리기")
        self.btn_bar_draw.setCheckable(True)
        self.btn_bar_draw.setToolTip("누른 뒤 격자 지도에서 꼭짓점을 차례로 클릭. 끝내려면 더블클릭·오른쪽 클릭 또는 이 단추를 다시 누름")
        self.btn_bar_draw.clicked.connect(self._toggle_bar_draw)
        r2.addWidget(self.btn_bar_draw)
        b = QtWidgets.QPushButton("선택한 방음벽 지우기")
        b.clicked.connect(self._del_barrier)
        r2.addWidget(b)
        r2.addStretch(1)
        v.addLayout(r2)
        r3 = QtWidgets.QHBoxLayout()
        self.btn_bar_run = QtWidgets.QPushButton("▶ 설치 후 계산·비교")
        self.btn_bar_run.setStyleSheet(f"QPushButton {{ font-weight: 700; color: white; background: {RED}; border: none; "
                                       f"border-radius: 6px; padding: 6px 16px; }} QPushButton:disabled {{ background: #e3e3e3; color: #a8a8a8; }}")
        self.btn_bar_run.clicked.connect(self.start_barrier_run)
        r3.addWidget(self.btn_bar_run)
        self.btn_bar_study = QtWidgets.QPushButton("높이별 비교…")
        self.btn_bar_study.setToolTip("그린 방음벽의 높이를 여러 값(예: 2, 3, 4, 5, 6 m)으로 바꿔 가며 계산하고, 설치 전 대비 저감량을 "
                                      "한 표로(‘방음벽 높이 비교’ 탭, CSV). 높이마다 한 번씩 계산하므로 시간이 높이 수만큼 걸림")
        self.btn_bar_study.clicked.connect(self.start_height_study)
        r3.addWidget(self.btn_bar_study)
        r3.addStretch(1)
        v.addLayout(r3)
        self.lbl_bar = QtWidgets.QLabel(
            "설치 전 결과(지금 결과 폴더)는 그대로 두고, 옆의 ‘…_방음벽’ 폴더에 설치 후를 계산해 차이 지도를 보여 줍니다. "
            "계산 대상·전파 방법은 ‘계산 실행’ 탭 값. 방음벽은 설정 폴더의 barriers.csv 에 저장됩니다.")
        self.lbl_bar.setWordWrap(True)
        self.lbl_bar.setStyleSheet("color: #666666; font-size: 11px;")
        v.addWidget(self.lbl_bar)
        v.addStretch(1)
        lay.addLayout(v, 2)
        return w

    def show_barrier_panel(self):
        if self.stack.currentIndex() != 1:
            self.enter_task("grid")
        self._set_dock("계산·결과 표", True)
        self.tabs.setCurrentIndex(self.tabs.count() - 1)
        self.lbl_status.setText("방음벽 — ‘＋ 지도에 그리기’를 누르고 지도에서 꼭짓점을 클릭, 높이를 넣고 ‘▶ 설치 후 계산·비교’")

    def _bar_path(self):
        """방음벽 파일: 설정에 input.barriers.path 가 있으면 그 파일, 없으면 설정 폴더의 barriers.csv"""
        if self.cfg is None:
            return None
        p = (self.cfg["input"].get("barriers") or {}).get("path")
        return Path(p) if p else Path(self.cfg_path).parent / "barriers.csv"

    def _load_barriers(self):
        from sori import inputs as I
        self.barriers, self._bar_draw = [], None
        p = self._bar_path()
        if p is not None and p.is_file():
            try:
                self.barriers = I.load_barriers({"input": {"barriers": {"path": str(p)}}})
            except Exception as e:
                log.error("방음벽 파일을 읽지 못함(%s): %s", p, e)
        self._fill_bar_table()

    def _save_barriers(self):
        from sori import inputs as I
        p = self._bar_path()
        if p is None:
            return None
        I.save_barriers(p, self.barriers)
        return p

    @staticmethod
    def _bar_len(b):
        return sum(math.hypot(x2 - x1, y2 - y1) for (x1, y1), (x2, y2) in zip(b["pts"][:-1], b["pts"][1:]))

    def _fill_bar_table(self):
        t = self.tbl_bar
        t.blockSignals(True)
        t.setRowCount(len(self.barriers))
        for i, b in enumerate(self.barriers):
            t.setItem(i, 0, QtWidgets.QTableWidgetItem(b["id"]))
            t.setItem(i, 1, QtWidgets.QTableWidgetItem(f"{b['h']:g}"))
            from sori.inputs import BARRIER_TYPES
            t.setItem(i, 2, QtWidgets.QTableWidgetItem(BARRIER_TYPES.get(b.get("kind", "reflect"), "반사형")))
            t.setItem(i, 3, QtWidgets.QTableWidgetItem("" if b.get("loss") is None else f"{b['loss']:g}"))
            L = QtWidgets.QTableWidgetItem(f"{self._bar_len(b):.1f}")
            L.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            t.setItem(i, 4, L)
        t.blockSignals(False)
        self.btn_bar_run.setEnabled(bool(self.barriers))
        if hasattr(self, "btn_bar_study"):
            self.btn_bar_study.setEnabled(bool(self.barriers))

    def _bar_edited(self, item):
        """표에서 이름·높이·반사 종류·반사 손실을 고침 → 목록·파일에 반영(잘못된 값은 되돌림)"""
        i, c = item.row(), item.column()
        if not 0 <= i < len(self.barriers):
            return
        b = self.barriers[i]
        if c == 0:
            name = item.text().strip()
            if name and name not in [x["id"] for k, x in enumerate(self.barriers) if k != i]:
                b["id"] = name
        elif c == 1:
            try:
                h = float(item.text())
                if 0.0 < h <= 30.0:
                    b["h"] = h
            except ValueError:
                pass
        elif c == 2:
            from sori.inputs import BARRIER_TYPES
            k = {v: kk for kk, v in BARRIER_TYPES.items()}.get(item.text().strip())
            if k:
                b["kind"] = k
        elif c == 3:
            txt = item.text().strip()
            try:
                v = None if not txt else float(txt)
                if v is None or 0.0 <= v <= 30.0:
                    b["loss"] = v
            except ValueError:
                pass
        self._fill_bar_table()
        self._save_barriers()
        self._draw_barriers()

    def _toggle_bar_draw(self, on=None):
        if on is None:
            on = self.btn_bar_draw.isChecked()
        if not on:
            return self._finish_barrier()
        if self.cfg is None:
            self.btn_bar_draw.setChecked(False)
            return self._err("설정 파일(config.yaml)을 먼저 여세요.")
        if self.stack.currentIndex() != 1 or self.view != "grid":
            self.enter_task("grid")
            self._set_dock("계산·결과 표", True)
            self.tabs.setCurrentIndex(self.tabs.count() - 1)
        if self.act_sec.isChecked():
            self.act_sec.setChecked(False)
            self.sec_pts = []
        self._bar_draw = []
        self.btn_bar_draw.setChecked(True)
        self.btn_bar_draw.setText("그리기 끝")
        self.lbl_status.setText("방음벽 그리기 — 지도에서 꼭짓점을 차례로 클릭, 끝내려면 더블클릭·오른쪽 클릭 또는 ‘그리기 끝’")

    def _finish_barrier(self):
        """그리기를 끝냄: 꼭짓점이 2개 이상이면 방음벽으로 넣고 저장"""
        if self._bar_draw is None:
            return
        pts, self._bar_draw = self._bar_draw, None
        self.btn_bar_draw.setChecked(False)
        self.btn_bar_draw.setText("＋ 지도에 그리기")
        if len(pts) >= 2:
            ids = {b["id"] for b in self.barriers}
            k = len(self.barriers) + 1
            while f"W{k}" in ids:
                k += 1
            self.barriers.append(dict(id=f"W{k}", h=float(self.sp_bar_h.value()),
                                      kind=self.cb_bar_type.currentData(), loss=None, pts=pts, note=""))
            self._fill_bar_table()
            p = self._save_barriers()
            b = self.barriers[-1]
            self.lbl_status.setText(f"방음벽 {b['id']}: 길이 {self._bar_len(b):.1f} m, 높이 {b['h']:g} m — 저장함({p.name}). "
                                    "‘▶ 설치 후 계산·비교’로 효과 확인")
        else:
            self.lbl_status.setText("방음벽 그리기 취소(꼭짓점 2개 미만)")
        self._draw_barriers()

    def _del_barrier(self):
        rows = sorted({i.row() for i in self.tbl_bar.selectedIndexes()}, reverse=True)
        if not rows:
            return
        for i in rows:
            del self.barriers[i]
        self._fill_bar_table()
        self._save_barriers()
        self._draw_barriers()

    def _draw_barriers(self):
        """격자 지도 위 방음벽(자주색 굵은 선 + 이름·높이), 그리는 중인 선(점선). 이 요소만 지우고 다시 그림"""
        ax = self.map.ax
        for a in self._bar_art:
            try:
                a.remove()
            except (ValueError, NotImplementedError, AttributeError):
                pass
        self._bar_art = []
        if ax is None or self.view != "grid":
            return
        sel = {i.row() for i in self.tbl_bar.selectedIndexes()}
        xl, yl = ax.get_xlim(), ax.get_ylim()
        dpi = getattr(self.map.fig, "_original_dpi", None) or self.map.fig.dpi
        k = 72.0 / dpi                                   # 화면 점 기준 굵기(보고서 틀은 dpi 를 낮춰 그림)
        for i, b in enumerate(self.barriers):
            xs, ys = zip(*b["pts"])
            self._bar_art += ax.plot(xs, ys, "-", color="#111111", lw=(6.5 if i in sel else 5.0) * k, zorder=45,
                                     solid_capstyle="round")
            self._bar_art += ax.plot(xs, ys, "-", color="#ffd400" if i in sel else "#c2187a", lw=3.0 * k, zorder=46,
                                     solid_capstyle="round")
            self._bar_art.append(ax.annotate(f"{b['id']} {b['h']:g} m", (xs[0], ys[0]), xytext=(5, 5),
                                             textcoords="offset points", fontsize=12 * k, weight="bold",
                                             color="#7a0a4e", zorder=47,
                                             bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="#c2187a", lw=0.6)))
        if self._bar_draw:
            xs, ys = zip(*self._bar_draw)
            self._bar_art += ax.plot(xs, ys, "o--", color="#c2187a", lw=2.4 * k, ms=6 * k, mfc="white", zorder=48)
        ax.set_xlim(xl)
        ax.set_ylim(yl)
        self.map.canvas.draw_idle()

    def _barrier_base(self):
        """설치 전(기준) 결과 폴더: 지금 결과가 방음벽 없는 격자 결과면 그 폴더, 방음벽 결과를 보고 있으면 그 기준"""
        bi = self.R.get("build_info") or {}
        if self.out and "grid" in self.R and not bi.get("barriers"):
            return Path(self.out)
        return self.diff_base

    def start_barrier_run(self):
        if not self.cfg_path:
            return self._err("설정 파일(config.yaml)을 먼저 여세요.")
        if self._bar_draw is not None:
            self._finish_barrier()
        if not self.barriers:
            return self._err("방음벽이 없습니다. ‘＋ 지도에 그리기’로 먼저 그리세요.")
        base = self._barrier_base()
        if base is None:
            return self._err("설치 전 결과가 없습니다.\n‘계산 실행’ 탭에서 방음벽 없는 현황을 먼저 계산하세요(격자 포함).")
        if (self.cfg["input"].get("barriers") or {}).get("path"):
            log.warning("설정 파일에 input.barriers.path 가 있음 — ‘계산 실행’(설치 전)에도 방음벽이 들어감. "
                        "전후 비교를 하려면 설정에서 그 줄을 지우세요")
        bars = self._save_barriers()
        out = str(base.with_name(base.name + "_방음벽"))
        which = [k for k, c in self.chk.items() if c.isChecked()]
        if "grid" not in which:
            which = ["grid"] + which
        ow = overwrite_list(out, which)
        if ow:
            c = self._overwrite_choice(out, ow)
            if c == "cancel":
                return
            if c == "new":
                out = str(fresh_out_dir(out))
        try:                                             # 설치 전과 같은 격자 높이로(차이를 점마다 맞추려고)
            xh = [float(h) for h in pd.read_csv(base / "grid_result.csv", usecols=["h"]).h.unique()]
        except (OSError, ValueError):
            xh = None
        self._set_dock("계산·결과 표", True)
        self.tabs.setCurrentIndex(1)
        log.info("방음벽 %d개 설치 후 계산 — 기준(설치 전): %s", len(self.barriers), base)
        self._pending_out = out
        self._launch(Worker(self.bridge, "run", cfg=str(self.cfg_path), out=out, which=which,
                            plots=self.chk_plots.isChecked(), method=self.cb_method.currentData(),
                            stages=self._stages(which, xh), extra_heights=xh, barriers=str(bars),
                            compare_base=str(base), scenario="방음벽"),
                     "방음벽 설치 후 계산 준비 중(모델 만들기)… 창을 닫지 마세요", busy=True)
        self._pending_out = out

    def start_height_study(self, heights=None):
        """방음벽 높이별 비교(sori_gui.barrier_height_study). heights 를 주지 않으면 물어봄"""
        if not self.cfg_path:
            return self._err("설정 파일(config.yaml)을 먼저 여세요.")
        if not self.barriers:
            return self._err("방음벽이 없습니다. ‘＋ 지도에 그리기’로 먼저 그리세요.")
        base = self._barrier_base()
        if base is None:
            return self._err("설치 전 결과가 없습니다.\n‘계산 실행’ 탭에서 방음벽 없는 현황을 먼저 계산하세요(격자 포함).")
        if heights is None:
            text, ok = QtWidgets.QInputDialog.getText(self, "방음벽 높이별 비교",
                                                      "비교할 높이(m, 쉼표로) — 모든 방음벽을 각 높이로 바꿔 계산", text="2, 3, 4, 5, 6")
            if not ok:
                return
            try:
                heights = sorted({float(t) for t in text.replace(" ", "").split(",") if t})
            except ValueError:
                return self._err("높이는 숫자를 쉼표로 구분해 넣으세요.")
        if not heights or any(not 0.0 < h <= 30.0 for h in heights):
            return self._err("높이는 0 초과 30 m 이하")
        which = [k for k in ("grid", "facade") if self.chk[k].isChecked()] or ["grid"]
        if "grid" not in which:
            which = ["grid"] + which
        self._set_dock("계산·결과 표", True)
        self.tabs.setCurrentIndex(1)
        log.info("방음벽 높이별 비교 %s m — 기준(설치 전): %s", ", ".join(f"{h:g}" for h in heights), base)
        self._study_base = base
        self._launch(Worker(self.bridge, "study", cfg=str(self.cfg_path), base=str(base), barriers=list(self.barriers),
                            heights=list(heights), which=which, method=self.cb_method.currentData()),
                     f"방음벽 높이별 비교 준비 중({len(heights)}개 높이)… 창을 닫지 마세요", busy=True)

    def _show_study(self, T):
        cols = {"height_m": "높이 m", "length_m": "길이 m", "area_m2": "면적 m²"}
        hk = sorted({c.split("_")[1] for c in T.columns if c.startswith("grid_h")})
        for h in hk:
            cols.update({f"grid_{h}_mean": f"격자 {h[1:]} m 평균 Δ dB", f"grid_{h}_min": f"격자 {h[1:]} m 최대 저감 dB",
                         f"grid_{h}_lower_3dB": f"격자 {h[1:]} m 3 dB↓ 점"})
        cols.update({"facade_mean": "벽면 평균 Δ dB", "facade_lower_3dB": "벽면 3 dB↓ 점",
                     "facade_bld_lower_3dB": "3 dB↓ 건물 수", "out": "결과 폴더"})
        V = T[[c for c in cols if c in T.columns]].rename(columns=cols)
        self.m_study.set(V)
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i) == "방음벽 높이 비교":
                self.tabs.setCurrentIndex(i)
        rows = [("기준(설치 전)", str(getattr(self, "_study_base", "")))]
        g = next((c for c in T.columns if c.startswith("grid_h") and c.endswith("_mean")), None)
        for r in T.itertuples():
            v = getattr(r, g) if g else None
            rows.append((f"{r.height_m:g} m (면적 {r.area_m2:g} m²)", "" if v is None else f"격자 평균 {v:+.2f} dB"))
        self._show_props(rows, "방음벽 높이별 비교")
        self.lbl_status.setText(f"방음벽 높이별 비교 완료({len(T)}개 높이) — 아래 ‘방음벽 높이 비교’ 탭, "
                                "높이별 결과 폴더는 ‘보기 ▸ 비교 기준 결과 폴더 고르기’ 등으로 열 수 있음")

    def pick_diff_base(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "비교 기준(대책 전) 결과 폴더", str(Path(self.out or Path.home()).parent))
        if d:
            self.set_diff_base(d)

    def set_diff_base(self, d):
        d = Path(d)
        if not (d / "grid_result.csv").is_file():
            return self._err(f"이 폴더에 격자 결과(grid_result.csv)가 없습니다.\n{d}")
        if "grid" not in self.R:
            return self._err("지금 연 결과에 격자 결과가 없습니다.")
        self.diff_base, self._diff = d, None
        self.act_diff.setEnabled(True)
        self.act_diff.setChecked(True)
        self.show_grid()
        self._show_props(self._diff_props(), "전후 차이")

    def _toggle_diff(self):
        self.show_grid()
        if self.act_diff.isChecked():
            self._show_props(self._diff_props(), "전후 차이")

    def _diff_table(self):
        """차이 표(지금 결과 − 기준). 겹치는 격자점이 없으면 None"""
        if self.diff_base is None or not self.out:
            return None
        if self._diff is None or self._diff[:2] != (self.diff_base, Path(self.out)):
            from sori import compare
            try:
                D = compare.diff_grid(self.diff_base, self.out)
            except Exception as e:
                log.warning("차이 표를 만들지 못함: %s", e)
                D = None
            self._diff = (self.diff_base, Path(self.out), D if D is not None and len(D) else None)
        return self._diff[2]

    def _draw_diff(self, h, key):
        """차이 지도를 그림. 그리지 못하면 False(보통 격자 지도로)"""
        D, C = self._diff_table(), self._hq_ctx()
        if D is None or C is None:
            self.lbl_status.setText("차이 지도를 그릴 수 없음 — 기준과 겹치는 격자점이 없거나 설정 파일이 열려 있지 않음")
            return False
        Dh = D[np.isclose(D.h, h)]
        if not len(Dh) or "d" + key not in Dh:
            self.lbl_status.setText(f"기준 결과에 높이 {h:g} m 격자가 없어 차이를 그릴 수 없음")
            return False
        from sori import compare
        s = compare.stats(Dh, key)
        try:
            self.map.draw_diff(C, C.cfg, D, key, h, lambda: (self._redraw_grid(), self._grid_extras()),
                               f"전후 차이(지금 − 기준) · {KEYS[key]} · 높이 {h:g} m · 평균 {s['mean']:+.2f} dB · "
                               f"최대 저감 {s['min']:+.1f} dB")
        except Exception as e:
            log.warning("차이 지도 그리기 실패: %s", e)
            return False
        self._grid_now = (Dh, key)
        return True

    def _diff_props(self):
        from sori import compare
        D = self._diff_table()
        rows = [("기준(대책 전)", str(self.diff_base)), ("비교(지금)", str(self.out)), ("정의", "Δ = 지금 − 기준 (음수 = 낮아짐)")]
        if D is None:
            return rows + [("차이", "겹치는 격자점 없음")]
        for h, g in D.groupby("h"):
            for k in ("LrT", "LrN"):
                if "d" + k not in g:
                    continue
                s = compare.stats(g, k)
                rows += [(f"h {h:g} m Δ{k} 평균", f"{s['mean']:+.2f} dB"), (f"h {h:g} m Δ{k} 범위", f"{s['min']:+.1f} ~ {s['max']:+.1f} dB"),
                         (f"h {h:g} m {k} 3 dB↓ 점", f"{s['lower_3dB']:,} / {s['n']:,}"),
                         (f"h {h:g} m {k} 1 dB↑ 점", f"{s['higher_1dB']:,}")]
        B = self.R.get("build_info", {}).get("barriers")
        if B:
            rows.append(("방음벽", f"{B['count']}개 · {B['length_m']:g} m · 높이 {B['height_m'][0]:g}~{B['height_m'][1]:g} m"))
        return rows

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
        fast = self.chk_fast.isChecked()
        if fast:                                          # 미리보기는 설정값 결과와 섞이지 않게 옆 폴더에(물어보지 않고 덮어씀)
            out = str(Path(out).with_name(Path(out).name + "_미리보기"))
            log.info("빠른 미리보기(격자 간격 2배·반사 1차) — 결과 폴더: %s", out)
        ow = [] if fast else overwrite_list(out, which)   # 이번 계산 대상의 결과가 이미 있을 때만 물음
        if ow:
            c = self._overwrite_choice(out, ow)
            if c == "cancel":
                return
            if c == "new":
                out = str(fresh_out_dir(out))
                self.ed_out.setText(out)
                log.info("이전 결과를 남기고 새 폴더에 계산: %s", out)
        if not fast:
            G.save_settings(last_config=self.cfg_path, last_out=Path(out).resolve())
        self._set_dock("계산·결과 표", True)   # 기록을 볼 수 있게 아래 창을 폄
        self.tabs.setCurrentIndex(1)
        xh = self._extra_heights()
        self._pending_out = out
        self._launch(Worker(self.bridge, "run", cfg=str(self.cfg_path), out=out, which=which,
                            plots=self.chk_plots.isChecked(), method=self.cb_method.currentData(),
                            stages=self._stages(which, xh), extra_heights=xh, preset="fast" if fast else None),
                     "계산 준비 중(모델 만들기)… 창을 닫지 마세요", busy=True)
        self._pending_out = out

    def _floor_rule(self):
        c = self.cfg or {}
        return float(((c.get("receivers") or {}).get("facade") or {}).get("z_in_floor", 2.8)), \
            float((c.get("model") or {}).get("floor_height", 3.0))

    def _extra_heights(self):
        """이번 계산에만 더하는 격자 높이: 고시 1.5 m, 층별 격자"""
        from sori.summary import floor_heights
        xh = [1.5] if self.chk_h15.isChecked() else []
        xh += floor_heights(self.sp_floors.value(), *self._floor_rule())
        return sorted(set(xh)) or None

    def _height_label(self, h):
        """높이 목록 글: 층별 격자 높이면 몇 층인지, 1.5 m 면 고시 높이"""
        zf, fh = self._floor_rule()
        k = (h - zf) / fh + 1
        if abs(k - round(k)) < 1e-6 and k >= 1:
            return f"{h:g} m ({int(round(k))}층)"
        return f"{h:g} m (고시)" if abs(h - 1.5) < 1e-9 else f"{h:g} m"

    def _stages(self, which, extra_heights=None):
        """진행률 단계 수(이번 계산에만 더하는 격자 높이 포함)"""
        import copy
        cfg = copy.deepcopy(dict(self.cfg))
        if extra_heights:
            hs = cfg["receivers"]["grid"]["heights"]
            cfg["receivers"]["grid"]["heights"] = sorted({round(float(h), 3) for h in list(hs) + list(extra_heights)})
        return count_stages(cfg, which)

    def _overwrite_choice(self, out, ow):
        """이미 있는 결과를 어떻게 할지 물음 → 'overwrite' | 'new'(옆에 날짜 붙인 새 폴더) | 'cancel'"""
        prev = ""
        try:
            rc = json.loads((Path(out) / "run_conditions.json").read_text(encoding="utf-8"))
            m = rc.get("propagation_method")
            prev = f"이전 계산: {str(rc.get('started', '')).replace('T', ' ')}" + (f" · {m}" if m else "") + "\n"
        except (OSError, ValueError):
            pass
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Warning)
        box.setWindowTitle("SORI — 이전 결과가 있음")
        box.setText("이 결과 폴더에 이번에 계산할 대상의 결과가 이미 있습니다.")
        box.setInformativeText(f"{out}\n{prev}\n" + "\n".join(f"· {t}: {fn} ({ts})" for t, fn, ts in ow)
                               + "\n\n덮어쓰면 위 결과와 그 그림·사본이 새 계산으로 바뀌고 되돌릴 수 없습니다.\n"
                                 f"‘새 폴더에 저장’은 이전 결과를 그대로 두고 {fresh_out_dir(out).name} 에 계산합니다.")
        b_new = box.addButton("새 폴더에 저장", QtWidgets.QMessageBox.ActionRole)
        b_ow = box.addButton("덮어쓰기", QtWidgets.QMessageBox.DestructiveRole)
        box.addButton("취소", QtWidgets.QMessageBox.RejectRole)
        box.setDefaultButton(b_new)
        box.exec()
        return "new" if box.clickedButton() is b_new else "overwrite" if box.clickedButton() is b_ow else "cancel"

    def start_check(self):
        self._set_dock("계산·결과 표", True)
        self.tabs.setCurrentIndex(1)
        self._launch(Worker(self.bridge, "check"), "자가 점검 중… (좌표 변환 + 기준 사례 계산, 1~2분)", busy=True)

    def _launch(self, w, text, busy=False):
        """busy: 진행률을 아직 모름 → 움직이는 막대(첫 진행률이 오면 % 로 바뀜)"""
        import time
        self._busy(True)
        self.bar.setMaximum(0 if busy else 1000)
        self.bar.setValue(0)
        self.bar.show()
        self._t0 = time.monotonic()
        self._tick()
        self.timer.start()
        self.lbl_status.setText(text)
        self.worker = w
        w.start()

    def _tick(self):
        import time
        self.lbl_time.setText(f"경과 {fmt_dur(time.monotonic() - self._t0)}")

    def _stop_clock(self, word):
        self.timer.stop()
        self.lbl_time.setText(self.lbl_time.text().replace("경과", word))

    def _busy(self, on):
        for x in (self.btn_run, self.btn_check, self.act_run, self.act_check):
            x.setEnabled(not on)
        self.btn_bar_run.setEnabled(not on and bool(self.barriers))
        self.btn_bar_study.setEnabled(not on and bool(self.barriers))

    def _on_log(self, level, text):
        self.logbox.appendPlainText(("⚠ " if level >= logging.WARNING else "") + text)

    def _on_prog(self, frac, text):
        if self.bar.maximum() == 0:
            self.bar.setMaximum(1000)
        self.bar.setValue(int(frac * 1000))
        self.lbl_status.setText(text)

    def _on_done(self, kind, r):
        self._busy(False)
        self.bar.setMaximum(1000)
        self.bar.setValue(1000)
        self._stop_clock("걸린 시간")
        if kind == "section":
            D, info, fn = r
            self.R["section"] = (D, info)
            self.act_last_sec.setEnabled(True)
            import time
            if getattr(self, "_sec_t0", None) and info.get("n_calc"):
                self._sec_rate = info["n_calc"] / max(time.time() - self._sec_t0, 0.1)
            self.show_section()
            log.info("단면 결과 저장: %s", fn)
        elif kind == "paths":
            self.paths = r
            self._section_side = False
            if not self.act_split.isChecked():           # 경로선은 오른쪽 창에 → 나란히 보기를 켬(다시 그리며 경로도 그림)
                self._split_auto = True
                self.act_split.setChecked(True)
                self._toggle_split(True)
            else:
                self._fit_split()
                self._show_paths()
        elif kind == "study":
            self._show_study(r)
        elif kind == "viewer3d":
            self._open_3d(Path(r))
        elif kind == "run":
            self.lbl_status.setText(f"계산 완료 ({fmt_dur(r['elapsed_s'])}). 결과: {self._pending_out}"
                                    " — 도구 줄 ‘결과 폴더’로 열기, ‘내보내기…’로 ZIP 묶기")
            self.open_results(self._pending_out)
            if r.get("compare_base") and self.act_diff.isEnabled():   # 방음벽 계산: 차이 지도와 요약을 바로 보여 줌
                self.act_diff.setChecked(True)
                self.show_grid()
                self._show_props(self._diff_props(), "방음벽 설치 전후 차이")
                self.lbl_status.setText(f"방음벽 계산 완료 ({fmt_dur(r['elapsed_s'])}) — 차이 지도(지금 − 설치 전). "
                                        "‘전후 차이’를 끄면 설치 후 소음지도")
        else:
            self.lbl_status.setText(f"자가 점검 통과 — 기준 사례 최대 차이 {r['max_diff_dB']:.2g} dB "
                                    f"(허용 0.01 dB, 프로젝트 임의 설정). 보고서: {r['report_path']}")

    def _on_fail(self, text):
        self._busy(False)
        self.bar.setMaximum(1000)
        self.bar.setValue(0)
        self.bar.hide()
        self._stop_clock("멈춤 —")
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


def light_theme(app):
    """운영체제가 다크 모드여도 흰 바탕 밝은 화면으로 고정(지도·표가 흰 그림과 어울리게)"""
    try:
        app.styleHints().setColorScheme(Qt.ColorScheme.Light)       # Qt 6.8 이상
    except AttributeError:
        pass
    app.setStyle("Fusion")
    P, R = QtGui.QPalette, QtGui.QPalette.ColorRole
    p = P()
    for role, c in ((R.Window, "#ffffff"), (R.WindowText, "#1a1a1a"), (R.Base, "#ffffff"),
                    (R.AlternateBase, "#f4f6f8"), (R.Text, "#1a1a1a"), (R.Button, "#f2f2f2"),
                    (R.ButtonText, "#1a1a1a"), (R.ToolTipBase, "#ffffe0"), (R.ToolTipText, "#1a1a1a"),
                    (R.Highlight, "#2f6fd6"), (R.HighlightedText, "#ffffff"), (R.PlaceholderText, "#8a8a8a"),
                    (R.Mid, "#c8c8c8"), (R.Midlight, "#e6e6e6"), (R.Dark, "#a0a0a0"), (R.Light, "#ffffff")):
        p.setColor(role, QtGui.QColor(c))
    for role in (R.WindowText, R.Text, R.ButtonText):
        p.setColor(P.ColorGroup.Disabled, role, QtGui.QColor("#a8a8a8"))
    app.setPalette(p)


def main(config=None, out=None, snapshot=None):
    if HAS_WEB and QtWidgets.QApplication.instance() is None:
        QtCore.QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)      # Qt WebEngine 요구 사항
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
    app.setApplicationName("SORI")
    light_theme(app)
    from sori.maps_hq import set_palette
    set_palette("simple")                             # 앱 화면·앱에서 저장하는 그림은 7색(보고서 그림 CLI 는 DIN 11색 그대로)
    icon = resource_path("sori", "assets", "SORI.png")    # 창·Dock·작업 표시줄 아이콘(앱 파일 아이콘은 SORI.spec)
    if icon.is_file():
        app.setWindowIcon(QtGui.QIcon(str(icon)))
    win = MainWindow()
    if not snapshot:
        win.show_splash()                             # 포스터 → 클릭하면 홈
    win.show()

    def _hook(et, ev, tb):                            # 지도 마우스 조작 중 오류: 앱을 끄지 않고 기록·상태 줄에 남김
        import traceback
        sys.__stderr__.write("".join(traceback.format_exception(et, ev, tb)))
        log.error("화면 처리 중 오류(앱은 계속 사용 가능): %s", ev)
        win.lbl_status.setText(f"화면 오류: {ev} — 지도를 다시 그리려면 시간대를 바꾸거나 건물을 다시 클릭")
    sys.excepthook = _hook
    restore(win, config, out)
    if snapshot:                                      # 화면 확인용: 그린 뒤 PNG 저장하고 끝냄
        import os
        if os.environ.get("SORI_SNAPSHOT_3D") and "grid" in win.R:   # 앱 안 3D 보기 확인(웹 화면은 그려질 때까지 기다림)
            win.enter_task("grid")
            win.show_3d_view(sync=True)
            loop = QtCore.QEventLoop()
            QtCore.QTimer.singleShot(int(os.environ.get("SORI_SNAPSHOT_WAIT_MS", "12000")), loop.quit)
            loop.exec()
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
