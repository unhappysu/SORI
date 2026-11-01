# -*- coding: utf-8 -*-
"""단면(수직) 소음지도 시험 — 기준 사례(가상 교차로)
1) 수음점 배치: 지면 아래·건물 안(지붕 아래) 점은 계산하지 않음, 선 위 위치·간격
2) 계산 연결: 단면 결과의 한 점을 같은 좌표로 run._run 에 직접 넣은 값과 같아야 함(점 순서·가림 대응 확인)
3) 격자와 비교: 같은 평면 위치의 격자(h = 4 m) 값과, 단면에서 높이로 선형 보간한 값의 차이가 작아야 함
   (같은 계산 함수, 높이 보간 오차만 남음 — 허용 0.5 dB 는 프로젝트 임의 설정)
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import numpy as np
import pytest

import reference_case as RC


@pytest.fixture(scope="module")
def case(tmp_path_factory):
    from sori import config as C
    work = tmp_path_factory.mktemp("sec")
    out = RC.run_case(work)
    cfg = C.load(work / "config.yaml")
    return cfg, out


def test_section_receivers_skip_ground_and_buildings(case):
    from sori import run as RUN, section
    cfg, out = case
    d, *_ = RUN.load_model(str(out))
    cx, cy = (float(v) for v in cfg["receivers"]["center"])
    R = section.receivers(d, (cx - 100, cy + 27), (cx + 100, cy + 27), ds=2.0, dz=1.0)   # 도로변 건물 줄(cy+20~35)을 가로지름
    assert R["L"] == pytest.approx(200.0)
    assert np.allclose(np.unique(R["s"])[:3], [1.0, 3.0, 5.0])
    calc = R["calc"]
    assert (R["z"][calc] > R["zg"][calc]).all()                               # 지면 위만
    inb = ~np.isnan(R["top"])
    assert inb.any() and not (calc & inb & (R["z"] <= R["top"])).any()        # 건물 안(지붕 아래) 계산 안 함
    assert (calc & inb & (R["z"] > R["top"] + 0.05)).any()                    # 지붕 위는 계산


def test_section_point_equals_direct_run(case):
    from sori import run as RUN, section
    cfg, out = case
    cx, cy = (float(v) for v in cfg["receivers"]["center"])
    D, info = section.compute(cfg, (cx - 60, cy - 70), (cx + 60, cy - 70), ds=4.0, dz=3.0)
    d, S, F, _ = RUN.load_model(str(out))
    r = D.dropna(subset=["LrT"]).iloc[len(D.dropna(subset=["LrT"])) // 3]
    one = RUN._run(cfg, d, S, F, np.array([r.x]), np.array([r.y]), np.array([r.z]), np.array([r.zg]),
                   np.array([-1], np.int64), np.array([-1], np.int64),
                   int(cfg["propagation"]["reflection"]["max_order_grid"]), "점")
    assert one["LrT"][0] == pytest.approx(r.LrT, abs=1e-9) and one["LrN"][0] == pytest.approx(r.LrN, abs=1e-9)


def test_section_matches_grid_at_4m(case):
    """격자 한 행을 따라 단면을 긋고, 각 격자점 위치에서 지면 위 4 m 값을 높이로 보간해 격자 값과 비교"""
    import pandas as pd
    from sori import section
    cfg, out = case
    G = pd.read_csv(out / "grid_result.csv")
    G = G[G.h == 4.0]
    step = float(np.min(np.diff(np.sort(G.x.unique()))))
    y = float(np.sort(G.y.unique())[len(G.y.unique()) // 4])
    row = G[G.y == y].sort_values("x")
    x0, x1 = float(row.x.iloc[0]) - step / 2, float(row.x.iloc[-1]) + step / 2
    D, info = section.compute(cfg, (x0, y), (x1, y), ds=step, dz=0.5)
    diffs = []
    for gx, gz, gv in zip(row.x, row.z, row.LrT):
        col = D[np.isclose(D.x, gx, atol=1e-6)].dropna(subset=["LrT"]).sort_values("z")
        if len(col) < 2 or not (col.z.min() <= gz <= col.z.max()):
            continue
        diffs.append(abs(np.interp(gz, col.z, col.LrT) - gv))
    assert len(diffs) > 10
    assert max(diffs) < 0.5, max(diffs)
