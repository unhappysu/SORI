#!/bin/sh
# SORI 문서 3종 PDF 만들기(xelatex 2회 — 목차·참조 반영). 사용: sh build.sh [파일.tex ...]
# 판 번호는 sori/__init__.py 의 __version__ 을 읽어 version.tex(\soriversion)로 씀 — 매뉴얼 판 = 프로그램 판
# ※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
cd "$(dirname "$0")" || exit 1
v=$(sed -n 's/^__version__ = "\([^"]*\)".*/\1/p' ../../sori/__init__.py)
[ -n "$v" ] || { echo "실패: ../../sori/__init__.py 에서 __version__ 을 읽지 못함"; exit 1; }
printf '%%%% 자동 생성(build.sh) — sori/__init__.py 의 __version__. 손으로 고치지 말 것\n\\def\\soriversion{%s}\n' "$v" > version.tex
echo "판 v$v"
[ $# -eq 0 ] && set -- SORI_앱개발노트.tex SORI_User_Manual.tex SORI_Technical_Manual.tex
for t in "$@"; do
  [ -f "$t" ] || continue
  xelatex -interaction=nonstopmode -halt-on-error "$t" >/dev/null && xelatex -interaction=nonstopmode -halt-on-error "$t" >/dev/null \
    && echo "OK  ${t%.tex}.pdf" || { echo "실패 $t (로그: ${t%.tex}.log)"; exit 1; }
done
