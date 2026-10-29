# -*- coding: utf-8 -*-
"""
결과 파일명 규칙: {프로젝트}_{시나리오}-{방법}_{시간대}_{grid|facade|section}[부가].확장자
  - 방법: propagation_method 에서 자동(iso9613_2 → ISO, rls19 → RLS). 사용자가 직접 쓰지 않음
  - 시간대: LrT → 주간(06~22시), LrN → 야간(22~06시), 두 시간대를 함께 담은 표(CSV) → 주야
  - 부가: 격자 높이(_h4m), 지도 종류(_exceed, _building)
  예) 전농사거리_현황-ISO_주간_grid_h4m.png, 전농사거리_현황-RLS_주야_facade.csv
기존 고정 이름 파일(grid_result.csv 등)은 웹 화면·비교 스크립트가 읽으므로 그대로 두고, 규칙 이름은 사본으로 만듦.
section(단면) 지도는 미구현 — 규칙에 자리만 둠.
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import os
import re
import shutil

METHOD_TAG = {"iso9613_2": "ISO", "rls19": "RLS"}
PERIOD = {"LrT": "주간", "LrN": "야간", None: "주야"}
KINDS = ("grid", "facade", "section")


def _part(s):
    """칸 안의 글자 정리: 칸 구분자 '_' 와 파일명에 쓸 수 없는 글자, 공백은 '-' 로"""
    s = re.sub(r'[\\/:*?"<>|_\s]+', "-", str(s).strip())
    return s.strip("-") or "이름없음"


def enabled(cfg):
    return bool(cfg["project"].get("file_naming", True))


def rule_name(cfg, kind, period=None, extra=""):
    """확장자 없는 규칙 이름. kind: grid | facade | section, period: LrT | LrN | None(주야)"""
    if kind not in KINDS:
        raise ValueError(f"파일명 규칙 종류는 {KINDS} 중 하나")
    from .propagation import get_model
    p = cfg["project"]
    return (f"{_part(p['name'])}_{_part(p.get('scenario') or '현황')}-{METHOD_TAG[get_model(cfg).name]}"
            f"_{PERIOD[period]}_{kind}{extra}")


def copy_as(cfg, src, kind, period=None, extra=""):
    """src 를 같은 폴더에 규칙 이름(같은 확장자)으로 복사. 꺼져 있으면 None"""
    if not enabled(cfg):
        return None
    d, fn = os.path.split(src)
    dst = os.path.join(d, rule_name(cfg, kind, period, extra) + os.path.splitext(fn)[1])
    shutil.copyfile(src, dst)
    return dst
