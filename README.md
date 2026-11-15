# SORI — RLS-19 + ISO 9613-2 도로교통소음 3D 소음지도(SORI Map)

**SORI** (Simulation of Outdoor Road-noise Impact) v0.3 — 도로교통소음 예측 소프트웨어(판 번호 규칙·변경은 9절). 소음지도 기능·결과물은 **SORI Map** 이라 부른다. Python 패키지·명령은 `sori` (`python -m sori …`).

> ※ 본 소프트웨어와 문서는 Claude(Anthropic의 AI)를 활용하여 제작하였음. 계산 결과의 검토·검증 책임은 사용자에게 있음.

| 구분 | 적용 기준 |
|---|---|
| 음원(방출) | RLS-19 (FGSV 052, Ausgabe 2019, Korrektur 04.03.2020) 원문 식 (4)~(9), 표 2~5 |
| 전파(감쇠) | 설정 `propagation_method` 로 선택 — `iso9613_2`(기본): ISO 9613-2:1996 식 (3)~(20), 표 2~4 / 대기흡수계수 ISO 9613-1:1993. "RLS-19 음원 + ISO 9613-2 전파"는 어느 표준도 규정하지 않은 프로젝트 구성 방법 · `rls19`: RLS-19 3.5 식 (11)~(17), 3.6·표 8 |
| 수음점 | 격자(여러 높이 = 3D 격자), 건물 파사드(층별, 외벽 앞 5 cm) |

## 0. 웹 화면으로 쓰기 (가장 쉬운 방법)

명령어 없이 브라우저 화면에서 4단계로 진행합니다.

1. 설치(최초 1회): `pip install -r requirements.txt`
2. 실행: **Windows는 `run_app.bat` 더블클릭**, Mac/Linux는 `./run_app.sh` (또는 `streamlit run app.py`)
3. 브라우저가 열리면 왼쪽 메뉴의 단계를 차례로 진행
   - **1. 지도 자료**: 수치지도 zip 업로드(또는 폴더 경로 입력, 또는 '예제 자료로 체험')
   - **2. 위치·범위**: 지도에서 교차로를 클릭, 범위는 슬라이더로
   - **3. 교통량**: 표에 아는 값만 입력(하루 통행량, 트럭 비율, 제한속도, 노면). 비우면 표준값·가정값 사용. 교차로 종류 확인
   - **4. 계산·결과**: '빠른 미리보기(반사 1번, 벽면 없음) / 표준(반사 1번) / 정밀(격자·벽면 반사 3번, 국내 고시 별표 2 제8호)' 중 선택 → 계산 시작 → 지도 위 결과, 그림, 결과 zip 내려받기
4. 결과와 사용한 설정은 `projects/프로젝트이름/` 에 저장됩니다. 그 안의 `config.yaml`로 명령행에서 같은 계산을 재현할 수 있습니다.

배경지도(OpenStreetMap)는 인터넷이 필요하며, 계산 자체는 내 컴퓨터에서 이루어집니다.

## 0.1 더블클릭 앱으로 쓰기 (SORI.app · SORI.exe)

Python 설치 없이 쓰는 데스크톱 앱입니다. 준비된 설정 파일(`config.yaml`)로 계산합니다. 지도 클릭으로 위치를 고르는 기능은 웹 화면(0절)에만 있습니다.

| 구분 | 받는 파일 | 실행 | 확인 상태 |
|---|---|---|---|
| macOS (Apple Silicon) | `SORI-macos.zip` → 풀면 `SORI.app` | `SORI.app` 더블클릭 | macOS 27.0.1(arm64)에서 빌드·실행 확인, Actions macOS 러너에서도 빌드·점검 통과 |
| Windows 10/11 (64비트) | `SORI-windows.zip` → 풀면 `SORI` 폴더 | 폴더 안 `SORI.exe` 더블클릭 | GitHub Actions Windows 러너(Windows Server 2025, x64)에서 빌드하고 `SORI.exe` 의 자가 점검·예제 계산을 화면 없이 실행해 확인. **Windows 에서 화면(GUI)·보안 경고 절차는 미확인** |

앱 파일은 저장소의 **Releases** 페이지(https://github.com/unhappysu/SORI/releases)에서 받습니다(`SORI-버전-macos-arm64.zip`, `SORI-버전-windows-x64.zip`, 해시 `SHA256SUMS.txt`). `v*` 태그를 push 하면 GitHub Actions(`.github/workflows/build.yml`)가 두 OS 앱을 빌드·점검하고 Release 초안을 만들며, 내용을 확인한 뒤 공개합니다. 직접 빌드하는 방법은 아래 "개발자용"을 참고하세요.

**처음 실행할 때 보안 경고 (서명하지 않은 앱)**

- **macOS**: "확인되지 않은 개발자" 또는 "열 수 없음" 경고가 나오면 **시스템 설정 → 개인정보 보호 및 보안**에서 아래쪽의 SORI 항목 옆 **그래도 열기**를 누르고, 다시 열 때 **열기**를 누릅니다. 한 번 허용하면 다음부터는 바로 열립니다.
- **Windows**: "Windows의 PC 보호" 창이 나오면 **추가 정보 → 실행**을 누릅니다. 압축은 반드시 폴더째 푼 뒤 실행하세요(`SORI.exe` 만 꺼내면 실행되지 않습니다).

**화면 사용법**

1. **설정 파일**: `찾아보기…`로 `config.yaml` 을 고릅니다. 처음이면 **예제 불러오기**로 가상 교차로 예제(실제 지역 아님)를 `문서/SORI/예제_가상교차로` 에 복사해 씁니다.
2. **결과 저장 폴더**: 기본값은 설정 파일 옆 `output`. 다른 폴더를 고를 수 있습니다. 앱 폴더 안에는 저장하지 않습니다.
3. **계산 대상**: 격자(소음지도), 건물 벽면(파사드), 점 수음점(설정에 있을 때), 그림·3D 뷰어 만들기. 계산은 주간(LrT)·야간(LrN) 모두 수행하며, **결과 요약 시간대**는 끝난 뒤 기록창에 보여 줄 요약만 고릅니다.
4. **▶ 계산 실행** → 진행 막대와 기록창(경고는 주황색)으로 진행 상황을 봅니다. 처음 한 번은 계산 코드를 컴파일하느라 1분 정도 더 걸립니다.
5. **결과 폴더 열기**로 결과를 확인합니다. 결과 폴더에는 6절의 결과 외에 `config_used.yaml`(사용한 설정 사본)과 `run_conditions.json`(실행 일시·설정 경로·계산 대상·소요 시간·요약·앱 환경)이 함께 저장됩니다.
6. **전농사거리 예제**(Qt 화면): **파일 ▸ 전농사거리 불러오기** 로 설정·교통량·측정점을 `문서/SORI/전농사거리` 에 복사합니다. 수치지도는 들어 있지 않으므로 국토정보플랫폼에서 수치지형도 v2.0 도엽 377051731·32·33·41·42·43·51·52·53 을 받아 그 폴더의 `data/map` 에 넣고(zip 그대로 가능) 다시 누르면 압축을 풀고 설정을 엽니다. 자세한 내용은 `examples/jeonnong/README.md`.
7. **자가 점검**: 좌표 변환(EPSG 5186·5179·4326)과 내장 기준 사례 계산을 기준값과 비교합니다(허용 오차 0.01 dB — 프로젝트 임의 설정). 보고서는 `문서/SORI/selfcheck_report.json`.

**결과 보기 (Qt 화면, 2026-11-01)**

앱은 PySide6(Qt) 화면을 먼저 쓰고, PySide6 가 없으면 위의 단순 화면(tkinter)으로 열립니다. Qt 화면에서는 앱을 켜면 지난번 설정의 결과가 가운데 지도에 바로 보입니다.

- **격자 소음지도**: 보고서용 고품질 지도(`HQ_grid_*.png`)와 같은 그림입니다 — 5 dB 등음선 채움, 간선도로 노면·건물·신호교차로·도로명, 방위표·축척, 범례·계산 조건·주의 문구. 위 도구 줄에서 격자 높이·시간대(주간 LrT / 야간 LrN)를 고르고, 확대(돋보기)·이동 도구로 자세히 봅니다. 격자점을 클릭하면 오른쪽 속성 창에 그 점의 값이 나옵니다. 설정 파일 없이 결과 폴더만 열었을 때는 간단한 지도로 표시됩니다.
- **3D 파사드 소음지도**: 지도에서 **건물을 클릭**하면 그 건물을 중심으로 한 3D 파사드 소음지도가 나옵니다(그림 파일 `facade_3d_*.png` 와 같은 모양, 2 dB 등급). 주변 건물도 벽면 결과가 있으면 층별 색으로 칠하고, 층별 값 글자는 클릭한 건물에만 붙입니다. 마우스로 끌어 돌려 볼 수 있고, 클릭한 건물의 층별 값 전체는 아래 **벽면** 표에 나옵니다. 벽면 계산 반경 밖 건물은 상태 줄에 "벽면 계산 결과 없음"으로 표시됩니다. 도구 줄 **격자+파사드 나란히**(기본 켬)이면 왼쪽 격자 지도는 그대로 두고 오른쪽 창에 3D 가 나오며(클릭한 건물은 격자 지도에 빨간 테두리), 끄면 한 창에서 바뀌고 **◀ 격자 지도** 로 돌아갑니다. 나란히 보기에서는 왼쪽 격자 지도를 **지도만 크게**(정보란 없이, 범례는 지도 안 작은 상자) 그리고, 한 창 보기와 **지도 저장**은 보고서 틀(A3, 정보란 포함) 그대로입니다(2026-11-11). 홈 화면에서 다른 작업을 골라도 이 선택은 유지됩니다('격자 + 3D 파사드' 작업만 켬).
- **격자 위 벽면 값**(2026-11-10): 도구 줄 **벽면 값**(기본 켬)이면 격자 지도 위 건물 외벽을 따라 벽면 결과를 색 띠로 함께 그립니다. 띠 값은 그 벽면 구간의 층별 최댓값, 색은 격자 지도와 같은 등급, 위치는 보고서 그림 `HQ_facade_*` 와 같습니다(벽에서 바깥으로 1.1 m). 벽면 계산을 하지 않은 결과에서는 단추가 꺼집니다.
- **단면(수직) 소음지도**: **단면 선 긋기** 를 누르고 격자 지도에서 시작점 A, 끝점 B 를 차례로 클릭하면 그 선을 따라 거리 × 표고 단면의 수음점을 계산해 보여 줍니다(전농사거리 180 m 단면, 계산점 약 3,200개에 약 25초). 지형과 건물 단면을 함께 그리고, 건물 안(지붕 아래)·지면 아래는 계산하지 않습니다. 계산은 격자와 같은 함수·반사 차수를 쓰며, 같은 위치의 지면 위 4 m 값이 격자 결과와 최대 0.007 dB 안에서 같음을 시험으로 확인했습니다. 결과는 결과 폴더의 `section_result.csv`, `section_info.json`, 규칙 이름 사본(`…_주야_section.csv`)에 저장되고, 결과 폴더를 다시 열면 파일 ▸ **마지막 단면 보기** 로 볼 수 있습니다. 음원(노면 위 0.5 m) 바로 위의 낮은 점은 값이 매우 크게 나올 수 있습니다(근거리).
- **전파 방법 고르기**(2026-11-02): **계산 실행** 탭의 **전파 방법** 칸에서 `ISO 9613-2 전파`(RLS-19 음원 + ISO 9613-2) 또는 `RLS-19 전파`(3.5·3.6 원문)를 고릅니다. 처음에는 설정 파일의 `propagation_method` 값이고, 설정 파일 자체는 바꾸지 않습니다. 설정과 다른 방법을 고르면 결과 폴더가 `output_rls19`(또는 `output_iso9613_2`)로 바뀌어 두 방법의 결과가 섞이지 않고, 그 폴더에 결과가 있으면 지도에 바로 보입니다. 계산하면 `config_used.yaml` 사본과 `run_conditions.json`(`propagation_method`, `propagation_method_override`)에 쓴 방법이 기록됩니다. 결과 폴더를 열면 칸이 그 결과의 방법으로 맞춰집니다. 화면 없이: `sori_gui.py --run config.yaml --out 폴더 --method rls19`.
- **지도 저장**: 도구 줄 **지도 저장…**(또는 파일 ▸ 지도 저장, ⌘S / Ctrl+S)으로 지금 보이는 지도를 PNG(200 dpi)·PDF 로 저장합니다. 기본 이름은 결과 파일명 규칙을 따릅니다(예: `전농사거리_현황-ISO_주간_grid_h4m.png`, `전농사거리_현황-ISO_주간_facade_3D_건물1498.png`, `전농사거리_현황-ISO_주간_section.png`).

컴파일 캐시는 사용자 폴더(macOS `~/Library/Application Support/SORI`, Windows `%LOCALAPPDATA%\SORI`)에 저장됩니다.

**개발자용**

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-lock.txt
```
- 개발 중 실행: Finder 에서 `SORI.command` 더블클릭(`.venv` 활성화 후 `sori_gui.py` 실행)
- 화면 없이 같은 작업: `python sori_gui.py --run config.yaml --out 결과폴더`, 자가 점검: `python sori_gui.py --selfcheck report.json`
- 앱 빌드(macOS): `.venv/bin/pip install -r requirements-build.txt` 후 `.venv/bin/pyinstaller --noconfirm --clean SORI.spec` → `dist/SORI.app`
- 자동 빌드: GitHub 원격 저장소에 올린 뒤 `v*` 태그를 push 하거나 Actions 화면에서 수동 실행(workflow_dispatch). Windows 용 `SORI.exe` 는 맥에서 만들 수 없어 이 방법으로 만듭니다.

## 1. 설치

```bash
pip install -r requirements.txt          # Anaconda 사용 시: conda install -c conda-forge geopandas rasterio numba pyyaml
```
numba가 없어도 동작하지만 수십~수백 배 느려집니다.

## 2. 빠른 시작 — 명령행 (가상 데이터로 10분 안에 확인)

```bash
python tools/make_synthetic_data.py examples/synthetic      # 수치지도 형식의 가상 자료 생성
cd examples/synthetic
python -m sori all -c config.yaml                            # 모델 구축 → 계산 → 그림
```
(패키지 폴더 밖에서 실행할 때는 `PYTHONPATH`에 이 폴더를 추가하거나 `pip install -e .` 대신 폴더를 복사해 사용)

## 3. 실제 지역 계산 순서

1. **작업 폴더 만들기**: `python -m sori init 전농사거리` → `config_example.yaml`을 `config.yaml`로 이름 변경
2. **수치지도 넣기**: 국토정보플랫폼에서 받은 수치지형도 v2.0(SHP) 압축을 `data/map/` 아래에 도엽별로 풀기 (하위 폴더 자동 탐색, 여러 도엽 자동 병합)
3. **DEM(선택)**: `input.dem.path`에 GeoTIFF/IMG 경로. 좌표계는 파일에서 읽어 자동 변환
4. **계산 중심 지정**: `receivers.center: [x, y]` (EPSG:5186 좌표)
5. **교통량 서식 만들기**: `python -m sori template -c config.yaml` → `traffic_template.csv` 생성 → 값 입력 후 `traffic.csv`로 저장
6. **교차로 확인(선택)**: `traffic_template_junctions_auto.csv`의 자동 판정(type)을 검토·수정 후 `input.junctions.path`로 지정
7. **실행**: `python -m sori all -c config.yaml`
8. **결과 확인**: `output/` 폴더 (아래 6절)

## 4. 입력 서식

### 4.1 교통량 `traffic.csv` (UTF-8 또는 CP949)

| 열 | 필수 | 설명 |
|---|---|---|
| key | ● | 도로명(예: 사가정로) 또는 수치지도 UFID |
| key_type | | `name`(기본) 또는 `ufid` — UFID 행이 도로명 행보다 우선 |
| road_type | | 도로유형(빈칸 값 보충용): `autobahn`, `bundesstrasse`, `landesstrasse`, `gemeinde` — RLS-19 표 2 / RLS-90 표 3 의 독일 도로 분류 |
| DTV | △ | 일평균 교통량(대/일, 양방향 합) — DTV 또는 M_day·M_night 중 하나 필수 |
| M_day, M_night | △ | 시간당 교통량(대/h, 양방향 합). 비우면 DTV × 표준표 계수(아래 ※). **DTV 없이 한쪽만 입력하면** 기본은 오류(문제 행을 모두 나열) — 설정 `input.traffic.missing_m: estimate` 이면 표준표의 주·야 계수 비율로 보충(가정값, `M_*_from = est_from_…`, 서울 간선에서는 야간을 과소 추정할 수 있음) |
| p1_day … p2_night | | Lkw1·Lkw2 비율 %. 비우면 RLS-19 표 2 표준값. RLS-19 2장: Lkw1 = 허용 총중량 3.5 t 초과 트레일러 없는 화물차와 버스, Lkw2 = 트레일러 부착 화물차·세미트레일러 연결차, 이륜자동차는 방출 측면에서 Lkw2(속도는 Pkw) |
| v_pkw, v_lkw | | 차종별 속도 km/h (30 미만은 30 적용, RLS-19 2장) |
| surface | | 노면: `ref`, `AC11`, `SMA5_8`, `SMA8_11`, `PA11`, `PA8`, `Waschbeton`, `LGA`, `LOA`, `SMA_LA8`, `DSH_V5`, `pflaster_even`, `pflaster_other` |
| note | | 출처 메모(보고서용) |
| basis | | 교통량 근거: `measured`(실측) 또는 `assumed`(가정). 빈칸이면 unspecified. `traffic_used.csv`의 `traffic_basis`로 기록 |
| dir_split_fwd | | 디지타이즈 방향 교통량 비율(0~1). **UFID 행이고 dir_basis = measured 일 때만 적용**, 그 밖은 0.5(RLS-19 3.3.1 기본). 일방통행은 무시 |
| dir_basis | | 방향별 비율의 근거: `measured` 또는 `assumed`. 적용 여부는 `traffic_used.csv`의 `dir_split_src` |
| v_pkw_fwd, v_lkw_fwd, v_pkw_bwd, v_lkw_bwd | | 방향별 속도 km/h(2026-11-11). fwd = 디지타이즈 방향, bwd = 반대. **UFID 행만** 적용(도로명 행은 무시·경고). 빈칸 = 공통 `v_pkw`·`v_lkw`(화물은 공통 v_lkw, 그것도 없으면 그 방향 승용) → 입력이 없으면 결과 불변. RLS-19·RLS-90 음원 모두 각 방향 음원선에 그 방향 속도. 기록: `traffic_used.csv` 의 `v_*_fwd/bwd`·`dir_speed_src`(same/ufid/name_key_ignored). SoundPLAN 처럼 방향마다 속도가 다를 때 |
| p_day, p_night | | RLS-90 계산(`propagation_method: rls90`)만: 화물차 비율 % (허용 총중량 2.8 t 초과). 비우면 p1 + p2, 그것도 비우면 RLS-90 표 3 |

교통량 파일에 없는 도로는 `fallback_dtv_by_lanes`(차로수별 가정값)를 쓰고, `output/traffic_used.csv`의 `traffic_src=fallback`으로 표시됩니다.

※ **교통량 자료가 없는 항목(M·p)은 독일 기준표로 채웁니다.** ISO 9613-2·RLS-19 계산은 RLS-19 표 2, RLS-90 계산은 RLS-90 표 3 의 도로유형별 표준값(DTV 에 곱하는 주·야 계수와 화물차 비율)입니다. 독일 도로의 표준값이므로 **국내 교통 특성(주·야 비율, 화물차 비율)과 다를 수 있습니다** — 예: 서울 28지점 주·야 계수 0.0518/0.0215, RLS-19 지자체 도로 0.0575/0.0100. 표로 채운 항목은 `traffic_used.csv`의 `std_filled`·`traffic_std`, `build_info.json`의 `traffic_defaults`에 남고 지도 정보란에도 표기됩니다. 차로수별 DTV(`fallback_dtv_by_lanes`)는 두 기준 어디에도 없는 SORI 가정값입니다.

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

### 4.5 점 수음점 `points.csv` (선택 — 현장 측정점 비교용)
현장 측정점처럼 임의 좌표·높이의 수음점을 `receivers.points.path`에 지정합니다. `python -m sori run -c config.yaml --points`로 점 수음점만 계산할 수 있습니다(모델은 먼저 `build`).

| 열 | 설명 |
|---|---|
| point_id | 수음점 이름(필수, 중복 불가). `id`·`ID`도 읽음 |
| x, y | 계산 좌표계(`crs`, 기본 EPSG:5186) 좌표(필수). `x_epsg5186`·`y_epsg5186`도 읽음 |
| h | 지면 위 높이(m). `height_m`·`height`·`z`도 읽음. 빈칸이면 `receivers.points.default_height`, 그것도 없으면 그 행은 건너뜀 |

`field_survey/plan/measurement_points.csv`를 그대로 지정할 수 있습니다(좌표가 빈 측정점은 건너뛰고 로그에 이름을 남김). 지면고는 격자와 같은 방식(모델 DTM 1차 보간)으로 구하고, 자기 벽면을 빼지 않으므로 벽 앞 측정점도 벽 반사를 포함해 계산합니다. 건물 안이거나 모델 범위 밖인 점은 계산하지 않고 `note`에 적습니다.

## 5. 주요 설정 (config.yaml)

**"혼합 방법" 표기**: 기본 `propagation_method: iso9613_2` 는 음원은 RLS-19, 전파는 ISO 9613-2 로 계산합니다. 이 결합은 RLS-19 와 ISO 9613-2 어느 쪽도 규정하지 않은 프로젝트 구성 방법이라, 지도 정보란 '구성'과 `run_info.json` 의 `method_label` 에 "RLS-19 음원 + ISO 9613-2 전파 — 어느 표준도 규정하지 않은 프로젝트 구성 방법"으로 적습니다(국내 「소음지도의 작성방법」 별표 2 제8호는 전달감쇠 영향인자에 KS I ISO 9613-1·-2 적용을 허용하나, 예측식 자체의 결합을 정한 것은 아님 — 해석). RLS-19 전파와 같은 값은 음원에서 가까운 곳뿐이고, 포장 지면에서 차폐 없이 먼 곳은 RLS-19 보다 높게 나옵니다(평지 100 m 에서 약 +4 ~ +6 dB, TECHNICAL_NOTES 2절). RLS-19 원문 그대로의 계산이 필요하면 `rls19` 와 반사 2차를 씁니다(기본 3차는 국내 고시 기준이며 RLS-19 3.6 을 넘고, 다중반사 가산값 D_refl 과 겹침 — TECHNICAL_NOTES 4a절). 표준 원문과 다른 설정은 계산 기록과 `run_info.json` 의 `standard_deviations` 에 나옵니다. 두 방법의 결과 차이와 원인은 TECHNICAL_NOTES 4b절.

| 항목 | 기본값 | 의미 |
|---|---|---|
| `propagation_method` | iso9613_2 | 전파 계산 방법. `iso9613_2` = ISO 9613-2(현행), `rls19` = RLS-19 3.5·3.6, `rls90` = RLS-90(1990, 음원·전파 모두 — 음원이 바뀌므로 build 부터 다시, TECHNICAL_NOTES 4d절). iso9613_2·rls19 는 음원(L_W′, 음원선, D_K, D_refl)과 기하가 공통. 한 출력 폴더에는 한 방법의 결과만 둠(다른 방법 결과가 있으면 실행 중단 — `project.output_dir` 를 바꿀 것). 방법·식 번호·미구현 항목은 `run_info.json`. 앱에서는 계산 실행 탭 **전파 방법** 칸으로 설정 파일을 고치지 않고 바꿀 수 있음(0.1절) |
| `propagation.reflection.own_facade` | last | 수음점이 놓인 외벽의 반사 제외 범위. `last` = 마지막 반사만(RLS-19 1장 Immissionsort 주석, TEST-20 K2), `all` = 모든 반사 순서(2026-10-29 이전 동작) |
| `propagation.terms_csv` | false | 점 수음점의 감쇠항별 중간값 `path_terms.csv` 출력(직접음·1차 반사) |
| `propagation.bands_hz` / `spectrum_dBA` | [500] / [0] | (iso9613_2) 500 Hz 단일대역(ISO 9613-2 1절 비고 1). 옥타브 스펙트럼을 넣으면 8대역 계산. rls19 는 A 가중 단일값이라 여러 대역 지정 불가 |
| `input.ground.method` | general | (iso9613_2) ISO 7.3.1 일반법. `alternative`는 7.3.2(식 10, 다공성 지면 전제). rls19 는 식 (14) 고정 |
| `propagation.reflection.rho` | 0.8 | (iso9613_2) ISO 표 4 '창이 있는 건물 벽', 식 (20) |
| `propagation.reflection.D_RV` | 0.5 | (rls19) 반사 손실 dB/회, RLS-19 표 8 '건물 외벽 및 반사성 방음벽'. 반사 조건은 h_R ≥ 1.0 m 이고 h_R ≥ 0.3·√a_R(3.6) |
| `propagation.reflection.D_E` | −1 | (rls90) 허상음원 보정 dB/회, RLS-90 표 7(매끈한 외벽 −1, 요철 외벽 −2, 흡음 −4, 고흡음 −8). 반사 조건 h_R ≥ 0,3·√a_R(4.6). RLS-90 은 1차 반사만 규정 |
| `max_order_grid` / `max_order_facade` / `max_order_point` | 3 / 3 / 3 | 반사 차수 0~3. 국내 「소음지도의 작성방법」 별표 2 제8호 "3차 이상" 원칙(주변 환경을 고려해 변경 가능). RLS-19 3.6 은 1·2차라 rls19 에서도 3차는 국내 조건 적용. 3차는 2차보다 계산 시간이 약 6배(전농사거리 표본) — 2026-10-29 이전 기본값은 1 / 2 / 2. 웹 화면 계산 수준은 따로 지정(빠른 미리보기·표준 1차, 정밀 3차) |
| `input.traffic.oneway_name_traffic` | apply | 도로명 교통량(양방향 합)을 같은 이름의 일방통행 객체에도 배정(`apply`) 또는 차로수 가정값 사용(`fallback`, `traffic_src = fallback_oneway`). UFID 지정 교통량은 항상 우선 |
| `input.junctions.dk_rule` | nearest | 교차로 보정 D_K(RLS-19 3.3.7)의 기준 교차점. `nearest` = 그 음원선이 속한 교차점 중 가장 가까운 것(현행). `nearest_signal` = 위 값과, 도로 소속과 무관하게 가장 가까운 signal·roundabout 교차점 기준 값 중 큰 쪽 — 가까운 이면도로 접속부(other)가 100 m 안 신호교차로 보정을 가리는 경우용. 켜면 `sources.csv` 에 `D_K_nearest`·`x_signal`·`kt_signal` 열 추가(TECHNICAL_NOTES 5a절 13) |
| `model.minor_roads` | exclude | 골목길(차로수 < `main_min_lanes` = 수치지도 1차로, 폭 0.5–14 m) 음원. `exclude` = 교통량 파일에 없는 골목길은 음원에서 뺌(교통량 파일에 UFID·도로명으로 넣은 골목길은 계산), `include` = 차로수 가정 교통량(DTV 1,000)으로 모두 계산(2026-11-07 이전 동작). 뺀 도로는 `traffic_used.csv` 의 `traffic_src = excluded_minor`, 도로선은 교차로 판정·차도 면 보정에 그대로 씀 |
| `model.source_ground` | dtm_at_source | 음원 높이(지면 + 0.5 m)의 지면: `dtm_at_source` = 음원점 위치 DTM, `road_profile` = 도로중심선 종단 고도(경사 산정과 같은 평활 DTM). road_profile 은 노면이 주변 DTM 보다 낮은 곳에서 음원 옆 지형이 차폐로 잡힐 수 있음(TECHNICAL_NOTES) |
| `model.odd_lane_rule` | ceil | 확인값이 없는 홀수 차로 양방향 도로의 방향별 차로 수. `ceil` = 양쪽 ⌈n/2⌉(5 → 3+3), `major_fwd` = 디지타이즈 방향 ⌈n/2⌉·반대 ⌊n/2⌋(5 → 3+2) |
| `model.source_lines` | direction | 음원선 배치(2026-11-11). `direction` = 주행방향마다 1개선(RLS-19 Bild 6 / RLS-90 Bild 10). `lane` = **차로마다 1개선**(차로 중앙, 그 방향 M 을 차로 수로 똑같이 나눔) — 전체 차로를 도로중심선 대칭으로 펼침(3+2 처럼 비대칭이면 상·하행 경계가 중심선에서 반 차로 비켜남). 원문과 다른 프로젝트 선택으로 SoundPLAN 차로별 음원선 비교용. 기록: `source_lines.csv` 의 `source_lines`·`lines_used`(오프셋:방향:M 비율), `build_info.json` |
| `model.lane_width` | auto | 음원선 위치 계산용 차로폭. `auto` = 도로폭/차로수를 `lane_width_clip`(2.75~3.5 m)로 제한, 도로폭 없으면 3.25 m. 숫자 = 고정값(m) |
| `receivers.grid.heights` | [4.0] | 3D 격자 높이 목록 |
| `propagation.max_dist_main/minor` | 300 / 150 m | 음원 탐색 거리 |

## 6. 출력 (`output/`)

**파일명 규칙 사본**: 아래 고정 이름 파일과 함께 `{프로젝트}_{시나리오}-{ISO|RLS}_{주간|야간|주야}_{grid|facade|section}[부가]` 이름의 사본을 만듭니다(설정 `project.scenario`, 기본 "현황"; `project.file_naming: false` 이면 만들지 않음). 방법 표시(ISO/RLS)는 `propagation_method` 에서 자동으로 붙고, 두 시간대를 함께 담은 CSV 는 `주야`, 격자 높이·지도 종류는 부가(`_h4m`, `_building`)로 붙습니다. 칸 안의 `_`·공백·파일명 금지 글자는 `-` 로 바뀝니다. 대상: 격자·파사드 결과 CSV, 격자 GeoTIFF, 고품질 지도(HQ_*) PNG·PDF. 점 수음점 결과와 확인용 그림(`grid_*_grid.png` 등)은 고정 이름만. section(단면) 지도는 미구현. 예) `전농사거리_현황-ISO_주간_grid_h4m.png`, `전농사거리_현황-RLS_주야_facade.csv`. 만든 목록은 `run_info.json` 의 `file_naming`.

| 파일 | 내용 |
|---|---|
| `grid_result.csv`, `grid_LrT_h4m.tif` … | 격자 결과(높이별), GeoTIFF는 QGIS에서 바로 열림 |
| `facade_result.csv` | 파사드 결과(층별). `LrT_cmp`는 RLS-19 3.1 올림값, `LrT_direct/refl1/refl2/refl3`는 성분(`refl3` 는 3차를 계산할 때만) |
| `point_result.csv` | 점 수음점 결과: 지면고 `zg`, 높이 `h`(`h_basis` = input/default_height), `LrT`·`LrN`, 성분, 3.1 올림값, `note`(건물 안·모델 범위 밖) |
| `point_levels_for_comparison.csv` | 측정값 비교용(point_id, period = day/night, Leq_calc). `field_survey/scripts/process_measurements.py --model` 입력 형식. 값은 RLS-19 평가레벨 LrT·LrN(교차로·반사 보정 포함)이며, 측정 회차와 비교하려면 그 회차의 동시 교통량으로 계산한 결과를 써야 함 |
| `HQ_*.png / .pdf` | **보고서·발표용 A3 지도(300 dpi + 벡터 PDF)**: 격자 소음지도, 벽면 색 띠 지도, 건물별 평가도 (주·야) |
| `noise3d.html` | **3D 웹 뷰어**: 지형 위 격자 소음지도 + 건물 벽면 층별 소음. 더블클릭해 브라우저로 열기(three.js 내장, 인터넷 없이 동작) |
| `*.png` | 확인용 간이 그림(격자, 파사드 평면, 파사드 3D) |
| `sources.csv` | 음원 점별 LW′, D_K, D_refl, 경사, 교차로 거리 등 |
| `source_lines.csv` | 도로 객체별 음원선 배치 근거: 사용한 방향별 차로 수, 근거(`lane_rule`: override·map_half·ceil·major_fwd·oneway_map·single_centre), 차로폭, 중심선 기준 오프셋 |
| `path_terms.csv` | (설정 `propagation.terms_csv: true`) 점 수음점의 직접음·1차 반사 경로별 감쇠항. rls19: D_div·D_atm·D_gr·D_z·D_RV·D_A, rls90: D_s·D_BM·D_z·D_E·D_A·K, iso9613_2: A_div·A_atm·A_gr·D_Omega·D_z·A_bar·refl_dB·A_net, 경로별 L_day. 같은 점·차수의 L_day 에너지 합 = `LrT_direct`/`LrT_refl1` |
| `segment_check.csv` | 구간 길이 진단: RLS-19 3.2 주석(경험칙) l ≤ s/2 를 어기는 음원이 있는 수음점만(위반 음원 수, 최소 s/l). 결과값에는 영향 없음. 요약은 `run_info.json` 의 `segment_check` |
| `traffic_used.csv`, `junctions_used.csv` | 실제 사용된 교통량·교차로 판정 (보고서 근거자료) |
| `build_info.json`, `run_info.json` | 모델 규모, 가정값 사용 수, 계산 조건 기록. `run_info.json` 에는 표준 원문과 다른 설정(`standard_deviations`), 재현용 실행 환경(`environment`: git 커밋·소스 해시·Python·패키지 판)과 기본값까지 합친 설정(`config_resolved`)도 들어감(2026-11-11) |

## 7. 계산 시간
단일 코어 기준 파사드 수음점 1개당 수 ms(가상자료) ~ 수십 ms(도심). 수만 개 수음점은 수 분~수십 분이 걸리며 numba가 CPU 코어를 모두 사용합니다. 빠른 시험은 `grid.step`을 10 m로, `max_order_facade`를 1로 낮추세요.

## 8. 검증
```bash
python -m pytest -q tests        # 330개 시험(2026-11-11 v0.2: 329 통과, RLS-19 공식 시험과제 TEST-20 포함, 1개는 Krad 미지원 xfail): 원문 식 재계산, 해석해(직선 음원), 차폐·1·2차 반사 수계산 비교(ISO·RLS-19 전파), 입력·음원 배치 회귀시험, 기준 사례·좌표 변환
```
기본값에서 결과가 바뀌지 않았는지는 `python tools/check_baseline.py`(전농사거리 기준선 `baseline/` 과 SHA-256 비교, 약 1분)로 확인합니다.
`tests/test_reference.py` 는 저장소에 들어 있는 기준 사례(가상 교차로, `tests/reference/synthetic/`)를 계산해 기준값과 허용 오차(기본 0.01 dB, 프로젝트 임의 설정, `--sori-tol` 로 변경)로 비교하고, `tests/test_crs.py` 는 좌표 변환(EPSG) 결과를 확인합니다. 기준값을 만든 커밋·환경은 `tests/reference/synthetic/expected/meta.json`, 다시 만들기·결과 폴더 비교는 `python tools/reference.py make|compare`.

그 밖의 도구: `tools/make_review_lists.py`(지도 확인 목록 → `docs/review/`), `tools/diagnostics.py`(구간 분할·횡단경사 진단 재현). 교통량 비교 시나리오는 `scenarios/`.
자세한 내용은 `TECHNICAL_NOTES.md`, 작업 경과와 남은 일은 `docs/작업기록_2026-10-15_17.md`.

## 9. 판 번호(버전)

- 판은 `sori/__init__.py` 의 `__version__` 하나로 정한다. 앱 제목·SORI 정보·결과 `run_info.json` 의 `software`·보고서 지도 아래 글이 이 값을 쓰고, User's·Technical Manual 표지·머리글은 `docs/manual/build.sh` 가 만드는 `docs/manual/version.tex`(`\soriversion`)로 같은 값을 쓴다. 어긋나면 `tests/test_version_sync.py` 가 실패한다(→ `sh docs/manual/build.sh` 후 `version.tex` 커밋).
- **둘째 자리**(0.1 → 0.2): 같은 설정으로 계산 결과가 바뀌는 변경(식·기본값), 계산 방법이나 화면 기능 추가. **셋째 자리**(0.2 → 0.2.1): 결과가 바뀌지 않는 오류 수정·문서·화면 다듬기.
- 판을 올릴 때: `__version__` → `docs/release/v<판>.md` → 두 매뉴얼의 판별 변경 표 → `sh docs/manual/build.sh` → 배포는 `v<판>` 태그.

| 판 | 날짜 | 요약 |
|---|---|---|
| v0.1 | 2026-11-04 | 첫 배포(태그 `v0.1`, 공개 안 함) — `docs/release/v0.1.md` |
| v0.2 | 2026-11-10 | RLS-90 추가, 차도 면 보정 기본 flatten, 교통량 빈칸 독일 기준표, 골목길 제외 기본(**결과 바뀜**), 홈·입력값 화면, 나란히 보기, 격자 위 벽면 값 — `docs/release/v0.2.md`. 태그 전 |
| v0.8.1 | 2026-11-15 | DEM 을 zip 그대로 지정해도 읽음(래스터 하나가 든 zip, 이전에는 ‘not recognized as being in a supported file format’ 오류로 계산 실패), 준비할 파일 탭이 DEM 을 미리 열어 봄, 방음벽 계산 때 설치 전 결과와 DEM 설정이 다르면 확인 창·차이 요약 경고 — 같은 입력의 계산 결과 불변 — `docs/release/v0.8.1.md` |
| v0.8 | 2026-11-15 | 소음 관리기준 관련 기능 전부 삭제(기준은 지역·건물마다 달라 프로그램이 정하지 않음): 격자 지도·단면 그래프 기준선, 건물별 값 초과 테두리, 건물 단위 표 지역·기준·초과 열과 ‘기준 초과 건물만’, 속성 창 기준 초과 줄, 보고서 기준 초과 지도(`HQ_exceed_*`), 설정 `input.zones`·`plots.limits`(있으면 경고 후 무시) — 계산 불변, 출력 파일 줄어듦 — `docs/release/v0.8.md` |
| v0.7.2 | 2026-11-15 | 방음벽 지우기 고침·개선: 표의 행마다 ✕, 지도에서 방음벽 선을 눌러 고르기, 하나뿐이면 고르지 않아도 지움, 모두 지우기·되돌리기, Delete 키, 그리기 취소(Esc) — 계산 불변 — `docs/release/v0.7.2.md` |
| v0.7.1 | 2026-11-15 | 데이터 입력 화면 다듬기: 설정값(묶음 카드·짧은 이름·단위는 칸 옆·긴 설명은 풍선 도움말), 교통량(두 줄 머리글·숫자 오른쪽 맞춤·빈 고급 열 숨김), 차로 수(도로명 먼저·고칠 칸 흰색/지도 값 회색/결과 파랑·적용 한글 표기) — 계산·파일 형식 불변 — `docs/release/v0.7.1.md` |
| v0.7 | 2026-11-15 | 정밀 격자(경계 세분) 선택 사항: 건물·간선 경계와 값이 2 dB 넘게 바뀌는 칸만 반 간격씩 두 번 더 계산(2.5 m → 0.625 m), 지도·GeoTIFF 는 삼각망 보간. 기본 끔 — 끄면 결과 불변, 켜면 그림·세밀 GeoTIFF 가 바뀜(규칙 격자 표는 그대로) — `docs/release/v0.7.md` |
| v0.6 | 2026-11-15 | 지도·화면 품질 개선 3차: 나란히 보기 오른쪽 3D 파사드를 three.js(앱 안 웹 화면)로 — 깊이 정렬 정확·부드러운 회전, 보이는 벽 값 글자·번호를 돌리는 대로 다시 배치, 3D 안에서 다른 건물 클릭, 지도 저장은 matplotlib 그대로 — 계산 불변 — `docs/release/v0.6.md` |
| v0.5 | 2026-11-14 | 지도·화면 품질 개선 2차: 격자 지도 등음선 dB 숫자·관리기준 기준선(보기 메뉴, 기본 켬), 단면에 지나는 도로 노면·도로명, 3D 파사드 바닥 소음지도(기본 켬) — 계산 불변, 화면 기능 추가 — `docs/release/v0.5.md` |
| v0.4.2 | 2026-11-14 | 3D 뷰어 벽면 방향별 명암·건물 윤곽선(뷰어 판 3, 계산 불변) — `docs/release/v0.4.2.md` |
| v0.4.1 | 2026-11-14 | 앱 3D 파사드: 벽 방향별 명암, 가장 시끄러운 벽이 보이는 시작 시점, 글자 겹침 화면 기준 정리(계산 불변) — `docs/release/v0.4.1.md` |
| v0.4 | 2026-11-14 | 단면 계산점 간격 고르기(자동·2·1·0.5 m, 예상 점 수·시간), 단면을 오른쪽 창에·격자와 같은 색표·보간·등음선·건물 번호·높이별 값 그래프 — `docs/release/v0.4.md` |
| v0.3.1 | 2026-11-14 | 격자 지도 건물·노면 색(조용한 지역과 구분), 상태 줄 마우스 위치 값(계산 불변) — `docs/release/v0.3.1.md` |
| v0.3 | 2026-11-14 | 측면 회절 기본 켬(**결과 바뀜**), 방음벽 시뮬레이션·전후 차이, 경로 보기, 반사 4–6차, 건물별 표·용도지역 기준, 화면 개편(홈·3D 파사드 번호·7색 색표) — `docs/release/v0.3.md`. 태그 전 |
