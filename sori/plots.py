# -*- coding: utf-8 -*-
"""결과 시각화: 격자 소음지도(높이별, 격자/등음선), 파사드 평면도, 파사드 3D
수정사항: 범례가 수음점 위에 그려지도록 zorder 지정, 3D 라벨 열 중복 제거, 한글 글꼴 자동 탐색
※ Claude(Anthropic의 AI)를 활용하여 제작하였음."""
import logging
import math
import os

import numpy as np
import pandas as pd
import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.colors import ListedColormap, BoundaryNorm, to_rgb
from matplotlib.patches import Rectangle
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
from shapely.geometry import box, Point
from shapely.geometry.polygon import orient

from . import AI_NOTICE
from .config import grid_bbox, center, facade_reach
from .bldz import roof_fn
from .propagation import SHORT, result_model
from .resources import resource_path

log = logging.getLogger("sori")

COLS = ["#007800", "#2e8b00", "#5aa800", "#9ac800", "#cfe100", "#f5d300",
        "#eba700", "#e07a00", "#d23c00", "#c80000", "#960a28", "#00007f"]
KO_FONTS = ["Noto Sans KR", "Malgun Gothic", "AppleGothic", "NanumGothic", "Noto Sans CJK KR",
            "Noto Sans CJK JP", "UnDotum"]
_font_logged = None


def _register_bundled_fonts():
    """sori/fonts 의 Noto Sans KR(SIL OFL 1.1)을 matplotlib 에 등록 — OS 와 관계없이 같은 글꼴"""
    have = {f.fname for f in font_manager.fontManager.ttflist}
    for p in sorted(resource_path("sori", "fonts").glob("*.ttf")):
        if str(p) not in have:
            font_manager.fontManager.addfont(str(p))


def set_font(name=None):
    global _font_logged
    _register_bundled_fonts()
    have = {f.name for f in font_manager.fontManager.ttflist}
    chosen = None
    for f in ([name] if name else []) + KO_FONTS:
        if f in have:
            plt.rcParams["font.family"] = f
            chosen = f
            break
    plt.rcParams["axes.unicode_minus"] = False
    if chosen != _font_logged:
        if chosen:
            log.info("그림 글꼴: %s", chosen)
        else:
            log.warning("한글 글꼴을 찾지 못함 — 그림의 한글이 깨질 수 있음(plots.font 설정 확인)")
        _font_logged = chosen


def lo_from_max(vmax, step=2):
    """색 범위를 최댓값 기준으로: 마지막 유한 구간의 상한이 최댓값을 덮도록 (포화 방지)"""
    return int(step * math.ceil(vmax / step) - step * (len(COLS) - 2))


def classes(lo, step):
    edges = [lo + i * step for i in range(len(COLS) - 1)]
    norm = BoundaryNorm([-200] + edges + [200], len(COLS))
    labels = [(None, edges[0])] + list(zip(edges[:-1], edges[1:])) + [(edges[-1], None)]
    return ListedColormap(COLS), norm, labels


def legend_box(ax, title, sub, labels):
    n = len(COLS)
    bx = ax.inset_axes([0.012, 0.012, 0.15, 0.06 + 0.026 * n], zorder=50)   # 데이터 위에 표시
    bx.set_xlim(0, 1); bx.set_ylim(0, n + 3.2); bx.axis("off")
    bx.add_patch(Rectangle((0, 0), 1, n + 3.2, fc="white", ec="k", lw=0.8))
    bx.text(0.08, n + 2.3, title, fontsize=10, va="center")
    bx.text(0.08, n + 1.3, sub, fontsize=8, va="center")
    for i, (c, (lo, hi)) in enumerate(zip(COLS, labels)):
        y = n - 1 - i + 0.1
        bx.add_patch(Rectangle((0.42, y), 0.2, 0.9, fc=c, ec="none"))
        if lo is not None:
            bx.text(0.40, y + 0.45, f"{lo:g} <", fontsize=7.5, ha="right", va="center")
        if hi is not None:
            bx.text(0.66, y + 0.45, f"<= {hi:g}", fontsize=7.5, va="center")


def footer(fig):
    fig.text(0.995, 0.003, AI_NOTICE, ha="right", va="bottom", fontsize=7, color="0.35")


def nice_len(width):
    for L in (500, 200, 100, 50, 20, 10):
        if L <= 0.22 * width:
            return L
    return 10


def scale_box(ax, bbox, text):
    """축척 막대 + 설명 상자 (도면 크기에 비례)"""
    W = bbox[2] - bbox[0]; H = bbox[3] - bbox[1]
    L = nice_len(W); u = H / 100.0
    x0 = bbox[2] - L - 0.06 * W; y0 = bbox[1] + 2.0 * u
    ax.add_patch(Rectangle((x0 - 0.02 * W, bbox[1] + 0.8 * u), L + 0.07 * W, 7.5 * u, fc="w", ec="k", zorder=40))
    ax.text(x0, y0 + 4.6 * u, text, fontsize=9, zorder=41)
    for i in range(4):
        ax.add_patch(Rectangle((x0 + i * L / 4, y0), L / 4, 0.8 * u, fc="k" if i % 2 == 0 else "w",
                               ec="k", lw=0.6, zorder=41))
    for i in range(5):
        ax.text(x0 + i * L / 4, y0 + 1.4 * u, f"{i * L / 4:g}", ha="center", fontsize=7, zorder=41)
    ax.text(x0 + L + 0.01 * W, y0, "m", fontsize=7, zorder=41)


def load_layers(out, main_min_lanes):
    bld = gpd.read_file(os.path.join(out, "buildings.gpkg"))
    rd = gpd.read_file(os.path.join(out, "roads.gpkg"))
    rd = rd[rd["lanes"] >= main_min_lanes]
    band = gpd.GeoSeries([g.buffer(max(w, 6) / 2, cap_style=2) for g, w in
                          zip(rd.geometry, rd["width"].fillna(6))], crs=bld.crs).union_all()
    S = pd.read_csv(os.path.join(out, "sources.csv"))
    J = pd.read_csv(os.path.join(out, "junctions_used.csv"))
    return bld, gpd.GeoDataFrame(geometry=[band], crs=bld.crs), S, J


def base_map(ax, bld, rarea, S, bbox):
    v = box(*bbox)
    rarea.clip(v).plot(ax=ax, color="#dcdcdc", ec="none", lw=0, zorder=3)
    m = S[S.main]
    ax.scatter(m.x, m.y, s=0.5, c="red", zorder=4, lw=0)
    bld[bld.intersects(v)].plot(ax=ax, fc="#dcdcdc", ec="#2030d0", lw=0.45, zorder=5)
    ax.set_xlim(bbox[0], bbox[2]); ax.set_ylim(bbox[1], bbox[3])
    ax.set_xticks([]); ax.set_yticks([]); ax.set_aspect("equal")
    ax.set_xlabel(""); ax.set_ylabel("")
    for s in ax.spines.values():
        s.set_linewidth(2)


def grid_maps(cfg):
    out = cfg["project"]["output_dir"]; gcfg = cfg["receivers"]["grid"]
    set_font(cfg["plots"]["font"])
    bld, rarea, S, J = load_layers(out, cfg["model"]["main_min_lanes"])
    G = pd.read_csv(os.path.join(out, "grid_result.csv"))
    d = np.load(os.path.join(out, "model_rasters.npz"))
    gx, gy = d["gx"], d["gy"]; step = gcfg["step"]
    cmap, norm, labels = classes(30, 5)
    bbox = grid_bbox(cfg)
    Jsig = J[J["type"] == "signal"].groupby("cluster")[["x", "y"]].mean()
    files = []
    for h in sorted(G.h.unique()):
        Gh = G[G.h == h]
        for key, nm in (("LrT", "LrT (주간 06-22시)"), ("LrN", "LrN (야간 22-06시)")):
            A = np.full((len(gy), len(gx)), np.nan)
            ix = np.round((Gh.x - gx[0]) / step).astype(int); iy = np.round((Gh.y - gy[0]) / step).astype(int)
            A[iy, ix] = Gh[key].values
            for style in ("grid", "isophone"):
                fig, ax = plt.subplots(figsize=(12.5, 13))
                if style == "grid":
                    ex = np.r_[gx - step / 2, gx[-1] + step / 2]; ey = np.r_[gy - step / 2, gy[-1] + step / 2]
                    ax.pcolormesh(ex, ey, np.ma.masked_invalid(A), cmap=cmap, norm=norm,
                                  edgecolors=(1, 1, 1, 0.25), linewidth=0.15, zorder=1)
                else:
                    from scipy.ndimage import distance_transform_edt
                    if np.isnan(A).all():
                        plt.close(fig); continue
                    idx = distance_transform_edt(np.isnan(A), return_distances=False, return_indices=True)
                    Af = A[tuple(idx)]
                    lv = [-200] + list(range(30, 81, 5)) + [200]
                    ax.contourf(gx, gy, Af, levels=lv, cmap=cmap, norm=norm, zorder=1)
                    ax.contour(gx, gy, Af, levels=list(range(30, 81, 5)), colors="k", linewidths=0.3, zorder=2)
                base_map(ax, bld, rarea, S, bbox)
                if len(Jsig):
                    ax.scatter(Jsig.x, Jsig.y, marker="o", s=60, fc="w", ec="k", zorder=8)
                legend_box(ax, "Noise level", f"{key} in dB(A)", labels)
                scale_box(ax, bbox, f"Grid {step:g} m, h = {h:g} m")
                ax.set_title(f"{cfg['project']['name']} 격자 소음지도 ({SHORT[result_model(cfg, out).name]}) — {nm}, h={h:g} m"
                             f"{'  [격자]' if style == 'grid' else '  [등음선]'}", fontsize=12)
                footer(fig); fig.tight_layout()
                fn = os.path.join(out, f"grid_{key}_h{h:g}m_{style}.png")
                fig.savefig(fn, dpi=cfg["plots"]["dpi"]); plt.close(fig); files.append(fn)
    return files


def facade_plan(cfg):
    out = cfg["project"]["output_dir"]
    set_font(cfg["plots"]["font"])
    bld, rarea, S, _ = load_layers(out, cfg["model"]["main_min_lanes"])
    FR = pd.read_csv(os.path.join(out, "facade_result.csv"))
    W = FR.groupby(["fid", "piece"]).agg(x=("x", "first"), y=("y", "first"), LrT=("LrT", "max"),
                                         LrN=("LrN", "max")).reset_index()
    cx, cy = center(cfg); r = facade_reach(cfg) + 25
    bbox = (cx - r, cy - r, cx + r, cy + r)
    files = []
    for key in ("LrT", "LrN"):
        lo = lo_from_max(np.nanmax(W[key]))
        cmap, norm, labels = classes(lo, 2)
        fig, ax = plt.subplots(figsize=(12.5, 12.5))
        base_map(ax, bld, rarea, S, bbox)
        ax.scatter(W.x, W.y, c=W[key], cmap=cmap, norm=norm, s=16, marker="s", ec="k", lw=0.25, zorder=9)
        legend_box(ax, "Noise level", f"{key} in dB(A) (층별 최댓값)", labels)
        scale_box(ax, bbox, "외벽 앞 5 cm")
        ax.set_title(f"{cfg['project']['name']} 파사드 소음지도(평면) — {key}, 외벽 앞 5 cm, 층별 최댓값", fontsize=12)
        footer(fig); fig.tight_layout()
        fn = os.path.join(out, f"facade_plan_{key}.png")
        fig.savefig(fn, dpi=cfg["plots"]["dpi"]); plt.close(fig); files.append(fn)
    return files


def bid_label_points(B, focus, n, gap=9.0):
    """건물 번호를 붙일 건물(앱 3D 파사드와 격자 지도가 같이 씀): 대상 focus + 대상에서 가까운 순 n 개,
    평면에서 gap(m) 안에 이미 번호가 있으면 뺌. B: 보이는 범위 안 건물(bid, ztop, geometry).
    반환 [(bid, x, y, ztop, 대상 여부)] — 대상이 B 에 없으면 빈 목록"""
    if focus is None or not (B.bid == focus).any():
        return []
    fb = B[B.bid == focus].iloc[0]
    fc = fb.geometry.representative_point()
    out = [(int(focus), fc.x, fc.y, float(fb.ztop), True)]
    P = B.geometry.representative_point()
    d = np.hypot(P.x.values - fc.x, P.y.values - fc.y)
    for k in np.argsort(d):
        if len(out) > int(n):
            break
        b, p = B.iloc[k], P.iloc[k]
        if int(b.bid) == int(focus) or any(np.hypot(p.x - o[1], p.y - o[2]) < gap for o in out):
            continue
        out.append((int(b.bid), p.x, p.y, float(b.ztop), False))
    return out


def wall_shade(nx, ny, azim, base, amp):
    """벽 방향별 밝기 배율(앱 3D 파사드 명암, 2026-11-14). 빛은 그릴 때 카메라 방위 + 40° 쪽에서 — 어느 방향에서
    그려도 면마다 밝기가 달라 모양이 읽힘(마우스로 돌리는 동안은 고정, 다시 그릴 때 맞춤).
    base + amp·(0.5 + 0.5·cos(벽 법선, 빛 방향))"""
    a = np.radians(azim + 40.0)
    d = np.asarray(nx) * np.cos(a) + np.asarray(ny) * np.sin(a)
    return base + amp * (0.5 + 0.5 * d)


def loud_azimuth(FR, F, bid, key, offset=-30.0):
    """대상 건물에서 가장 시끄러운 벽(벽면 수음점 값 최댓값)이 정면에 보이는 카메라 방위각(도, matplotlib azim).
    정면 그대로면 납작하게 보이므로 offset 만큼 비껴 봄. 벽면 결과가 없으면 None"""
    sub = FR[(FR.bid == bid) & FR[key].notna()]
    if not len(sub):
        return None
    f = F.loc[sub.loc[sub[key].idxmax(), "fid"]]
    return float(np.degrees(np.arctan2(f.ny, f.nx)) + offset)


def ground_cells(grid, bounds, z, cmap, norm, max_cells=90):
    """3D 파사드 바닥 소음지도(앱, 2026-11-14): 격자 결과 A 의 bounds(xmin, ymin, xmax, ymax) 안 칸을 높이 z 의
    수평 사각형으로. 한 변 max_cells 칸이 넘으면 몇 칸씩 묶어 평균(에너지 평균이 아닌 값 평균 — 색 등급 보기용).
    값 없는 칸(건물 안·범위 밖)은 그리지 않음 → 밑의 지면 색이 보임"""
    A, (x0, x1, y0, y1) = grid
    ny, nx = A.shape
    dx, dy = (x1 - x0) / nx, (y1 - y0) / ny
    i0, i1 = max(int((bounds[0] - x0) // dx), 0), min(int(np.ceil((bounds[2] - x0) / dx)), nx)
    j0, j1 = max(int((bounds[1] - y0) // dy), 0), min(int(np.ceil((bounds[3] - y0) / dy)), ny)
    sub = A[j0:j1, i0:i1]
    k = max(1, int(np.ceil(max(sub.shape + (1,)) / max_cells)))
    polys, cols = [], []
    for j in range(0, sub.shape[0], k):
        for i in range(0, sub.shape[1], k):
            blk = sub[j:j + k, i:i + k]
            if not np.isfinite(blk).any():
                continue
            v = float(np.nanmean(blk))
            xa, ya = x0 + (i0 + i) * dx, y0 + (j0 + j) * dy
            xb, yb = xa + blk.shape[1] * dx, ya + blk.shape[0] * dy
            polys.append([(xa, ya, z), (xb, ya, z), (xb, yb, z), (xa, yb, z)])
            cols.append(cmap(norm(v)))
    return Poly3DCollection(polys, facecolors=cols, edgecolors="none", antialiased=False, zorder=0, gid="ground_grid")


def draw_facade_3d(ax, bld, FR, F, T, key, cmap, norm, view, fh, elev, azim, label_bids=None, colors=None,
                   focus=None, bid_labels=0, ghost=False, label_box=False, label_gap=18.0, shade=False, ground_grid=None):
    """3D 파사드 소음지도 그리기(그림 파일 facade_3d 와 데스크톱 화면 sori_qt 가 같이 씀)
    T: 층별 색을 칠할 건물 번호 집합, label_bids: 층별 값 글자를 붙일 건물(None 이면 T 전체)
    view: 보이는 범위(shapely 도형), fh: 층고(m), elev·azim: 시점(도). 계산 없이 결과 표만 그림.
    colors: 둘레 색 {wall, roof, ground, sky} 바꾸기(앱 화면은 회색 계열, 기본값은 보고서 그림 색)
    앱 화면용(기본값이면 보고서 그림과 같음, 2026-11-14):
      focus: 확인 대상 건물 번호 — 지붕 위 번호를 빨간 상자로
      bid_labels: 지붕 위 건물 번호를 붙일 주변 건물 수(focus 에서 가까운 순, 0 = 번호 없음)
      ghost: focus 밖 건물을 투명하게(옅은 윤곽선만, 벽면 색 없음) — 대상 건물만 보기
      label_box: 층별 값 글자에 흰 바탕 상자, label_gap: 값 글자 열 사이 최소 거리(m)
      shade: 벽 방향별 명암(wall_shade) — 회색 벽은 0.62–1.0, 소음 색 벽은 0.84–1.0(등급 색을 헷갈리지 않게 약하게)
      ground_grid: (A, (xmin, xmax, ymin, ymax)) 격자 결과 — 바닥을 격자 소음지도 색으로(값 없는 칸 = 지면 색)"""
    C = {**dict(wall="#cfd0f7", roof="#f7c97f", ground="#9ac09a", sky="#8fd3fe"), **(colors or {})}
    B = bld[bld.intersects(view)]
    if ghost and focus is not None:                  # 주변 건물은 색 없이 윤곽만 → 층별 색은 대상 건물만
        T = {b for b in T if b == focus}
    lab_set = T if label_bids is None else set(label_bids)
    az, el = np.radians(azim), np.radians(elev)
    cam = np.array([np.cos(el) * np.cos(az), np.cos(el) * np.sin(az)])
    polys, cols = [], []
    gpolys = []                                      # 투명하게 그릴 주변 건물 면
    ax.set_autoscale_on(False)                       # 범위는 아래에서 직접 정함(자동 범위가 NaN·Inf 로 튀어 화면이 꺼지는 일 방지)
    z0min = B.z0.min(); g = view.bounds
    ground = Poly3DCollection([[(g[0], g[1], z0min - 0.2), (g[2], g[1], z0min - 0.2), (g[2], g[3], z0min - 0.2),
                                (g[0], g[3], z0min - 0.2)]], facecolors=C["ground"], edgecolors="none", zorder=0)
    ax.add_collection3d(ground, autolim=False)                      # 지면은 항상 맨 뒤(깊이 정렬 오류 방지)
    zfloor = z0min - 0.2       # 벽 아래 끝 = 바닥판. 경사지에서 z0 가 높은 건물이 떠 보이지 않게 받침 벽으로(그리기만, 2026-11-20)
    if ground_grid is not None:
        ax.add_collection3d(ground_cells(ground_grid, g, z0min - 0.1, cmap, norm), autolim=False)
    for b in B.itertuples():
        if b.bid in T:
            continue
        c = list(orient(b.geometry, 1.0).exterior.coords)
        rf = roof_fn(b)                              # 꼭짓점별 지붕(지형 따라가기). 예전 결과는 평지붕 ztop
        out = gpolys if ghost and b.bid != focus else polys
        for (x1, y1), (x2, y2) in zip(c[:-1], c[1:]):
            out.append([(x1, y1, zfloor), (x2, y2, zfloor), (x2, y2, rf(x2, y2)), (x1, y1, rf(x1, y1))])
            if out is polys:
                if shade:                            # 반시계 외곽선의 바깥 법선 = (dy, −dx)
                    ln = math.hypot(x2 - x1, y2 - y1) or 1.0
                    k = float(wall_shade((y2 - y1) / ln, -(x2 - x1) / ln, azim, 0.62, 0.38))
                    cols.append(tuple(min(1.0, v * k) for v in to_rgb(C["wall"])))
                else:
                    cols.append(C["wall"])
        out.append([(x, y, rf(x, y)) for x, y in c[:-1]])
        if out is polys:
            cols.append(C["roof"])
    lab_cols = []                                    # 라벨 열 위치(겹침 방지)
    labels3d = []
    for bid in T:
        b = bld[bld.bid == bid].iloc[0]
        ring = list(orient(b.geometry, 1.0).exterior.coords)
        rf = roof_fn(b)
        polys.append([(x, y, rf(x, y)) for x, y in ring[:-1]])
        cols.append(C["roof"])
        gz = lambda x, y: rf(x, y) - float(b.H)      # noqa: E731  그 자리 지면(지붕 − H). 평지붕 결과면 z0
        for (x1, y1), (x2, y2) in zip(ring[:-1], ring[1:]):   # 받침 벽(바닥판 ~ 그 자리 지면) — 층별 색 벽 아래 틈 메움
            if max(gz(x1, y1), gz(x2, y2)) > zfloor + 0.05:
                polys.append([(x1, y1, zfloor), (x2, y2, zfloor), (x2, y2, gz(x2, y2)), (x1, y1, gz(x1, y1))])
                cols.append(C["wall"])
        sub = FR[FR.bid == bid]
        cand = F.loc[sub.fid.unique()]
        score = (cand.nx * cam[0] + cand.ny * cam[1]) * np.minimum(cand.L, 25)
        for fid in (score[score > 2.4].sort_values(ascending=False).index if bid in lab_set else []):
            f = F.loc[fid]
            mx, my = (f.x1 + f.x2) / 2, (f.y1 + f.y2) / 2
            if all(np.hypot(mx - a, my - c_) > label_gap for a, c_, _ in lab_cols):
                lab_cols.append((mx, my, (b.z0 + b.ztop) / 2))
                grp = sub[sub.fid == fid]
                pmid = grp.piece.unique()[len(grp.piece.unique()) // 2]
                for _, rr in grp[grp.piece == pmid].iterrows():
                    xa = f.x1 + (rr.a + rr.b) / 2 * (f.x2 - f.x1); ya = f.y1 + (rr.a + rr.b) / 2 * (f.y2 - f.y1)
                    labels3d.append((xa + 0.8 * f.nx, ya + 0.8 * f.ny, getattr(rr, "zg", b.z0) + (rr.floor - 0.5) * fh,
                                     f"{rr[key]:.1f}", len(lab_cols) - 1))
                if label_box:                        # 앱: 보이는 벽마다 한 열(대상 건물을 둘러 가며 값)
                    continue
                break
        for _, rr in sub.iterrows():
            f = F.loc[rr.fid]
            xa = f.x1 + rr.a * (f.x2 - f.x1); ya = f.y1 + rr.a * (f.y2 - f.y1)
            xb = f.x1 + rr.b * (f.x2 - f.x1); yb = f.y1 + rr.b * (f.y2 - f.y1)
            za = getattr(rr, "zg", b.z0) + (rr.floor - 1) * fh; zb = za + fh   # 층 바닥 = 수음점 지면
            polys.append([(xa, ya, za), (xb, yb, za), (xb, yb, zb), (xa, ya, zb)])
            c_ = cmap(norm(rr[key]))
            if shade:
                k = float(wall_shade(f.nx, f.ny, azim, 0.84, 0.16))
                c_ = (c_[0] * k, c_[1] * k, c_[2] * k, c_[3])
            cols.append(c_)
    if gpolys:                                       # 주변 건물: 거의 투명한 면 + 옅은 윤곽선(대상 건물보다 먼저)
        ax.add_collection3d(Poly3DCollection(gpolys, facecolors=(0.85, 0.85, 0.88, 0.06),
                                             edgecolors=(0.35, 0.35, 0.40, 0.35), linewidths=0.4, zorder=1),
                            autolim=False)
    ax.add_collection3d(Poly3DCollection(polys, facecolors=cols, edgecolors=(0.3, 0.3, 0.5, 0.25),
                                         linewidths=0.2, zorder=2), autolim=False)
    ax.set_xlim(g[0], g[2]); ax.set_ylim(g[1], g[3])
    zr = max(B.ztop.max() - z0min + 5, 40)
    ax.set_zlim(z0min, z0min + zr)
    ax.set_box_aspect((1, 1, zr / (g[2] - g[0])), zoom=1.45)
    ax.view_init(elev=elev, azim=azim); ax.set_axis_off()
    ax.set_facecolor(C["sky"])
    bids = bid_label_points(B, focus, bid_labels) if bid_labels or focus is not None else []
    if label_box:                                    # 앱: 겹침을 화면 좌표로 판단(2026-11-14 — 평면 거리로는 비스듬한 시점에서
        labels3d, bids = _screen_declutter(ax, labels3d, lab_cols, bids, B, focus)   # 숫자 열·주변 번호가 겹쳤음)
    box = dict(boxstyle="round,pad=0.1", fc="white", ec="none", alpha=0.65) if label_box else None
    for x, y, z, t, _ in labels3d:
        ax.text(x, y, z, t, fontsize=7, ha="center", va="center", zorder=100, bbox=box,
                weight="bold" if label_box else "normal")
    for b_, x, y, zt, me in bids:
        if me:                                       # 건물 번호: 대상은 빨간 상자, 주변은 가까운 순 bid_labels 개
            ax.text(x, y, zt + 1.5, str(b_), fontsize=9, weight="bold", color="white", ha="center", va="bottom",
                    zorder=102, bbox=dict(boxstyle="round,pad=0.2", fc="#d00000", ec="white", lw=0.8))
        else:
            ax.text(x, y, zt + 1.0, str(b_), fontsize=6.5, color="#333333", ha="center", va="bottom", zorder=101,
                    bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="#888888", lw=0.4, alpha=0.85))


def _screen_declutter(ax, labels3d, lab_cols, bids, B, focus, col_px=40.0, box_px=(30.0, 13.0)):
    """3D 글자 겹침을 지금 시점의 화면 좌표(px)로 정리: 값 숫자 열은 화면 가로로 col_px 넘게 떨어진 열만,
    주변 건물 번호는 대상 건물이 화면에서 차지하는 사각형 안이거나 이미 놓인 번호와 겹치면 뺌(대상 번호는 늘)"""
    from mpl_toolkits.mplot3d import proj3d
    M = ax.get_proj()

    def px(x, y, z):
        X, Y, _ = proj3d.proj_transform(np.asarray(x, float), np.asarray(y, float), np.asarray(z, float), M)
        return ax.transData.transform(np.column_stack([np.atleast_1d(X), np.atleast_1d(Y)]))
    keep, xs = set(), []
    for i, (mx, my, zm) in enumerate(lab_cols):
        p = px(mx, my, zm)[0, 0]
        if all(abs(p - q) > col_px for q in xs):
            xs.append(p)
            keep.add(i)
    labels3d = [l for l in labels3d if l[4] in keep]
    if focus is None or not (B.bid == focus).any():
        return labels3d, bids
    fb = B[B.bid == focus].iloc[0]
    cx, cy = np.asarray(fb.geometry.exterior.coords).T
    P = px(np.r_[cx, cx], np.r_[cy, cy], np.r_[np.full(len(cx), fb.z0), np.full(len(cx), fb.ztop)])
    x0, y0 = P.min(axis=0) - 4
    x1, y1 = P.max(axis=0) + 4
    out, placed = [], []
    for b_, x, y, zt, me in bids:
        q = px(x, y, zt + 1.0)[0]
        if not me:
            if x0 <= q[0] <= x1 and y0 <= q[1] <= y1:
                continue
            if any(abs(q[0] - a) < box_px[0] and abs(q[1] - c) < box_px[1] for a, c in placed):
                continue
        placed.append(tuple(q))
        out.append((b_, x, y, zt, me))
    return labels3d, out

def facade_3d(cfg, key="LrN"):
    out = cfg["project"]["output_dir"]; pc = cfg["plots"]; mc = cfg["model"]
    set_font(pc["font"])
    bld = gpd.read_file(os.path.join(out, "buildings.gpkg"))
    FR = pd.read_csv(os.path.join(out, "facade_result.csv"))
    F = pd.read_csv(os.path.join(out, "facades.csv"), index_col="fid")
    cx, cy = center(cfg)
    targets = pc["targets_3d"]
    if not targets:   # 기본: 중심 반경 내 5층 이상 건물 중 최고레벨 상위 4개(없으면 전체에서)
        near = FR[np.hypot(FR.x - cx, FR.y - cy) < pc["radius_3d"]]
        agg = near.groupby("bid").agg(L=(key, "max"), fl=("floor", "max"))
        tall = agg[agg.fl >= 5]
        targets = list((tall if len(tall) >= 2 else agg).sort_values("L", ascending=False).index[:4])
    T = set(int(t) for t in targets)
    lo = lo_from_max(np.nanmax(FR[FR.bid.isin(T)][key]))
    cmap, norm, labels = classes(lo, 2)
    view = Point(cx, cy).buffer(pc["radius_3d"])
    fig = plt.figure(figsize=(14, 10.5))
    ax = fig.add_subplot(111, projection="3d", computed_zorder=False)
    draw_facade_3d(ax, bld, FR, F, T, key, cmap, norm, view, mc["floor_height"], pc["elev_3d"], pc["azim_3d"])
    fig.patch.set_facecolor("#8fd3fe")
    legend_box(ax, "Noise level", f"{key} in dB(A)", labels)
    ax.set_title(f"{cfg['project']['name']} 파사드 소음지도 3D — {key} (층별, 외벽 앞 5 cm)", fontsize=13)
    footer(fig)
    fig.subplots_adjust(0, 0, 1, 0.96)
    fn = os.path.join(out, f"facade_3d_{key}.png")
    fig.savefig(fn, dpi=pc["dpi"], facecolor=fig.get_facecolor()); plt.close(fig)
    return [fn]


def main(cfg):
    files = []
    if cfg["receivers"]["grid"]["enabled"] and os.path.exists(os.path.join(cfg["project"]["output_dir"], "grid_result.csv")):
        files += grid_maps(cfg)
    if os.path.exists(os.path.join(cfg["project"]["output_dir"], "facade_result.csv")):
        files += facade_plan(cfg)
        files += facade_3d(cfg, "LrT") + facade_3d(cfg, "LrN")
    return files
