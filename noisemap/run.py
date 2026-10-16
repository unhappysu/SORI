# -*- coding: utf-8 -*-
"""계산 실행: 격자(높이별)·파사드(층별) 수음점 → CSV, GeoTIFF, 실행정보(JSON)
※ Claude(Anthropic의 AI)를 활용하여 제작하였음."""
import json
import logging
import os
import time

import numpy as np
import pandas as pd

from . import AI_NOTICE, __version__, HAVE_NUMBA
from . import rls19
from .iso9613 import alpha_iso9613_1, C_SOUND
from .solver import compute, to_db

log = logging.getLogger("noisemap")


def _params(cfg):
    p = cfg["propagation"]
    bands = np.array(p["bands_hz"], float)
    lams = C_SOUND / bands
    if p["alpha_dB_per_km"] is not None:
        alphas = np.array(p["alpha_dB_per_km"], float)
    else:
        alphas = np.array([alpha_iso9613_1(f, p["temperature_C"], p["humidity_pct"], p["pressure_kPa"])
                           for f in bands])
    w = 10 ** (0.1 * np.array(p["spectrum_dBA"], float))
    wts = w / w.sum()
    return bands, lams, alphas, wts


def load_model(out):
    d = np.load(os.path.join(out, "model_rasters.npz"))
    S = pd.read_csv(os.path.join(out, "sources.csv"))
    F = pd.read_csv(os.path.join(out, "facades.csv"), index_col="fid")
    FRp = os.path.join(out, "facade_receivers.csv")
    FR = pd.read_csv(FRp) if os.path.getsize(FRp) > 5 else pd.DataFrame()
    return d, S, F, FR


def _run(cfg, d, S, F, rx, ry, rz, rg, rbid, rfid, order, label, chunk=256, progress=None):
    p = cfg["propagation"]; rf = p["reflection"]; br = p["barrier"]
    bands, lams, alphas, wts = _params(cfg)
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
                    *args_s, *args_f, float(rf["rho"]),
                    d["gnd"].astype(np.float64), d["dsm"].astype(np.float64), d["bid"].astype(np.int64),
                    d["G"].astype(np.float64), float(d["x0"]), float(d["y0"]), float(d["res"]), nx, ny,
                    float(p["profile_step"]), float(p["max_dist_main"]), float(p["max_dist_minor"]),
                    float(rf["r_facade"]), float(rf["r_source_main"]), float(rf["r_source_minor"]),
                    float(rf["r_second"]), float(p["skip_radius"]),
                    bands, lams, alphas, wts, cfg["input"]["ground"]["method"] == "alternative",
                    float(br["C2"]), float(br["dz_max_single"]), float(br["dz_max_double"]))
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


def _write_tif(path, A, gx, gy, step, crs):
    import rasterio
    from rasterio.transform import from_origin
    tr = from_origin(gx[0] - step / 2, gy[-1] + step / 2, step, step)
    with rasterio.open(path, "w", driver="GTiff", height=A.shape[0], width=A.shape[1], count=1,
                       dtype="float32", crs=f"EPSG:{crs}", transform=tr, nodata=np.nan) as ds:
        ds.write(A[::-1].astype(np.float32), 1)
        ds.update_tags(NOTE=AI_NOTICE)


def main(cfg, which=("grid", "facade"), progress=None):
    """progress(label, done, total, eta_s): 진행률 콜백(웹앱용, 선택)"""
    out = cfg["project"]["output_dir"]
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
                           cfg["receivers"]["grid"]["step"], cfg["crs"])
            summary[f"grid_h{h:g}"] = {k: [float(np.nanmin(G[k])), float(np.nanmax(G[k]))] for k in ("LrT", "LrN")}
        pd.concat(frames).to_csv(os.path.join(out, "grid_result.csv"), index=False)
    if "facade" in which and cfg["receivers"]["facade"]["enabled"] and len(FR):
        r = _run(cfg, d, S, F, FR.x.values, FR.y.values, FR.z.values, FR.zg.values,
                 FR.bid.values.astype(np.int64), FR.fid.values.astype(np.int64),
                 int(cfg["propagation"]["reflection"]["max_order_facade"]), "건물 벽면", progress=progress)
        for k, v in r.items():
            FR[k] = v
        FR["LrT_cmp"] = [rls19.round_up(v) if np.isfinite(v) else np.nan for v in FR.LrT]   # RLS-19 3.1 올림
        FR["LrN_cmp"] = [rls19.round_up(v) if np.isfinite(v) else np.nan for v in FR.LrN]
        FR.to_csv(os.path.join(out, "facade_result.csv"), index=False)
        summary["facade"] = {k: [float(np.nanmin(FR[k])), float(np.nanmax(FR[k]))] for k in ("LrT", "LrN")}
        if do_seg:
            _seg("facade", FR.x.values, FR.y.values, FR.z.values, "facade",
                 {"h": np.full(len(FR), np.nan), "floor": FR.floor.values})
    if do_seg and seg_rows:
        pd.concat(seg_rows).to_csv(os.path.join(out, "segment_check.csv"), index=False)
        log.info("구간 길이 진단(l ≤ s/2, 결과값 불변): %s", seg_sum)
    bands, lams, alphas, wts = _params(cfg)
    info = {"software": f"noisemap {__version__}", "notice": AI_NOTICE, "numba": HAVE_NUMBA,
            "emission": "RLS-19 (FGSV 052, 2019, Korrektur 2020)", "propagation": "ISO 9613-2:1996",
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
