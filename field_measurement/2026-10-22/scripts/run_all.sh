#!/bin/sh
# 사가정로 현장 소음측정(2026-10-22) 분석 전체 재현. AI(Claude)를 활용하여 제작하였음.
# 저장소 루트에서 실행: sh field_measurement/2026-10-22/scripts/run_all.sh [--skip-map]
set -e
D=field_measurement/2026-10-22/scripts
python3 $D/analyze.py          # 1차: 지표·후보 구간
python3 $D/wav_check.py        # 녹음 스펙트로그램·순음 지표·사이렌 시계 차이
python3 $D/analyze.py          # 2차: 순음 지표로 지점2 경적 후보 선별
if [ "$1" = "--skip-map" ]; then
  python3 $D/model_compare.py --skip-map
else
  python3 $D/model_compare.py --only-map   # 소음지도(격자·파사드, 약 3분)
  python3 $D/model_compare.py --skip-map   # 점 비교·민감도·그림
fi
