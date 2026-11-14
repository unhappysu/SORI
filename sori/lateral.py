# -*- coding: utf-8 -*-
"""
측면 회절(ISO 9613-2:1996 7.4 식 13) — 선택 기능(propagation.lateral.enabled, ISO 9613-2 방법만, 기본 끔).
설계·영향 확인: validation/lateral_diffraction/README.md (보완 사항 8번, 2026-11-13)

  대상   직접음 경로 중 연직 단면이 건물·방음벽으로 막힌 것. 지형만으로 막히면 없음(ISO/TR 17534-3 5.8),
         반사 경로(허상음원)에는 넣지 않음(CNOSSOS-EU 2021 개정 문구)
  경로   평면에서 음원 S·수음점 R 과 '직접 경로가 뚫고 지나는' 건물(래스터 표본에서 지붕이 가시선보다 높은 건물)·
         방음벽(교점 높이가 가시선보다 높은 방음벽, 그 방음벽 전체)의 꼭짓점을 감싸는 볼록 껍질(고무줄).
         껍질을 S·R 에서 나눈 왼쪽·오른쪽 사슬이 측면 경로(최대 2개). S 나 R 이 껍질 꼭짓점이 아니면 그 경로 없음.
         실제 음원점–수음점 선 기준으로 매번 만들므로 비스듬한 입사·회전한 건물·ㄱ자 건물도 그대로 다룸.
         높이는 S→R 직선 보간(평면 사슬을 펼친 길이 기준) — 높이 차가 큰 경우 CNOSSOS 의 기울어진 측면 평면과 조금 다름
  막힘   (변형 C, 프로젝트 해석) 측면 사슬을 다른 건물(지붕이 경로보다 높고 표본점이 외곽선 안)이나 방음벽이 막으면
         그것을 그 쪽 껍질에 더해 다시 돌아감. 좌우 따로, 최대 max_grow 번 넓혀도 막히면 그 쪽 경로는 버림
  감쇠   경로마다 A = A_div(직선 거리 d) + A_atm(펼친 경로 길이) + A_gr(측면 경로 아래 지면, 식 9 또는 10) + D_z − D_c
         D_z = 10 lg[3 + (C2/λ)·C3·z·K_met] (식 14), K_met = 1(7.4 측면 회절), z = 펼친 경로 길이 − d,
         C3 = 1(모서리 1개) / 식 (15)(모서리 2개 이상, e = 첫·끝 모서리 사이), 상한 20 / 25 dB
  합치기 윗모서리(연직) + 좌 + 우 에너지 합, 차폐가 없을 때(지형만 있는 단면) 값을 넘지 않게(ISO/TR 17534-3 5.6)
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import math

import numpy as np

from . import njit
from .geometry import _cell
from .iso9613 import A_atm, A_div, A_gr_alternative, A_gr_general, D_omega, C_met

MAXP = 4096          # 껍질 점 수 한도(넘으면 그 쌍은 측면 회절 없음)
MAXX = 256           # 껍질에 넣는 건물·방음벽 수 한도


@njit(cache=True)
def _cross(ax, ay, bx, by, cx, cy):
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)


@njit(cache=True)
def convex_hull(px, py, n, order, H):
    """단조 사슬(Andrew) 볼록 껍질. 반시계 방향 점 색인을 H 에 쓰고 개수 반환(일직선 점 제외)"""
    if n < 3:
        for i in range(n):
            H[i] = i
        return n
    o = np.argsort(px[:n])
    for i in range(n):
        order[i] = o[i]
    for i in range(1, n):                                   # x 가 같으면 y 오름차순(삽입 정렬, 같은 x 는 드묾)
        j = i
        while j > 0 and px[order[j - 1]] == px[order[j]] and py[order[j - 1]] > py[order[j]]:
            t = order[j - 1]; order[j - 1] = order[j]; order[j] = t
            j -= 1
    k = 0
    for i in range(n):
        p = order[i]
        while k >= 2 and _cross(px[H[k - 2]], py[H[k - 2]], px[H[k - 1]], py[H[k - 1]], px[p], py[p]) <= 0.0:
            k -= 1
        H[k] = p; k += 1
    t = k + 1
    for i in range(n - 2, -1, -1):
        p = order[i]
        while k >= t and _cross(px[H[k - 2]], py[H[k - 2]], px[H[k - 1]], py[H[k - 1]], px[p], py[p]) <= 0.0:
            k -= 1
        H[k] = p; k += 1
    return k - 1


@njit(cache=True)
def side_chain(px, py, H, h, side, cx, cy):
    """껍질에서 S(색인 0) → R(색인 1) 사슬 중 side(+1 = SR 왼쪽, −1 = 오른쪽) 쪽을 cx, cy 에 씀. 점 수 반환(없으면 0)"""
    iS = -1; iR = -1
    for i in range(h):
        if H[i] == 0:
            iS = i
        elif H[i] == 1:
            iR = i
    if iS < 0 or iR < 0:
        return 0
    for step in (1, -1):
        m = 0
        i = iS
        while True:
            cx[m] = px[H[i]]; cy[m] = py[H[i]]; m += 1
            if i == iR:
                break
            i = (i + step) % h
        if m < 3:
            continue
        s = _cross(px[0], py[0], px[1], py[1], cx[1], cy[1])
        if (s > 0.0 and side > 0) or (s < 0.0 and side < 0):
            return m
    return 0


@njit(cache=True)
def point_in_building(bxy, s, e, x, y):
    """건물 외곽선(닫힌 고리들, 고리 사이 NaN 줄) 안에 (x, y) 가 있는지(짝홀 규칙)"""
    inside = False
    for i in range(s, e - 1):
        x1 = bxy[i, 0]; y1 = bxy[i, 1]; x2 = bxy[i + 1, 0]; y2 = bxy[i + 1, 1]
        if math.isnan(x1) or math.isnan(x2):
            continue
        if (y1 > y) != (y2 > y):
            xi = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < xi:
                inside = not inside
    return inside


@njit(cache=True)
def _seg_hit(ax, ay, bx, by, w, bars):
    """선분 a→b 와 방음벽 조각 w 의 교점 위치(a 에서 거리 비 0~1), 없으면 −1"""
    ldx = bx - ax; ldy = by - ay
    bex = bars[w, 2] - bars[w, 0]; bey = bars[w, 3] - bars[w, 1]
    den = ldx * bey - ldy * bex
    if abs(den) < 1e-12:
        return -1.0
    wx = bars[w, 0] - ax; wy = bars[w, 1] - ay
    u = (wx * ldy - wy * ldx) / den
    if u < 0.0 or u > 1.0:
        return -1.0
    return (wx * bey - wy * bex) / den


@njit(cache=True)
def _has(X, n, v):
    for i in range(n):
        if X[i] == v:
            return True
    return False


@njit(cache=True)
def _blockers(cx, cy, m, zs, zr, Lh, rbid, gnd, dsm, bid, x0, y0, res, nx, ny, step, skip_r,
              bars, bgid, bxy, bptr, Xb, nb, Xg, ng):
    """사슬 cx, cy(m 점)를 막는 건물·방음벽을 Xb·Xg 끝에 더하고 새 개수 (nb, ng, 더한 수) 반환"""
    added = 0
    cum = 0.0
    for q in range(m - 1):
        lx = cx[q + 1] - cx[q]; ly = cy[q + 1] - cy[q]
        L = math.sqrt(lx * lx + ly * ly)
        if L <= 0.0:
            continue
        nn = int(L / step)
        for t in range(nn + 1):
            dl = (t + 0.5) * step
            if dl >= L:
                break
            dd = cum + dl
            if dd < skip_r or Lh - dd < skip_r:
                continue
            x = cx[q] + lx * dl / L; y = cy[q] + ly * dl / L
            idx = _cell(x0, y0, res, nx, ny, x, y)
            if idx < 0:
                continue
            b = bid.flat[idx]
            if b < 0 or b == rbid or _has(Xb, nb, b):
                continue
            if dsm.flat[idx] <= zs + (zr - zs) * dd / Lh:
                continue
            if not point_in_building(bxy, bptr[b], bptr[b + 1], x, y):
                continue
            if nb < Xb.shape[0]:
                Xb[nb] = b; nb += 1; added += 1
        for w in range(bars.shape[0]):
            g = bgid[w]
            if _has(Xg, ng, g):
                continue
            f = _seg_hit(cx[q], cy[q], cx[q + 1], cy[q + 1], w, bars)
            if f < 0.0:
                continue
            dd = cum + f * L
            if dd < 0.1 or Lh - dd < 0.1:
                continue
            idx = _cell(x0, y0, res, nx, ny, cx[q] + lx * f, cy[q] + ly * f)
            if idx < 0:
                continue
            if gnd.flat[idx] + bars[w, 4] > zs + (zr - zs) * dd / Lh and ng < Xg.shape[0]:
                Xg[ng] = g; ng += 1; added += 1
        cum += L
    return nb, ng, added


@njit(cache=True)
def _chain_factor(cx, cy, m, zs, zr, hs, hr, d, gnd, Gmap, x0, y0, res, nx, ny, step,
                  bands, lams, alphas, wts, ground_alt, C2, dz1, dz2, dp=0.0, cmet0=0.0):
    """측면 경로 하나의 에너지 계수 Σ_b w_b·10^(−0.1·A_b) (식 13)"""
    Lh = 0.0
    for q in range(m - 1):
        Lh += math.sqrt((cx[q + 1] - cx[q]) ** 2 + (cy[q + 1] - cy[q]) ** 2)
    if Lh < 0.1:
        return 0.0
    L3 = math.sqrt(Lh * Lh + (zr - zs) ** 2)
    z = L3 - d
    if z < 0.0:
        z = 0.0
    k = m - 2                                             # 모서리 수
    e = 0.0
    if k >= 2:
        s0 = math.sqrt((cx[1] - cx[0]) ** 2 + (cy[1] - cy[0]) ** 2)
        s1 = math.sqrt((cx[m - 1] - cx[m - 2]) ** 2 + (cy[m - 1] - cy[m - 2]) ** 2)
        e = (Lh - s0 - s1) * L3 / Lh
    # 지면: 경로를 따라 G 평균(음원 30·hs, 수음점 30·hr 영역), 평균 높이(대안법)
    Gs_s = 0.0; Gs_n = 0; Gr_s = 0.0; Gr_n = 0; Gm_s = 0.0; Gm_n = 0
    area = 0.0
    lim_s = 30.0 * hs; lim_r = 30.0 * hr
    cum = 0.0
    for q in range(m - 1):
        lx = cx[q + 1] - cx[q]; ly = cy[q + 1] - cy[q]
        L = math.sqrt(lx * lx + ly * ly)
        if L <= 0.0:
            continue
        nn = int(L / step)
        for t in range(nn + 1):
            dl = (t + 0.5) * step
            if dl >= L:
                break
            dd = cum + dl
            idx = _cell(x0, y0, res, nx, ny, cx[q] + lx * dl / L, cy[q] + ly * dl / L)
            if idx < 0:
                continue
            G = Gmap.flat[idx]
            area += (zs + (zr - zs) * dd / Lh - gnd.flat[idx]) * step
            if dd <= lim_s:
                Gs_s += G; Gs_n += 1
            if Lh - dd <= lim_r:
                Gr_s += G; Gr_n += 1
            if dd > lim_s and Lh - dd > lim_r:
                Gm_s += G; Gm_n += 1
        cum += L
    Gs = Gs_s / Gs_n if Gs_n > 0 else (Gr_s / Gr_n if Gr_n > 0 else 0.0)
    Gr = Gr_s / Gr_n if Gr_n > 0 else Gs
    Gm = Gm_s / Gm_n if Gm_n > 0 else 0.5 * (Gs + Gr)
    hm = area / Lh
    adiv = A_div(d)
    F = 0.0
    for b in range(bands.shape[0]):
        lam = lams[b]
        if ground_alt:
            agr = A_gr_alternative(hm, L3)
            dc = D_omega(Lh, hs, hr)
        else:
            agr = A_gr_general(bands[b], hs, hr, Lh, Gs, Gr, Gm)
            dc = 0.0
        if k >= 2 and e > 0.0:
            r = (5.0 * lam / e) ** 2
            C3 = (1.0 + r) / (1.0 / 3.0 + r)
            cap = dz2
        else:
            C3 = 1.0
            cap = dz1 if k < 2 else dz2
        dz = 10.0 * math.log10(3.0 + (C2 / lam) * C3 * z)
        if dz > cap:
            dz = cap
        A = adiv + A_atm(alphas[b], L3) + agr + dz - dc + C_met(cmet0, hs, hr, dp)   # C_met 은 직선 d_p 기준
        F += wts[b] * 10.0 ** (-0.1 * A)
    return F


@njit(cache=True)
def lateral_factor(xs, ys, zs, hs, xr, yr, zr, hr, rbid, gnd, dsm, bid, Gmap, x0, y0, res, nx, ny, step, skip_r,
                   bars, bgid, bxy, bptr, bands, lams, alphas, wts, ground_alt, C2, dz1, dz2, maxgrow,
                   px, py, order, H, cx, cy, Xb, Xg, Xb2, Xg2, cmet0=0.0):
    """한 음원점–수음점 쌍의 측면 경로(좌·우) 에너지 계수 합. 직접 경로를 뚫는 건물·방음벽이 없으면 0.
    작업 배열: px, py, order(MAXP), H(2·MAXP), cx, cy(MAXP), Xb, Xg, Xb2, Xg2(MAXX)"""
    Lsr = math.sqrt((xr - xs) ** 2 + (yr - ys) ** 2)
    if Lsr < 0.5:
        return 0.0
    d = math.sqrt(Lsr * Lsr + (zr - zs) ** 2)
    # 직접 경로를 뚫는 건물(래스터 표본)·방음벽
    nb = 0; ng = 0
    nn = int(Lsr / step)
    for t in range(nn):
        dd = (t + 0.5) * step
        if dd > Lsr - 0.25:
            break
        if dd < skip_r:
            continue
        idx = _cell(x0, y0, res, nx, ny, xs + (xr - xs) * dd / Lsr, ys + (yr - ys) * dd / Lsr)
        if idx < 0:
            continue
        b = bid.flat[idx]
        if b < 0 or (b == rbid and Lsr - dd < skip_r):
            continue
        if dsm.flat[idx] > zs + (zr - zs) * dd / Lsr and not _has(Xb, nb, b) and nb < Xb.shape[0]:
            Xb[nb] = b; nb += 1
    for w in range(bars.shape[0]):
        f = _seg_hit(xs, ys, xr, yr, w, bars)
        if f < 0.0:
            continue
        dd = f * Lsr
        if dd < 0.1 or Lsr - dd < 0.1:
            continue
        idx = _cell(x0, y0, res, nx, ny, xs + (xr - xs) * f, ys + (yr - ys) * f)
        if idx < 0:
            continue
        if gnd.flat[idx] + bars[w, 4] > zs + (zr - zs) * dd / Lsr and not _has(Xg, ng, bgid[w]) and ng < Xg.shape[0]:
            Xg[ng] = bgid[w]; ng += 1
    if nb == 0 and ng == 0:
        return 0.0
    F = 0.0
    for side in (1, -1):
        mb = nb; mg = ng
        for i in range(nb):
            Xb2[i] = Xb[i]
        for i in range(ng):
            Xg2[i] = Xg[i]
        for it in range(maxgrow + 1):
            # 껍질 점: S, R, 건물 꼭짓점, 방음벽 끝점
            n = 2
            px[0] = xs; py[0] = ys; px[1] = xr; py[1] = yr
            over = False
            for i in range(mb):
                b = Xb2[i]
                for v in range(bptr[b], bptr[b + 1]):
                    if math.isnan(bxy[v, 0]):
                        continue
                    if n >= px.shape[0]:
                        over = True
                        break
                    px[n] = bxy[v, 0]; py[n] = bxy[v, 1]; n += 1
            for w in range(bars.shape[0]):
                if not _has(Xg2, mg, bgid[w]):
                    continue
                for c in range(2):
                    if n >= px.shape[0]:
                        over = True
                        break
                    px[n] = bars[w, 2 * c]; py[n] = bars[w, 2 * c + 1]; n += 1
            if over:
                break
            h = convex_hull(px, py, n, order, H)
            m = side_chain(px, py, H, h, side, cx, cy)
            if m < 3:
                break
            Lh = 0.0
            for q in range(m - 1):
                Lh += math.sqrt((cx[q + 1] - cx[q]) ** 2 + (cy[q + 1] - cy[q]) ** 2)
            mb2, mg2, add = _blockers(cx, cy, m, zs, zr, Lh, rbid, gnd, dsm, bid, x0, y0, res, nx, ny,
                                      step, skip_r, bars, bgid, bxy, bptr, Xb2, mb, Xg2, mg)
            if add == 0:
                F += _chain_factor(cx, cy, m, zs, zr, hs, hr, d, gnd, Gmap, x0, y0, res, nx, ny, step,
                                   bands, lams, alphas, wts, ground_alt, C2, dz1, dz2, Lsr, cmet0)
                break
            mb = mb2; mg = mg2
    return F
