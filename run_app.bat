@echo off
REM 도로 소음지도 웹 화면 실행 (Windows: 더블클릭)
REM ※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
cd /d "%~dp0"
python -m streamlit run app.py
pause
