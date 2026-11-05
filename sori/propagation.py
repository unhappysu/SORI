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
_D_REFL_NOTE = ("D_refl(RLS-19 식 9, 다중 반사 가산값)을 음원 L_W′ 에 포함하고 허상음원 반사도 함께 적용 — "
                "도로 협곡에서 이중 계상 가능성 있음. 이 결합을 배제·허용하는 규정은 RLS-19 3.3.8·3.6, "
                "ISO 9613-2 7.5, 「소음지도의 작성방법」 별표 2에서 확인하지 못함(근거 미확인)")


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
                "D_refl_in_source": True, "D_refl_note": _D_REFL_NOTE,
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
                "reflection_loss": f"ρ = {float(cfg['propagation']['reflection']['rho']):g} (ISO 9613-2 식 20)"}


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
                                "p = 교통량 표 p_day·p_night, 없으면 p1 + p2(RLS-19 3,5 t 기준 — 근사)",
                                "노면: SORI 코드 → 표 4 행 대응(rls90.SURFACE_ROW, 해석)"]}

    def info(self, cfg):
        d = super().info(cfg)
        d["D_refl_in_source"] = True
        d["D_refl_note"] = "D_refl(식 24a, 4·h_Beb/w ≤ 3,2)을 1차 반사(D_E)와 함께 씀 — RLS-90 4.6 규정 그대로"
        return d


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
