# -*- coding: utf-8 -*-
"""
평지 샌드박스 — 평평한 지면(z = 0) 위에 음원·방음벽·수음점을 직접 놓고 회절 원리를 확인하는 계산 모듈.
과제 「방음벽 회절원리 검토」(점음원 → 선음원 → RLS-19 도로 → ray tracing) 용.

SORI 본 계산과 **같은 함수**를 그대로 씀(결과 차이는 지형·건물이 없는 것뿐):
  - 경로 기하(가시선·고무줄 회절 모서리·z·A·B·e·h_m): geometry.path_geometry_b — 방음벽은 두께 없는 선분
  - 감쇠: ISO 9613-2(iso9613.atten_iso: A_div·A_atm·A_gr·D_z·A_bar) 또는 RLS-19 3.5(rls19_propagation.atten_rls19)
  - 반사 채택: solver._accept(ISO 식 19 / RLS-19 3.6 높이 조건), 대역 합: solver._band_factor
  - 측면 회절: lateral.lateral_factor(ISO 식 13, ISO 방법만) — solver.compute 와 같은 상한 처리(TR 17534-3 5.6)
  - RLS-19 도로 음원 LW′: rls19.LW_prime(식 4~9)
지면은 1칸짜리 래스터(높이 0, 지면계수 G 한 값)로 표현함.

음원 종류
  point : 점음원, Lw [dB(A)] (ISO: 자유공간 음향파워. A_div = 20 lg d + 11)
  line  : 선음원, Lw′ [dB(A)/m] — 길이 seg_len 이하 조각(점음원)으로 나눔, 조각 LW = Lw′ + 10 lg l
  road  : RLS-19 도로 — 교통량에서 LW′(식 4), 음원 높이 0.5 m, 음원선 1개(중심) 또는 2개(± offset, 교통량 반씩)
반사: 방음벽 양면의 1차 반사(허상음원). 흡음형(absorbing) 방음벽은 반사 없음.
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import json
import math

import numpy as np

from . import njit, prange
from . import lateral as LAT
from .geometry import (NGEOM, G_D, G_SGR, G_HM, G_SHIELD, G_DOUBLE, G_Z, G_DSS, G_DSR, G_E, MAXO, MAXV,
                       path_geometry_b)
from .iso9613 import C_SOUND, alpha_iso9613_1, atten_iso, terms_iso, TERM_NAMES as ISO_TERMS
from .rls19 import LW_prime
from .rls19_propagation import terms_rls19, TERM_NAMES as RLS_TERMS
from .solver import HULL_N, METH_ISO, METH_RLS19, _accept, _band_factor, _path

OCTAVES = (63.0, 125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0)
# 2D 래스터 대신 1칸 래스터: _cell(x0, y0, res, 1, 1, x, y) 가 |x|,|y| < 1e6 m 에서 늘 0
_X0, _Y0, _RES = -1.0e6, 1.0e6, 1.0e7
COMP = ("direct", "reflection", "lateral")


# ====================================================================== 기본 장면
def default_scene(kind="point"):
    """과제 단계별 예제 장면. kind: point | line | road | empty"""
    sc = {
        "method": "iso9613_2",          # iso9613_2 | rls19
        "spectrum": "A500",             # A500(A 가중 단일값, 500 Hz 감쇠항) | octave
        "spectrum_dBA": [-20.0, -14.0, -10.0, -7.0, -4.0, -6.0, -11.0, -18.0],   # 옥타브 계산용 상대 A 가중 레벨(가정값, 화면에서 바꿈)
        "ground_G": 0.0,                # 0 = 단단한 지면, 1 = 다공질 지면
        "ground_alt": False,            # ISO 지면 감쇠 대안법(식 10) 사용
        "temperature_C": 15.0, "humidity_pct": 70.0,
        "C2": 20.0, "dz_max_single": 20.0, "dz_max_double": 25.0,
        "reflection": True, "refl_loss_dB": 1.0,
        "lateral": True, "lateral_max_grow": 3,
        "seg_len": 1.0,                 # 선음원·도로 조각 최대 길이 [m]
        "step": 1.0,                    # 단면 표본 간격 [m] (평지라 결과에 거의 영향 없음)
        "sources": [], "barriers": [], "receivers": [],
        "grid": {"x0": -20.0, "y0": -30.0, "x1": 80.0, "y1": 30.0, "res": 1.0, "h": 4.0},
    }
    if kind == "point":
        sc["sources"] = [{"type": "point", "name": "S1", "x": 0.0, "y": 0.0, "h": 0.5, "Lw": 100.0}]
        sc["barriers"] = [{"name": "W1", "x1": 10.0, "y1": -15.0, "x2": 10.0, "y2": 15.0, "h": 4.0,
                           "absorbing": False}]
        sc["receivers"] = [{"name": "R1", "x": 30.0, "y": 0.0, "h": 1.5}]
    elif kind == "line":
        sc["sources"] = [{"type": "line", "name": "L1", "x1": 0.0, "y1": -50.0, "x2": 0.0, "y2": 50.0,
                          "h": 0.5, "Lw_per_m": 80.0}]
        sc["barriers"] = [{"name": "W1", "x1": 10.0, "y1": -40.0, "x2": 10.0, "y2": 40.0, "h": 4.0,
                           "absorbing": False}]
        sc["receivers"] = [{"name": "R1", "x": 30.0, "y": 0.0, "h": 1.5}]
        sc["grid"].update({"y0": -50.0, "y1": 50.0})
    elif kind == "road":
        sc["method"] = "rls19"
        sc["sources"] = [{"type": "road", "name": "도로1", "x1": 0.0, "y1": -100.0, "x2": 0.0, "y2": 100.0,
                          "M": 1500.0, "p1": 5.0, "p2": 5.0, "v_pkw": 60.0, "v_lkw": 60.0,
                          "surface": "ref", "g": 0.0, "n_lines": 2, "offset": 5.25}]
        sc["barriers"] = [{"name": "W1", "x1": 12.0, "y1": -60.0, "x2": 12.0, "y2": 60.0, "h": 4.0,
                           "absorbing": False}]
        sc["receivers"] = [{"name": "R1", "x": 30.0, "y": 0.0, "h": 4.0},
                           {"name": "R2", "x": 50.0, "y": 0.0, "h": 4.0}]
        sc["grid"].update({"y0": -60.0, "y1": 60.0, "x1": 100.0})
    return sc


def load_scene(path):
    with open(path, encoding="utf-8") as f:
        sc = json.load(f)
    base = default_scene("empty")
    base.update(sc)
    return base


def save_scene(sc, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sc, f, ensure_ascii=False, indent=2)


# ====================================================================== 입력 → 배열
def road_lw_prime(src):
    """RLS-19 식 (4) 도로 전체 LW′ [dB/m] (종단경사 g %, 교차로·반사 보정 0)"""
    return LW_prime(float(src["M"]), float(src["p1"]), float(src["p2"]), float(src["v_pkw"]),
                    float(src["v_lkw"]), src.get("surface", "ref"), float(src.get("g", 0.0)))


def _split(x1, y1, x2, y2, seg):
    L = math.hypot(x2 - x1, y2 - y1)
    n = max(1, int(math.ceil(L / max(seg, 1e-3))))
    t = (np.arange(n) + 0.5) / n
    return x1 + t * (x2 - x1), y1 + t * (y2 - y1), L / n


def expand_sources(sc):
    """음원 → 점음원 배열 (sx, sy, sz, P[선형 파워, A 가중], grp[음원 번호])"""
    xs, ys, zs, ps, gs = [], [], [], [], []
    seg = float(sc.get("seg_len", 1.0))
    for i, s in enumerate(sc["sources"]):
        if s.get("off"):
            continue
        t = s["type"]
        if t == "point":
            xs.append([s["x"]]); ys.append([s["y"]]); zs.append([s["h"]])
            ps.append([10 ** (0.1 * s["Lw"])]); gs.append([i])
            continue
        if t == "line":
            lines = [(0.0, float(s["Lw_per_m"]))]
            h = float(s["h"])
        elif t == "road":
            lw = road_lw_prime(s)
            h = 0.5                                     # RLS-19 3.2: 노면 위 0.5 m
            if int(s.get("n_lines", 1)) == 2:
                lines = [(+float(s["offset"]), lw - 10 * math.log10(2)),
                         (-float(s["offset"]), lw - 10 * math.log10(2))]
            else:
                lines = [(0.0, lw)]
        else:
            raise ValueError(f"알 수 없는 음원 종류 {t}")
        dx, dy = s["x2"] - s["x1"], s["y2"] - s["y1"]
        L = math.hypot(dx, dy) or 1.0
        nx_, ny_ = -dy / L, dx / L                      # 왼쪽 법선
        for off, lwp in lines:
            px, py, l = _split(s["x1"] + off * nx_, s["y1"] + off * ny_,
                               s["x2"] + off * nx_, s["y2"] + off * ny_, seg)
            xs.append(px); ys.append(py); zs.append(np.full(px.size, h))
            ps.append(np.full(px.size, 10 ** (0.1 * (lwp + 10 * math.log10(l))))); gs.append(np.full(px.size, i))
    if not xs:
        z = np.zeros(0)
        return z, z, z, z, np.zeros(0, np.int64)
    cat = lambda a: np.concatenate([np.asarray(v, float) for v in a])
    return cat(xs), cat(ys), cat(zs), cat(ps), np.concatenate([np.asarray(g, np.int64) for g in gs])


def bars_array(sc, with_barriers=True):
    """방음벽 → bars[n, 5](x1, y1, x2, y2, h), 반사 여부, 방음벽 번호"""
    B = [b for b in sc["barriers"] if with_barriers and not b.get("off")]
    bars = np.array([[b["x1"], b["y1"], b["x2"], b["y2"], b["h"]] for b in B], float).reshape(-1, 5)
    refl = np.array([not b.get("absorbing", False) for b in B], np.bool_)
    return bars, refl, np.arange(len(B), dtype=np.int64)


def bands_of(sc):
    """(meth, bands, lams, alphas, wts)"""
    meth = METH_RLS19 if sc["method"] == "rls19" else METH_ISO
    if meth == METH_RLS19 or sc.get("spectrum", "A500") == "A500":
        bands = np.array([500.0])
        wts = np.array([1.0])
    else:
        bands = np.array(OCTAVES)
        w = 10 ** (0.1 * np.asarray(sc["spectrum_dBA"], float))
        wts = w / w.sum()
    lams = C_SOUND / bands
    alphas = np.array([alpha_iso9613_1(f, sc["temperature_C"], sc["humidity_pct"]) for f in bands])
    return meth, bands, lams, alphas, wts


# ====================================================================== 계산 커널
@njit(parallel=True, cache=True)
def _solve(rx, ry, rz, sx, sy, sz, P, grp, ng, bars, brefl, bgid,
           meth, bands, lams, alphas, wts, ground_alt, Gv, C2, dz1, dz2,
           refl_on, refl_gain, lat_on, lat_grow, step):
    """E[r, g, c]: 수음점 r, 음원 g, 성분 c(0 직접 · 1 방음벽 반사 · 2 측면 회절 추가분) 의 에너지(선형)"""
    NR = rx.shape[0]; NS = sx.shape[0]; NB = bands.shape[0]; NW = bars.shape[0]
    E = np.zeros((NR, ng, 3))
    gnd = np.zeros((1, 1)); bid = np.full((1, 1), -1, np.int64); Gmap = np.full((1, 1), Gv)
    bxy = np.zeros((0, 2)); bptr = np.zeros(1, np.int64); nobars = np.zeros((0, 5))
    for r in prange(NR):
        hd = np.empty(HULL_N); hh = np.empty(HULL_N); out = np.empty(NB); geom = np.empty(NGEOM)
        inc = np.ones(NB, dtype=np.bool_)
        vx = np.empty(MAXV); vy = np.empty(MAXV)
        rb = np.full(MAXO, -1, dtype=np.int64)
        np_ = LAT.MAXP if lat_on else 1
        nx_ = LAT.MAXX if lat_on else 1
        lpx = np.empty(np_); lpy = np.empty(np_); lord = np.empty(np_, np.int64); lH = np.empty(2 * np_, np.int64)
        lcx = np.empty(np_); lcy = np.empty(np_)
        lXb = np.empty(nx_, np.int64); lXg = np.empty(nx_, np.int64)
        lXb2 = np.empty(nx_, np.int64); lXg2 = np.empty(nx_, np.int64)
        gfree = np.empty(NGEOM); ofree = np.empty(NB)
        xr = rx[r]; yr = ry[r]; zr = rz[r]
        for j in range(NS):
            g = grp[j]
            # ---- 직접음(+ 윗모서리 회절)
            vx[0] = sx[j]; vy[0] = sy[j]; vx[1] = xr; vy[1] = yr
            _path(meth, vx, vy, 2, sz[j], zr, sz[j], zr, gnd, gnd, bid, Gmap, _X0, _Y0, _RES, 1, 1, step,
                  -1, rb, 0.0, bands, lams, alphas, ground_alt, C2, dz1, dz2, hd, hh, geom, out, bars, 0.0)
            for b in range(NB):
                inc[b] = True
            F = _band_factor(out, wts, inc, 1.0)
            E[r, g, 0] += P[j] * F
            # ---- 측면 회절(ISO 식 13)
            if lat_on and meth == METH_ISO and geom[G_SHIELD] > 0.5:
                # 평지: 지형만의 단면은 늘 열려 있음 → 그 값이 상한(TR 17534-3 5.6, solver.compute 와 같음)
                path_geometry_b(vx, vy, 2, sz[j], zr, sz[j], zr, gnd, gnd, bid, Gmap, _X0, _Y0, _RES, 1, 1,
                                step, -1, rb, 0.0, hd, hh, gfree, nobars)
                atten_iso(gfree, bands, lams, alphas, ground_alt, C2, dz1, dz2, ofree, 0.0)
                Ffree = _band_factor(ofree, wts, inc, 1.0)
                Fl = LAT.lateral_factor(sx[j], sy[j], sz[j], sz[j], xr, yr, zr, zr, -1,
                                        gnd, gnd, bid, Gmap, _X0, _Y0, _RES, 1, 1, step, 0.0,
                                        bars, bgid, bxy, bptr, bands, lams, alphas, wts, ground_alt,
                                        C2, dz1, dz2, lat_grow, lpx, lpy, lord, lH, lcx, lcy,
                                        lXb, lXg, lXb2, lXg2, 0.0)
                if Fl > 0.0:
                    cap = Ffree if Ffree > F else F
                    add = F + Fl
                    if add > cap:
                        add = cap
                    E[r, g, 2] += P[j] * (add - F)
            # ---- 방음벽 면 1차 반사(허상음원)
            if refl_on:
                for w in range(NW):
                    if not brefl[w]:
                        continue
                    ex = bars[w, 2] - bars[w, 0]; ey = bars[w, 3] - bars[w, 1]
                    L = math.sqrt(ex * ex + ey * ey)
                    if L < 0.1:
                        continue
                    nxw = -ey / L; nyw = ex / L
                    ds = (sx[j] - bars[w, 0]) * nxw + (sy[j] - bars[w, 1]) * nyw
                    dr = (xr - bars[w, 0]) * nxw + (yr - bars[w, 1]) * nyw
                    if ds * dr <= 0.0 or abs(ds) < 0.05 or abs(dr) < 0.05:
                        continue                                  # 같은 쪽에 있어야 반사
                    u = abs(ds) / (abs(ds) + abs(dr))
                    mx = xr - 2.0 * dr * nxw; my = yr - 2.0 * dr * nyw
                    px = sx[j] + u * (mx - sx[j]); py = sy[j] + u * (my - sy[j])
                    tt = ((px - bars[w, 0]) * ex + (py - bars[w, 1]) * ey) / (L * L)
                    if tt < 0.0 or tt > 1.0:
                        continue
                    d1 = math.sqrt((px - sx[j]) ** 2 + (py - sy[j]) ** 2)
                    d2 = math.sqrt((px - xr) ** 2 + (py - yr) ** 2)
                    if d1 < 0.1 or d2 < 0.1:
                        continue
                    zp = sz[j] + (zr - sz[j]) * d1 / (d1 + d2)
                    if zp > bars[w, 4]:
                        continue
                    for b in range(NB):
                        inc[b] = True
                    lmin = min(L, bars[w, 4])
                    if not _accept(meth, lams, lmin, abs(ds) / d1, d1, d2, inc, bars[w, 4]):
                        continue
                    vx[0] = sx[j]; vy[0] = sy[j]; vx[1] = px; vy[1] = py; vx[2] = xr; vy[2] = yr
                    _path(meth, vx, vy, 3, sz[j], zr, sz[j], zr, gnd, gnd, bid, Gmap, _X0, _Y0, _RES, 1, 1,
                          step, -1, rb, 0.0, bands, lams, alphas, ground_alt, C2, dz1, dz2, hd, hh, geom, out,
                          bars, 0.0)
                    E[r, g, 1] += P[j] * _band_factor(out, wts, inc, refl_gain)
    return E


# ====================================================================== 공개 함수
def _db(E):
    return 10.0 * np.log10(np.maximum(E, 1e-30))


def compute(sc, rx, ry, rz, with_barriers=True):
    """수음점 배열 → E[r, 음원, 성분] (선형 에너지). with_barriers=False 면 방음벽 없이(설치 전)"""
    rx = np.ascontiguousarray(rx, float); ry = np.ascontiguousarray(ry, float)
    rz = np.ascontiguousarray(rz, float)
    sx, sy, sz, P, grp = expand_sources(sc)
    ng = max(1, len(sc["sources"]))
    if sx.size == 0 or rx.size == 0:
        return np.zeros((rx.size, ng, 3))
    bars, brefl, bgid = bars_array(sc, with_barriers)
    meth, bands, lams, alphas, wts = bands_of(sc)
    return _solve(rx, ry, rz, sx, sy, sz, P, grp, ng, bars, brefl, bgid,
                  meth, bands, lams, alphas, wts, bool(sc.get("ground_alt", False)), float(sc["ground_G"]),
                  float(sc["C2"]), float(sc["dz_max_single"]), float(sc["dz_max_double"]),
                  bool(sc.get("reflection", True)), 10 ** (-0.1 * float(sc.get("refl_loss_dB", 1.0))),
                  bool(sc.get("lateral", True)), int(sc.get("lateral_max_grow", 3)), float(sc.get("step", 1.0)))


def grid_axes(sc):
    g = sc["grid"]
    r = float(g["res"])
    xs = np.arange(g["x0"] + r / 2, g["x1"], r)
    ys = np.arange(g["y0"] + r / 2, g["y1"], r)
    return xs, ys


def compute_map(sc, with_before=True):
    """격자 소음지도. 반환 dict(xs, ys, after, before, il) — 배열 모양 (ny, nx), 단위 dB(A)"""
    xs, ys = grid_axes(sc)
    X, Y = np.meshgrid(xs, ys)
    Z = np.full(X.size, float(sc["grid"]["h"]))
    after = _db(compute(sc, X.ravel(), Y.ravel(), Z).sum(axis=(1, 2))).reshape(X.shape)
    res = {"xs": xs, "ys": ys, "after": after}
    if with_before:
        before = _db(compute(sc, X.ravel(), Y.ravel(), Z, False).sum(axis=(1, 2))).reshape(X.shape)
        res["before"] = before
        res["il"] = before - after
    return res


def receiver_table(sc):
    """수음점별 결과: L(설치 후), L0(설치 전), IL, 성분별·음원별 dB"""
    R = sc["receivers"]
    if not R:
        return []
    rx = [r["x"] for r in R]; ry = [r["y"] for r in R]; rz = [r["h"] for r in R]
    E1 = compute(sc, rx, ry, rz, True)
    E0 = compute(sc, rx, ry, rz, False)
    rows = []
    for i, r in enumerate(R):
        L = float(_db(E1[i].sum())); L0 = float(_db(E0[i].sum()))
        rows.append({"name": r.get("name", f"R{i + 1}"), "x": r["x"], "y": r["y"], "h": r["h"],
                     "L": L, "L0": L0, "IL": L0 - L,
                     "comp": {c: float(_db(E1[i, :, k].sum())) for k, c in enumerate(COMP)},
                     "by_source": [float(_db(E1[i, g].sum())) for g in range(E1.shape[1])]})
    return rows


def _rep_source_point(src, xr, yr):
    """대표 경로의 음원점: 점음원은 그 점, 선음원·도로는 수음점에서 가장 가까운 선 위 점(도로는 가까운 음원선)"""
    if src["type"] == "point":
        return src["x"], src["y"], src["h"]
    h = 0.5 if src["type"] == "road" else src["h"]
    offs = [0.0]
    if src["type"] == "road" and int(src.get("n_lines", 1)) == 2:
        offs = [+src["offset"], -src["offset"]]
    dx, dy = src["x2"] - src["x1"], src["y2"] - src["y1"]
    L2 = dx * dx + dy * dy or 1.0
    L = math.sqrt(L2); nx_, ny_ = -dy / L, dx / L
    best = None
    for o in offs:
        x1, y1 = src["x1"] + o * nx_, src["y1"] + o * ny_
        t = min(1.0, max(0.0, ((xr - x1) * dx + (yr - y1) * dy) / L2))
        p = (x1 + t * dx, y1 + t * dy)
        dd = math.hypot(p[0] - xr, p[1] - yr)
        if best is None or dd < best[0]:
            best = (dd, p)
    return best[1][0], best[1][1], h


def section_hull(sc, xs, ys, zs, xr, yr, zr, with_barriers=True):
    """S→R 연직 단면: 방음벽 교차점(거리, 윗모서리 높이, 이름)과 고무줄(상부 볼록 껍질) 꼭짓점 목록"""
    Ls = math.hypot(xr - xs, yr - ys)
    cr = []
    for b in (sc["barriers"] if with_barriers else []):
        if b.get("off"):
            continue
        ex, ey = b["x2"] - b["x1"], b["y2"] - b["y1"]
        lx, ly = xr - xs, yr - ys
        den = lx * ey - ly * ex
        if abs(den) < 1e-12:
            continue
        wx, wy = b["x1"] - xs, b["y1"] - ys
        u = (wx * ly - wy * lx) / den
        t = (wx * ey - wy * ex) / den
        if 0 <= u <= 1 and 0 < t < 1:
            cr.append((t * Ls, float(b["h"]), b.get("name", "")))
    cr.sort()
    pts = [(0.0, zs)] + [(d, h) for d, h, _ in cr if h > zs + (zr - zs) * d / max(Ls, 1e-9)] + [(Ls, zr)]
    hull = []
    for p in pts:                                         # 상부 볼록 껍질(geometry._hull_push 와 같은 규칙)
        while len(hull) >= 2:
            (ax, ay), (bx, by) = hull[-2], hull[-1]
            if (bx - ax) * (p[1] - ay) - (by - ay) * (p[0] - ax) >= 0:
                hull.pop()
            else:
                break
        hull.append(p)
    return {"length": Ls, "crossings": cr, "hull": hull}


def path_detail(sc, ri=0, si=0):
    """수음점 ri · 음원 si 의 대표 직접 경로(가장 가까운 음원점) 기하와 감쇠항.
    ISO 는 옥타브 63–8k 대역별 항(계산이 A500 이어도 회절의 주파수 의존성을 보이려고 함께 계산)"""
    r = sc["receivers"][ri]; s = sc["sources"][si]
    xs, ys, zs = _rep_source_point(s, r["x"], r["y"])
    out = {"source_point": (xs, ys, zs), "receiver": (r["x"], r["y"], r["h"])}
    for tag, wb in (("after", True), ("before", False)):
        bars, _, _ = bars_array(sc, wb)
        vx = np.zeros(MAXV); vy = np.zeros(MAXV)
        vx[0], vy[0], vx[1], vy[1] = xs, ys, r["x"], r["y"]
        hd = np.empty(HULL_N); hh = np.empty(HULL_N); geom = np.empty(NGEOM)
        gnd = np.zeros((1, 1)); bid = np.full((1, 1), -1, np.int64); Gmap = np.full((1, 1), float(sc["ground_G"]))
        path_geometry_b(vx, vy, 2, zs, r["h"], zs, r["h"], gnd, gnd, bid, Gmap, _X0, _Y0, _RES, 1, 1,
                        float(sc.get("step", 1.0)), -1, np.full(MAXO, -1, np.int64), 0.0, hd, hh, geom, bars)
        g = {"d": geom[G_D], "s_gr": geom[G_SGR], "h_m": geom[G_HM], "shielded": geom[G_SHIELD] > 0.5,
             "multi_edge": geom[G_DOUBLE] > 0.5, "z": geom[G_Z], "A(d_ss)": geom[G_DSS],
             "B(d_sr)": geom[G_DSR], "e": geom[G_E]}
        if sc["method"] == "rls19":
            t = np.zeros(len(RLS_TERMS)); terms_rls19(geom, t)
            g["terms"] = {"A": dict(zip(RLS_TERMS, map(float, t)))}
        else:
            g["terms"] = {}
            for f in OCTAVES:
                t = np.zeros(len(ISO_TERMS))
                terms_iso(geom, f, C_SOUND / f, alpha_iso9613_1(f, sc["temperature_C"], sc["humidity_pct"]),
                          bool(sc.get("ground_alt", False)), float(sc["C2"]), float(sc["dz_max_single"]),
                          float(sc["dz_max_double"]), t, 0.0)
                g["terms"][int(f)] = dict(zip(ISO_TERMS, map(float, t)))
        g["section"] = section_hull(sc, xs, ys, zs, r["x"], r["y"], r["h"], wb)
        out[tag] = g
    return out


def fresnel_N(z, f):
    """프레넬 수 N = 2z/λ (마에카와 도표 비교용)"""
    return 2.0 * z / (C_SOUND / f)
