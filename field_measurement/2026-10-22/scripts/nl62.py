"""RION NL-62 Auto Store(.rnh / AUTO_LP / AUTO_LEQ) 읽기 모듈.

AI(Claude)를 활용하여 제작하였음.
1단계에서 확인한 구조에 맞춘 파서다. 두 소음계의 저장 방식이 달라서
(511765: 100 ms, Main=LAF 순시값 + Leq/Lmax/Lmin 열 / 511778: 1 s Leq, Main=LAeq,1s)
각 Store를 같은 형태의 시계열로 바꿔 돌려준다.
"""
import csv
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

RAW = Path(__file__).resolve().parents[1] / "raw_copy"

# 지점 ↔ 폴더 ↔ 파일 대응 (1단계에서 확인)
STORES = {
    1: {"folder": "하행_임양록", "rounds": {1: "0306", 2: "0307", 3: "0308"}},
    2: {"folder": "상행_임영택", "rounds": {1: "0303", 2: "0304", 3: "0305"}},
}

TFMT = "%Y/%m/%d %H:%M:%S"


def _num(s):
    s = s.strip()
    return np.nan if s in ("", "-.-") else float(s)


def read_header(path):
    out = {}
    for line in Path(path).read_text(encoding="ascii").splitlines():
        parts = line.split(",")
        if len(parts) >= 2 and parts[0]:
            out[parts[0].strip()] = parts[1].strip()
    return out


@dataclass
class Store:
    point: int
    rnd: int
    name: str
    serial: str
    header: dict
    t_end: np.ndarray       # 각 샘플 구간의 끝 시각(파일 타임스탬프, datetime64[ms])
    dt: float               # 샘플 간격 s
    leq: np.ndarray         # 샘플 구간 LAeq (dB)
    lmax: np.ndarray        # 샘플 구간 LAFmax (없으면 nan)
    lmin: np.ndarray        # 샘플 구간 LAFmin (없으면 nan)
    device_blocks: list     # AUTO_LEQ 블록 [{start, dur_s, Leq, Lmax, Lmin, L5, L10, L50, L90, L95}]


def _read_device_leq(path):
    blocks, cur, use_col = [], None, 1
    with open(path, encoding="ascii", newline="") as f:
        for row in csv.reader(f):
            if not row:
                continue
            key = row[0].strip()
            if key == "Address":
                cur = {}
                blocks.append(cur)
            elif key == "Start Time":
                cur["start"] = datetime.strptime(row[1].strip(), TFMT)
            elif key == "Measurement Time":
                d, hms = row[1].strip().split("d ")
                h, m, s = hms.split(":")
                cur["dur_s"] = int(d) * 86400 + int(h) * 3600 + int(m) * 60 + float(s)
            elif key == "":
                # 열 이름 행: Sub 채널이 있으면 Main이 한 칸 뒤
                use_col = 2 if row[1].strip() == "Sub" else 1
            elif key in ("Leq", "Lmax", "Lmin", "LN1", "LN2", "LN3", "LN4", "LN5"):
                name = {"LN1": "L5", "LN2": "L10", "LN3": "L50", "LN4": "L90", "LN5": "L95"}.get(key, key)
                cur[name] = _num(row[use_col])
    return blocks


def load_store(point, rnd):
    info = STORES[point]
    name = info["rounds"][rnd]
    d = RAW / info["folder"] / f"Auto_{name}"
    h = read_header(d / f"Auto_{name}.rnh")
    lp = next((d / "AUTO_LP").glob("*.rnd"))
    with open(lp, encoding="ascii", newline="") as f:
        rows = list(csv.reader(f))
    cols = [c.strip() for c in rows[1]]
    data = [r for r in rows[2:] if r and r[0].strip()]
    t = np.array([np.datetime64(datetime.strptime(r[cols.index("Start Time")].strip(), TFMT + ".%f"), "ms")
                  for r in data])
    interval = h["Lp Store Interval"]
    if interval == "100 ms":
        dt = 0.1
        leq = np.array([_num(r[cols.index("Leq")]) for r in data])
        lmax = np.array([_num(r[cols.index("Lmax")]) for r in data])
        lmin = np.array([_num(r[cols.index("Lmin")]) for r in data])
    elif interval == "Leq_1s":
        dt = 1.0
        leq = np.array([_num(r[cols.index("Main")]) for r in data])
        lmax = np.full(len(leq), np.nan)
        lmin = np.full(len(leq), np.nan)
    else:
        raise ValueError(f"알 수 없는 Lp 저장 간격: {interval}")
    blocks = _read_device_leq(next((d / "AUTO_LEQ").glob("*.rnd")))
    return Store(point, rnd, name, h["Serial Number"], h, t, dt, leq, lmax, lmin, blocks)


def energy_mean(levels):
    """에너지 평균 LAeq = 10·log10((1/N)·Σ10^(Li/10))."""
    x = np.asarray(levels, dtype=float)
    x = x[~np.isnan(x)]
    return 10 * np.log10(np.mean(10 ** (x / 10)))


def to_1s(store):
    """샘플을 1초 LAeq로 묶는다. 반환: (1초 구간 끝 시각, LAeq,1s).

    100 ms 자료는 기기 시작 시각부터 10개씩 묶는다(기기 LN Mode Leq_1s와 같은 방식으로 가정).
    """
    if store.dt == 1.0:
        return store.t_end, store.leq
    n = len(store.leq) // 10
    e = 10 ** (store.leq[: n * 10] / 10)
    leq1 = 10 * np.log10(e.reshape(n, 10).mean(axis=1))
    return store.t_end[9: n * 10: 10], leq1


def block_mask(store, block):
    """기기 AUTO_LEQ 블록과 같은 구간에 속하는 샘플 마스크(샘플 수 기준).

    기기 블록 길이 / 샘플 간격 만큼 첫 샘플부터 잡는다.
    """
    n = int(round(block["dur_s"] / store.dt))
    m = np.zeros(len(store.leq), bool)
    m[:n] = True
    return m


def to_datetime(t64):
    return [datetime.fromisoformat(str(x)) for x in t64]


def seconds(td):
    return td / timedelta(seconds=1)
