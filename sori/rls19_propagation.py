# -*- coding: utf-8 -*-
"""
RLS-19 전파(감쇠) 모듈 — FGSV 052 (Ausgabe 2019, Korrektur 04.03.2020) 3.5절.
식 번호는 원문 번호를 그대로 사용함. 기하(s, s_gr, h_m, 고무줄 모서리, z, A·B·C)는 geometry.path_geometry 공통.
  D_A = D_div + D_atm + max{D_gr; D_z}                        식 (11)
A 가중 단일값 계산(주파수 대역 없음). 측면 회절은 고려하지 않음(3.5, 규정상 제외).
반사(3.6: h_R 조건, 표 8 D_RV)는 solver.compute 에서 처리.
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import math

from . import njit
from .geometry import G_D, G_HM, G_SHIELD, G_DOUBLE, G_Z, G_DSS, G_DSR, G_E

LG_2PI = 10.0 * math.log10(2.0 * math.pi)


@njit(cache=True)
def D_div(s):
    """식 (12) 20 lg s + 10 lg 2π (2π 로 지면 반사 포함, 원문 주석). s ≥ 0.1 m 하한(프로젝트 설정, ISO 경로와 같음)"""
    return 20.0 * math.log10(max(s, 0.1)) + LG_2PI


@njit(cache=True)
def D_atm(s):
    """식 (13) s / 200"""
    return s / 200.0


@njit(cache=True)
def D_gr(hm, s):
    """식 (14) max{4.8 − (h_m/s)(34 + 600/s); 0}, h_m = F/s_gr (그림 11)"""
    v = 4.8 - hm / s * (34.0 + 600.0 / s)
    return v if v > 0.0 else 0.0


@njit(cache=True)
def K_w(A, B, s, z):
    """식 (17) exp(−(1/2000)·√(A·B·s/(2z)))"""
    return math.exp(-1.0 / 2000.0 * math.sqrt(A * B * s / (2.0 * z)))


@njit(cache=True)
def D_z(z, Kw):
    """식 (15) 10 lg[3 + 80·z·K_w]. 원문에 상한 규정 없음"""
    return 10.0 * math.log10(3.0 + 80.0 * z * Kw)


@njit(cache=True)
def D_A(geom):
    """식 (11). geom: geometry.path_geometry 결과"""
    s = geom[G_D]
    v = D_div(s) + D_atm(s)
    dgr = D_gr(geom[G_HM], s)
    if geom[G_SHIELD] > 0.5:
        A = geom[G_DSS]; B = geom[G_DSR]
        if geom[G_DOUBLE] > 0.5:
            # 다중 회절(그림 13): 첫~마지막 모서리 사이 경로 C 를 A·B 중 큰 쪽에 더함(3.5.5).
            # A = B 이면 A 에 더함(원문에 규정 없음, 해석)
            if A >= B:
                A += geom[G_E]
            else:
                B += geom[G_E]
        dz = D_z(geom[G_Z], K_w(A, B, s, geom[G_Z]))
        v += dz if dz > dgr else dgr
    else:
        v += dgr
    return v


@njit(cache=True)
def atten_rls19(geom, out):
    """대역 배열 out 의 모든 칸에 D_A 기록(RLS-19 는 A 가중 단일값 → 커널은 1대역으로 호출)"""
    v = D_A(geom)
    for b in range(out.shape[0]):
        out[b] = v


@njit(cache=True)
def accept_reflection(hR, aR):
    """3.6 반사 고려 조건: h_R ≥ 1.0 m 이고 h_R ≥ 0.3·√a_R.
    a_R = 음원–반사체, 반사체–수음점 거리 중 작은 값(수평 거리 사용 — 해석).
    h_R = 반사면(벽면) 높이 ztop − z0 (원문은 '반사면의 높이'라고만 적음 — 해석)"""
    return hR >= 1.0 and hR >= 0.3 * math.sqrt(aR)
