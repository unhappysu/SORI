"""특이사항 후보 구간의 WAV 녹음 스펙트로그램 확인(사이렌·경적·오토바이 판별 보조).

AI(Claude)를 활용하여 제작하였음.

실행: python3 field_measurement/2026-10-22/scripts/wav_check.py   (analyze.py 다음에 실행)
- 입력: results/특이사항_구간.csv, raw_copy/*/SOUND/*.wav (48 kHz, 16 bit)
- 출력: figures/특이사항_후보_스펙트로그램.png, results/특이사항_후보_음색지표.csv
- 판별은 하지 않는다. 순음성 지표와 그림만 제시하고, 확정은 사람이 듣고 판단한다.

WAV 시작 시각 = AUTO_LP 첫 샘플 구간의 시작(첫 타임스탬프 − 샘플 간격)으로 본다(가정).
WAV 길이가 측정 시간과 0.1 s 이내로 같아 녹음과 측정이 함께 시작된 것으로 판단.

순음성 지표: 0.25 s 프레임 스펙트럼(300~3000 Hz)에서 최대 성분이 주변(±1/3 옥타브) 중앙값보다 몇 dB 높은지.
사이렌은 주파수가 시간에 따라 크게 오르내리고(주파수 범위가 큼), 경적은 같은 주파수에 머문다.
"""
import csv
import wave
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy import signal

from nl62 import STORES, load_store

BASE = Path(__file__).resolve().parents[1]
AI_NOTE = "AI(Claude)를 활용하여 제작하였음"
DAY = "2026-10-22"
PAD = 3.0


def wav_segment(point, rnd, t0, t1):
    s = load_store(point, rnd)
    start = s.t_end[0] - np.timedelta64(int(s.dt * 1000), "ms")
    wav = next((BASE / "raw_copy" / STORES[point]["folder"] / f"Auto_{STORES[point]['rounds'][rnd]}" / "SOUND").glob("*.wav"))
    with wave.open(str(wav)) as w:
        fs = w.getframerate()
        a = max(0.0, (t0 - start) / np.timedelta64(1, "ms") / 1000)
        b = (t1 - start) / np.timedelta64(1, "ms") / 1000
        w.setpos(int(a * fs))
        x = np.frombuffer(w.readframes(int((b - a) * fs)), dtype="<i2").astype(float) / 32768
    return fs, x, a


def tonality(fs, x):
    f, t, S = signal.stft(x, fs, nperseg=int(0.25 * fs), noverlap=int(0.125 * fs))
    P = 20 * np.log10(np.abs(S) + 1e-12)
    band = (f >= 300) & (f <= 3000)
    fb, Pb = f[band], P[band]
    prom, fpk = [], []
    for j in range(Pb.shape[1]):
        k = int(np.argmax(Pb[:, j]))
        lo, hi = fb[k] / 2 ** (1 / 6), fb[k] * 2 ** (1 / 6)
        nb = (fb >= lo) & (fb <= hi)
        prom.append(Pb[k, j] - np.median(Pb[nb, j]))
        fpk.append(fb[k])
    prom, fpk = np.array(prom), np.array(fpk)
    tonal = prom >= 15
    return dict(prom_max=prom.max(), tonal_frac=tonal.mean(),
                f_tonal_min=fpk[tonal].min() if tonal.any() else np.nan,
                f_tonal_max=fpk[tonal].max() if tonal.any() else np.nan,
                f_tonal_med=np.median(fpk[tonal]) if tonal.any() else np.nan)


def main():
    with open(BASE / "results" / "특이사항_구간.csv", encoding="utf-8-sig") as f:
        rows = [r for r in csv.DictReader(line for line in f if not line.startswith("#"))]
    cand = [r for r in rows if r["구분"].startswith(("후보", "제외"))]
    n = len(cand)
    ncol = 3
    nrow = int(np.ceil(n / ncol))
    plt.rcParams.update({"font.family": "AppleGothic", "axes.unicode_minus": False})
    fig, axes = plt.subplots(nrow, ncol, figsize=(12, 2.7 * nrow), squeeze=False)
    out = []
    for ax, r in zip(axes.flat, cand):
        p, rnd = int(r["지점"]), int(r["회차"])
        lo = np.datetime64(f"{DAY}T{r['시작']}", "ms")
        hi = np.datetime64(f"{DAY}T{r['끝']}", "ms")
        fs, x, a = wav_segment(p, rnd, lo - np.timedelta64(int(PAD * 1000), "ms"), hi + np.timedelta64(int(PAD * 1000), "ms"))
        f, t, S = signal.spectrogram(x, fs, nperseg=4096, noverlap=3072)
        Sdb = 10 * np.log10(S + 1e-14)
        keep = f <= 5000
        ax.pcolormesh(t - PAD, f[keep], Sdb[keep], shading="auto", cmap="magma",
                      vmin=np.percentile(Sdb[keep], 40), vmax=np.percentile(Sdb[keep], 99.7))
        ax.axvline(0, color="white", lw=0.6, ls="--")
        ax.axvline((hi - lo) / np.timedelta64(1, "ms") / 1000, color="white", lw=0.6, ls="--")
        ti = tonality(fs, x[int(PAD * fs): len(x) - int(PAD * fs)] if len(x) > 2 * PAD * fs else x)
        ax.set_title(f"지점{p} {rnd}회 {r['시작'][:8]} ({r['구분']})\n순음 돌출 최대 {ti['prom_max']:.0f} dB, "
                     f"순음 프레임 {100 * ti['tonal_frac']:.0f} %, 주파수 {ti['f_tonal_min']:.0f}–{ti['f_tonal_max']:.0f} Hz",
                     fontsize=7.5, loc="left")
        ax.set_ylabel("Hz", fontsize=7)
        ax.tick_params(labelsize=7)
        out.append({"지점": p, "회차": rnd, "시작": r["시작"], "끝": r["끝"], "구분": r["구분"],
                    "순음돌출_최대_dB": round(float(ti["prom_max"]), 1), "순음프레임_비율_%": int(round(100 * ti["tonal_frac"])),
                    "순음주파수_최소_Hz": round(ti["f_tonal_min"], 0) if ti["f_tonal_min"] == ti["f_tonal_min"] else "",
                    "순음주파수_최대_Hz": round(ti["f_tonal_max"], 0) if ti["f_tonal_max"] == ti["f_tonal_max"] else "",
                    "순음주파수_중앙_Hz": round(ti["f_tonal_med"], 0) if ti["f_tonal_med"] == ti["f_tonal_med"] else "",
                    "비고": "판별 보조 지표. 확정 아님"})
    for ax in list(axes.flat)[n:]:
        ax.axis("off")
    for ax in axes[-1]:
        ax.set_xlabel("후보 구간 시작 기준 시간 (s), 점선 = 후보 구간", fontsize=7)
    fig.suptitle("특이사항 후보 구간 WAV 스펙트로그램(0~5 kHz) — 사이렌: 오르내리는 순음, 경적: 고정 순음+배음, 차량: 광대역",
                 x=0.01, ha="left", fontsize=10)
    fig.text(0.99, 0.005, f"{AI_NOTE} · 순음 프레임 = 0.25 s 프레임 중 순음 돌출 15 dB 이상", ha="right", fontsize=7, color="#666")
    fig.tight_layout(rect=(0, 0.015, 1, 0.97))
    fig.savefig(BASE / "figures" / "특이사항_후보_스펙트로그램.png", dpi=130)
    with open(BASE / "results" / "특이사항_후보_음색지표.csv", "w", encoding="utf-8-sig", newline="") as f:
        f.write(f"# {AI_NOTE}\n")
        w = csv.DictWriter(f, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)
    for o in out:
        print(o["지점"], o["회차"], o["시작"], o["순음돌출_최대_dB"], o["순음프레임_비율_%"], o["순음주파수_최소_Hz"], o["순음주파수_최대_Hz"])


def siren_figure(lo="16:31:30", hi="16:32:30", rnd=2):
    """사이렌 후보 시간대의 두 지점 스펙트로그램(같은 시간축, 각 기기 시계 그대로)."""
    plt.rcParams.update({"font.family": "AppleGothic", "axes.unicode_minus": False})
    fig, axes = plt.subplots(2, 1, figsize=(11, 6), sharex=True)
    t0 = np.datetime64(f"{DAY}T{lo}", "ms")
    t1 = np.datetime64(f"{DAY}T{hi}", "ms")
    for ax, p in zip(axes, (1, 2)):
        fs, x, a = wav_segment(p, rnd, t0, t1)
        f, t, S = signal.spectrogram(x, fs, nperseg=4096, noverlap=3072)
        Sdb = 10 * np.log10(S + 1e-14)
        keep = f <= 3000
        ax.pcolormesh(t, f[keep], Sdb[keep], shading="auto", cmap="magma",
                      vmin=np.percentile(Sdb[keep], 40), vmax=np.percentile(Sdb[keep], 99.7))
        ax.set_ylabel("Hz")
        ax.set_title(f"지점{p} ({'북측' if p == 1 else '남측'}) — {lo} 기준 경과 시간, 기기 시계 그대로(지점2가 약 1.6 s 늦음)",
                     loc="left", fontsize=9)
    axes[-1].set_xlabel(f"{lo} 이후 시간 (s)")
    fig.suptitle(f"{rnd}회 사이렌 후보 시간대 스펙트로그램(0~3 kHz)", x=0.01, ha="left", fontsize=10)
    fig.text(0.99, 0.005, AI_NOTE, ha="right", fontsize=7, color="#666")
    fig.tight_layout(rect=(0, 0.02, 1, 0.96))
    fig.savefig(BASE / "figures" / "사이렌후보_스펙트로그램_2회.png", dpi=130)
    plt.close(fig)


SIREN = ("16:31:40", "16:32:10")   # 지점2 기기 시각. 스펙트로그램에서 1 kHz 순음·스윕이 보이는 범위(눈으로 읽음)


def siren_offset_and_leq(rnd=2):
    """사이렌 대역(600~1700 Hz) 포락선 교차상관으로 시계 차이를 정밀 추정하고, 사이렌 구간 제외 LAeq를 계산."""
    from nl62 import energy_mean
    lo = np.datetime64(f"{DAY}T16:31:35", "ms")
    hi = np.datetime64(f"{DAY}T16:32:10", "ms")
    env = {}
    for p in (1, 2):
        fs, x, a = wav_segment(p, rnd, lo, hi)
        sos = signal.butter(4, [600, 1700], "bandpass", fs=fs, output="sos")
        e = signal.resample_poly(signal.sosfiltfilt(sos, x) ** 2, 1, fs // 100)   # 100 Hz 포락선
        e = np.log(np.clip(e, 1e-14, None))
        env[p] = e - e.mean()
    c = signal.correlate(env[1], env[2])
    lags = signal.correlation_lags(len(env[1]), len(env[2])) / 100
    m = np.abs(lags) < 5
    k = int(np.argmax(c[m]))
    lag = float(lags[m][k])
    r = float(c[m][k] / np.sqrt((env[1] ** 2).sum() * (env[2] ** 2).sum()))
    rows = [{"방법": "사이렌 대역(600–1700 Hz) 포락선 교차상관, WAV 100 Hz", "구간(지점2 시각)": "16:31:35–16:32:10",
             "추정_차이_s(지점1-지점2)": round(lag, 2), "정규화_상관": round(r, 3),
             "비고": "양수 = 지점2 시계가 늦음. WAV 시작 = AUTO_LP 첫 구간 시작으로 가정. 보정 미적용"}]
    # 사이렌 구간 제외 LAeq(참고값). 지점1 구간은 추정 시계 차이만큼 옮긴다.
    for p in (1, 2):
        s = load_store(p, rnd)
        n = int(round(s.device_blocks[0]["dur_s"] / s.dt))
        sh = np.timedelta64(int(round(lag * 1000)), "ms") if p == 1 else np.timedelta64(0, "ms")
        a0 = np.datetime64(f"{DAY}T{SIREN[0]}", "ms") + sh
        a1 = np.datetime64(f"{DAY}T{SIREN[1]}", "ms") + sh
        dt = np.timedelta64(int(s.dt * 1000), "ms")
        msk = (s.t_end[:n] > a0) & (s.t_end[:n] - dt < a1)
        rows.append({"방법": f"지점{p} {rnd}회 사이렌 구간 제외 LAeq(참고값)",
                     "구간(지점2 시각)": f"{SIREN[0]}–{SIREN[1]}" + (f" (지점1 시각 +{lag:.2f} s)" if p == 1 else ""),
                     "추정_차이_s(지점1-지점2)": "", "정규화_상관": "",
                     "비고": f"전체 {energy_mean(s.leq[:n]):.2f} → 제외 {energy_mean(s.leq[:n][~msk]):.2f} dB, 제외 {msk.sum() * s.dt:.1f} s"})
    with open(BASE / "results" / "사이렌_시계차이_제외LAeq.csv", "w", encoding="utf-8-sig", newline="") as f:
        f.write(f"# {AI_NOTE}\n")
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    for r_ in rows:
        print(r_)


if __name__ == "__main__":
    main()
    siren_figure()
    siren_offset_and_leq()
