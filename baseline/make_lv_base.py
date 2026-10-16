# -*- coding: utf-8 -*-
"""기준선 lv_base.csv 생성: 계산 중심 반경 R(기본 150 m) 안의 격자(높이 4 m)·파사드 수음점 LrT/LrN.
noisemap 의 run._run(직접음+반사, 설정의 반사 차수)을 그대로 사용함. 결과값을 바꾸는 처리는 없음.
사용: PYTHONPATH=<noisemap 코드 경로> python make_lv_base.py <모델 output 폴더> <config.yaml> <출력 csv> [R]
※ Claude(Anthropic의 AI)를 활용하여 제작하였음."""
import sys

import numpy as np
import pandas as pd

from noisemap import config as C, run as RUN

out, cfgp, dst = sys.argv[1], sys.argv[2], sys.argv[3]
rsel = float(sys.argv[4]) if len(sys.argv) > 4 else 150.0
cfg = C.load(cfgp)
cx, cy = C.center(cfg)
d, S, F, FR = RUN.load_model(out)
X, Y = np.meshgrid(d["gx"], d["gy"])
m = ~d["ginside"].ravel()
gx, gy, gz = X.ravel()[m], Y.ravel()[m], d["gz"].ravel()[m]
h = float(cfg["receivers"]["grid"]["heights"][0])
sg = np.hypot(gx - cx, gy - cy) <= rsel
sf = np.hypot(FR.x - cx, FR.y - cy).values <= rsel
rf = cfg["propagation"]["reflection"]
G = RUN._run(cfg, d, S, F, gx[sg], gy[sg], gz[sg] + h, gz[sg], np.full(sg.sum(), -1, np.int64),
             np.full(sg.sum(), -1, np.int64), int(rf["max_order_grid"]), "grid")
Fa = RUN._run(cfg, d, S, F, FR.x.values[sf], FR.y.values[sf], FR.z.values[sf], FR.zg.values[sf],
              FR.bid.values[sf].astype(np.int64), FR.fid.values[sf].astype(np.int64),
              int(rf["max_order_facade"]), "facade")
A = pd.DataFrame(dict(kind="grid", x=gx[sg], y=gy[sg], z=gz[sg] + h, floor=np.nan, **G))
B = pd.DataFrame(dict(kind="facade", x=FR.x.values[sf], y=FR.y.values[sf], z=FR.z.values[sf],
                      floor=FR.floor.values[sf], **Fa))
pd.concat([A, B]).to_csv(dst, index=False, float_format="%.6f")
print(f"grid {sg.sum()}, facade {sf.sum()} -> {dst}")
