# -*- coding: utf-8 -*-
"""건물 꼭짓점별 지붕 높이(model.building_ground: follow) — 3D 그리기 공통.
build 가 buildings.gpkg 의 ztops 열에 JSON [[x, y, 지붕 표고], …](모든 고리의 꼭짓점)을 저장. 그리기에서는
roof_fn(b) 로 (x, y) → 지붕 표고 함수를 얻음. 열이 없거나(예전 결과·vertex_min·pad) 꼭짓점이 없으면 b.ztop(평지붕).
※ Claude(Anthropic의 AI)를 활용하여 제작하였음."""
import json

import numpy as np


def encode(geom, gnd_at, H):
    """도형의 모든 고리 꼭짓점 → JSON 문자열. gnd_at(x 배열, y 배열) = 지형 표고"""
    pts = []
    for g in getattr(geom, "geoms", [geom]):
        for r in [g.exterior] + list(g.interiors):
            c = np.asarray(r.coords)[:-1]
            z = gnd_at(c[:, 0], c[:, 1]) + H
            pts += [[round(float(x), 2), round(float(y), 2), round(float(v), 2)] for (x, y), v in zip(c, z)]
    return json.dumps(pts, separators=(",", ":"))


def roof_fn(b):
    """건물 행 b(itertuples·Series) → f(x, y) = 지붕 표고. 저장된 꼭짓점과 좌표가 같으면 그 값, 아니면 가장 가까운 꼭짓점 값"""
    zt = float(getattr(b, "ztop"))
    raw = getattr(b, "ztops", None)
    if not isinstance(raw, str) or not raw:
        return lambda x, y: zt
    P = np.asarray(json.loads(raw), float)
    if not len(P):
        return lambda x, y: zt
    d = {(round(x, 2), round(y, 2)): z for x, y, z in P}

    def f(x, y):
        v = d.get((round(float(x), 2), round(float(y), 2)))
        if v is not None:
            return v
        k = int(np.argmin((P[:, 0] - x) ** 2 + (P[:, 1] - y) ** 2))
        return float(P[k, 2])
    return f
