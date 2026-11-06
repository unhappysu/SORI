# -*- coding: utf-8 -*-
"""
SORI 결과와 SoundPLAN 결과 비교(같은 입력·같은 기준으로 계산했다는 전제).
  python tools/compare_soundplan.py --sori <SORI 출력 폴더> [--sp-points 파일] [--sp-grid 파일] [--out 폴더]

SoundPLAN 결과 파일(어느 쪽이든 하나 이상)
  --sp-points  수음점 결과표: .csv / .txt / .xlsx. 좌표 열(x, y)이 있으면 가장 가까운 SORI 점 수음점과,
               없으면 이름 열(Name·ID 등)이 SORI point_id 와 같은 행끼리 맞춤
  --sp-grid    격자 결과: ESRI ASCII 격자(.asc) 또는 x y 값 표(.csv/.txt/.xlsx). SORI grid_result.csv 의
               가장 가까운 격자점과 맞춤(허용 거리 --tol, 기본 SORI 격자 간격의 절반)
  열 이름은 자동 인식(x: X·Rechtswert·Easting, y: Y·Hochwert·Northing, 주간: LrD·LrT·Lr,T·Tag·Day, 야간: LrN·Lr,N·
  Nacht·Night). 독일어 판의 소수 쉼표(54,3)와 ; 구분자도 읽음. 인식이 안 되면 --x-col --y-col --day-col --night-col.
  SoundPLAN 좌표가 SORI(EPSG:5186)와 평행 이동만 다르면 --offset dx dy (SoundPLAN 좌표에 더함).

결과(--out, 기본 <SORI 출력>/soundplan_compare)
  matched_points.csv / matched_grid.csv   맞춘 값과 차이(Δ = SORI − SoundPLAN)
  stats.json, 비교요약.md                   n, 평균 차이, 표준편차, RMSE, MAE, 최대 |Δ|, |Δ| ≤ 0.5/1/2/3 dB 비율,
                                            상관계수 r, 회귀(SORI = a·SoundPLAN + b), Bland–Altman 일치 한계
  그림: 산점도(1:1 선), Bland–Altman, 차이 분포, 격자 차이 지도
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import argparse
import csv
import io
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ALIASES = {
    "x": ("x", "xm", "xkoordinate", "rechtswert", "easting", "east", "xcoord", "xcoordinate"),
    "y": ("y", "ym", "ykoordinate", "hochwert", "northing", "north", "ycoord", "ycoordinate"),
    "day": ("lrd", "lrt", "lrtag", "lrday", "ld", "lday", "tag", "day", "lrdb", "lrdba", "lrddba", "lrtdba",
            "lmd", "lmt", "lrtagdba"),
    "night": ("lrn", "lrnacht", "lrnight", "ln", "lnight", "nacht", "night", "lrndba", "lmn", "lrnachtdba"),
    "id": ("name", "id", "pointid", "receiver", "receivername", "io", "immissionsort", "bezeichnung", "nr", "no"),
}
SORI_KEY = {"day": "LrT", "night": "LrN"}
PERIOD_KO = {"day": "주간", "night": "야간"}

# 색(dataviz 기본 팔레트): 단일 계열 파랑, 차이 지도는 파랑–회색–빨강 발산형, 글자·축은 무채색
C_SERIES = "#2a78d6"
C_POS, C_MID, C_NEG = "#e34948", "#f0efec", "#2a78d6"
C_INK, C_MUTED, C_GRID = "#0b0b0b", "#6b6b68", "#e4e3df"


def _norm(s):
    return re.sub(r"[^0-9a-z]", "", str(s).lower().replace("ä", "a").replace("ö", "o").replace("ü", "u"))


def read_table(path):
    """CSV/TXT(구분자·소수 쉼표 자동)·XLSX → DataFrame. 숫자로 바꿀 수 있는 열은 숫자로"""
    p = Path(path)
    if p.suffix.lower() in (".xlsx", ".xls"):
        df = pd.read_excel(p)
    else:
        raw = p.read_bytes()
        for enc in ("utf-8-sig", "cp949", "latin-1"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        lines = [ln for ln in text.splitlines() if ln.strip()]
        sample = "\n".join(lines[:50])
        try:
            sep = csv.Sniffer().sniff(sample, delimiters=";\t,").delimiter
        except csv.Error:
            sep = r"\s+"
        # 소수 쉼표: 구분자가 ; 또는 탭이고 숫자 사이 쉼표가 보이면
        dec = "," if sep in (";", "\t") and re.search(r"\d,\d", sample) else "."
        # 머리글 줄 찾기: 숫자가 아닌 칸이 가장 많은 앞쪽 줄
        df = pd.read_csv(io.StringIO("\n".join(lines)), sep=sep, decimal=dec, engine="python")
    for c in df.columns:
        if df[c].dtype == object:
            v = pd.to_numeric(df[c].astype(str).str.replace(",", ".", regex=False).str.strip(), errors="coerce")
            if v.notna().mean() > 0.8:
                df[c] = v
    return df


def read_asc(path):
    """ESRI ASCII 격자 → DataFrame(x, y, value) — 칸 중심 좌표"""
    head, n = {}, 0
    with open(path, encoding="latin-1") as f:
        lines = f.read().splitlines()
    while n < len(lines) and re.match(r"^[A-Za-z]", lines[n].strip()):
        k, v = lines[n].split()[:2]
        head[k.lower()] = float(v.replace(",", "."))
        n += 1
    nc, nr, cs = int(head["ncols"]), int(head["nrows"]), head["cellsize"]
    x0 = head["xllcenter"] if "xllcenter" in head else head["xllcorner"] + cs / 2
    y0 = head["yllcenter"] if "yllcenter" in head else head["yllcorner"] + cs / 2
    nod = head.get("nodata_value", -9999.0)
    Z = np.array(" ".join(lines[n:]).replace(",", ".").split(), float).reshape(nr, nc)
    xs = x0 + np.arange(nc) * cs
    ys = y0 + (nr - 1 - np.arange(nr)) * cs
    X, Y = np.meshgrid(xs, ys)
    m = Z != nod
    return pd.DataFrame({"x": X[m], "y": Y[m], "value": Z[m]})


def pick(df, key, override=None):
    if override:
        if override not in df.columns:
            raise SystemExit(f"열 '{override}' 이 없음. 있는 열: {list(df.columns)}")
        return override
    names = {_norm(c): c for c in df.columns}
    for a in ALIASES[key]:
        if a in names:
            return names[a]
    return None


def stats(d):
    """d: DataFrame(sori, sp). Δ = SORI − SoundPLAN"""
    d = d.dropna(subset=["sori", "sp"])
    n = len(d)
    if n == 0:
        return {"n": 0}
    e = (d.sori - d.sp).to_numpy()
    a = np.abs(e)
    out = {"n": n, "mean_diff": e.mean(), "sd_diff": e.std(ddof=1) if n > 1 else 0.0,
           "rmse": float(np.sqrt((e ** 2).mean())), "mae": a.mean(), "max_abs": a.max(),
           "p95_abs": float(np.percentile(a, 95)), "min_diff": e.min(), "max_diff": e.max(),
           **{f"within_{t}dB_pct": 100.0 * (a <= t + 1e-9).mean() for t in (0.5, 1, 2, 3)}}
    if n > 2 and d.sp.std() > 0:
        out["r"] = float(np.corrcoef(d.sp, d.sori)[0, 1])
        b, c = np.polyfit(d.sp, d.sori, 1)
        out["slope"], out["intercept"] = float(b), float(c)
    out["loa_low"] = out["mean_diff"] - 1.96 * out["sd_diff"]
    out["loa_high"] = out["mean_diff"] + 1.96 * out["sd_diff"]
    return {k: (round(float(v), 4) if isinstance(v, (float, np.floating)) else v) for k, v in out.items()}


def match_points(sori_out, path, a):
    S = pd.read_csv(Path(sori_out) / "point_result.csv", encoding="utf-8-sig")
    D = read_table(path)
    xc, yc = pick(D, "x", a.x_col), pick(D, "y", a.y_col)
    cols = {per: pick(D, per, getattr(a, f"{per}_col")) for per in ("day", "night")}
    if not any(cols.values()):
        raise SystemExit(f"수음점 파일에서 주간·야간 레벨 열을 찾지 못함: {list(D.columns)} — --day-col/--night-col 지정")
    if xc and yc:
        from scipy.spatial import cKDTree
        X = D[xc].to_numpy(float) + a.offset[0]; Y = D[yc].to_numpy(float) + a.offset[1]
        dist, k = cKDTree(S[["x", "y"]].to_numpy()).query(np.c_[X, Y])
        ok = dist <= a.point_tol
        M = pd.DataFrame({"point_id": np.where(ok, S.point_id.to_numpy()[k], None), "sp_x": X, "sp_y": Y,
                          "dist_m": np.round(dist, 3)})
        how = f"좌표(가장 가까운 SORI 점, {a.point_tol:g} m 이내)"
    else:
        ic = pick(D, "id", a.id_col)
        if not ic:
            raise SystemExit("수음점 파일에 좌표 열도 이름 열도 없음 — --x-col/--y-col 또는 --id-col 지정")
        M = pd.DataFrame({"point_id": D[ic].astype(str).str.strip()})
        how = f"이름 열 '{ic}'"
    for per, c in cols.items():
        if c:
            M[f"sp_{per}"] = D[c].to_numpy(float)
    M = M.merge(S[["point_id", "x", "y", "h", "LrT", "LrN"]], on="point_id", how="left")
    M = M.rename(columns={"LrT": "sori_day", "LrN": "sori_night"})
    return M, how, [p for p, c in cols.items() if c]


def match_grid(sori_out, path, a):
    from scipy.spatial import cKDTree
    G = pd.read_csv(Path(sori_out) / "grid_result.csv", encoding="utf-8-sig")
    if a.height is not None:
        G = G[np.isclose(G.h, a.height)]
    elif G.h.nunique() > 1:
        raise SystemExit(f"SORI 격자 높이가 여러 개({sorted(G.h.unique())}) — --height 지정")
    p = Path(path)
    if p.suffix.lower() == ".asc":
        D = read_asc(p)
        per = a.asc_period
        D = D.rename(columns={"value": f"sp_{per}"})
        pers = [per]
    else:
        D = read_table(p)
        xc, yc = pick(D, "x", a.x_col), pick(D, "y", a.y_col)
        if not (xc and yc):
            raise SystemExit(f"격자 파일에서 x·y 열을 찾지 못함: {list(D.columns)}")
        pers = []
        R = pd.DataFrame({"x": D[xc].to_numpy(float), "y": D[yc].to_numpy(float)})
        for per in ("day", "night"):
            c = pick(D, per, getattr(a, f"{per}_col"))
            if c:
                R[f"sp_{per}"] = D[c].to_numpy(float); pers.append(per)
        if not pers:
            raise SystemExit(f"격자 파일에서 주간·야간 열을 찾지 못함: {list(D.columns)}")
        D = R
    D["x"] = D.x + a.offset[0]; D["y"] = D.y + a.offset[1]
    step = float(np.median(np.diff(np.unique(G.x)))) if G.x.nunique() > 1 else 1.0
    tol = a.tol if a.tol is not None else step / 2 + 1e-6
    dist, k = cKDTree(G[["x", "y"]].to_numpy()).query(D[["x", "y"]].to_numpy())
    ok = dist <= tol
    M = D[ok].copy()
    M["dist_m"] = np.round(dist[ok], 3)
    g = G.iloc[k[ok]]
    M["sori_x"] = g.x.to_numpy(); M["sori_y"] = g.y.to_numpy()
    M["sori_day"] = g.LrT.to_numpy(); M["sori_night"] = g.LrN.to_numpy()
    info = {"sp_cells": int(len(D)), "matched": int(ok.sum()), "tol_m": tol, "sori_step_m": step}
    return M, info, pers


def _style(ax):
    ax.grid(True, color=C_GRID, lw=0.8); ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(C_MUTED)
    ax.tick_params(colors=C_MUTED, labelcolor=C_INK)


def figures(M, per, kind, dst, st):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm
    from sori.plots import set_font
    set_font()
    d = M.dropna(subset=[f"sori_{per}", f"sp_{per}"])
    sp, so = d[f"sp_{per}"].to_numpy(), d[f"sori_{per}"].to_numpy()
    e = so - sp
    tag = f"{kind}_{per}"
    ko = f"{'격자' if kind == 'grid' else '수음점'} {PERIOD_KO[per]}"
    small = len(d) <= 30
    files = []
    # 1) 산점도 + 1:1 선, ±1·±3 dB 띠
    fig, ax = plt.subplots(figsize=(5.2, 5.0), dpi=150)
    lo, hi = np.floor(min(sp.min(), so.min())) - 1, np.ceil(max(sp.max(), so.max())) + 1
    t = np.array([lo, hi])
    ax.fill_between(t, t - 3, t + 3, color=C_GRID, alpha=0.5, lw=0, label="±3 dB")
    ax.fill_between(t, t - 1, t + 1, color=C_GRID, alpha=1.0, lw=0, label="±1 dB")
    ax.plot(t, t, color=C_MUTED, lw=1.2, label="1:1")
    ax.scatter(sp, so, s=36 if small else 8, color=C_SERIES, edgecolor="white" if small else "none", lw=1.5,
               alpha=1.0 if small else 0.5, zorder=3)
    if small and "point_id" in d:                     # 이름표는 골라서: 측정점(P…)과 |Δ| 가 큰 3개
        big = set(d.point_id.to_numpy()[np.argsort(-np.abs(e))[:3]])
        for x, y, n in zip(sp, so, d.point_id):
            if not (str(n).startswith("P") or n in big):
                continue
            ax.annotate(str(n), (x, y), xytext=(5, -10), textcoords="offset points", fontsize=8, color=C_INK)
    ax.set_xlim(lo, hi); ax.set_ylim(lo, hi); ax.set_aspect("equal")
    ax.set_xlabel(f"SoundPLAN {PERIOD_KO[per]} [dB(A)]", color=C_INK)
    ax.set_ylabel(f"SORI {PERIOD_KO[per]} [dB(A)]", color=C_INK)
    ax.set_title(f"SORI 와 SoundPLAN — {ko} (n = {st['n']})", fontsize=10, color=C_INK, loc="left")
    txt = f"평균 Δ {st['mean_diff']:+.2f} dB, RMSE {st['rmse']:.2f} dB" + (f", r = {st['r']:.3f}" if "r" in st else "")
    ax.text(0.02, 0.98, txt, transform=ax.transAxes, va="top", fontsize=8, color=C_INK)
    ax.legend(loc="lower right", fontsize=8, frameon=False)
    _style(ax); fig.tight_layout()
    f = dst / f"산점도_{tag}.png"; fig.savefig(f); plt.close(fig); files.append(f)
    # 2) Bland–Altman
    fig, ax = plt.subplots(figsize=(6.0, 3.8), dpi=150)
    mean = (so + sp) / 2
    ax.scatter(mean, e, s=36 if small else 8, color=C_SERIES, edgecolor="white" if small else "none", lw=1.5,
               alpha=1.0 if small else 0.5, zorder=3)
    for v, lab, ls in ((st["mean_diff"], f"평균 {st['mean_diff']:+.2f}", "-"),
                       (st["loa_low"], f"−1.96 SD {st['loa_low']:+.2f}", "--"),
                       (st["loa_high"], f"+1.96 SD {st['loa_high']:+.2f}", "--")):
        ax.axhline(v, color=C_MUTED, lw=1.2, ls=ls)
        ax.text(1.0, v, " " + lab, transform=ax.get_yaxis_transform(), va="center", fontsize=8, color=C_INK)
    ax.axhline(0, color=C_INK, lw=0.8)
    ax.set_xlabel("두 프로그램 평균 [dB(A)]", color=C_INK); ax.set_ylabel("Δ = SORI − SoundPLAN [dB]", color=C_INK)
    ax.set_title(f"Bland–Altman — {ko}", fontsize=10, color=C_INK, loc="left")
    _style(ax); fig.tight_layout()
    f = dst / f"BlandAltman_{tag}.png"; fig.savefig(f); plt.close(fig); files.append(f)
    if not small:
        # 3) 차이 분포
        fig, ax = plt.subplots(figsize=(6.0, 3.4), dpi=150)
        w = max(0.1, round((e.max() - e.min()) / 40, 1))
        bins = np.arange(np.floor(e.min() / w) * w, e.max() + w, w)
        ax.hist(e, bins=bins, color=C_SERIES, edgecolor="white", lw=1.0)
        ax.axvline(0, color=C_INK, lw=0.8)
        ax.set_xlabel("Δ = SORI − SoundPLAN [dB]", color=C_INK); ax.set_ylabel("격자점 수", color=C_INK)
        ax.set_title(f"차이 분포 — {ko}", fontsize=10, color=C_INK, loc="left")
        _style(ax); fig.tight_layout()
        f = dst / f"차이분포_{tag}.png"; fig.savefig(f); plt.close(fig); files.append(f)
    if kind == "grid":
        # 4) 차이 지도(발산형: 파랑 = SORI 낮음, 회색 = 같음, 빨강 = SORI 높음)
        cmap = LinearSegmentedColormap.from_list("div", [C_NEG, C_MID, C_POS])
        lim = max(1.0, float(np.ceil(np.percentile(np.abs(e), 98))))
        asp = max(np.ptp(d.sori_y), 1.0) / max(np.ptp(d.sori_x), 1.0)
        fig, ax = plt.subplots(figsize=(6.4, min(8.0, max(3.0, 5.2 * asp + 1.0))), dpi=150)
        stp = float(np.median(np.diff(np.unique(d.sori_x)))) if d.sori_x.nunique() > 1 else 2.5
        sc = ax.scatter(d.sori_x, d.sori_y, c=e, cmap=cmap, norm=TwoSlopeNorm(0, -lim, lim), marker="s",
                        s=max(2.0, (72 * stp / max(np.ptp(d.sori_x), 1.0) * 5.0) ** 2), lw=0)
        cb = fig.colorbar(sc, ax=ax, fraction=0.04, pad=0.03)
        cb.set_label("Δ = SORI − SoundPLAN [dB]", color=C_INK); cb.outline.set_visible(False)
        ax.set_aspect("equal"); ax.set_xlabel("x [m]", color=C_INK); ax.set_ylabel("y [m]", color=C_INK)
        ax.set_title(f"차이 지도 — {ko}", fontsize=10, color=C_INK, loc="left")
        ax.ticklabel_format(useOffset=False, style="plain")
        _style(ax); ax.grid(False); fig.tight_layout()
        f = dst / f"차이지도_{tag}.png"; fig.savefig(f); plt.close(fig); files.append(f)
    return files


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sori", required=True, help="SORI 출력 폴더(point_result.csv·grid_result.csv)")
    ap.add_argument("--sp-points"); ap.add_argument("--sp-grid")
    ap.add_argument("--out")
    ap.add_argument("--x-col"); ap.add_argument("--y-col"); ap.add_argument("--id-col")
    ap.add_argument("--day-col"); ap.add_argument("--night-col")
    ap.add_argument("--asc-period", choices=("day", "night"), default="day", help=".asc 격자 값의 시간대(기본 day)")
    ap.add_argument("--offset", nargs=2, type=float, default=(0.0, 0.0), metavar=("DX", "DY"))
    ap.add_argument("--tol", type=float, help="격자 맞춤 허용 거리 m (기본 SORI 격자 간격/2)")
    ap.add_argument("--point-tol", type=float, default=1.0, help="수음점 좌표 맞춤 허용 거리 m (기본 1)")
    ap.add_argument("--height", type=float, help="SORI 격자 높이(여러 개일 때)")
    ap.add_argument("--no-plots", action="store_true")
    a = ap.parse_args(argv)
    if not (a.sp_points or a.sp_grid):
        ap.error("--sp-points 또는 --sp-grid 중 하나는 필요")
    dst = Path(a.out or Path(a.sori) / "soundplan_compare")
    dst.mkdir(parents=True, exist_ok=True)
    ri = Path(a.sori) / "run_info.json"
    meth = json.loads(ri.read_text(encoding="utf-8")).get("method_label", "") if ri.exists() else ""
    res, md = {"sori": str(a.sori), "sori_method": meth}, []
    md += ["# SORI ↔ SoundPLAN 비교 요약", "",
           "※ Claude(Anthropic의 AI)를 활용하여 작성하였음. `tools/compare_soundplan.py` 자동 생성.", "",
           f"- SORI 결과: `{a.sori}` ({meth or '방법 기록 없음'})", "- Δ = SORI − SoundPLAN", ""]
    head = ("| 대상 | n | 평균 Δ | SD | RMSE | MAE | 최대 절댓값 | 절댓값 ≤0.5 | ≤1 | ≤2 | ≤3 dB | r | 기울기 | "
            "일치 한계(95 %) |")
    rows = []
    if a.sp_points:
        M, how, pers = match_points(a.sori, a.sp_points, a)
        M.to_csv(dst / "matched_points.csv", index=False, encoding="utf-8-sig")
        nomatch = M.point_id.isna() | M.sori_day.isna()
        res["points"] = {"file": a.sp_points, "match": how, "unmatched": int(nomatch.sum())}
        md.append(f"- 수음점: `{a.sp_points}`, 맞춤 = {how}, 못 맞춘 행 {int(nomatch.sum())}개")
        for per in pers:
            st = stats(M.rename(columns={f"sori_{per}": "sori", f"sp_{per}": "sp"}))
            res["points"][per] = st; rows.append((f"수음점 {PERIOD_KO[per]}", st))
            if not a.no_plots and st["n"]:
                figures(M, per, "points", dst, st)
    if a.sp_grid:
        M, info, pers = match_grid(a.sori, a.sp_grid, a)
        M.to_csv(dst / "matched_grid.csv", index=False, encoding="utf-8-sig")
        res["grid"] = {"file": a.sp_grid, **info}
        md.append(f"- 격자: `{a.sp_grid}`, SoundPLAN 칸 {info['sp_cells']}개 중 {info['matched']}개를 SORI 격자점과 맞춤"
                  f"(허용 {info['tol_m']:.2f} m, SORI 간격 {info['sori_step_m']:g} m)")
        for per in pers:
            st = stats(M.rename(columns={f"sori_{per}": "sori", f"sp_{per}": "sp"}))
            res["grid"][per] = st; rows.append((f"격자 {PERIOD_KO[per]}", st))
            if not a.no_plots and st["n"]:
                figures(M, per, "grid", dst, st)
    md += ["", head, "|" + "---|" * 15]
    for name, s in rows:
        if not s.get("n"):
            md.append(f"| {name} | 0 |" + " |" * 13); continue
        md.append(f"| {name} | {s['n']} | {s['mean_diff']:+.2f} | {s['sd_diff']:.2f} | {s['rmse']:.2f} | {s['mae']:.2f} | "
                  f"{s['max_abs']:.2f} | {s['within_0.5dB_pct']:.0f} % | {s['within_1dB_pct']:.0f} % | "
                  f"{s['within_2dB_pct']:.0f} % | {s['within_3dB_pct']:.0f} % | {s.get('r', float('nan')):.3f} | "
                  f"{s.get('slope', float('nan')):.3f} | {s['loa_low']:+.2f} ~ {s['loa_high']:+.2f} |")
    md += ["", "일치 한계 = 평균 Δ ± 1.96·SD (Bland–Altman). 같은 입력·같은 기준으로 계산했는지(계산조건.md)를 먼저 확인할 것."]
    (dst / "stats.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    (dst / "비교요약.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    return res


if __name__ == "__main__":
    main()
