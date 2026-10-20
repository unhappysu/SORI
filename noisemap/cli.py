# -*- coding: utf-8 -*-
"""명령행 실행
  python -m noisemap init      [폴더]            예제 설정·교통량 서식 복사
  python -m noisemap template  -c config.yaml    도로명/UFID 교통량 입력 서식 + 자동 교차로 목록 생성
  python -m noisemap build     -c config.yaml    계산 모델 구축
  python -m noisemap run       -c config.yaml [--grid] [--facade]
  python -m noisemap plot      -c config.yaml
  python -m noisemap all       -c config.yaml    build → run → plot
※ Claude(Anthropic의 AI)를 활용하여 제작하였음."""
import argparse
import logging
import os
import shutil
import sys

from . import AI_NOTICE, __version__
from . import config as C

HERE = os.path.dirname(os.path.abspath(__file__))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="noisemap", description=f"RLS-19 + ISO 9613-2 소음지도 v{__version__}. {AI_NOTICE}")
    ap.add_argument("cmd", choices=["init", "template", "build", "run", "plot", "all"])
    ap.add_argument("folder", nargs="?", default=".")
    ap.add_argument("-c", "--config", default="config.yaml")
    ap.add_argument("--grid", action="store_true")
    ap.add_argument("--facade", action="store_true")
    ap.add_argument("--points", action="store_true", help="점 수음점만(receivers.points.path)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    log = logging.getLogger("noisemap")
    log.info(AI_NOTICE)
    if a.cmd == "init":
        os.makedirs(a.folder, exist_ok=True)
        for fn in ("config_example.yaml", "traffic_example.csv", "junctions_example.csv"):
            src = os.path.join(HERE, "templates", fn)
            dst = os.path.join(a.folder, fn)
            if not os.path.exists(dst):
                shutil.copy(src, dst)
                log.info("생성: %s", dst)
        return 0
    cfg = C.load(a.config)
    which = tuple(w for w, f in (("grid", a.grid), ("facade", a.facade), ("points", a.points)) if f) \
        or ("grid", "facade", "points")
    if a.cmd == "template":
        from . import build
        out = os.path.join(os.path.dirname(cfg["_config_path"]), "traffic_template.csv")
        p1, p2 = build.template(cfg, out)
        log.info("교통량 서식: %s\n자동 교차로 목록: %s (필요시 type 수정 후 input.junctions.path 로 지정)", p1, p2)
        return 0
    if a.cmd in ("build", "all"):
        from . import build
        build.main(cfg)
    if a.cmd in ("run", "all"):
        from . import run
        run.main(cfg, which)
    if a.cmd in ("plot", "all"):
        from . import plots, maps_hq, viewer3d
        for f in plots.main(cfg):
            log.info("그림: %s", f)
        for f in maps_hq.main(cfg):
            log.info("고품질 지도: %s", f)
        out = cfg["project"]["output_dir"]
        if os.path.exists(os.path.join(out, "facade_result.csv")) and os.path.exists(os.path.join(out, "grid_result.csv")):
            log.info("3D 뷰어: %s", viewer3d.main(cfg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
