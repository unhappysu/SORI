# -*- coding: utf-8 -*-
"""
RLS-19 음원(방출) 모듈 — FGSV 052 (Ausgabe 2019, Korrektur 04.03.2020)
식·표 번호는 원문 번호를 그대로 사용함.
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.

참고: 국문 번역본(번역본RLS19_KR)과 원문의 차이
  - 식 (8): 번역본 "KKT + max{...}" → 원문 "KKT · max{...}" (곱). 본 코드는 원문을 따름.
  - 3.1  : 번역본 "정수로 반올림" → 원문 "auf ganze Dezibel aufzurunden"(올림). 본 코드는 원문을 따름.
"""
import math
import warnings

# ------------------------------------------------------------------ 표 2 (도로유형별 표준값)
#   (M 계수 × DTV, p1 %, p2 %)  주간 06–22시 / 야간 22–06시
TAB2 = {
    "autobahn":      {"day": (0.0555, 3.0, 11.0), "night": (0.0140, 10.0, 25.0)},   # 연방고속도로·자동차전용도로
    "bundesstrasse": {"day": (0.0575, 3.0, 7.0),  "night": (0.0100, 7.0, 13.0)},   # 연방국도
    "landesstrasse": {"day": (0.0575, 3.0, 5.0),  "night": (0.0100, 5.0, 6.0)},    # 주·지방·지자체 연결도로
    "gemeinde":      {"day": (0.0575, 3.0, 4.0),  "night": (0.0100, 3.0, 4.0)},    # 지자체 도로
}

# ------------------------------------------------------------------ 표 3
AW = {"Pkw": 88.0, "Lkw1": 100.3, "Lkw2": 105.4}
BW = {"Pkw": 20.0, "Lkw1": 40.0, "Lkw2": 50.0}
CW = {"Pkw": 3.06, "Lkw1": 4.33, "Lkw2": 4.88}

# ------------------------------------------------------------------ 표 4a (포장 외 노면)
#   (Pkw ≤60, Pkw >60, Lkw ≤60, Lkw >60)  None = 원문에 값 없음(해당 속도대 적용 불가)
TAB4A = {
    "ref":        (0.0, 0.0, 0.0, 0.0),        # Nicht geriffelter Gussasphalt (기준)
    "SMA5_8":     (-2.6, None, -1.8, None),    # SMA 5, SMA 8
    "SMA8_11":    (None, -1.8, None, -2.0),    # SMA 8, SMA 11
    "AC11":       (-2.7, -1.9, -1.9, -2.1),    # Asphaltbetone ≤ AC 11
    "PA11":       (None, -4.5, None, -4.4),    # 배수성 PA 11
    "PA8":        (None, -5.5, None, -5.4),    # 배수성 PA 8
    "Waschbeton": (None, -1.4, None, -2.3),    # 노출골재 콘크리트
    "LGA":        (None, -2.0, None, -1.5),    # 저소음 Gussasphalt (Verfahren B)
    "LOA":        (-3.2, None, -1.0, None),    # AC D LOA
    "SMA_LA8":    (None, -2.8, None, -4.6),    # SMA LA 8
    "DSH_V5":     (-3.9, -2.8, -0.9, -2.3),    # 박층 DSH-V 5
}
# 표 4b (블록 포장) : v 30 / 40 / ≥50 km/h, 차종 구분 없음
TAB4B = {"pflaster_even": (1.0, 2.0, 3.0), "pflaster_other": (5.0, 6.0, 7.0)}
SURFACES = tuple(TAB4A) + tuple(TAB4B)

# ------------------------------------------------------------------ 표 5
KKT = {"signal": 3.0, "roundabout": 2.0, "other": 0.0}

_warned = set()


def _warn_once(msg):
    if msg not in _warned:
        _warned.add(msg)
        warnings.warn(msg, stacklevel=3)


def speed_rule(v):
    """2장 용어정의 'Geschwindigkeit': 30 km/h 미만은 30 km/h 적용"""
    return max(float(v), 30.0)


def LW0(fzg, v):
    """식 (6) 차량 1대 음향파워 기본값 [dB]"""
    return AW[fzg] + 10.0 * math.log10(1.0 + (v / BW[fzg]) ** CW[fzg])


def D_SD(sdt, fzg, v):
    """3.3.5 노면보정 — 표 4a(Pkw/Lkw 구분) / 표 4b(블록포장, 구분 없음)"""
    if sdt in TAB4B:
        a = TAB4B[sdt]
        return a[0] if v < 40 else (a[1] if v < 50 else a[2])
    if sdt not in TAB4A:
        raise ValueError(f"알 수 없는 노면 유형 '{sdt}'. 사용 가능: {SURFACES}")
    row = TAB4A[sdt]
    idx = (0 if fzg == "Pkw" else 2) + (0 if v <= 60 else 1)
    val = row[idx]
    if val is None:
        _warn_once(f"표 4a: 노면 '{sdt}' 는 {fzg} {'≤60' if v <= 60 else '>60'} km/h 값이 원문에 없음 → 0 dB 적용")
        return 0.0
    return val


def D_LN(fzg, g, v):
    """식 (7a)(7b)(7c) 종단경사 보정. g[%]는 ±12 %로 제한"""
    g = max(-12.0, min(12.0, g))
    if fzg == "Pkw":
        if g < -6:
            return (g + 6) / -6 * (90 - min(v, 70)) / 20
        if g > 2:
            return (g - 2) / 10 * (v + 70) / 100
        return 0.0
    if fzg == "Lkw1":
        if g < -4:
            return (g + 4) / -8 * (v - 20) / 10
        if g > 2:
            return (g - 2) / 10 * v / 10
        return 0.0
    if g < -4:                                     # Lkw2
        return (g + 4) / -8 * v / 10
    if g > 2:
        return (g - 2) / 10 * (v + 10) / 10
    return 0.0


def D_K(x, kt):
    """식 (8) 교차로 보정: KKT · max{1 − x/120 ; 0}"""
    return KKT[kt] * max(1.0 - x / 120.0, 0.0)


def D_refl(h_beb, w):
    """식 (9) 다중반사 할증: min{2·hBeb/w ; 1.6}, w ≤ 100 m 일 때만"""
    if h_beb is None or w is None or not (w == w) or w <= 0 or w > 100:
        return 0.0
    return min(2.0 * h_beb / w, 1.6)


def LW_vehicle(fzg, v, sdt, g, dk, drefl):
    """식 (5) 차종별 음향파워레벨"""
    fz_sd = "Pkw" if fzg == "Pkw" else "Lkw"
    return LW0(fzg, v) + D_SD(sdt, fz_sd, v) + D_LN(fzg, g, v) + dk + drefl


def LW_prime(M, p1, p2, v_pkw, v_lkw, sdt, g, dk=0.0, drefl=0.0):
    """식 (4) 음원선 1 m당 음향파워레벨 LW' [dB]
    M: 시간당 교통량(대/h), p1,p2: Lkw1/Lkw2 비율(%), v: 차종별 속도(km/h), g: 종단경사(%)"""
    if M <= 0:
        return -99.0
    v_pkw = speed_rule(v_pkw)
    v_lkw = speed_rule(v_lkw)
    t = ((100.0 - p1 - p2) / 100.0 * 10 ** (0.1 * LW_vehicle("Pkw", v_pkw, sdt, g, dk, drefl)) / v_pkw
         + p1 / 100.0 * 10 ** (0.1 * LW_vehicle("Lkw1", v_lkw, sdt, g, dk, drefl)) / v_lkw
         + p2 / 100.0 * 10 ** (0.1 * LW_vehicle("Lkw2", v_lkw, sdt, g, dk, drefl)) / v_lkw)
    return 10.0 * math.log10(M) + 10.0 * math.log10(t) - 30.0


def round_up(L):
    """3.1 평가레벨의 기준 비교용 정수화: 올림(aufrunden)"""
    return math.ceil(L - 1e-9)
