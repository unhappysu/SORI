# noisemap — RLS-19 + ISO 9613-2 도로교통소음 3D 소음지도

> ※ 본 소프트웨어와 문서는 Claude(Anthropic의 AI)를 활용하여 제작하였음. 계산 결과의 검토·검증 책임은 사용자에게 있음.

| 구분 | 적용 기준 |
|---|---|
| 음원(방출) | RLS-19 (FGSV 052, Ausgabe 2019, Korrektur 04.03.2020) 원문 식 (4)~(9), 표 2~5 |
| 전파(감쇠) | ISO 9613-2:1996 식 (3)~(20), 표 2~4 / 대기흡수계수 ISO 9613-1:1993 |
| 수음점 | 격자(여러 높이 = 3D 격자), 건물 파사드(층별, 외벽 앞 5 cm) |

## 0. 웹 화면으로 쓰기 (가장 쉬운 방법)

명령어 없이 브라우저 화면에서 4단계로 진행합니다.

1. 설치(최초 1회): `pip install -r requirements.txt`
2. 실행: **Windows는 `run_app.bat` 더블클릭**, Mac/Linux는 `./run_app.sh` (또는 `streamlit run app.py`)
3. 브라우저가 열리면 왼쪽 메뉴의 단계를 차례로 진행
   - **1. 지도 자료**: 수치지도 zip 업로드(또는 폴더 경로 입력, 또는 '예제 자료로 체험')
   - **2. 위치·범위**: 지도에서 교차로를 클릭, 범위는 슬라이더로
   - **3. 교통량**: 표에 아는 값만 입력(하루 통행량, 트럭 비율, 제한속도, 노면). 비우면 표준값·가정값 사용. 교차로 종류 확인
   - **4. 계산·결과**: '빠른 미리보기 / 표준 / 정밀' 중 선택 → 계산 시작 → 지도 위 결과, 그림, 결과 zip 내려받기
4. 결과와 사용한 설정은 `projects/프로젝트이름/` 에 저장됩니다. 그 안의 `config.yaml`로 명령행에서 같은 계산을 재현할 수 있습니다.

배경지도(OpenStreetMap)는 인터넷이 필요하며, 계산 자체는 내 컴퓨터에서 이루어집니다.

## 1. 설치

```bash
pip install -r requirements.txt          # Anaconda 사용 시: conda install -c conda-forge geopandas rasterio numba pyyaml
```
numba가 없어도 동작하지만 수십~수백 배 느려집니다.

## 2. 빠른 시작 — 명령행 (가상 데이터로 10분 안에 확인)

```bash
python tools/make_synthetic_data.py examples/synthetic      # 수치지도 형식의 가상 자료 생성
cd examples/synthetic
python -m noisemap all -c config.yaml                        # 모델 구축 → 계산 → 그림
```
(패키지 폴더 밖에서 실행할 때는 `PYTHONPATH`에 이 폴더를 추가하거나 `pip install -e .` 대신 폴더를 복사해 사용)

## 3. 실제 지역 계산 순서

1. **작업 폴더 만들기**: `python -m noisemap init 전농사거리` → `config_example.yaml`을 `config.yaml`로 이름 변경
2. **수치지도 넣기**: 국토정보플랫폼에서 받은 수치지형도 v2.0(SHP) 압축을 `data/map/` 아래에 도엽별로 풀기 (하위 폴더 자동 탐색, 여러 도엽 자동 병합)
3. **DEM(선택)**: `input.dem.path`에 GeoTIFF/IMG 경로. 좌표계는 파일에서 읽어 자동 변환
4. **계산 중심 지정**: `receivers.center: [x, y]` (EPSG:5186 좌표)
5. **교통량 서식 만들기**: `python -m noisemap template -c config.yaml` → `traffic_template.csv` 생성 → 값 입력 후 `traffic.csv`로 저장
6. **교차로 확인(선택)**: `traffic_template_junctions_auto.csv`의 자동 판정(type)을 검토·수정 후 `input.junctions.path`로 지정
7. **실행**: `python -m noisemap all -c config.yaml`
8. **결과 확인**: `output/` 폴더 (아래 6절)

## 4. 입력 서식

### 4.1 교통량 `traffic.csv` (UTF-8 또는 CP949)

| 열 | 필수 | 설명 |
|---|---|---|
| key | ● | 도로명(예: 사가정로) 또는 수치지도 UFID |
| key_type | | `name`(기본) 또는 `ufid` — UFID 행이 도로명 행보다 우선 |
| road_type | | RLS-19 표 2: `autobahn`, `bundesstrasse`, `landesstrasse`, `gemeinde` (빈칸 값 보충용) |
| DTV | △ | 일평균 교통량(대/일, 양방향 합) — DTV 또는 M_day·M_night 중 하나 필수 |
| M_day, M_night | △ | 시간당 교통량(대/h, 양방향 합). 비우면 DTV × 표 2 계수. **DTV 없이 한쪽만 입력하면** 기본은 오류(문제 행을 모두 나열) — 설정 `input.traffic.missing_m: estimate` 이면 표 2 의 주·야 계수 비율로 보충(가정값, `M_*_from = est_from_…`, 서울 간선에서는 야간을 과소 추정할 수 있음) |
| p1_day … p2_night | | Lkw1(중형화물)·Lkw2(대형화물·버스·이륜) 비율 %. 비우면 표 2 표준값 |
| v_pkw, v_lkw | | 차종별 속도 km/h (30 미만은 30 적용, RLS-19 2장) |
| surface | | 노면: `ref`, `AC11`, `SMA5_8`, `SMA8_11`, `PA11`, `PA8`, `Waschbeton`, `LGA`, `LOA`, `SMA_LA8`, `DSH_V5`, `pflaster_even`, `pflaster_other` |
| note | | 출처 메모(보고서용) |
| basis | | 교통량 근거: `measured`(실측) 또는 `assumed`(가정). 빈칸이면 unspecified. `traffic_used.csv`의 `traffic_basis`로 기록 |
| dir_split_fwd | | 디지타이즈 방향 교통량 비율(0~1). **UFID 행이고 dir_basis = measured 일 때만 적용**, 그 밖은 0.5(RLS-19 3.3.1 기본). 일방통행은 무시 |
| dir_basis | | 방향별 비율의 근거: `measured` 또는 `assumed`. 적용 여부는 `traffic_used.csv`의 `dir_split_src` |

교통량 파일에 없는 도로는 `fallback_dtv_by_lanes`(차로수별 가정값)를 쓰고, `output/traffic_used.csv`의 `traffic_src=fallback`으로 표시됩니다.

### 4.2 교차로 `junctions.csv` (선택)
`x, y, type` — type은 `signal`(KKT 3), `roundabout`(2), `other`(0). 반경 30 m 안의 자동 교차점 군집에 적용됩니다.

### 4.3 다공성 지면 (선택)
녹지 폴리곤(SHP/GPKG)을 `input.ground.soft_polygons`에 지정하면 그 범위의 G = `soft_G`(기본 1).

### 4.4 도로별 확인값 `roads_override.csv` (선택)
지도·항공사진으로 확인한 도로 객체별 값을 `input.road_override.path`에 지정합니다(서식: `templates/roads_override_example.csv`).

| 열 | 설명 |
|---|---|
| ufid | 수치지도 UFID(필수, 열 이름 대소문자 무관). 그 밖의 열(도로명·좌표 등)은 무시 |
| lanes_fwd, lanes_bwd | 디지타이즈 방향 / 반대 방향 차로 수(일방통행은 lanes_bwd 빈칸 또는 0) |
| oneway_dir | 일방통행만: +1 = 디지타이즈 방향이 진행방향, −1 = 반대 |
| lanes_basis, oneway_dir_basis | `checked`(확인) 또는 `assumed`(가정). 빈칸이면 unspecified |
| note | 확인 출처 메모 |

방향별 차로 수(`lanes_fwd`·`lanes_bwd`, 양방향은 둘 다 1 이상일 때)는 음원선 위치(RLS-19 그림 6)에 반영됩니다(`output/source_lines.csv`의 `lane_rule = override`). 일방통행 `oneway_dir = −1`이면 음원선을 진행방향 우측(디지타이즈 기준 좌측)에 두고 종단경사 부호를 반대로 씁니다(`source_lines.csv`의 `oneway_dir_used`). 값이 없으면 +1(디지타이즈 방향 = 진행방향, 가정). 읽은 값과 경고는 `output/road_override_used.csv`, `build_info.json`의 `road_override`.

## 5. 주요 설정 (config.yaml)

| 항목 | 기본값 | 의미 |
|---|---|---|
| `propagation.bands_hz` / `spectrum_dBA` | [500] / [0] | 500 Hz 단일대역(ISO 9613-2 1절 비고 1). 옥타브 스펙트럼을 넣으면 8대역 계산 |
| `input.ground.method` | general | ISO 7.3.1 일반법. `alternative`는 7.3.2(식 10, 다공성 지면 전제) |
| `propagation.reflection.rho` | 0.8 | ISO 표 4 '창이 있는 건물 벽' |
| `max_order_grid` / `max_order_facade` | 1 / 2 | 반사 차수 (RLS-19 3.6은 2차까지) |
| `input.traffic.oneway_name_traffic` | apply | 도로명 교통량(양방향 합)을 같은 이름의 일방통행 객체에도 배정(`apply`) 또는 차로수 가정값 사용(`fallback`, `traffic_src = fallback_oneway`). UFID 지정 교통량은 항상 우선 |
| `model.source_ground` | dtm_at_source | 음원 높이(지면 + 0.5 m)의 지면: `dtm_at_source` = 음원점 위치 DTM, `road_profile` = 도로중심선 종단 고도(경사 산정과 같은 평활 DTM). road_profile 은 노면이 주변 DTM 보다 낮은 곳에서 음원 옆 지형이 차폐로 잡힐 수 있음(TECHNICAL_NOTES) |
| `model.odd_lane_rule` | ceil | 확인값이 없는 홀수 차로 양방향 도로의 방향별 차로 수. `ceil` = 양쪽 ⌈n/2⌉(5 → 3+3), `major_fwd` = 디지타이즈 방향 ⌈n/2⌉·반대 ⌊n/2⌋(5 → 3+2) |
| `model.lane_width` | auto | 음원선 위치 계산용 차로폭. `auto` = 도로폭/차로수를 `lane_width_clip`(2.75~3.5 m)로 제한, 도로폭 없으면 3.25 m. 숫자 = 고정값(m) |
| `receivers.grid.heights` | [4.0] | 3D 격자 높이 목록 |
| `propagation.max_dist_main/minor` | 300 / 150 m | 음원 탐색 거리 |

## 6. 출력 (`output/`)

| 파일 | 내용 |
|---|---|
| `grid_result.csv`, `grid_LrT_h4m.tif` … | 격자 결과(높이별), GeoTIFF는 QGIS에서 바로 열림 |
| `facade_result.csv` | 파사드 결과(층별). `LrT_cmp`는 RLS-19 3.1 올림값, `LrT_direct/refl1/refl2`는 성분 |
| `HQ_*.png / .pdf` | **보고서·발표용 A3 지도(300 dpi + 벡터 PDF)**: 격자 소음지도, 기준 초과 지도, 벽면 색 띠 지도, 건물별 평가도 (주·야) |
| `noise3d.html` | **3D 웹 뷰어**: 지형 위 격자 소음지도 + 건물 벽면 층별 소음. 더블클릭해 브라우저로 열기(three.js 내장, 인터넷 없이 동작) |
| `*.png` | 확인용 간이 그림(격자, 파사드 평면, 파사드 3D) |
| `sources.csv` | 음원 점별 LW′, D_K, D_refl, 경사, 교차로 거리 등 |
| `source_lines.csv` | 도로 객체별 음원선 배치 근거: 사용한 방향별 차로 수, 근거(`lane_rule`: override·map_half·ceil·major_fwd·oneway_map·single_centre), 차로폭, 중심선 기준 오프셋 |
| `segment_check.csv` | 구간 길이 진단: RLS-19 3.2 주석(경험칙) l ≤ s/2 를 어기는 음원이 있는 수음점만(위반 음원 수, 최소 s/l). 결과값에는 영향 없음. 요약은 `run_info.json` 의 `segment_check` |
| `traffic_used.csv`, `junctions_used.csv` | 실제 사용된 교통량·교차로 판정 (보고서 근거자료) |
| `build_info.json`, `run_info.json` | 모델 규모, 가정값 사용 수, 계산 조건 기록 |

## 7. 계산 시간
단일 코어 기준 파사드 수음점 1개당 수 ms(가상자료) ~ 수십 ms(도심). 수만 개 수음점은 수 분~수십 분이 걸리며 numba가 CPU 코어를 모두 사용합니다. 빠른 시험은 `grid.step`을 10 m로, `max_order_facade`를 1로 낮추세요.

## 8. 검증
```bash
python -m pytest -q tests        # 38개 시험: 원문 식 재계산, 해석해(직선 음원), 차폐·1·2차 반사 수계산 비교, 입력·음원 배치 회귀시험
```
기본값에서 결과가 바뀌지 않았는지는 `python tools/check_baseline.py`(전농사거리 기준선 `baseline/` 과 SHA-256 비교, 약 1분)로 확인합니다.
자세한 내용은 `TECHNICAL_NOTES.md`, 작업 경과와 남은 일은 `docs/작업기록_2026-10-15_17.md`.
