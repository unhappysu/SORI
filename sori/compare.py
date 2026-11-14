# -*- coding: utf-8 -*-
"""
두 결과 폴더의 차이(대책 전후 등): 격자·벽면·점 수음점을 같은 위치끼리 맞춰 (비교 − 기준) 을 구함.
  - 값: Δ = 비교(new) − 기준(base) [dB]. 음수 = 낮아짐(방음벽 등 대책 효과)
  - 격자: x, y, h 가 같은 점(1 cm 반올림)만. 격자 설정(범위·간격·높이)이 다르면 겹치는 점만 비교
  - 벽면: fid, piece, floor 가 같은 점. 점 수음점: point_id
  - 결과: 비교 폴더에 diff_grid.csv · diff_facade.csv · diff_point.csv · diff_info.json, 그림 HQ_diff_*
  python -m sori diff -c config.yaml --base 기준결과폴더     (config 의 output_dir = 비교 폴더)
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import json
import logging
import os

import numpy as np
import pandas as pd

log = logging.getLogger("sori")

KEYS = ("LrT", "LrN")
# 차이 지도 등급 경계(dB)와 색: 파랑 = 낮아짐, 회색 = 변화 ±1 dB 안, 빨강 = 높아짐
DIFF_EDGES = [-10, -6, -3, -1, 1, 3, 6]
DIFF_COLS = ["#08306b", "#2171b5", "#6baed6", "#c6dbef", "#eeeeee", "#fcbba1", "#fb6a4a", "#a50f15"]
DIFF_LABELS = ["10 dB 넘게 낮아짐", "6 – 10 dB 낮아짐", "3 – 6 dB 낮아짐", "1 – 3 dB 낮아짐", "변화 ±1 dB 안",
               "1 – 3 dB 높아짐", "3 – 6 dB 높아짐", "6 dB 넘게 높아짐"]


def _read(out, name):
    p = os.path.join(str(out), name)
    return pd.read_csv(p) if os.path.exists(p) and os.path.getsize(p) > 5 else None


def _merge(a, b, on):
    keep = [k for k in KEYS if k in a.columns and k in b.columns]
    m = a[on + keep].merge(b[on + keep], on=on, suffixes=("_base", "_new"))
    for k in keep:
        m["d" + k] = m[k + "_new"] - m[k + "_base"]
    return m


def diff_grid(base_out, new_out):
    """격자 차이 표: x, y, h, LrT_base, LrT_new, dLrT, LrN_… (겹치는 점만). 한쪽에 격자 결과가 없으면 None"""
    a, b = _read(base_out, "grid_result.csv"), _read(new_out, "grid_result.csv")
    if a is None or b is None:
        return None
    on = ["_x", "_y", "_h"]
    for g in (a, b):                                   # 좌표를 cm 정수로 바꿔 맞춤(부동소수 오차로 어긋나지 않게)
        for c in ("x", "y", "h"):
            g["_" + c] = np.round(g[c] * 100).astype(np.int64)
    m = _merge(a, b, on)
    for i, c in enumerate(("x", "y", "h")):
        m.insert(i, c, m["_" + c] / 100.0)
    return m.drop(columns=on)


def diff_facade(base_out, new_out):
    a, b = _read(base_out, "facade_result.csv"), _read(new_out, "facade_result.csv")
    if a is None or b is None or not len(a) or not len(b):
        return None
    on = ["fid", "piece", "floor"]
    m = _merge(a, b, on)
    return m.merge(b[on + ["bid", "x", "y", "z"]], on=on)


def diff_point(base_out, new_out):
    a, b = _read(base_out, "point_result.csv"), _read(new_out, "point_result.csv")
    if a is None or b is None:
        return None
    return _merge(a, b, ["point_id"])


def stats(D, key="LrT"):
    """차이 요약(격자·벽면 공통): 점 수, 평균·최소·최대 Δ, 1·3·5 dB 넘게 낮아진 점 수, 1 dB 넘게 높아진 점 수"""
    d = D["d" + key].values
    d = d[np.isfinite(d)]
    if not len(d):
        return {"n": 0}
    return {"n": int(len(d)), "mean": round(float(d.mean()), 2), "min": round(float(d.min()), 2),
            "max": round(float(d.max()), 2), "lower_1dB": int((d < -1).sum()), "lower_3dB": int((d < -3).sum()),
            "lower_5dB": int((d < -5).sum()), "higher_1dB": int((d > 1).sum())}


def write(base_out, new_out):
    """차이 표를 비교 폴더(new_out)에 저장하고 요약(dict)을 돌려줌. 기준·비교의 전파 방법이 다르면 경고만 함"""
    info = {"base": str(base_out), "new": str(new_out), "definition": "Δ = 비교(new) − 기준(base) dB, 음수 = 낮아짐"}
    meth = []
    for o in (base_out, new_out):
        try:
            ri = json.load(open(os.path.join(str(o), "run_info.json"), encoding="utf-8"))
            meth.append(sorted(set((ri.get("outputs") or {}).values())))
        except (OSError, ValueError):
            meth.append(None)
    info["methods"] = {"base": meth[0], "new": meth[1]}
    if meth[0] and meth[1] and meth[0] != meth[1]:
        info["warning"] = f"전파 방법이 다름(기준 {meth[0]}, 비교 {meth[1]}) — 차이에 방법 차이가 섞임"
        log.warning(info["warning"])
    for name, fn in (("grid", diff_grid), ("facade", diff_facade), ("point", diff_point)):
        D = fn(base_out, new_out)
        if D is None or not len(D):
            continue
        D.to_csv(os.path.join(str(new_out), f"diff_{name}.csv"), index=False, encoding="utf-8-sig")
        if name == "grid":
            info[name] = {f"h{h:g}": {k: stats(g, k) for k in KEYS if "d" + k in g} for h, g in D.groupby("h")}
        else:
            info[name] = {k: stats(D, k) for k in KEYS if "d" + k in D}
    with open(os.path.join(str(new_out), "diff_info.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)
    return info


def summary_lines(info, key="LrT"):
    """요약 글(기록창·속성 창용)"""
    out = []
    for hk, s in (info.get("grid") or {}).items():
        s = s.get(key) or {}
        if s.get("n"):
            out.append(f"격자 {hk[1:]} m {key}: 평균 {s['mean']:+.2f} dB, 가장 많이 낮아진 곳 {s['min']:+.1f} dB, "
                       f"3 dB 넘게 낮아진 점 {s['lower_3dB']:,}/{s['n']:,}개, 1 dB 넘게 높아진 점 {s['higher_1dB']:,}개")
    s = (info.get("facade") or {}).get(key) or {}
    if s.get("n"):
        out.append(f"건물 벽면 {key}: 평균 {s['mean']:+.2f} dB, 최대 저감 {s['min']:+.1f} dB, "
                   f"3 dB 넘게 낮아진 벽면·층 {s['lower_3dB']:,}/{s['n']:,}개")
    s = (info.get("point") or {}).get(key) or {}
    if s.get("n"):
        out.append(f"점 수음점 {key}: {s['min']:+.1f} ~ {s['max']:+.1f} dB ({s['n']}점)")
    return out


# ---------------------------------------------------------------------- 그림
def read_barriers(out):
    """결과 폴더의 방음벽 조각(barriers_used.csv). 없으면 None"""
    return _read(out, "barriers_used.csv")


def plot_barriers(ax, B, lw=2.6, z=45):
    """방음벽 선(검정 바탕 + 자주색). B: x1, y1, x2, y2 열"""
    if B is None or not len(B):
        return
    from matplotlib.collections import LineCollection
    segs = np.stack([B[["x1", "y1"]].values, B[["x2", "y2"]].values], axis=1)
    ax.add_collection(LineCollection(segs, colors="#111111", linewidths=lw * 1.7, capstyle="round", zorder=z))
    ax.add_collection(LineCollection(segs, colors="#c2187a", linewidths=lw, capstyle="round", zorder=z + 1))


# 주야 차이(LrT − LrN) 등급: 0 ~ 12 dB, 2 dB 간격
DN_EDGES = [0, 2, 4, 6, 8, 10, 12]
DN_COLS = ["#5e4fa2", "#ffffcc", "#fee391", "#fec44f", "#fe9929", "#ec7014", "#cc4c02", "#8c2d04"]


def diff_strip(fig, rect, edges=None, cols=None, barrier=True):
    """가로 범례 띠(차이 등급)"""
    from matplotlib.patches import Rectangle
    edges = DIFF_EDGES if edges is None else edges
    cols = DIFF_COLS if cols is None else cols
    lg = fig.add_axes(rect)
    n = len(cols)
    lg.set_xlim(-1.9, n + 0.1); lg.set_ylim(0, 1); lg.axis("off")
    lg.text(-1.85, 0.72, "Δ dB", fontsize=7.5, weight="bold", va="center")
    for i, c in enumerate(cols):
        lg.add_patch(Rectangle((i, 0.45), 1, 0.55, fc=c, ec="white", lw=0.4))
    for i, e in enumerate(edges):
        lg.text(i + 1, 0.18, f"{e:+d}", fontsize=7, ha="center", va="center")
    if barrier:
        lg.plot([-1.85, -1.35], [0.17, 0.17], color="#c2187a", lw=2.4, solid_capstyle="round")
        lg.text(-1.25, 0.17, "방음벽", fontsize=6.5, va="center")
    return lg


def draw_diff(fig, C, cfg, D, key, h, title=None, edges=None, cols=None):
    """차이 지도(지도만 크게 + 위 가로 범례)를 fig 에 그림. C = maps_hq.Ctx(비교 폴더), D = diff_grid 표. 지도 축 반환.
    edges·cols 를 주면 그 등급·색(주야 차이 지도 DN_EDGES·DN_COLS 등), 기본은 전후 차이 등급"""
    from matplotlib.colors import BoundaryNorm, ListedColormap
    from .config import grid_bbox
    from . import maps_hq as M
    bbox = grid_bbox(cfg)
    G = D[np.isclose(D.h, h)].rename(columns={"d" + key: "_d"})
    gx, gy, step, Am, A = M.grid_array(C, "_d", h, bbox, G=G)
    ax = fig.add_axes([0.005, 0.005, 0.99, 0.905])
    fig.text(0.01, 0.975, title or f"차이 지도(비교 − 기준) · {M.KEYNAME[key]} · 높이 {h:g} m",
             fontsize=11, weight="bold", va="center", color="#222222")
    std = edges is None
    edges = DIFF_EDGES if edges is None else edges
    cols = DIFF_COLS if cols is None else cols
    cmap = ListedColormap(cols); lv = [-500] + list(edges) + [500]
    ax.contourf(gx, gy, Am, levels=lv, cmap=cmap, norm=BoundaryNorm(lv, len(cols)), zorder=1)
    if std:
        ax.contour(gx, gy, Am, levels=[-3], colors="#08306b", linewidths=0.5, zorder=2)
    M.base_layers(ax, C, bbox)
    M.road_labels(ax, C, bbox)
    M.frame(ax, bbox); M.north_scale(ax, bbox)
    plot_barriers(ax, read_barriers(C.out))
    ax.set_anchor("N")
    ax.set_xticklabels([]); ax.set_yticklabels([]); ax.set_xlabel(""); ax.set_ylabel("")
    diff_strip(fig, [0.01, 0.915, 0.98, 0.04], edges, cols, barrier=std)
    return ax


def diff_map(cfg, base_out, key="LrT", h=None):
    """차이 지도 그림 파일 HQ_diff_{key}_h{h}m.png/.pdf 를 비교 폴더(cfg output_dir)에 저장"""
    import matplotlib.pyplot as plt
    from . import AI_NOTICE
    from . import maps_hq as M
    from .plots import set_font
    D = diff_grid(base_out, cfg["project"]["output_dir"])
    if D is None or not len(D):
        raise ValueError("두 폴더에 겹치는 격자 결과가 없음")
    h = h if h is not None else float(sorted(D.h.unique())[0])
    C = M.Ctx(cfg)
    set_font()
    fig = plt.figure(figsize=(11.69, 11.0))
    s = stats(D[np.isclose(D.h, h)], key)
    draw_diff(fig, C, cfg, D, key, h,
              f"{cfg['project']['name']} 차이 지도(비교 − 기준) · {M.KEYNAME[key]} · 높이 {h:g} m · "
              f"평균 {s['mean']:+.2f} dB, 최대 저감 {s['min']:+.1f} dB")
    fig.text(0.995, 0.002, f"기준: {base_out}   ※ {AI_NOTICE}", ha="right", va="bottom", fontsize=6, color="0.4")
    return M.save(fig, C.out, f"HQ_diff_{key}_h{h:g}m")
