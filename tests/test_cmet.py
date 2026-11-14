# -*- coding: utf-8 -*-
"""ISO 9613-2 기상 보정 C_met(식 21·22, propagation.cmet_C0).
※ Claude(Anthropic의 AI)를 활용하여 제작하였음."""
import math
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from sori import build as B, config as C, run as RUN
from sori.iso9613 import C_met
from reference_case import INPUT


def test_cmet_formula():
    assert C_met(3.0, 0.5, 4.0, 45.0) == 0.0                                   # d_p ≤ 10(hs + hr)
    assert C_met(3.0, 0.5, 4.0, 90.0) == pytest.approx(3.0 * (1 - 45.0 / 90.0))
    assert C_met(0.0, 0.5, 4.0, 900.0) == 0.0
    assert C_met(2.0, 0.5, 4.0, 1e9) == pytest.approx(2.0)


def test_cmet_in_kernel_free_field():
    """평지 점음원: C0 를 주면 L = L(DW) − C_met(d_p) 그대로"""
    from test_lateral import solve, world
    w, bxy, bptr = world([], [])
    from sori.solver import compute_l
    gnd, dsm, bid, G, x0, y0, res, nx, ny = w
    F = [np.zeros(0)] * 9 + [np.zeros(0, np.int64)]
    one = np.ones(1)

    def run(c0, dist):
        o = compute_l(np.array([100.0 + dist]), np.array([150.0]), np.array([4.0]), np.zeros(1),
                      -np.ones(1, np.int64), -np.ones(1, np.int64), 0, np.array([100.0]), np.array([150.0]),
                      np.array([0.5]), np.zeros(1), one, one, np.ones(1, np.bool_), *F, 0.8, gnd, dsm, bid, G,
                      x0, y0, res, nx, ny, 1.0, 500.0, 500.0, 500.0, 500.0, 500.0, 500.0, 1.5, np.array([500.0]),
                      np.array([0.68]), np.zeros(1), one, False, 20.0, 20.0, 25.0, 1, np.zeros(1, np.int64),
                      np.zeros(0, np.int64), False, np.zeros((0, 5)), False, 0, np.zeros(0, np.int64), bxy, bptr,
                      np.ones(0), c0)
        return 10 * math.log10(o[0][0])
    for dist in (30.0, 120.0):
        assert run(2.5, dist) - run(0.0, dist) == pytest.approx(-C_met(2.5, 0.5, 4.0, dist), abs=1e-9)


def test_cmet_config_and_reference(tmp_path):
    c = C._merge(C.DEFAULTS, {"receivers": {"center": [0, 0]}, "propagation": {"cmet_C0": 12}})
    with pytest.raises(ValueError, match="cmet_C0"):
        C._validate(c)
    work = tmp_path / "w"
    shutil.copytree(INPUT, work)
    cfg = C.load(work / "config.yaml")
    cfg["receivers"]["facade"]["enabled"] = False
    cfg["receivers"]["grid"]["heights"] = [4.0]
    B.main(cfg); RUN.main(cfg, ("grid",))
    G0 = pd.read_csv(Path(cfg["project"]["output_dir"]) / "grid_result.csv")
    cfg["propagation"]["cmet_C0"] = 2.0
    RUN.main(cfg, ("grid",))
    G1 = pd.read_csv(Path(cfg["project"]["output_dir"]) / "grid_result.csv")
    d = G1.LrT.values - G0.LrT.values
    assert d.max() <= 1e-9 and d.min() >= -2.0 - 1e-9 and d.min() < -0.5          # 낮아지기만, C0 넘게는 아님
    import json
    ri = json.load(open(Path(cfg["project"]["output_dir"]) / "run_info.json", encoding="utf-8"))
    assert "C0 = 2" in ri["meteorology"]
    cfg["propagation_method"] = "rls19"                                            # RLS-19 는 적용 안 함
    assert RUN.cmet_c0(cfg) == 0.0
