# 상용 소음 예측 소프트웨어의 서류 체계와 SORI 대응표

작성: 서울시립대학교 환경공학부 전현수(2022890065) · 2026-11-04

> ※ Claude(Anthropic의 AI)를 활용하여 조사·작성하였음. 상용 소프트웨어 서류는 공개 웹에서 확인한 것만 [확인]으로, 찾지 못한 것은 [미확인]으로 표시함.

## 1. 목적

SoundPLAN, AEDT 같은 상용·공공 소음 예측 소프트웨어가 어떤 서류를 내는지 조사한다. 그 서류를 **User's Manual / Technical Manual / Project** 세 갈래로 나누고, 우리 소프트웨어 SORI(Simulation of Outdoor Road-noise Impact) v0.1의 결과물을 짝지어 빈 곳을 찾는다.

## 2. 세 갈래 서류의 구분

| 구분 | 누구를 위한 서류 | 답하는 질문 | 바뀌는 때 |
|---|---|---|---|
| **User's Manual** | 프로그램을 쓰는 사람 | 어떻게 설치하고, 무엇을 넣고, 어떤 버튼을 누르고, 결과를 어떻게 읽나 | 화면·입력 서식이 바뀔 때 |
| **Technical Manual** | 검토자, 개발자, 결과를 인용하는 사람 | 어떤 식·계수·가정으로 계산하나, 표준과 맞는지 어떻게 검증했나 | 계산 방법이 바뀔 때 |
| **Project** | 발주처, 심사자(특정 지역 평가) | 이 지역(전농사거리)의 소음이 얼마이고, 측정과 맞으며, 기준을 넘나 | 대상 지역·자료·시나리오마다 새로 |

User's Manual과 Technical Manual은 **소프트웨어에 딸린** 서류이고, Project 서류는 **소프트웨어를 써서 만든 결과물**이다. AEDT도 SoundPLAN도 Project 서류는 직접 내지 않는다. 사용자(컨설턴트, 공항 운영자)가 각 사업마다 따로 쓴다.

## 3. 상용 소프트웨어의 서류 체계 조사

### 3.1 AEDT (Aviation Environmental Design Tool, 미국 FAA)

항공기 소음·배출 평가 도구. FAA 와 Volpe Center 가 개발하고, 판(3c, 3d, 3e, 3g, 4b …)마다 서류를 다시 낸다.

| 서류 | 내용 | 비고 |
|---|---|---|
| **User Manual** | 설치 뒤 스터디 만들기, 입력, 실행, 결과 보기 | [확인] AEDT 3g User Manual(2024-08), 3e, 3c 판 |
| **Technical Manual** | 소음·성능·배출 계산 방법, 식, 자료원 | [확인] AEDT 3g Technical Manual(2024-08), 3d, 3e 판 |
| **Uncertainty Quantification(UQ) Report** | 검증(verification)·확인(validation), 매개변수 불확도·민감도 분석 | [확인] AEDT 2a UQ Report(2014-02)와 Executive Summary, 2a SP2 UQ Supplemental Report(2014-05), AEDT 2b UQ Report(2017-08). 3판의 UQ 보고서는 [미확인] |
| Product Information | 판별 시스템 요구사항과 서류 목록 | [확인] 지원 사이트 메뉴 |
| Downloads | 설치 파일, 선행 설치 요소, 부가 도구 | [확인] 지원 사이트 메뉴 |
| FAQ, Feedback | 자주 묻는 질문, 오류 신고 | [확인] 지원 사이트 메뉴 |

AEDT 의 특징은 계산 방법(Technical Manual)과 별도로 **"이 계산을 얼마나 믿을 수 있나"를 다룬 UQ 보고서**를 따로 낸다는 점이다.

### 3.2 SoundPLAN (SoundPLAN GmbH, 독일)

도로·철도·산업·항공 소음 예측 상용 소프트웨어. 판매용이라 서류도 사용자용과 판매용으로 나뉜다.

| 서류 | 내용 | 비고 |
|---|---|---|
| **Module description** | 모듈마다 들어 있는 기능 | [확인] 영문판(2025-12), 독일어판(2024-04) |
| Highlights 소개서(SoundPLANnoise EN/DE) | 모델링·계산·결과 문서화 기능 소개 | [확인] 2025-08 판 |
| Product comparison | SoundPLANnoise 와 SoundPLANessential 비교 | [확인] 2020-08 판(8.2 / 5.1) |
| Standards 목록 | 구현한 계산 기준 목록 | [확인] 제품 페이지 메뉴 |
| Hardware recommendations | 권장 사양 | [확인] 제품 페이지 메뉴 |
| **적합성 선언(Declaration of conformity)** | CNOSSOS-EU 도로·철도·항공·산업 방법 구현 적합성 | [확인] 2022-01 서명본이 헝가리 정부 사이트에 공개됨 |
| **품질 보증·시험과제** | ISO 17534-1 등 공식 시험과제와 자체 시험과제로 검사, DIN 45687 부속서 F의 통계적 정확도 계산 | [확인] 제품 자료의 문구. 시험과제별 통과 목록 원문은 [미확인] |
| Trainings and Webinars, Demo versions | 교육, 체험판 | [확인] 제품 페이지 메뉴 |
| 사용자 설명서(Handbook)·도움말, Release notes | 화면 사용법, 판별 변경 사항 | 공개 웹에서 원문 [미확인](유지보수 계약자에게만 배포되는 것으로 보임) |

SoundPLAN 의 특징은 계산식을 공개 Technical Manual 로 내지 않는다는 점이다. 대신 **"표준 시험과제를 통과했다"는 적합성 선언과 품질 보증 자료**로 신뢰를 보인다. 독일에서는 DIN 45687(실외 소음 계산 소프트웨어 품질 요구, 2부 적합성 평가)과 ISO 17534-1 이 이 틀을 정한다.

## 4. 대응표

| 서류 종류 | AEDT | SoundPLAN | **SORI v0.1 결과물** | 상태 |
|---|---|---|---|---|
| **User's Manual** | User Manual | Handbook·도움말 | `docs/manual/SORI_User_Manual.pdf`(15쪽, 설치·화면 사용법·입력 서식·출력·문제 해결) | 있음 |
| 빠른 시작·예제 | (User Manual 안 예제) | Trainings, Demo versions | `README.md` 0.1절, `examples/jeonnong/`(설정·교통량·측정점)과 `README.md` | 있음 |
| 설치·시스템 요구사항 | Product Information, Downloads | Hardware recommendations | `docs/release/v0.1.md`(받는 파일, 서명 안 된 앱 실행법), GitHub Release 의 macOS·Windows zip | 있음(Windows 실제 화면 확인 안 됨) |
| 변경 기록 | 판별 매뉴얼 개정 | Release notes | Technical Manual 변경 이력 절, `git log`, `TODO.md` | 있음 |
| **Technical Manual** | Technical Manual | (공개 안 함, Standards 목록으로 대신) | `docs/manual/SORI_Technical_Manual.pdf`(14쪽, RLS-19 음원 식, ISO 9613-2·RLS-19 전파, 반사, 단면, 식↔함수 대응), `TECHNICAL_NOTES.md` | 있음 |
| 구현 기준 목록 | Technical Manual 안 | Standards | Technical Manual 1장, User's Manual 7장 설정 표(propagation_method) | 있음 |
| **검증·적합성** | UQ Report | 적합성 선언, 시험과제(ISO 17534-1, DIN 45687) | Technical Manual 검증 절(pytest 237건), `tests/test_test20_*.py`(BASt TEST-20 v2.1 대조), `tools/test20_report.py`, `tools/compare_methods.py` | 일부. **상용 프로그램(SoundPLAN) 대조 없음**, 불확도 정량화 없음 |
| 개발 기록(실험노트) | (내부 문서) | (내부 문서) | `docs/manual/SORI_앱개발노트.pdf`, `docs/매뉴얼_개발노트.md`, `docs/작업기록_*.md`, `field_measurement/2026-10-22/03_실험노트_초안.md` | 있음 |
| 제품 소개 | 지원 사이트 | Module description, Highlights 소개서 | `docs/release/v0.1.md` 첫머리 | 짧음. **포스터가 이 역할** |
| **Project** | (사용자가 사업별로 작성) | (사용자가 작성, 프로그램의 결과 문서화 기능 사용) | `field_survey/report/전농사거리_소음현황조사_결과보고서.pdf`(측정 결과·기준 비교·시뮬레이션 비교), `field_survey/plan/전농사거리_소음측정계획서.pdf`, `field_survey/forms/소음측정_야장*`, `field_measurement/2026-10-22/02_분석보고서.md` | 있음. 결과보고서 수정분 커밋 전 |

## 5. 빈 곳과 할 일

1. **검증 보고서(AEDT 의 UQ Report, SoundPLAN 의 적합성 선언에 해당)**: 지금은 TEST-20 대조와 회귀 시험이 Technical Manual 한 절에 들어 있다. 다음 셋을 묶은 `SORI 검증 보고서`를 따로 만들면 상용 소프트웨어 체계와 맞는다.
   - TEST-20 시험과제별 통과·불일치 표(다이어그램 III 불일치 원인 포함)
   - 현장 측정(2026-10-22)과 계산값 차이, 민감도(`field_measurement/2026-10-22/model_sens`)
   - SoundPLAN 결과와 같은 입력으로 비교(학회 포스터 1번 과제, SoundPLAN 결과 구할 곳 필요)
2. **제품 소개서(SoundPLAN 의 Module description·Highlights 에 해당)**: 학회 포스터와 함께 1~2쪽 SORI 소개서(기능, 구현 기준, 화면 그림 3장)를 만든다.
3. **Project 서류의 위치**: 전농사거리 소음지도 결과는 현황조사 결과보고서 안 "시뮬레이션 비교" 절에 있다. 소음지도(격자, 벽면, 단면)를 중심으로 한 `전농사거리 소음지도 보고서`를 따로 둘지 정해야 한다.
4. **User's Manual 보완**: 전농사거리 예제(수치지도 별도로 받기) 절, Windows 화면 그림.

## 출처

- AEDT 3g User Manual: <https://aedt.faa.gov/Documents/AEDT3g_UserManual.pdf>
- AEDT 3g Technical Manual: <https://aedt.faa.gov/Documents/AEDT3g_TechManual.pdf>
- AEDT 3e User Manual / Technical Manual: <https://aedt.faa.gov/Documents/AEDT3e_UserManual.pdf>, <https://aedt.faa.gov/Documents/AEDT3e_TechManual.pdf>
- AEDT 3c User Manual(ROSA P): <https://rosap.ntl.bts.gov/view/dot/49821>
- AEDT 2b Uncertainty Quantification Report: <https://aedt.faa.gov/Documents/AEDT2b_Uncertainty%20Quantification_Report.pdf>
- AEDT 2a Uncertainty Quantification Report: <https://aedt.faa.gov/Documents/AEDT%202a%20Uncertainty%20Quantification%20Report.pdf>
- AEDT 2a SP2 UQ Supplemental Report: <https://aedt.faa.gov/Documents/AEDT_2a_SP2_UQ_Supplemental_Report.pdf>
- AEDT 지원 사이트: <https://aedt.faa.gov/>
- SoundPLANnoise 제품 페이지: <https://www.soundplan.eu/en/software/soundplannoise>
- SoundPLANnoise 모듈 설명서(독일어판): <https://www.soundplan.eu/fileadmin/user_upload/soundplan-dateien/software/Modulbeschreibung_SoundPLANnoise__4_.pdf>
- SoundPLANnoise / SoundPLANessential 비교: <https://www.soundplan.eu/fileadmin/user_upload/soundplan-dateien/software/EN_Produktvergleich_essential_-_noise.pdf>
- SoundPLAN CNOSSOS-EU 적합성 선언(헝가리 정부 사이트): <https://cdn.2020-2025.kormany.hu//uploads/sheets//e/e1/e1f/e1fad1c3e2fe43945130afe646387dc.pdf>
- DIN 45687 개정안과 적합성 선언(DAGA 2021): <https://pub.dega-akustik.de/DAGA_2021/data/articles/000461.pdf>
- 독일 환경청, DIN 45687 품질 보증 시험과제 발표: <https://www.umweltbundesamt.de/system/files/medien/421/dokumente/06_qs_testaufgaben_krapf.pdf>
- Petersen & Kragh, 실외 소음 예측 소프트웨어 시험(IOA): <https://www.ioa.org.uk/system/files/proceedings/j_petersen_j_kragh_testing_of_software_for_outdoor_noise_prediction.pdf>
