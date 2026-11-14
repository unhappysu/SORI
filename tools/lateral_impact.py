# -*- coding: utf-8 -*-
"""
측면 회절(ISO 9613-2:1996 식 13) 영향 확인용 시제품 — 보완 사항 8번 1단계. 계산 엔진(sori/)은 바꾸지 않고,
이미 계산한 결과 폴더(모델 파일)를 읽기만 함. 결과는 따로 지정한 폴더에 씀.

계산 규칙(CNOSSOS-EU 2021 개정 문구·ISO/TR 17534-3 목차를 참고한 프로젝트 해석)
  - 대상: 직접음 경로 중 연직 단면이 '건물'로 막힌 것(지형만으로 막히면 측면 회절 없음 — TR 17534-3 5.8).
    반사 경로(허상음원)에는 넣지 않음(CNOSSOS).
  - 경로: 평면에서 음원 S·수음점 R 과, 직접 경로(3D 직선)가 뚫고 지나는 건물(평면으로 SR 과 만나고 그곳 지붕이
    직선보다 높은 건물)의 외곽 꼭짓점을 모두 감싸는 볼록 껍질(고무줄). S·R 이 껍질 꼭짓점이 아니면 측면 경로 없음.
    껍질을 S·R 에서 나눈 왼쪽·오른쪽 사슬 = 측면 경로 최대 2개. 높이는 S→R 직선 보간(펼친 경로).
  - 감쇠(식 13, 경로마다): A = A_div(직선 거리 d) + A_atm(펼친 경로 길이) + A_gr(측면 경로 아래 G, 일반법 식 9)
    + D_z(식 14, K_met = 1(ISO 7.4 측면 회절), C3 는 모서리 1개면 1, 2개 이상이면 식 15(e = 첫·끝 모서리 사이),
    z = 펼친 경로 길이 − d, 상한 20/25 dB)
  - 합치기: 윗모서리(지금 계산) + 왼쪽 + 오른쪽 에너지 합. 차폐가 없을 때(지형만 있는 단면) 값을 넘지 않게 제한
    (ISO/TR 17534-3 5.6 '측면 회절로 레벨이 오르지 않음')
  - 변형 A(CNOSSOS 식, 큰 쪽 추정): 측면 경로가 다른 건물을 지나도 지면 항에만 반영(여기서는 G 래스터 그대로)
    변형 B(작은 쪽 추정): 측면 경로가 껍질을 만든 건물이 아닌 건물을 그 높이보다 낮게 지나면 그 경로를 버림
    변형 C(껍질 넓히기): 측면 경로를 막는 건물을 그 쪽 껍질에 더해 다시 돌아감(최대 MAX_GROW 번, 그래도 막히면 버림).
      좌우를 따로 넓힘. 붙은 건물 줄은 줄 끝까지 돌아가는 경로가 됨
  python tools/lateral_impact.py <설정 yaml> <결과 폴더> <내보낼 폴더> [--n-tall 40] [--n-random 40]
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sori import config as C, run as RUN  # noqa: E402
from sori.geometry import NGEOM, G_SHIELD, path_geometry  # noqa: E402
from sori.iso9613 import A_atm, A_div, A_gr_general, atten_iso  # noqa: E402
from sori.propagation import get_model  # noqa: E402

HULL_N = 8192


def c3(e, lam):
    """ISO 9613-2 식 (15)"""
    if e <= 0.0:
        return 1.0
    r = (5.0 * lam / e) ** 2
    return (1.0 + r) / (1.0 / 3.0 + r)


def lateral_chains(S, R, verts):
    """평면 볼록 껍질에서 S→R 왼쪽·오른쪽 사슬(각각 S, 모서리…, R 꼭짓점 배열). S·R 이 껍질 꼭짓점이 아니면 []"""
    from scipy.spatial import ConvexHull, QhullError
    P = np.vstack([S, R, verts])
    try:
        h = list(ConvexHull(P).vertices)                 # 2D: 반시계 방향
    except QhullError:
        return []
    if 0 not in h or 1 not in h:
        return []
    i0 = h.index(0)
    h = h[i0:] + h[:i0]                                  # S 부터
    j = h.index(1)
    a = h[:j + 1]                                        # S → … → R (반시계)
    b = [0] + h[j:][::-1]                                # S → … → R (시계)
    return [P[c] for c in (a, b) if len(c) >= 3]


def lateral_atten(chain, zs, zr, hs, hr, d, f, lam, alpha, Gfun, C2=20.0, dz1=20.0, dz2=25.0):
    """측면 경로 하나의 감쇠 A(식 13: A_div(d) + A_atm + A_gr + D_z, K_met = 1)와 경로차 z"""
    seg = np.hypot(*np.diff(chain, axis=0).T)
    Lh = float(seg.sum())
    L3 = math.hypot(Lh, zr - zs)
    z = L3 - d
    k = len(chain) - 2                                   # 모서리 수
    e = (Lh - seg[0] - seg[-1]) * (L3 / Lh) if k >= 2 else 0.0
    dz = 10.0 * math.log10(3.0 + (C2 / lam) * c3(e, lam) * max(z, 0.0))
    dz = min(dz, dz2 if k >= 2 else dz1)
    Gs, Gr, Gm = Gfun(chain, Lh, hs, hr)
    agr = A_gr_general(f, hs, hr, Lh, Gs, Gr, Gm)
    return A_div(d) + A_atm(alpha, L3) + agr + dz, z, k


MAX_GROW = 6


def side_of(S, R, chain):
    """사슬이 SR 의 왼쪽(+1)인지 오른쪽(−1)인지(첫 모서리 기준)"""
    v = chain[1] - S
    u = R - S
    return 1 if u[0] * v[1] - u[1] * v[0] > 0 else -1


def grown_chains(S, R, Bd, X, zs, zr):
    """변형 C: 좌우 사슬마다 막는 건물을 더해 껍질을 넓힘. 반환 사슬 목록(막힌 채 남은 쪽은 뺌)"""
    from shapely.geometry import LineString
    out = []
    for side in (1, -1):
        Xs = list(X)
        for _ in range(MAX_GROW + 1):
            ch = [c for c in lateral_chains(S, R, np.vstack([Bd.verts[i] for i in Xs])) if side_of(S, R, c) == side]
            if not ch:
                break
            c = ch[0]
            pl = LineString(c)
            Lh = pl.length
            blk = Bd.crossing(pl, lambda t, Lh=Lh: zs + (zr - zs) * t / Lh, exclude=set(Xs))
            if not blk:
                out.append(c)
                break
            Xs += blk
    return out


def make_G(d):
    """측면 경로를 따라 G 래스터 평균(geometry.path_geometry 와 같은 영역 구분: 음원 30·hs, 수음점 30·hr)"""
    G = d["G"]; x0, y0, res = float(d["x0"]), float(d["y0"]), float(d["res"])
    ny, nx = G.shape

    def fn(chain, Lh, hs, hr):
        cum = np.r_[0.0, np.cumsum(np.hypot(*np.diff(chain, axis=0).T))]
        t = np.arange(0.5, Lh, 1.0)
        if not len(t):
            return 0.0, 0.0, 0.0
        x = np.interp(t, cum, chain[:, 0]); y = np.interp(t, cum, chain[:, 1])
        i = np.clip(((y0 - y) / res).astype(int), 0, ny - 1); j = np.clip(((x - x0) / res).astype(int), 0, nx - 1)
        g = G[i, j]
        s = t <= 30 * hs; r = (Lh - t) <= 30 * hr; m = ~s & ~r
        Gs = g[s].mean() if s.any() else (g[r].mean() if r.any() else 0.0)
        Gr = g[r].mean() if r.any() else Gs
        Gm = g[m].mean() if m.any() else 0.5 * (Gs + Gr)
        return float(Gs), float(Gr), float(Gm)
    return fn


class Buildings:
    """건물 외곽선(평면)·지붕 높이, 선분과 만나는 건물 찾기"""
    def __init__(self, gpkg):
        import geopandas as gpd
        from shapely import STRtree
        self.B = gpd.read_file(gpkg)
        self.geom = list(self.B.geometry.values)
        self.ztop = self.B.ztop.values.astype(float)
        self.tree = STRtree(self.geom)
        self.verts = []
        for g in self.geom:
            parts = getattr(g, "geoms", [g])
            self.verts.append(np.vstack([np.asarray(p.exterior.coords)[:-1, :2] for p in parts]))

    def crossing(self, line, zfun, exclude=()):
        """line(평면 LineString) 과 만나고 그 구간에서 지붕이 경로 높이보다 높은 건물 색인. zfun(거리) = 경로 높이.
        구간 양 끝·가운데 세 점의 경로 높이 중 가장 낮은 값과 비교(경로 높이는 직선이라 끝점이 최솟값을 포함)"""
        from shapely.geometry import Point
        out = []
        for i in self.tree.query(line, predicate="intersects"):
            if int(i) in exclude:
                continue
            inter = line.intersection(self.geom[i])
            pts = [line.project(Point(c)) for g in getattr(inter, "geoms", [inter]) if hasattr(g, "coords")
                   for c in g.coords]
            if not pts:
                continue
            if self.ztop[i] > min(zfun(min(pts)), zfun(max(pts))) + 1e-6:
                out.append(int(i))
        return out


def receiver_levels(cfg, d, S, Bd, x, y, zg, h, model, stats):
    """수음점 하나: 직접음 에너지(지금 계산과 같음)와 측면 회절을 더한 에너지(변형 A·B), 쌍 수"""
    from shapely.geometry import LineString
    p = cfg["propagation"]; br = p["barrier"]
    bands, lams, alphas, wts = model.kernel_bands(cfg)
    assert len(bands) == 1, "시제품은 단일 대역(500 Hz)만"
    f, lam, alpha = float(bands[0]), float(lams[0]), float(alphas[0])
    gnd = d["gnd"].astype(np.float64); dsm = d["dsm"].astype(np.float64); bid = d["bid"].astype(np.int64)
    G = d["G"].astype(np.float64)
    ny, nx = gnd.shape
    x0, y0, res = float(d["x0"]), float(d["y0"]), float(d["res"])
    hd = np.empty(HULL_N); hh = np.empty(HULL_N); geom = np.empty(NGEOM); out = np.empty(1)
    vx = np.empty(8); vy = np.empty(8); rb = np.full(6, -1, np.int64)
    Gfun = make_G(d)
    zr = zg + h
    sx, sy, sz, sgv = S.x.values, S.y.values, S.z.values, S.zg.values
    P = 10 ** (0.1 * S.LW_day.values); main = S.main.values.astype(bool)
    Rm, Rn = float(p["max_dist_main"]), float(p["max_dist_minor"])
    E0 = EA = EB = EC = 0.0
    n_sh = n_lat = 0
    for j in range(len(S)):
        dx, dy = sx[j] - x, sy[j] - y
        Rmax = Rm if main[j] else Rn
        if dx * dx + dy * dy > Rmax * Rmax:
            continue
        vx[0], vy[0], vx[1], vy[1] = sx[j], sy[j], x, y
        hs = sz[j] - sgv[j]
        path_geometry(vx, vy, 2, sz[j], zr, hs, h, gnd, dsm, bid, G, x0, y0, res, nx, ny, float(p["profile_step"]),
                      -1, rb, float(p["skip_radius"]), hd, hh, geom)
        atten_iso(geom, bands, lams, alphas, False, float(br["C2"]), float(br["dz_max_single"]),
                  float(br["dz_max_double"]), out)
        e_v = wts[0] * 10 ** (-0.1 * out[0])
        E0 += P[j] * e_v
        eA = eB = eC = e_v
        if geom[G_SHIELD] > 0.5:
            n_sh += 1
            # 지형만의 단면(건물 없음): 지형이 막으면 측면 회절 없음, 막지 않으면 이 값이 '차폐 없을 때' 상한
            path_geometry(vx, vy, 2, sz[j], zr, hs, h, gnd, gnd, bid, G, x0, y0, res, nx, ny,
                          float(p["profile_step"]), -1, rb, float(p["skip_radius"]), hd, hh, geom)
            if geom[G_SHIELD] < 0.5:
                atten_iso(geom, bands, lams, alphas, False, float(br["C2"]), float(br["dz_max_single"]),
                          float(br["dz_max_double"]), out)
                e_free = wts[0] * 10 ** (-0.1 * out[0])
                line = LineString([(sx[j], sy[j]), (x, y)])
                L = line.length
                zfun = (lambda s, a=sz[j], b=zr, L=L: a + (b - a) * s / L)
                X = Bd.crossing(line, zfun)
                if X:
                    dd = math.hypot(L, zr - sz[j])
                    chains = lateral_chains(np.array([sx[j], sy[j]]), np.array([x, y]),
                                            np.vstack([Bd.verts[i] for i in X]))
                    addA = addB = 0.0
                    for ch in chains:
                        A, z, k = lateral_atten(ch, sz[j], zr, hs, h, dd, f, lam, alpha, Gfun,
                                                float(br["C2"]), float(br["dz_max_single"]), float(br["dz_max_double"]))
                        e = wts[0] * 10 ** (-0.1 * A)
                        addA += e
                        pl = LineString(ch)
                        Lh = pl.length
                        zf2 = (lambda s, a=sz[j], b=zr, Lh=Lh: a + (b - a) * s / Lh)
                        if not Bd.crossing(pl, zf2, exclude=set(X)):
                            addB += e
                    addC = 0.0
                    for ch in grown_chains(np.array([sx[j], sy[j]]), np.array([x, y]), Bd, X, sz[j], zr):
                        A, z, k = lateral_atten(ch, sz[j], zr, hs, h, dd, f, lam, alpha, Gfun,
                                                float(br["C2"]), float(br["dz_max_single"]), float(br["dz_max_double"]))
                        addC += wts[0] * 10 ** (-0.1 * A)
                    if chains:
                        n_lat += 1
                    eA = min(e_v + addA, max(e_free, e_v))
                    eB = min(e_v + addB, max(e_free, e_v))
                    eC = min(e_v + addC, max(e_free, e_v))
        EA += P[j] * eA
        EB += P[j] * eB
        EC += P[j] * eC
    stats["pairs_shielded"] = n_sh
    stats["pairs_lateral"] = n_lat
    return E0, EA, EB, EC


def pick_receivers(out, Bd, n_tall, n_random, seed=0):
    """대표 수음점: (1) 높이 ≥ 20 m·짧은 변 ≤ 20 m 건물 둘레 2~20 m 격자점 중 직접음이 가장 작은 곳(그림자),
    건물마다 1개, 높은 건물부터 n_tall 개, (2) 격자 전체에서 무작위 n_random 개"""
    from shapely.geometry import Point
    G = pd.read_csv(Path(out) / "grid_result.csv")
    G = G[np.isclose(G.h, G.h.min())].reset_index(drop=True)
    B = Bd.B.copy()
    w = []
    for g in B.geometry:
        r = g.minimum_rotated_rectangle
        c = np.asarray(r.exterior.coords)
        e = np.hypot(*np.diff(c, axis=0).T)[:2] if len(c) >= 3 else [0.0]
        w.append(float(min(e)))
    B["w"] = w
    xmin, xmax, ymin, ymax = G.x.min(), G.x.max(), G.y.min(), G.y.max()
    T = B[(B.H >= 20) & (B.w <= 20) & (B.w > 2)]
    T = T[T.geometry.centroid.x.between(xmin + 20, xmax - 20) & T.geometry.centroid.y.between(ymin + 20, ymax - 20)]
    T = T.sort_values("H", ascending=False)
    from scipy.spatial import cKDTree
    tree = cKDTree(G[["x", "y"]].values)
    rows = []
    for b in T.itertuples():
        c = b.geometry.centroid
        idx = tree.query_ball_point([c.x, c.y], b.geometry.length / 2 + 25)
        cand = [i for i in idx if 2.0 <= b.geometry.distance(Point(G.x[i], G.y[i])) <= 20.0]
        if not cand:
            continue
        i = min(cand, key=lambda i: G.LrT_direct[i])
        rows.append(dict(group="높고 좁은 건물 뒤", bid=int(b.bid), H=float(b.H), w=float(b.w), gi=i))
        if len(rows) >= n_tall:
            break
    rng = np.random.default_rng(seed)
    for i in rng.choice(len(G), size=min(n_random, len(G)), replace=False):
        rows.append(dict(group="격자 무작위", bid=-1, H=np.nan, w=np.nan, gi=int(i)))
    R = pd.DataFrame(rows)
    for c in ("x", "y", "z", "h", "LrT", "LrT_direct"):
        R[c] = G[c].values[R.gi]
    R["zg"] = R.z - R.h
    return R


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("config"); ap.add_argument("out"); ap.add_argument("dest")
    ap.add_argument("--n-tall", type=int, default=40); ap.add_argument("--n-random", type=int, default=40)
    a = ap.parse_args(argv)
    cfg = C.load(a.config)
    cfg["project"]["output_dir"] = str(Path(a.out).resolve())
    model = get_model(cfg)
    if model.name != "iso9613_2":
        raise SystemExit("ISO 9613-2 결과 폴더만(측면 회절 식 13 은 ISO 방법)")
    d, S, F, _ = RUN.load_model(cfg["project"]["output_dir"])
    Bd = Buildings(Path(a.out) / "buildings.gpkg")
    R = pick_receivers(a.out, Bd, a.n_tall, a.n_random)
    res = []
    for r in R.itertuples():
        st = {}
        t0 = time.perf_counter()
        E0, EA, EB, EC = receiver_levels(cfg, d, S, Bd, r.x, r.y, r.zg, r.h, model, st)
        dt = time.perf_counter() - t0
        L_all = 10 ** (0.1 * r.LrT)                                  # 반사 포함 지금 값
        res.append(dict(direct_check=10 * np.log10(E0) - r.LrT_direct,
                        dL_direct_A=10 * np.log10(EA / E0), dL_direct_B=10 * np.log10(EB / E0),
                        dL_direct_C=10 * np.log10(EC / E0),
                        dLrT_A=10 * np.log10((L_all + EA - E0) / L_all), dLrT_B=10 * np.log10((L_all + EB - E0) / L_all),
                        dLrT_C=10 * np.log10((L_all + EC - E0) / L_all),
                        t_s=dt, **st))
        print(f"{r.group} ({r.x:.1f}, {r.y:.1f}) LrT {r.LrT:.1f}  ΔLrT A {res[-1]['dLrT_A']:+.2f} "
              f"B {res[-1]['dLrT_B']:+.2f} C {res[-1]['dLrT_C']:+.2f}  쌍 {st['pairs_lateral']}/{st['pairs_shielded']}  {dt:.1f}s", flush=True)
    R = pd.concat([R.reset_index(drop=True), pd.DataFrame(res)], axis=1)
    dest = Path(a.dest)
    dest.mkdir(parents=True, exist_ok=True)
    R.to_csv(dest / "lateral_impact_points.csv", index=False, encoding="utf-8-sig")
    summ = {}
    for g, s in R.groupby("group"):
        summ[g] = {k: {"mean": round(float(s[k].mean()), 2), "median": round(float(s[k].median()), 2),
                       "p90": round(float(s[k].quantile(0.9)), 2), "max": round(float(s[k].max()), 2)}
                   for k in ("dLrT_A", "dLrT_B", "dLrT_C", "dL_direct_A", "dL_direct_B", "dL_direct_C")}
        summ[g]["n"] = int(len(s))
        summ[g]["t_s_mean"] = round(float(s.t_s.mean()), 2)
    summ["direct_check_max_abs_dB"] = float(R.direct_check.abs().max())
    (dest / "lateral_impact_summary.json").write_text(json.dumps(summ, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summ, ensure_ascii=False, indent=2))
    return R


if __name__ == "__main__":
    main()
