# -*- coding: utf-8 -*-
"""
보고서·발표용 고품질 지도 (A3 가로, PNG 300 dpi + PDF 벡터)
  - grid_map      : 격자 소음지도(등음선), 5 dB 등급, DIN 45682 색 순서
  - exceed_map    : 기준 초과 지도(초과량 등급 + 기준 초과 건물 표시)
  - facade_bands  : 파사드 소음지도 — 벽면을 따라 색 띠(층별 최댓값)
  - building_map  : 건물별 평가도 — 건물을 벽면 최고레벨로 채색, 상위 건물 표기
공통 요소: 도로명, 방위표, 축척, 좌표 눈금(EPSG), 정보란(기준·자료·교통량 가정), AI 제작 표기
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import math
import os
import time

import numpy as np
import pandas as pd
import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
from matplotlib.collections import LineCollection
from matplotlib.colors import ListedColormap, BoundaryNorm
from matplotlib.patches import Rectangle, Polygon as MPoly, FancyArrow
from shapely.geometry import box
from shapely.ops import linemerge

from . import AI_NOTICE, __version__
from .config import grid_bbox, center
from .plots import set_font

# DIN 45682(=DIN 18005-2) 소음지도 색 순서에 맞춘 근사 RGB (5 dB 등급)
DIN_EDGES = [35, 40, 45, 50, 55, 60, 65, 70, 75, 80]
DIN_COLS = ["#c8e6a0",  # ≤35 hellgrün
            "#82c85a",  # 35–40 grün
            "#288c3c",  # 40–45 dunkelgrün
            "#fff03c",  # 45–50 gelb
            "#d2aa3c",  # 50–55 ocker
            "#ff8c00",  # 55–60 orange
            "#e63c1e",  # 60–65 zinnoberrot
            "#b41432",  # 65–70 karminrot
            "#8c1e6e",  # 70–75 lilarot
            "#283cb4",  # 75–80 blau
            "#141464"]  # >80 dunkelblau
EXC_EDGES = [0, 3, 6, 10]
EXC_COLS = ["#e9ede6", "#ffd84d", "#ff8a1f", "#d7261e", "#7a0a2a"]
ROAD_FC = "#d9d9d9"
BLD_FC = "#f2f2f2"
BLD_EC = "#3a3a3a"

DEFAULT_LIMITS = {"LrT": 68.0, "LrN": 58.0,
                  "label": "도로교통소음 관리기준(주거지역 등) 주간 68 / 야간 58 dB(A)"}
KEYNAME = {"LrT": "LrT 주간 (06–22시)", "LrN": "LrN 야간 (22–06시)"}


# ------------------------------------------------------------------ 정보란 줄바꿈
_NBSP = "\u00a0"


def wrap_info(v, width=30):
    """정보란 문구 줄바꿈. 숫자와 단위(대/일·대/h·dB·m·%), 'M_T 2,500'·'DTV 30,000' 같은 표기는 가르지 않음"""
    import re
    import textwrap
    v = re.sub(r"(\d) (?=대/|dB|m\b|%|개)", r"\1" + _NBSP, v)
    v = re.sub(r"\b(M_T|M_N|DTV|G=|EPSG:?) (?=[\d,])", r"\1" + _NBSP, v)
    return [w.replace(_NBSP, " ") for w in textwrap.wrap(v, width)] or [""]


# ------------------------------------------------------------------ 교통량 표기
BASIS_KO = {"measured": "실측", "assumed": "가정", "unspecified": "출처 미기재"}


def traffic_summary(T):
    """traffic_used.csv(T) → (정보란 교통량 문구, 가정값 도로 객체 수). 순수 함수.
    도로명(axis)별로 입력 DTV(DTV_in)가 있으면 "DTV n 대/일", 없으면 "M_T n / M_N n 대/h".
    M 에서 DTV 를 역산하지 않음. 출처(traffic_basis)는 실측/가정/출처 미기재, 추정 보충이면 "주간·야간 추정".
    한 axis 안에 값이 다르면 "여러 값". S1 이전 서식(출처 열 없음)은 M 값과 "출처 미기재"로 표시."""
    src = T["traffic_src"].astype(str)
    fb = int(src.str.startswith("fallback").sum())
    u = T[src == "user"]
    parts = []
    for ax, g in u.groupby("axis"):
        dtv = g["DTV_in"] if "DTV_in" in g else pd.Series(np.nan, index=g.index)
        vals = {("D", round(float(d), 6)) if pd.notna(d) else ("M", round(float(md), 6), round(float(mn), 6))
                for d, md, mn in zip(dtv, g["M_day"], g["M_night"])}
        if len(vals) > 1:
            txt = f"{ax} 여러 값"
        elif next(iter(vals))[0] == "D":
            txt = f"{ax} DTV {next(iter(vals))[1]:,.0f} 대/일"
        else:
            _, md, mn = next(iter(vals))
            txt = f"{ax} M_T {md:,.0f} / M_N {mn:,.0f} 대/h"
        basis = g["traffic_basis"] if "traffic_basis" in g else pd.Series("unspecified", index=g.index)
        tags = list(dict.fromkeys(BASIS_KO.get(str(b), str(b)) for b in basis.fillna("unspecified")))
        for col, lab in (("M_day_from", "주간 추정"), ("M_night_from", "야간 추정")):
            if col in g and g[col].astype(str).str.startswith("est_").any():
                tags.append(lab)
        parts.append(f"{txt}({'·'.join(tags)})")
    return ", ".join(parts), fb


# ------------------------------------------------------------------ 자료
class Ctx:
    def __init__(self, cfg):
        self.cfg = cfg
        self.out = cfg["project"]["output_dir"]
        o = self.out
        self.bld = gpd.read_file(os.path.join(o, "buildings.gpkg"))
        rd = gpd.read_file(os.path.join(o, "roads.gpkg"))
        self.roads = rd
        main = rd[rd["lanes"] >= cfg["model"]["main_min_lanes"]]
        self.road_band = gpd.GeoSeries(
            [g.buffer(max(w, 4) / 2, cap_style=2, join_style=2) for g, w in zip(main.geometry, main["width"].fillna(6))],
            crs=rd.crs).union_all()
        minor = rd[rd["lanes"] < cfg["model"]["main_min_lanes"]]
        self.minor_band = gpd.GeoSeries(
            [g.buffer(max(w, 2.5) / 2, cap_style=2) for g, w in zip(minor.geometry, minor["width"].fillna(3))],
            crs=rd.crs).union_all()
        self.J = pd.read_csv(os.path.join(o, "junctions_used.csv"))
        self.T = pd.read_csv(os.path.join(o, "traffic_used.csv"))
        self.run_info = {}
        p = os.path.join(o, "run_info.json")
        if os.path.exists(p):
            import json
            self.run_info = json.load(open(p, encoding="utf-8"))

    def traffic_text(self):
        txt, fb = traffic_summary(self.T)
        return ("입력: " + txt if txt else "입력 없음"), fb


# ------------------------------------------------------------------ 공통 지도 요소
def din_cmap():
    return ListedColormap(DIN_COLS), BoundaryNorm([-200] + DIN_EDGES + [200], len(DIN_COLS))


def setup_fig(title, subtitle):
    set_font()
    fig = plt.figure(figsize=(16.54, 11.69))                 # A3 가로
    ax = fig.add_axes([0.035, 0.06, 0.66, 0.86])
    side = fig.add_axes([0.715, 0.06, 0.265, 0.86]); side.axis("off")
    fig.text(0.035, 0.955, f"SORI Map · {title}", fontsize=19, weight="bold", va="center")
    fig.text(0.035, 0.928, subtitle, fontsize=11.5, color="0.25", va="center")
    fig.text(0.98, 0.018, f"※ {AI_NOTICE}  SORI v{__version__} · 작성 {time.strftime('%Y-%m-%d')}",
             ha="right", fontsize=8, color="0.35")
    return fig, ax, side


def frame(ax, bbox, tick=100):
    ax.set_xlim(bbox[0], bbox[2]); ax.set_ylim(bbox[1], bbox[3]); ax.set_aspect("equal")
    xs = np.arange(math.ceil(bbox[0] / tick) * tick, bbox[2], tick)
    ys = np.arange(math.ceil(bbox[1] / tick) * tick, bbox[3], tick)
    ax.set_xticks(xs); ax.set_yticks(ys)
    ax.set_xticklabels([f"{v:,.0f}" for v in xs], fontsize=7, color="0.3")
    ax.set_yticklabels([f"{v:,.0f}" for v in ys], fontsize=7, color="0.3", rotation=90, va="center")
    ax.tick_params(direction="in", length=4, width=0.6, pad=2)
    ax.set_xlabel(""); ax.set_ylabel("")
    for s in ax.spines.values():
        s.set_linewidth(1.6)


def base_layers(ax, C, bbox, roads=True, bld=True, bld_fc=BLD_FC, z=10):
    v = box(*bbox)
    if roads:
        gpd.GeoSeries([C.road_band.intersection(v)]).plot(ax=ax, fc=ROAD_FC, ec="none", lw=0, zorder=z)
    if bld:
        B = C.bld[C.bld.intersects(v)]
        B.plot(ax=ax, fc=bld_fc, ec=BLD_EC, lw=0.35, zorder=z + 2)


def road_labels(ax, C, bbox, min_lanes=4, z=30):
    v = box(*bbox)
    rd = C.roads[(C.roads["lanes"] >= min_lanes) & (C.roads["axis"].str.len() > 0) & ~C.roads["axis"].str.startswith("_")]
    done = []
    for ax_name, g in rd.groupby("axis"):
        u = g.geometry.union_all()
        geom = (linemerge(u) if u.geom_type == "MultiLineString" else u).intersection(v.buffer(-25))
        parts = [p for p in getattr(geom, "geoms", [geom]) if p.geom_type == "LineString" and p.length > 60]
        for p in sorted(parts, key=lambda q: -q.length)[:2]:
            for frac in (0.62, 0.30):
                m = p.interpolate(frac, normalized=True)
                if all(math.hypot(m.x - a, m.y - b) > 90 for a, b in done):
                    break
            else:
                continue
            a = p.interpolate(max(p.project(m) - 8, 0)); b = p.interpolate(min(p.project(m) + 8, p.length))
            ang = math.degrees(math.atan2(b.y - a.y, b.x - a.x))
            if ang > 90: ang -= 180
            if ang < -90: ang += 180
            ax.text(m.x, m.y, ax_name, rotation=ang, rotation_mode="anchor", ha="center", va="center",
                    fontsize=10.5, weight="bold", color="#222", zorder=z,
                    path_effects=[pe.withStroke(linewidth=3.2, foreground="white")])
            done.append((m.x, m.y))


def north_scale(ax, bbox):
    W = bbox[2] - bbox[0]; H = bbox[3] - bbox[1]
    x = bbox[2] - 0.06 * W; y = bbox[3] - 0.13 * H
    ax.add_patch(MPoly([[x, y + 0.075 * H], [x - 0.018 * W, y], [x, y + 0.018 * H]], closed=True, fc="k", zorder=40))
    ax.add_patch(MPoly([[x, y + 0.075 * H], [x + 0.018 * W, y], [x, y + 0.018 * H]], closed=True, fc="white",
                       ec="k", lw=0.8, zorder=40))
    ax.text(x, y + 0.085 * H, "N", ha="center", fontsize=13, weight="bold", zorder=40,
            path_effects=[pe.withStroke(linewidth=3, foreground="white")])
    L = next(v for v in (200, 100, 50, 20) if v <= 0.25 * W)
    x0 = bbox[0] + 0.04 * W; y0 = bbox[1] + 0.035 * H; h = 0.008 * H
    ax.add_patch(Rectangle((x0 - 0.012 * W, y0 - 0.018 * H), L + 0.05 * W, 0.06 * H, fc="white", ec="0.3",
                           lw=0.6, alpha=0.92, zorder=39))
    for i in range(4):
        ax.add_patch(Rectangle((x0 + i * L / 4, y0), L / 4, h, fc="k" if i % 2 == 0 else "white", ec="k",
                               lw=0.6, zorder=40))
    for i in (0, 2, 4):
        ax.text(x0 + i * L / 4, y0 + 1.8 * h, f"{i * L / 4:g}", ha="center", fontsize=8, zorder=40)
    ax.text(x0 + L + 0.008 * W, y0, "m", fontsize=8, zorder=40)


def legend_classes(side, y_top, title, edges, cols, unit="dB(A)", labels=None):
    side.text(0.0, y_top, title, fontsize=12, weight="bold", va="top", transform=side.transAxes)
    n = len(cols); hgt = 0.032; y = y_top - 0.05
    if labels is None:
        labels = [f"≤ {edges[0]}"] + [f"{a} – {b}" for a, b in zip(edges[:-1], edges[1:])] + [f"> {edges[-1]}"]
    for i, (c, lab) in enumerate(zip(cols, labels)):
        yy = y - i * hgt
        side.add_patch(Rectangle((0.0, yy - hgt * 0.8), 0.13, hgt * 0.8, fc=c, ec="0.3", lw=0.5,
                                 transform=side.transAxes))
        side.text(0.17, yy - hgt * 0.4, f"{lab} {unit}", fontsize=10, va="center", transform=side.transAxes)
    return y - n * hgt - 0.02


def info_block(side, y_top, C, lines_extra):
    cfg = C.cfg
    tr, fb = C.traffic_text()
    ri = C.run_info
    g = cfg["input"]["ground"]
    rows = [("음원", "RLS-19 (FGSV 052, 2019/2020)"),
            ("전파", f"ISO 9613-2:1996, 500 Hz, 지면 {'일반법' if g['method'] == 'general' else '대안법'} G={g['default_G']:g}"),
            ("지형·건물", "국토지리정보원 수치지형도 v2.0, 높이 = 층수 × " f"{cfg['model']['floor_height']:g} m"),
            ("좌표계", f"EPSG:{cfg['crs']}"),
            ("교통량", tr),
            ("", f"그 밖의 도로 {fb}개 객체는 차로수 기준 가정값")]
    rows = lines_extra + rows
    side.text(0.0, y_top, "계산 조건", fontsize=12, weight="bold", va="top", transform=side.transAxes)
    y = y_top - 0.045
    for k, v in rows:
        if k:
            side.text(0.0, y, k, fontsize=9, weight="bold", va="top", transform=side.transAxes)
        wrapped = wrap_info(v, 30)
        for i, w in enumerate(wrapped):
            side.text(0.25, y - i * 0.022, w, fontsize=9, va="top", transform=side.transAxes)
        y -= 0.022 * len(wrapped) + 0.008
    return y


def warn_box(side, y, text):
    import textwrap
    lines = textwrap.wrap(text, 38)
    hh = 0.022 * len(lines) + 0.02
    side.add_patch(Rectangle((0, y - hh), 1.0, hh, fc="#fff4e5", ec="#e08a00", lw=0.8, transform=side.transAxes))
    for i, l in enumerate(lines):
        side.text(0.03, y - 0.012 - i * 0.022, l, fontsize=8.8, va="top", color="#6a3d00", transform=side.transAxes)
    return y - hh - 0.02


def save(fig, out, name):
    pdf = os.path.join(out, name + ".pdf"); png = os.path.join(out, name + ".png")
    fig.savefig(png, dpi=300); fig.savefig(pdf)
    plt.close(fig)
    return [png, pdf]


# ------------------------------------------------------------------ 격자 배열
def grid_array(C, key, h):
    G = pd.read_csv(os.path.join(C.out, "grid_result.csv"))
    d = np.load(os.path.join(C.out, "model_rasters.npz"))
    gx, gy = d["gx"], d["gy"]
    step = float(gx[1] - gx[0])
    Gh = G[np.isclose(G.h, h)]
    A = np.full((len(gy), len(gx)), np.nan)
    A[np.round((Gh.y - gy[0]) / step).astype(int), np.round((Gh.x - gx[0]) / step).astype(int)] = Gh[key].values
    # 건물 경계 1칸만 최근접값으로 보완(등음선이 벽까지 닿도록), 건물 내부는 마스크 유지
    from scipy.ndimage import distance_transform_edt, binary_dilation
    nan = np.isnan(A)
    idx = distance_transform_edt(nan, return_distances=False, return_indices=True)
    near = binary_dilation(~nan, iterations=2) & nan
    A2 = A.copy(); A2[near] = A[tuple(idx)][near]
    return gx, gy, step, np.ma.masked_invalid(A2), A


# ------------------------------------------------------------------ 1) 격자 소음지도
def grid_map(cfg, key="LrT", h=None):
    C = Ctx(cfg)
    h = h if h is not None else cfg["receivers"]["grid"]["heights"][0]
    gx, gy, step, Am, A = grid_array(C, key, h)
    bbox = grid_bbox(cfg)
    fig, ax, side = setup_fig(f"{cfg['project']['name']} 도로교통소음 지도 — {KEYNAME[key]}",
                              f"격자 소음지도 · 수음 높이 {h:g} m · 격자 간격 {step:g} m · 등음선 5 dB")
    cmap, norm = din_cmap()
    lv = [-200] + DIN_EDGES + [200]
    ax.contourf(gx, gy, Am, levels=lv, cmap=cmap, norm=norm, zorder=1, antialiased=True)
    ax.contour(gx, gy, Am, levels=DIN_EDGES, colors="#333", linewidths=0.25, alpha=0.6, zorder=2)
    base_layers(ax, C, bbox)
    sig = C.J[C.J.type == "signal"].groupby("cluster")[["x", "y"]].mean()
    ax.scatter(sig.x, sig.y, marker="o", s=70, fc="white", ec="k", lw=1.2, zorder=35)
    road_labels(ax, C, bbox)
    frame(ax, bbox); north_scale(ax, bbox)
    y = legend_classes(side, 1.0, f"{key} [dB(A)]", DIN_EDGES, DIN_COLS, unit="")
    side.add_patch(Rectangle((0.0, y - 0.022), 0.13, 0.02, fc=ROAD_FC, ec="white", transform=side.transAxes))
    side.text(0.17, y - 0.012, "간선도로 노면", fontsize=10, va="center", transform=side.transAxes)
    side.scatter([0.065], [y - 0.05], s=60, fc="white", ec="k", lw=1.2, transform=side.transAxes)
    side.text(0.17, y - 0.05, "신호교차로", fontsize=10, va="center", transform=side.transAxes)
    vals = A[np.isfinite(A)]
    y = info_block(side, y - 0.10, C, [("결과 범위", f"{vals.min():.1f} – {vals.max():.1f} dB(A) (도로 위 포함)")])
    warn_box(side, y - 0.01, "교통량 일부가 가정값입니다. 절대 레벨은 참고용이며 실측 교통량으로 다시 계산해야 합니다.")
    return save(fig, C.out, f"HQ_grid_{key}_h{h:g}m")


# ------------------------------------------------------------------ 2) 기준 초과 지도
def exceed_map(cfg, key="LrT", h=None, limits=None):
    C = Ctx(cfg)
    lim = dict(DEFAULT_LIMITS, **(limits or cfg.get("plots", {}).get("limits") or {}))
    L = float(lim[key])
    h = h if h is not None else cfg["receivers"]["grid"]["heights"][0]
    gx, gy, step, Am, A = grid_array(C, key, h)
    bbox = grid_bbox(cfg)
    fig, ax, side = setup_fig(f"{cfg['project']['name']} 기준 초과 지도 — {KEYNAME[key]}",
                              f"기준값 {L:g} dB(A) 대비 초과량 · 수음 높이 {h:g} m · 격자 {step:g} m")
    E = Am - L
    cmap = ListedColormap(EXC_COLS); norm = BoundaryNorm([-500] + EXC_EDGES + [500], len(EXC_COLS))
    ax.contourf(gx, gy, E, levels=[-500] + EXC_EDGES + [500], cmap=cmap, norm=norm, zorder=1)
    ax.contour(gx, gy, E, levels=[0], colors="#7a0a2a", linewidths=0.9, zorder=2)
    # 벽면 최고레벨이 기준을 넘는 건물 강조
    nb = nf = 0
    fp = os.path.join(C.out, "facade_result.csv")
    v = box(*bbox)
    base_layers(ax, C, bbox)
    if os.path.exists(fp):
        FR = pd.read_csv(fp)
        mx = FR.groupby("bid")[key].max()
        exc = mx[mx > L].index
        nb = len(exc); nf = int((FR[key] > L).sum())
        B = C.bld[C.bld.bid.isin(exc) & C.bld.intersects(v)]
        B.plot(ax=ax, fc="#f6b3b8", ec="#9b0f2a", lw=0.7, zorder=13)
    road_labels(ax, C, bbox)
    frame(ax, bbox); north_scale(ax, bbox)
    labels = [f"기준 이하"] + [f"초과 {a} – {b} dB" for a, b in zip(EXC_EDGES[:-1], EXC_EDGES[1:])] + [f"초과 > {EXC_EDGES[-1]} dB"]
    y = legend_classes(side, 1.0, f"{key} − {L:g} dB", EXC_EDGES, EXC_COLS, unit="", labels=labels)
    side.add_patch(Rectangle((0.0, y - 0.022), 0.13, 0.02, fc="#f6b3b8", ec="#9b0f2a", transform=side.transAxes))
    side.text(0.17, y - 0.012, "벽면 최고레벨이 기준을 넘는 건물", fontsize=10, va="center", transform=side.transAxes)
    valid = np.isfinite(A)
    share = 100 * np.mean(A[valid] > L)
    y = info_block(side, y - 0.07, C, [("기준", lim["label"]),
                                       ("초과 면적", f"격자 지점의 {share:.0f} % (도로 위 포함)"),
                                       ("초과 건물", f"{nb}동 (초과 벽면·층 {nf:,}개)" if os.path.exists(fp) else "벽면 계산 없음")])
    # Lr 도 A가중 등가소음도(LAeq 와 같은 종류)다. 다른 것은 계산 조건(D_K 가산, 순풍 LAT(DW)·Cmet 미적용, 가정 입력)
    warn_box(side, y - 0.01, "지도 값은 A가중 등가소음도로 기준과 같은 지표이나, 교차로 보정(D_K)·순풍 조건(Cmet 미적용)·"
                             "가정 교통량이 들어가 실측 LAeq와 조건이 다릅니다. 실측 검증 전에는 초과 범위를 참고로만 보세요.")
    return save(fig, C.out, f"HQ_exceed_{key}_h{h:g}m")


# ------------------------------------------------------------------ 3) 파사드 색 띠
def _facade_pieces(C, key):
    FR = pd.read_csv(os.path.join(C.out, "facade_result.csv"))
    F = pd.read_csv(os.path.join(C.out, "facades.csv"), index_col="fid")
    W = FR.groupby(["fid", "piece"]).agg(a=("a", "first"), b=("b", "first"), L=(key, "max"),
                                         bid=("bid", "first")).reset_index()
    f = F.loc[W.fid]
    off = 1.1                                              # 벽에서 바깥으로 띄우는 거리(m)
    W["x1"] = f.x1.values + W.a * (f.x2.values - f.x1.values) + off * f.nx.values
    W["y1"] = f.y1.values + W.a * (f.y2.values - f.y1.values) + off * f.ny.values
    W["x2"] = f.x1.values + W.b * (f.x2.values - f.x1.values) + off * f.nx.values
    W["y2"] = f.y1.values + W.b * (f.y2.values - f.y1.values) + off * f.ny.values
    return FR, W


def facade_bands(cfg, key="LrT"):
    C = Ctx(cfg)
    FR, W = _facade_pieces(C, key)
    cx, cy = center(cfg); r = cfg["receivers"]["facade"]["radius"] + 15
    bbox = (cx - r, cy - r, cx + r, cy + r)
    fig, ax, side = setup_fig(f"{cfg['project']['name']} 건물 벽면(파사드) 소음지도 — {KEYNAME[key]}",
                              "벽면 바깥 5 cm, 층별 계산값 중 최댓값을 벽면을 따라 색 띠로 표시")
    base_layers(ax, C, bbox)
    gpd.GeoSeries([C.minor_band.intersection(box(*bbox))]).plot(ax=ax, fc="#ececec", ec="none", zorder=9)
    cmap, norm = din_cmap()
    W = W.sort_values("L")
    segs = np.stack([W[["x1", "y1"]].values, W[["x2", "y2"]].values], axis=1)
    lc = LineCollection(segs, colors=cmap(norm(W.L.values)), linewidths=2.4, capstyle="butt", zorder=20)
    ax.add_collection(lc)
    road_labels(ax, C, bbox)
    frame(ax, bbox); north_scale(ax, bbox)
    y = legend_classes(side, 1.0, f"{key} [dB(A)] (층별 최댓값)", DIN_EDGES, DIN_COLS, unit="")
    y = info_block(side, y - 0.03, C, [("수음점", f"벽면 {len(W):,}구간 × 층 = {len(FR):,}지점, 외벽 앞 5 cm"),
                                       ("반사", "1·2차 (자기 벽면 반사 제외, RLS-19 1장)"),
                                       ("결과 범위", f"{FR[key].min():.1f} – {FR[key].max():.1f} dB(A)")])
    warn_box(side, y - 0.01, "교통량 일부가 가정값입니다. 절대 레벨은 참고용입니다.")
    return save(fig, C.out, f"HQ_facade_{key}")


# ------------------------------------------------------------------ 4) 건물 평가도
def building_map(cfg, key="LrT", top=12):
    C = Ctx(cfg)
    FR = pd.read_csv(os.path.join(C.out, "facade_result.csv"))
    agg = FR.groupby("bid").agg(L=(key, "max"), fl=("floor", "max"))
    cx, cy = center(cfg); r = cfg["receivers"]["facade"]["radius"] + 15
    bbox = (cx - r, cy - r, cx + r, cy + r)
    fig, ax, side = setup_fig(f"{cfg['project']['name']} 건물별 소음 평가도 — {KEYNAME[key]}",
                              "건물마다 벽면(전 층) 최고 레벨로 채색 · 상위 건물 표기")
    base_layers(ax, C, bbox, bld=False)
    v = box(*bbox)
    B = C.bld[C.bld.intersects(v)].merge(agg, left_on="bid", right_index=True, how="left")
    cmap, norm = din_cmap()
    B[B.L.isna()].plot(ax=ax, fc=BLD_FC, ec="#999", lw=0.3, zorder=11)
    E = B[B.L.notna()]
    E.plot(ax=ax, color=[cmap(norm(x)) for x in E.L], ec=BLD_EC, lw=0.35, zorder=12)
    T = E.sort_values("L", ascending=False)
    placed = []
    for _, b in T.iterrows():
        if len(placed) >= top:
            break
        p = b.geometry.representative_point()
        if any(math.hypot(p.x - a, p.y - c) < 28 for a, c in placed):
            continue
        nm = (b.bname or "").strip()
        lab = f"{b.L:.0f}" + (f"\n{nm[:10]}" if nm and nm != "nan" else f"\n{int(b.floors)}층")
        ax.text(p.x, p.y, lab, ha="center", va="center", fontsize=7.5, weight="bold", zorder=30, linespacing=0.95,
                path_effects=[pe.withStroke(linewidth=2.6, foreground="white")])
        placed.append((p.x, p.y))
    road_labels(ax, C, bbox)
    frame(ax, bbox); north_scale(ax, bbox)
    y = legend_classes(side, 1.0, f"벽면 최고 {key} [dB(A)]", DIN_EDGES, DIN_COLS, unit="")
    side.add_patch(Rectangle((0.0, y - 0.022), 0.13, 0.02, fc=BLD_FC, ec="#999", transform=side.transAxes))
    side.text(0.17, y - 0.012, "계산 범위 밖 건물", fontsize=10, va="center", transform=side.transAxes)
    y = info_block(side, y - 0.06, C, [("평가 건물", f"{len(E):,}동 (반경 {cfg['receivers']['facade']['radius']:g} m)")])
    warn_box(side, y - 0.01, "숫자 = 벽면 최고 레벨 dB(A), 아래는 건물명 또는 층수. 교통량 일부 가정값.")
    return save(fig, C.out, f"HQ_building_{key}")


def main(cfg):
    files = []
    hs = cfg["receivers"]["grid"]["heights"]
    for key in ("LrT", "LrN"):
        files += grid_map(cfg, key, hs[0])
        files += exceed_map(cfg, key, hs[0])
        if os.path.exists(os.path.join(cfg["project"]["output_dir"], "facade_result.csv")):
            files += facade_bands(cfg, key)
            files += building_map(cfg, key)
    return files
