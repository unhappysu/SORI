# SORI ↔ SoundPLAN 9.1 (RLS-90) 비교

※ Claude(Anthropic의 AI)를 활용하여 작성하였음. 2026-11-05.

학회 확장요약문의 "동일한 도로 및 수음점 조건에서 SoundPLAN 9.1 RLS-90 예측 결과와 비교"를 하기 위한 폴더. **두 프로그램에 같은 입력을 넣었을 때 결과가 얼마나 같은지**를 본다. 입력이 다르면 차이가 프로그램 차이인지 입력 차이인지 나눌 수 없으므로, SORI 가 쓴 입력을 그대로 SoundPLAN 에 넣는 것을 기본으로 한다.

※ 2026-11-06 다시 계산·내보냄: 차도 면 지형 보정(`model.road_surface: flatten`, 기본값)과, 교통량 자료가 없는 도로의 M·p 를 RLS-90 표 3(독일 기준, 국내와 다를 수 있음)으로 채우는 규칙(TECHNICAL_NOTES 5a-15)을 반영. 이전(11-05) 대비 점 수음점 LrT −0.36 ~ +0.55 dB(평균 +0.01), LrN −1.02 ~ +0.03 dB(평균 −0.25). SoundPLAN 에는 새 `soundplan_export/` 를 넣어야 한다.

※ 2026-11-08 다시 계산·내보냄: 교통량 자료가 없는 골목길(1차로, 이 범위 1,168개 객체)을 음원에서 뺌(`model.minor_roads: exclude`, TECHNICAL_NOTES 5a-16). 내보내기의 도로에서도 빠진다. 11-06 대비 점 LrT −4.99 ~ 0.00 dB(사가정로 변 10·15 m 점 −0.12 ~ 0.00, 골목 쪽 뒤편 BN20 −5.0·AS80 −3.6·AN50 −2.1 dB), LrN −1.85 ~ 0.00 dB. SoundPLAN 에서도 같은 골목길을 빼야 비교가 맞다.

※ **2026-11-11 SoundPLAN 숫자 비교 완료(1차)** — 조형찬 SoundPLAN 모델의 숫자 결과(11-09 받음: 격자 701점 2 m, 수음점 2곳)와 같은 좌표·높이로 비교: `sp_compare_1109/README.md`. 조건을 SoundPLAN 에 맞춤(RLS-19, 서 30·동 50 km/h 방향별 속도, 사가정로 3 + 2차로 차로별 음원선, 교통량 같다고 봄). SoundPLAN 기본 설정 가정(노면 기준 노면, 반사 2차)에서 수음점 **+0.30·−0.01 dB**, 사가정로 영향권 365점 평균 +0.08 dB·SD 2.2 dB. 주변 도로(SORI 만 있음)는 비교 한계, 노면·반사 차수·남측 큰 건물은 가정·미확인으로 정리(사용자 결정 2026-11-11). **SoundPLAN 설정을 확인하지 못한 가정 기반 비교**이므로 "일치"·"동일 조건"으로 쓰지 않으며, 격자 전체는 RMSE 3.80 dB·±2 dB 안 57 %다(모든 조건의 결과와 읽는 법: `sp_compare_1109/README.md`). 속도만 바꾼 영향은 `dir_speed_30_50/README.md`.

## 폴더

| 파일 | 내용 |
|---|---|
| `config.yaml` | SORI 비교 설정: `propagation_method: rls90`, 반사 1차·D_E −1 dB, 격자 240 × 160 m(사가정로 측정점 주변) 2.5 m 간격·높이 4 m, 벽면 수음점 끔 |
| `traffic.csv` | 교통량 — `field_measurement/2026-10-22/model/AVG_v30` 과 같음(사가정로 현장 계수, 30 km/h) |
| `points.csv` | 점 수음점 21개: 측정점 P1·P2·P2c(지면 위 1.3 m) + 횡단 측선 A·B(사가정로 중심선에서 남북 10~80 m, 4 m) |
| `output/` | SORI 결과(`grid_result.csv`, `point_result.csv`, `path_terms.csv`, 지도) |
| `output/soundplan_export/` | SoundPLAN 에 넣을 입력(셰이프파일)과 `계산조건.md` |

## 절차

### 1. SORI 계산 (약 25초)

```bash
.venv/bin/python -m sori all validation/soundplan -c validation/soundplan/config.yaml
```

### 2. SoundPLAN 입력 내보내기

```bash
.venv/bin/python tools/export_soundplan.py validation/soundplan/config.yaml
```

`output/soundplan_export/` 의 `roads.shp`(교통량·속도·노면·차로·음원선 위치), `buildings.shp`, `signals.shp`, `receivers.shp`, `calc_area.shp`, `terrain_points.csv` 를 SoundPLAN Geo-Database 로 가져온다(좌표계 EPSG:5186). 셰이프파일 글자는 UTF-8.

### 3. SoundPLAN 설정 — `계산조건.md` 표대로

결과에 크게 영향을 주는 것(먼저 확인):

1. 계산 기준 Road = **RLS-90**
2. **반사 차수 1**, 건물 반사 손실 **1 dB**
3. 도로별 **시간당 교통량 M**(DTV 아님)과 **화물차 비율 p**, 속도, 노면 보정
4. **신호교차로(K)**: `signals.shp` 의 교차점만
5. 격자: `calc_area.shp` 영역, 2.5 m 간격, 높이 4 m / 수음점 높이(지면 위)
6. 탐색 반경 300 m(간선)·150 m(이면도로) 이상

### 4. SoundPLAN 결과 내보내기

- 수음점 표(Receiver table): 이름·LrD·LrN(소수 둘째 자리까지) → `.txt`/`.csv`/`.xlsx`
- 격자(Grid noise map): x, y, LrD, LrN 표 또는 ESRI ASCII(`.asc`, 시간대별 파일)
- 도로 방출표(Emission, 도로별 L_m,E) — `계산조건.md` 의 L_m,E 와 먼저 비교

### 5. 비교

```bash
.venv/bin/python tools/compare_soundplan.py --sori validation/soundplan/output --sp-points <수음점 파일> --sp-grid <격자 파일>
```

`output/soundplan_compare/` 에 `비교요약.md`(n, 평균 차이, SD, RMSE, MAE, 최대 차이, ±0.5/1/2/3 dB 안 비율, r, 회귀 기울기, Bland–Altman 일치 한계), 맞춘 값 CSV, 그림(산점도, Bland–Altman, 차이 분포, 차이 지도)이 생긴다. 열 이름·구분자·소수 쉼표는 자동 인식하고, 안 되면 `--day-col` 등으로 지정. `.asc` 는 `--asc-period day|night`. SoundPLAN 좌표가 평행 이동돼 있으면 `--offset dx dy`.

### 6. 차이를 읽는 순서

1. **음원**: 도로별 L_m,E 가 같은가(교통량·p·속도·노면 입력 확인)
2. **수음점**: 횡단 측선의 거리별 차이 — 가까운 점이 같고 먼 점만 다르면 지면 감쇠(D_BM)·탐색 반경, 건물 뒤 점만 다르면 차폐(D_z)·건물 높이
3. **격자**: 차이 지도에서 도로변·건물 뒤·교차로(K 계단) 위치별로 확인

## 이미 있는 SoundPLAN 모델(조형찬)을 그대로 쓸 경우

받은 SoundPLAN 지도(LrD, 사가정로 측정점 주변)는 다른 사람이 만든 모델이라 입력이 SORI 와 다를 수 있다. 그 모델로 비교하려면 다음을 받아 SORI 입력을 그쪽에 맞춘다.

- 계산 기준(RLS-90 확인), 반사 차수·반사 손실, 탐색 반경, 격자 영역·간격·높이
- 도로별 교통량(M 또는 DTV와 시간 계수), p, 속도, 노면, 차로 폭·음원선 위치, 신호교차로 지정
- 건물 높이 기준(층수 × 높이?), 지형(DGM) 원자료
- 숫자 결과(수음점 표, 격자 표) — 지도 그림만으로는 비교 불가

## 아직 확인 못 한 것

- 실제 SoundPLAN 9.1 내보내기 파일의 열 이름·형식(도구는 흔한 이름과 독일어판 소수 쉼표를 읽도록 만들었고, 가짜 파일로만 시험함 — `tests/test_soundplan_tools.py`)
- SoundPLAN 이 UTF-8 셰이프파일의 한글 도로명을 제대로 읽는지
- SoundPLAN 의 RLS-90 구간 나누기·h_m 계산 방식(원문 해석 차이가 있을 수 있음)
