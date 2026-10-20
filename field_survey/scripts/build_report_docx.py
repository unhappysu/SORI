"""전농사거리 소음현황조사 결과보고서 양식(docx) 생성.

※ 본 스크립트는 생성형 AI(Claude)를 활용하여 작성되었음.

    python field_survey/scripts/build_report_docx.py --out 폴더 [--pdf]

결과값 자리는 모두 빈칸 또는 "[측정 후 기입]"이며 예시 숫자를 넣지 않는다.
--pdf: Pages(macOS)로 검수용 PDF를 만든다(PDF용은 시스템 글꼴 Apple SD Gothic Neo).
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import docx_common  # noqa: E402
from docx_common import (INPUT_FILL, add_toc, bullets, caption, cover, h, keep_together, m_frac, m_r, m_sub, m_sum, m_sup,  # noqa: E402
                         math_para, new_document, note_box, page_break, para, table)
from survey_points import POINTS  # noqa: E402

OUT_NAME = "전농사거리_소음현황조사_보고서_양식.docx"
TBD = "[측정 후 기입]"
IN = "[  ]"
PIDS = [p["point_id"] for p in POINTS]


def blank_rows(n, ncol, first=None):
    return [[(first[i] if first and i < len(first) else "")] + [""] * (ncol - 1) for i in range(n)]


def yellow(rows, cols, start=1):
    return {(r, c): INPUT_FILL for r in range(start, len(rows)) for c in cols}


def figure_box(doc, text, height_mm=70):
    t = table(doc, [[f"[그림 자리] {text}"]], [166], header_rows=0, size=9.5, min_height_mm=height_mm,
              align="c", fills={(0, 0): "FAFAFA"})
    return t


def m_rad(e):
    return f"<m:rad><m:radPr><m:degHide m:val=\"1\"/></m:radPr><m:deg/><m:e>{e}</m:e></m:rad>"


def build(out):
    doc = new_document("전농사거리 소음현황조사 결과보고서")
    cover(doc, "전농사거리 소음현황조사 결과보고서",
          "현재 소음도 평가와 소음지도 시뮬레이션 검증 — 보고서 양식",
          [["과목", "소음진동설계"], ["대상", "전농사거리(사가정로 × 전농로) 주변"],
           ["측정일", TBD], ["작성일", IN], ["작성자", IN], ["문서 상태", "양식(결과 미기입)"]])
    add_toc(doc)

    # 1
    h(doc, "1. 요약", 1)
    note_box(doc, "작성 요령: 아래 항목을 3~6문장으로 적는다. 측정하지 않은 값은 쓰지 않고, 평가하지 못한 항목은 "
                  "'평가 불가'와 이유를 적는다.", fill=INPUT_FILL)
    table(doc, [
        ["항목", "내용"],
        ["측정 일시·지점 수", TBD],
        ["법정 평가 결과(목적 가)", TBD],
        ["시뮬레이션 비교 결과(목적 나)", TBD],
        ["주요 한계", TBD],
        ["소음지도 작업에 대한 시사점", TBD],
    ], [52, 114], min_height_mm=9, fills={(r, 1): INPUT_FILL for r in range(1, 6)})

    # 2
    h(doc, "2. 조사 개요", 1)
    h(doc, "2.1 목적", 2)
    bullets(doc, ["가. 현재 소음도 조사: 전농사거리 주변의 소음피해 현황을 국내 기준과 비교·평가",
                  "나. 시뮬레이션 검증: 측정값을 소음지도 소프트웨어(RLS-19 + ISO 9613-2) 계산값과 같은 위치·조건에서 비교"])
    h(doc, "2.2 일시·인원·장비", 2)
    rows = [["항목", "내용"], ["측정일·요일", TBD], ["예비일 사용 여부·이유", TBD], ["회차별 시각", TBD],
            ["조사 인원·역할", TBD], ["시나리오(장비 대수)", TBD]]
    table(doc, rows, [52, 114], min_height_mm=8.5, fills={(r, 1): INPUT_FILL for r in range(1, len(rows))})
    # Pages 는 표 안 '다음 단락과 함께'를 무시하므로, 장비 표가 2.2 와 같은 쪽에 들어가도록 위 표 높이를 줄였음
    rows = [["장비 ID", "모델", "일련번호", "등급", "교정기(모델·표시값)"]] + blank_rows(4, 5, ["SLM-1", "SLM-2", "SLM-3", "SLM-4"])
    keep_together(table(doc, rows, [22, 40, 34, 20, 50], min_height_mm=8, fills=yellow(rows, range(1, 5)), align="ccccc"))
    h(doc, "2.3 적용 기준", 2)
    table(doc, [
        ["구분", "문서", "현행본(2026-10-19 확인)"],
        ["측정방법", "소음·진동공정시험기준 ES 03301.1c(환경기준), ES 03304.1d(도로교통소음관리기준), ES 03300.c(총칙)",
         "국립환경과학원고시 제2026-39호(2026-09-14)"],
        ["환경기준", "환경정책기본법 시행령 [별표 1] 2. 소음", "대통령령 제36726호(2026-10-08 시행)"],
        ["관리기준", "소음·진동관리법 시행규칙 [별표 11] 1. 도로", "기후에너지환경부령 제42호(2026-06-22 시행)"],
    ], [24, 86, 56], size=9)
    para(doc, "보고서 제출 시점에 개정 여부를 다시 확인하고 확인일을 적는다: 확인일 " + IN, size=9.5)

    # 3
    h(doc, "3. 측정 방법", 1)
    h(doc, "3.1 측정 조건", 2)
    table(doc, [
        ["항목", "적용 내용"],
        ["측정기 설정", "A 특성, 동특성 빠름(fast), 샘플주기 1초 이하"],
        ["1회 측정시간", "10분(환경기준 5분 이상, 도로교통 10분 이상)"],
        ["측정 높이·위치", "지면 위 1.2~1.5 m. 도로변은 건축물에서 도로 방향 1.0 m 등(측정점별 4장 표 참조)"],
        ["교정", "회차 시작 전 교정, 회차 끝난 뒤 점검(부록 B)"],
        ["기상 조건", "풍속 2 m/s 이상 방풍망, 5 m/s 초과 측정 금지"],
        ["평균 방법", "법정 평가: 산술평균(원문 규정). 시뮬레이션 비교: 측정시간 가중 에너지평균(본 조사 자체 설정)"],
        ["배경소음 보정", "적용 방법에 조항이 없어 보정하지 않음"],
        ["회차 간 시간 간격 요건", "수동 확인 — 환경기준 낮 2시간 이상 간격 4회·밤 2시간 간격 2회(ES 03301.1c 5.3), 도로교통 "
                          "4시간 이상 간격 2회(ES 03304.1d 5.3.2·5.3.3). 야장·처리 스크립트는 자동 확인하지 않음. 확인 결과: " + TBD],
    ], [36, 130], size=9)
    h(doc, "3.2 계획 대비 변경 사항", 2)
    rows = [["항목", "계획", "실제", "변경 이유", "결과에 미치는 영향"]] + blank_rows(5, 5)
    table(doc, rows, [26, 34, 34, 36, 36], min_height_mm=11, fills=yellow(rows, range(5)))
    h(doc, "3.3 무효 처리한 측정", 2)
    rows = [["측정점", "회차", "무효 사유(풍속·교정·이상 소음 등)", "재측정 여부"]] + blank_rows(4, 4)
    table(doc, rows, [22, 20, 90, 34], min_height_mm=10, fills=yellow(rows, range(4)))

    # 4
    h(doc, "4. 측정 결과", 1)
    h(doc, "4.1 측정점 위치도", 2)
    figure_box(doc, "측정점 위치도(measurement_points.geojson을 지도에 표시, 축척·방위 표시)", 90)
    h(doc, "4.2 측정점별 결과표", 2)
    caption(doc, "표 4-1 측정점별 회차별 L_{Aeq} (단위 dB(A), 유효 측정만)")
    rows = [["측정점", "D1", "D2", "D3", "D4", "N1", "N2", "낮 산술평균", "밤 산술평균"]]
    rows += [[p] + [""] * 8 for p in PIDS]
    table(doc, rows, [16, 16, 16, 16, 16, 16, 16, 27, 27], size=9, min_height_mm=9,
          fills=yellow(rows, range(1, 9)), align="c" * 9)
    caption(doc, "표 4-2 측정점별 통계 지표(낮 시간대 회차 평균, 장비가 제공한 값만)")
    rows = [["측정점", "L_{Aeq}", "L_{Amax}", "L_{Amin}", "L_{10}", "L_{50}", "L_{90}", "비고"]]
    rows += [[p] + [""] * 7 for p in PIDS]
    table(doc, rows, [16, 19, 19, 19, 19, 19, 19, 36], size=9, min_height_mm=9,
          fills=yellow(rows, range(1, 8)), align="c" * 8)
    h(doc, "4.3 시간대별 변화", 2)
    figure_box(doc, "고정점 R1의 10분 L_{Aeq} 시계열과 같은 시각의 교통량(시간당 환산) 그래프", 75)
    figure_box(doc, "측정점별 회차별 L_{Aeq} 막대그래프(낮·밤 구분)", 65)
    h(doc, "4.4 동시 교통량·기상", 2)
    caption(doc, "표 4-3 회차별 접근로 교통량(대/h, 계수 차종별) — 야장 6_계산 시트 요약")
    rows = [["회차", "도로·접근로", "승용·승합", "소형화물", "버스", "중형화물", "대형화물", "이륜차", "평균 속도\n(km/h)"]]
    rows += [["", "", "", "", "", "", "", "", ""] for _ in range(8)]
    table(doc, rows, [14, 30, 17, 17, 14, 17, 17, 16, 24], size=8.5, min_height_mm=8.5,
          fills=yellow(rows, range(9)), align="c" * 9)
    rows = [["회차", "풍속(m/s)", "풍향", "기온(°C)", "습도(%)", "노면", "신호 주기(s)"]] + blank_rows(6, 7, ["D1", "D2", "D3", "D4", "N1", "N2"])
    table(doc, rows, [20, 24, 22, 24, 24, 24, 28], size=9, min_height_mm=8.5, fills=yellow(rows, range(1, 7)), align="c" * 7)

    # 5
    h(doc, "5. 기준 비교 평가(목적 가)", 1)
    h(doc, "5.1 적용 지역 구분과 기준값", 2)
    para(doc, "용도지역은 토지이음(eum.go.kr) 토지이용계획으로 확인한 결과를 적는다. 학교·종합병원 등 부지경계 50 m 이내 여부도 함께 적는다.",
         size=9.5)
    rows = [["측정점", "용도지역", "50 m 규정", "별표 1 구분", "별표 1 기준\n낮/밤", "별표 11 구분", "별표 11 기준\n주간/야간"]]
    rows += [[p] + [""] * 6 for p in PIDS]
    table(doc, rows, [16, 26, 20, 26, 26, 26, 26], size=9, min_height_mm=9, fills=yellow(rows, range(1, 7)), align="c" * 7)
    h(doc, "5.2 소음 환경기준 비교", 2)
    caption(doc, "표 5-1 측정소음도(산술평균, 정수)와 환경기준 비교 (dB(A))")
    rows = [["측정점", "낮 측정소음도", "낮 기준", "낮 판정", "밤 측정소음도", "밤 기준", "밤 판정", "횟수 충족\n(낮 4·밤 2)"]]
    rows += [[p] + [""] * 7 for p in PIDS]
    table(doc, rows, [16, 22, 18, 20, 22, 18, 20, 30], size=9, min_height_mm=9, fills=yellow(rows, range(1, 8)), align="c" * 8)
    h(doc, "5.3 교통소음 관리기준(도로) 비교", 2)
    rows = [["구분", "대상 지점", "측정소음도(지점별 산술평균의 산술평균)", "기준", "판정"],
            ["주간", "", "", "", ""], ["야간", "", "", "", ""]]
    table(doc, rows, [18, 40, 62, 20, 26], size=9, min_height_mm=10, fills=yellow(rows, range(1, 5)), align="c" * 5)
    h(doc, "5.4 평가 의견", 2)
    note_box(doc, f"{TBD} — 초과 지점, 초과 폭, 시간대별 특징. 평가하지 못한 시간대(예: 야간 미측정)는 '평가 불가'로 적는다.",
             fill=INPUT_FILL)

    # 6
    h(doc, "6. 시뮬레이션 비교(목적 나)", 1)
    h(doc, "6.1 비교 조건", 2)
    rows = [["항목", "내용"],
            ["모델 버전·설정 파일", TBD], ["입력 교통량", "회차별 동시 계수값(시간당 환산) — 계수하지 못한 도로는 표시: " + TBD],
            ["계수 차종 → Pkw/Lkw1/Lkw2 대응", TBD], ["수음점 위치·높이", "측정점과 같은 좌표·높이(사용한 수음점 ID): " + TBD],
            ["측정값 평균", "측정시간 가중 에너지평균(자체 설정)"], ["이상 소음 구간 처리", TBD]]
    table(doc, rows, [52, 114], size=9, min_height_mm=9, fills={(r, 1): INPUT_FILL for r in range(1, len(rows))})
    h(doc, "6.2 측정값과 계산값", 2)
    caption(doc, "표 6-1 측정 L_{Aeq}와 계산 L_{Aeq} 비교 (dB(A), 차이 = 측정 − 계산)")
    rows = [["측정점", "시간대", "측정 L_{Aeq}", "계산 L_{Aeq}", "차이", "비고"]]
    rows += [[p, per, "", "", "", ""] for p in PIDS for per in ("낮", "밤")]
    table(doc, rows, [16, 26, 26, 26, 20, 52], size=9, min_height_mm=8, fills=yellow(rows, range(2, 6)), align="cccccl")
    h(doc, "6.3 차이 통계", 2)
    intro = para(doc, "차이 d_{i} = L_{측정,i} − L_{계산,i}, 비교 점 수 n. 평균 차이와 표준편차(표본)는 다음과 같다.", size=9.5)
    d_i = m_sub(m_r("d"), m_r("i"))
    dbar = f"<m:bar><m:barPr><m:pos m:val=\"top\"/></m:barPr><m:e>{m_r('d')}</m:e></m:bar>"
    f1 = math_para(doc, dbar + m_r("=", False) + m_frac(m_r("1", False), m_r("n")) + m_sum(d_i, m_r("i") + m_r("=1", False), m_r("n")))
    f2 = math_para(doc, m_r("s") + m_r("=", False) + m_rad(m_frac(
        m_sum(m_sup(f"<m:d><m:e>{d_i}{m_r('−', False)}{dbar}</m:e></m:d>", m_r("2", False)), m_r("i") + m_r("=1", False), m_r("n")),
        m_r("n") + m_r("−1", False))))
    for p_ in (intro, f1, f2):                           # 설명·두 수식·통계표 머리가 한 쪽에 오도록
        p_.paragraph_format.keep_with_next = True
    rows = [["지표", "값(dB)", "비고"], ["비교 점 수 n", "", ""], ["평균 차이", "", "양수면 모델이 낮게 계산"],
            ["표준편차", "", ""], ["최대 차이(절댓값)", "", "해당 측정점·회차"]]
    table(doc, rows, [50, 40, 76], size=9, min_height_mm=9, fills={(r, 1): INPUT_FILL for r in range(1, 5)})
    figure_box(doc, "측정 L_{Aeq}(가로축) 대 계산 L_{Aeq}(세로축) 산점도와 1:1 선", 80)
    h(doc, "6.4 차이 원인 검토", 2)
    rows = [["검토 항목", "확인 내용", "영향 방향·크기(추정이면 '추정')"],
            ["교통량 입력", "동시 계수값과 모델 입력의 일치, 미계수 도로", ""],
            ["차종 구분", "계수 차종 → Pkw/Lkw1/Lkw2 대응, 이륜차 처리", ""],
            ["반사", "측정점 주변 반사면과 모델 반사 차수·수음점 위치(파사드 5 cm 대 측정 1.0 m 등)", ""],
            ["신호 대기", "정지·출발, 교차로 보정(RLS-19) 적용 범위", ""],
            ["배경소음", "도로 외 소음(이상 소음 코드) 포함 구간", ""],
            ["기상", "풍속·풍향, 노면 상태, 기온·습도와 모델 설정(propagation.temperature_C 15 °C·humidity_pct 70 %, 코드 기본값)", ""],
            ["주행속도", "측정 속도와 모델 입력 속도(교통량 파일에 속도가 없으면 간선 50 km/h·그 밖 30 km/h, "
                     "input.traffic.default_speed_main/minor)", ""],
            ["기타", "", ""]]
    table(doc, rows, [30, 80, 56], size=9, min_height_mm=11, fills=yellow(rows, (2,)))

    # 7
    h(doc, "7. 불확실성 및 한계", 1)
    rows = [["항목", "내용"],
            ["측정기", "클래스 등급, 교정 전후 편차(부록 B) " + TBD],
            ["측정 횟수·시간", "회차 수, 1회 10분의 대표성 " + TBD],
            ["교통량", "계수 방법(영상/현장), 5분 계수 오차, 미계수 도로 " + TBD],
            ["위치·좌표", "GPS 오차, 모델 수음점과의 위치 차이 " + TBD],
            ["기상", "측정 중 풍속 변화, 노면 " + TBD],
            ["모델", "가정값 입력(차로 수·노면·종단경사 등), 반사 차수 " + TBD]]
    table(doc, rows, [36, 130], size=9, min_height_mm=10, fills={(r, 1): INPUT_FILL for r in range(1, len(rows))})

    # 8
    h(doc, "8. 결론 및 소음지도 작업에 대한 시사점", 1)
    h(doc, "8.1 결론", 2)
    note_box(doc, TBD, fill=INPUT_FILL)
    h(doc, "8.2 소음지도 작업에 대한 시사점", 2)
    rows = [["구분", "내용"], ["입력 자료 보완", TBD], ["모델 설정 조정 검토", TBD], ["추가 측정 필요", TBD]]
    table(doc, rows, [40, 126], size=9, min_height_mm=12, fills={(r, 1): INPUT_FILL for r in range(1, len(rows))})

    # 부록
    page_break(doc)
    h(doc, "부록 A. 원자료 목록", 1)
    rows = [["파일명", "종류", "측정점·회차", "보관 위치", "비고"]] + blank_rows(8, 5)
    table(doc, rows, [52, 20, 28, 40, 26], size=9, min_height_mm=8.5, fills=yellow(rows, range(5)))
    h(doc, "부록 B. 교정 기록", 1)
    rows = [["회차", "장비", "교정 전", "교정 후", "회차 후", "차이", "판정"]] + blank_rows(8, 7)
    table(doc, rows, [18, 22, 24, 24, 24, 22, 32], size=9, min_height_mm=8.5, fills=yellow(rows, range(7)), align="c" * 7)
    para(doc, "교정 전후 허용 편차는 공정시험기준에 규정이 없어(미확인) 본 조사에서 정한 값을 적는다: " + IN + " dB", size=9.5)
    h(doc, "부록 C. 현장 사진", 1)
    for p in PIDS[:2]:
        figure_box(doc, f"{p} 설치 모습·주변(사진 번호 {IN})", 55)
    para(doc, "나머지 측정점도 같은 형식으로 넣는다. 얼굴·번호판은 흐리게 처리한다.", size=9.5)
    h(doc, "부록 D. 교통량 원자료", 1)
    para(doc, "야장(소음측정_야장.xlsx) 4_교통량·4b_신호속도 시트를 그대로 첨부하거나, 회차별 5분 계수표를 표로 옮긴다.", size=9.5)
    doc.save(out)
    return out


if __name__ == "__main__":
    out_dir = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else Path(__file__).resolve().parents[1] / "report"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = build(out_dir / OUT_NAME)
    print("저장:", out)
    if "--pdf" in sys.argv:
        tmp = Path(tempfile.mkdtemp(prefix="fs_pdf_"))         # PDF용 임시 docx는 저장소 밖에
        docx_common.set_font("Apple SD Gothic Neo")
        src = build(tmp / "for_pdf.docx")
        docx_common.export_pdf(src, out.with_suffix(".pdf"), tmp)
        print("PDF:", out.with_suffix(".pdf"))
