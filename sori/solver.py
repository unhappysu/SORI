# -*- coding: utf-8 -*-
"""
수음점별 에너지 합산.  Lr = 10 lg Σ_i Σ_b w_b · 10^0.1(LW_i − (A − Dc)_ib)   (ISO 9613-2 식 3·5 / RLS-19 식 2)
  - 직접음 + 1차·2차·3차 반사(허상음원, ISO 9613-2 7.5 / RLS-19 3.6). 3차는 국내 「소음지도의 작성방법」
    별표 2 제8호(반사차수 3차 이상 원칙) — RLS-19 3.6(1·2차)을 넘는 조건
  - 2·3차 탐색 제한(계산량, 프로젝트 설정): 마지막 반사면이 수음점 R2 이내, 앞 반사면은 다음 반사면과
    중심 거리 R2 이내이고 서로 마주보는 면. 3차는 Python 에서 만든 이웃 목록(nb_ptr, nb_idx, facade_neighbors) 사용
  - 전파 방법 meth: METH_RLS19(RLS-19 3.5 식 11~17) | METH_ISO(ISO 9613-2 7장). 기하는 geometry.path_geometry 공통
  - 반사 조건 — ISO: 거울반사 작도 가능, ρ > 0.2, 크기 조건 식 (19) (대역별)
               RLS-19: 거울반사 작도 가능, h_R ≥ 1.0 m 이고 h_R ≥ 0.3·√a_R (3.6)
  - 허상음원 이득 gain(인자 rho): ISO 식 (20) ρ (DIr = 0, 무지향) / RLS-19 10^(−0.1·D_RV) (표 8). 2차 gain², 3차 gain³
  - 수음점이 놓인 파사드 자신의 반사는 제외(RLS-19 1장 'Immissionsort')
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import math

import numpy as np

from . import njit, prange
from .geometry import NGEOM, G_D, G_SGR, G_HM, G_SHIELD, G_Z, path_geometry
from .iso9613 import atten_iso, terms_iso
from .rls19_propagation import accept_reflection, atten_rls19, terms_rls19

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


@njit(cache=True)
def _beam(qx, qy, ax1, ay1, ax2, ay2, anx, any_, bx1, by1, bx2, by2):
    """3차 반사 탐색 가지치기(결과 불변). Q 는 면 a 뒤의 거울상. 면 b 위의 어떤 점 E 에서 Q 로 가는 선분이
    면 a 선분을 지날 수 있으면 True(필요조건). b 가 a 앞쪽에 완전히 있지 않으면 판단하지 않고 True."""
    dq = -((qx - ax1) * anx + (qy - ay1) * any_)
    if dq <= 0.0:
        return True
    ex = ax2 - ax1; ey = ay2 - ay1
    L2 = ex * ex + ey * ey
    tmin = 1e300; tmax = -1e300
    for k in range(2):
        px = bx1 if k == 0 else bx2
        py = by1 if k == 0 else by2
        de = (px - ax1) * anx + (py - ay1) * any_
        if de <= 0.0:
            return True
        u = dq / (dq + de)                       # Q → E 선분이 면 a 의 직선과 만나는 위치(중심 투영, E 에 대해 연속)
        X = qx + u * (px - qx); Y = qy + u * (py - qy)
        t = ((X - ax1) * ex + (Y - ay1) * ey) / L2
        tmin = min(tmin, t); tmax = max(tmax, t)
    return tmax >= 0.0 and tmin <= 1.0


@njit(parallel=True, cache=True)
def compute(rx, ry, rz, rg, rbid, rfid, order,
            sx, sy, sz, sg, Pd, Pn, smain,
            fx1, fy1, fx2, fy2, fnx, fny, fz0, fzt, fL, fbid, rho,
            gnd, dsm, bid, Gmap, x0, y0, res, nx, ny, step,
            Rmain, Rminor, Rf, Rsm, Rsn, R2, skip_r,
            bands, lams, alphas, wts, ground_alt, C2, dz1, dz2, meth, nb_ptr, nb_idx):
    NR = rx.shape[0]; NS = sx.shape[0]; NF = fx1.shape[0]; NB = bands.shape[0]
    Ed = np.zeros(NR); En = np.zeros(NR)
    Edir = np.zeros(NR); E1 = np.zeros(NR); E2 = np.zeros(NR); E3 = np.zeros(NR)
    lg_rho = rho
    for r in prange(NR):
        hd = np.empty(HULL_N); hh = np.empty(HULL_N); out = np.empty(NB); geom = np.empty(NGEOM)
        inc = np.ones(NB, dtype=np.bool_)
        vx = np.empty(5); vy = np.empty(5)
        rb = np.full(3, -1, dtype=np.int64)
        xr = rx[r]; yr = ry[r]; zr = rz[r]; hr = zr - rg[r]
        ed = 0.0; en = 0.0; e_dir = 0.0; e1 = 0.0; e2 = 0.0; e3 = 0.0
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
            # ---------------- 3차 반사: S → P1(f1) → P2(f2) → P3(f3) → R  (f1 = f3 허용: 마주보는 두 벽 사이 왕복)
            if order >= 3:
                for ci in range(nc):
                    f3 = cand[ci]
                    ex3 = fx2[f3] - fx1[f3]; ey3 = fy2[f3] - fy1[f3]
                    L32 = ex3 * ex3 + ey3 * ey3
                    dr3 = (xr - fx1[f3]) * fnx[f3] + (yr - fy1[f3]) * fny[f3]
                    t0 = ((xr - fx1[f3]) * ex3 + (yr - fy1[f3]) * ey3) / L32
                    t0 = min(1.0, max(0.0, t0))
                    qx = fx1[f3] + t0 * ex3 - xr; qy = fy1[f3] + t0 * ey3 - yr
                    if qx * qx + qy * qy > R2 * R2:
                        continue
                    q1x = xr - 2 * dr3 * fnx[f3]; q1y = yr - 2 * dr3 * fny[f3]        # R 의 f3 거울상
                    lmin3 = min(fL[f3], fzt[f3] - fz0[f3])
                    for k2 in range(nb_ptr[f3], nb_ptr[f3 + 1]):
                        f2 = nb_idx[k2]
                        if f2 == rfid[r]:
                            continue
                        dQ1 = (q1x - fx1[f2]) * fnx[f2] + (q1y - fy1[f2]) * fny[f2]
                        if dQ1 <= 0.05:
                            continue
                        if not _beam(q1x, q1y, fx1[f3], fy1[f3], fx2[f3], fy2[f3], fnx[f3], fny[f3],
                                     fx1[f2], fy1[f2], fx2[f2], fy2[f2]):
                            continue                                                 # f2 → f3 → R 경로 불가
                        q2x = q1x - 2 * dQ1 * fnx[f2]; q2y = q1y - 2 * dQ1 * fny[f2]    # 다시 f2 거울상
                        ex2 = fx2[f2] - fx1[f2]; ey2 = fy2[f2] - fy1[f2]
                        L22 = ex2 * ex2 + ey2 * ey2
                        lmin2 = min(fL[f2], fzt[f2] - fz0[f2])
                        for k1 in range(nb_ptr[f2], nb_ptr[f2 + 1]):
                            f1 = nb_idx[k1]
                            if f1 == rfid[r]:
                                continue
                            dQ2 = (q2x - fx1[f1]) * fnx[f1] + (q2y - fy1[f1]) * fny[f1]
                            if dQ2 <= 0.05:
                                continue
                            if not _beam(q2x, q2y, fx1[f2], fy1[f2], fx2[f2], fy2[f2], fnx[f2], fny[f2],
                                         fx1[f1], fy1[f1], fx2[f1], fy2[f1]):
                                continue                                             # f1 → f2 경로 불가
                            q3x = q2x - 2 * dQ2 * fnx[f1]; q3y = q2y - 2 * dQ2 * fny[f1]  # 다시 f1 거울상
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
                                u1 = ds1 / (ds1 + dQ2)
                                p1x = sx[j] + u1 * (q3x - sx[j]); p1y = sy[j] + u1 * (q3y - sy[j])
                                t1 = ((p1x - fx1[f1]) * ex1 + (p1y - fy1[f1]) * ey1) / L12
                                if t1 < 0.0 or t1 > 1.0:
                                    continue
                                dP1 = (p1x - fx1[f2]) * fnx[f2] + (p1y - fy1[f2]) * fny[f2]
                                if dP1 <= 0.05:
                                    continue
                                u2 = dP1 / (dP1 + dQ1)
                                p2x = p1x + u2 * (q2x - p1x); p2y = p1y + u2 * (q2y - p1y)
                                t2 = ((p2x - fx1[f2]) * ex2 + (p2y - fy1[f2]) * ey2) / L22
                                if t2 < 0.0 or t2 > 1.0:
                                    continue
                                dP2 = (p2x - fx1[f3]) * fnx[f3] + (p2y - fy1[f3]) * fny[f3]
                                if dP2 <= 0.05:
                                    continue
                                u3 = dP2 / (dP2 + dr3)
                                p3x = p2x + u3 * (q1x - p2x); p3y = p2y + u3 * (q1y - p2y)
                                t3 = ((p3x - fx1[f3]) * ex3 + (p3y - fy1[f3]) * ey3) / L32
                                if t3 < 0.0 or t3 > 1.0:
                                    continue
                                a = math.sqrt((p1x - sx[j]) ** 2 + (p1y - sy[j]) ** 2)
                                bb = math.sqrt((p2x - p1x) ** 2 + (p2y - p1y) ** 2)
                                c = math.sqrt((p3x - p2x) ** 2 + (p3y - p2y) ** 2)
                                e = math.sqrt((xr - p3x) ** 2 + (yr - p3y) ** 2)
                                if a < 0.1 or bb < 0.1 or c < 0.1 or e < 0.1:
                                    continue
                                Lt = a + bb + c + e
                                zp1 = sz[j] + (zr - sz[j]) * a / Lt
                                zp2 = sz[j] + (zr - sz[j]) * (a + bb) / Lt
                                zp3 = sz[j] + (zr - sz[j]) * (a + bb + c) / Lt
                                if (zp1 > fzt[f1] or zp1 < fz0[f1] or zp2 > fzt[f2] or zp2 < fz0[f2]
                                        or zp3 > fzt[f3] or zp3 < fz0[f3]):
                                    continue
                                for b in range(NB):
                                    inc[b] = True
                                if not _accept(meth, lams, lmin1, ds1 / a, a, bb + c + e, inc, fzt[f1] - fz0[f1]):
                                    continue
                                if not _accept(meth, lams, lmin2, dP1 / bb, a + bb, c + e, inc, fzt[f2] - fz0[f2]):
                                    continue
                                if not _accept(meth, lams, lmin3, dP2 / c, a + bb + c, e, inc, fzt[f3] - fz0[f3]):
                                    continue
                                vx[0] = sx[j]; vy[0] = sy[j]; vx[1] = p1x; vy[1] = p1y
                                vx[2] = p2x; vy[2] = p2y; vx[3] = p3x; vy[3] = p3y; vx[4] = xr; vy[4] = yr
                                rb[0] = fbid[f1]; rb[1] = fbid[f2]; rb[2] = fbid[f3]
                                _path(meth, vx, vy, 5, sz[j], zr, sz[j] - sg[j], hr, gnd, dsm, bid, Gmap,
                                      x0, y0, res, nx, ny, step, rbid[r], rb, skip_r,
                                      bands, lams, alphas, ground_alt, C2, dz1, dz2, hd, hh, geom, out)
                                F = _band_factor(out, wts, inc, lg_rho * lg_rho * lg_rho)
                                ed += Pd[j] * F; en += Pn[j] * F; e3 += Pd[j] * F
        Ed[r] = ed; En[r] = en; Edir[r] = e_dir; E1[r] = e1; E2[r] = e2; E3[r] = e3
    return Ed, En, Edir, E1, E2, E3


def facade_neighbors(fx1, fy1, fx2, fy2, fnx, fny, R2):
    """3차 반사 탐색용 이웃 목록(CSR): 중심 거리 R2 이내이고 서로 마주보는 면 쌍(2차 반사와 같은 조건).
    반환 (nb_ptr[NF+1], nb_idx). 대칭이며 자기 자신은 없음"""
    NF = len(fx1)
    if NF == 0:
        return np.zeros(1, np.int64), np.zeros(0, np.int64)
    from scipy.spatial import cKDTree
    cx = 0.5 * (np.asarray(fx1) + np.asarray(fx2)); cy = 0.5 * (np.asarray(fy1) + np.asarray(fy2))
    pairs = cKDTree(np.c_[cx, cy]).query_pairs(R2, output_type="ndarray")
    if len(pairs):
        i, k = pairs[:, 0], pairs[:, 1]
        ok = (((cx[k] - cx[i]) * fnx[i] + (cy[k] - cy[i]) * fny[i]) > 0.0) & \
             (((cx[i] - cx[k]) * fnx[k] + (cy[i] - cy[k]) * fny[k]) > 0.0)
        i, k = i[ok], k[ok]
        a = np.r_[i, k]; b = np.r_[k, i]
    else:
        a = b = np.zeros(0, np.int64)
    o = np.lexsort((b, a))
    a, b = a[o], b[o]
    ptr = np.zeros(NF + 1, np.int64)
    np.add.at(ptr, a + 1, 1)
    return np.cumsum(ptr).astype(np.int64), b.astype(np.int64)


def to_db(E):
    with np.errstate(divide="ignore"):
        return np.where(E > 0, 10.0 * np.log10(np.maximum(E, 1e-300)), np.nan)


# ---------------------------------------------------------------------- 감쇠항별 중간값(대표 수음점)
# 경로 정보 열: 음원 색인, 차수, 반사면 색인, 반사점 x, y, d, s_gr, h_m, z, 이득(에너지 비)
P_J, P_ORDER, P_FACE, P_PX, P_PY, P_D, P_SGR, P_HM, P_Z, P_GAIN = range(10)
NPINFO = 10
NTERM = 7


@njit(cache=True)
def point_paths(xr, yr, zr, zgr, rbidr, rfidr, order,
                sx, sy, sz, sg, smain,
                fx1, fy1, fx2, fy2, fnx, fny, fz0, fzt, fL, fbid, rho,
                gnd, dsm, bid, Gmap, x0, y0, res, nx, ny, step,
                Rmain, Rminor, Rf, Rsm, Rsn, skip_r,
                bands, lams, alphas, ground_alt, C2, dz1, dz2, meth):
    """수음점 1개의 직접음·1차 반사 경로별 기하와 감쇠항(대역별). compute 의 직접음·1차 반사와 같은 조건.
    반환 (info[n, NPINFO], terms[n, NTERM], band[n]). terms 열: RLS-19 = rls19_propagation.TERM_NAMES,
    ISO = iso9613.TERM_NAMES (RLS-19 는 앞 5칸만 사용). 2·3차 반사 경로는 넣지 않음"""
    NS = sx.shape[0]; NF = fx1.shape[0]; NB = bands.shape[0]
    hd = np.empty(HULL_N); hh = np.empty(HULL_N); geom = np.empty(NGEOM); t = np.zeros(NTERM)
    inc = np.ones(NB, dtype=np.bool_)
    vx = np.empty(5); vy = np.empty(5)
    rb = np.full(3, -1, dtype=np.int64)
    hr = zr - zgr
    cap = NS * (1 + (NF if order >= 1 else 0)) * NB
    cap = min(cap, 2000000)
    info = np.zeros((cap, NPINFO)); terms = np.zeros((cap, NTERM)); band = np.zeros(cap, np.int64)
    n = 0
    for j in range(NS):
        dx = sx[j] - xr; dy = sy[j] - yr
        R = Rmain if smain[j] else Rminor
        if dx * dx + dy * dy > R * R:
            continue
        vx[0] = sx[j]; vy[0] = sy[j]; vx[1] = xr; vy[1] = yr
        path_geometry(vx, vy, 2, sz[j], zr, sz[j] - sg[j], hr, gnd, dsm, bid, Gmap, x0, y0, res, nx, ny, step,
                      rbidr, rb, skip_r, hd, hh, geom)
        for b in range(NB):
            if n >= cap:
                break
            if meth == METH_RLS19:
                terms_rls19(geom, t)
            else:
                terms_iso(geom, bands[b], lams[b], alphas[b], ground_alt, C2, dz1, dz2, t)
            info[n, P_J] = j; info[n, P_ORDER] = 0; info[n, P_FACE] = -1
            info[n, P_PX] = np.nan; info[n, P_PY] = np.nan
            info[n, P_D] = geom[G_D]; info[n, P_SGR] = geom[G_SGR]; info[n, P_HM] = geom[G_HM]
            info[n, P_Z] = geom[G_Z] if geom[G_SHIELD] > 0.5 else 0.0
            info[n, P_GAIN] = 1.0
            for q in range(NTERM):
                terms[n, q] = t[q]
            band[n] = b; n += 1
    if order >= 1 and NF > 0:
        for f in range(NF):
            if f == rfidr:
                continue
            ex = fx2[f] - fx1[f]; ey = fy2[f] - fy1[f]
            L2 = ex * ex + ey * ey
            tt0 = ((xr - fx1[f]) * ex + (yr - fy1[f]) * ey) / L2
            tt0 = min(1.0, max(0.0, tt0))
            qx = fx1[f] + tt0 * ex - xr; qy = fy1[f] + tt0 * ey - yr
            if qx * qx + qy * qy > Rf * Rf:
                continue
            if (xr - fx1[f]) * fnx[f] + (yr - fy1[f]) * fny[f] <= 0.05:
                continue
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
                path_geometry(vx, vy, 3, sz[j], zr, sz[j] - sg[j], hr, gnd, dsm, bid, Gmap, x0, y0, res, nx, ny,
                              step, rbidr, rb, skip_r, hd, hh, geom)
                for b in range(NB):
                    if not inc[b] or n >= cap:
                        continue
                    if meth == METH_RLS19:
                        terms_rls19(geom, t)
                    else:
                        terms_iso(geom, bands[b], lams[b], alphas[b], ground_alt, C2, dz1, dz2, t)
                    info[n, P_J] = j; info[n, P_ORDER] = 1; info[n, P_FACE] = f
                    info[n, P_PX] = px; info[n, P_PY] = py
                    info[n, P_D] = geom[G_D]; info[n, P_SGR] = geom[G_SGR]; info[n, P_HM] = geom[G_HM]
                    info[n, P_Z] = geom[G_Z] if geom[G_SHIELD] > 0.5 else 0.0
                    info[n, P_GAIN] = rho
                    for q in range(NTERM):
                        terms[n, q] = t[q]
                    band[n] = b; n += 1
    return info[:n], terms[:n], band[:n]
