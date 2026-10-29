# -*- coding: utf-8 -*-
"""
TEST-20 전파 과제 재현 결과표: python tools/test20_report.py
열: SORI(전체·직접·1차·2차), 시험결과, Δ, 엄격 기준(소수 둘째 자리 반올림 후 ±0.01 dB), 기준 범위(소수 첫째 자리 반올림 후 포함).
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
import test20_cases as T  # noqa: E402


def f(v):
    return "—" if not math.isfinite(v) else f"{v:.2f}"


if __name__ == "__main__":
    print("| 과제 | 수음점 | SORI | 직접 | 1차 | 2차 | 시험결과 | Δ | 엄격(±0.01) | 기준 범위 | 기준 범위 포함 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    n = ok = 0
    for c in T.CASES:
        for k, v in T.run(c).items():
            p, lo, hi = c.expected[k]
            strict = abs(round(v["L"], 2) - p) <= 0.01 + 1e-9
            ref = lo - 1e-9 <= round(v["L"], 1) <= hi + 1e-9
            n += 1; ok += strict
            print(f"| {c.name} | {k} | {f(v['L'])} | {f(v['Ldir'])} | {f(v['L1'])} | {f(v['L2'])} | {p:.2f} | "
                  f"{v['L'] - p:+.2f} | {'통과' if strict else '불통과'} | {lo:g}~{hi:g} | {'예' if ref else '아니오'} |")
    print(f"\n엄격 기준 통과 {ok}/{n}")
