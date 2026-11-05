# -*- coding: utf-8 -*-
"""
RLS-90 전파 모듈 — 4.4.2 Teilstück-Verfahren(식 19~27), 4.6 Einfachreflexion(반사 조건, 표 7 D_E).
기하(s, h_m, 고무줄 모서리, z, A·B·C)는 geometry.path_geometry 공통(RLS-19·ISO 와 같음).

계산기 형식(L = L_W′eq + 10 lg l − D_A, rls90.py 머리말)에 맞춘 감쇠:
  D_A = 20 lg s + 10 lg 2π + s/200 − D_BM        (차폐 없음)      ← 식 (21)(22)
  D_A = 20 lg s + 10 lg 2π + s/200 + D_z          (차폐 있음, "Bei Abschirmung entfällt D_BM", 4.4.2.1.2)
  D_BM = (h_m/s)·(34 + 600/s) − 4,8 ≤ 0           식 (22)
  D_z  = 10 lg(3 + 80·z·K_w)                      식 (25), 상한 규정 없음
  z = A + B + C − s                               식 (26)
  K_w = exp(−(1/2000)·√(A·B·s/(2z)))              식 (27); 다중 회절이면 C 를 A·B 중 '작은' 쪽에 더함(4.4.2.1.3.2)
RLS-19 와 다른 점: 차폐 시 max{D_gr; D_z} 대신 D_z 만, 다중 회절 C 를 작은 쪽에(RLS-19 는 큰 쪽),
반사 조건 h_R ≥ 0,3·√a_R 만(RLS-19 의 h_R ≥ 1 m 없음), 반사 손실 D_E(표 7).
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import math

from . import njit
from .geometry import G_D, G_HM, G_SHIELD, G_DOUBLE, G_Z, G_DSS, G_DSR, G_E

LG_2PI = 10.0 * math.log10(2.0 * math.pi)


@njit(cache=True)
def D_s(s):
    """식 (21) D_s = 11,2 − 20 lg s − s/200 (기록·시험용; 계산은 D_A 형식). s ≥ 0.1 m 하한(프로젝트 설정)"""
    s = max(s, 0.1)
    return 11.2 - 20.0 * math.log10(s) - s / 200.0


@njit(cache=True)
def D_BM(hm, s):
    """식 (22) D_BM = (h_m/s)·(34 + 600/s) − 4,8 ≤ 0"""
    v = hm / s * (34.0 + 600.0 / s) - 4.8
    return v if v < 0.0 else 0.0


@njit(cache=True)
def K_w(A, B, s, z):
    """식 (27) exp(−(1/2000)·√(A·B·s/(2z)))"""
    return math.exp(-1.0 / 2000.0 * math.sqrt(A * B * s / (2.0 * z)))


@njit(cache=True)
def D_z(z, Kw):
    """식 (25) 10 lg(3 + 80·z·K_w). 원문에 상한 규정 없음"""
    return 10.0 * math.log10(3.0 + 80.0 * z * Kw)


@njit(cache=True)
def _dz_geom(geom):
    A = geom[G_DSS]; B = geom[G_DSR]
    if geom[G_DOUBLE] > 0.5:
        # 다중 회절(Bild 16): C 를 A·B 중 작은 쪽에 더함(4.4.2.1.3.2 "zum kleineren der Abstände A oder B").
        # A = B 이면 A 에 더함(원문에 규정 없음, 해석)
        if A <= B:
            A += geom[G_E]
        else:
            B += geom[G_E]
    return D_z(geom[G_Z], K_w(A, B, geom[G_D], geom[G_Z]))


@njit(cache=True)
def D_A(geom):
    """계산기 형식 감쇠 [dB]. geom: geometry.path_geometry 결과"""
    s = max(geom[G_D], 0.1)
    v = 20.0 * math.log10(s) + LG_2PI + s / 200.0
    if geom[G_SHIELD] > 0.5:
        v += _dz_geom(geom)                       # 차폐: D_BM 없음
    else:
        v -= D_BM(geom[G_HM], s)
    return v


@njit(cache=True)
def atten_rls90(geom, out):
    """대역 배열 out 의 모든 칸에 D_A 기록(A 가중 단일값 → 커널은 1대역으로 호출)"""
    v = D_A(geom)
    for b in range(out.shape[0]):
        out[b] = v


@njit(cache=True)
def accept_reflection(hR, aR):
    """4.6 반사 고려 조건 h_R ≥ 0,3·√a_R. a_R = 음원–반사체 거리(원문 'Abstand zwischen Quelle und Reflektor',
    수평 거리 사용 — 해석), h_R = 벽면 높이 ztop − z0(해석)"""
    return hR >= 0.3 * math.sqrt(aR)


# 감쇠항별 중간값 색인(terms_rls90). D_s 는 원문 식 (21) 값(기록용), D_A 는 계산기 형식
T_DS, T_DBM, T_DZ, T_DA = range(4)
TERM_NAMES = ("D_s", "D_BM", "D_z", "D_A")


@njit(cache=True)
def terms_rls90(geom, t):
    """감쇠항별 중간값 [dB]: D_s 식(21), D_BM 식(22)(차폐 시 0 — 'entfällt'), D_z 식(25)(차폐 없으면 0), D_A.
    관계: L = L_m,E + D_refl + 10 lg l + D_s + D_BM − D_z (= L_W′eq + 10 lg l − D_A)"""
    s = max(geom[G_D], 0.1)
    t[T_DS] = D_s(s)
    if geom[G_SHIELD] > 0.5:
        t[T_DBM] = 0.0
        t[T_DZ] = _dz_geom(geom)
    else:
        t[T_DBM] = D_BM(geom[G_HM], s)
        t[T_DZ] = 0.0
    t[T_DA] = D_A(geom)
