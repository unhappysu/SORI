# -*- coding: utf-8 -*-
"""계산 실행: 격자(높이별)·파사드(층별)·점 수음점 → CSV, GeoTIFF, 실행정보(JSON)
※ Claude(Anthropic의 AI)를 활용하여 제작하였음."""
import json
import logging
import os
import time

import numpy as np
import pandas as pd

from . import AI_NOTICE, __version__, HAVE_NUMBA
from . import rls19
from .propagation import get_model
from .solver import compute, to_db

log = logging.getLogger("sori")


def _params(cfg):
    """커널 대역 인자 (bands, lams, alphas, wts) — 전파 방법별(propagation.py)"""
    return get_model(cfg).kernel_bands(cfg)


def load_model(out):
    d = np.load(os.path.join(out, "model_rasters.npz"))
    S = pd.read_csv(os.path.join(out, "sources.csv"))
    F = pd.read_csv(os.path.join(out, "facades.csv"), index_col="fid")
    FRp = os.path.join(out, "facade_receivers.csv")
    FR = pd.read_csv(FRp) if os.path.getsize(FRp) > 5 else pd.DataFrame()
    return d, S, F, FR


def _run(cfg, d, S, F, rx, ry, rz, rg, rbid, rfid, order, label, chunk=256, progress=None):
    p = cfg["propagation"]; rf = p["reflection"]; br = p["barrier"]
    model = get_model(cfg)
    bands, lams, alphas, wts = model.kernel_bands(cfg)
    args_s = (S.x.values, S.y.values, S.z.values, S.zg.values,
              10 ** (0.1 * S.LW_day.values), 10 ** (0.1 * S.LW_night.values), S.main.values.astype(np.bool_))
    if len(F):
        args_f = tuple(F[c].values.astype(np.float64) for c in ("x1", "y1", "x2", "y2", "nx", "ny", "z0", "ztop", "L")) \
            + (F.bid.values.astype(np.int64),)
    else:
        args_f = tuple(np.zeros(0) for _ in range(9)) + (np.zeros(0, np.int64),)
    ny, nx = d["gnd"].shape
    n = len(rx)
    res = [np.empty(n) for _ in range(5)]
    t0 = time.time()
    for a in range(0, n, chunk):
        b = min(a + chunk, n)
        o = compute(rx[a:b], ry[a:b], rz[a:b], rg[a:b], rbid[a:b], rfid[a:b], order,
                    *args_s, *args_f, model.reflection_gain(cfg),
                    d["gnd"].astype(np.float64), d["dsm"].astype(np.float64), d["bid"].astype(np.int64),
                    d["G"].astype(np.float64), float(d["x0"]), float(d["y0"]), float(d["res"]), nx, ny,
                    float(p["profile_step"]), float(p["max_dist_main"]), float(p["max_dist_minor"]),
                    float(rf["r_facade"]), float(rf["r_source_main"]), float(rf["r_source_minor"]),
                    float(rf["r_second"]), float(p["skip_radius"]),
                    bands, lams, alphas, wts, cfg["input"]["ground"]["method"] == "alternative",
                    float(br["C2"]), float(br["dz_max_single"]), float(br["dz_max_double"]), model.code)
        for k in range(5):
            res[k][a:b] = o[k]
        el = time.time() - t0
        log.info("%s %d/%d  %.0fs  (남은 시간 약 %.0fs)", label, b, n, el, el / b * (n - b))
        if progress is not None:
            progress(label, b, n, el / b * (n - b))
    Ed, En, Edir, E1, E2 = res
    return dict(LrT=to_db(Ed), LrN=to_db(En), LrT_direct=to_db(Edir), LrT_refl1=to_db(E1), LrT_refl2=to_db(E2))


def segment_check(S, rx, ry, rz):
    """RLS-19 3.2 주석(경험칙) l_i ≤ s_i/2 진단. 결과값에는 영향 없음.
    s = 구간 중앙(음원점) → 수음점 직접 경로의 3D 거리. 반사 경로는 s가 더 길어 제외.
    반환: 수음점별 (위반 음원 수, 그중 간선 음원 수, 반경 2·l_max 안 음원의 최소 s/l — 없으면 inf)"""
    from scipy.spatial import cKDTree
    sx, sy, sz, sl = (S[c].values.astype(np.float64) for c in ("x", "y", "z", "l"))
    sm = S["main"].values.astype(bool)
    n = len(rx)
    nv = np.zeros(n, np.int64); nvm = np.zeros(n, np.int64); ratio = np.full(n, np.inf)
    if n == 0 or len(sx) == 0:
        return nv, nvm, ratio
    tree = cKDTree(np.c_[sx, sy])
    for k, js in enumerate(tree.query_ball_point(np.c_[rx, ry], 2.0 * sl.max())):
        if not js:
            continue
        js = np.asarray(js)
        q = np.sqrt((sx[js] - rx[k]) ** 2 + (sy[js] - ry[k]) ** 2 + (sz[js] - rz[k]) ** 2) / sl[js]
        bad = q < 2.0
        nv[k] = bad.sum(); nvm[k] = (bad & sm[js]).sum(); ratio[k] = q.min()
    return nv, nvm, ratio


def _segment_summary(nv, nvm, ratio):
    v = nv > 0
    return {"receivers": int(len(nv)), "violating": int(v.sum()),
            "violating_pct": round(100.0 * v.mean(), 2) if len(nv) else 0.0,
            "violating_via_main": int((nvm > 0).sum()),
            "min_s_over_l": float(ratio.min()) if len(ratio) and np.isfinite(ratio.min()) else None}


# 점 수음점 CSV 열 이름 대응(왼쪽이 표준 이름). field_survey/plan/measurement_points.csv 형식도 읽음
POINT_COLS = {"point_id": ("point_id", "id", "ID"), "x": ("x", "x_epsg5186", "X"), "y": ("y", "y_epsg5186", "Y"),
              "h": ("h", "height_m", "height", "z")}


def read_points(path, default_height=None):
    """점 수음점 CSV → DataFrame(point_id, x, y, h, h_basis). 좌표(EPSG = cfg crs)가 빈 행은 건너뜀.
    h 는 지면 위 높이(m). 빈 칸이면 default_height(없으면 그 행 건너뜀)."""
    from .inputs import read_csv_any
    raw = read_csv_any(path)
    cols = {}
    for std, names in POINT_COLS.items():
        hit = next((n for n in names if n in raw.columns), None)
        if hit is None and std != "h":
            raise ValueError(f"점 수음점 CSV에 {std} 열이 없음(허용 이름: {', '.join(names)}): {path}")
        cols[std] = hit
    rows, skipped = [], []
    for _, r in raw.iterrows():
        pid = str(r[cols["point_id"]]).strip()
        x, y = pd.to_numeric(r[cols["x"]], errors="coerce"), pd.to_numeric(r[cols["y"]], errors="coerce")
        h = pd.to_numeric(r[cols["h"]], errors="coerce") if cols["h"] else np.nan
        basis = "input"
        if not np.isfinite(h) and default_height is not None:
            h, basis = float(default_height), "default_height"
        if not (np.isfinite(x) and np.isfinite(y) and np.isfinite(h)):
            skipped.append(pid)
            continue
        rows.append(dict(point_id=pid, x=float(x), y=float(y), h=float(h), h_basis=basis))
    if skipped:
        log.warning("점 수음점 %d개 건너뜀(좌표 또는 높이 없음): %s", len(skipped), ", ".join(skipped))
    P = pd.DataFrame(rows, columns=["point_id", "x", "y", "h", "h_basis"])
    if P.point_id.duplicated().any():
        raise ValueError(f"점 수음점 point_id 중복: {sorted(set(P.point_id[P.point_id.duplicated()]))}")
    return P


def point_ground(d, x, y):
    """점 수음점의 지면고(build.Raster.sample 과 같은 1차 보간)와 건물 안·모델 범위 밖 여부."""
    from scipy.ndimage import map_coordinates
    gnd, bid = d["gnd"], d["bid"]
    x0, y0, res = float(d["x0"]), float(d["y0"]), float(d["res"])
    x, y = np.asarray(x, float), np.asarray(y, float)
    zg = map_coordinates(gnd.astype(np.float64), [(y0 - y) / res - 0.5, (x - x0) / res - 0.5], order=1, mode="nearest")
    i, j = np.floor((y0 - y) / res).astype(int), np.floor((x - x0) / res).astype(int)
    outside = (i < 0) | (j < 0) | (i >= gnd.shape[0]) | (j >= gnd.shape[1])
    inside = np.zeros(len(x), bool)
    ok = ~outside
    inside[ok] = bid[i[ok], j[ok]] >= 0
    return zg, inside, outside


def _write_tif(path, A, gx, gy, step, crs, method=None):
    import rasterio
    from rasterio.transform import from_origin
    tr = from_origin(gx[0] - step / 2, gy[-1] + step / 2, step, step)
    with rasterio.open(path, "w", driver="GTiff", height=A.shape[0], width=A.shape[1], count=1,
                       dtype="float32", crs=f"EPSG:{crs}", transform=tr, nodata=np.nan) as ds:
        ds.write(A[::-1].astype(np.float32), 1)
        ds.update_tags(NOTE=AI_NOTICE)
        if method:
            ds.update_tags(PROPAGATION_METHOD=method)


def previous_outputs(out, method):
    """같은 출력 폴더의 이전 run_info.json 에서 결과 종류별 전파 방법을 읽음.
    다른 방법의 결과가 있으면 오류(소음지도 3종·점 결과에 전파 방법이 섞이지 않게)."""
    p = os.path.join(out, "run_info.json")
    if not os.path.exists(p):
        return {}
    try:
        prev = json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    outs = prev.get("outputs") or {}
    if not outs and prev.get("propagation"):           # 이 기능 이전 판의 run_info(전파는 ISO 9613-2 고정)
        outs = {k: "iso9613_2" for k in ("grid", "facade", "points")
                if os.path.exists(os.path.join(out, f"{'point' if k == 'points' else k}_result.csv"))}
    other = {k: v for k, v in outs.items() if v != method}
    if other:
        raise ValueError(f"출력 폴더 {out} 에 다른 전파 방법의 결과가 있음({other}) — 이번 방법 {method}. "
                         "방법이 섞이지 않도록 project.output_dir 를 바꾸거나 기존 결과(run_info.json 포함)를 지우고 다시 실행")
    return outs


def main(cfg, which=("grid", "facade", "points"), progress=None):
    """progress(label, done, total, eta_s): 진행률 콜백(웹앱용, 선택)"""
    out = cfg["project"]["output_dir"]
    model = get_model(cfg)                              # 이번 실행의 모든 결과 종류가 같은 전파 방법을 씀
    outputs = previous_outputs(out, model.name)
    d, S, F, FR = load_model(out)
    summary = {}
    do_seg = bool(cfg["propagation"].get("segment_check", True))
    seg_rows, seg_sum = [], {}

    def _seg(kind, rx, ry, rz, label, extra):
        nv, nvm, ratio = segment_check(S, rx, ry, rz)
        seg_sum[label] = _segment_summary(nv, nvm, ratio)
        v = nv > 0
        seg_rows.append(pd.DataFrame(dict(kind=kind, x=rx[v], y=ry[v], z=rz[v], **{k: a[v] for k, a in extra.items()},
                                          n_viol=nv[v], n_viol_main=nvm[v], min_s_over_l=ratio[v])))
    if "grid" in which and cfg["receivers"]["grid"]["enabled"]:
        gx, gy, gz, ins = d["gx"], d["gy"], d["gz"], d["ginside"]
        X, Y = np.meshgrid(gx, gy)
        m = ~ins.ravel()
        frames = []
        for h in cfg["receivers"]["grid"]["heights"]:
            rx, ry = X.ravel()[m], Y.ravel()[m]
            rg = gz.ravel()[m]; rz = rg + float(h)
            nr = len(rx)
            r = _run(cfg, d, S, F, rx, ry, rz, rg, np.full(nr, -1, np.int64), np.full(nr, -1, np.int64),
                     int(cfg["propagation"]["reflection"]["max_order_grid"]), f"격자 h={h:g} m", progress=progress)
            G = pd.DataFrame(dict(x=rx, y=ry, z=rz, h=float(h), **r))
            frames.append(G)
            if do_seg:
                _seg("grid", rx, ry, rz, f"grid_h{h:g}", {"h": np.full(nr, float(h)), "floor": np.full(nr, np.nan)})
            for key in ("LrT", "LrN"):
                A = np.full(X.shape, np.nan)
                A.ravel()[m] = G[key].values
                _write_tif(os.path.join(out, f"grid_{key}_h{h:g}m.tif"), A, gx, gy,
                           cfg["receivers"]["grid"]["step"], cfg["crs"], model.name)
            summary[f"grid_h{h:g}"] = {k: [float(np.nanmin(G[k])), float(np.nanmax(G[k]))] for k in ("LrT", "LrN")}
        pd.concat(frames).to_csv(os.path.join(out, "grid_result.csv"), index=False, encoding="utf-8-sig")
        outputs["grid"] = model.name
    if "facade" in which and cfg["receivers"]["facade"]["enabled"] and len(FR):
        r = _run(cfg, d, S, F, FR.x.values, FR.y.values, FR.z.values, FR.zg.values,
                 FR.bid.values.astype(np.int64), FR.fid.values.astype(np.int64),
                 int(cfg["propagation"]["reflection"]["max_order_facade"]), "건물 벽면", progress=progress)
        for k, v in r.items():
            FR[k] = v
        FR["LrT_cmp"] = [rls19.round_up(v) if np.isfinite(v) else np.nan for v in FR.LrT]   # RLS-19 3.1 올림
        FR["LrN_cmp"] = [rls19.round_up(v) if np.isfinite(v) else np.nan for v in FR.LrN]
        FR.to_csv(os.path.join(out, "facade_result.csv"), index=False, encoding="utf-8-sig")
        outputs["facade"] = model.name
        summary["facade"] = {k: [float(np.nanmin(FR[k])), float(np.nanmax(FR[k]))] for k in ("LrT", "LrN")}
        if do_seg:
            _seg("facade", FR.x.values, FR.y.values, FR.z.values, "facade",
                 {"h": np.full(len(FR), np.nan), "floor": FR.floor.values})
    ppath = cfg["receivers"].get("points", {}).get("path")
    if "points" in which and ppath:
        P = read_points(ppath, cfg["receivers"]["points"].get("default_height"))
        zg, inside, outside = point_ground(d, P.x.values, P.y.values)
        P["zg"], P["z"] = zg, zg + P.h.values
        P["note"] = np.where(outside, "모델 범위 밖", np.where(inside, "건물 안", ""))
        ok = (P.note == "").values
        keys = ("LrT", "LrN", "LrT_direct", "LrT_refl1", "LrT_refl2")
        for k in keys:
            P[k] = np.nan
        if ok.any():
            n = int(ok.sum())
            r = _run(cfg, d, S, F, P.x.values[ok], P.y.values[ok], P.z.values[ok], P.zg.values[ok],
                     np.full(n, -1, np.int64), np.full(n, -1, np.int64),
                     int(cfg["propagation"]["reflection"]["max_order_point"]), "점 수음점", progress=progress)
            for k in keys:
                P.loc[ok, k] = r[k]
        P["LrT_cmp"] = [rls19.round_up(v) if np.isfinite(v) else np.nan for v in P.LrT]   # RLS-19 3.1 올림
        P["LrN_cmp"] = [rls19.round_up(v) if np.isfinite(v) else np.nan for v in P.LrN]
        P = P[["point_id", "x", "y", "zg", "h", "h_basis", "z", *keys, "LrT_cmp", "LrN_cmp", "note"]]
        P.to_csv(os.path.join(out, "point_result.csv"), index=False, encoding="utf-8-sig")
        outputs["points"] = model.name
        # field_survey/scripts/process_measurements.py --model 입력 형식(point_id, period, Leq_calc)
        cmp = pd.concat([pd.DataFrame(dict(point_id=P.point_id, period=per, Leq_calc=P[k].round(1),
                                           quantity=f"{k} (RLS-19 평가레벨, 교차로·반사 보정 포함)", note=P.note))
                         for per, k in (("day", "LrT"), ("night", "LrN"))])
        cmp.to_csv(os.path.join(out, "point_levels_for_comparison.csv"), index=False, encoding="utf-8-sig")
        if ok.any():
            summary["points"] = {k: [float(np.nanmin(P[k])), float(np.nanmax(P[k]))] for k in ("LrT", "LrN")}
        if (~ok).any():
            log.warning("점 수음점 %d개 계산 안 함: %s", int((~ok).sum()),
                        ", ".join(f"{a}({b})" for a, b in zip(P.point_id[~ok], P.note[~ok])))
    if do_seg and seg_rows:
        pd.concat(seg_rows).to_csv(os.path.join(out, "segment_check.csv"), index=False, encoding="utf-8-sig")
        log.info("구간 길이 진단(l ≤ s/2, 결과값 불변): %s", seg_sum)
    bands, lams, alphas, wts = model.kernel_bands(cfg)
    info = {"software": f"SORI v{__version__}", "notice": AI_NOTICE, "numba": HAVE_NUMBA,
            "emission": "RLS-19 (FGSV 052, 2019, Korrektur 2020)", "propagation": model.label(cfg),
            **model.info(cfg), "outputs": outputs,
            "ground_method": cfg["input"]["ground"]["method"], "bands_hz": list(bands),
            "alpha_dB_per_km": [round(a, 3) for a in alphas], "weights": list(np.round(wts, 4)),
            "reflection": cfg["propagation"]["reflection"], "results_range": summary,
            "segment_check": ({"rule": "RLS-19 3.2 주석 l_i <= s_i/2 (경험칙, 진단만·결과값 불변). "
                                       "s = 직접 경로 3D 거리", **seg_sum} if do_seg else None),
            "config": cfg["_config_path"], "time": time.strftime("%Y-%m-%d %H:%M:%S")}
    with open(os.path.join(out, "run_info.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2, default=str)
    log.info("계산 완료: %s", summary)
    return summary
