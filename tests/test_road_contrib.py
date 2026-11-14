# -*- coding: utf-8 -*-
"""점 수음점 도로별 기여(point_road_contrib.csv, paths.road_contrib_table).
※ Claude(Anthropic의 AI)를 활용하여 제작하였음."""
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from sori import build as B, config as C, run as RUN
from reference_case import INPUT


@pytest.mark.parametrize("lateral", [False, True])
def test_point_road_contrib(tmp_path, lateral):
    work = tmp_path / "w"
    shutil.copytree(INPUT, work)
    cx, cy = 205108.0, 553110.0
    pd.DataFrame(dict(point_id=["A", "B", "C"], x=[cx + 40.0, cx, cx + 60.0], y=[cy - 7.0, cy - 45.0, cy + 40.0],
                      h=[1.5, 4.0, 4.0])).to_csv(work / "points.csv", index=False)
    cfg = C.load(work / "config.yaml")
    cfg["receivers"]["points"]["path"] = str(work / "points.csv")
    cfg["propagation"]["lateral"]["enabled"] = lateral
    B.main(cfg); RUN.main(cfg, ("points",))
    out = Path(cfg["project"]["output_dir"])
    P = pd.read_csv(out / "point_result.csv")
    R = pd.read_csv(out / "point_road_contrib.csv")
    assert set(R.point_id) == set(P.point_id[P.LrT.notna()])
    for pid, g in R.groupby("point_id"):
        e = 10 * np.log10((10 ** (0.1 * g.LrT)).sum())
        assert e == pytest.approx(g.covered_LrT.iloc[0], abs=0.02)            # 도로 합 = 경로 합
        assert g.share_LrT.sum() == pytest.approx(100.0, abs=0.5)
        assert list(g.LrT) == sorted(g.LrT, reverse=True)
        tot = P.set_index("point_id").LrT[pid]
        if lateral:
            assert g.not_covered_dB.iloc[0] >= -0.02                          # 측면 회절만큼 덜 덮음
        else:
            assert g.covered_LrT.iloc[0] == pytest.approx(tot, abs=0.02)      # 반사 3차까지 = 전체
    assert set(R.road) <= {"가로A", "가로B"} | {r for r in R.road if r.startswith("이름 없는 도로")}
