# -*- coding: utf-8 -*-
"""정밀 격자(경계 세분, sori/refine.py, 2026-11-15)
※ Claude(Anthropic의 AI)를 활용하여 제작하였음."""
import numpy as np
import pandas as pd

from sori import refine as RF


def test_refine_rule_on_synthetic_field():
    """세분 규칙: 건물·노면에 닿는 칸과 값 범위가 기준을 넘는 칸만 반 간격으로, 단계마다 거기서만 다시 판단.
    계산한 값은 compute 가 준 값 그대로, 건물 안 점은 계산하지 않음"""
    k = 4
    n = 4 * k + 1                                         # 규칙 격자 5 × 5, 세밀 17 × 17
    I, J = np.mgrid[0:n, 0:n]
    truth = np.where(J > 9.5, 70.0, 60.0)                # 세로 경계(열 9.5)에서 10 dB 계단
    ins = np.zeros((n, n), bool)
    ins[0:3, 0:3] = True                                  # 왼쪽 아래 건물
    calls = []

    def compute(ii, jj):
        calls.append(len(ii))
        assert not ins[ii, jj].any()
        return {"LrT": truth[ii, jj], "LrN": truth[ii, jj] - 5}
    V0 = truth[::k, ::k].copy()
    V0[ins[::k, ::k]] = np.nan
    V, comp, new = RF.refine(V0, ins, ins.copy(), k, 2.0, compute)
    assert len(calls) == 2 and [h for *_, h in new] == [2, 1]
    assert np.allclose(V[comp & ~ins], truth[comp & ~ins])
    assert comp[:, 9].all() and comp[:, 10].all()        # 경계 양쪽 열은 가장 촘촘히
    assert not comp[13:, 1].any()                         # 경계·건물에서 먼 왼쪽 위 칸은 세분 안 함
    assert comp[1, 3] and not ins[1, 3]                   # 건물에 닿는 칸은 세분(건물 밖 점만)
    reg = np.zeros((n, n), bool)
    reg[::k, ::k] = True
    assert not (comp & ins & ~reg).any()                  # 세분 점은 건물 안에 없음(규칙 격자점 표시는 그대로)


def test_window_any():
    M = np.zeros((9, 9), bool)
    M[4, 8] = True
    W = RF._window_any(M, 4)                              # 칸 2 × 2
    assert W.tolist() == [[False, True], [False, True]]   # 꼭짓점(4, 8)은 오른쪽 두 칸이 같이 가짐


def test_refined_grid_run_reference_case(tmp_path):
    """기준 사례: 정밀 격자를 켜고 격자만 다시 계산 → 규칙 격자 결과는 그대로, 세분 점·세밀 배열·GeoTIFF 생김,
    세분 점 값은 같은 점을 따로 계산한 값과 같음, 세밀 배열은 계산점에서 계산값. 끄고 다시 계산하면 세밀 파일 지움"""
    import reference_case as RC
    from sori import config as C, run as RUN, maps_hq as M
    out = RC.run_case(tmp_path / "case")
    G0 = pd.read_csv(out / "grid_result.csv")
    cfg = C.load(str(tmp_path / "case" / "config.yaml"))
    cfg["receivers"]["grid"]["refine"]["enabled"] = True
    RUN.main(cfg, ("grid",))
    G1 = pd.read_csv(out / "grid_result.csv")
    assert np.allclose(G0.LrT, G1.LrT, equal_nan=True) and np.allclose(G0.LrN, G1.LrN, equal_nan=True)
    R = pd.read_csv(out / "grid_refined.csv")
    step = float(cfg["receivers"]["grid"]["step"])
    assert len(R) > len(G0) and set(R.step_m.round(6)) == {step / 2, step / 4}
    h = float(cfg["receivers"]["grid"]["heights"][0])
    Rh = R[np.isclose(R.h, h)]
    on = Rh.merge(G1[np.isclose(G1.h, h)][["x", "y"]], on=["x", "y"], how="inner")
    assert len(on) == 0                                   # 규칙 격자점을 다시 계산하지 않음
    d, S, F, _ = RUN.load_model(str(out))                 # 세분 점 몇 개를 따로 계산 → 같은 값
    r = Rh.sample(5, random_state=0)
    zg, _, _ = RUN.point_ground(d, r.x.values, r.y.values)
    v = RUN._run(cfg, d, S, F, r.x.values, r.y.values, zg + h, zg, np.full(5, -1, np.int64), np.full(5, -1, np.int64),
                 int(cfg["propagation"]["reflection"]["max_order_grid"]), "확인")["LrT"]
    assert np.allclose(v, r.LrT.values, atol=1e-9)
    z = RF.load_fine(str(out), h)
    f = float(z["fine_step"])
    i = np.round((Rh.y.values - z["fy"][0]) / f).astype(int)
    j = np.round((Rh.x.values - z["fx"][0]) / f).astype(int)
    vals = z["LrT"][i, j]
    ok = np.isfinite(vals)
    assert ok.mean() > 0.95 and np.allclose(vals[ok], Rh.LrT.values[ok], atol=1e-3)   # float32
    for key in ("LrT", "LrN"):
        assert (out / f"grid_{key}_h{h:g}m_fine.tif").exists()
    C_ = M.Ctx(cfg)
    gx, gy, st, Am, A = M.grid_array(C_, "LrT", h)
    assert len(gx) == len(z["fx"]) and st == step and M.refine_note(C_, h) == f" · 경계 세분 {step / 4:g} m"
    cfg["receivers"]["grid"]["refine"]["enabled"] = False
    RUN.main(cfg, ("grid",))
    assert not (out / "grid_refined.csv").exists() and RF.load_fine(str(out), h) is None


def test_stage_count_with_refine():
    """진행률 단계: 정밀 격자면 높이마다 세분 단계 수만큼 더"""
    import sori_qt as Q
    cfg = {"receivers": {"grid": {"enabled": True, "heights": [4.0, 1.5], "refine": {"enabled": True, "levels": 2}},
                         "facade": {"enabled": False}, "points": {"path": None}}}
    assert Q.count_stages(cfg, ["grid"]) == 6
    cfg["receivers"]["grid"]["refine"]["enabled"] = False
    assert Q.count_stages(cfg, ["grid"]) == 2
