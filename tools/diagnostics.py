# -*- coding: utf-8 -*-
"""
진단 계산 재현 스크립트 — TECHNICAL_NOTES 5a절·작업기록의 수치를 다시 계산함(결과값을 바꾸지 않는 진단).
저장소에는 쓰지 않음(실제 자료 진단은 임시 폴더에서 build).

  python tools/diagnostics.py line-source            # 10-15 B-6: 평지 직선 도로, 구간 1/3/5 m vs 해석해
  python tools/diagnostics.py refine [-n 3000]       # 10-16: l ≤ s/2 위반 구간 세분 재계산(실제 자료, 표본 n)
  python tools/diagnostics.py crossfall [-p 2.0]     # 10-15: 횡단경사 p % 로 간선 음원 높이를 낮췄을 때(반경 150 m)

※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import argparse
import math
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from sori import config as C, run as RUN                           # noqa: E402
from sori.solver import compute                                     # noqa: E402


# ---------------------------------------------------------------- 1. 평지 직선 도로(가상 시험)
def line_source(_a):
    """평지·G = 0·건물 없음·α = 0·500 Hz, 도로 ±45 m(q = 0 유지), LW′ = 80 dB/m, 음원 높이 0.5 m.
    수음점이 구간 중앙 맞은편(ctr)·경계 맞은편(edge)일 때 solver − 해석해(dB)."""
    half, lwp, hs, n = 45.0, 80.0, 0.5, 400
    cx = cy = 200.0
    W = (np.zeros((n, n)), np.zeros((n, n)), -np.ones((n, n), np.int64), np.zeros((n, n)), 0.0, float(n), 1.0, n, n)
    F0 = [np.zeros(0)] * 9 + [np.zeros(0, np.int64)]

    def solver(seg, off, dist, hr):
        k = int(round(2 * half / seg)); xs = cx - half + seg * (np.arange(k) + 0.5)
        P = np.full(k, 10 ** (0.1 * (lwp + 10 * math.log10(seg))))
        gnd, dsm, bid, G, x0, y0, res, nx, ny = W
        Ed, *_ = compute(np.array([cx + off]), np.array([cy + dist]), np.array([hr]), np.zeros(1),
                         -np.ones(1, np.int64), -np.ones(1, np.int64), 0, xs, np.full(k, cy), np.full(k, hs), np.zeros(k),
                         P, P, np.ones(k, np.bool_), *F0, 0.8, gnd, dsm, bid, G, x0, y0, res, nx, ny, 1.0,
                         1e4, 1e4, 1e4, 1e4, 1e4, 1e4, 1.5, np.array([500.0]), np.array([0.68]), np.array([0.0]),
                         np.array([1.0]), False, 20.0, 20.0, 25.0)
        return 10 * math.log10(Ed[0])

    def analytic(off, dist, hr):
        dd = math.hypot(dist, hr - hs)
        return lwp + 10 * math.log10((math.atan((half - off) / dd) - math.atan((-half - off) / dd)) / dd) - 8.0

    for hr in (4.0, 1.5):
        print(f"\n수음 높이 {hr} m — solver − 해석해 [dB]")
        print("거리 | " + " | ".join(f"{s:g} m 중앙 | {s:g} m 경계" for s in (1, 3, 5)))
        for dist in (2, 3, 4, 5, 6, 8, 10, 12, 15, 20):
            v = [solver(s, o, dist, hr) - analytic(o, dist, hr) for s in (1.0, 3.0, 5.0) for o in (0.0, s / 2)]
            print(f"{dist:4g} | " + " | ".join(f"{x:+.3f}" for x in v))


# ---------------------------------------------------------------- 실제 자료 모델(임시 폴더)
def build_real(tmp):
    for k in ("config.yaml", "traffic.csv"):
        shutil.copy(os.path.join(ROOT, "baseline", k), tmp)
    env = dict(os.environ, PYTHONPATH=ROOT + os.pathsep + os.environ.get("PYTHONPATH", ""))
    r = subprocess.run([sys.executable, "-m", "sori", "build", "-c", "config.yaml"], cwd=tmp, env=env,
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(r.stderr[-3000:])
    cfg = C.load(os.path.join(tmp, "config.yaml"))
    return cfg, RUN.load_model(cfg["project"]["output_dir"])


def receivers(cfg, d, FR, rsel=None):
    X, Y = np.meshgrid(d["gx"], d["gy"]); m = ~d["ginside"].ravel()
    h = float(cfg["receivers"]["grid"]["heights"][0])
    G = pd.DataFrame(dict(kind="grid", x=X.ravel()[m], y=Y.ravel()[m], zg=d["gz"].ravel()[m], bid=-1, fid=-1))
    G["z"] = G.zg + h
    Fa = pd.DataFrame(dict(kind="facade", x=FR.x, y=FR.y, z=FR.z, zg=FR.zg, bid=FR.bid, fid=FR.fid))
    R = pd.concat([G, Fa], ignore_index=True)
    if rsel:
        cx, cy = C.center(cfg); R = R[np.hypot(R.x - cx, R.y - cy) <= rsel].reset_index(drop=True)
    return R


def levels(cfg, d, S, F, R):
    rf = cfg["propagation"]["reflection"]; out = {}
    for kind, order in (("grid", rf["max_order_grid"]), ("facade", rf["max_order_facade"])):
        q = R[R.kind == kind]
        if len(q):
            o = RUN._run(cfg, d, S, F, q.x.values, q.y.values, q.z.values, q.zg.values, q.bid.values.astype(np.int64),
                         q.fid.values.astype(np.int64), int(order), kind)
            out.update({i: (a, b) for i, a, b in zip(q.index, o["LrT"], o["LrN"])})
    return np.array([out[i][0] for i in R.index]), np.array([out[i][1] for i in R.index])


def summary(name, R, dT):
    for k in ("grid", "facade"):
        v = pd.Series(dT[(R.kind == k).values])
        if len(v):
            print(f"{name} {k:6s} n={len(v)} 평균 {v.mean():+.3f} 5~95% [{v.quantile(.05):+.3f}, {v.quantile(.95):+.3f}] "
                  f"최소 {v.min():+.3f} 최대 {v.max():+.3f} |d|>0.1 {100*(v.abs()>0.1).mean():.1f}% |d|>0.5 {100*(v.abs()>0.5).mean():.1f}%")


# ---------------------------------------------------------------- 2. 구간 세분 재계산
def refine(a):
    """l ≤ s/2 위반 수음점(직접 경로 3D 거리)에서 위반 구간을 하위 점음원 n 개(l/n ≤ s_k/2)로 나눠 다시 계산.
    dL = 10 lg[(E_전체 − E_위반구간 + E_세분) / E_전체]. 하위 점은 구간 접선 위, 높이는 원 구간과 같음(가정)."""
    from scipy.spatial import cKDTree
    from sori.run import segment_check
    tmp = tempfile.mkdtemp(prefix="sori_diag_")
    try:
        cfg, (d, S, F, FR) = build_real(tmp)
        R = receivers(cfg, d, FR)
        nv, _, _ = segment_check(S, R.x.values, R.y.values, R.z.values)
        idx = np.where(nv > 0)[0]
        print(f"위반 수음점: 격자 {int((nv[R.kind.values == 'grid'] > 0).sum())}, 파사드 {int((nv[R.kind.values == 'facade'] > 0).sum())}")
        if a.n and a.n < len(idx):
            idx = idx[np.linspace(0, len(idx) - 1, a.n).astype(int)]
        Rv = R.iloc[idx].reset_index(drop=True)
        LT, _ = levels(cfg, d, S, F, Rv)
        sx, sy, sz, sl = S.x.values, S.y.values, S.z.values, S.l.values
        tree = cKDTree(np.c_[sx, sy]); rf = cfg["propagation"]["reflection"]
        cols = ["x", "y", "z", "zg", "LW_day", "LW_night", "main"]
        dl = []
        for i, r in Rv.iterrows():
            js = np.array(tree.query_ball_point([r.x, r.y], 2 * sl.max() + 0.01), int)
            js = js[np.sqrt((sx[js] - r.x) ** 2 + (sy[js] - r.y) ** 2 + (sz[js] - r.z) ** 2) < 2 * sl[js]]
            sub = []
            for j in js:
                s0 = S.iloc[j]; n = 2
                while n <= 256:
                    t = (np.arange(n) + 0.5) / n - 0.5
                    px, py = s0.x + t * s0.l * s0.tx, s0.y + t * s0.l * s0.ty
                    if s0.l / n <= (np.sqrt((px - r.x) ** 2 + (py - r.y) ** 2 + (s0.z - r.z) ** 2) / 2).min():
                        break
                    n *= 2
                sub += [dict(x=u, y=v, z=s0.z, zg=s0.zg, LW_day=s0.LW_day - 10 * math.log10(n),
                             LW_night=s0.LW_night - 10 * math.log10(n), main=s0.main) for u, v in zip(px, py)]
            order = int(rf["max_order_grid" if r.kind == "grid" else "max_order_facade"])
            e = []
            for SS in (S.iloc[js][cols].reset_index(drop=True), pd.DataFrame(sub)):
                o = RUN._run(cfg, d, SS, F, np.array([r.x]), np.array([r.y]), np.array([r.z]), np.array([r.zg]),
                             np.array([r.bid], np.int64), np.array([r.fid], np.int64), order, r.kind)
                e.append(10 ** (0.1 * np.nan_to_num(o["LrT"], nan=-999.0))[0])
            E = 10 ** (0.1 * LT[i]); dl.append(10 * math.log10((E - e[0] + e[1]) / E))
        dl = np.array(dl); summary("세분 − 고정", Rv, dl)
        for lo, hi in ((0, 55), (55, 60), (60, 65), (65, 70), (70, 99)):
            m = (LT > lo) & (LT <= hi)
            if m.any():
                print(f"  LrT {lo}~{hi} dB: n={m.sum()} |d|>0.5 {100*(np.abs(dl[m]) > 0.5).mean():.1f}%  5~95% "
                      f"[{np.quantile(dl[m], .05):+.2f}, {np.quantile(dl[m], .95):+.2f}]")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- 3. 횡단경사
def crossfall(a):
    """간선(2개선) 음원을 |오프셋| × p % 만큼 낮춤(중심선 정점, 노면이 바깥으로 낮아진다고 가정 — p 는 가정값).
    지면 고도도 같이 낮춰 음원은 노면 위 0.5 m 유지. 반경 150 m 격자·파사드 LrT 차이."""
    tmp = tempfile.mkdtemp(prefix="sori_diag_")
    try:
        cfg, (d, S, F, FR) = build_real(tmp)
        Ln = pd.read_csv(os.path.join(cfg["project"]["output_dir"], "source_lines.csv")).set_index("fid")
        off = np.where(S.along.isna(), 0.0, np.where(S.along.astype(str) == "True",
                       Ln.offset_fwd.reindex(S.fid).abs().values, Ln.offset_bwd.reindex(S.fid).abs().fillna(0).values))
        dz = -off * a.p / 100.0
        S2 = S.copy(); S2["z"] = S.z + dz; S2["zg"] = S.zg + dz
        print(f"횡단경사 {a.p:g} %(가정): 음원 Δz 최소 {dz.min():.3f} m, 오프셋별 " +
              ", ".join(f"{o:.2f} m → {-o * a.p / 100:.3f} m" for o in sorted(set(np.round(off[off > 0], 2)))))
        R = receivers(cfg, d, FR, rsel=150.0)
        L0, _ = levels(cfg, d, S, F, R); L1, _ = levels(cfg, d, S2, F, R)
        summary("횡단경사 − 기준", R, L1 - L0)
        top = np.argsort(np.where(R.kind.values == "facade", L0, -1))[-5:]
        print("최고 레벨 파사드 5곳 ΔLrT:", ", ".join(f"{L1[i] - L0[i]:+.2f}" for i in top))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description="진단 계산 재현")
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("line-source").set_defaults(f=line_source)
    p = sp.add_parser("refine"); p.add_argument("-n", type=int, default=3000, help="표본 수(0 = 전부)"); p.set_defaults(f=refine)
    p = sp.add_parser("crossfall"); p.add_argument("-p", type=float, default=2.0, help="횡단경사 %%(가정값)"); p.set_defaults(f=crossfall)
    a = ap.parse_args(); a.f(a)


if __name__ == "__main__":
    main()
