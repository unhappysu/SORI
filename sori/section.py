# -*- coding: utf-8 -*-
"""
단면(수직) 소음지도: 평면 위 두 점을 잇는 선을 따라 (둘레가 아닌) 선 위 거리 s × 높이 z 의 수음점을 계산.
계산은 격자 수음점과 같은 함수(run._run)를 그대로 부름 — 전파 방법·반사 차수(격자 값 max_order_grid)·지면 처리 동일.
건물 안(지붕 아래)과 지면 아래 점은 계산하지 않고 빈칸(NaN)으로 둠. 결과는 section_result.csv,
단면 정보(양 끝점·간격·지형·지붕 단면) section_info.json, 파일명 규칙 사본({프로젝트}_{시나리오}-{방법}_주야_section.csv).
  python -c "from sori import config, section; section.main(config.load('config.yaml'), (x1, y1), (x2, y2))"
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import json
import logging
import os

import numpy as np
import pandas as pd

from . import run as RUN

log = logging.getLogger("sori")


def receivers(d, p1, p2, ds=None, dz=None, h_above=10.0, max_points=4000):
    """선 위 수음점 배치. ds·dz 를 비우면 점 수가 max_points 안쪽이 되게 고름(가로 1 m, 세로 0.5 m 단위로 올림).
    반환: s, x, y, z, zg(지면고), top(그 위치 건물 지붕 높이, 건물 밖이면 NaN), calc(계산할 점)"""
    (x1, y1), (x2, y2) = p1, p2
    L = float(np.hypot(x2 - x1, y2 - y1))
    if L < 1.0:
        raise ValueError("단면 선이 너무 짧음(1 m 미만)")
    # 지면·지붕 높이 단면(0.5 m 간격)으로 높이 범위를 정함
    sp = np.linspace(0.0, L, max(2, int(L / 0.5) + 1))
    xp, yp = x1 + sp / L * (x2 - x1), y1 + sp / L * (y2 - y1)
    zgp, inside, outside = RUN.point_ground(d, xp, yp)
    if outside.all():
        raise ValueError("단면 선이 계산 모델 범위 밖에 있음")
    dsm = _sample(d["dsm"], d, xp, yp)
    zmin = float(np.nanmin(zgp[~outside]))
    zmax = float(np.nanmax(np.where(inside, dsm, zgp)[~outside])) + h_above
    H = zmax - zmin
    if ds is None or dz is None:
        k = np.sqrt(max_points / (L * H))              # 가로·세로 같은 비율로 점 수 맞춤
        ds = ds or max(1.0, np.ceil(1.0 / k))
        dz = dz or max(0.5, np.ceil(2.0 / k) / 2)
    s = np.arange(ds / 2, L, ds)
    z = np.arange(zmin + dz / 2, zmax, dz)
    S, Z = np.meshgrid(s, z)
    X, Y = x1 + S / L * (x2 - x1), y1 + S / L * (y2 - y1)
    zg, ins, out = RUN.point_ground(d, X.ravel(), Y.ravel())
    top = np.where(ins, _sample(d["dsm"], d, X.ravel(), Y.ravel()), np.nan)
    zz = Z.ravel()
    calc = ~out & (zz > zg + 0.05) & ~(ins & (zz <= top + 0.05))   # 지면 위, 건물이면 지붕 위
    return dict(s=S.ravel(), x=X.ravel(), y=Y.ravel(), z=zz, zg=zg, top=top, calc=calc,
                ds=float(ds), dz=float(dz), L=L, profile=(sp, zgp, np.where(inside, dsm, np.nan)))


def _sample(A, d, x, y):
    """래스터 셀 값(최근접) — 건물 지붕 높이(dsm)용"""
    x0, y0, res = float(d["x0"]), float(d["y0"]), float(d["res"])
    i = np.clip(np.floor((y0 - np.asarray(y)) / res).astype(int), 0, A.shape[0] - 1)
    j = np.clip(np.floor((np.asarray(x) - x0) / res).astype(int), 0, A.shape[1] - 1)
    return A[i, j].astype(float)


def compute(cfg, p1, p2, ds=None, dz=None, progress=None, max_points=4000):
    """단면 계산 → DataFrame(s, x, y, z, zg, h, LrT, LrN, LrT_direct, LrT_refl1…) 와 단면 정보(dict)"""
    out = cfg["project"]["output_dir"]
    d, S, F, _ = RUN.load_model(out)
    R = receivers(d, p1, p2, ds, dz, max_points=max_points)
    m = R["calc"]
    n = int(m.sum())
    log.info("단면 %.0f m: 가로 %.1f m × 세로 %.1f m 간격, 계산점 %d개(건물 안·지면 아래 제외)", R["L"], R["ds"], R["dz"], n)
    order = int(cfg["propagation"]["reflection"]["max_order_grid"])
    r = RUN._run(cfg, d, S, F, R["x"][m], R["y"][m], R["z"][m], R["zg"][m],
                 np.full(n, -1, np.int64), np.full(n, -1, np.int64), order, "단면", progress=progress)
    D = pd.DataFrame({k: R[k] for k in ("s", "x", "y", "z", "zg")})
    D["h"] = D.z - D.zg
    D["inside_building"] = ~np.isnan(R["top"]) & (D.z <= R["top"] + 0.05)
    for k, v in r.items():
        col = np.full(len(D), np.nan)
        col[m] = v
        D[k] = col
    info = dict(p1=list(map(float, p1)), p2=list(map(float, p2)), length_m=R["L"], ds_m=R["ds"], dz_m=R["dz"],
                n_calc=n, reflection_order=order, profile=R["profile"])
    return D, info


def main(cfg, p1, p2, ds=None, dz=None, progress=None, max_points=4000):
    """계산하고 결과 폴더에 section_result.csv(+ 파일명 규칙 사본) 저장. (DataFrame, info, 파일 경로) 반환"""
    D, info = compute(cfg, p1, p2, ds, dz, progress, max_points)
    out = cfg["project"]["output_dir"]
    fn = os.path.join(out, "section_result.csv")
    D.to_csv(fn, index=False, encoding="utf-8-sig")
    info["profile"] = [[None if np.isnan(v) else round(float(v), 3) for v in a] for a in info["profile"]]
    with open(os.path.join(out, "section_info.json"), "w", encoding="utf-8") as f:   # 화면에서 다시 그릴 때 씀
        json.dump(info, f, ensure_ascii=False)
    try:
        from .naming import copy_as
        copy_as(cfg, fn, "section")
    except Exception as e:                              # 이름 사본 실패는 계산 결과와 무관
        log.warning("단면 결과 이름 사본 실패: %s", e)
    log.info("단면 결과: %s", fn)
    return D, info, fn
