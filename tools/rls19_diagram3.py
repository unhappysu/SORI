# -*- coding: utf-8 -*-
"""
RLS-19 다이어그램 III(평탄 지형, 길고 곧은 음원선의 전파 레벨 감소)과 SORI RLS-19 감쇠식(rls19_propagation.D_A) 비교.
  python tools/rls19_diagram3.py
다이어그램 판독값: 원문 PDF(fgsv_052_korrektur_04032020.pdf) 28쪽 그림 타일을 이어 붙여 픽셀로 읽음
  (y 축 59.27 px/dB, x 축 1178 px/decade). 100 m 넘는 거리는 h = 0~80 m 곡선이 한 줄로 겹쳐 높이 구분 없이 1개 값.
  판독 오차: 선 두께·격자 정렬 기준 ±0.2 dB(프로젝트 판단).
계산: 원문 식 −10 lg[∫ 10^(−0.1·D_A(φ)) dφ] 를 단위 길이 적분(dl = s⊥/cos²φ dφ)으로 해석 — 원문·번역본 모두 dφ 만 적혀 있음
  (dφ 그대로는 단위 길이당 L_W′ 와 맞지 않아 15 dB 이상 어긋남). 음원선 높이 0.5 m, D_A = 식 (11)~(14), 차폐 없음,
  h_m = (0.5 + h)/2 (평탄 지면, 그림 11).
결과(2026-10-29): 계산값이 판독값보다 h = 0 m 에서 0.8~1.0 dB, h = 4 m 에서 1.1~1.3 dB, h = 20~80 m 에서 1.7~4.6 dB 작음.
원인 조사(같은 날, 이 도구의 --analyze):
  ① 판독값을 재현하는 데 필요한 등가 지면항 X = 판독 − (D_div + D_atm 적분): h = 0 m 에서 5.2~5.7 dB 로
     식 (14) 최댓값 4.8 dB 를 넘음 → h_m 정의와 무관하게 0.4~0.9 dB 가 더 있음(무엇인지 미확인).
  ② 높이 의존: "D_gr = 4.8 dB 고정"(높이와 무관) 가정이 먼 거리 판독값을 h = 0~80 m 모두 −0.9~+0.1 dB 로 재현,
     식 (14)+그림 11(h_m = (0.5 + h)/2)은 −1.7~−4.6 dB. → 다이어그램은 수음점 높이에 따른 D_gr 감소를 반영하지 않은
     조건으로 만든 것으로 보임(추정). 다이어그램은 부록의 보조 자료이고 계산 규정은 본문 식이므로 코드는 본문 식을 유지.
  ③ 원문 표기 차이: 부록 목차는 "h 0,5 m ~ 80 m", 그림 제목은 "0 m ~ 80 m"(원문 자체, 번역 오류 아님).
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sori import rls19_propagation as RP  # noqa: E402
from sori.geometry import NGEOM, G_D, G_HM, G_SHIELD  # noqa: E402

READ = {120: 30.77, 150: 32.0, 250: 35.15, 350: 37.4, 390: 38.24}      # s⊥ m : 판독 레벨 감소 dB
TOL_READ = 0.2


def level_reduction(sp, h, n=200001):
    ph = np.linspace(-math.pi / 2, math.pi / 2, n)[1:-1]
    geom = np.zeros(NGEOM); geom[G_SHIELD] = 0.0; geom[G_HM] = (0.5 + h) / 2
    da = np.empty(len(ph))
    for k, a in enumerate(ph):
        geom[G_D] = math.hypot(sp / math.cos(a), h - 0.5)
        da[k] = RP.D_A(geom)
    return -10 * math.log10(np.trapezoid(10 ** (-0.1 * da) * sp / np.cos(ph) ** 2, ph))


# 근거리 높이별 판독값(34·65 m, 격자선 잔상 25·26·30·31 dB 제외)과 먼 거리 겹침 값
READ_H = {34: {80: 26.21, 60: 25.46, 40: 24.76, 20: 24.25, 0: 24.04}, 65: {80: 28.19, 60: 27.84, 40: 27.58, 20: 27.38},
          130: {0: 31.23, 80: 31.23}, 230: {0: 34.57, 80: 34.57}, 370: {0: 37.81, 80: 37.81}}


def _integ(sp, h, ground, n=40001):
    ph = np.linspace(-math.pi / 2, math.pi / 2, n)[1:-1]
    s = np.sqrt((sp / np.cos(ph)) ** 2 + (h - 0.5) ** 2)
    D = 20 * np.log10(s) + RP.LG_2PI + s / 200 + ground(s, h)
    return -10 * math.log10(np.trapezoid(10 ** (-0.1 * D) * sp / np.cos(ph) ** 2, ph))


def analyze():
    eq14 = lambda s, h: np.maximum(4.8 - (0.5 + h) / 2 / s * (34 + 600 / s), 0.0)
    hyps = {"식 14 + 그림 11(본문)": eq14, "D_gr = 4.8 고정": lambda s, h: 4.8, "지면항 없음": lambda s, h: 0.0}
    print("가설별 계산 − 판독 [dB] (s⊥/h)")
    for name, g in hyps.items():
        r = [(sp, h, _integ(sp, h, g) - v) for sp, d in READ_H.items() for h, v in d.items()]
        print(f"  {name}: " + " ".join(f"{sp}/{h}:{x:+.2f}" for sp, h, x in r))
    print("필요한 등가 지면항 X = 판독 − (D_div + D_atm) [dB]")
    for sp, d in READ_H.items():
        print(f"  {sp} m: " + " ".join(f"h{h}:{v - _integ(sp, h, hyps['지면항 없음']):.2f}" for h, v in d.items()))


if __name__ == "__main__":
    if "--analyze" in sys.argv:
        analyze()
        sys.exit(0)
    print("| s⊥ m | 판독(±0.2) | h=0 | h=4 | h=20 | h=80 |")
    print("|---|---|---|---|---|---|")
    for sp, v in READ.items():
        c = [level_reduction(sp, h, 40001) for h in (0.0, 4.0, 20.0, 80.0)]
        print(f"| {sp} | {v:.2f} | " + " | ".join(f"{x:.2f} ({x - v:+.2f})" for x in c) + " |")
