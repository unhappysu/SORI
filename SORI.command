#!/bin/bash
# SORI 개발용 실행 (macOS: Finder 에서 더블클릭)
# 저장소의 .venv 가상환경을 활성화한 뒤 데스크톱 화면(sori_gui.py)을 실행한다.
# .venv 가 없으면: python3 -m venv .venv && .venv/bin/pip install -r requirements-lock.txt
# ※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
cd "$(dirname "$0")" || exit 1
if [ ! -f .venv/bin/activate ]; then
    echo "가상환경(.venv)이 없습니다. 다음 명령으로 먼저 만드세요:"
    echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements-lock.txt"
    read -r -p "엔터를 누르면 창을 닫습니다."
    exit 1
fi
source .venv/bin/activate
python sori_gui.py "$@"
