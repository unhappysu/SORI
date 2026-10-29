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
from .geometry import (NGEOM, G_D, G_SGR, G_HM, G_HS, G_HR, G_GS, G_GR, G_GM, G_SHIELD, G_DOUBLE,
                       G_Z, G_DSS, G_DSR, G_E, path_geometry)

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


# ------------------------------------------------------------------ 경로 감쇠
@njit(cache=True)
def atten_iso(geom, bands, lams, alphas, ground_alt, C2, dzmax1, dzmax2, out):
    """geometry.path_geometry 의 기하로 대역별 순감쇠 (A − Dc) 를 out[b] 에 기록.
      - 지면: 일반법(Gs/Gr/Gm은 경로를 따라 G 래스터 평균) 또는 대안법(hm=F/s_gr, Dc = DΩ)
      - 차폐: 식 (12) Abar = Dz − Agr > 0, 단일/이중 회절(C3)"""
    d = geom[G_D]; sgr = geom[G_SGR]; hs = geom[G_HS]; hr = geom[G_HR]
    shielded = geom[G_SHIELD] > 0.5
    double = geom[G_DOUBLE] > 0.5
    adiv = A_div(d)
    for b in range(bands.shape[0]):
        f = bands[b]
        if ground_alt:
            agr = A_gr_alternative(geom[G_HM], d)
            dc = D_omega(sgr, hs, hr)
        else:
            agr = A_gr_general(f, hs, hr, sgr, geom[G_GS], geom[G_GR], geom[G_GM])
            dc = 0.0
        A = adiv + A_atm(alphas[b], d) + agr
        if shielded:
            dz = D_z(geom[G_Z], geom[G_DSS], geom[G_DSR], d, geom[G_E], lams[b], C2, double, dzmax1, dzmax2)
            abar = dz - agr                          # 식 (12)
            if abar > 0.0:
                A += abar
        out[b] = A - dc


@njit(cache=True)
def path_loss(vx, vy, nv, zs, zr, hs, hr,
              gnd, dsm, bid, Gmap, x0, y0, res, nx, ny, step,
              rbid, refl_bid, skip_r,
              bands, lams, alphas, ground_alt, C2, dzmax1, dzmax2,
              hd, hh, out):
    """공통 기하(geometry.path_geometry) + ISO 감쇠(atten_iso). 반환값: 3D 전파거리 d"""
    geom = np.empty(NGEOM)
    d = path_geometry(vx, vy, nv, zs, zr, hs, hr, gnd, dsm, bid, Gmap, x0, y0, res, nx, ny, step,
                      rbid, refl_bid, skip_r, hd, hh, geom)
    atten_iso(geom, bands, lams, alphas, ground_alt, C2, dzmax1, dzmax2, out)
    return d


# 감쇠항별 중간값 색인(terms_iso)
T_ADIV, T_AATM, T_AGR, T_DOMEGA, T_DZ, T_ABAR, T_NET = range(7)
TERM_NAMES = ("A_div", "A_atm", "A_gr", "D_Omega", "D_z", "A_bar", "A_net")


@njit(cache=True)
def terms_iso(geom, f, lam, alpha, ground_alt, C2, dzmax1, dzmax2, t):
    """대역 f 의 감쇠항별 중간값 [dB] 을 t 에 기록: A_div 식(7), A_atm 식(8), A_gr 식(9) 또는 (10),
    D_Ω 식(11)(대안법만, 아니면 0), D_z 식(14)(차폐 없으면 0), A_bar 식(12)(>0 일 때만, 아니면 0),
    A_net = A − D_c (atten_iso 와 같은 순서로 계산)"""
    d = geom[G_D]; sgr = geom[G_SGR]; hs = geom[G_HS]; hr = geom[G_HR]
    t[T_ADIV] = A_div(d); t[T_AATM] = A_atm(alpha, d)
    if ground_alt:
        t[T_AGR] = A_gr_alternative(geom[G_HM], d)
        t[T_DOMEGA] = D_omega(sgr, hs, hr)
    else:
        t[T_AGR] = A_gr_general(f, hs, hr, sgr, geom[G_GS], geom[G_GR], geom[G_GM])
        t[T_DOMEGA] = 0.0
    A = t[T_ADIV] + t[T_AATM] + t[T_AGR]
    t[T_DZ] = 0.0; t[T_ABAR] = 0.0
    if geom[G_SHIELD] > 0.5:
        t[T_DZ] = D_z(geom[G_Z], geom[G_DSS], geom[G_DSR], d, geom[G_E], lam, C2, geom[G_DOUBLE] > 0.5,
                      dzmax1, dzmax2)
        abar = t[T_DZ] - t[T_AGR]
        if abar > 0.0:
            t[T_ABAR] = abar
            A += abar
    t[T_NET] = A - t[T_DOMEGA]
