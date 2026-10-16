# 기준선 (Baseline) — 전농사거리 실제 자료, 수정 전 원래 코드

> ※ 본 기준선 자료와 스크립트는 Claude(Anthropic의 AI)를 활용하여 작성하였음.

코드를 단계별로 고칠 때마다 이 파일들과 비교해 **기본값에서 결과가 바뀌지 않았는지** 확인한다.
값이 바뀌는 변경(옵션을 켠 경우 등)은 차이를 따로 보고한다.

## 생성 조건

| 항목 | 값 |
|---|---|
| 생성 코드 | 커밋 `29602cd` (main, "수정 전 원래 코드") — `git archive 29602cd` 로 꺼낸 코드로 실행 |
| 생성 일시 | 2026-10-16 |
| 설정 | `baseline/config.yaml` (「소음지도 앱 코드/config.yaml」과 같은 조건, 수치지도 경로만 절대경로) |
| 교통량 | `baseline/traffic.csv` — 「소음지도 앱 코드/traffic.csv」 사본. **4개 노선 모두 가정값(실측 아님)** |
| 수치지도 | `~/Downloads/3-2/소음진동설계/전농사거리_수치지도` (도엽 377051731~33, 41~43, 51~53, 2025). 저장소 밖, 읽기 전용 |
| DEM | 사용 안 함 (등고선 + 표고점) |
| 환경 | Python 3.14.6, numpy 2.4.6, numba 0.65.1, pandas 3.0.3, scipy 1.18.0, shapely 2.2.0, geopandas 1.2.0, pyproj 3.8.0 (macOS, /opt/anaconda3) |
| 재현성 | `lv_base.csv` 를 같은 코드로 두 번 계산해 SHA-256 동일 확인. `sources.csv` 는 2026-10-15 재구축본과 동일 |

## 파일

| 파일 | 내용 | 행 수 | 크기 | SHA-256 |
|---|---|---|---|---|
| `sources.csv` | `noisemap build` 의 음원점(10,671개): 위치, 구간 길이 l, LW′, D_K, D_refl 등 | 10,671 | 2.9 MB | `bbcef88bb6387f48aa7669d71f044f1fcbfb5015505c976127facd5acaf80ed9` |
| `traffic_used.csv` | 도로 객체별 실제 사용 교통량(2,261개). 원래 코드 판이라 S1 의 출처 열(traffic_basis 등)은 없음 | 2,261 | 0.3 MB | `d3f9103f7285aa75aaa47a9604015002e711c78227ceb629425b6c00c024f9ec` |
| `lv_base.csv` | 계산 중심 반경 150 m 안 격자(h = 4 m) 7,152개 + 파사드 3,710개의 LrT, LrN 및 성분(직접·1차·2차 반사) | 10,862 | 1.0 MB | `61c1d98432b40a7844377f2f21bfdd1fa8d116cc1ff16c01be8a02fc6235aded` |
| `config.yaml` | 계산 설정 | — | — | `9d8ce453f3cf5bf6b2234599695ddc88a23af1d26955e4b6d7475c1c682307a3` |
| `traffic.csv` | 교통량 입력 | — | — | `b20ff356dbf63fd64ac3f826e44976c8f755882b843e7b1068e17ae7024b3aac` |
| `make_lv_base.py` | `lv_base.csv` 생성 스크립트 | — | — | `becc90639d43d07d4fcc9fa511df2fbe49ee2a98336439ab33dfc9c6f60608f6` |

## 재현 방법

```bash
git archive 29602cd | tar -x -C /tmp/noisemap_29602cd
```
```bash
mkdir -p /tmp/bl && cp baseline/config.yaml baseline/traffic.csv /tmp/bl/ && cd /tmp/bl && PYTHONPATH=/tmp/noisemap_29602cd python -m noisemap build -c config.yaml
```
```bash
cd /tmp/bl && PYTHONPATH=/tmp/noisemap_29602cd python <저장소>/baseline/make_lv_base.py output config.yaml lv_base.csv 150
```

## 비교 규칙

- `sources.csv`, `lv_base.csv`: 기본값으로 실행한 결과가 바이트 단위로 같아야 함(SHA-256 비교).
- `traffic_used.csv`: S1 이후 판은 열이 추가되므로 **기존 열**만 같아야 함.
- 기준선을 바꿀 때(새 기본값 채택 등)는 이 README 에 새 커밋 해시와 사유를 기록한다.
