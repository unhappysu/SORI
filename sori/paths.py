# -*- coding: utf-8 -*-
"""
수음점 하나의 전파 경로와 기여도(SoundPLAN 식 경로선 보기용).
  - 경로: 직접음 + 1차 반사(solver.point_paths_b) + 2차 이상(solver._order_n 의 경로 기록, max_order 까지) —
    격자·벽면 계산과 같은 조건. n 차까지 넣은 합은 격자 결과의 LrT_direct ⊕ LrT_refl1 ⊕ … ⊕ LrT_refl{n} 과 같음
  - refl_pts: 반사점 목록(음원 쪽부터), refl_x·refl_y 는 첫 반사점
  - 경로별 기여 L = L_W + 10 lg(대역 가중·반사 이득) − 감쇠 [dB], 주간·야간
  - 도로별 기여도: 같은 도로(음원선 이름, 없으면 도로 번호)의 경로를 에너지 합
  - RLS-90 은 수음점 할증 K(표 2)를 경로마다 더함(전체 값과 맞게)
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import numpy as np
import pandas as pd

from . import run as RUN
from .terms import path_terms


def esum(v):
    v = np.asarray(v, float)
    v = v[np.isfinite(v)]
    return float(10.0 * np.log10(np.sum(10.0 ** (0.1 * v)))) if len(v) else float("nan")


COLS = ["order", "source", "road", "sx", "sy", "refl_x", "refl_y", "refl_pts", "d_m", "z_m", "L_day", "L_night"]
MAX_REC = 200000                                      # 차수마다 기록하는 경로 수 한도(합에는 영향 없음)


def _high_order(cfg, d, S, F, x, y, z, zg, n):
    """수음점 하나의 n 차 반사 경로(n ≥ 2): DataFrame(source, factor, refl_pts). 계산 커널과 같은 탐색 규칙"""
    from .geometry import MAXO, MAXV, NGEOM
    from .propagation import get_model
    from .solver import HULL_N, _order_n, facade_neighbors
    if not len(F):
        return pd.DataFrame(columns=["source", "factor", "refl_pts"])
    p = cfg["propagation"]; rf = p["reflection"]; br = p["barrier"]
    model = get_model(cfg)
    bands, lams, alphas, wts = model.kernel_bands(cfg)
    fa = [F[c].values.astype(np.float64) for c in ("x1", "y1", "x2", "y2", "nx", "ny", "z0", "ztop", "L")]
    fbid = F.bid.values.astype(np.int64)
    x1, y1, x2, y2, fnx, fny = fa[:6]
    ex, ey = x2 - x1, y2 - y1
    t = np.clip(((x - x1) * ex + (y - y1) * ey) / (ex * ex + ey * ey), 0.0, 1.0)
    dist2 = (x1 + t * ex - x) ** 2 + (y1 + t * ey - y) ** 2
    front = (x - x1) * fnx + (y - y1) * fny
    cand = np.where((dist2 <= float(rf["r_facade"]) ** 2) & (front > 0.05))[0].astype(np.int64)
    nb = facade_neighbors(*fa[:6], float(rf["r_second"]))
    ns = len(S)
    one = np.ones(ns)
    ny, nx = d["gnd"].shape
    rj = np.zeros(MAX_REC, np.int64); rfac = np.zeros(MAX_REC); rp = np.zeros((MAX_REC, 2 * MAXO))
    rn = np.zeros(1, np.int64)
    _order_n(n, float(x), float(y), float(z), float(z - zg), -1, -1, cand, len(cand),
             S.x.values.astype(float), S.y.values.astype(float), S.z.values.astype(float), S.zg.values.astype(float),
             one, one, S.main.values.astype(np.bool_), *fa, fbid, model.reflection_gain(cfg),
             d["gnd"].astype(np.float64), d["dsm"].astype(np.float64), d["bid"].astype(np.int64),
             d["G"].astype(np.float64), float(d["x0"]), float(d["y0"]), float(d["res"]), nx, ny,
             float(p["profile_step"]), float(rf["r_source_main"]), float(rf["r_source_minor"]), float(rf["r_second"]),
             float(p["skip_radius"]), bands, lams, alphas, wts, cfg["input"]["ground"]["method"] == "alternative",
             float(br["C2"]), float(br["dz_max_single"]), float(br["dz_max_double"]), model.code, *nb,
             rf.get("own_facade", "last") == "all", RUN.model_bars(d),
             np.empty(HULL_N), np.empty(HULL_N), np.empty(NGEOM), np.empty(len(bands)), np.ones(len(bands), np.bool_),
             np.empty(MAXV), np.empty(MAXV), np.full(MAXO, -1, np.int64), rj, rfac, rp, rn, RUN.face_factors(cfg, F),
             RUN.cmet_c0(cfg))
    k = int(rn[0])
    return pd.DataFrame(dict(source=rj[:k], factor=rfac[:k],
                             refl_pts=[[(rp[q, 2 * i], rp[q, 2 * i + 1]) for i in range(n)] for q in range(k)]))


def point_table(cfg, x, y, h, model=None, max_order=1):
    """수음점 (x, y, 지면 위 h) 의 경로 표. model = RUN.load_model 결과(없으면 결과 폴더에서 읽음).
    max_order: 넣을 반사 차수(1 = 직접 + 1차, 2·3… = 그 차수까지).
    열: order(0 직접 / n 차 반사), source, road, sx, sy, refl_x, refl_y(첫 반사점), refl_pts(반사점 목록),
        d_m, z_m(경로차, 차폐 없으면 0 — 2차 이상은 비움), L_day, L_night. 반환 (표, 수음점 정보 dict)"""
    d, S, F, _ = model or RUN.load_model(cfg["project"]["output_dir"])
    zg, inside, outside = RUN.point_ground(d, [x], [y])
    if outside[0]:
        raise ValueError("수음점이 계산 모델 범위 밖에 있음")
    if inside[0]:
        raise ValueError("수음점이 건물 안에 있음 — 건물 밖 지점을 고르세요")
    P = pd.DataFrame([dict(point_id="P", x=float(x), y=float(y), z=float(zg[0]) + float(h), zg=float(zg[0]))])
    T = path_terms(cfg, d, S, F, P)
    info = dict(x=float(x), y=float(y), h=float(h), zg=float(zg[0]), z=float(zg[0]) + float(h))
    parts = []
    if len(T):                                        # 대역이 여러 개면 경로(음원·차수·반사면)마다 에너지 합
        g = T.groupby(["source", "order", "facade_fid"], sort=False)
        A = g.agg(refl_x=("refl_x", "first"), refl_y=("refl_y", "first"), d_m=("d_m", "first"), z_m=("z_m", "first"),
                  L_day=("L_day", esum)).reset_index()
        A["refl_pts"] = [[] if o == 0 else [(a, b)] for o, a, b in zip(A.order, A.refl_x, A.refl_y)]
        parts.append(A.drop(columns=["facade_fid"]))
    Kp = 0.0
    if RUN.get_model(cfg).name == "rls90":            # 경로 표(path_terms)와 같게 수음점 할증 K 를 더함
        Kp = float(RUN.junction_K(cfg["project"]["output_dir"], [x], [y])[0])
    for n in range(2, int(max_order) + 1):
        H = _high_order(cfg, d, S, F, x, y, info["z"], info["zg"], n)
        if len(H):
            j = H.source.values
            parts.append(pd.DataFrame(dict(source=j, order=n, refl_x=[p[0][0] for p in H.refl_pts],
                                           refl_y=[p[0][1] for p in H.refl_pts], refl_pts=H.refl_pts,
                                           d_m=np.nan, z_m=np.nan,
                                           L_day=S.LW_day.values[j] + 10 * np.log10(H.factor.values) + Kp)))
    if not parts:
        return pd.DataFrame(columns=COLS), info
    A = pd.concat(parts, ignore_index=True)
    j = A.source.values.astype(int)
    A["source"] = j
    A["L_night"] = A.L_day + (S.LW_night.values[j] - S.LW_day.values[j])
    A["sx"], A["sy"] = S.x.values[j], S.y.values[j]
    name = (S["name"].where(S["name"].notna(), "").astype(str).values[j] if "name" in S
            else np.array([""] * len(j), dtype=object))
    fid = S.fid.values[j] if "fid" in S else j
    A["road"] = [str(n) if str(n).strip() not in ("", "nan", "None") else f"이름 없는 도로 {int(f)}"
                 for n, f in zip(name, fid)]
    A = A.sort_values("L_day", ascending=False).reset_index(drop=True)
    info.update(L_day=esum(A.L_day), L_night=esum(A.L_night), n_paths=int(len(A)), max_order=int(max_order),
                L_day_direct=esum(A.L_day[A.order == 0]))
    for n in range(1, int(max_order) + 1):
        info[f"L_day_refl{n}"] = esum(A.L_day[A.order == n])
    return A[COLS], info


def by_road(A, key="L_day"):
    """도로별 기여도: road, L(에너지 합 dB), share(에너지 비율 %), L_direct, L_refl(반사 전체) — 큰 순서"""
    if not len(A):
        return pd.DataFrame(columns=["road", "L", "share", "L_direct", "L_refl"])
    tot = np.sum(10.0 ** (0.1 * A[key].values))
    rows = []
    for road, g in A.groupby("road", sort=False):
        e = np.sum(10.0 ** (0.1 * g[key].values))
        rows.append(dict(road=road, L=10.0 * np.log10(e), share=100.0 * e / tot,
                         L_direct=esum(g[key][g.order == 0]), L_refl=esum(g[key][g.order > 0])))
    return pd.DataFrame(rows).sort_values("L", ascending=False).reset_index(drop=True)


def top_paths(A, key="L_day", n=30):
    """기여가 큰 경로 n개(지도에 선으로 그릴 것)"""
    return A.sort_values(key, ascending=False).head(int(n)).reset_index(drop=True)


def road_contrib_table(cfg, P, model=None, max_order=None):
    """점 수음점(P: point_id, x, y, h 와 계산 결과 LrT·LrN)마다 도로별 기여 → 긴 표
    (point_id, road, LrT, LrN, share_LrT %, share_LrN %, LrT_direct, LrT_refl). 경로는 직접음 + 반사 max_order 차
    (기본 = max_order_point, 3차까지)까지 — 측면 회절·그보다 높은 차수 반사는 빠지므로 도로 합(covered_LrT)이
    전체 LrT 보다 조금 작을 수 있음(그 차이를 행마다 함께 적음)"""
    model = model or RUN.load_model(cfg["project"]["output_dir"])
    mo = int(cfg["propagation"]["reflection"]["max_order_point"]) if max_order is None else int(max_order)
    mo = max(0, min(mo, 3))
    rows = []
    for r in P.itertuples():
        if not (np.isfinite(getattr(r, "LrT", np.nan))):
            continue
        A, info = point_table(cfg, r.x, r.y, r.h, model=model, max_order=max(mo, 1) if mo else 0)
        if mo == 0:
            A = A[A.order == 0]
        if not len(A):
            continue
        Rd, Rn = by_road(A, "L_day"), by_road(A, "L_night").set_index("road")
        cov_d, cov_n = esum(A.L_day), esum(A.L_night)
        for q in Rd.itertuples():
            rows.append(dict(point_id=r.point_id, road=q.road, LrT=round(q.L, 2), LrN=round(Rn.L[q.road], 2),
                             share_LrT=round(q.share, 1), share_LrN=round(Rn.share[q.road], 1),
                             LrT_direct=round(q.L_direct, 2), LrT_refl=round(q.L_refl, 2),
                             covered_LrT=round(cov_d, 2), total_LrT=round(float(r.LrT), 2),
                             not_covered_dB=round(float(r.LrT) - cov_d, 2)))
    return pd.DataFrame(rows, columns=["point_id", "road", "LrT", "LrN", "share_LrT", "share_LrN", "LrT_direct",
                                       "LrT_refl", "covered_LrT", "total_LrT", "not_covered_dB"])
