# -*- coding: utf-8 -*-
"""
두 전파 방법 비교표(작업 4단계-4) — 같은 기하에서 감쇠항별로 차이를 분해함. 차이는 두 기준의 차이이며 오류가 아님.
  python tools/compare_methods.py [출력 CSV]      (기본: 표준출력에 Markdown 표)
조건: 평탄·단단한 지면(G = 0), 점음원 h_s = 0.5 m, 수음점 h_r = 4 m, 거리 d_p = 10·50·100·200 m,
      방음벽 있음 = 음원에서 min(5 m, d_p/2) 떨어진 높이 3 m 얇은 벽(1 m 래스터). 500 Hz, 15 °C·70 %(설정 기본값).
분해(순감쇠 차 Δ = RLS D_A − ISO A_net):
  Δ_div = D_div − A_div (2π 대 4π: −3.01 dB 고정), Δ_atm = D_atm − A_atm,
  Δ_gr+bar = max{D_gr; D_z} − (A_gr + A_bar), Δ_Ω = + D_Ω (대안법만)
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sori import config  # noqa: E402
from sori import iso9613 as I  # noqa: E402
from sori import rls19_propagation as RP  # noqa: E402
from sori.geometry import NGEOM, path_geometry  # noqa: E402

HS, HR = 0.5, 4.0


def world(n, wall_x=None, wall_h=3.0):
    gnd = np.zeros((n, n)); dsm = np.zeros((n, n)); bid = -np.ones((n, n), np.int64); G = np.zeros((n, n))
    if wall_x is not None:
        dsm[:, int(wall_x)] = wall_h; bid[:, int(wall_x)] = 0
    return gnd, dsm, bid, G


def geometry(dp, barrier):
    n = 260
    xs, y = 20.0, n / 2
    wall = xs + min(5.0, dp / 2) if barrier else None
    gnd, dsm, bid, G = world(n, wall)
    vx = np.array([xs, xs + dp, 0, 0, 0]); vy = np.array([y, y, 0, 0, 0])
    hd = np.empty(8192); hh = np.empty(8192); geom = np.empty(NGEOM)
    path_geometry(vx, vy, 2, HS, HR, HS, HR, gnd, dsm, bid, G, 0.0, float(n), 1.0, n, n, 1.0,
                  -1, np.full(3, -1, np.int64), 0.0, hd, hh, geom)
    return geom


def table():
    p = config.DEFAULTS["propagation"]
    f = 500.0; lam = I.C_SOUND / f
    alpha = I.alpha_iso9613_1(f, p["temperature_C"], p["humidity_pct"], p["pressure_kPa"])
    br = p["barrier"]
    rows = []
    for barrier in (False, True):
        for dp in (10.0, 50.0, 100.0, 200.0):
            g = geometry(dp, barrier)
            tr = np.zeros(7); RP.terms_rls19(g, tr)
            for alt in (False, True):
                ti = np.zeros(7)
                I.terms_iso(g, f, lam, alpha, alt, br["C2"], br["dz_max_single"], br["dz_max_double"], ti)
                gb_r = max(tr[RP.T_DGR], tr[RP.T_DZ]) if g[8] > 0.5 else tr[RP.T_DGR]
                rows.append(dict(
                    case="방음벽" if barrier else "차폐 없음", d_p=dp, iso_ground="대안법" if alt else "일반법",
                    z=round(g[10], 4) if g[8] > 0.5 else 0.0,
                    D_div=tr[RP.T_DDIV], D_atm=tr[RP.T_DATM], D_gr=tr[RP.T_DGR], D_z=tr[RP.T_DZ], D_A=tr[RP.T_DA],
                    A_div=ti[I.T_ADIV], A_atm=ti[I.T_AATM], A_gr=ti[I.T_AGR], D_Omega=ti[I.T_DOMEGA],
                    D_z_iso=ti[I.T_DZ], A_bar=ti[I.T_ABAR], A_net=ti[I.T_NET],
                    d_div=tr[RP.T_DDIV] - ti[I.T_ADIV], d_atm=tr[RP.T_DATM] - ti[I.T_AATM],
                    d_gr_bar=gb_r - (ti[I.T_AGR] + ti[I.T_ABAR]), d_omega=ti[I.T_DOMEGA],
                    delta=tr[RP.T_DA] - ti[I.T_NET]))
    T = pd.DataFrame(rows)
    assert np.allclose(T.d_div + T.d_atm + T.d_gr_bar + T.d_omega, T.delta, atol=1e-9)   # 분해 닫힘
    return T


def markdown(T):
    head = ("| 경우 | d_p m | ISO 지면 | RLS D_A | ISO A−D_c | Δ | Δ_div | Δ_atm | Δ_지면·차폐 | Δ_Ω |\n"
            "|---|---|---|---|---|---|---|---|---|---|")
    lines = [head]
    for r in T.itertuples():
        lines.append(f"| {r.case} | {r.d_p:g} | {r.iso_ground} | {r.D_A:.2f} | {r.A_net:.2f} | {r.delta:+.2f} | "
                     f"{r.d_div:+.2f} | {r.d_atm:+.2f} | {r.d_gr_bar:+.2f} | {r.d_omega:+.2f} |")
    return "\n".join(lines)


if __name__ == "__main__":
    T = table()
    if len(sys.argv) > 1:
        T.to_csv(sys.argv[1], index=False, encoding="utf-8-sig", float_format="%.4f")
    print(markdown(T))
