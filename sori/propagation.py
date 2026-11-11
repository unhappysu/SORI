# -*- coding: utf-8 -*-
"""
전파 계산 방법 선택 — 설정 `propagation_method: "iso9613_2" | "rls19" | "rls90"`.
음원 모델(L_W′, 음원선, 구간 분할, 0.5 m 점음원, D_K, D_refl)은 iso9613_2·rls19 가 공통으로 씀(build 단계).
rls90 은 음원도 RLS-90(rls90.py: L_m,E → 등가 L_W′, 바깥 차로 중앙 음원선, K 는 수음점 할증)으로 바꿈.
기하(geometry.path_geometry)도 공통이고, 감쇠식과 반사 채택 조건·손실만 방법별로 다름.
numba 커널 안에서는 Python 클래스를 쓸 수 없으므로 이 모듈은 커널 인자(정수 방법 코드·계수) 준비와
메타데이터·범례 문구를 맡고, 계산은 solver.compute 가 방법 코드로 분기함.
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
from abc import ABC, abstractmethod

import numpy as np

from .iso9613 import alpha_iso9613_1, C_SOUND

DEFAULT_METHOD = "iso9613_2"

# build_info.json·run_info.json 의 음원 표기. run.py 가 계산 방법과 맞는지 확인함(다른 음원 모델 결과에 계산하지 않게)
EMISSION_LABEL = {"rls19": "RLS-19 (FGSV 052, 2019, Korrektur 2020)",
                  "rls90": "RLS-90 (Ausgabe 1990) L_m,E 식 (6)~(9) → 등가 L_W′ = L_m,E + 11,2 + 10 lg 2π"}

# 이번 판에서 계산하지 않는 항목(두 방법 공통)
_COMMON_NOT_IMPLEMENTED = ["section(단면) 소음지도", "4차 이상 반사"]
# D_refl(다중 반사 가산값)과 허상음원 반사의 조합 — 2026-11-11 원문 대조:
#   RLS-19: 3.3.8 식 (9) + 3.6 "1차 및 2차 반사". 공식 시험과제 TEST-20 v2.1 K2 가 직접음 + 1차 + 2차 반사에
#           식 (9)를 함께 적용(12·23쪽) → D_refl + 2차까지가 표준 절차. 3차 이상은 표준 밖
#   RLS-90: 4.6 "평행 벽 사이 도로는 1차 반사만 D_E 로, 그 뒤 반사는 D_refl 로" → D_refl + 1차가 표준 절차
#   국내 「소음지도의 작성방법」 별표 2 제8호: 반사차수 3차 이상 원칙(주변 환경에 따라 변경 가능),
#           제14호 가: 양쪽 반사 구조는 다중반사 효과 고려 — D_refl 같은 가산값과의 조합은 규정 없음
_STD_ORDER = {"rls19": 2, "iso9613_2": 2, "rls90": 1}       # D_refl 과 함께 쓰는 표준의 최고 반사 차수
                                                            # (iso9613_2 는 음원이 RLS-19 라 RLS-19 기준)


def _max_order(cfg):
    rf = cfg["propagation"]["reflection"]
    return max(int(rf["max_order_grid"]), int(rf["max_order_facade"]), int(rf["max_order_point"]))


def _canyon_on(cfg):
    return bool(cfg["model"]["canyon"]["enabled"])


def d_refl_note(name, cfg):
    """run_info.json 의 D_refl_note: 음원 쪽 다중 반사 가산값과 허상음원 반사 차수의 조합이 표준 절차인지"""
    if not _canyon_on(cfg):
        return "D_refl(다중 반사 가산값) 미적용(model.canyon.enabled: false) — 반사는 허상음원만"
    n, std = _max_order(cfg), _STD_ORDER[name]
    if name == "rls90":
        base = ("D_refl(RLS-90 식 24a, 4·h_Beb/w ≤ 3,2)을 음원에 포함. RLS-90 4.6: 평행 벽 사이 도로는 "
                "1차 반사만 허상음원(D_E)으로, 그 뒤 반사는 D_refl 로 계산")
    else:
        base = ("D_refl(RLS-19 식 9, 2·h_Beb/w ≤ 1,6)을 음원 L_W′ 에 포함. RLS-19 3.3.8·3.6 과 공식 시험과제 "
                "TEST-20 K2 는 D_refl 을 1·2차 허상음원 반사와 함께 적용")
    if n <= std:
        return base + f" — 반사 {n}차: 표준 절차 범위"
    return base + (f" — 반사 {n}차는 표준({std}차)을 넘는 프로젝트 설정이며, {std + 1}차 이상 허상음원은 D_refl 이 "
                   "대신하는 고차 반사와 겹침(도로 협곡에서 이중 계상). 겹침을 피하려면 반사 차수를 "
                   f"{std}로 낮추거나 model.canyon.enabled: false")


def standard_deviations(name, cfg):
    """표준 원문의 계산 절차와 다른 설정 목록(run_info.json `standard_deviations`, 계산 때 기록창에도 표시)"""
    out = []
    n, std = _max_order(cfg), _STD_ORDER[name]
    src = "RLS-90 4.6(1차)" if name == "rls90" else "RLS-19 3.6(1·2차)"
    if n > std:
        out.append(f"반사 {n}차 — {src}을 넘음. 근거: 「소음지도의 작성방법」 별표 2 제8호(3차 이상 원칙)"
                   + (" + D_refl 과 겹침" if _canyon_on(cfg) else ""))
    if name == "iso9613_2":
        out.append("RLS-19 음원 + ISO 9613-2 전파 — 어느 표준도 규정하지 않은 구성. RLS-19 전파와 같은 값은 "
                   "가까운 거리(지면 감쇠 0)뿐이고, 포장 지면(G = 0)·차폐 없는 먼 거리에서는 RLS-19 보다 높게 나옴"
                   "(평지 100 m 에서 약 +4 ~ +6 dB: RLS-19 식 14 의 지면·기상 감쇠가 ISO 일반법 G = 0 에는 없음)")
    if cfg["model"].get("source_lines", "direction") == "lane" and name != "rls90":
        out.append("음원선을 차로마다 둠(model.source_lines: lane) — RLS-19 Bild 6 은 주행방향마다 1개선")
    return out


class PropagationModel(ABC):
    """전파 방법 인터페이스"""
    name = ""                       # 설정값
    code = -1                       # solver 커널 분기 코드

    @abstractmethod
    def kernel_bands(self, cfg):
        """커널 대역 인자 (bands, lams, alphas, wts)"""

    @abstractmethod
    def reflection_gain(self, cfg):
        """허상음원 1회 반사당 에너지 이득(커널 인자 rho)"""

    @abstractmethod
    def equations(self, cfg):
        """사용한 식 번호 목록"""

    @abstractmethod
    def not_implemented(self, cfg):
        """미구현 항목 목록"""

    @abstractmethod
    def label(self, cfg):
        """지도 정보란·제목에 쓰는 전파 방법 문구"""

    @abstractmethod
    def metadata(self, cfg):
        """run_info.json 에 넣을 방법별 항목"""

    def combo_label(self, cfg):
        """음원+전파 구성 문구(범례용)"""
        return self.label(cfg)

    def info(self, cfg):
        rf = cfg["propagation"]["reflection"]
        return {"propagation_method": self.name, "method_label": self.combo_label(cfg),
                "equations": self.equations(cfg),
                "reflection_order": {"grid": int(rf["max_order_grid"]), "facade": int(rf["max_order_facade"]),
                                     "points": int(rf["max_order_point"])},
                "D_refl_in_source": _canyon_on(cfg), "D_refl_note": d_refl_note(self.name, cfg),
                "standard_deviations": standard_deviations(self.name, cfg),
                "lateral_diffraction": self.lateral(cfg),
                "not_implemented": self.not_implemented(cfg), **self.metadata(cfg)}

    def lateral(self, cfg):
        return ""


class ISO9613Propagation(PropagationModel):
    """ISO 9613-2:1996 (현행 경로). 대기흡수계수는 ISO 9613-1"""
    name = "iso9613_2"
    code = 1

    def kernel_bands(self, cfg):
        p = cfg["propagation"]
        bands = np.array(p["bands_hz"], float)
        lams = C_SOUND / bands
        if p["alpha_dB_per_km"] is not None:
            alphas = np.array(p["alpha_dB_per_km"], float)
        else:
            alphas = np.array([alpha_iso9613_1(f, p["temperature_C"], p["humidity_pct"], p["pressure_kPa"])
                               for f in bands])
        w = 10 ** (0.1 * np.array(p["spectrum_dBA"], float))
        wts = w / w.sum()
        return bands, lams, alphas, wts

    def reflection_gain(self, cfg):
        return float(cfg["propagation"]["reflection"]["rho"])            # 식 (20) 10 lg ρ

    def _alt(self, cfg):
        return cfg["input"]["ground"]["method"] == "alternative"

    def equations(self, cfg):
        g = ["ISO 9613-2 식 (10) 대안법", "식 (11) D_Ω"] if self._alt(cfg) else ["ISO 9613-2 식 (9) 일반법·표 3"]
        return (["ISO 9613-2 식 (3)", "식 (4)", "식 (5)", "식 (7) A_div", "식 (8) A_atm·표 2(ISO 9613-1)"] + g
                + ["식 (12) A_bar = D_z − A_gr", "식 (14)~(18) D_z·C3·z·K_met, 상한 20/25 dB",
                   "7.5 식 (19) 반사 크기 조건", "식 (20) L_W,im = L_W + 10 lg ρ"])

    def not_implemented(self, cfg):
        return ["측면 회절(ISO 9613-2 식 13)", "음(−)의 z(가시선 위 장애물)", "A_misc(부속서 A)",
                "차폐 대상 조건(7.4 면밀도·수평 치수) 검사", "C_met(식 21·22) — L_AT(DW)로 계산",
                *(["옥타브 대역 스펙트럼(출처 미확보 — 1대역 사용)"]
                  if len(cfg["propagation"]["bands_hz"]) == 1 else []),
                *_COMMON_NOT_IMPLEMENTED]

    def lateral(self, cfg):
        return "미구현(ISO 9613-2 식 13)"

    def label(self, cfg):
        b = cfg["propagation"]["bands_hz"]
        g = cfg["input"]["ground"]
        bt = f"{b[0]:g} Hz" if len(b) == 1 else f"{len(b)}대역"
        return f"ISO 9613-2:1996, {bt}, 지면 {'대안법' if self._alt(cfg) else '일반법'} G={g['default_G']:g}"

    def combo_label(self, cfg):
        return "RLS-19 음원 + ISO 9613-2 전파 — 어느 표준도 규정하지 않은 프로젝트 구성 방법"

    def metadata(self, cfg):
        b = cfg["propagation"]["bands_hz"]
        alt = self._alt(cfg)
        return {"iso_ground_method": "alternative(7.3.2)" if alt else "general(7.3.1)",
                "iso_ground_note": ("7.3.2 적용 조건: 다공성 지면 또는 대부분이 다공성인 혼합 지면 — "
                                    "포장 지면(G≈0)에서는 조건 미충족") if alt else "",
                "representative_frequency_hz": float(b[0]) if len(b) == 1 else None,
                "representative_frequency_basis": "ISO 9613-2 1절 비고 1(A 가중 L_W 만 알면 500 Hz 감쇠항)"
                if len(b) == 1 else "옥타브 대역(spectrum_dBA)",
                "meteorology": "L_AT(DW) 순풍 조건(ISO 9613-2 식 3·5), C_met 미적용",
                "reflection_loss": f"ρ = {float(cfg['propagation']['reflection']['rho']):g} (ISO 9613-2 식 20)",
                "hybrid_note": ("RLS-19 의 L_W 는 반공간(2π) 기준. ISO 일반법 G = 0·근거리(q = 0)에서만 A_gr = −3 dB 로 "
                                "RLS-19 전파와 같은 값. 먼 거리는 A_gr = −3 − 3q(더 큰 음수)이고 RLS-19 식 (14)의 "
                                "감쇠(0 ~ 4,8 dB)가 없어 더 높게, 차폐 경로는 식 (12)에서 A_gr 이 상쇄되어 "
                                "회절이 작을 때 약 3 dB 낮게 나옴(TECHNICAL_NOTES 4a절)")}


class RLS19Propagation(PropagationModel):
    """RLS-19 3.5(식 11~17)·3.6(반사 조건, 표 8)"""
    name = "rls19"
    code = 0

    def kernel_bands(self, cfg):
        # A 가중 단일값. 대역 인자는 커널 형식상 1칸만 두며 감쇠 계산에는 쓰지 않음
        one = np.array([500.0])
        return one, C_SOUND / one, np.zeros(1), np.ones(1)

    def reflection_gain(self, cfg):
        return 10 ** (-0.1 * float(cfg["propagation"]["reflection"]["D_RV"]))    # 표 8

    def equations(self, cfg):
        return ["RLS-19 식 (2)", "식 (11) D_A", "식 (12) D_div", "식 (13) D_atm", "식 (14) D_gr",
                "식 (15) D_z", "식 (16) z", "식 (17) K_w", "3.6 반사 조건 h_R", "표 8 D_RV"]

    def not_implemented(self, cfg):
        return list(_COMMON_NOT_IMPLEMENTED)

    def lateral(self, cfg):
        return "고려 안 함(RLS-19 3.5 규정)"

    def label(self, cfg):
        return f"RLS-19 3.5·3.6 (반사 손실 D_RV {float(cfg['propagation']['reflection']['D_RV']):g} dB)"

    def combo_label(self, cfg):
        return "RLS-19 음원 + RLS-19 전파(3.5·3.6)"

    def metadata(self, cfg):
        return {"representative_frequency_hz": None,
                "representative_frequency_basis": "A 가중 단일값(RLS-19), 대역 설정 미사용",
                "meteorology": "RLS-19 3.1 주석: 순풍(약 3 m/s)·기온 역전 조건 값",
                "reflection_loss": f"D_RV = {float(cfg['propagation']['reflection']['D_RV']):g} dB/회 (표 8)",
                "reflection_rule": "h_R ≥ 1.0 m 이고 h_R ≥ 0.3·√a_R (3.6). h_R = 벽면 높이, a_R = 수평 거리(해석)",
                "rls19_notes": ["D_z 상한 없음(원문 3.5.5)", "다중 회절: C 를 A·B 중 큰 쪽에 더함(A = B 이면 A, 해석)",
                                "h_m = F/s_gr(그림 11)"]}


class RLS90Propagation(PropagationModel):
    """RLS-90 4.4.2 Teilstück-Verfahren(식 19~27)·4.6 Einfachreflexion(표 7 D_E)·표 2 K. 음원도 RLS-90(rls90.py)"""
    name = "rls90"
    code = 2

    def kernel_bands(self, cfg):
        one = np.array([500.0])                     # A 가중 단일값(대역 인자는 형식상 1칸)
        return one, C_SOUND / one, np.zeros(1), np.ones(1)

    def _DE(self, cfg):
        return float(cfg["propagation"]["reflection"].get("D_E", -1.0))

    def reflection_gain(self, cfg):
        return 10 ** (0.1 * self._DE(cfg))                                   # 표 7 D_E (음수)

    def equations(self, cfg):
        return ["RLS-90 식 (1)(2) L_r = L_m + K·표 2", "식 (3) 두 바깥 차로", "식 (6) L_m,E", "식 (7) L_m^(25)",
                "식 (8) D_v", "표 4 D_StrO", "식 (9) D_Stg", "식 (19)(20) Teilstück, D_l = 10 lg l",
                "식 (21) D_s", "식 (22) D_BM", "식 (23)(24a) D_B = D_refl − D_z", "식 (25)~(27) D_z·z·K_w",
                "4.6 반사 조건 h_R ≥ 0,3·√a_R", "표 7 D_E"]

    def not_implemented(self, cfg):
        return ["4.4.1 '긴 직선 도로' 방법·4.4.3 표준 단면 도표(Teilstück-Verfahren 만 사용)", "4.5 주차장",
                "중간값 0,1 dB 반올림(4.0, 연속값으로 계산)", "흡음 벽면 D_refl 식 (24b)", "section(단면) 소음지도"]

    def lateral(self, cfg):
        return "고려 안 함(RLS-90 에 규정 없음)"

    def label(self, cfg):
        return f"RLS-90 4.4.2·4.6 (반사 D_E {self._DE(cfg):g} dB)"

    def combo_label(self, cfg):
        return "RLS-90 음원 + RLS-90 전파(Teilstück-Verfahren)"

    def metadata(self, cfg):
        rf = cfg["propagation"]["reflection"]
        orders = {int(rf["max_order_grid"]), int(rf["max_order_facade"]), int(rf["max_order_point"])}
        return {"representative_frequency_hz": None,
                "representative_frequency_basis": "A 가중 단일값(RLS-90), 대역 설정 미사용",
                "meteorology": "RLS-90 4.0: 약 3 m/s 순풍·기온 역전 조건 값",
                "reflection_loss": f"D_E = {self._DE(cfg):g} dB/회 (표 7)",
                "reflection_rule": "h_R ≥ 0,3·√a_R (4.6). a_R = 음원–반사체 수평 거리, h_R = 벽면 높이(해석)",
                "reflection_order_note": ("RLS-90 4.6 은 1차 반사(Einfachreflexion)만 규정 — 2차 이상은 원문 밖 설정"
                                          if max(orders) > 1 else "1차 반사(RLS-90 4.6)"),
                "junction_K": "표 2: 수음점 ~ 가장 가까운 신호(signal) 교차점 거리로 3/2/1/0 dB, 결과 전체에 더함",
                "rls90_notes": ["D_z 상한 없음(식 25)", "차폐 시 D_BM 없음(4.4.2.1.2)",
                                "다중 회절: C 를 A·B 중 작은 쪽에 더함(A = B 이면 A, 해석)",
                                "h_m = F/s_gr(평탄 지형에서 0,5(h_GE + h_GI)와 같음)",
                                "p = 교통량 표 p_day·p_night → 입력 p1 + p2(RLS-19 3,5 t 기준 — 근사) → 둘 다 없으면 표 3(독일 기준)",
                                "M·p 빈칸 = 표 3(DTV × 계수, 독일 기준 — 국내와 다를 수 있음)",
                                "노면: SORI 코드 → 표 4 행 대응(rls90.SURFACE_ROW, 해석)"]}


METHODS = {m.name: m for m in (ISO9613Propagation(), RLS19Propagation(), RLS90Propagation())}


def method_name(cfg):
    return cfg.get("propagation_method", DEFAULT_METHOD) or DEFAULT_METHOD


def get_model(cfg):
    name = method_name(cfg)
    if name not in METHODS:
        raise ValueError(f"propagation_method 는 {' 또는 '.join(METHODS)} (입력: {name!r})")
    return METHODS[name]

SHORT = {"iso9613_2": "RLS-19 + ISO 9613-2", "rls19": "RLS-19", "rls90": "RLS-90"}


def result_model(cfg, out=None):
    """그림·3D 뷰어용: 출력 폴더 run_info.json 의 결과 종류별 방법이 하나로 같은지, 설정과 같은지 확인하고 모델을 돌려줌.
    run_info 가 없으면 설정값. 방법이 섞여 있거나 설정과 다르면 오류(지도에 다른 방법이 섞이지 않게)."""
    import json
    import os
    out = out or cfg["project"]["output_dir"]
    model = get_model(cfg)
    p = os.path.join(out, "run_info.json")
    if not os.path.exists(p):
        return model
    info = json.load(open(p, encoding="utf-8"))
    used = set((info.get("outputs") or {}).values()) or {info.get("propagation_method", DEFAULT_METHOD)}
    if len(used) > 1:
        raise ValueError(f"{out} 의 결과에 전파 방법이 섞여 있음: {info.get('outputs')}")
    m = used.pop()
    if m != model.name:
        raise ValueError(f"{out} 의 결과는 전파 방법 {m} 로 계산됨 — 설정 propagation_method({model.name})와 다름")
    return model
