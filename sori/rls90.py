# -*- coding: utf-8 -*-
"""
RLS-90 음원(방출)·평가레벨 보정 모듈 — Richtlinien für den Lärmschutz an Straßen, Ausgabe 1990
(원문: 02_기준원문_참고자료/RLS90-opt in German.pdf). 식·표 번호는 원문 번호를 그대로 사용함.

SORI 의 계산기(solver)는 음원을 'L_W′(1 m당 음향파워) − D_A' 형식으로 다루므로, RLS-90 Teilstück-Verfahren
식 (20)·(21)을 같은 형식으로 바꿔 씀:
    L_m,i = L_m,E + 10 lg l + 11,2 − 20 lg s − s/200 + D_BM + D_B                       식 (20)(21)(22)
          = L_W′eq + 10 lg l − [20 lg s + 10 lg 2π + s/200 − D_BM − D_B]
    L_W′eq = L_m,E + 11,2 + 10 lg 2π                                                   (등가 환산, 항등식)
→ 대괄호 안은 rls90_propagation.D_A. 환산은 식을 바꾸지 않는 항등 변형임(값 동일).

중간값 0.1 dB 반올림(4.0절)은 하지 않음(연속값 계산). 최종 비교용 정수화는 올림(aufrunden, 4.0절) — run.py.
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import math

LG_2PI = 10.0 * math.log10(2.0 * math.pi)
LW_OFFSET = 11.2 + LG_2PI                     # L_W′eq − L_m,E  [dB]

# ------------------------------------------------------------------ 표 3 (Straßengattung 별 maßgebende Verkehrsstärke·Lkw-Anteil)
#   (M 계수 × DTV, p %)  tags 06–22시 / nachts 22–06시. 원문 4.4.1.1.1: 프로젝트 조사 결과가 있으면 표 3 을 쓰지 않음
#   (원문 RLS90-opt in German.pdf 12쪽 대조, 2026-11-06). 키는 RLS-19 표 2 와 같은 도로유형 이름을 씀:
#   autobahn = Bundesautobahnen, bundesstrasse = Bundesstraßen,
#   landesstrasse = Landes-, Kreis- und Gemeindeverbindungsstraßen, gemeinde = Gemeindestraßen
TAB3 = {
    "autobahn":      {"day": (0.06, 25.0), "night": (0.014, 45.0)},
    "bundesstrasse": {"day": (0.06, 20.0), "night": (0.011, 20.0)},
    "landesstrasse": {"day": (0.06, 20.0), "night": (0.008, 10.0)},
    "gemeinde":      {"day": (0.06, 10.0), "night": (0.011, 3.0)},
}

# ------------------------------------------------------------------ 표 4 D_StrO (zul. Höchstgeschwindigkeit 30 / 40 / ≥ 50 km/h)
TAB4 = {
    "gussasphalt": (0.0, 0.0, 0.0),           # 1 nicht geriffelte Gußasphalte, Asphaltbetone oder Splittmastixasphalte
    "beton":       (1.0, 1.5, 2.0),           # 2 Betone oder geriffelte Gußasphalte
    "pflaster_even": (2.0, 2.5, 3.0),         # 3 Pflaster mit ebener Oberfläche
    "pflaster_other": (3.0, 4.5, 6.0),        # 4 sonstiges Pflaster
}
# 표 4 각주 *): 저소음 노면 예 — offenporige Asphalte, zul. Geschwindigkeit > 60 km/h → −3 dB(A)
OPA_DSTRO = -3.0

# SORI 노면 코드(RLS-19 표 4a·4b) → RLS-90 표 4 행. 해석 표(원문 대응표 아님):
#   아스팔트 계열(기준·SMA·AC·저소음 Gussasphalt·LOA·DSH-V)은 1행, 노출골재 콘크리트는 2행(Betone),
#   배수성(PA)은 1행 + 각주(> 60 km/h 에서 −3 dB)
SURFACE_ROW = {
    "ref": "gussasphalt", "SMA5_8": "gussasphalt", "SMA8_11": "gussasphalt", "AC11": "gussasphalt",
    "LGA": "gussasphalt", "LOA": "gussasphalt", "SMA_LA8": "gussasphalt", "DSH_V5": "gussasphalt",
    "PA11": "opa", "PA8": "opa",
    "Waschbeton": "beton",
    "pflaster_even": "pflaster_even", "pflaster_other": "pflaster_other",
}

# ------------------------------------------------------------------ 표 7 D_E (Spiegelschallquellen)
TAB7 = {"glatt": -1.0, "gegliedert": -2.0, "absorbierend": -4.0, "hochabsorbierend": -8.0}


def v_pkw_rule(v):
    """4.4.1.1.2: v_Pkw 는 30 ~ 130 km/h"""
    return min(max(float(v), 30.0), 130.0)


def v_lkw_rule(v):
    """4.4.1.1.2: v_Lkw 는 30 ~ 80 km/h"""
    return min(max(float(v), 30.0), 80.0)


def Lm25(M, p):
    """식 (7) L_m^(25) = 37,3 + 10 lg[M·(1 + 0,082·p)]. M: 이 음원선(Fahrstreifen)의 Kfz/h, p: Lkw-Anteil %"""
    if M <= 0:
        return -99.0
    return 37.3 + 10.0 * math.log10(M * (1.0 + 0.082 * p))


def L_pkw(v_pkw):
    """식 (8) L_Pkw = 27,7 + 10 lg[1 + (0,02·v_Pkw)³]"""
    return 27.7 + 10.0 * math.log10(1.0 + (0.02 * v_pkw_rule(v_pkw)) ** 3)


def L_lkw(v_lkw):
    """식 (8) L_Lkw = 23,1 + 12,5 lg(v_Lkw)"""
    return 23.1 + 12.5 * math.log10(v_lkw_rule(v_lkw))


def D_v(v_pkw, v_lkw, p):
    """식 (8) D_v = L_Pkw − 37,3 + 10 lg[(100 + (10^(0,1·D) − 1)·p) / (100 + 8,23·p)],  D = L_Lkw − L_Pkw"""
    lp = L_pkw(v_pkw)
    D = L_lkw(v_lkw) - lp
    return lp - 37.3 + 10.0 * math.log10((100.0 + (10.0 ** (0.1 * D) - 1.0) * p) / (100.0 + 8.23 * p))


def D_StrO(surface, v):
    """표 4. surface: SORI 노면 코드 또는 표 4 행 이름. v: zul. Höchstgeschwindigkeit(Pkw) km/h.
    속도 열 선택: v < 40 → 30 km/h 열, v < 50 → 40 km/h 열, 그 밖 → ≥ 50 km/h 열(RLS-19 표 4b 와 같은 규칙, 해석)"""
    row = SURFACE_ROW.get(surface, surface)
    if row == "opa":
        return OPA_DSTRO if v > 60 else 0.0
    if row not in TAB4:
        raise ValueError(f"RLS-90 표 4: 알 수 없는 노면 '{surface}'. 사용 가능: {tuple(SURFACE_ROW)} 또는 {tuple(TAB4)}")
    a = TAB4[row]
    return a[0] if v < 40 else (a[1] if v < 50 else a[2])


def D_Stg(g):
    """식 (9) D_Stg = 0,6·|g| − 3 (|g| > 5 %), 그 밖 0. 오르막·내리막 같은 값"""
    g = abs(float(g))
    return 0.6 * g - 3.0 if g > 5.0 else 0.0


def Lm_E(M, p, v_pkw, v_lkw, surface, g):
    """식 (6) L_m,E = L_m^(25) + D_v + D_StrO + D_Stg (+ D_E 는 반사 경로에서 따로 — 표 7, solver)"""
    if M <= 0:
        return -99.0
    return Lm25(M, p) + D_v(v_pkw, v_lkw, p) + D_StrO(surface, v_pkw) + D_Stg(g)


def LW_prime(M, p, v_pkw, v_lkw, surface, g, drefl=0.0):
    """계산기 입력용 등가 L_W′ = L_m,E + D_refl + 11,2 + 10 lg 2π.
    D_refl(식 24a)은 Teilstück 의 D_B 에 들어가는 상수 가산값이라 음원 쪽에 더함(값 동일)"""
    if M <= 0:
        return -99.0
    return Lm_E(M, p, v_pkw, v_lkw, surface, g) + drefl + LW_OFFSET


def D_refl(h_beb, w):
    """식 (24a) 반사성 평행 벽면 사이 다중반사 할증 D_refl = 4·h_Beb/w ≤ 3,2 dB.
    (흡음 벽면 식 24b, 고흡음 무시 — 건물 외벽만 다루므로 24a 만 씀)"""
    if h_beb is None or w is None or not (w == w) or not (h_beb == h_beb) or w <= 0:
        return 0.0
    return min(4.0 * h_beb / w, 3.2)


def K_signal(d):
    """표 2 lichtzeichengeregelte Kreuzungen 할증 K: 수음점 ~ 가장 가까운 축 교차점 거리 d
    ≤ 40 m: 3, 40 < d ≤ 70: 2, 70 < d ≤ 100: 1, > 100: 0  (가장 가까운 교차로 1곳만)"""
    if d is None or not (d == d):
        return 0.0
    if d <= 40.0:
        return 3.0
    if d <= 70.0:
        return 2.0
    if d <= 100.0:
        return 1.0
    return 0.0
