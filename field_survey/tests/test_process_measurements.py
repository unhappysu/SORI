"""process_measurements.py 검산 — 손으로 확인할 수 있는 단순 사례.

※ 본 테스트는 생성형 AI(Claude)를 활용하여 작성되었음.

    python -m pytest field_survey/tests -q
"""
import csv
import datetime as dt
import math
import sys
from pathlib import Path

import pytest
from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_fieldbook_xlsx  # noqa: E402
import process_measurements as pm  # noqa: E402


# ───────────────────────── 계산식
def test_energy_average_same_levels():
    assert pm.energy_average([70.0, 70.0]) == pytest.approx(70.0)


def test_energy_average_60_70():
    # 10·lg((10^6 + 10^7)/2) = 10·lg(5.5×10^6) = 67.40
    assert pm.energy_average([60.0, 70.0]) == pytest.approx(10 * math.log10(5.5e6))
    assert pm.round_half_up(pm.energy_average([60.0, 70.0]), 1) == 67.4


def test_energy_average_time_weighted():
    # (20분×10^6 + 10분×10^7) / 30분 = 1.2×10^8 / 30 = 4×10^6 → 10·lg(4×10^6) = 66.02 dB
    assert pm.energy_average([60.0, 70.0], [20.0, 10.0]) == pytest.approx(10 * math.log10(4e6))
    assert pm.round_half_up(pm.energy_average([60.0, 70.0], [20.0, 10.0]), 1) == 66.0


def test_energy_not_below_arithmetic():
    levels = [58.0, 63.0, 71.0]
    assert pm.energy_average(levels) >= sum(levels) / len(levels)


@pytest.mark.parametrize("x, nd, expect", [(65.5, 0, 66), (64.5, 0, 65), (68.25, 1, 68.3), (71.04, 1, 71.0)])
def test_round_half_up(x, nd, expect):
    assert pm.round_half_up(x, nd) == expect


def test_arithmetic_average_legal():
    # (65 + 66)/2 = 65.5 → 최종 66 (사사오입; 파이썬 round(65.5) = 66, round(64.5) = 64 와 구분)
    assert pm.arithmetic_average_legal([65.0, 66.0]) == (65.5, 66)
    assert pm.arithmetic_average_legal([64.0, 65.0]) == (64.5, 65)


def test_minutes_between_midnight():
    assert pm.minutes_between(dt.time(23, 55), dt.time(0, 5)) == pytest.approx(10.0)
    assert pm.minutes_between(dt.time(7, 30), dt.time(7, 40, 30)) == pytest.approx(10.5)


def test_diff_stats():
    # 차이 1, −1, 3 → 평균 1, 표본표준편차 √((0² + 2² + 2²)/2) = 2, 최대 |3|
    s = pm.diff_stats([("A", 1.0), ("B", -1.0), ("C", 3.0)])
    assert (s["n"], s["mean_diff"], s["sd_diff"], s["max_abs_diff"], s["max_abs_point"]) == (3, 1.0, 2.0, 3.0, "C")


def test_empty_inputs_raise():
    with pytest.raises(ValueError):
        pm.energy_average([])
    with pytest.raises(ValueError):
        pm.arithmetic_average_legal([])


# ───────────────────────── 야장 → CSV
def _fieldbook(tmp_path):
    src = build_fieldbook_xlsx.build(tmp_path / "야장.xlsx")
    wb = load_workbook(src)
    p = wb["2_측정점"]
    p["F5"], p["G5"] = 205100.0, 553120.0          # R1 좌표
    n = wb["3_소음기록"]
    rows = [("R1", "D1", 70.0, 10, "Y"), ("R1", "D2", 72.0, 10, "Y"), ("R1", "D3", 68.0, 10, "Y"),
            ("R1", "D4", 74.0, 10, "Y"), ("R1", "N1", 62.0, 10, "Y"), ("R1", "N2", 58.0, 10, "Y"),
            ("F1", "D1", 65.0, 10, "Y"), ("F1", "D2", 67.0, 10, "N"), ("F1", "D3", 66.0, 20, "Y")]
    for i, (pid, rd, L, mins, ok) in enumerate(rows):
        r = 5 + i
        start = dt.datetime(2026, 10, 21, 7, 30)
        n.cell(r, 1, pid); n.cell(r, 2, rd); n.cell(r, 9, L); n.cell(r, 18, ok)
        n.cell(r, 6, start.time()); n.cell(r, 7, (start + dt.timedelta(minutes=mins)).time())
        n.cell(r, 22, f"{rd}_TC")
    wb.save(src)
    return src


def _read(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return {(r["point_id"], r.get("period")): r for r in csv.DictReader(f)}


def test_fieldbook_to_measured_levels(tmp_path):
    out = tmp_path / "data"
    pm.main([str(_fieldbook(tmp_path)), "--out", str(out)])
    m = _read(out / "measured_levels.csv")
    assert list(csv.reader(open(out / "measured_levels.csv", encoding="utf-8-sig")))[0][:9] == \
        ["point_id", "x", "y", "z", "period", "Leq", "n_samples", "valid_flag", "traffic_count_ref"]
    r1d = m[("R1", "day")]
    # 에너지평균 10·lg((10^7 + 10^7.2 + 10^6.8 + 10^7.4)/4) = 71.56 → 71.6, 산술평균 71.0
    assert (float(r1d["Leq"]), int(r1d["n_samples"]), r1d["valid_flag"]) == (71.6, 4, "Y")
    assert (float(r1d["Leq_legal_mean"]), int(r1d["Leq_legal_final"]), r1d["legal_count_ok"]) == (71.0, 71, "Y")
    assert (float(r1d["x"]), float(r1d["y"]), float(r1d["z"])) == (205100.0, 553120.0, 1.5)
    assert r1d["traffic_count_ref"] == "D1_TC;D2_TC;D3_TC;D4_TC"
    r1n = m[("R1", "night")]
    # 10·lg((10^6.2 + 10^5.8)/2) = 60.44 → 60.4, 산술평균 60.0, 밤 2회 충족
    assert (float(r1n["Leq"]), float(r1n["Leq_legal_mean"]), r1n["legal_count_ok"]) == (60.4, 60.0, "Y")
    f1d = m[("F1", "day")]
    # 무효(N) 행 제외. (10분×10^6.5 + 20분×10^6.6)/30분 → 65.69 → 65.7, 산술 (65+66)/2 = 65.5 → 66, 낮 4회 미달
    assert (float(f1d["Leq"]), int(f1d["n_samples"]), float(f1d["Leq_legal_mean"]), int(f1d["Leq_legal_final"]),
            f1d["legal_count_ok"], float(f1d["total_minutes"])) == (65.7, 2, 65.5, 66, "N", 30.0)
    assert ("F1", "night") not in m


def test_short_measurement_flagged(tmp_path):
    src = _fieldbook(tmp_path)
    wb = load_workbook(src)
    n = wb["3_소음기록"]
    n.cell(5, 7, dt.time(7, 35))                    # R1 D1 을 5분으로
    wb.save(src)
    out = tmp_path / "d2"
    pm.main([str(src), "--out", str(out)])
    assert _read(out / "measured_levels.csv")[("R1", "day")]["valid_flag"] == "N"


def test_model_comparison(tmp_path):
    model = tmp_path / "model.csv"
    model.write_text("point_id,period,Leq_calc\nR1,day,70.0\nF1,day,66.7\nR1,night,\n", encoding="utf-8")
    out = tmp_path / "data"
    pm.main([str(_fieldbook(tmp_path)), "--model", str(model), "--out", str(out)])
    c = _read(out / "comparison.csv")
    assert float(c[("R1", "day")]["diff"]) == 1.6       # 71.6 − 70.0
    assert float(c[("F1", "day")]["diff"]) == -1.0      # 65.7 − 66.7
    assert c[("R1", "night")]["note"] == "계산값 없음"
    s = next(csv.DictReader(open(out / "comparison_summary.csv", encoding="utf-8-sig")))
    # 차이 1.6, −1.0 → 평균 0.3, 표준편차 √((1.3² + 1.3²)/1) = 1.84
    assert (int(s["n"]), float(s["mean_diff"]), float(s["sd_diff"]), float(s["max_abs_diff"]), s["max_abs_point"]) == \
        (2, 0.3, 1.84, 1.6, "R1/day")
