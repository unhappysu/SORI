# -*- coding: utf-8 -*-
"""
감쇠항별 중간값 출력(대표 수음점 = 점 수음점) → path_terms.csv
  설정 propagation.terms_csv: true 일 때 run.main 의 점 수음점 계산 뒤에 만듦.
  경로: 직접음 + 1차 반사(solver.point_paths). 2·3차 반사 경로는 넣지 않음(행 수가 너무 많아짐).
  열 — rls19: D_div 식(12), D_atm 식(13), D_gr 식(14), D_z 식(15), D_RV(표 8, 반사 경로만), D_A 식(11)
       iso9613_2: A_div 식(7), A_atm 식(8), A_gr 식(9)/(10), D_Omega 식(11), D_z 식(14), A_bar 식(12),
                  refl_dB = 10 lg ρ (식 20, 반사 경로만), A_net = A − D_c
  L_day = L_W(주간) + 10 lg(대역 가중) + 10 lg(반사 이득) − (D_A 또는 A_net). 같은 수음점·같은 차수의 L_day 를
  에너지 합하면 point_result.csv 의 LrT_direct / LrT_refl1 과 같음(시험 test_terms_csv_closure).
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import numpy as np
import pandas as pd

from . import iso9613, rls19_propagation
from .propagation import get_model
from .solver import (P_J, P_ORDER, P_FACE, P_PX, P_PY, P_D, P_SGR, P_HM, P_Z, P_GAIN, point_paths)


def path_terms(cfg, d, S, F, P):
    """P: point_id, x, y, z, zg 열을 가진 점 수음점 표(계산 대상만). 반환 DataFrame"""
    p = cfg["propagation"]; rf = p["reflection"]; br = p["barrier"]
    model = get_model(cfg)
    bands, lams, alphas, wts = model.kernel_bands(cfg)
    gain = model.reflection_gain(cfg)
    order = min(int(rf["max_order_point"]), 1)
    if len(F):
        fa = tuple(F[c].values.astype(np.float64) for c in ("x1", "y1", "x2", "y2", "nx", "ny", "z0", "ztop", "L")) \
            + (F.bid.values.astype(np.int64),)
        fids = F.index.values
    else:
        fa = tuple(np.zeros(0) for _ in range(9)) + (np.zeros(0, np.int64),)
        fids = np.zeros(0, np.int64)
    ny, nx = d["gnd"].shape
    names = rls19_propagation.TERM_NAMES if model.name == "rls19" else iso9613.TERM_NAMES
    frames = []
    for r in P.itertuples():
        info, terms, band = point_paths(
            float(r.x), float(r.y), float(r.z), float(r.zg), -1, -1, order,
            S.x.values, S.y.values, S.z.values, S.zg.values, S.main.values.astype(np.bool_), *fa, gain,
            d["gnd"].astype(np.float64), d["dsm"].astype(np.float64), d["bid"].astype(np.int64),
            d["G"].astype(np.float64), float(d["x0"]), float(d["y0"]), float(d["res"]), nx, ny,
            float(p["profile_step"]), float(p["max_dist_main"]), float(p["max_dist_minor"]),
            float(rf["r_facade"]), float(rf["r_source_main"]), float(rf["r_source_minor"]), float(p["skip_radius"]),
            bands, lams, alphas, cfg["input"]["ground"]["method"] == "alternative",
            float(br["C2"]), float(br["dz_max_single"]), float(br["dz_max_double"]), model.code)
        if not len(info):
            continue
        j = info[:, P_J].astype(int)
        fi = info[:, P_FACE].astype(int)
        D = pd.DataFrame({"point_id": r.point_id, "propagation_method": model.name,
                          "source": j, "road_fid": S.fid.values[j], "order": info[:, P_ORDER].astype(int),
                          "facade_fid": np.where(fi >= 0, fids[np.maximum(fi, 0)] if len(fids) else -1, -1),
                          "refl_x": info[:, P_PX], "refl_y": info[:, P_PY],
                          "d_m": info[:, P_D], "s_gr_m": info[:, P_SGR], "h_m": info[:, P_HM], "z_m": info[:, P_Z],
                          "band_hz": bands[band], "LW_day": S.LW_day.values[j]})
        g = info[:, P_GAIN]
        if model.name == "rls19":
            for k, nm in enumerate(names[:-1]):
                D[nm] = terms[:, k]
            D["D_RV"] = np.where(D.order > 0, -10 * np.log10(g), 0.0)
            D["D_A"] = terms[:, len(names) - 1]
            net = D["D_A"].values
        else:
            for k, nm in enumerate(names[:-1]):
                D[nm] = terms[:, k]
            D["refl_dB"] = np.where(D.order > 0, 10 * np.log10(g), 0.0)
            D["A_net"] = terms[:, len(names) - 1]
            net = D["A_net"].values
        D["L_day"] = D.LW_day.values + 10 * np.log10(wts[band] * g) - net
        frames.append(D)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
