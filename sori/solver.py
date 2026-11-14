# -*- coding: utf-8 -*-
"""
수음점별 에너지 합산.  Lr = 10 lg Σ_i Σ_b w_b · 10^0.1(LW_i − (A − Dc)_ib)   (ISO 9613-2 식 3·5 / RLS-19 식 2)
  - 직접음 + 1차·2차·3차 반사(허상음원, ISO 9613-2 7.5 / RLS-19 3.6). 3차는 국내 「소음지도의 작성방법」
    별표 2 제8호(반사차수 3차 이상 원칙) — RLS-19 3.6(1·2차)을 넘는 조건
  - 4차 이상(최대 MAXO 차, _order_n): 3차와 같은 이웃 목록·조건을 차수만 늘린 일반 탐색. 차수가 하나 늘 때마다
    탐색량이 이웃 면 수만큼 곱해짐. 4차 이상 성분은 E4(7번째 반환값)에 합침
  - 2·3차 탐색 제한(계산량, 프로젝트 설정): 마지막 반사면이 수음점 R2 이내, 앞 반사면은 다음 반사면과
    중심 거리 R2 이내이고 서로 마주보는 면. 3차는 Python 에서 만든 이웃 목록(nb_ptr, nb_idx, facade_neighbors) 사용
  - 전파 방법 meth: METH_RLS19(RLS-19 3.5 식 11~17) | METH_ISO(ISO 9613-2 7장) | METH_RLS90(RLS-90 4.4.2 식 19~27).
    기하는 geometry.path_geometry 공통
  - 반사 조건 — ISO: 거울반사 작도 가능, ρ > 0.2, 크기 조건 식 (19) (대역별)
               RLS-19: 거울반사 작도 가능, h_R ≥ 1.0 m 이고 h_R ≥ 0.3·√a_R (3.6)
               RLS-90: 거울반사 작도 가능, h_R ≥ 0.3·√a_R (4.6), a_R = 음원–반사체 거리
  - 허상음원 이득 gain(인자 rho): ISO 식 (20) ρ (DIr = 0, 무지향) / RLS-19 10^(−0.1·D_RV) (표 8). 2차 gain², 3차 gain³
  - 수음점이 놓인 파사드: RLS-19 1장 'Immissionsort' 주석 "마지막 반사가 수음점이 속한 외벽에서 일어나는 경로는
    고려하지 않음" → 마지막 반사면에서만 제외(own_all = False, 기본). own_all = True 는 2026-10-29 이전 동작
    (모든 반사 순서에서 제외) — 원래 코드 기준선 비교용
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import math

import numpy as np

from . import njit, prange
from .geometry import NGEOM, G_D, G_SGR, G_HM, G_SHIELD, G_Z, MAXO, MAXV, path_geometry_b
from . import lateral as LAT
from .iso9613 import atten_iso, terms_iso
from .rls19_propagation import accept_reflection, atten_rls19, terms_rls19
from . import rls90_propagation as R90

HULL_N = 8192
METH_RLS19 = 0
METH_ISO = 1
METH_RLS90 = 2


@njit(cache=True)
def _path(meth, vx, vy, nv, zs, zr, hs, hr, gnd, dsm, bid, Gmap, x0, y0, res, nx, ny, step,
          rbid, refl_bid, skip_r, bands, lams, alphas, ground_alt, C2, dzmax1, dzmax2, hd, hh, geom, out, bars,
          cmet0=0.0):
    """공통 기하 → 방법별 감쇠를 out[b] 에 기록"""
    path_geometry_b(vx, vy, nv, zs, zr, hs, hr, gnd, dsm, bid, Gmap, x0, y0, res, nx, ny, step,
                    rbid, refl_bid, skip_r, hd, hh, geom, bars)
    if meth == METH_RLS19:
        atten_rls19(geom, out)
    elif meth == METH_RLS90:
        R90.atten_rls90(geom, out)
    else:
        atten_iso(geom, bands, lams, alphas, ground_alt, C2, dzmax1, dzmax2, out, cmet0)


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
    if meth == METH_RLS19 or meth == METH_RLS90:
        ok = accept_reflection(hR, min(dso, dor)) if meth == METH_RLS19 else R90.accept_reflection(hR, dso)
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


@njit(cache=True)
def _order_n(n, xr, yr, zr, hr, rbid_r, rfid_r, cand, nc,
             sx, sy, sz, sg, Pd, Pn, smain,
             fx1, fy1, fx2, fy2, fnx, fny, fz0, fzt, fL, fbid, rho,
             gnd, dsm, bid, Gmap, x0, y0, res, nx, ny, step, Rsm, Rsn, R2, skip_r,
             bands, lams, alphas, wts, ground_alt, C2, dz1, dz2, meth, nb_ptr, nb_idx,
             own_all, bars, hd, hh, geom, out, inc, vx, vy, rb, rec_j, rec_f, rec_p, rec_n, fatt, cmet0=0.0):
    """n 차 반사(n ≥ 4) 한 수음점의 에너지 (주간, 야간).
      rec_*: 경로 기록(경로 보기용, sori/paths.py). rec_j.shape[0] = 0 이면 기록하지 않음(계산 커널).
      기록하면 경로마다 음원 rec_j, 에너지 계수 rec_f(대역 가중·반사 이득·감쇠 포함, 음원 파워에 곱함),
      반사점 rec_p[k, 2i:2i+2](음원 쪽부터), 개수 rec_n[0]. 자리가 모자라면 기록만 멈춤(합은 그대로)"" 3차와 같은 규칙을 차수 n 으로 일반화:
      면 사슬 cf[0](수음점 쪽, 후보 면·수음점 R2 이내) ← cf[1] ← … ← cf[n−1](음원 쪽), 이웃은 facade_neighbors
      거울상 q[0] = R 의 cf[0] 거울상, q[i] = q[i−1] 의 cf[i] 거울상. 반사점은 음원에서 q[n−1] 쪽으로 차례로 작도"""
    NS = sx.shape[0]; NB = bands.shape[0]
    cf = np.empty(MAXO, dtype=np.int64); it = np.empty(MAXO, dtype=np.int64)
    qx = np.empty(MAXO); qy = np.empty(MAXO); dq = np.empty(MAXO)
    px = np.empty(MAXO); py = np.empty(MAXO); sl = np.empty(MAXO + 1); cs = np.empty(MAXO)
    gain = 1.0
    for _ in range(n):
        gain *= rho
    ed = 0.0; en = 0.0
    for ci in range(nc):
        f0 = cand[ci]
        ex = fx2[f0] - fx1[f0]; ey = fy2[f0] - fy1[f0]
        L2 = ex * ex + ey * ey
        dr = (xr - fx1[f0]) * fnx[f0] + (yr - fy1[f0]) * fny[f0]
        t0 = ((xr - fx1[f0]) * ex + (yr - fy1[f0]) * ey) / L2
        t0 = min(1.0, max(0.0, t0))
        wx = fx1[f0] + t0 * ex - xr; wy = fy1[f0] + t0 * ey - yr
        if wx * wx + wy * wy > R2 * R2:
            continue
        cf[0] = f0; dq[0] = dr
        qx[0] = xr - 2 * dr * fnx[f0]; qy[0] = yr - 2 * dr * fny[f0]
        depth = 1
        it[1] = nb_ptr[f0]
        while depth >= 1:
            if depth < n:                               # 사슬에 면 하나 더
                p = cf[depth - 1]
                if it[depth] >= nb_ptr[p + 1]:
                    depth -= 1
                    continue
                g = nb_idx[it[depth]]
                it[depth] += 1
                if own_all and g == rfid_r:
                    continue
                d = (qx[depth - 1] - fx1[g]) * fnx[g] + (qy[depth - 1] - fy1[g]) * fny[g]
                if d <= 0.05:
                    continue
                if not _beam(qx[depth - 1], qy[depth - 1], fx1[p], fy1[p], fx2[p], fy2[p], fnx[p], fny[p],
                             fx1[g], fy1[g], fx2[g], fy2[g]):
                    continue
                cf[depth] = g; dq[depth] = d
                qx[depth] = qx[depth - 1] - 2 * d * fnx[g]; qy[depth] = qy[depth - 1] - 2 * d * fny[g]
                depth += 1
                if depth < n:
                    it[depth] = nb_ptr[g]
                continue
            # 사슬 완성: 음원마다 반사점 작도
            for j in range(NS):
                dx = sx[j] - xr; dy = sy[j] - yr
                R = Rsm if smain[j] else Rsn
                if dx * dx + dy * dy > R * R:
                    continue
                ax = sx[j]; ay = sy[j]
                ok = True
                Lt = 0.0
                for k in range(n):                      # k 번째 반사(음원 쪽부터): 면 cf[n−1−k], 목표 거울상 q[n−1−k]
                    m = n - 1 - k
                    f = cf[m]
                    ds = (ax - fx1[f]) * fnx[f] + (ay - fy1[f]) * fny[f]
                    if ds <= 0.05:
                        ok = False
                        break
                    u = ds / (ds + dq[m])
                    bx = ax + u * (qx[m] - ax); by = ay + u * (qy[m] - ay)
                    fex = fx2[f] - fx1[f]; fey = fy2[f] - fy1[f]
                    tt = ((bx - fx1[f]) * fex + (by - fy1[f]) * fey) / (fex * fex + fey * fey)
                    if tt < 0.0 or tt > 1.0:
                        ok = False
                        break
                    seg = math.sqrt((bx - ax) ** 2 + (by - ay) ** 2)
                    if seg < 0.1:
                        ok = False
                        break
                    px[k] = bx; py[k] = by; sl[k] = seg; cs[k] = ds / seg
                    Lt += seg
                    ax = bx; ay = by
                if not ok:
                    continue
                seg = math.sqrt((xr - ax) ** 2 + (yr - ay) ** 2)
                if seg < 0.1:
                    continue
                sl[n] = seg; Lt += seg
                acc = 0.0
                for k in range(n):                      # 반사점 높이가 면 높이 범위 안인지
                    acc += sl[k]
                    f = cf[n - 1 - k]
                    zp = sz[j] + (zr - sz[j]) * acc / Lt
                    if zp > fzt[f] or zp < fz0[f]:
                        ok = False
                        break
                if not ok:
                    continue
                for b in range(NB):
                    inc[b] = True
                acc = 0.0
                for k in range(n):
                    acc += sl[k]
                    f = cf[n - 1 - k]
                    hF = fzt[f] - fz0[f]
                    if not _accept(meth, lams, min(fL[f], hF), cs[k], acc, Lt - acc, inc, hF):
                        ok = False
                        break
                if not ok:
                    continue
                vx[0] = sx[j]; vy[0] = sy[j]
                for k in range(n):
                    vx[k + 1] = px[k]; vy[k + 1] = py[k]; rb[k] = fbid[cf[n - 1 - k]]
                vx[n + 1] = xr; vy[n + 1] = yr
                _path(meth, vx, vy, n + 2, sz[j], zr, sz[j] - sg[j], hr, gnd, dsm, bid, Gmap,
                      x0, y0, res, nx, ny, step, rbid_r, rb, skip_r,
                      bands, lams, alphas, ground_alt, C2, dz1, dz2, hd, hh, geom, out, bars, cmet0)
                ga = gain
                for k in range(n):                       # 반사면별 추가 계수(방음벽 반사 저감, 건물 = 1)
                    ga *= fatt[cf[k]]
                F = _band_factor(out, wts, inc, ga)
                ed += Pd[j] * F; en += Pn[j] * F
                if rec_j.shape[0] > 0 and rec_n[0] < rec_j.shape[0]:
                    q = rec_n[0]
                    rec_j[q] = j; rec_f[q] = F
                    for k in range(n):
                        rec_p[q, 2 * k] = px[k]; rec_p[q, 2 * k + 1] = py[k]
                    rec_n[0] = q + 1
            depth -= 1
    return ed, en


@njit(cache=True)
def compute(rx, ry, rz, rg, rbid, rfid, order,
            sx, sy, sz, sg, Pd, Pn, smain,
            fx1, fy1, fx2, fy2, fnx, fny, fz0, fzt, fL, fbid, rho,
            gnd, dsm, bid, Gmap, x0, y0, res, nx, ny, step,
            Rmain, Rminor, Rf, Rsm, Rsn, R2, skip_r,
            bands, lams, alphas, wts, ground_alt, C2, dz1, dz2, meth, nb_ptr, nb_idx, own_all):
    """방음벽 없는 계산(이전 호출 형식 유지) — compute_b 에 빈 방음벽 배열을 넘김"""
    o = compute_b(rx, ry, rz, rg, rbid, rfid, order, sx, sy, sz, sg, Pd, Pn, smain,
                  fx1, fy1, fx2, fy2, fnx, fny, fz0, fzt, fL, fbid, rho,
                  gnd, dsm, bid, Gmap, x0, y0, res, nx, ny, step, Rmain, Rminor, Rf, Rsm, Rsn, R2, skip_r,
                  bands, lams, alphas, wts, ground_alt, C2, dz1, dz2, meth, nb_ptr, nb_idx, own_all,
                  np.zeros((0, 5)))
    return o[0], o[1], o[2], o[3], o[4], o[5]


@njit(cache=True)
def compute_b(rx, ry, rz, rg, rbid, rfid, order,
              sx, sy, sz, sg, Pd, Pn, smain,
              fx1, fy1, fx2, fy2, fnx, fny, fz0, fzt, fL, fbid, rho,
              gnd, dsm, bid, Gmap, x0, y0, res, nx, ny, step,
              Rmain, Rminor, Rf, Rsm, Rsn, R2, skip_r,
              bands, lams, alphas, wts, ground_alt, C2, dz1, dz2, meth, nb_ptr, nb_idx, own_all, bars):
    """측면 회절 없는 계산(이전 호출 형식 유지). 반환 (Ed, En, Edir, E1, E2, E3, E4)"""
    o = compute_l(rx, ry, rz, rg, rbid, rfid, order, sx, sy, sz, sg, Pd, Pn, smain,
                  fx1, fy1, fx2, fy2, fnx, fny, fz0, fzt, fL, fbid, rho,
                  gnd, dsm, bid, Gmap, x0, y0, res, nx, ny, step, Rmain, Rminor, Rf, Rsm, Rsn, R2, skip_r,
                  bands, lams, alphas, wts, ground_alt, C2, dz1, dz2, meth, nb_ptr, nb_idx, own_all, bars,
                  False, 0, np.zeros(bars.shape[0], np.int64), np.zeros((0, 2)), np.zeros(1, np.int64),
                  np.ones(fx1.shape[0]))
    return o[0], o[1], o[2], o[3], o[4], o[5], o[6]


@njit(parallel=True, cache=True)
def compute_l(rx, ry, rz, rg, rbid, rfid, order,
              sx, sy, sz, sg, Pd, Pn, smain,
              fx1, fy1, fx2, fy2, fnx, fny, fz0, fzt, fL, fbid, rho,
              gnd, dsm, bid, Gmap, x0, y0, res, nx, ny, step,
              Rmain, Rminor, Rf, Rsm, Rsn, R2, skip_r,
              bands, lams, alphas, wts, ground_alt, C2, dz1, dz2, meth, nb_ptr, nb_idx, own_all, bars,
              lat_on, lat_grow, bgid, bxy, bptr, fatt, cmet0=0.0):
    """bars[n, 5] = 방음벽 선분 x1, y1, x2, y2, 지면 위 높이(geometry.path_geometry_b), bgid = 조각별 방음벽 번호.
    lat_on: 측면 회절(lateral.lateral_factor, ISO 방법만), bxy·bptr = 건물 외곽선(건물 번호 = bid).
    cmet0 = ISO 9613-2 식 (22) C0(dB, 0 = 기상 보정 없음 L_AT(DW), ISO 방법만).
    fatt[NF] = 반사면별 추가 이득 계수(방음벽 반사 저감: 표 8·표 7 값 / 기본 이득, 건물·반사형 = 1).
    반환 (Ed, En, Edir, E1, E2, E3, E4, Elat): E4 = 4차 이상 반사, Elat = 측면 회절로 더해진 에너지(주간)"""
    NR = rx.shape[0]; NS = sx.shape[0]; NF = fx1.shape[0]; NB = bands.shape[0]
    Ed = np.zeros(NR); En = np.zeros(NR)
    Edir = np.zeros(NR); E1 = np.zeros(NR); E2 = np.zeros(NR); E3 = np.zeros(NR); E4 = np.zeros(NR)
    Elat = np.zeros(NR)
    lg_rho = rho
    nobars = np.zeros((0, 5))
    for r in prange(NR):
        hd = np.empty(HULL_N); hh = np.empty(HULL_N); out = np.empty(NB); geom = np.empty(NGEOM)
        inc = np.ones(NB, dtype=np.bool_)
        vx = np.empty(MAXV); vy = np.empty(MAXV)
        rb = np.full(MAXO, -1, dtype=np.int64)
        xr = rx[r]; yr = ry[r]; zr = rz[r]; hr = zr - rg[r]
        ed = 0.0; en = 0.0; e_dir = 0.0; e1 = 0.0; e2 = 0.0; e3 = 0.0; e4 = 0.0; elat = 0.0
        np_ = LAT.MAXP if lat_on else 1
        nx_ = LAT.MAXX if lat_on else 1
        lpx = np.empty(np_); lpy = np.empty(np_); lord = np.empty(np_, np.int64); lH = np.empty(2 * np_, np.int64)
        lcx = np.empty(np_); lcy = np.empty(np_)
        lXb = np.empty(nx_, np.int64); lXg = np.empty(nx_, np.int64)
        lXb2 = np.empty(nx_, np.int64); lXg2 = np.empty(nx_, np.int64)
        gfree = np.empty(NGEOM); ofree = np.empty(NB)
        # ---------------- 직접음
        for j in range(NS):
            dx = sx[j] - xr; dy = sy[j] - yr
            R = Rmain if smain[j] else Rminor
            if dx * dx + dy * dy > R * R:
                continue
            vx[0] = sx[j]; vy[0] = sy[j]; vx[1] = xr; vy[1] = yr
            _path(meth, vx, vy, 2, sz[j], zr, sz[j] - sg[j], hr, gnd, dsm, bid, Gmap,
                      x0, y0, res, nx, ny, step, rbid[r], rb, skip_r,
                      bands, lams, alphas, ground_alt, C2, dz1, dz2, hd, hh, geom, out, bars, cmet0)
            for b in range(NB):
                inc[b] = True
            F = _band_factor(out, wts, inc, 1.0)
            ed += Pd[j] * F; en += Pn[j] * F; e_dir += Pd[j] * F
            if lat_on and meth == METH_ISO and geom[G_SHIELD] > 0.5:
                # 지형만의 단면(건물·방음벽 없음): 지형이 막으면 측면 회절 없음, 아니면 이 값이 상한(TR 17534-3 5.6)
                path_geometry_b(vx, vy, 2, sz[j], zr, sz[j] - sg[j], hr, gnd, gnd, bid, Gmap, x0, y0, res, nx, ny,
                                step, rbid[r], rb, skip_r, hd, hh, gfree, nobars)
                if gfree[G_SHIELD] < 0.5:
                    atten_iso(gfree, bands, lams, alphas, ground_alt, C2, dz1, dz2, ofree, cmet0)
                    Ffree = _band_factor(ofree, wts, inc, 1.0)
                    Fl = LAT.lateral_factor(sx[j], sy[j], sz[j], sz[j] - sg[j], xr, yr, zr, hr, rbid[r],
                                            gnd, dsm, bid, Gmap, x0, y0, res, nx, ny, step, skip_r,
                                            bars, bgid, bxy, bptr, bands, lams, alphas, wts, ground_alt,
                                            C2, dz1, dz2, lat_grow, lpx, lpy, lord, lH, lcx, lcy,
                                            lXb, lXg, lXb2, lXg2, cmet0)
                    if Fl > 0.0:
                        cap = Ffree if Ffree > F else F
                        add = F + Fl
                        if add > cap:
                            add = cap
                        add -= F
                        ed += Pd[j] * add; en += Pn[j] * add; elat += Pd[j] * add
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
                              bands, lams, alphas, ground_alt, C2, dz1, dz2, hd, hh, geom, out, bars, cmet0)
                    F = _band_factor(out, wts, inc, lg_rho * fatt[f])
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
                        if f1 == f2 or (own_all and f1 == rfid[r]):
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
                                      bands, lams, alphas, ground_alt, C2, dz1, dz2, hd, hh, geom, out, bars, cmet0)
                            F = _band_factor(out, wts, inc, lg_rho * lg_rho * fatt[f1] * fatt[f2])
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
                        if own_all and f2 == rfid[r]:
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
                            if own_all and f1 == rfid[r]:
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
                                      bands, lams, alphas, ground_alt, C2, dz1, dz2, hd, hh, geom, out, bars, cmet0)
                                F = _band_factor(out, wts, inc, lg_rho * lg_rho * lg_rho * fatt[f1] * fatt[f2] * fatt[f3])
                                ed += Pd[j] * F; en += Pn[j] * F; e3 += Pd[j] * F
            # ---------------- 4차 이상 반사(일반 탐색)
            if order >= 4:
                for n in range(4, min(order, MAXO) + 1):
                    a_d, a_n = _order_n(n, xr, yr, zr, hr, rbid[r], rfid[r], cand, nc,
                                        sx, sy, sz, sg, Pd, Pn, smain,
                                        fx1, fy1, fx2, fy2, fnx, fny, fz0, fzt, fL, fbid, rho,
                                        gnd, dsm, bid, Gmap, x0, y0, res, nx, ny, step, Rsm, Rsn, R2, skip_r,
                                        bands, lams, alphas, wts, ground_alt, C2, dz1, dz2, meth, nb_ptr, nb_idx,
                                        own_all, bars, hd, hh, geom, out, inc, vx, vy, rb,
                                        np.zeros(0, np.int64), np.zeros(0), np.zeros((0, 2 * MAXO)),
                                        np.zeros(1, np.int64), fatt, cmet0)
                    ed += a_d; en += a_n; e4 += a_d
        Ed[r] = ed; En[r] = en; Edir[r] = e_dir; E1[r] = e1; E2[r] = e2; E3[r] = e3; E4[r] = e4; Elat[r] = elat
    return Ed, En, Edir, E1, E2, E3, E4, Elat


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
    """방음벽 없는 경우(이전 호출 형식 유지)"""
    return point_paths_b(xr, yr, zr, zgr, rbidr, rfidr, order, sx, sy, sz, sg, smain,
                         fx1, fy1, fx2, fy2, fnx, fny, fz0, fzt, fL, fbid, rho,
                         gnd, dsm, bid, Gmap, x0, y0, res, nx, ny, step, Rmain, Rminor, Rf, Rsm, Rsn, skip_r,
                         bands, lams, alphas, ground_alt, C2, dz1, dz2, meth, np.zeros((0, 5)), np.ones(fx1.shape[0]))


@njit(cache=True)
def point_paths_b(xr, yr, zr, zgr, rbidr, rfidr, order,
                  sx, sy, sz, sg, smain,
                  fx1, fy1, fx2, fy2, fnx, fny, fz0, fzt, fL, fbid, rho,
                  gnd, dsm, bid, Gmap, x0, y0, res, nx, ny, step,
                  Rmain, Rminor, Rf, Rsm, Rsn, skip_r,
                  bands, lams, alphas, ground_alt, C2, dz1, dz2, meth, bars, fatt, cmet0=0.0):
    """수음점 1개의 직접음·1차 반사 경로별 기하와 감쇠항(대역별). compute 의 직접음·1차 반사와 같은 조건.
    반환 (info[n, NPINFO], terms[n, NTERM], band[n]). terms 열: RLS-19 = rls19_propagation.TERM_NAMES,
    ISO = iso9613.TERM_NAMES (RLS-19 는 앞 5칸만 사용). 2·3차 반사 경로는 넣지 않음"""
    NS = sx.shape[0]; NF = fx1.shape[0]; NB = bands.shape[0]
    hd = np.empty(HULL_N); hh = np.empty(HULL_N); geom = np.empty(NGEOM); t = np.zeros(NTERM)
    inc = np.ones(NB, dtype=np.bool_)
    vx = np.empty(MAXV); vy = np.empty(MAXV)
    rb = np.full(MAXO, -1, dtype=np.int64)
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
        path_geometry_b(vx, vy, 2, sz[j], zr, sz[j] - sg[j], hr, gnd, dsm, bid, Gmap, x0, y0, res, nx, ny, step,
                        rbidr, rb, skip_r, hd, hh, geom, bars)
        for b in range(NB):
            if n >= cap:
                break
            if meth == METH_RLS19:
                terms_rls19(geom, t)
            elif meth == METH_RLS90:
                R90.terms_rls90(geom, t)
            else:
                terms_iso(geom, bands[b], lams[b], alphas[b], ground_alt, C2, dz1, dz2, t, cmet0)
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
                path_geometry_b(vx, vy, 3, sz[j], zr, sz[j] - sg[j], hr, gnd, dsm, bid, Gmap, x0, y0, res, nx, ny,
                                step, rbidr, rb, skip_r, hd, hh, geom, bars)
                for b in range(NB):
                    if not inc[b] or n >= cap:
                        continue
                    if meth == METH_RLS19:
                        terms_rls19(geom, t)
                    elif meth == METH_RLS90:
                        R90.terms_rls90(geom, t)
                    else:
                        terms_iso(geom, bands[b], lams[b], alphas[b], ground_alt, C2, dz1, dz2, t, cmet0)
                    info[n, P_J] = j; info[n, P_ORDER] = 1; info[n, P_FACE] = f
                    info[n, P_PX] = px; info[n, P_PY] = py
                    info[n, P_D] = geom[G_D]; info[n, P_SGR] = geom[G_SGR]; info[n, P_HM] = geom[G_HM]
                    info[n, P_Z] = geom[G_Z] if geom[G_SHIELD] > 0.5 else 0.0
                    info[n, P_GAIN] = rho * fatt[f]
                    for q in range(NTERM):
                        terms[n, q] = t[q]
                    band[n] = b; n += 1
    return info[:n], terms[:n], band[:n]
