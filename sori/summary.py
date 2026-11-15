# -*- coding: utf-8 -*-
"""
결과 요약·입력 도우미(계산 커널과 무관, 화면 없이 시험 가능).
  - 건물 단위 표: 건물마다 벽면(전 층) 최고 레벨, 최고 층, 수음점 수
  - 건물별 노출: 최고 레벨 5 dB 등급별 건물 수
  (소음 관리기준 대조는 하지 않음 — 2026-11-15 사용자 결정: 기준은 지역·건물마다 달라 프로그램이 정하지 않음)
  - 성분 다시 합치기: 고른 성분(직접·1차·2차·3차·4차 이상)의 에너지 합(주간 LrT 만 성분이 있음)
  - 국내 12종 차종 → RLS-19 차량군(Pkw / Lkw1 / Lkw2) 변환(가정 대응표 — 근거 확인 필요)
  - 계산 전 예상 점 수·시간
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import math

import numpy as np
import pandas as pd

CLASS_EDGES = [35, 40, 45, 50, 55, 60, 65, 70, 75, 80]


def building_table(FR, bld):
    """건물 단위 표(벽면 결과가 있는 건물만), LrT 최고 내림차순.
    열: bid, 이름, 종류, 층수, 수음점, LrT_max, LrN_max, 최고층(LrT 최고인 층)"""
    cols = ["bid", "이름", "종류", "층수", "수음점", "LrT_max", "LrN_max", "최고층"]
    if FR is None or not len(FR):
        return pd.DataFrame(columns=cols)
    g = FR.groupby("bid")
    T = g.agg(수음점=("LrT", "size"), LrT_max=("LrT", "max"), LrN_max=("LrN", "max")).reset_index()
    top = FR.loc[g.LrT.idxmax().dropna().astype(int), ["bid", "floor"]].rename(columns={"floor": "최고층"})
    T = T.merge(top, on="bid", how="left")
    B = bld.set_index("bid")
    T["이름"] = [str(B.bname.get(b, "") or "") if "bname" in B else "" for b in T.bid]
    T["이름"] = T["이름"].replace({"nan": "", "None": ""})
    T["종류"] = [str(B.kind.get(b, "")) if "kind" in B else "" for b in T.bid]
    T["층수"] = [int(B.floors.get(b, 0)) if "floors" in B else 0 for b in T.bid]
    return T.sort_values("LrT_max", ascending=False).reset_index(drop=True)[cols]


def exposure(T, key="LrT"):
    """건물별 노출: 최고 레벨 등급(5 dB)별 건물 수. 반환 [(등급 글, 건물 수)] (건물이 있는 등급만이 아니라 전 등급)"""
    v = T[f"{key}_max"].values if len(T) else np.array([])
    e = CLASS_EDGES
    labels = [f"{e[0]} 이하"] + [f"{a} – {b}" for a, b in zip(e[:-1], e[1:])] + [f"{e[-1]} 초과"]
    idx = np.searchsorted(e, v, side="left") if len(v) else np.array([], int)
    return [(lab, int((idx == i).sum())) for i, lab in enumerate(labels)]


# ---------------------------------------------------------------- 성분 다시 합치기
COMPONENTS = {"direct": ("직접음", "LrT_direct"), "refl1": ("1차 반사", "LrT_refl1"), "refl2": ("2차 반사", "LrT_refl2"),
              "refl3": ("3차 반사", "LrT_refl3"), "refl4p": ("4차 이상", "LrT_refl4p"),
              "lateral": ("측면 회절", "LrT_lateral")}


def available_components(G):
    return [k for k, (_n, c) in COMPONENTS.items() if c in G.columns and G[c].notna().any()]


def combine(G, comps):
    """고른 성분(COMPONENTS 키)의 에너지 합 dB(주간). 성분이 하나도 없는 점은 NaN"""
    E = np.zeros(len(G))
    for k in comps:
        c = COMPONENTS[k][1]
        if c in G.columns:
            v = G[c].values.astype(float)
            E += np.where(np.isfinite(v), 10.0 ** (0.1 * v), 0.0)
    with np.errstate(divide="ignore"):
        return np.where(E > 0, 10.0 * np.log10(np.maximum(E, 1e-300)), np.nan)


# ---------------------------------------------------------------- 국내 차종 → RLS-19 차량군
# 국토교통부 교통량 조사 12종 분류 → RLS-19 2.1 차량군. ※ 가정 대응표(프로젝트 선택) — 공식 대응 규정은 확인하지 못함.
#   Pkw  : 승용차·3.5 t 이하 화물(RLS-19: Pkw, Lieferwagen ≤ 3,5 t)
#   Lkw1 : 트레일러 없는 3.5 t 초과 화물차·버스
#   Lkw2 : 트레일러·세미트레일러 화물차(이 프로젝트는 이륜차도 Lkw2 로 넣음 — 교통량 표 p2 설명)
KR12 = [(1, "1종 승용차·미니트럭", "Pkw"), (2, "2종 버스", "Lkw1"), (3, "3종 소형 화물(1~2.5 t 미만)", "Pkw"),
        (4, "4종 중형 화물(2.5~8.5 t 미만)", "Lkw1"), (5, "5종 대형 화물(3축)", "Lkw1"), (6, "6종 대형 화물(4축)", "Lkw1"),
        (7, "7종 대형 화물(5축)", "Lkw1"), (8, "8종 세미 트레일러(4축)", "Lkw2"), (9, "9종 풀 트레일러(4축)", "Lkw2"),
        (10, "10종 세미 트레일러(5축)", "Lkw2"), (11, "11종 풀 트레일러(5축)", "Lkw2"),
        (12, "12종 세미·풀 트레일러(6축 이상)", "Lkw2")]
KR_EXTRA = [("moto", "이륜차", "Lkw2")]
KR_NOTE = ("국내 12종 차종 → RLS-19 차량군은 프로젝트 가정 대응(1·3종 → Pkw, 2·4~7종 → Lkw1, 8~12종·이륜차 → Lkw2). "
           "공식 대응 규정은 확인하지 못함")


def kr_to_rls19(counts, hours=1.0):
    """counts: {1..12 또는 'moto': 대수} (hours 시간 동안 센 값) → dict(M 대/h, p1 %, p2 %, n_pkw, n_lkw1, n_lkw2)"""
    grp = {k: g for k, _n, g in KR12}
    grp.update({k: g for k, _n, g in KR_EXTRA})
    n = {"Pkw": 0.0, "Lkw1": 0.0, "Lkw2": 0.0}
    for k, v in counts.items():
        if k not in grp:
            raise ValueError(f"모르는 차종: {k}")
        v = float(v or 0.0)
        if v < 0:
            raise ValueError("대수는 0 이상")
        n[grp[k]] += v
    tot = sum(n.values())
    if tot <= 0 or hours <= 0:
        return dict(M=0.0, p1=0.0, p2=0.0, n_pkw=0.0, n_lkw1=0.0, n_lkw2=0.0)
    return dict(M=tot / hours, p1=100.0 * n["Lkw1"] / tot, p2=100.0 * n["Lkw2"] / tot,
                n_pkw=n["Pkw"], n_lkw1=n["Lkw1"], n_lkw2=n["Lkw2"])


# ---------------------------------------------------------------- 계산 전 예상 점 수·시간
def estimate_points(cfg, which=("grid", "facade", "points"), build_info=None, extra_heights=None):
    """계산 전 예상 수음점 수. 격자는 범위 넓이 ÷ 간격² × 높이 수(건물 안 점은 빠지므로 실제는 이보다 적음),
    벽면·건물 안 점은 모델을 만들어야 알 수 있어 이전 모델 기록(build_info)이 있으면 그 값을 씀"""
    from .config import grid_bbox
    r = cfg["receivers"]
    out = {"grid": 0, "facade": None, "points": None, "grid_exact": False}
    if "grid" in which and r["grid"]["enabled"]:
        x0, y0, x1, y1 = grid_bbox(cfg)
        step = float(r["grid"]["step"])
        hs = {round(float(h), 3) for h in list(r["grid"]["heights"]) + list(extra_heights or [])}
        per = int(math.ceil((x1 - x0) / step) * math.ceil((y1 - y0) / step))
        if build_info and build_info.get("grid_receivers_per_height"):
            per, out["grid_exact"] = int(build_info["grid_receivers_per_height"]), True
        out["grid"] = per * len(hs)
    if "facade" in which and r["facade"]["enabled"]:
        out["facade"] = int(build_info["facade_receivers"]) if build_info and "facade_receivers" in build_info else None
    return out


def estimate_time(n_points, rate):
    """rate(점/초)가 있으면 예상 시간(초), 없으면 None"""
    return n_points / rate if rate and rate > 0 and n_points else None


# ---------------------------------------------------------------- 측정 − 계산 차이
_PERIOD = {"day": "LrT", "주간": "LrT", "d": "LrT", "lrt": "LrT", "night": "LrN", "야간": "LrN", "n": "LrN", "lrn": "LrN"}
_LEQ = ("Leq", "Leq_meas", "LAeq", "측정_LAeq", "L_meas")


def read_measured(path):
    """측정값 CSV → DataFrame(point_id, key(LrT/LrN), meas). 두 형식:
      긴 형식  point_id, period(day/night/주간/야간), Leq(또는 LAeq·Leq_meas) — 같은 지점·시간대가 여러 줄이면 에너지 평균
      넓은 형식 point_id, LrT_meas, LrN_meas (한쪽만 있어도 됨)"""
    from .inputs import read_csv_any
    raw = read_csv_any(path)
    pid = next((c for c in ("point_id", "id", "수음점") if c in raw.columns), None)
    if pid is None:
        raise ValueError(f"측정값 CSV 에 point_id 열이 없음: {list(raw.columns)}")
    rows = []
    if "period" in raw.columns:
        lc = next((c for c in _LEQ if c in raw.columns), None)
        if lc is None:
            raise ValueError(f"측정값 CSV(period 형식)에 측정값 열이 없음(허용: {', '.join(_LEQ)})")
        for _, r in raw.iterrows():
            k = _PERIOD.get(str(r["period"]).strip().lower())
            v = pd.to_numeric(r[lc], errors="coerce")
            if k and np.isfinite(v):
                rows.append((str(r[pid]).strip(), k, float(v)))
    else:
        for k in ("LrT", "LrN"):
            if f"{k}_meas" in raw.columns:
                for _, r in raw.iterrows():
                    v = pd.to_numeric(r[f"{k}_meas"], errors="coerce")
                    if np.isfinite(v):
                        rows.append((str(r[pid]).strip(), k, float(v)))
        if not rows:
            raise ValueError("측정값 CSV 형식: point_id, period, Leq 또는 point_id, LrT_meas, LrN_meas")
    M = pd.DataFrame(rows, columns=["point_id", "key", "meas"])
    return M.groupby(["point_id", "key"], sort=False).meas.apply(
        lambda v: 10 * np.log10(np.mean(10 ** (0.1 * v.values)))).reset_index()


def measured_compare(P, M):
    """점 수음점 결과 P(point_id, x, y, LrT, LrN) 와 측정값 M → point_id, x, y, key, meas, calc, diff(계산 − 측정).
    계산값이 없는 지점(건물 안·범위 밖·이름 불일치)은 calc·diff 가 NaN"""
    P = P.assign(point_id=P.point_id.astype(str))
    out = M.merge(P[["point_id", "x", "y"] + [k for k in ("LrT", "LrN") if k in P]], on="point_id", how="left")
    out["calc"] = [r[r.key] if r.key in out.columns and pd.notna(r.get(r.key)) else np.nan for _, r in out.iterrows()]
    out["diff"] = out.calc - out.meas
    return out[["point_id", "x", "y", "key", "meas", "calc", "diff"]]


def floor_heights(n, z_in_floor=2.8, floor_height=3.0):
    """층별 격자 높이(지면 위 m): k 층 = z_in_floor + (k − 1)·floor_height, k = 1 … n (벽면 수음점과 같은 높이 규칙)"""
    return [round(float(z_in_floor) + k * float(floor_height), 3) for k in range(int(n))]
