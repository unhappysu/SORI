# -*- coding: utf-8 -*-
"""
전파 경로의 공통 기하 계산 — RLS-19(3.5)와 ISO 9613-2(7장)가 함께 씀.
두 방법이 같은 기하를 쓰므로 결과 차이는 감쇠식의 차이에서만 생김.
  - 음원 S(=v0) → [반사점 v1, v2] → 수음점 R(=v_{nv-1}) 의 (전개된) 수직단면
  - 차폐: 단면의 상부 볼록 껍질(고무줄법, RLS-19 3.5.5 주석). 회절 모서리 1개=단일,
    2개 이상=다중(첫·끝 모서리, 사이 경로길이 = RLS-19 C = ISO e)
  - 평균 높이 h_m = F / s_gr (RLS-19 3.5.4, 그림 11). ISO 그림 3 의 h_m = F/d 의 d 가 수평거리인지는 근거 미확인
  - 지면계수 Gs/Gr/Gm: 경로를 따라 G 래스터 평균(ISO 7.3.1 영역 구분)
  - 래스터 이산화 보정: 음원 1.5 m 이내 건물, 수음점 소속 건물의 수음점 1.5 m 이내,
    반사 건물의 반사점 1.5 m 이내 표본은 지면 높이로 대체
결과는 길이 NGEOM 배열 geom 에 기록(아래 색인).
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import math

import numpy as np

from . import njit

# geom 색인
G_D, G_SGR, G_HM, G_HS, G_HR, G_GS, G_GR, G_GM, G_SHIELD, G_DOUBLE, G_Z, G_DSS, G_DSR, G_E = range(14)
NGEOM = 14


@njit(cache=True)
def _cell(x0, y0, res, nx, ny, x, y):
    i = int((y0 - y) / res)
    j = int((x - x0) / res)
    if i < 0 or j < 0 or i >= ny or j >= nx:
        return -1
    return i * nx + j


@njit(cache=True)
def path_geometry(vx, vy, nv, zs, zr, hs, hr,
                  gnd, dsm, bid, Gmap, x0, y0, res, nx, ny, step,
                  rbid, refl_bid, skip_r, hd, hh, geom):
    """기하량을 geom 에 기록. 반환값: 3D 전파거리 d (= RLS-19 s)"""
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
        z = dss + e + dsr - d                        # RLS-19 식 (16) = ISO 식 (16)(17), a = 0 (수직단면)
        if z <= 0.0:
            shielded = False

    geom[G_D] = d; geom[G_SGR] = sgr; geom[G_HM] = hm; geom[G_HS] = hs; geom[G_HR] = hr
    geom[G_GS] = Gs; geom[G_GR] = Gr; geom[G_GM] = Gm
    geom[G_SHIELD] = 1.0 if shielded else 0.0
    geom[G_DOUBLE] = 1.0 if double else 0.0
    geom[G_Z] = z; geom[G_DSS] = dss; geom[G_DSR] = dsr; geom[G_E] = e
    return d
