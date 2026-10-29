# -*- coding: utf-8 -*-
"""
RLS-19 공식 시험과제 TEST-20 v2.1 — 전파 과제(I1~I9, K1~K5)를 SORI 계산 경로로 재현하는 장치.
원문: 02_기준원문_참고자료/원문/TEST-20_v2.1_2021-09-16.pdf 13~26쪽(입력 좌표·시험결과·기준 범위).
  - 지형·건물·벽을 래스터(DTM, DSM, 건물 ID)로 만들고, 반사면(facade 배열)을 만든 뒤 solver.compute(rls19, 2차 반사)
  - 음원: TEST-20 시험 설정과 같은 2 m 구간 중앙 점음원, 높이 노면 + 0.5 m, L_W′ = 90 dB (+ D_K, D_refl: 과제 설명대로)
  - 탐색 반경은 크게 둠(시험은 방법 확인이 목적, SORI 기본 반경은 프로젝트 설정)
  - 얇은 벽은 래스터 칸 경계를 벽 선에 맞춰(칸 중심이 벽 선 위) 위치 오차를 줄임. I2·I5 는 0.1 m 칸에서 −0.03 dB,
    0.05 m 에서 시험결과로 수렴 → 0.05 m
  - K1~K4 는 수음점이 도로에 가까워 2 m 구간으로는 수렴하지 않음(K3 −0.14, K4 −0.13 dB) → 0.5 m 구간(수렴 확인).
    K5 는 원문이 2 m 구간 1개(119~121)라 그대로 둠(나누면 반사 유효 구간이 달라짐)
해석이 들어간 곳(원문 입력만으로 정해지지 않음): I6·I7 비탈 단면(선형), I8 지형(차로·비탈 윗선·비탈 아랫선 사이 선형 보간),
  I4 방음벽의 음원 쪽 면을 반사면으로 둠(2차 허상음원 근거), K2 D_refl 은 1차로 전체(과제 주석 '1. 차로만').
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import math
from dataclasses import dataclass, field

import numpy as np
from matplotlib.path import Path as MPath

from sori import rls19 as R
from sori.solver import compute, facade_neighbors, to_db

LWP = 90.0
SEG = 2.0
D_RV = 0.5
BIG = 5000.0


@dataclass
class Case:
    name: str
    bbox: tuple                                    # (xmin, ymin, xmax, ymax)
    res: float
    lanes: list                                    # [(x1, y1, z1, x2, y2, z2), ...] 노면 높이
    receivers: dict                                # {"IO1": (x, y, z), ...}
    expected: dict                                 # {"IO1": (시험결과, 기준 하한, 기준 상한)}
    ground: object = None                          # f(X, Y) → 지면 높이 배열(기본 100)
    walls: list = field(default_factory=list)      # [(x1, y1, x2, y2, z_top, reflective_side), ...]
    buildings: list = field(default_factory=list)  # [([(x, y), ...], z_top), ...]
    drefl: object = None                           # f(x, y) → D_refl
    dk: object = None                              # f(x, y, lane_index) → D_K
    order: int = 2
    seg: float = SEG                               # 구간 길이(m). TEST-20 시험 설정 "약 2 m, s/l > 20"
    note: str = ""


def _grid(c):
    x0, y0, x1, y1 = c.bbox
    r = c.res
    nx = int(round((x1 - x0) / r)); ny = int(round((y1 - y0) / r))
    xs = x0 + (np.arange(nx) + 0.5) * r
    ys = y1 - (np.arange(ny) + 0.5) * r
    X, Y = np.meshgrid(xs, ys)
    return X, Y, x0, y1, nx, ny


def world(c):
    X, Y, x0, ytop, nx, ny = _grid(c)
    gnd = c.ground(X, Y) if c.ground else np.full(X.shape, 100.0)
    dsm = gnd.copy(); bid = -np.ones(X.shape, np.int64)
    F = []                                         # (x1, y1, x2, y2, nx, ny, z0, ztop, L, bid)
    k = 0
    for (pts, ztop) in c.buildings:
        m = MPath(pts).contains_points(np.c_[X.ravel(), Y.ravel()]).reshape(X.shape)
        dsm[m] = np.maximum(dsm[m], ztop); bid[m] = k
        P = np.array(pts, float)
        area = 0.5 * np.sum(P[:, 0] * np.roll(P[:, 1], -1) - np.roll(P[:, 0], -1) * P[:, 1])
        for i in range(len(P)):
            a, b = P[i], P[(i + 1) % len(P)]
            ex, ey = b - a; L = math.hypot(ex, ey)
            nxv, nyv = (ey / L, -ex / L) if area > 0 else (-ey / L, ex / L)     # 바깥 방향 법선
            F.append((a[0], a[1], b[0], b[1], nxv, nyv, 100.0, ztop, L, k))
        k += 1
    for (wx1, wy1, wx2, wy2, ztop, side) in c.walls:
        # 얇은 벽: 벽 선을 따라 촘촘히 점을 찍어 그 칸을 벽으로
        L = math.hypot(wx2 - wx1, wy2 - wy1); n = int(L / (c.res / 4)) + 1
        px = np.linspace(wx1, wx2, n); py = np.linspace(wy1, wy2, n)
        j = np.floor((px - x0) / c.res).astype(int); i = np.floor((ytop - py) / c.res).astype(int)
        ok = (i >= 0) & (j >= 0) & (i < ny) & (j < nx)
        dsm[i[ok], j[ok]] = ztop; bid[i[ok], j[ok]] = k
        if side:                                   # 반사면: side = (nx, ny) 반사하는 쪽 법선
            F.append((wx1, wy1, wx2, wy2, side[0], side[1], 100.0, ztop, L, k))
        k += 1
    Gm = np.zeros(X.shape)
    return gnd, dsm, bid, Gm, x0, ytop, nx, ny, F


def ground_at(c, x, y):
    if c.ground is None:
        return 100.0
    return float(c.ground(np.array([[x]]), np.array([[y]]))[0, 0])


def sources(c):
    S = []
    for li, (x1, y1, z1, x2, y2, z2) in enumerate(c.lanes):
        L = math.hypot(x2 - x1, y2 - y1); n = max(1, int(round(L / c.seg)))
        for q in range(n):
            t = (q + 0.5) / n
            x = x1 + t * (x2 - x1); y = y1 + t * (y2 - y1); zg = z1 + t * (z2 - z1)
            lw = LWP + 10 * math.log10(L / n)
            if c.dk:
                lw += c.dk(x, y, li)
            if c.drefl:
                lw += c.drefl(x, y, li)
            S.append((x, y, zg + 0.5, zg, lw))
    return np.array(S)


def run(c):
    """반환 {수음점: dict(L, Ldir, L1, L2)}"""
    gnd, dsm, bid, Gm, x0, ytop, nx, ny, F = world(c)
    S = sources(c)
    if F:
        Fa = [np.array([f[k] for f in F], float) for k in range(9)] + [np.array([f[9] for f in F], np.int64)]
    else:
        Fa = [np.zeros(0)] * 9 + [np.zeros(0, np.int64)]
    nb = facade_neighbors(*Fa[:6], BIG) if c.order >= 3 else (np.zeros(len(F) + 1, np.int64), np.zeros(0, np.int64))
    out = {}
    for name, (x, y, z) in c.receivers.items():
        zg = ground_at(c, x, y)
        rb, rf = -1, -1                            # 수음점이 벽면 0.2 m 안이면 그 면(자기 반사 제외, SORI 벽면 수음점과 같음)
        for i, f in enumerate(F):
            ex, ey = f[2] - f[0], f[3] - f[1]
            t = min(1.0, max(0.0, ((x - f[0]) * ex + (y - f[1]) * ey) / (ex * ex + ey * ey)))
            if math.hypot(f[0] + t * ex - x, f[1] + t * ey - y) < 0.2 and (x - f[0]) * f[4] + (y - f[1]) * f[5] > 0:
                rf, rb = i, int(f[9])
        P = 10 ** (0.1 * S[:, 4])
        Ed, En, Edir, E1, E2, E3 = compute(
            np.array([x]), np.array([y]), np.array([z]), np.array([zg]), np.array([rb], np.int64),
            np.array([rf], np.int64), c.order, S[:, 0], S[:, 1], S[:, 2], S[:, 3], P, P, np.ones(len(S), np.bool_),
            *Fa, 10 ** (-0.1 * D_RV), gnd, dsm, bid, Gm, float(x0), float(ytop), float(c.res), nx, ny, c.res / 2,
            BIG, BIG, BIG, BIG, BIG, BIG, 1.5, np.array([500.0]), np.array([0.68]), np.zeros(1), np.ones(1),
            False, 20.0, 20.0, 25.0, 0, *nb, False)
        out[name] = dict(L=float(to_db(Ed)[0]), Ldir=float(to_db(Edir)[0]), L1=float(to_db(E1)[0]),
                         L2=float(to_db(E2)[0]))
    return out


# ====================================================================== 과제 정의(TEST-20 13~26쪽)
LANE = (30, 50, 100, 250, 50, 100)


def _slope(y0, z0, y1, z1, zlow, zhigh_side):
    """y 방향 단면: y ≤ y0 → z0, y ≥ y1 → z1, 사이는 선형"""
    def f(X, Y):
        t = np.clip((Y - y0) / (y1 - y0), 0.0, 1.0)
        return z0 + t * (z1 - z0)
    return f


def _i8_ground(X, Y):
    """I8(해석): 차로(y = 50, z 는 x = 250 → 100, x = 30 → 122) — 비탈 윗선(y = 58, z = 125 − 25x/250)
    — 비탈 아랫선(y = 95.5 − 37.5x/250, z = 100) 사이 선형, 그 밖 100. 차로 바깥(x < 30)은 x = 30 높이로 연장"""
    zl = 100 + 22 * (250 - np.clip(X, 30, 250)) / 220
    zk = 125 - 25 * X / 250
    yf = 95.5 - 37.5 * X / 250
    Z = np.full(X.shape, 100.0)
    a = (Y >= 50) & (Y <= 58)
    Z[a] = (zl + (zk - zl) * (Y - 50) / 8)[a]
    b = (Y > 58) & (Y < yf)
    Z[b] = (zk + (100 - zk) * (Y - 58) / np.maximum(yf - 58, 1e-9))[b]
    Z[Y < 50] = zl[Y < 50]
    return Z


def _k1_dk(x, y, li):
    pts = [(200, 200), (206, 200), (200, 220), (206, 220)]       # 서로 교차하는 음원선의 교차점
    xk = min(math.hypot(x - a, y - b) for a, b in pts)
    return R.D_K(xk, "signal")


CASES = [
    Case("I1", (-5, 40, 255, 135), 0.25, [LANE], {"IO1": (0, 70, 130.5), "IO2": (0, 100, 105.5)},
         {"IO1": (64.45, 64.3, 64.5), "IO2": (60.35, 60.2, 60.4)}),
    Case("I2", (-5.025, 40.025, 255.025, 105.025), 0.05, [LANE], {"IO": (0, 100, 105.5)}, {"IO": (55.39, 55.4, 55.4)},
         walls=[(0, 58, 250, 58, 102.75, None)]),
    Case("I3", (-5.05, 35.05, 255.05, 105.05), 0.1, [LANE], {"IO": (0, 100, 105.5)}, {"IO": (62.45, 62.3, 62.5)},
         walls=[(0, 40, 250, 40, 110.0, (0.0, 1.0))]),
    Case("I4", (-5.05, 35.05, 255.05, 105.05), 0.1, [LANE], {"IO": (0, 100, 105.5)}, {"IO": (61.56, 61.4, 61.6)},
         walls=[(0, 58, 250, 58, 102.75, (0.0, -1.0)), (0, 40, 250, 40, 110.0, (0.0, 1.0))],
         drefl=lambda x, y, li: R.D_refl(2.75, 18.0)),
    Case("I5", (-5.025, 40.025, 255.025, 105.025), 0.05, [LANE], {"IO1": (0, 100, 108.3), "IO2": (0, 100, 105.5)},
         {"IO1": (56.98, 56.9, 57.0), "IO2": (55.27, 55.2, 55.3)},
         walls=[(0, 58, 250, 58, 102.75, None), (0, 62, 250, 62, 103.25, None)]),
    Case("I6", (-5, 40, 255, 105), 0.1, [LANE], {"IO1": (0, 100, 108.3), "IO2": (0, 100, 120.5)},
         {"IO1": (52.12, 52.1, 52.1), "IO2": (61.47, 61.2, 61.5)}, ground=_slope(55.3, 100.0, 62.8, 105.5, 0, 0)),
    Case("I7", (-5, 40, 255, 85), 0.1, [(30, 50, 105, 250, 50, 105)], {"IO1": (0, 75, 102.6), "IO2": (0, 75, 105.0)},
         {"IO1": (60.58, 60.4, 60.6), "IO2": (63.10, 62.7, 63.1)}, ground=_slope(55.3, 105.0, 62.8, 100.0, 0, 0)),
    Case("I8", (-5, 40, 255, 120), 0.25, [(250, 50, 100, 30, 50, 122)], {"IO1": (0, 110, 105.5), "IO2": (0, 110, 122.0)},
         {"IO1": (52.09, 52.1, 52.1), "IO2": (62.51, 62.3, 62.5)}, ground=_i8_ground, note="지형 해석 포함"),
    Case("I9", (-10, -10, 560, 340), 0.5, [(100, 60, 100, 550, 330, 100)],
         {"IO1": (50, 30, 100.5), "IO2": (0, 0, 100.5)}, {"IO1": (58.59, 58.3, 58.5), "IO2": (54.61, 54.3, 54.5)}),
    Case("K1", (40, 40, 366, 380), 0.5,
         [(50, 200, 100, 356, 200, 100), (50, 220, 100, 356, 220, 100), (200, 50, 100, 200, 370, 100),
          (206, 50, 100, 206, 370, 100)], {"IO": (180, 240, 105.5)}, {"IO": (79.78, 79.7, 79.8)}, dk=_k1_dk, seg=0.5),
    Case("K2", (10, 20, 230, 150), 0.25, [(80, 80, 100, 120, 80, 100), (120, 80, 100, 220, 80, 100)],
         {"IO": (60, 99.95, 102.8)}, {"IO": (66.54, 66.3, 66.6)},
         buildings=[([(20, 100), (120, 100), (120, 140), (20, 140)], 112.0),
                    ([(50, 30), (150, 30), (150, 70), (50, 70)], 112.0)],
         drefl=lambda x, y, li: R.D_refl(12.0, 30.0) if li == 0 else 0.0, seg=0.5),
    Case("K3", (-5, 60, 70, 170), 0.25, [(0, 100, 100, 60, 160, 100)], {"IO": (34.79, 79.53, 102.8)},
         {"IO": (62.78, 62.7, 62.8)},
         buildings=[([(7.07, 92.93), (28.28, 71.72), (35.36, 78.79), (14.14, 100.0)], 130.0),
                    ([(28.28, 114.14), (49.50, 92.92), (56.57, 100.0), (35.36, 121.21)], 130.0)], seg=0.5),
    Case("K4", (10, 10, 340, 240), 0.25, [(20, 20, 100, 280, 20, 100)], {"IO": (110.05, 160, 102.8)},
         {"IO": (43.63, 43.4, 43.6)},
         buildings=[([(230, 30), (330, 30), (330, 70), (230, 70)], 130.0),
                    ([(70, 30), (210, 30), (210, 70), (110, 70), (110, 190), (210, 190), (210, 230), (70, 230)], 130.0)], seg=0.5),
    Case("K5", (30, 30, 270, 490), 0.5, [(119, 140, 100, 121, 140, 100)], {"IO": (60, 360, 102.8)},
         {"IO": (36.39, 36.4, 36.4)},
         buildings=[([(160, 420), (40, 420), (40, 480), (160, 480)], 104.5),
                    ([(260, 160), (200, 160), (200, 280), (260, 280)], 103.5),
                    ([(200, 40), (80, 40), (80, 100), (200, 100)], 104.5)], seg=2.0),
]
