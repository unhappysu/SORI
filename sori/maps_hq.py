# -*- coding: utf-8 -*-
"""
보고서·발표용 고품질 지도 (A3 가로, PNG 300 dpi + PDF 벡터)
  - grid_map      : 격자 소음지도(등음선), 5 dB 등급, DIN 45682 색 순서
  - facade_bands  : 파사드 소음지도 — 벽면을 따라 색 띠(층별 최댓값)
  - building_map  : 건물별 평가도 — 건물을 벽면 최고레벨로 채색, 상위 건물 표기
공통 요소: 도로명, 방위표, 축척, 좌표 눈금(EPSG), 정보란(자료·교통량 가정), AI 제작 표기
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
from . import naming
from .propagation import result_model

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
# 앱 화면용 단순 색표(2026-11-14 사용자: "모든 색이 들어가서 별로, 색을 줄여줘"): 노랑 → 주황 → 빨강 한 계열 7단계,
# 50 dB 이하는 한 칸(옅은 회녹색), 그 위는 5 dB 등급
PALETTES = {
    "din": ([35, 40, 45, 50, 55, 60, 65, 70, 75, 80], list(DIN_COLS)),
    "simple": ([50, 55, 60, 65, 70, 75],
               ["#e6ecdf",   # ≤50
                "#fff2b3",   # 50–55
                "#fdd27a",   # 55–60
                "#f99b4c",   # 60–65
                "#e8542f",   # 65–70
                "#b5172b",   # 70–75
                "#640d26"]),  # >75
}


# 색표마다 건물 바탕·테두리·테두리 굵기·간선 노면 색. simple(2026-11-14): 건물 #f2f2f2 는 50 dB 이하 칸(#e6ecdf)과
# 거의 같아 골목 지역이 건물과 섞여 보였음 → 회색 #c9c9c9 + 진한 테두리(후보 넷 비교), 노면은 건물과 섞이지 않게 더 밝게 #ececec
PALETTE_STYLE = {"din": ("#f2f2f2", "#3a3a3a", 0.35, "#d9d9d9"),
                 "simple": ("#c9c9c9", "#4a4a4a", 0.45, "#ececec")}


def set_palette(name):
    """색표 바꾸기("din" = DIN 45682 11색, 보고서 그림 기본 / "simple" = 앱 화면 7색). DIN_EDGES·DIN_COLS 목록을
    제자리에서 바꾸므로 이 목록을 가져다 쓰는 곳(viewer3d 등)도 함께 바뀜. 반환: 이전 색표 이름"""
    if name not in PALETTES:
        raise ValueError(f"색표는 {sorted(PALETTES)} 중 하나")
    global BLD_FC, BLD_EC, BLD_LW, ROAD_FC
    prev = palette_name()
    e, c = PALETTES[name]
    DIN_EDGES[:] = e
    DIN_COLS[:] = c
    BLD_FC, BLD_EC, BLD_LW, ROAD_FC = PALETTE_STYLE[name]
    return prev


def palette_name():
    return next((k for k, (e, c) in PALETTES.items() if e == DIN_EDGES and c == DIN_COLS), "din")
ROAD_FC = "#d9d9d9"
BLD_FC = "#f2f2f2"
BLD_EC = "#3a3a3a"
BLD_LW = 0.35

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
    한 axis 안에 값이 다르면 "여러 값". S1 이전 서식(출처 열 없음)은 M 값과 "출처 미기재"로 표시.
    방향별 승용차 속도(v_pkw_fwd ≠ v_pkw_bwd, 5a-17)가 있는 도로만 "속도 정/역 km/h(방향별)"을 붙임(2026-11-14)"""
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
        if {"v_pkw_fwd", "v_pkw_bwd"} <= set(g.columns):
            vf, vb = pd.to_numeric(g["v_pkw_fwd"], errors="coerce"), pd.to_numeric(g["v_pkw_bwd"], errors="coerce")
            dif = {(float(a), float(b)) for a, b in zip(vf, vb) if pd.notna(a) and pd.notna(b) and a != b}
            if len(dif) == 1:
                a, b = next(iter(dif))
                txt += f" · 속도 {a:g}/{b:g} km/h(방향별)"
            elif dif:
                txt += " · 방향별 속도 여러 값"
        basis = g["traffic_basis"] if "traffic_basis" in g else pd.Series("unspecified", index=g.index)
        tags = list(dict.fromkeys(BASIS_KO.get(str(b), str(b)) for b in basis.fillna("unspecified")))
        for col, lab in (("M_day_from", "주간 추정"), ("M_night_from", "야간 추정")):
            if col in g and g[col].astype(str).str.startswith("est_").any():
                tags.append(lab)
        parts.append(f"{txt}({'·'.join(tags)})")
    return ", ".join(parts), fb


def traffic_std_text(T):
    """traffic_used.csv(T) → "빈칸 M·p 는 RLS-19 표 2(독일 기준, 국내와 다를 수 있음)" 같은 문구. 순수 함수.
    std_filled 열이 없는 이전 결과나 채운 도로가 없으면 빈 문자열"""
    if "traffic_std" not in T:
        return ""
    labs = [s for s in dict.fromkeys(T["traffic_std"].fillna("").astype(str)) if s]
    return f"빈칸 M·p 는 {'·'.join(labs)}(독일 기준, 국내와 다를 수 있음)" if labs else ""


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
        self.model = result_model(cfg, o)               # 결과의 전파 방법이 하나이고 설정과 같은지 확인

    def traffic_text(self):
        txt, fb = traffic_summary(self.T)
        return ("입력: " + txt if txt else "입력 없음"), fb

    def traffic_std_text(self):
        """교통량 빈칸(M·p)을 독일 기준표로 채운 경우의 정보란 문구. 채운 도로가 없으면 빈 문자열"""
        return traffic_std_text(self.T)


# ------------------------------------------------------------------ 공통 지도 요소
def din_cmap():
    return ListedColormap(DIN_COLS), BoundaryNorm([-200] + DIN_EDGES + [200], len(DIN_COLS))


def setup_fig(title, subtitle):
    set_font()
    fig = plt.figure(figsize=(16.54, 11.69))                 # A3 가로
    ax, side = layout(fig, title, subtitle)
    return fig, ax, side


def layout(fig, title, subtitle):
    """보고서 지도 틀(지도 축·오른쪽 정보란·제목·고지)을 이미 있는 그림에 배치 — 데스크톱 화면(sori_qt)도 씀"""
    ax = fig.add_axes([0.035, 0.06, 0.66, 0.86])
    side = fig.add_axes([0.715, 0.06, 0.265, 0.86]); side.axis("off")
    fig.text(0.035, 0.955, f"SORI Map · {title}", fontsize=19, weight="bold", va="center")
    fig.text(0.035, 0.928, subtitle, fontsize=11.5, color="0.25", va="center")
    fig.text(0.98, 0.018, f"※ {AI_NOTICE}  SORI v{__version__} · 작성 {time.strftime('%Y-%m-%d')}",
             ha="right", fontsize=8, color="0.35")
    return ax, side


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


def base_layers(ax, C, bbox, roads=True, bld=True, bld_fc=None, z=10):
    """간선 노면 띠·건물. 색은 부를 때의 색표 값(set_palette 가 BLD_FC·BLD_EC·ROAD_FC·BLD_LW 를 바꿈)"""
    v = box(*bbox)
    if roads:
        gpd.GeoSeries([C.road_band.intersection(v)]).plot(ax=ax, fc=ROAD_FC, ec="none", lw=0, zorder=z)
    if bld:
        B = C.bld[C.bld.intersects(v)]
        B.plot(ax=ax, fc=bld_fc or BLD_FC, ec=BLD_EC, lw=BLD_LW, zorder=z + 2)


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


WARN_RESERVE = 0.10      # 정보란 맨 아래 경고 상자(warn_box) 자리(정보란 높이 비율)


def info_block(side, y_top, C, lines_extra):
    cfg = C.cfg
    tr, fb = C.traffic_text()
    ri = C.run_info
    rows = [("음원", "RLS-19 (FGSV 052, 2019/2020)"),
            ("전파", C.model.label(cfg)),
            ("구성", C.model.combo_label(cfg)),
            ("지형·건물", "국토지리정보원 수치지형도 v2.0, 높이 = 층수 × " f"{cfg['model']['floor_height']:g} m"),
            ("좌표계", f"EPSG:{cfg['crs']}"),
            ("교통량", tr),
            ("", f"그 밖의 도로 {fb}개 객체는 차로수 기준 가정값")]
    st = C.traffic_std_text()
    if st:
        rows.append(("", st))
    nx = int((C.T["traffic_src"].astype(str) == "excluded_minor").sum())
    if nx:
        rows.append(("", f"교통량 자료 없는 골목길(1차로) {nx}개 객체는 음원에서 제외"))
    raw = lines_extra + rows
    # 정보란 아래 끝(0)을 넘지 않게: 줄이 많아 넘치면 글자·줄 간격을 같이 줄이고, 글자가 작아진 만큼 한 줄에 더 넣음
    # (아래 경고 상자 자리 WARN_RESERVE 남김). 2026-11-12: 골목길 제외 줄이 붙으며 마지막 줄이 정보란 밖으로 나가
    # 아래 고지 글과 겹치고 경고 상자가 잘리던 문제
    for s in np.arange(1.0, 0.54, -0.025):
        rows = [(k, wrap_info(v, int(30 / s))) for k, v in raw]
        if (0.045 + sum(0.022 * len(w) + 0.008 for _, w in rows)) * s <= y_top - WARN_RESERVE:
            break
    s = float(s)
    side._sori_scale = s
    side.text(0.0, y_top, "계산 조건", fontsize=12 * max(s, 0.8), weight="bold", va="top", transform=side.transAxes)
    y = y_top - 0.045 * s
    for k, wrapped in rows:
        if k:
            side.text(0.0, y, k, fontsize=9 * s, weight="bold", va="top", transform=side.transAxes)
        for i, w in enumerate(wrapped):
            side.text(0.25, y - i * 0.022 * s, w, fontsize=9 * s, va="top", transform=side.transAxes)
        y -= (0.022 * len(wrapped) + 0.008) * s
    return y


def warn_box(side, y, text):
    import textwrap
    s = getattr(side, "_sori_scale", 1.0)               # info_block 이 줄인 비율을 같이 씀
    lines = textwrap.wrap(text, int(38 / max(s, 0.7)))
    hh = (0.022 * len(lines) + 0.02) * s
    side.add_patch(Rectangle((0, y - hh), 1.0, hh, fc="#fff4e5", ec="#e08a00", lw=0.8, transform=side.transAxes))
    for i, l in enumerate(lines):
        side.text(0.03, y - (0.012 + i * 0.022) * s, l, fontsize=8.8 * s, va="top", color="#6a3d00",
                  transform=side.transAxes)
    return y - hh - 0.02


def save(fig, out, name, cfg=None, rule=None):
    """rule = (종류, 시간대, 부가): 파일명 규칙(naming.py) 사본도 만듦"""
    pdf = os.path.join(out, name + ".pdf"); png = os.path.join(out, name + ".png")
    fig.savefig(png, dpi=300); fig.savefig(pdf)
    plt.close(fig)
    files = [png, pdf]
    if rule is not None and cfg is not None:
        files += [f for f in (naming.copy_as(cfg, png, *rule), naming.copy_as(cfg, pdf, *rule)) if f]
    return files


# ------------------------------------------------------------------ 격자 배열
DISPLAY_STEP = 1.25       # 등음선을 그릴 때의 표시 간격 상한(m). 계산 격자가 이보다 성기면 격자점 사이를 보간해 그림
                          # (2026-11-14: 2.5 → 1.25 m — 2.5 m 격자도 2배로 보간해 확대했을 때 각진 등음선 완화, 계산값 불변)


def grid_array(C, key, h, bbox=None, G=None):
    """격자 결과 → 등음선용 배열. 반환 (gx, gy, step, Am, A):
      A  = 계산한 격자점 값 그대로(건물 안 NaN) — 결과 범위·통계는 이 값으로
      Am = 그리기용(gx, gy 는 Am 의 좌표). 계산값은 바꾸지 않고 표시만 다듬음:
           ① 건물 경계 2칸을 최근접값으로 메움(등음선이 벽까지 닿도록, 건물 안쪽은 가림 유지)
           ② 계산 격자가 DISPLAY_STEP 보다 성기면 격자점 사이를 3차 보간(각진 등음선 완화, 둘러싼 격자점 4개 값 범위 안으로 제한)
           ③ bbox 를 주면 지도 테두리까지 가장자리 값으로 채움(격자점은 테두리에서 반 칸 안쪽이라 흰 띠가 생김)
      step = 계산 격자 간격(표시 간격 아님)
    G: 격자 표(x, y, h, key 열)를 직접 줄 때(차이 지도 등). 기본은 결과 폴더의 grid_result.csv.
    정밀 격자(sori.refine)로 계산한 결과면 Am 은 세분 계산점의 삼각망 배열(grid_fine_h*.npz, 보간·넘침 제한 없음)"""
    fine = None
    if G is None:
        G = pd.read_csv(os.path.join(C.out, "grid_result.csv"))
        from .refine import load_fine
        fine = load_fine(C.out, h)
    d = np.load(os.path.join(C.out, "model_rasters.npz"))
    gx, gy = d["gx"], d["gy"]
    step = float(gx[1] - gx[0])
    Gh = G[np.isclose(G.h, h)]
    A = np.full((len(gy), len(gx)), np.nan)
    A[np.round((Gh.y - gy[0]) / step).astype(int), np.round((Gh.x - gx[0]) / step).astype(int)] = Gh[key].values
    from scipy.ndimage import distance_transform_edt, binary_dilation, map_coordinates
    nan = np.isnan(A)
    if nan.all():
        return gx, gy, step, np.ma.masked_invalid(A), A
    if fine is not None and key in fine:
        return _fine_display(fine, key, bbox, step, A)
    idx = distance_transform_edt(nan, return_distances=False, return_indices=True)
    full = A[tuple(idx)]                                   # 빈 칸을 모두 최근접값으로(보간 입력)
    hide = nan & ~binary_dilation(~nan, iterations=2)      # 가릴 칸: 건물 안쪽(경계 2칸 밖)
    n = max(1, int(round(step / DISPLAY_STEP)))
    fs = step / n
    x0, x1, y0, y1 = gx[0], gx[-1], gy[0], gy[-1]
    if bbox is not None:                                   # 테두리까지(격자 밖은 가장자리 값)
        x0, y0 = min(x0, bbox[0]), min(y0, bbox[1])
        x1, y1 = max(x1, bbox[2]), max(y1, bbox[3])
    if n == 1 and (x0, x1, y0, y1) == (gx[0], gx[-1], gy[0], gy[-1]):
        return gx, gy, step, np.ma.masked_where(hide, full), A
    fx = np.r_[x0, np.arange(gx[0] + fs * np.ceil((x0 - gx[0]) / fs - 1e-9), x1 - 1e-9, fs), x1]
    fy = np.r_[y0, np.arange(gy[0] + fs * np.ceil((y0 - gy[0]) / fs - 1e-9), y1 - 1e-9, fs), y1]
    fx, fy = np.unique(np.round(fx, 6)), np.unique(np.round(fy, 6))
    cx, cy = (fx - gx[0]) / step, (fy - gy[0]) / step      # 계산 격자 색인 좌표
    YY, XX = np.meshgrid(cy, cx, indexing="ij")
    F = map_coordinates(full, [YY, XX], order=3 if n > 1 else 1, mode="nearest")
    # 3차 보간의 넘침 방지: 각 표시점을 둘러싼 격자점 4개 값의 범위 안으로 제한(격자점에 없는 봉우리·골을 만들지 않음)
    iy = np.clip(np.floor(YY).astype(int), 0, full.shape[0] - 1); jx = np.clip(np.floor(XX).astype(int), 0, full.shape[1] - 1)
    iy1 = np.minimum(iy + 1, full.shape[0] - 1); jx1 = np.minimum(jx + 1, full.shape[1] - 1)
    q = np.stack([full[iy, jx], full[iy, jx1], full[iy1, jx], full[iy1, jx1]])
    F = np.clip(F, q.min(axis=0), q.max(axis=0))
    H = map_coordinates(hide.astype(float), [YY, XX], order=0, mode="nearest") > 0.5
    return fx, fy, step, np.ma.masked_where(H, F), A


def _fine_display(fine, key, bbox, step, A):
    """정밀 격자 그리기 배열: 건물 경계 2칸(세밀 간격)은 최근접값으로 메우고 안쪽은 가림, bbox 테두리까지 가장자리 값"""
    from scipy.ndimage import distance_transform_edt, binary_dilation
    fx, fy, F = fine["fx"], fine["fy"], fine[key].astype(float)
    nan = np.isnan(F)
    full = F[tuple(distance_transform_edt(nan, return_distances=False, return_indices=True))]
    hide = nan & ~binary_dilation(~nan, iterations=2)
    if bbox is not None:
        px0, px1 = int(bbox[0] < fx[0]), int(bbox[2] > fx[-1])
        py0, py1 = int(bbox[1] < fy[0]), int(bbox[3] > fy[-1])
        fx = np.r_[[bbox[0]] * px0, fx, [bbox[2]] * px1]
        fy = np.r_[[bbox[1]] * py0, fy, [bbox[3]] * py1]
        full = np.pad(full, ((py0, py1), (px0, px1)), mode="edge")
        hide = np.pad(hide, ((py0, py1), (px0, px1)), mode="edge")
    return fx, fy, step, np.ma.masked_where(hide, full), A


def refine_note(C, h):
    """지도 제목 덧붙임: 정밀 격자면 ' · 경계 세분 0.625 m'"""
    from .refine import fine_path
    p = fine_path(C.out, h)
    if not os.path.exists(p):
        return ""
    return f" · 경계 세분 {float(np.load(p)['fine_step']):g} m"


# ------------------------------------------------------------------ 1) 격자 소음지도
def grid_map(cfg, key="LrT", h=None):
    C = Ctx(cfg)
    h = h if h is not None else cfg["receivers"]["grid"]["heights"][0]
    set_font()
    fig = plt.figure(figsize=(16.54, 11.69))                 # A3 가로
    draw_grid(fig, C, cfg, key, h)
    return save(fig, C.out, f"HQ_grid_{key}_h{h:g}m", cfg, ("grid", key, f"_h{h:g}m"))


def draw_grid(fig, C, cfg, key, h):
    """보고서 수준 격자 소음지도를 fig 에 그림(그림 파일 grid_map 과 데스크톱 화면 sori_qt 가 같이 씀).
    반환: 지도 축(ax) — 화면에서 클릭·단면 선 긋기에 씀"""
    bbox = grid_bbox(cfg)
    gx, gy, step, Am, A = grid_array(C, key, h, bbox)
    ax, side = layout(fig, f"{cfg['project']['name']} 도로교통소음 지도 — {KEYNAME[key]}",
                      f"격자 소음지도 · 수음 높이 {h:g} m · 격자 간격 {step:g} m{refine_note(C, h)} · 등음선 5 dB")
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
    return ax


def contour_labels(ax, cs, bbox, gap=0.12, per_level=5, avoid=None, z=31):
    """등음선 dB 숫자(앱 화면, 2026-11-14): 선을 끊지 않고 흰 테두리 글자로 얹음. 건물·노면(avoid 도형) 위이거나,
    이미 놓인 숫자와 지도 폭의 gap 배 안쪽이거나, 같은 값이 per_level 개를 넘으면 뺌(골목의 작은 고리마다 숫자가
    붙어 어지럽지 않게). 반환: 남긴 글자 목록"""
    from shapely.geometry import Point
    from shapely.prepared import prep
    texts = ax.clabel(cs, inline=False, fontsize=6.5, fmt="%d", colors="#222222")
    av = prep(avoid) if avoid is not None and not avoid.is_empty else None
    d = gap * (bbox[2] - bbox[0])
    kept, pts, count = [], [], {}
    for t in texts:
        x, y = t.get_position()
        lv = t.get_text()
        if (not (bbox[0] < x < bbox[2] and bbox[1] < y < bbox[3]) or count.get(lv, 0) >= per_level
                or any(math.hypot(x - a, y - b) < d for a, b in pts) or (av is not None and av.contains(Point(x, y)))):
            t.remove()
            continue
        pts.append((x, y))
        count[lv] = count.get(lv, 0) + 1
        t.set_zorder(z)
        t.set_gid("contour_label")
        t.set_path_effects([pe.withStroke(linewidth=2.2, foreground="white")])
        kept.append(t)
    return kept


def draw_grid_compact(fig, C, cfg, key, h, G=None, note="", labels=False):
    """좁은 창(데스크톱 화면의 격자 + 3D 나란히 보기)용 지도만 크게: draw_grid 와 같은 지도 레이어(등음선 채움·
    건물·간선 노면·신호교차로·도로명·방위표·축척)에 범례는 제목 아래 가로 띠로(지도를 가리지 않게). 정보란·계산 조건은 없음
    (보고서 그림·지도 저장은 draw_grid). 앱 화면(2026-11-14): labels=True 면 등음선에 dB 숫자. 반환: 지도 축(ax)"""
    bbox = grid_bbox(cfg)
    gx, gy, step, Am, A = grid_array(C, key, h, bbox, G=G)       # G: 화면에서 조건을 걸어 다시 합친 표(성분 고르기)
    ax = fig.add_axes([0.005, 0.005, 0.99, 0.905])
    fig.text(0.01, 0.975, f"격자 소음지도 · {KEYNAME[key]} · 높이 {h:g} m · 간격 {step:g} m"
             f"{refine_note(C, h) if G is None else ''}{note}",
             fontsize=11, weight="bold", va="center", color="#222222")
    cmap, norm = din_cmap()
    ax.contourf(gx, gy, Am, levels=[-200] + DIN_EDGES + [200], cmap=cmap, norm=norm, zorder=1, antialiased=True)
    cs = ax.contour(gx, gy, Am, levels=DIN_EDGES, colors="#333", linewidths=0.25, alpha=0.6, zorder=2)
    if labels:                                                       # 숫자는 건물·간선 노면 밖에만
        v = box(*bbox)
        avoid = C.bld[C.bld.intersects(v)].geometry.union_all().union(C.road_band.intersection(v))
        contour_labels(ax, cs, bbox, avoid=avoid)
    base_layers(ax, C, bbox)
    sig = C.J[C.J.type == "signal"].groupby("cluster")[["x", "y"]].mean()
    ax.scatter(sig.x, sig.y, marker="o", s=50, fc="white", ec="k", lw=1.1, zorder=35)
    road_labels(ax, C, bbox)
    frame(ax, bbox); north_scale(ax, bbox)
    ax.set_anchor("N")                                                # 세로로 긴 창에서 지도를 위쪽에 붙임
    ax.set_xticklabels([]); ax.set_yticklabels([])                    # 좁은 창에서는 좌표 글자를 뺌
    ax.set_xlabel(""); ax.set_ylabel("")                            # geopandas 가 붙이는 좌표축 이름 지움
    legend_strip(fig, [0.01, 0.915, 0.98, 0.04])                     # 범례: 제목 아래 가로 띠(지도를 가리지 않게)
    return ax


def legend_strip(fig, rect, road=True):
    """가로 범례 띠(DIN 5 dB 등급 11칸, 칸 사이에 경계값). 데스크톱 화면의 격자·3D 파사드가 같은 색표를 씀"""
    lg = fig.add_axes(rect)
    n = len(DIN_COLS)
    lg.set_xlim(-1.6, n + 0.1); lg.set_ylim(0, 1); lg.axis("off")
    lg.text(-1.55, 0.72, "dB(A)", fontsize=7.5, weight="bold", va="center")
    for i, c in enumerate(DIN_COLS):
        lg.add_patch(Rectangle((i, 0.45), 1, 0.55, fc=c, ec="white", lw=0.4))
    for i, e in enumerate(DIN_EDGES):
        lg.text(i + 1, 0.18, f"{e}", fontsize=7, ha="center", va="center")
    if road:
        lg.add_patch(Rectangle((-1.55, 0.02), 0.5, 0.3, fc=ROAD_FC, ec="0.5", lw=0.4))
        lg.text(-0.95, 0.17, "노면", fontsize=6.5, va="center")
    return lg





# ------------------------------------------------------------------ 3) 파사드 색 띠
def facade_segments(FR, F, key, off=1.1):
    """벽면 결과(facade_result) → 벽면 구간별 선분(x1, y1, x2, y2)과 층별 최댓값 L.
    off: 벽에서 바깥으로 띄우는 거리(m). 보고서 HQ_facade 와 데스크톱 화면(sori_qt)의 격자 위 겹쳐 보기가 같이 씀"""
    W = FR.groupby(["fid", "piece"]).agg(a=("a", "first"), b=("b", "first"), L=(key, "max"),
                                         bid=("bid", "first")).reset_index()
    f = F.loc[W.fid]
    W["x1"] = f.x1.values + W.a * (f.x2.values - f.x1.values) + off * f.nx.values
    W["y1"] = f.y1.values + W.a * (f.y2.values - f.y1.values) + off * f.ny.values
    W["x2"] = f.x1.values + W.b * (f.x2.values - f.x1.values) + off * f.nx.values
    W["y2"] = f.y1.values + W.b * (f.y2.values - f.y1.values) + off * f.ny.values
    return W


def _facade_pieces(C, key):
    FR = pd.read_csv(os.path.join(C.out, "facade_result.csv"))
    F = pd.read_csv(os.path.join(C.out, "facades.csv"), index_col="fid")
    return FR, facade_segments(FR, F, key)


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
    return save(fig, C.out, f"HQ_facade_{key}", cfg, ("facade", key, ""))


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
    return save(fig, C.out, f"HQ_building_{key}", cfg, ("facade", key, "_building"))


def main(cfg):
    files = []
    hs = cfg["receivers"]["grid"]["heights"]
    for key in ("LrT", "LrN"):
        files += grid_map(cfg, key, hs[0])
        if os.path.exists(os.path.join(cfg["project"]["output_dir"], "facade_result.csv")):
            files += facade_bands(cfg, key)
            files += building_map(cfg, key)
    return files
