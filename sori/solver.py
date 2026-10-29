# -*- coding: utf-8 -*-
"""
수음점별 에너지 합산.  Lr = 10 lg Σ_i Σ_b w_b · 10^0.1(LW_i − (A − Dc)_ib)   (ISO 9613-2 식 3·5 / RLS-19 식 2)
  - 직접음 + 1차 반사 + 2차 반사(허상음원, ISO 9613-2 7.5 / RLS-19 3.6)
  - 전파 방법 meth: METH_RLS19(RLS-19 3.5 식 11~17) | METH_ISO(ISO 9613-2 7장). 기하는 geometry.path_geometry 공통
  - 반사 조건 — ISO: 거울반사 작도 가능, ρ > 0.2, 크기 조건 식 (19) (대역별)
               RLS-19: 거울반사 작도 가능, h_R ≥ 1.0 m 이고 h_R ≥ 0.3·√a_R (3.6)
  - 허상음원 이득 gain(인자 rho): ISO 식 (20) ρ (DIr = 0, 무지향) / RLS-19 10^(−0.1·D_RV) (표 8). 2차는 gain²
  - 수음점이 놓인 파사드 자신의 반사는 제외(RLS-19 1장 'Immissionsort')
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import math

import numpy as np

from . import njit, prange
from .geometry import NGEOM, path_geometry
from .iso9613 import atten_iso
from .rls19_propagation import accept_reflection, atten_rls19

HULL_N = 8192
METH_RLS19 = 0
METH_ISO = 1


@njit(cache=True)
def _path(meth, vx, vy, nv, zs, zr, hs, hr, gnd, dsm, bid, Gmap, x0, y0, res, nx, ny, step,
          rbid, refl_bid, skip_r, bands, lams, alphas, ground_alt, C2, dzmax1, dzmax2, hd, hh, geom, out):
    """공통 기하 → 방법별 감쇠를 out[b] 에 기록"""
    path_geometry(vx, vy, nv, zs, zr, hs, hr, gnd, dsm, bid, Gmap, x0, y0, res, nx, ny, step,
                  rbid, refl_bid, skip_r, hd, hh, geom)
    if meth == METH_RLS19:
        atten_rls19(geom, out)
    else:
        atten_iso(geom, bands, lams, alphas, ground_alt, C2, dzmax1, dzmax2, out)


@njit(cache=True)
def _band_factor(out, wts, inc, gain):
    s = 0.0
    for b in range(out.shape[0]):
        if inc[b]:
            s += wts[b] * 10.0 ** (-0.1 * out[b])
    return s * gain


@njit(cache=True)
def _crit(lams, lmin, cosb, dso, dor, inc):
    """식 (19): 1/λ > 2/(lmin cosβ)² · dso·dor/(dso+dor)  → 대역별 포함 여부. 하나라도 포함되면 True"""
    any_ok = False
    x = dso * dor / (dso + dor)
    lc = lmin * cosb
    for b in range(lams.shape[0]):
        ok = lc > 0.0 and (1.0 / lams[b]) > 2.0 / (lc * lc) * x
        inc[b] = inc[b] and ok
        any_ok = any_ok or inc[b]
    return any_ok


@njit(cache=True)
def _accept(meth, lams, lmin, cosb, dso, dor, inc, hR):
    """반사 채택 여부(대역별 inc 갱신). ISO: 식 (19) / RLS-19: 3.6 높이 조건(대역 무관)"""
    if meth == METH_RLS19:
        ok = accept_reflection(hR, min(dso, dor))
        for b in range(inc.shape[0]):
            inc[b] = inc[b] and ok
        return ok
    return _crit(lams, lmin, cosb, dso, dor, inc)


@njit(parallel=True, cache=True)
def compute(rx, ry, rz, rg, rbid, rfid, order,
            sx, sy, sz, sg, Pd, Pn, smain,
            fx1, fy1, fx2, fy2, fnx, fny, fz0, fzt, fL, fbid, rho,
            gnd, dsm, bid, Gmap, x0, y0, res, nx, ny, step,
            Rmain, Rminor, Rf, Rsm, Rsn, R2, skip_r,
            bands, lams, alphas, wts, ground_alt, C2, dz1, dz2, meth):
    NR = rx.shape[0]; NS = sx.shape[0]; NF = fx1.shape[0]; NB = bands.shape[0]
    Ed = np.zeros(NR); En = np.zeros(NR)
    Edir = np.zeros(NR); E1 = np.zeros(NR); E2 = np.zeros(NR)
    lg_rho = rho
    for r in prange(NR):
        hd = np.empty(HULL_N); hh = np.empty(HULL_N); out = np.empty(NB); geom = np.empty(NGEOM)
        inc = np.ones(NB, dtype=np.bool_)
        vx = np.empty(4); vy = np.empty(4)
        rb = np.full(2, -1, dtype=np.int64)
        xr = rx[r]; yr = ry[r]; zr = rz[r]; hr = zr - rg[r]
        ed = 0.0; en = 0.0; e_dir = 0.0; e1 = 0.0; e2 = 0.0
        # ---------------- 직접음
        for j in range(NS):
            dx = sx[j] - xr; dy = sy[j] - yr
            R = Rmain if smain[j] else Rminor
            if dx * dx + dy * dy > R * R:
                continue
            vx[0] = sx[j]; vy[0] = sy[j]; vx[1] = xr; vy[1] = yr
            _path(meth, vx, vy, 2, sz[j], zr, sz[j] - sg[j], hr, gnd, dsm, bid, Gmap,
                      x0, y0, res, nx, ny, step, rbid[r], rb, skip_r,
                      bands, lams, alphas, ground_alt, C2, dz1, dz2, hd, hh, geom, out)
            for b in range(NB):
                inc[b] = True
            F = _band_factor(out, wts, inc, 1.0)
            ed += Pd[j] * F; en += Pn[j] * F; e_dir += Pd[j] * F
        if order >= 1 and NF > 0:
            # 후보 반사면: 수음점 앞쪽, 거리 Rf 이내, 수음점 자신의 파사드 제외
            cand = np.empty(NF, dtype=np.int64); nc = 0
            for f in range(NF):
                if f == rfid[r]:
                    continue
                ex = fx2[f] - fx1[f]; ey = fy2[f] - fy1[f]
                L2 = ex * ex + ey * ey
                t = ((xr - fx1[f]) * ex + (yr - fy1[f]) * ey) / L2
                t = min(1.0, max(0.0, t))
                qx = fx1[f] + t * ex - xr; qy = fy1[f] + t * ey - yr
                if qx * qx + qy * qy > Rf * Rf:
                    continue
                if (xr - fx1[f]) * fnx[f] + (yr - fy1[f]) * fny[f] <= 0.05:
                    continue
                cand[nc] = f; nc += 1
            # ---------------- 1차 반사
            for ci in range(nc):
                f = cand[ci]
                ex = fx2[f] - fx1[f]; ey = fy2[f] - fy1[f]
                L2 = ex * ex + ey * ey
                dr = (xr - fx1[f]) * fnx[f] + (yr - fy1[f]) * fny[f]
                mx = xr - 2 * dr * fnx[f]; my = yr - 2 * dr * fny[f]
                hF = fzt[f] - fz0[f]
                lmin = min(fL[f], hF)
                for j in range(NS):
                    dx = sx[j] - xr; dy = sy[j] - yr
                    R = Rsm if smain[j] else Rsn
                    if dx * dx + dy * dy > R * R:
                        continue
                    ds = (sx[j] - fx1[f]) * fnx[f] + (sy[j] - fy1[f]) * fny[f]
                    if ds <= 0.05:
                        continue
                    u = ds / (ds + dr)
                    px = sx[j] + u * (mx - sx[j]); py = sy[j] + u * (my - sy[j])
                    tt = ((px - fx1[f]) * ex + (py - fy1[f]) * ey) / L2
                    if tt < 0.0 or tt > 1.0:
                        continue
                    d1 = math.sqrt((px - sx[j]) ** 2 + (py - sy[j]) ** 2)
                    d2 = math.sqrt((px - xr) ** 2 + (py - yr) ** 2)
                    if d1 < 0.1 or d2 < 0.1:
                        continue
                    zp = sz[j] + (zr - sz[j]) * d1 / (d1 + d2)
                    if zp > fzt[f] or zp < fz0[f]:
                        continue
                    for b in range(NB):
                        inc[b] = True
                    if not _accept(meth, lams, lmin, ds / d1, d1, d2, inc, hF):
                        continue
                    vx[0] = sx[j]; vy[0] = sy[j]; vx[1] = px; vy[1] = py; vx[2] = xr; vy[2] = yr
                    rb[0] = fbid[f]; rb[1] = -1
                    _path(meth, vx, vy, 3, sz[j], zr, sz[j] - sg[j], hr, gnd, dsm, bid, Gmap,
                              x0, y0, res, nx, ny, step, rbid[r], rb, skip_r,
                              bands, lams, alphas, ground_alt, C2, dz1, dz2, hd, hh, geom, out)
                    F = _band_factor(out, wts, inc, lg_rho)
                    ed += Pd[j] * F; en += Pn[j] * F; e1 += Pd[j] * F
            # ---------------- 2차 반사: S → P1(f1) → P2(f2) → R
            if order >= 2:
                for ci in range(nc):
                    f2 = cand[ci]
                    ex2 = fx2[f2] - fx1[f2]; ey2 = fy2[f2] - fy1[f2]
                    L22 = ex2 * ex2 + ey2 * ey2
                    dr2 = (xr - fx1[f2]) * fnx[f2] + (yr - fy1[f2]) * fny[f2]
                    t0 = ((xr - fx1[f2]) * ex2 + (yr - fy1[f2]) * ey2) / L22
                    t0 = min(1.0, max(0.0, t0))
                    qx = fx1[f2] + t0 * ex2 - xr; qy = fy1[f2] + t0 * ey2 - yr
                    if qx * qx + qy * qy > R2 * R2:
                        continue
                    r1x = xr - 2 * dr2 * fnx[f2]; r1y = yr - 2 * dr2 * fny[f2]
                    c2x = 0.5 * (fx1[f2] + fx2[f2]); c2y = 0.5 * (fy1[f2] + fy2[f2])
                    lmin2 = min(fL[f2], fzt[f2] - fz0[f2])
                    for f1 in range(NF):
                        if f1 == f2 or f1 == rfid[r]:
                            continue
                        c1x = 0.5 * (fx1[f1] + fx2[f1]); c1y = 0.5 * (fy1[f1] + fy2[f1])
                        if (c1x - c2x) ** 2 + (c1y - c2y) ** 2 > R2 * R2:
                            continue
                        # 서로 마주보는 면만
                        if (c2x - c1x) * fnx[f1] + (c2y - c1y) * fny[f1] <= 0.0:
                            continue
                        if (c1x - c2x) * fnx[f2] + (c1y - c2y) * fny[f2] <= 0.0:
                            continue
                        dR1 = (r1x - fx1[f1]) * fnx[f1] + (r1y - fy1[f1]) * fny[f1]
                        if dR1 <= 0.05:
                            continue
                        r2x = r1x - 2 * dR1 * fnx[f1]; r2y = r1y - 2 * dR1 * fny[f1]
                        ex1 = fx2[f1] - fx1[f1]; ey1 = fy2[f1] - fy1[f1]
                        L12 = ex1 * ex1 + ey1 * ey1
                        lmin1 = min(fL[f1], fzt[f1] - fz0[f1])
                        for j in range(NS):
                            dx = sx[j] - xr; dy = sy[j] - yr
                            R = Rsm if smain[j] else Rsn
                            if dx * dx + dy * dy > R * R:
                                continue
                            ds1 = (sx[j] - fx1[f1]) * fnx[f1] + (sy[j] - fy1[f1]) * fny[f1]
                            if ds1 <= 0.05:
                                continue
                            u1 = ds1 / (ds1 + dR1)
                            p1x = sx[j] + u1 * (r2x - sx[j]); p1y = sy[j] + u1 * (r2y - sy[j])
                            t1 = ((p1x - fx1[f1]) * ex1 + (p1y - fy1[f1]) * ey1) / L12
                            if t1 < 0.0 or t1 > 1.0:
                                continue
                            dP1 = (p1x - fx1[f2]) * fnx[f2] + (p1y - fy1[f2]) * fny[f2]
                            if dP1 <= 0.05:
                                continue
                            u2 = dP1 / (dP1 + dr2)
                            p2x = p1x + u2 * (r1x - p1x); p2y = p1y + u2 * (r1y - p1y)
                            t2 = ((p2x - fx1[f2]) * ex2 + (p2y - fy1[f2]) * ey2) / L22
                            if t2 < 0.0 or t2 > 1.0:
                                continue
                            a = math.sqrt((p1x - sx[j]) ** 2 + (p1y - sy[j]) ** 2)
                            bb = math.sqrt((p2x - p1x) ** 2 + (p2y - p1y) ** 2)
                            c = math.sqrt((xr - p2x) ** 2 + (yr - p2y) ** 2)
                            if a < 0.1 or bb < 0.1 or c < 0.1:
                                continue
                            Lt = a + bb + c
                            zp1 = sz[j] + (zr - sz[j]) * a / Lt
                            zp2 = sz[j] + (zr - sz[j]) * (a + bb) / Lt
                            if zp1 > fzt[f1] or zp1 < fz0[f1] or zp2 > fzt[f2] or zp2 < fz0[f2]:
                                continue
                            for b in range(NB):
                                inc[b] = True
                            if not _accept(meth, lams, lmin1, ds1 / a, a, bb + c, inc, fzt[f1] - fz0[f1]):
                                continue
                            if not _accept(meth, lams, lmin2, dP1 / bb, a + bb, c, inc, fzt[f2] - fz0[f2]):
                                continue
                            vx[0] = sx[j]; vy[0] = sy[j]; vx[1] = p1x; vy[1] = p1y
                            vx[2] = p2x; vy[2] = p2y; vx[3] = xr; vy[3] = yr
                            rb[0] = fbid[f1]; rb[1] = fbid[f2]
                            _path(meth, vx, vy, 4, sz[j], zr, sz[j] - sg[j], hr, gnd, dsm, bid, Gmap,
                                      x0, y0, res, nx, ny, step, rbid[r], rb, skip_r,
                                      bands, lams, alphas, ground_alt, C2, dz1, dz2, hd, hh, geom, out)
                            F = _band_factor(out, wts, inc, lg_rho * lg_rho)
                            ed += Pd[j] * F; en += Pn[j] * F; e2 += Pd[j] * F
        Ed[r] = ed; En[r] = en; Edir[r] = e_dir; E1[r] = e1; E2[r] = e2
    return Ed, En, Edir, E1, E2


def to_db(E):
    with np.errstate(divide="ignore"):
        return np.where(E > 0, 10.0 * np.log10(np.maximum(E, 1e-300)), np.nan)
