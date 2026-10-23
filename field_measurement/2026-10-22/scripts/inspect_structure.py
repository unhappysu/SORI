"""1단계: RION NL-62 Auto Store 원시 파일 구조 점검 (분석값 산출 아님).

AI(Claude)를 활용하여 제작하였음.
raw_copy/ 아래 복사본만 읽는다. 원본은 건드리지 않는다.
"""
import csv
import sys
import wave
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "raw_copy"


def read_rnh(path):
    """rnh/rwh 헤더를 {키: 값} 딕셔너리로 읽는다."""
    out = {}
    for line in path.read_text(encoding="ascii").splitlines():
        parts = line.split(",")
        if len(parts) >= 2 and parts[0]:
            out[parts[0].strip()] = parts[1].strip()
    return out


def read_lp(path):
    """AUTO_LP 시계열을 (헤더, 행 목록)으로 읽는다."""
    with path.open(encoding="ascii", newline="") as f:
        rows = list(csv.reader(f))
    header = [h.strip() for h in rows[1]]
    data = [[c.strip() for c in r] for r in rows[2:] if r and r[0].strip()]
    return header, data


def main():
    for store in sorted(ROOT.glob("*/Auto_*")):
        h = read_rnh(next(store.glob("*.rnh")))
        lp_path = next((store / "AUTO_LP").glob("*.rnd"))
        header, data = read_lp(lp_path)
        idx = {name: i for i, name in enumerate(header)}
        times = [datetime.strptime(r[idx["Start Time"]], "%Y/%m/%d %H:%M:%S.%f") for r in data]
        dts = Counter(round((b - a).total_seconds(), 3) for a, b in zip(times, times[1:]))
        main_vals = [r[idx["Main"]] for r in data]
        missing = sum(v == "-.-" for v in main_vals)
        nums = [float(v) for v in main_vals if v != "-.-"]
        flags = {k: sum(r[idx[k]].strip("-") != "" for r in data)
                 for k in ("Over", "Under", "Output Over", "Marker 1", "Marker 2")}
        wav = next((store / "SOUND").glob("*.wav"))
        with wave.open(str(wav)) as w:
            wav_sec = w.getnframes() / w.getframerate()
        print(f"== {store.parent.name}/{store.name}  SN {h['Serial Number']}")
        print(f"   설정: {h['Frequency Weighting']}/{h['Time Weighting']}, Lp 저장 {h['Lp Store Interval']}, "
              f"LN Mode {h['LN Mode']}, 밴드가중 {h['Frequency Weighting (Band)']}, Lmax Type {h['Lmax Type']}, "
              f"Sub {h['Display Sub Channel']}, 방풍망보정 {h['Windscreen Correction']}")
        print(f"   rnh: {h['Measurement Start Time']} ~ {h['Measurement Stop Time']} ({h['Measure Time']}), "
              f"Lp {h['Lp Data Number']}개, Leq {h['Leq Data Number']}개")
        print(f"   Lp열: {[c for c in header[2:6]]}... 총 {len(header)}열")
        print(f"   Lp 행 {len(data)}개, {times[0].time()} ~ {times[-1].time()}, 간격분포 {dict(dts)}")
        print(f"   Main 결측 {missing}, 범위 {min(nums):.1f}~{max(nums):.1f} dB, 플래그 {flags}")
        print(f"   WAV {wav.name}: {wav_sec:.1f} s")


if __name__ == "__main__":
    sys.exit(main())
