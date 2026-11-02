#!/bin/sh
# SORI 문서 3종 PDF 만들기(xelatex 2회 — 목차·참조 반영). 사용: sh build.sh [파일.tex ...]
# ※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
cd "$(dirname "$0")" || exit 1
[ $# -eq 0 ] && set -- SORI_앱개발노트.tex SORI_User_Manual.tex SORI_Technical_Manual.tex
for t in "$@"; do
  [ -f "$t" ] || continue
  xelatex -interaction=nonstopmode -halt-on-error "$t" >/dev/null && xelatex -interaction=nonstopmode -halt-on-error "$t" >/dev/null \
    && echo "OK  ${t%.tex}.pdf" || { echo "실패 $t (로그: ${t%.tex}.log)"; exit 1; }
done
