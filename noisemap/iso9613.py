# -*- coding: utf-8 -*-
"""
ISO 9613-2:1996 전파(감쇠) 모듈.  L = LW + Dc − A,  A = Adiv + Aatm + Agr + Abar (+Amisc=0)
식·표 번호는 ISO 9613-2 원문 번호를 따름. 대기흡수계수는 ISO 9613-1:1993 식으로 계산.
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.

RLS-19 음원과의 결합(Technical Manual 참조)
  RLS-19의 LW는 반공간(Ω=2π) 방사 기준. ISO 일반법(7.3.1)에서 단단한 지면(G=0)·근거리(q=0)는
  Agr = −3 dB 이므로 L = LW − 20lg d − 11 + 3 = LW − 10lg(2πd²) 가 되어 두 체계가 일치함.
  → 일반법에서는 Dc = 0, 대안법(7.3.2)에서는 Dc = DΩ(식 11).
"""
import math

import numpy as np

from . import njit

C_SOUND = 340.0                                     # ISO 9613-2 7.5: λ = 340/f


def alpha_iso9613_1(f, T_C, RH, pa=101.325):
    """ISO 9613-1 대기흡수계수 [dB/km] (ISO 9613-2 표 2 재현 확인됨)"""
    T = T_C + 273.15
    T0, T01, pr = 293.15, 273.16, 101.325
    C = -6.8346 * (T01 / T) ** 1.261 + 4.6151
    h = RH * 10 ** C / (pa / pr)
    frO = (pa / pr) * (24 + 4.04e4 * h * (0.02 + h) / (0.391 + h))
    frN = (pa / pr) * (T / T0) ** -0.5 * (9 + 280 * h * math.exp(-4.170 * ((T / T0) ** (-1 / 3) - 1)))
    a = 8.686 * f * f * (1.84e-11 * (pr / pa) * (T / T0) ** 0.5 + (T / T0) ** -2.5 * (
        0.01275 * math.exp(-2239.1 / T) / (frO + f * f / frO)
        + 0.1068 * math.exp(-3352.0 / T) / (frN + f * f / frN)))
    return a * 1000.0


# ------------------------------------------------------------------ 7.1 / 7.2
@njit(cache=True)
def A_div(d):
    """식 (7)"""
    return 20.0 * math.log10(max(d, 0.1)) + 11.0


@njit(cache=True)
def A_atm(alpha, d):
    """식 (8), alpha [dB/km]"""
    return alpha * d / 1000.0


# ------------------------------------------------------------------ 7.3.1 일반법 (표 3)
@njit(cache=True)
def _a_s(f, G, h, dp):
    e50 = 1.0 - math.exp(-dp / 50.0)
    if f < 90.0:                                     # 63 Hz
        return -1.5
    if f < 180.0:                                    # 125 Hz: a'(h)
        a = 1.5 + 3.0 * math.exp(-0.12 * (h - 5.0) ** 2) * e50 \
            + 5.7 * math.exp(-0.09 * h * h) * (1.0 - math.exp(-2.8e-6 * dp * dp))
        return -1.5 + G * a
    if f < 355.0:                                    # 250 Hz: b'(h)
        return -1.5 + G * (1.5 + 8.6 * math.exp(-0.09 * h * h) * e50)
    if f < 710.0:                                    # 500 Hz: c'(h)
        return -1.5 + G * (1.5 + 14.0 * math.exp(-0.46 * h * h) * e50)
    if f < 1420.0:                                   # 1 kHz: d'(h)
        return -1.5 + G * (1.5 + 5.0 * math.exp(-0.9 * h * h) * e50)
    return -1.5 * (1.0 - G)                          # 2–8 kHz


@njit(cache=True)
def A_gr_general(f, hs, hr, dp, Gs, Gr, Gm):
    """식 (9) Agr = As + Ar + Am (표 3)"""
    lim = 30.0 * (hs + hr)
    q = 0.0 if dp <= lim else 1.0 - lim / dp
    Am = -3.0 * q if f < 90.0 else -3.0 * q * (1.0 - Gm)
    return _a_s(f, Gs, hs, dp) + _a_s(f, Gr, hr, dp) + Am


@njit(cache=True)
def A_gr_alternative(hm, d):
    """식 (10) Agr = 4.8 − (2hm/d)[17 + 300/d] ≥ 0"""
    v = 4.8 - (2.0 * hm / d) * (17.0 + 300.0 / d)
    return v if v > 0.0 else 0.0


@njit(cache=True)
def D_omega(dp, hs, hr):
    """식 (11)"""
    return 10.0 * math.log10(1.0 + (dp * dp + (hs - hr) ** 2) / (dp * dp + (hs + hr) ** 2))


# ------------------------------------------------------------------ 7.4 차폐
@njit(cache=True)
def D_z(z, dss, dsr, d, e, lam, C2, double, dzmax1, dzmax2):
    """식 (14)(15)(18) 상단 회절 감쇠. z>0 일 때만 호출. 상한 20/25 dB"""
    if double and e > 0.0:
        r = (5.0 * lam / e) ** 2
        C3 = (1.0 + r) / (1.0 / 3.0 + r)
    else:
        C3 = 1.0
    Kmet = math.exp(-1.0 / 2000.0 * math.sqrt(dss * dsr * d / (2.0 * z))) if z > 0 else 1.0
    v = 10.0 * math.log10(3.0 + (C2 / lam) * C3 * z * Kmet)
    cap = dzmax2 if double else dzmax1
    return v if v < cap else cap


# ------------------------------------------------------------------ 경로 계산
@njit(cache=True)
def _cell(x0, y0, res, nx, ny, x, y):
    i = int((y0 - y) / res)
    j = int((x - x0) / res)
    if i < 0 or j < 0 or i >= ny or j >= nx:
        return -1
    return i * nx + j


@njit(cache=True)
def path_loss(vx, vy, nv, zs, zr, hs, hr,
              gnd, dsm, bid, Gmap, x0, y0, res, nx, ny, step,
              rbid, refl_bid, skip_r,
              bands, lams, alphas, ground_alt, C2, dzmax1, dzmax2,
              hd, hh, out):
    """
    음원 S(=v0) → [반사점 v1, v2] → 수음점 R(=v_{nv-1}) 의 (전개된) 수직단면에서
    대역별 순감쇠 (A − Dc) 를 out[b] 에 기록.
      - 차폐: 단면의 상부 볼록 껍질(고무줄법). 회절 모서리 1개=단일, 2개 이상=이중(첫·끝 모서리, e=사이 경로길이)
      - 지면: 일반법(Gs/Gr/Gm은 경로를 따라 G 래스터 평균) 또는 대안법(hm=F/d)
      - 래스터 이산화 보정: 음원 1.5 m 이내 건물, 수음점 소속 건물의 수음점 1.5 m 이내,
        반사 건물의 반사점 1.5 m 이내 표본은 지면 높이로 대체
    반환값: 3D 전파거리 d
    """
    # 각 구간 길이(수평)
    cum0 = 0.0
    legs = np.empty(4)
    cum = np.empty(4)
    cum[0] = 0.0
    for k in range(nv - 1):
        legs[k] = math.sqrt((vx[k + 1] - vx[k]) ** 2 + (vy[k + 1] - vy[k]) ** 2)
        cum0 += legs[k]
        cum[k + 1] = cum0
    sgr = cum0
    if sgr < 0.1:
        sgr = 0.1
    d = math.sqrt(sgr * sgr + (zr - zs) ** 2)
    n = int(sgr / step)
    hd[0] = 0.0
    hh[0] = zs
    k = 1
    area = 0.0
    Gs_sum = 0.0; Gs_n = 0
    Gr_sum = 0.0; Gr_n = 0
    Gm_sum = 0.0; Gm_n = 0
    lim_s = 30.0 * hs
    lim_r = 30.0 * hr
    leg = 0
    for t in range(n):
        dd = (t + 0.5) * step
        if dd > sgr - 0.25:
            break
        while leg < nv - 2 and dd > cum[leg + 1]:
            leg += 1
        f = (dd - cum[leg]) / legs[leg] if legs[leg] > 0 else 0.0
        x = vx[leg] + f * (vx[leg + 1] - vx[leg])
        y = vy[leg] + f * (vy[leg + 1] - vy[leg])
        idx = _cell(x0, y0, res, nx, ny, x, y)
        if idx < 0:
            continue
        g = gnd.flat[idx]
        G = Gmap.flat[idx]
        insrc = dd <= lim_s
        inrec = (sgr - dd) <= lim_r
        if insrc:
            Gs_sum += G; Gs_n += 1
        if inrec:
            Gr_sum += G; Gr_n += 1
        if (not insrc) and (not inrec):
            Gm_sum += G; Gm_n += 1
        zl = zs + (zr - zs) * dd / sgr
        if zl - g > 0.0:
            area += (zl - g) * step
        h = dsm.flat[idx]
        b = bid.flat[idx]
        if b >= 0:
            if dd < skip_r:
                h = g
            elif b == rbid and (sgr - dd) < skip_r:
                h = g
            else:
                for q in range(1, nv - 1):
                    if b == refl_bid[q - 1] and abs(dd - cum[q]) < skip_r:
                        h = g
        if h > zl:
            while k >= 2:
                ax = hd[k - 1] - hd[k - 2]; ay = hh[k - 1] - hh[k - 2]
                bx = dd - hd[k - 2]; by = h - hh[k - 2]
                if ax * by - ay * bx >= 0.0:
                    k -= 1
                else:
                    break
            hd[k] = dd; hh[k] = h; k += 1
    while k >= 2:
        ax = hd[k - 1] - hd[k - 2]; ay = hh[k - 1] - hh[k - 2]
        bx = sgr - hd[k - 2]; by = zr - hh[k - 2]
        if ax * by - ay * bx >= 0.0:
            k -= 1
        else:
            break
    hd[k] = sgr; hh[k] = zr; k += 1

    # 지면 계수 (표본이 없으면 G 기본값으로 인접 영역값 사용)
    Gs = Gs_sum / Gs_n if Gs_n > 0 else (Gr_sum / Gr_n if Gr_n > 0 else 0.0)
    Gr = Gr_sum / Gr_n if Gr_n > 0 else Gs
    Gm = Gm_sum / Gm_n if Gm_n > 0 else 0.5 * (Gs + Gr)
    hm = area / sgr

    # 차폐 기하
    z = 0.0
    dss = 0.0; dsr = 0.0; e = 0.0
    shielded = k > 2
    double = k > 3
    if shielded:
        dss = math.sqrt((hd[1] - hd[0]) ** 2 + (hh[1] - hh[0]) ** 2)
        dsr = math.sqrt((hd[k - 1] - hd[k - 2]) ** 2 + (hh[k - 1] - hh[k - 2]) ** 2)
        for q in range(1, k - 2):
            e += math.sqrt((hd[q + 1] - hd[q]) ** 2 + (hh[q + 1] - hh[q]) ** 2)
        z = dss + e + dsr - d                        # 식 (16)(17), a = 0 (수직단면)
        if z <= 0.0:
            shielded = False

    adiv = A_div(d)
    for b in range(bands.shape[0]):
        f = bands[b]
        if ground_alt:
            agr = A_gr_alternative(hm, d)
            dc = D_omega(sgr, hs, hr)
        else:
            agr = A_gr_general(f, hs, hr, sgr, Gs, Gr, Gm)
            dc = 0.0
        A = adiv + A_atm(alphas[b], d) + agr
        if shielded:
            dz = D_z(z, dss, dsr, d, e, lams[b], C2, double, dzmax1, dzmax2)
            abar = dz - agr                          # 식 (12)
            if abar > 0.0:
                A += abar
        out[b] = A - dc
    return d
