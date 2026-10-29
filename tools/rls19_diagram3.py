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
  다이어그램은 먼 거리에서 높이와 무관하게 곡선이 겹치는데, 식 (14)는 h 가 크면 D_gr 이 작아져 겹칠 수 없음 → 원인 미확인.
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


if __name__ == "__main__":
    print("| s⊥ m | 판독(±0.2) | h=0 | h=4 | h=20 | h=80 |")
    print("|---|---|---|---|---|---|")
    for sp, v in READ.items():
        c = [level_reduction(sp, h, 40001) for h in (0.0, 4.0, 20.0, 80.0)]
        print(f"| {sp} | {v:.2f} | " + " | ".join(f"{x:.2f} ({x - v:+.2f})" for x in c) + " |")
