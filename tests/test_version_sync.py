# -*- coding: utf-8 -*-
"""매뉴얼 판 번호 = 프로그램 판 번호(sori.__version__) 확인. docs/manual/build.sh 가 version.tex 를 다시 만들므로
이 시험이 실패하면 build.sh 를 돌리고 version.tex 를 함께 커밋한다.
※ Claude(Anthropic의 AI)를 활용하여 제작하였음."""
import re
from pathlib import Path

import sori

ROOT = Path(__file__).resolve().parents[1]
MAN = ROOT / "docs" / "manual"


def test_manual_version_matches_program():
    m = re.search(r"\\def\\soriversion\{([^}]*)\}", (MAN / "version.tex").read_text(encoding="utf-8"))
    assert m and m.group(1) == sori.__version__


def test_manuals_use_version_macro():
    """표지·머리글에 판 번호를 손으로 쓰지 않고 \\soriversion 을 씀"""
    for name in ("SORI_User_Manual.tex", "SORI_Technical_Manual.tex"):
        t = (MAN / name).read_text(encoding="utf-8")
        assert "\\input{version}" in t
        assert re.search(r"\\def\\docheader\{SORI v\\soriversion", t)
        assert "{\\sffamily\\Huge\\bfseries\\color{navy} SORI v\\soriversion" in t


def test_release_note_exists_for_version():
    assert (ROOT / "docs" / "release" / f"v{sori.__version__}.md").exists()
