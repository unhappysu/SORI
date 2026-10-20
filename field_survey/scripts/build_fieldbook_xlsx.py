"""전농사거리 소음측정 야장(xlsx) 생성.

※ 본 스크립트는 생성형 AI(Claude)를 활용하여 작성되었음.

    python field_survey/scripts/build_fieldbook_xlsx.py --out 폴더

시트: 1_측정개요, 2_측정점, 3_소음기록, 4_교통량, 4b_신호속도, 5_기상, 6_계산, 목록(숨김)
- 입력 칸: 연한 노랑(잠금 해제). 수식 칸: 연한 파랑(잠금). 시트 보호는 암호 없이 걸어 둠.
- 법정 평가: 산술평균(ES 03301.1c 5.3, ES 03304.1d 7.1.1.2). 계산 중 소수 첫째 자리, 최종 정수.
- 시뮬레이션 비교: 측정시간 가중 에너지평균 L = 10 lg(Σ T_i·10^(L_i/10) / Σ T_i) — 본 조사 자체 설정.
- 배경소음 보정: 적용 방법(ES 03301.1c, ES 03304.1d)에 조항이 없어 넣지 않음.
"""
import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Protection, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

sys.path.insert(0, str(Path(__file__).resolve().parent))
from survey_points import POINTS  # noqa: E402

AI_NOTE = "본 문서는 생성형 AI(Claude)를 활용하여 작성되었음"
FONT = "맑은 고딕"
F_IN = PatternFill("solid", fgColor="FFF8DC")
F_CALC = PatternFill("solid", fgColor="E3ECF7")
F_HEAD = PatternFill("solid", fgColor="D9E2F3")
F_FIX = PatternFill("solid", fgColor="F2F2F2")
THIN = Side(style="thin", color="A6A6A6")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

N_NOISE = 200     # 소음 기록 행 수
N_TRAFFIC = 600   # 교통량 행 수(5분 × 접근로 × 방향)
N_SIGNAL = 40
N_SPEED = 200
N_WEATHER = 80
ROUNDS = ["D1", "D2", "D3", "D4", "N1", "N2"]
DEVICES = ["SLM-1", "SLM-2", "SLM-3", "SLM-4"]
ROADS = ["사가정로 서측", "사가정로 동측", "전농로 남측", "전농로25길 북측", "사가정로5길", "전농로29길", "사가정로17길", "기타"]
VEH = ["1 승용·승합", "2 소형화물", "3 버스", "4 중형화물", "5 대형화물", "6 이륜차"]
H0 = 4            # 머리글 행(데이터는 H0+1부터)


def font(bold=False, size=10, color="000000"):
    return Font(name=FONT, bold=bold, size=size, color=color)


def setup(ws, title, widths, landscape=True):
    ws["A1"] = title
    ws["A1"].font = font(True, 13, "1F3864")
    ws["A2"] = f"{AI_NOTE}.  노랑 = 입력 칸, 파랑 = 자동 계산(잠금)."
    ws["A2"].font = font(False, 9, "595959")
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.sheet_view.zoomScale = 100
    ps = ws.page_setup
    ps.paperSize = ws.PAPERSIZE_A4
    ps.orientation = "landscape" if landscape else "portrait"
    ps.fitToWidth, ps.fitToHeight = 1, 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.oddFooter.left.text = AI_NOTE
    ws.oddFooter.left.size = 8
    ws.oddFooter.right.text = "&P / &N"
    ws.oddFooter.right.size = 8
    ws.print_title_rows = f"{H0}:{H0}"
    ws.protection.sheet = True          # 암호 없음: 필요하면 검토 → 시트 보호 해제
    ws.protection.formatColumns = False
    ws.protection.formatRows = False


def header(ws, row, names):
    for c, n in enumerate(names, start=1):
        cell = ws.cell(row, c, n)
        cell.font = font(True, 10)
        cell.fill = F_HEAD
        cell.border = BOX
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[row].height = 32


def inp(cell, value=None, fmt=None):
    if value is not None:
        cell.value = value
    cell.fill = F_IN
    cell.border = BOX
    cell.font = font()
    cell.protection = Protection(locked=False)
    cell.alignment = Alignment(vertical="center", wrap_text=True)
    if fmt:
        cell.number_format = fmt


def calc(cell, formula, fmt=None):
    cell.value = formula
    cell.fill = F_CALC
    cell.border = BOX
    cell.font = font()
    cell.protection = Protection(locked=True)
    cell.alignment = Alignment(horizontal="center", vertical="center")
    if fmt:
        cell.number_format = fmt


def fixed(cell, value, bold=False):
    cell.value = value
    cell.fill = F_FIX
    cell.border = BOX
    cell.font = font(bold)
    cell.alignment = Alignment(vertical="center", wrap_text=True)


def dv_list(ws, formula, rng):
    dv = DataValidation(type="list", formula1=formula, allow_blank=True)
    dv.error, dv.errorTitle = "목록에서 고르세요", "입력 오류"
    ws.add_data_validation(dv)
    dv.add(rng)


def dv_num(ws, rng, lo, hi):
    dv = DataValidation(type="decimal", operator="between", formula1=str(lo), formula2=str(hi), allow_blank=True)
    dv.error, dv.errorTitle = f"{lo}~{hi} 사이 숫자를 넣으세요", "입력 오류"
    ws.add_data_validation(dv)
    dv.add(rng)


def lists_sheet(wb):
    ws = wb.create_sheet("목록")
    cols = {"A": ("회차", ROUNDS), "B": ("장비", DEVICES), "C": ("도로", ROADS),
            "D": ("Y/N", ["Y", "N"]), "E": ("노면", ["건조", "젖음"]), "F": ("강우", ["없음", "있음"]),
            "G": ("계수방법", ["영상", "현장"]), "H": ("basis", ["measured", "estimated", "assumed"]),
            "I": ("방향", ["진입", "진출", "동행", "서행", "남행", "북행"]),
            "J": ("정체", ["원활", "서행", "정체"])}
    ref = {}
    for col, (name, vals) in cols.items():
        ws[f"{col}1"] = name
        for i, v in enumerate(vals, start=2):
            ws[f"{col}{i}"] = v
        ref[name] = f"=목록!${col}$2:${col}${len(vals) + 1}"
    ws.sheet_state = "hidden"
    return ref


# ───────────────────────── 1 측정 개요
def sheet_overview(wb, L):
    ws = wb.active
    ws.title = "1_측정개요"
    setup(ws, "1. 측정 개요", [16, 14, 16, 16, 16, 16, 16, 18, 22], landscape=False)
    r = H0
    items = [("측정일", "yyyy-mm-dd"), ("요일", None), ("예비일 사용 여부·이유", None), ("시나리오(A/B/C)", None),
             ("측정자(총괄·기록)", None), ("측정자(SLM-1~4)", None), ("교통 계수 담당", None),
             ("기상 개요(맑음/흐림 등)", None), ("시각 동기 기준 시계", None), ("교정 전후 허용 편차(dB)", "0.0")]
    for name, fmt in items:
        fixed(ws.cell(r, 1), name, bold=True)
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=5)
        inp(ws.cell(r, 2), fmt=fmt)
        for c in range(3, 6):
            ws.cell(r, c).border = BOX
            ws.cell(r, c).protection = Protection(locked=False)
        r += 1
    thr = "$B$13"   # 허용 편차 칸
    ws.cell(r, 1, "허용 편차는 공정시험기준에 규정이 없음(미확인). 지도교수 확인 후 입력. 비워 두면 판정 '기준 미정'.").font = font(False, 9, "C00000")
    r += 2
    ws.cell(r, 1, "장비").font = font(True, 11, "1F3864")
    r += 1
    header(ws, r, ["장비 ID", "모델", "일련번호", "등급(Class)", "1초 Leq 저장", "교정기 모델", "교정기 일련번호",
                   "교정기 표시값(dB)", "시각 오차(초, 기준−장비)"])
    for d in DEVICES:
        r += 1
        fixed(ws.cell(r, 1), d)
        for c in range(2, 10):
            inp(ws.cell(r, c), fmt="0.0" if c == 8 else None)
    r += 2
    ws.cell(r, 1, "교정 기록(회차 시작 전 교정, 회차 끝난 뒤 점검)").font = font(True, 11, "1F3864")
    r += 1
    header(ws, r, ["회차", "장비", "날짜", "교정 전 지시값(dB)", "교정 후 지시값(dB)", "회차 후 지시값(dB)",
                   "회차 전후 차이(dB)", "판정", "메모"])
    for rd in ROUNDS:
        for d in DEVICES:
            r += 1
            fixed(ws.cell(r, 1), rd)
            fixed(ws.cell(r, 2), d)
            inp(ws.cell(r, 3), fmt="yyyy-mm-dd")
            for c in (4, 5, 6):
                inp(ws.cell(r, c), fmt="0.0")
            calc(ws.cell(r, 7), f'=IF(OR(E{r}="",F{r}=""),"",ROUND(F{r}-E{r},1))', "0.0")
            calc(ws.cell(r, 8), f'=IF(G{r}="","",IF({thr}="","기준 미정",IF(ABS(G{r})<={thr},"적합","무효 후보")))')
            inp(ws.cell(r, 9))
    dv_num(ws, f"D{r - 23}:F{r}", 30, 140)
    ws.freeze_panes = None


# ───────────────────────── 2 측정점
def sheet_points(wb, L):
    ws = wb.create_sheet("2_측정점")
    heads = ["ID", "유형", "위치 설명", "경도(WGS84)", "위도(WGS84)", "X(EPSG:5186)", "Y(EPSG:5186)", "높이(m)",
             "차도 끝 거리(m)", "도로 중심선 거리(m)", "반사면 이격(m)", "지역 구분", "용도지역", "주변 상황",
             "사진 번호", "모델 수음점 ID", "좌표 basis"]
    setup(ws, "2. 측정점 정보", [6, 6, 40, 13, 13, 13, 13, 8, 11, 11, 11, 12, 12, 30, 14, 12, 11])
    header(ws, H0, heads)
    r = H0
    for p in POINTS:
        r += 1
        fixed(ws.cell(r, 1), p["point_id"], bold=True)
        fixed(ws.cell(r, 2), p["type"])
        inp(ws.cell(r, 3), p["location_desc"])
        for c in (4, 5):
            inp(ws.cell(r, c), fmt="0.000000")
        for c in (6, 7):
            inp(ws.cell(r, c), fmt="0.0")
        inp(ws.cell(r, 8), float(p["height_m"]) if p["height_m"] else None, "0.0")
        for c in (9, 10, 11):
            inp(ws.cell(r, c), fmt="0.0")
        inp(ws.cell(r, 12), p["area_class"])
        for c in range(13, 17):
            inp(ws.cell(r, c))
        inp(ws.cell(r, 17))
        ws.row_dimensions[r].height = 30
    for _ in range(3):     # 추가 측정점용 빈 행
        r += 1
        for c in range(1, 18):
            inp(ws.cell(r, c))
    dv_list(ws, L["basis"], f"Q{H0 + 1}:Q{r}")
    ws.freeze_panes = f"B{H0 + 1}"
    return f"='2_측정점'!$A${H0 + 1}:$A${r}", r


# ───────────────────────── 3 소음 기록
NOISE_HEAD = ["측정점", "회차", "시간대", "날짜", "장비", "시작 시각", "종료 시각", "측정시간(분)",
              "LAeq", "LAmax", "LAmin", "L10", "L50", "L90", "배경소음(참고)", "풍속(m/s)",
              "이상 소음(시각·코드·메모)", "유효(Y/N)", "무효 사유", "시간 요건(10분)", "풍속 판정",
              "교통량 참조", "basis", "에너지 가중값", "가중 시간", "유효 LAeq(1/0)"]


def sheet_noise(wb, L, pt_ref):
    ws = wb.create_sheet("3_소음기록")
    setup(ws, "3. 소음 기록 (단위 dB(A), 시각은 기준 시계)",
          [8, 7, 7, 11, 8, 9, 9, 9, 8, 8, 8, 8, 8, 8, 9, 8, 32, 8, 18, 10, 13, 12, 10, 12, 9, 9])
    header(ws, H0, NOISE_HEAD)
    first, last = H0 + 1, H0 + N_NOISE
    for r in range(first, last + 1):
        inp(ws.cell(r, 1)); inp(ws.cell(r, 2))
        calc(ws.cell(r, 3), f'=IF(B{r}="","",IF(LEFT(B{r},1)="N","밤","낮"))')
        inp(ws.cell(r, 4), fmt="yyyy-mm-dd"); inp(ws.cell(r, 5))
        inp(ws.cell(r, 6), fmt="hh:mm:ss"); inp(ws.cell(r, 7), fmt="hh:mm:ss")
        calc(ws.cell(r, 8), f'=IF(OR(F{r}="",G{r}=""),"",ROUND(MOD(G{r}-F{r},1)*1440,1))', "0.0")
        for c in range(9, 17):
            inp(ws.cell(r, c), fmt="0.0")
        inp(ws.cell(r, 17)); inp(ws.cell(r, 18)); inp(ws.cell(r, 19))
        calc(ws.cell(r, 20), f'=IF(H{r}="","",IF(H{r}>=10,"충족","10분 미만"))')
        calc(ws.cell(r, 21), f'=IF(P{r}="","",IF(P{r}>5,"5 m/s 초과(무효)",IF(P{r}>=2,"방풍망 필수","적합")))')
        inp(ws.cell(r, 22)); inp(ws.cell(r, 23), None)
        ok = f'AND(R{r}="Y",ISNUMBER(I{r}),ISNUMBER(H{r}))'
        calc(ws.cell(r, 24), f'=IF({ok},H{r}*10^(I{r}/10),0)', "0.000E+00")
        calc(ws.cell(r, 25), f'=IF({ok},H{r},0)', "0.0")
        calc(ws.cell(r, 26), f'=IF(AND(R{r}="Y",ISNUMBER(I{r})),1,0)', "0")
    dv_list(ws, pt_ref, f"A{first}:A{last}")
    dv_list(ws, L["회차"], f"B{first}:B{last}")
    dv_list(ws, L["장비"], f"E{first}:E{last}")
    dv_list(ws, L["Y/N"], f"R{first}:R{last}")
    dv_list(ws, L["basis"], f"W{first}:W{last}")
    dv_num(ws, f"I{first}:O{last}", 20, 140)
    dv_num(ws, f"P{first}:P{last}", 0, 30)
    ws.freeze_panes = f"C{first}"
    return first, last


# ───────────────────────── 4 교통량
TRAFFIC_HEAD = ["회차", "날짜", "구간 시작", "구간 끝", "도로·접근로", "방향", *VEH, "자동차 합계(1~5)",
                "시간당 환산(대/h)", "계수 방법", "basis", "메모", "계수 구간"]


def sheet_traffic(wb, L):
    ws = wb.create_sheet("4_교통량")
    setup(ws, "4. 교통량 계수표 (5분 단위, 방향 × 차종, 이륜차 별도)",
          [7, 11, 9, 9, 16, 8, 9, 9, 9, 9, 9, 9, 11, 11, 9, 10, 24, 8])
    header(ws, H0, TRAFFIC_HEAD)
    first, last = H0 + 1, H0 + N_TRAFFIC
    for r in range(first, last + 1):
        inp(ws.cell(r, 1)); inp(ws.cell(r, 2), fmt="yyyy-mm-dd"); inp(ws.cell(r, 3), fmt="hh:mm")
        calc(ws.cell(r, 4), f'=IF(C{r}="","",C{r}+TIME(0,5,0))', "hh:mm")
        inp(ws.cell(r, 5)); inp(ws.cell(r, 6))
        for c in range(7, 13):
            inp(ws.cell(r, c), fmt="0")
        calc(ws.cell(r, 13), f'=IF(COUNT(G{r}:K{r})=0,"",SUM(G{r}:K{r}))', "0")
        calc(ws.cell(r, 14), f'=IF(OR(M{r}="",D{r}=""),"",ROUND(M{r}*60/(MOD(D{r}-C{r},1)*1440),0))', "0")
        inp(ws.cell(r, 15)); inp(ws.cell(r, 16)); inp(ws.cell(r, 17))
        calc(ws.cell(r, 18), f'=IF(M{r}="",0,1)', "0")
    dv_list(ws, L["회차"], f"A{first}:A{last}")
    dv_list(ws, L["도로"], f"E{first}:E{last}")
    dv_list(ws, L["방향"], f"F{first}:F{last}")
    dv_list(ws, L["계수방법"], f"O{first}:O{last}")
    dv_list(ws, L["basis"], f"P{first}:P{last}")
    dv_num(ws, f"G{first}:L{last}", 0, 2000)
    ws.cell(3, 1, "계수하지 못한 구간은 0을 넣지 말고 비워 둔다(미계수). 차종 → 소프트웨어 차종(Pkw/Lkw1/Lkw2) 대응은 6_계산 시트에서 입력.").font = font(False, 9, "C00000")
    ws.freeze_panes = f"G{first}"
    return first, last


def sheet_signal_speed(wb, L):
    ws = wb.create_sheet("4b_신호속도")
    setup(ws, "4b. 신호 주기·정체·주행속도", [7, 11, 9, 11, 14, 14, 14, 14, 4, 7, 16, 8, 11, 11, 11, 20])
    header(ws, H0, ["회차", "날짜", "시각", "주기 길이(s)", "녹색: 사가정로 서측(s)", "녹색: 사가정로 동측(s)",
                    "녹색: 전농로 남측(s)", "녹색: 전농로25길 북측(s)", "",
                    "회차", "도로·접근로", "방향", "구간 거리(m)", "통과 시간(s)", "속도(km/h)", "메모"])
    ws.cell(H0, 9).fill = PatternFill(None)
    for r in range(H0 + 1, H0 + 1 + max(N_SIGNAL, N_SPEED)):
        if r <= H0 + N_SIGNAL:
            inp(ws.cell(r, 1)); inp(ws.cell(r, 2), fmt="yyyy-mm-dd"); inp(ws.cell(r, 3), fmt="hh:mm")
            for c in range(4, 9):
                inp(ws.cell(r, c), fmt="0")
        inp(ws.cell(r, 10)); inp(ws.cell(r, 11)); inp(ws.cell(r, 12))
        inp(ws.cell(r, 13), fmt="0.0"); inp(ws.cell(r, 14), fmt="0.0")
        calc(ws.cell(r, 15), f'=IF(OR(M{r}="",N{r}="",N{r}=0),"",ROUND(3.6*M{r}/N{r},1))', "0.0")
        inp(ws.cell(r, 16))
    end_sig, end_sp = H0 + N_SIGNAL, H0 + N_SPEED
    dv_list(ws, L["회차"], f"A{H0 + 1}:A{end_sig}")
    dv_list(ws, L["회차"], f"J{H0 + 1}:J{end_sp}")
    dv_list(ws, L["도로"], f"K{H0 + 1}:K{end_sp}")
    dv_list(ws, L["방향"], f"L{H0 + 1}:L{end_sp}")
    ws.cell(3, 10, "속도 = 3.6 × 거리(m) / 시간(s). 신호에 막히지 않은 차량만(자체 방법).").font = font(False, 9, "595959")
    ws.freeze_panes = f"A{H0 + 1}"
    return H0 + 1, end_sp


# ───────────────────────── 5 기상
def sheet_weather(wb, L):
    ws = wb.create_sheet("5_기상")
    setup(ws, "5. 기상 기록 (풍속은 마이크로폰 높이)", [11, 9, 12, 10, 9, 9, 9, 9, 9, 18, 30], landscape=False)
    header(ws, H0, ["날짜", "시각", "위치·측정점", "풍속(m/s)", "풍향", "기온(°C)", "습도(%)", "노면", "강우",
                    "측정 조건 판정", "메모"])
    first, last = H0 + 1, H0 + N_WEATHER
    for r in range(first, last + 1):
        inp(ws.cell(r, 1), fmt="yyyy-mm-dd"); inp(ws.cell(r, 2), fmt="hh:mm"); inp(ws.cell(r, 3))
        inp(ws.cell(r, 4), fmt="0.0"); inp(ws.cell(r, 5)); inp(ws.cell(r, 6), fmt="0.0"); inp(ws.cell(r, 7), fmt="0")
        inp(ws.cell(r, 8)); inp(ws.cell(r, 9))
        calc(ws.cell(r, 10), f'=IF(AND(D{r}="",H{r}="",I{r}=""),"",IF(N(D{r})>5,"측정 금지(5 m/s 초과)",'
                             f'IF(OR(H{r}="젖음",I{r}="있음"),"연기(자체 기준)",IF(N(D{r})>=2,"방풍망 필수","가능"))))')
        inp(ws.cell(r, 11))
    dv_list(ws, L["노면"], f"H{first}:H{last}")
    dv_list(ws, L["강우"], f"I{first}:I{last}")
    dv_num(ws, f"D{first}:D{last}", 0, 40)
    ws.freeze_panes = f"A{first}"


# ───────────────────────── 6 계산
def sheet_calc(wb, L, n_first, n_last, t_first, t_last, sp_first, sp_last):
    ws = wb.create_sheet("6_계산")
    setup(ws, "6. 계산 (자동)", [8, 8, 11, 10, 11, 12, 8, 11, 10, 11, 12, 10, 10, 10, 10, 10, 10, 10, 10])
    N = "'3_소음기록'!"
    A = f"{N}$A${n_first}:$A${n_last}"
    C = f"{N}$C${n_first}:$C${n_last}"
    I_ = f"{N}$I${n_first}:$I${n_last}"
    X = f"{N}$X${n_first}:$X${n_last}"
    Y = f"{N}$Y${n_first}:$Y${n_last}"
    Z = f"{N}$Z${n_first}:$Z${n_last}"
    ws.cell(3, 1, "법정 평가는 산술평균(원문 규정), 시뮬레이션 비교는 측정시간 가중 에너지평균(자체 설정). "
                  "유효(Y)이고 LAeq가 있는 행만 씀. 배경소음 보정 없음.").font = font(False, 9, "595959")
    heads = ["측정점", "낮 n", "낮 산술평균", "낮 최종(정수)", "낮 횟수(환경기준 4회)", "낮 에너지평균(검증용)",
             "밤 n", "밤 산술평균", "밤 최종(정수)", "밤 횟수(환경기준 2회)", "밤 에너지평균(검증용)",
             "별표1 낮 기준", "별표1 밤 기준", "별표1 판정", "도로교통 대상(Y/N)", "별표11 주간 기준", "별표11 야간 기준",
             "도로교통 산입(주간)", "도로교통 산입(야간)"]
    header(ws, H0, heads)
    r = H0
    for i, p in enumerate(POINTS):
        r += 1
        pr = H0 + 1 + i
        calc(ws.cell(r, 1), f"='2_측정점'!A{pr}")
        for per, base, need in (("낮", 2, 4), ("밤", 7, 2)):
            nc, ac, fc, qc, ec = (get_column_letter(base + k) for k in range(5))
            cond = f'{A},$A{r},{C},"{per}",{Z},1'
            calc(ws.cell(r, base), f'=SUMIFS({Z},{A},$A{r},{C},"{per}")', "0")
            calc(ws.cell(r, base + 1), f'=IF({nc}{r}=0,"",ROUND(SUMIFS({I_},{cond})/{nc}{r},1))', "0.0")
            calc(ws.cell(r, base + 2), f'=IF({ac}{r}="","",ROUND({ac}{r},0))', "0")
            calc(ws.cell(r, base + 3), f'=IF({nc}{r}=0,"",IF({nc}{r}>={need},"충족","부족("&{nc}{r}&"회)"))')
            calc(ws.cell(r, base + 4), f'=IF(SUMIFS({Y},{A},$A{r},{C},"{per}")=0,"",'
                                       f'ROUND(10*LOG10(SUMIFS({X},{A},$A{r},{C},"{per}")/SUMIFS({Y},{A},$A{r},{C},"{per}")),1))',
                 "0.0")
        inp(ws.cell(r, 12), fmt="0"); inp(ws.cell(r, 13), fmt="0")
        calc(ws.cell(r, 14), f'=IF(OR(L{r}="",D{r}=""),"",IF(D{r}>L{r},"낮 초과",'
                             f'"낮 이하")&IF(OR(M{r}="",I{r}=""),"",IF(I{r}>M{r},", 밤 초과",", 밤 이하")))')
        inp(ws.cell(r, 15)); inp(ws.cell(r, 16), fmt="0"); inp(ws.cell(r, 17), fmt="0")
        calc(ws.cell(r, 18), f'=IF(AND(O{r}="Y",ISNUMBER(C{r})),1,0)', "0")
        calc(ws.cell(r, 19), f'=IF(AND(O{r}="Y",ISNUMBER(H{r})),1,0)', "0")
    last_pt = r
    dv_list(ws, L["Y/N"], f"O{H0 + 1}:O{last_pt}")

    # 도로교통 관리기준 측정소음도
    r += 2
    ws.cell(r, 1, "도로교통소음 관리기준 측정소음도(ES 03304.1d 7.1.1.2: 지점별 산술평균을 다시 산술평균)").font = font(True, 11, "1F3864")
    r += 1
    header(ws, r, ["구분", "지점 수", "산술평균", "최종(정수)", "별표11 기준(입력)", "판정", "", "", "", "", "", "", "", "", "", "", ""])
    rng = lambda col: f"${col}${H0 + 1}:${col}${last_pt}"  # noqa: E731
    for per, ac, fl in (("주간", "C", "R"), ("야간", "H", "S")):
        r += 1
        fixed(ws.cell(r, 1), per, bold=True)
        calc(ws.cell(r, 2), f'=SUM({rng(fl)})', "0")
        calc(ws.cell(r, 3), f'=IF(B{r}=0,"",ROUND(SUMIFS({rng(ac)},{rng(fl)},1)/B{r},1))', "0.0")
        calc(ws.cell(r, 4), f'=IF(C{r}="","",ROUND(C{r},0))', "0")
        inp(ws.cell(r, 5), fmt="0")
        calc(ws.cell(r, 6), f'=IF(OR(D{r}="",E{r}=""),"",IF(B{r}<2,"지점 2개 미만",IF(D{r}>E{r},"초과","이하")))')
    ws.cell(r + 1, 1, "주의: 회차 간 4시간 이상 간격(5.3.2·5.3.3)은 자동 확인하지 않음 — 3_소음기록의 시각으로 직접 확인.").font = font(False, 9, "C00000")

    # 교통량 요약
    r += 3
    ws.cell(r, 1, "교통량 요약(회차 × 도로, 양방향 시간당 = 합계 × 60 / (구간 행 수 × 5분 / 방향 수))").font = font(True, 11, "1F3864")
    r += 1
    T = "'4_교통량'!"
    tc = lambda col: f"{T}${col}${t_first}:${col}${t_last}"  # noqa: E731
    header(ws, r, ["회차", "도로·접근로", "계수 구간 행 수", *[v + "(대/h)" for v in VEH], "자동차 합계(대/h)",
                   "평균 속도(km/h)", "상태", "방향 수(입력)", "", "", ""])
    S = "'4b_신호속도'!"
    sc = lambda col: f"{S}${col}${sp_first}:${col}${sp_last}"  # noqa: E731
    for rd in ROUNDS:
        for road in ROADS[:4]:
            r += 1
            fixed(ws.cell(r, 1), rd); fixed(ws.cell(r, 2), road)
            cond = f'{tc("A")},$A{r},{tc("E")},$B{r}'
            calc(ws.cell(r, 3), f'=SUMIFS({tc("R")},{cond})', "0")
            per_h = f'*12*N($M{r})/$C{r}'
            for k, col in enumerate("GHIJKL"):
                calc(ws.cell(r, 4 + k), f'=IF(OR($C{r}=0,N($M{r})=0),"",ROUND(SUMIFS({tc(col)},{cond}){per_h},0))', "0")
            calc(ws.cell(r, 10), f'=IF(OR($C{r}=0,N($M{r})=0),"",ROUND(SUMIFS({tc("M")},{cond}){per_h},0))', "0")
            calc(ws.cell(r, 11), f'=IFERROR(ROUND(AVERAGEIFS({sc("O")},{sc("J")},$A{r},{sc("K")},$B{r}),1),"")', "0.0")
            calc(ws.cell(r, 12), f'=IF($C{r}=0,"미계수","계수 "&$C{r}&"행")')
            inp(ws.cell(r, 13), 2, "0")
    ws.cell(r + 1, 1, "4_교통량에 방향별로 따로 적은 행 수를 '방향 수'로 나누어 계수 시간을 구함(방향별 계수 구간이 같다고 가정). "
                      "방향을 나누지 않고 적었으면 방향 수 = 1.").font = font(False, 9, "C00000")

    # 차종 대응
    r += 3
    ws.cell(r, 1, "계수 차종 → 소프트웨어 차종(RLS-19) 대응 — RLS-19 원문 확인 후 입력(미확인)").font = font(True, 11, "1F3864")
    r += 1
    header(ws, r, ["계수 차종", "", "소프트웨어 차종", "", "", "", "", "", "", "", "", "", "", "", "", "", ""])
    for v in VEH:
        r += 1
        fixed(ws.cell(r, 1), v)
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=2)
        inp(ws.cell(r, 3))
    ws.freeze_panes = f"B{H0 + 1}"


def build(out_path):
    wb = Workbook()
    L = lists_sheet(wb)
    sheet_overview(wb, L)
    pt_ref, _ = sheet_points(wb, L)
    n_first, n_last = sheet_noise(wb, L, pt_ref)
    t_first, t_last = sheet_traffic(wb, L)
    sp_first, sp_last = sheet_signal_speed(wb, L)
    sheet_weather(wb, L)
    sheet_calc(wb, L, n_first, n_last, t_first, t_last, sp_first, sp_last)
    # 시트 순서: 목록을 맨 뒤로
    wb.move_sheet("목록", offset=len(wb.sheetnames))
    wb.active = 0
    wb.properties.creator = "전농사거리 소음현황조사(생성형 AI Claude 활용)"
    wb.properties.title = "소음측정 야장"
    wb.save(out_path)
    return out_path


if __name__ == "__main__":
    out_dir = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else Path(__file__).resolve().parents[1] / "forms"
    out_dir.mkdir(parents=True, exist_ok=True)
    print("저장:", build(out_dir / "소음측정_야장.xlsx"))
