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
from matplotlib.colors import ListedColormap, BoundaryNorm
from matplotlib.patches import Rectangle
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
from shapely.geometry import box, Point
from shapely.geometry.polygon import orient

from . import AI_NOTICE
from .config import grid_bbox, center
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
                ax.set_title(f"{cfg['project']['name']} 격자 소음지도 (RLS-19 + ISO 9613-2) — {nm}, h={h:g} m"
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
    cx, cy = center(cfg); r = cfg["receivers"]["facade"]["radius"] + 25
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
    B = bld[bld.intersects(view)]
    elev, azim = pc["elev_3d"], pc["azim_3d"]
    az, el = np.radians(azim), np.radians(elev)
    cam = np.array([np.cos(el) * np.cos(az), np.cos(el) * np.sin(az)])
    fig = plt.figure(figsize=(14, 10.5))
    ax = fig.add_subplot(111, projection="3d", computed_zorder=False)
    polys, cols = [], []
    z0min = B.z0.min(); g = view.bounds
    ground = Poly3DCollection([[(g[0], g[1], z0min - 0.2), (g[2], g[1], z0min - 0.2), (g[2], g[3], z0min - 0.2),
                                (g[0], g[3], z0min - 0.2)]], facecolors="#9ac09a", edgecolors="none", zorder=0)
    ax.add_collection3d(ground)                      # 지면은 항상 맨 뒤(깊이 정렬 오류 방지)
    for b in B.itertuples():
        if b.bid in T:
            continue
        c = list(orient(b.geometry, 1.0).exterior.coords)
        for (x1, y1), (x2, y2) in zip(c[:-1], c[1:]):
            polys.append([(x1, y1, b.z0), (x2, y2, b.z0), (x2, y2, b.ztop), (x1, y1, b.ztop)]); cols.append("#cfd0f7")
        polys.append([(x, y, b.ztop) for x, y in c[:-1]]); cols.append("#f7c97f")
    lab_cols = []                                    # 라벨 열 위치(겹침 방지)
    labels3d = []
    for bid in T:
        b = bld[bld.bid == bid].iloc[0]
        polys.append([(x, y, b.ztop) for x, y in list(orient(b.geometry, 1.0).exterior.coords)[:-1]])
        cols.append("#f7c97f")
        sub = FR[FR.bid == bid]
        cand = F.loc[sub.fid.unique()]
        score = (cand.nx * cam[0] + cand.ny * cam[1]) * np.minimum(cand.L, 25)
        for fid in score[score > 2.4].sort_values(ascending=False).index:
            f = F.loc[fid]
            mx, my = (f.x1 + f.x2) / 2, (f.y1 + f.y2) / 2
            if all(np.hypot(mx - a, my - c_) > 18 for a, c_ in lab_cols):
                lab_cols.append((mx, my))
                grp = sub[sub.fid == fid]
                pmid = grp.piece.unique()[len(grp.piece.unique()) // 2]
                for _, rr in grp[grp.piece == pmid].iterrows():
                    xa = f.x1 + (rr.a + rr.b) / 2 * (f.x2 - f.x1); ya = f.y1 + (rr.a + rr.b) / 2 * (f.y2 - f.y1)
                    labels3d.append((xa + 0.8 * f.nx, ya + 0.8 * f.ny, b.z0 + (rr.floor - 0.5) * mc["floor_height"],
                                     f"{rr[key]:.1f}"))
                break
        for _, rr in sub.iterrows():
            f = F.loc[rr.fid]
            xa = f.x1 + rr.a * (f.x2 - f.x1); ya = f.y1 + rr.a * (f.y2 - f.y1)
            xb = f.x1 + rr.b * (f.x2 - f.x1); yb = f.y1 + rr.b * (f.y2 - f.y1)
            za = b.z0 + (rr.floor - 1) * mc["floor_height"]; zb = za + mc["floor_height"]
            polys.append([(xa, ya, za), (xb, yb, za), (xb, yb, zb), (xa, ya, zb)])
            cols.append(cmap(norm(rr[key])))
    ax.add_collection3d(Poly3DCollection(polys, facecolors=cols, edgecolors=(0.3, 0.3, 0.5, 0.25),
                                         linewidths=0.2, zorder=1))
    for x, y, z, t in labels3d:
        ax.text(x, y, z, t, fontsize=7, ha="center", va="center", zorder=100)
    ax.set_xlim(g[0], g[2]); ax.set_ylim(g[1], g[3])
    zr = max(B.ztop.max() - z0min + 5, 40)
    ax.set_zlim(z0min, z0min + zr)
    ax.set_box_aspect((1, 1, zr / (g[2] - g[0])), zoom=1.45)
    ax.view_init(elev=elev, azim=azim); ax.set_axis_off()
    ax.set_facecolor("#8fd3fe"); fig.patch.set_facecolor("#8fd3fe")
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
