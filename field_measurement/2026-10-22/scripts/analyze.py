"""사가정로 현장 소음측정(2026-10-22) 2~4단계 분석.

AI(Claude)를 활용하여 제작하였음.

실행: python3 field_measurement/2026-10-22/scripts/analyze.py
(전체 재현 순서: analyze.py → wav_check.py → analyze.py → model_compare.py. 두 번째 analyze.py 는
 wav_check.py 가 만든 녹음 순음 지표로 지점2 경적 후보를 선별한다.)
입력: ../raw_copy/ (원본 복사본, 해시는 ../SHA256SUMS_원본.txt)
출력: ../results/*.csv, ../figures/*.png, ../model_validation/*
난수를 쓰지 않으므로 다시 실행하면 같은 결과가 나온다.

시각 처리 원칙
- 각 소음계의 내부 시계를 그대로 쓴다. 두 기기 간 시계 차이는 추정만 하고 보정하지 않는다.
- AUTO_LP 타임스탬프는 각 샘플 구간의 끝 시각으로 본다(가정). 그림은 구간 중앙에 찍는다.
"""
import csv
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np

from nl62 import energy_mean, load_store, to_1s

BASE = Path(__file__).resolve().parents[1]
RES = BASE / "results"
FIG = BASE / "figures"
MV = BASE / "model_validation"
AI_NOTE = "AI(Claude)를 활용하여 제작하였음"
DAY = "2026-10-22"

POINTS = {1: "지점1 (사가정로 123 앞, 북측)", 2: "지점2 (전농동SK아파트상가입구, 남측)"}
ROUNDS = {1: ("16:15", "16:25"), 2: ("16:28", "16:38"), 3: ("16:41", "16:51")}
COLOR = {1: "#2a78d6", 2: "#eb6834"}   # dataviz 기본 팔레트 1·2번

# 현장 교통량(10분 계수, 승용차/화물차). 각 조는 자기 쪽(가까운 차로) 방향을 셌다고 가정.
# 방향은 지도 판정: 지점1 북측 → 서행(전농사거리·기점 방향) = 상행, 지점2 남측 → 동행 = 하행
TRAFFIC = {
    1: {"direction": "상행(서행, 전농사거리 방향)", "counts": {1: (148, 33), 2: (138, 54), 3: (133, 47)}},
    2: {"direction": "하행(동행, 사가정역 방향)", "counts": {1: (180, 76), 2: (180, 68), 3: (163, 50)}},
}

# 지점1 야장 이벤트 기록(기준 시계 = 네이버 시계). 기기 시계와의 차이는 1~2 s 이내로 봄.
P1_EVENTS = [
    (1, "16:21:00", "16:21:03", "도보에 오토바이 지나감(야장 최대 73.2 dB)"),
    (2, "16:29:20", "16:29:25", "도보에 오토바이 지나감(야장 최대 74.1 dB)"),
]
# 지점2 야장 힌트(손글씨 판독, 불확실): 2회 16:31 사이렌, 3회 16:4? 경적 2회
P2_HINT = {2: ("16:31:00", "16:31:59", "사이렌(야장 16:31)"), 3: ("16:41:00", "16:51:00", "경적 2회(야장 16:4?, 시각 판독 불가)")}

SIREN_WIN = ("16:31:40", "16:32:10")   # 지점2 기기 시각, 녹음 스펙트로그램에서 읽은 사이렌 지속 구간
HORN_TONAL_PCT = 20.0  # 경적 후보 선별: 녹음 0.25 s 프레임 중 순음 돌출 15 dB 이상 비율(%)
PEAK_DB = 10.0      # 후보 탐지: 1초 LAeq가 61 s 이동 중앙값보다 이만큼 이상 높을 때
MERGE_S = 3         # 이 간격 안의 피크는 하나의 후보로 묶음
PAD_S = 1           # 후보 구간 앞뒤 여유
EVENT_SEARCH_S = 60  # 지점1 야장 이벤트: 기록 시각 앞뒤로 피크를 찾는 범위
EVENT_EDGE_DB = 6.0  # 지점1 이벤트 제외 구간: 100 ms LAFmax가 61 s 중앙값 + 이 값 이상인 연속 구간
CLOCK_OFFSET_S = 1.6  # 추정 시계 차이(지점2가 늦음, 사이렌 녹음 교차상관 1.59 s). 그림·보정에는 쓰지 않고 상대 지점 확인에만 사용


def T(hms):
    return np.datetime64(f"{DAY}T{hms}", "ms")


def fmt(t64):
    return str(t64)[11:22]


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        f.write(f"# {AI_NOTE}\n")
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def r1(x):
    return "" if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), 1)


def rolling_median(x, w=61):
    h = w // 2
    return np.array([np.median(x[max(0, i - h): i + h + 1]) for i in range(len(x))])


def percentiles(l1s):
    """L5, L10, L50, L90, L95 (초과 백분율). 1초 LAeq로 계산(기기 LN Mode = Leq_1s와 같게)."""
    v = np.percentile(l1s, [95, 90, 50, 10, 5])
    return dict(zip(["L5", "L10", "L50", "L90", "L95"], v))


def overlap_mask(store, lo, hi):
    """샘플 구간 (t_end-dt, t_end]이 [lo, hi]와 겹치는 샘플."""
    dt = np.timedelta64(int(store.dt * 1000), "ms")
    return (store.t_end > lo) & (store.t_end - dt < hi)


def moving_1s(store):
    """지점1 100 ms 자료의 0.1 s 간격 1초 이동 LAeq(교차상관·피크 비교용)."""
    e = np.convolve(10 ** (store.leq / 10), np.ones(10) / 10, "valid")
    return store.t_end[9:], 10 * np.log10(e)


def detect_candidates(t, l):
    """1초 LAeq 시계열에서 돌출 피크 후보 구간을 찾는다(확정 아님)."""
    med = rolling_median(l)
    idx = np.where(l - med >= PEAK_DB)[0]
    groups = []
    for i in idx:
        if groups and i - groups[-1][-1] <= MERGE_S:
            groups[-1].append(i)
        else:
            groups.append([i])
    out = []
    for g in groups:
        k = g[int(np.argmax(l[g]))]
        out.append({"lo": t[g[0]] - np.timedelta64(1000 * (1 + PAD_S), "ms"),
                    "hi": t[g[-1]] + np.timedelta64(1000 * PAD_S, "ms"),
                    "t_peak": t[k] - np.timedelta64(500, "ms"), "L_peak": l[k], "excess": l[k] - med[k]})
    return out


def clock_offset(a, b):
    """지점1(100 ms)과 지점2(1 s) 시계열 교차상관으로 시계 차이를 추정한다.

    반환 lag: 지점2 기기 시각 + lag ≈ 지점1 기기 시각 (양수면 지점2 시계가 늦음).
    """
    ta, la = moving_1s(a)
    ta_s = (ta - ta[0]) / np.timedelta64(1, "ms") / 1000
    tb_s = (b.t_end - ta[0]) / np.timedelta64(1, "ms") / 1000
    best = (-2.0, 0.0)
    r0 = None
    for lag in np.round(np.arange(-15, 15.001, 0.1), 1):
        x = np.interp(tb_s + lag, ta_s, la, left=np.nan, right=np.nan)
        ok = ~np.isnan(x)
        r = np.corrcoef(x[ok], b.leq[ok])[0, 1]
        if lag == 0:
            r0 = r
        if r > best[0]:
            best = (r, lag)
    return best[1], best[0], r0


def main():
    for d in (RES, FIG, MV):
        d.mkdir(parents=True, exist_ok=True)
    S = {(p, r): load_store(p, r) for p in (1, 2) for r in (1, 2, 3)}

    # ---------------- 2단계: 회차별 지표 ----------------
    metric_rows, event_rows, clock_rows = [], [], []
    excl = {}
    for (p, r), s in S.items():
        blk = s.device_blocks[0]
        n = int(round(blk["dur_s"] / s.dt))           # 기기 10분 블록과 같은 샘플 범위
        leq = s.leq[:n]
        t1, l1 = to_1s(s)
        l1 = l1[: int(round(blk["dur_s"]))]
        pc = percentiles(l1)
        re = {"LAeq": energy_mean(leq), **pc}
        if s.dt < 1:
            re["LAmax"] = np.nanmax(s.lmax[:n])
            re["LAmin"] = np.nanmin(s.lmin[:n])
            max_note = "100 ms 구간 LAFmax 최대값"
        else:
            re["LAmax"] = np.nan
            re["LAmin"] = np.nan
            max_note = "시계열에 LAF 최대·최소가 없어 재계산 불가(1초 LAeq 최대 %.1f, 최소 %.1f는 참고)" % (l1.max(), l1.min())
        row = {"지점": p, "회차": r, "파일": s.name, "일련번호": s.serial,
               "분석구간_시작": fmt(s.t_end[0] - np.timedelta64(int(s.dt * 1000), "ms")),
               "분석구간_길이_s": round(blk["dur_s"], 1), "샘플수": n, "샘플간격_s": s.dt}
        dev_key = {"LAeq": "Leq", "LAmax": "Lmax", "LAmin": "Lmin"}
        for k in ["LAeq", "LAmax", "LAmin", "L5", "L10", "L50", "L90", "L95"]:
            dv = blk[dev_key.get(k, k)]
            row[f"{k}_기기"] = r1(dv)
            row[f"{k}_재계산"] = r1(re[k])
            row[f"{k}_차이"] = "" if np.isnan(re[k]) else round(float(re[k]) - dv, 2)
        row["LAeq_전체파일_재계산"] = r1(energy_mean(s.leq))
        row["비고"] = max_note
        metric_rows.append(row)

    # 지점1: 야장 기록 이벤트를 실제 피크와 대조해 제외 구간을 정한다(사용자 지시 2026-10-23)
    # 야장 시각 ±EVENT_SEARCH_S 안에서 1초 이동 LAeq가 61 s 이동 중앙값보다 PEAK_DB 이상 돌출한 피크 중
    # 야장 구간에 가장 가까운 것을 고르고, 100 ms LAFmax가 (중앙값 + EVENT_EDGE_DB) 이상인 연속 구간을 제외한다.
    for rnd, lo, hi, what in P1_EVENTS:
        s = S[(1, rnd)]
        b = S[(2, rnd)]
        ta, la = moving_1s(s)
        med = rolling_median(la, 601)                  # 0.1 s 간격 → 601개 = 61 s
        prom = la - med
        win = (ta >= T(lo) - np.timedelta64(EVENT_SEARCH_S * 1000, "ms")) & \
              (ta <= T(hi) + np.timedelta64(EVENT_SEARCH_S * 1000, "ms"))
        cand = np.where(win & (prom >= PEAK_DB))[0]
        note_mid = T(lo) + (T(hi) - T(lo)) / 2
        m_note = overlap_mask(s, T(lo), T(hi))
        if len(cand) == 0:
            event_rows.append({"지점": 1, "회차": rnd, "구분": "대조 실패(미제외)", "시작": lo, "끝": hi,
                               "길이_s": 0, "구간_LAFmax": r1(np.nanmax(s.lmax[m_note])), "내용": what,
                               "근거": f"야장 시각 ±{EVENT_SEARCH_S} s 안에 돌출 피크 없음", "상대지점_동시각": "", "비고": ""})
            continue
        # 같은 피크에 속한 연속 샘플은 묶어서 최대값 위치를 쓴다
        k = cand[np.argmin(np.abs((ta[cand] - note_mid) / np.timedelta64(1, "ms")))]
        grp = cand[np.abs(cand - k) <= 30]
        k = grp[np.argmax(la[grp])]
        t_peak = ta[k] - np.timedelta64(500, "ms")      # 1초 이동창의 중앙
        # 100 ms 자료에서 피크 주변 연속 구간
        # 피크 ±1 s 안에서 100 ms LAFmax 최대 샘플을 찾고, LAFmax ≥ 중앙값 + EVENT_EDGE_DB 인 연속 구간으로 넓힌다
        near = np.where(np.abs((s.t_end - t_peak) / np.timedelta64(1, "ms")) <= 1000)[0]
        i0 = int(near[np.nanargmax(s.lmax[near])])
        thr = med[k] + EVENT_EDGE_DB
        a0 = i0
        while a0 > 0 and s.lmax[a0 - 1] >= thr and i0 - a0 < 50:
            a0 -= 1
        a1 = i0
        while a1 < len(s.lmax) - 1 and s.lmax[a1 + 1] >= thr and a1 - i0 < 50:
            a1 += 1
        m = np.zeros(len(s.leq), bool)
        m[a0: a1 + 1] = True
        excl.setdefault((1, rnd), np.zeros(len(s.leq), bool))
        excl[(1, rnd)] |= m
        t0 = s.t_end[a0] - np.timedelta64(100, "ms")
        t1 = s.t_end[a1]
        shift = (t_peak - note_mid) / np.timedelta64(1, "ms") / 1000
        # 지점2(약 1.6 s 늦은 시계)에서 같은 시각의 레벨: 보도 위 오토바이라면 지점2에는 작게 나타나야 함
        tb = b.t_end + np.timedelta64(int(CLOCK_OFFSET_S * 1000), "ms")
        wb = (tb >= t0 - np.timedelta64(1000, "ms")) & (tb <= t1 + np.timedelta64(1000, "ms"))
        b_txt = f"지점2 같은 시각(시계차 {CLOCK_OFFSET_S} s 반영) 1초 LAeq 최대 {b.leq[wb].max():.1f} dB, 지점2 중앙값 {np.median(b.leq):.1f}"
        event_rows.append({"지점": 1, "회차": rnd, "구분": "제외(야장 기록 + 피크 대조)", "시작": fmt(t0), "끝": fmt(t1),
                           "길이_s": round(m.sum() * s.dt, 1), "구간_LAFmax": r1(np.nanmax(s.lmax[m])),
                           "내용": f"{what}. 야장 기록 {lo}~{hi}",
                           "근거": f"야장 시각 ±{EVENT_SEARCH_S} s 안 가장 가까운 돌출 피크(중앙값 +{prom[k]:.1f} dB), "
                                   f"피크 {fmt(t_peak)} = 야장 구간 중앙에서 {shift:+.1f} s. 제외 구간 = 100 ms LAFmax ≥ 중앙값 + {EVENT_EDGE_DB:.0f} dB 연속 구간. "
                                   f"야장 구간 자체의 LAFmax {np.nanmax(s.lmax[m_note]):.1f} dB",
                           "상대지점_동시각": b_txt, "비고": ""})

    # 지점2: 피크 후보 자동 탐지(확정 아님) + 지점1 동시각 확인
    # wav_check.py 가 만든 녹음 순음 지표가 있으면 경적 후보 선별에 쓴다(없으면 야장 힌트만)
    tonal = {}
    tpath = RES / "특이사항_후보_음색지표.csv"
    if tpath.exists():
        with open(tpath, encoding="utf-8-sig") as f:
            for row in csv.DictReader(x for x in f if not x.startswith("#")):
                if row["지점"] == "2":
                    tonal[row["시작"]] = float(row["순음프레임_비율_%"])
    for r in (1, 2, 3):
        b = S[(2, r)]
        a = S[(1, r)]
        ta, la = moving_1s(a)
        med_a = np.median(la)
        cands = detect_candidates(b.t_end, b.leq)
        excl[(2, r)] = np.zeros(len(b.leq), bool)
        for c in cands:
            win = (ta >= c["lo"] - np.timedelta64(3000, "ms")) & (ta <= c["hi"] + np.timedelta64(3000, "ms"))
            a_peak = la[win].max() if win.any() else np.nan
            a_t = fmt(ta[win][np.argmax(la[win])] - np.timedelta64(500, "ms")) if win.any() else ""
            hint = P2_HINT.get(r)
            in_hint = bool(hint and T(hint[0]) <= c["t_peak"] <= T(hint[1]))
            tf = tonal.get(fmt(c["lo"]))
            if r == 2:
                # 사이렌: 녹음 스펙트로그램에서 확인한 지속 구간(SIREN_WIN)을 따로 제외하므로 후보 구간은 표시만
                used = False
            elif tonal:
                # 경적: 야장 힌트 구간 안 + 녹음 순음 프레임 비율 ≥ HORN_TONAL_PCT 인 후보만 제외(참고값)
                used = in_hint and tf is not None and tf >= HORN_TONAL_PCT
            else:
                used = in_hint
            if used:
                excl[(2, r)] |= overlap_mask(b, c["lo"], c["hi"])
            event_rows.append({
                "지점": 2, "회차": r, "구분": "후보·제외(참고값)" if used else "후보(미제외)",
                "시작": fmt(c["lo"]), "끝": fmt(c["hi"]),
                "길이_s": round((c["hi"] - c["lo"]) / np.timedelta64(1, "s"), 1),
                "구간_LAFmax": "", "내용": f"1초 LAeq 최대 {c['L_peak']:.1f} dB (중앙값 +{c['excess']:.1f})",
                "근거": f"자동 탐지(61 s 이동 중앙값 +{PEAK_DB:.0f} dB 이상). "
                        + (f"야장 힌트 '{hint[2]}' 구간 안" if in_hint else "야장 힌트와 대응 안 됨")
                        + (f". 녹음 순음 프레임 {tf:.0f} %" if tf is not None else ""),
                "상대지점_동시각": f"±3 s 내 1초 LAeq 최대 {a_peak:.1f} dB @ {a_t} (지점1 중앙값 {med_a:.1f})",
                "비고": "확정 아님"})

    # 지점2 2회 사이렌: 녹음 스펙트로그램(figures/사이렌후보_스펙트로그램_2회.png)에서 확인한 지속 구간을 제외(참고값)
    b = S[(2, 2)]
    m = overlap_mask(b, T(SIREN_WIN[0]), T(SIREN_WIN[1]))
    excl[(2, 2)] |= m
    event_rows.append({"지점": 2, "회차": 2, "구분": "제외(녹음 확인 사이렌, 참고값)", "시작": SIREN_WIN[0], "끝": SIREN_WIN[1],
                       "길이_s": round(m.sum() * b.dt, 1), "구간_LAFmax": "",
                       "내용": "사이렌: 1 kHz 순음과 오르내리는 스윕(yelp/hi-lo) 패턴",
                       "근거": "야장 '16:31 사이렌' + WAV 스펙트로그램에서 두 지점 모두 같은 패턴(지점1은 약 1.6 s 늦게 기록)",
                       "상대지점_동시각": "지점1 녹음에도 같은 사이렌 패턴", "비고": "구간 경계는 눈으로 읽은 값(±2 s)"})

    # 특이사항 제외 LAeq (원래 값은 그대로 두고 병기)
    for row in metric_rows:
        p, r = row["지점"], row["회차"]
        s = S[(p, r)]
        n = row["샘플수"]
        m = excl.get((p, r), np.zeros(len(s.leq), bool))[:n]
        if m.any():
            row["LAeq_특이사항제외"] = r1(energy_mean(s.leq[:n][~m]))
            row["제외_길이_s"] = round(m.sum() * s.dt, 1)
            row["제외_근거"] = ("야장 기록 + 피크 대조" if p == 1 else
                               "녹음 확인 사이렌 구간 — 참고값" if r == 2 else "야장 힌트 + 녹음 순음 후보(경적) — 참고값")
        else:
            row["LAeq_특이사항제외"] = row["LAeq_재계산"]
            row["제외_길이_s"] = 0
            row["제외_근거"] = "제외 구간 없음"

    # 시계 차이 추정
    for r in (1, 2, 3):
        lag, rbest, r0 = clock_offset(S[(1, r)], S[(2, r)])
        clock_rows.append({"회차": r, "방법": "1초 LAeq 교차상관(±15 s, 0.1 s 간격)",
                           "추정_차이_s(지점1-지점2)": lag, "상관계수_최대": round(rbest, 3), "상관계수_차이0": round(r0, 3),
                           "비고": "양수면 지점2 시계가 지점1보다 늦음. 보정 미적용"})
    # 공통 피크 비교(2회 사이렌 후보, 3회 16:45 경적 후보)
    for r, lo, hi, what in [(2, "16:31:45", "16:32:02", "사이렌 후보"), (3, "16:45:20", "16:45:32", "경적 후보")]:
        a, b = S[(1, r)], S[(2, r)]
        ta, la = moving_1s(a)
        wa = (ta >= T(lo)) & (ta <= T(hi))
        wb = (b.t_end >= T(lo)) & (b.t_end <= T(hi))
        tpa = ta[wa][np.argmax(la[wa])]
        tpb = b.t_end[wb][np.argmax(b.leq[wb])]
        clock_rows.append({"회차": r, "방법": f"공통 피크 시각 비교({what}, {lo}~{hi})",
                           "추정_차이_s(지점1-지점2)": round((tpa - tpb) / np.timedelta64(1, "ms") / 1000, 1),
                           "상관계수_최대": "", "상관계수_차이0": "",
                           "비고": f"지점1 {fmt(tpa)} / 지점2 {fmt(tpb)} (1초 구간 끝 기준). 지점2 해상도 1 s"})

    fields = ["지점", "회차", "파일", "일련번호", "분석구간_시작", "분석구간_길이_s", "샘플수", "샘플간격_s"]
    for k in ["LAeq", "LAmax", "LAmin", "L5", "L10", "L50", "L90", "L95"]:
        fields += [f"{k}_기기", f"{k}_재계산", f"{k}_차이"]
    fields += ["LAeq_전체파일_재계산", "LAeq_특이사항제외", "제외_길이_s", "제외_근거", "비고"]
    write_csv(RES / "회차별_소음지표.csv", metric_rows, fields)
    write_csv(RES / "특이사항_구간.csv", event_rows,
              ["지점", "회차", "구분", "시작", "끝", "길이_s", "구간_LAFmax", "내용", "근거", "상대지점_동시각", "비고"])
    write_csv(RES / "시계차이_추정.csv", clock_rows,
              ["회차", "방법", "추정_차이_s(지점1-지점2)", "상관계수_최대", "상관계수_차이0", "비고"])

    # 지점별 3회 요약
    summ = []
    for p in (1, 2):
        rows = [x for x in metric_rows if x["지점"] == p]
        for key, label in [("LAeq_기기", "기기값"), ("LAeq_재계산", "재계산"), ("LAeq_특이사항제외", "특이사항 제외")]:
            v = np.array([x[key] for x in rows], float)
            summ.append({"지점": p, "기준": label, "1회": v[0], "2회": v[1], "3회": v[2],
                         "3회_에너지평균": r1(energy_mean(v)), "3회_산술평균": r1(v.mean()),
                         "최대-최소": round(v.max() - v.min(), 1), "표준편차(n-1)": round(v.std(ddof=1), 2)})
    write_csv(RES / "지점별_3회_요약.csv", summ,
              ["지점", "기준", "1회", "2회", "3회", "3회_에너지평균", "3회_산술평균", "최대-최소", "표준편차(n-1)"])

    diff = []
    for r in (1, 2, 3):
        a = next(x for x in metric_rows if x["지점"] == 1 and x["회차"] == r)
        b = next(x for x in metric_rows if x["지점"] == 2 and x["회차"] == r)
        diff.append({"회차": r, "지점1_LAeq": a["LAeq_재계산"], "지점2_LAeq": b["LAeq_재계산"],
                     "차이(지점1-지점2)": round(a["LAeq_재계산"] - b["LAeq_재계산"], 1),
                     "지점1_제외": a["LAeq_특이사항제외"], "지점2_제외": b["LAeq_특이사항제외"],
                     "차이_제외": round(a["LAeq_특이사항제외"] - b["LAeq_특이사항제외"], 1)})
    write_csv(RES / "지점간_LAeq_차이.csv", diff,
              ["회차", "지점1_LAeq", "지점2_LAeq", "차이(지점1-지점2)", "지점1_제외", "지점2_제외", "차이_제외"])

    # ---------------- 3단계: 교통량 ----------------
    trows = []
    q = {}
    for r in (1, 2, 3):
        tot = {"car": 0, "hgv": 0}
        for p in (1, 2):
            car, hgv = TRAFFIC[p]["counts"][r]
            tot["car"] += car
            tot["hgv"] += hgv
            q[(p, r)] = (car + hgv) * 6
            trows.append({"회차": r, "시각": "–".join(ROUNDS[r]), "구분": f"{TRAFFIC[p]['direction']} (지점{p} 조 계수)",
                          "승용차_10분": car, "화물차_10분": hgv, "합계_10분": car + hgv,
                          "Q_대/h": (car + hgv) * 6, "승용차_대/h": car * 6, "화물차_대/h": hgv * 6,
                          "p_%": round(100 * hgv / (car + hgv), 1)})
        q[("both", r)] = (tot["car"] + tot["hgv"]) * 6
        trows.append({"회차": r, "시각": "–".join(ROUNDS[r]), "구분": "양방향 합계",
                      "승용차_10분": tot["car"], "화물차_10분": tot["hgv"], "합계_10분": tot["car"] + tot["hgv"],
                      "Q_대/h": (tot["car"] + tot["hgv"]) * 6, "승용차_대/h": tot["car"] * 6,
                      "화물차_대/h": tot["hgv"] * 6, "p_%": round(100 * tot["hgv"] / (tot["car"] + tot["hgv"]), 1)})
    for label in [f"{TRAFFIC[1]['direction']} (지점1 조 계수)", f"{TRAFFIC[2]['direction']} (지점2 조 계수)", "양방향 합계"]:
        rows = [x for x in trows if x["구분"] == label]
        car = sum(x["승용차_10분"] for x in rows) / 3
        hgv = sum(x["화물차_10분"] for x in rows) / 3
        trows.append({"회차": "평균", "시각": "3회 평균", "구분": label, "승용차_10분": round(car, 1),
                      "화물차_10분": round(hgv, 1), "합계_10분": round(car + hgv, 1), "Q_대/h": round((car + hgv) * 6, 1),
                      "승용차_대/h": round(car * 6, 1), "화물차_대/h": round(hgv * 6, 1),
                      "p_%": round(100 * hgv / (car + hgv), 1)})
    tf = ["회차", "시각", "구분", "승용차_10분", "화물차_10분", "합계_10분", "Q_대/h", "승용차_대/h", "화물차_대/h", "p_%"]
    write_csv(RES / "교통량_정리.csv", trows, tf)

    # 교통량-LAeq 참고 회귀(n=3, 일반화 금지)
    reg = []
    for p in (1, 2):
        for qkey, qlabel in [("both", "양방향 Q"), (p, "가까운 방향 Q")]:
            x = np.log10([q[(qkey, r)] for r in (1, 2, 3)])
            y = np.array([next(m["LAeq_재계산"] for m in metric_rows if m["지점"] == p and m["회차"] == r) for r in (1, 2, 3)])
            b1, b0 = np.polyfit(x, y, 1)
            reg.append({"지점": p, "설명변수": qlabel, "식": f"LAeq = {b0:.1f} + {b1:.1f}·log10(Q)", "n": 3,
                        "비고": "참고용. 표본 3개로 일반화하지 말 것"})
    write_csv(RES / "교통량_LAeq_참고회귀.csv", reg, ["지점", "설명변수", "식", "n", "비고"])

    # ---------------- 그림 ----------------
    plt.rcParams.update({"font.family": "AppleGothic", "axes.unicode_minus": False, "font.size": 9,
                         "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
                         "grid.color": "#e4e4e4", "grid.linewidth": 0.6})
    hm = mdates.DateFormatter("%H:%M")
    for r in (1, 2, 3):
        a, b = S[(1, r)], S[(2, r)]
        ta1, la1 = to_1s(a)
        fig, ax = plt.subplots(figsize=(11, 4.2))
        half = np.timedelta64(500, "ms")
        ax.plot(ta1 - half, la1, color=COLOR[1], lw=1.0, label=f"{POINTS[1]} · 100 ms 자료를 1초 LAeq로 묶음")
        ax.plot(b.t_end - half, b.leq, color=COLOR[2], lw=1.0, label=f"{POINTS[2]} · 1초 LAeq")
        k = 0
        for e in event_rows:
            if e["회차"] != r:
                continue
            lo, hi = T(e["시작"]), T(e["끝"])
            if e["지점"] == 1 and e["구분"].startswith("제외"):
                ax.axvspan(lo, hi, color=COLOR[1], alpha=0.25, lw=0)
                ax.annotate(f"지점1 오토바이(피크 대조 {e['시작'][:8]})", (lo, 88), fontsize=7.5, color="#333", ha="center")
            elif e["지점"] == 1:
                ax.axvspan(lo, hi, facecolor="none", edgecolor=COLOR[1], hatch="///", lw=0.6)
                ax.annotate("지점1 참고 피크", (lo, 86), fontsize=7.5, color="#333", ha="center")
            elif e["구분"].startswith("제외"):
                ax.axvspan(lo, hi, color=COLOR[2], alpha=0.18, lw=0)
                ax.annotate("지점2 사이렌(녹음 확인)", (lo, 88), fontsize=7.5, color="#333", ha="left")
            else:
                k += 1
                ax.axvspan(lo, hi, facecolor="none", edgecolor=COLOR[2], hatch="\\\\\\", lw=0.6)
                ax.annotate(f"후보{k}", (lo, 86 - 1.8 * (k % 2)), fontsize=7.5, color="#333", ha="center")
        la_dev = next(m for m in metric_rows if m["지점"] == 1 and m["회차"] == r)["LAeq_재계산"]
        lb_dev = next(m for m in metric_rows if m["지점"] == 2 and m["회차"] == r)["LAeq_재계산"]
        ax.set_title(f"{r}회 ({ROUNDS[r][0]}–{ROUNDS[r][1]}) 1초 LAeq 시계열 — LAeq,10min 지점1 {la_dev} dB, 지점2 {lb_dev} dB",
                     loc="left", fontsize=10)
        ax.set_ylabel("LAeq,1s (dB)")
        ax.set_ylim(50, 90)
        ax.xaxis.set_major_formatter(hm)
        ax.legend(loc="lower left", fontsize=8, frameon=True, facecolor="white", edgecolor="none", framealpha=0.9, ncol=2)
        fig.text(0.99, 0.01, f"{AI_NOTE} · 각 기기 내부 시계 그대로(시계 보정 미적용, 지점2가 약 1.6 s 늦음) · "
                 "주황 빗금: 지점2 자동 탐지 후보(확정 아님, 번호는 특이사항_구간.csv 순서)",
                 ha="right", fontsize=7, color="#666")
        fig.tight_layout(rect=(0, 0.03, 1, 1))
        fig.savefig(FIG / f"시계열_{r}회_두지점.png", dpi=150)
        plt.close(fig)

    # 지점별 비교
    # 막대 대신 점 그림(세로축을 0에서 시작하지 않으므로 막대는 쓰지 않음)
    fig, ax = plt.subplots(figsize=(7, 4))
    for i, p in enumerate((1, 2)):
        v = [next(m["LAeq_재계산"] for m in metric_rows if m["지점"] == p and m["회차"] == r) for r in (1, 2, 3)]
        vx = [next(m["LAeq_특이사항제외"] for m in metric_rows if m["지점"] == p and m["회차"] == r) for r in (1, 2, 3)]
        x = np.arange(3) + (i - 0.5) * 0.22
        ax.plot(x, v, "o", ms=8, color=COLOR[p], mec="white", mew=1.5, zorder=3, label=f"{POINTS[p]} LAeq")
        ax.plot(x, vx, "o", ms=8, mfc="none", mec=COLOR[p], mew=1.5, zorder=2,
                label=f"지점{p} 특이사항 제외" + (" (지점2는 후보 제외 참고값)" if p == 2 else ""))
        for xi, vi in zip(x, v):
            ax.annotate(f"{vi:.1f}", (xi, vi), textcoords="offset points", xytext=(0, 8), ha="center", fontsize=8)
        em = energy_mean(v)
        ax.axhline(em, color=COLOR[p], lw=1, ls="--")
        ax.annotate(f"지점{p} 3회 에너지 평균 {em:.1f}", (2.45, em), fontsize=7.5, color="#333", va="bottom")
    ax.set_xticks(range(3), [f"{r}회\n{ROUNDS[r][0]}–{ROUNDS[r][1]}" for r in (1, 2, 3)])
    ax.set_xlim(-0.5, 3.3)
    ax.set_ylim(67, 70.5)
    ax.set_ylabel("LAeq,10min (dB)")
    ax.set_title("회차별 LAeq,10min (속 빈 점: 특이사항 제외값, 원래 값을 대체하지 않음)", loc="left", fontsize=10)
    ax.legend(fontsize=7.5, frameon=False, loc="upper left")
    fig.text(0.99, 0.01, AI_NOTE, ha="right", fontsize=7, color="#666")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(FIG / "지점별_LAeq_비교.png", dpi=150)
    plt.close(fig)

    # 교통량-LAeq 산점도 (한 축, 두 패널)
    fig, axes = plt.subplots(1, 2, figsize=(10, 4), sharey=True)
    for ax, mode in zip(axes, ("both", "near")):
        for p in (1, 2):
            xs = [q[("both" if mode == "both" else p, r)] for r in (1, 2, 3)]
            ys = [next(m["LAeq_재계산"] for m in metric_rows if m["지점"] == p and m["회차"] == r) for r in (1, 2, 3)]
            ax.scatter(xs, ys, s=40, color=COLOR[p], edgecolor="white", linewidth=1.5, zorder=3, label=POINTS[p])
            for xi, yi, r in zip(xs, ys, (1, 2, 3)):
                ax.annotate(f"{r}회", (xi, yi), textcoords="offset points", xytext=(5, 4), fontsize=7.5, color="#333")
            b1, b0 = np.polyfit(np.log10(xs), ys, 1)
            xx = np.linspace(min(xs), max(xs), 20)
            ax.plot(xx, b0 + b1 * np.log10(xx), color=COLOR[p], lw=1, ls=":")
        ax.set_xlabel("양방향 시간교통량 Q (대/h)" if mode == "both" else "가까운 방향 시간교통량 Q (대/h, 각 조 계수값)")
        ax.set_title("양방향 Q 대비" if mode == "both" else "가까운 방향 Q 대비", loc="left", fontsize=10)
    axes[0].set_ylabel("LAeq,10min (dB)")
    fig.legend(*axes[0].get_legend_handles_labels(), fontsize=8, frameon=False, loc="upper right", ncol=2)
    fig.suptitle("교통량–LAeq (점선: n=3 참고 회귀, 일반화 금지)", x=0.01, ha="left", fontsize=10)
    fig.text(0.99, 0.01, f"{AI_NOTE} · 속도 미측정", ha="right", fontsize=7, color="#666")
    fig.tight_layout(rect=(0, 0.03, 1, 0.95))
    fig.savefig(FIG / "교통량_LAeq_산점도.png", dpi=150)
    plt.close(fig)

    # ---------------- 4단계: 모델 검증용 입력 ----------------
    mv = []

    def add(group, item, value, unit, source, note=""):
        mv.append({"구분": group, "항목": item, "값": value, "단위": unit, "출처": source, "비고": note})

    gps_warn = "GPS ±9~18 m이고 좌표가 소수 셋째 자리(약 ±50 m)로 반올림됨 → 모델에는 쓰지 않고 수치지도 기반 좌표(아래)를 씀"
    mp = {}
    if (BASE / "model" / "points.csv").exists():
        with open(BASE / "model" / "points.csv", encoding="utf-8") as f:
            mp = {int(r["point_id"][1]): r for r in csv.DictReader(f)}
    recv = {1: dict(name="사가정로 123 앞(도로 북측)", lat=37.577, lon=127.058, alt=27.9, acc=18, edge=4.213, h=1.309,
                    osm=(37.5779, 127.0586), bld=1.001),
            2: dict(name="전농동SK아파트상가입구(도로 남측, 사가정로 148)", lat=37.578, lon=127.059, alt=33.7, acc=9,
                    edge=3.321, h=1.312, osm=(37.5775, 127.0589), bld=None)}
    for p, v in recv.items():
        g = f"수음점{p}"
        add(g, "위치명", v["name"], "", "현장기록", "도로 남·북측은 도로명주소 번지(홀수 북측·짝수 남측)와 OSM으로 판정")
        add(g, "위도(GPS)", v["lat"], "deg", "실측", gps_warn)
        add(g, "경도(GPS)", v["lon"], "deg", "실측", gps_warn)
        add(g, "GPS 정확도", f"±{v['acc']}", "m", "실측", "")
        add(g, "참고 위도(주소 기반)", v["osm"][0], "deg", "외부자료", "OpenStreetMap 주소점 사이 보간. 검증 안 됨")
        add(g, "참고 경도(주소 기반)", v["osm"][1], "deg", "외부자료", "OpenStreetMap 주소점 사이 보간. 검증 안 됨")
        add(g, "GPS 고도", v["alt"], "m", "실측", "스마트폰 GPS 고도. 지형 고도로 쓰지 말 것")
        add(g, "마이크 높이(지면 위)", v["h"], "m", "실측", "")
        add(g, "도로끝선(연석)까지 거리", v["edge"], "m", "실측", "")
        if v["bld"]:
            add(g, "가장 가까운 건물 경계까지", v["bld"], "m", "실측", "야장 기재. 반사음 영향 가능")
        if p in mp:
            r = mp[p]
            src = "수치지도 사가정로 중심선·보도 경계(A0033320) + 실측 도로끝선 거리. scripts/model_compare.py"
            add(g, "모델 좌표 x (EPSG:5186)", r["x"], "m", "외부자료", src)
            add(g, "모델 좌표 y (EPSG:5186)", r["y"], "m", "외부자료", src)
            add(g, "모델 좌표 위도·경도", f'{r["lat"]}, {r["lon"]}', "deg", "외부자료", src)
            add(g, "중심선→도로끝선 / 중심선→수음점", f'{r["curb_from_centerline_m"]} / {r["receiver_from_centerline_m"]}', "m",
                "외부자료", "도로끝선 = 수치지도 보도 안쪽 경계")
            add(g, "수음점→건물 벽(수치지도)", r["building_from_receiver_m"], "m", "외부자료",
                "야장 값과 비교용(지점1 1.001 m 기재)")
    for p in (1, 2):
        g = f"교통_{TRAFFIC[p]['direction']}"
        for r in (1, 2, 3):
            car, hgv = TRAFFIC[p]["counts"][r]
            add(g, f"{r}회 Q", (car + hgv) * 6, "대/h", "현장기록", f"10분 계수 {car + hgv}대 × 6")
            add(g, f"{r}회 p(화물차 비율)", round(100 * hgv / (car + hgv), 1), "%", "현장기록", "")
        rows = [x for x in trows if x["회차"] == "평균" and x["구분"].startswith(TRAFFIC[p]["direction"])][0]
        add(g, "3회 평균 Q", rows["Q_대/h"], "대/h", "현장기록", "")
        add(g, "3회 평균 p", rows["p_%"], "%", "현장기록", "")
        add(g, "차종 구분", "Pkw / Lkw1 = 화물차 전부(이륜차 포함) / Lkw2 = 0", "", "가정",
            "현장 화물차 계수에 이륜차 포함(사용자 확인). 이륜차 대수 미기록이라 전부 Lkw1, Lkw2 = 0으로 가정. RLS-19는 이륜차를 Lkw2로 다룸(상한 +1.6 dB)")
        add(g, "계수 차로", "지점 쪽 가까운 차로 방향", "", "현장기록", "방향은 지도 기준으로 확정(사용자 지시 2026-10-23). 원시자료 폴더명의 상행/하행 표기는 반대로 붙어 있음")
    rows = [x for x in trows if x["회차"] == "평균" and x["구분"] == "양방향 합계"][0]
    add("교통_양방향", "3회 평균 Q", rows["Q_대/h"], "대/h", "현장기록", "")
    add("교통_양방향", "3회 평균 p", rows["p_%"], "%", "현장기록", "")
    add("교통_공통", "차량 주행속도", "미측정", "", "현장기록", "")
    add("교통_공통", "모델 사용 속도 = 규정(최고) 속도", 50, "km/h", "외부자료",
        "사가정로 제한속도 50 km/h(OSM maxspeed=50, 서울 안전속도 5030). 승용차·화물차 모두 적용. 실제 주행속도 아님(최고속도 가정)")
    add("교통_공통", "신호 교차로", "전농사거리 중심에서 지점1 약 88 m, 지점2 약 100 m", "", "외부자료", "수치지도 도로중심선 거리. RLS-19 교차로 보정(120 m 이내) 대상")
    add("기상", "풍속", 1.8, "m/s", "외부자료", "인근 기상정보(네이버 지도 날씨), 현장 미실측")
    add("기상", "기온", 15, "°C", "외부자료", "인근 기상정보(네이버 지도 날씨), 현장 미실측")
    add("기상", "습도", "미기록", "%", "외부자료", "")
    add("기상", "풍향·노면 상태", "미기록", "", "현장기록", "")
    for p in (1, 2):
        for r in (1, 2, 3):
            m = next(x for x in metric_rows if x["지점"] == p and x["회차"] == r)
            add(f"측정결과_지점{p}", f"{r}회 LAeq,10min", m["LAeq_재계산"], "dB(A)", "실측",
                f"기기값 {m['LAeq_기기']}. 특이사항 제외 {m['LAeq_특이사항제외']}")
        s = next(x for x in summ if x["지점"] == p and x["기준"] == "재계산")
        add(f"측정결과_지점{p}", "3회 에너지 평균 LAeq", s["3회_에너지평균"], "dB(A)", "실측", f"산술평균 {s['3회_산술평균']}")
    add("측정조건", "교정", "양 지점 모두 실시(94 dB, 1 kHz 교정기)", "", "현장기록",
        "지점2 야장: 측정 전 93.7, 측정 후 94 dB. 지점1 수치는 미기록")
    add("측정조건", "방풍망", "양 지점 장착", "", "현장기록", "기기 WS-10 보정 설정과 일치")
    add("측정조건", "두 소음계 시계 차이", "약 1.6 s(지점2가 늦음, 추정)", "s", "실측", "1초 LAeq 교차상관 1.4~1.7 s, 사이렌 녹음 교차상관 1.59 s. 보정 미적용. 10분 LAeq 영향은 무시 가능")
    write_csv(MV / "모델검증_입력자료.csv", mv, ["구분", "항목", "값", "단위", "출처", "비고"])

    print("완료:", RES, FIG, MV)


if __name__ == "__main__":
    main()
