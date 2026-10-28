# 사가정로 현장 소음측정 (2026-10-22) — 폴더 안내

> AI(Claude)를 활용하여 제작하였음.

## 재현

저장소 루트에서 실행한다.

```bash
sh field_measurement/2026-10-22/scripts/run_all.sh
```

- 전체 소요 시간은 약 7분이다.
- `--skip-map`을 붙이면 소음지도 재계산을 건너뛴다.
- 원시자료 복사본 `raw_copy/`는 git에 넣지 않는다. 원본에서 다시 복사한 뒤 `SHA256SUMS_원본.txt`로 확인한다.

```bash
cd field_measurement/2026-10-22/raw_copy && shasum -a 256 -c ../SHA256SUMS_원본.txt
```

## 문서

| 파일 | 내용 |
|---|---|
| `01_데이터구조_보고.md` | 1단계: 원시 파일 구조, 기기 설정 차이, 야장과의 불일치, 사용자 확인 결과 |
| `02_분석보고서.md` | 회차별 지표, 시계 차이, 교통량, 특이사항 처리, 한계, 모델 비교(7절), 소음지도(8절) |
| `03_실험노트_초안.md` | 실험노트 옮겨 적기용 초안(기록 1~7. 기록 5~7은 검토 의견 B2~B4) |

## 스크립트 (`scripts/`)

| 파일 | 역할 |
|---|---|
| `nl62.py` | RION NL-62 Auto Store(.rnh / AUTO_LP / AUTO_LEQ) 파서 |
| `inspect_structure.py` | 1단계 구조 점검 |
| `analyze.py` | 회차별 지표, 특이사항(피크 대조·후보 탐지), 교통량, 시계열 그림, 모델 검증용 입력 CSV |
| `wav_check.py` | WAV 스펙트로그램, 순음 지표, 사이렌 기반 시계 차이 |
| `model_compare.py` | 수음점 좌표(수치지도), SORI 입력 생성·실행, 측정 비교, 민감도, 소음지도. `--plot-only`는 기존 결과로 비교 그림만 다시 그림 |
| `run_all.sh` | 위 순서대로 전체 실행 |
| `sensitivity_combo.py` | 조합 민감도 96개 실제 실행(속도·차종·노면·D_K·D_refl) → `results/민감도_조합.csv`. 계산 폴더 `model_sens/`(git 무시). `run_all.sh`에 포함하지 않음 |
| `ground_diag.py` | 지면효과 진단(q 항 제거·대안법) → `results/지면효과_진단.csv`. 계산 폴더 `model_diag/`(git 무시, 진단용 코드 사본 포함). `run_all.sh`에 포함하지 않음 |

## 결과

| 폴더 | 내용 |
|---|---|
| `results/` | 지표·교통량·특이사항·시계 차이·모델 비교·민감도 CSV |
| `figures/` | 시계열, 지점 비교, 산점도, 스펙트로그램, 모델 비교, 소음지도(PNG/PDF) |
| `model_validation/` | 모델 검증용 입력자료(출처 열 포함) |
| `model/` | SORI 계산 폴더(시나리오별 `config.yaml`·`traffic.csv`, `points.csv`). `*/output/`은 git 무시 대상이며 3D 뷰어 `AVG_v50/output/noise3d.html`도 여기에 있다. 시나리오 폴더 = `<회차>_v<속도>`(R1~R3·AVG × 30/40/50 km/h) |

> 이름 변경 안내(2026-10-26): 계산 프로그램의 가칭 `noisemap` 을 **SORI**(패키지 `sori`, v0.1)로 바꿨다. 계산식·결과값은 바뀌지 않았다.
> 이 폴더의 보고서·실험노트와 이미 만든 `results/`·`model_validation/` CSV 헤더, `figures/` 지도 하단 표기에는 당시 이름 `noisemap`(1.0.0)이 그대로 남아 있다. 스크립트를 다시 실행하면 새 이름으로 다시 생성된다.
