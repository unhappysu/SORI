#!/bin/sh
# 도로 소음지도 웹 화면 실행 (Mac/Linux)
# ※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
cd "$(dirname "$0")"
python3 -m streamlit run app.py
