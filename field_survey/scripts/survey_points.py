"""전농사거리 소음현황조사 — 측정점 계획 목록과 CSV/GeoJSON 생성.

※ 본 스크립트는 생성형 AI(Claude)를 활용하여 작성되었음.

측정점 2개(A1·A2)는 2026-10-21 확정. 좌표·거리는 수치지도에서 읽은 값이며 현장 답사 후
실측값으로 고친다(--from-csv).

사용법
    python field_survey/scripts/survey_points.py            # 계획 목록으로 CSV·GeoJSON 새로 만들기
    python field_survey/scripts/survey_points.py --from-csv # 사용자가 채운 CSV로 GeoJSON만 다시 만들기

GeoJSON 좌표는 WGS84 경위도(lon, lat)를 쓴다. 경위도가 비어 있는 점은 geometry 를 null 로 둔다
(RFC 7946 3.2 은 위치 없는 Feature 의 geometry 를 null 로 허용).
"""
import csv
import json
import sys
from pathlib import Path

PLAN_DIR = Path(__file__).resolve().parents[1] / "plan"
CSV_PATH = PLAN_DIR / "measurement_points.csv"
GEOJSON_PATH = PLAN_DIR / "measurement_points.geojson"

# 측정점 유형. 2026-10-21 확정(A안): 2팀 × 3명, 2지점 동시 측정
TYPE_NAMES = {
    "f": "도로변 주거 건물 외벽 앞(법정 평가·모델 검증 겸용)",
}

FIELDS = [
    "point_id", "type", "type_name", "location_desc", "nearest_road",
    "lon_wgs84", "lat_wgs84", "x_epsg5186", "y_epsg5186",
    "height_m", "dist_carriageway_edge_m", "dist_road_centerline_m", "dist_reflector_m",
    "method", "area_class", "rationale", "model_receiver_id", "coord_basis", "photo_no", "note",
]

# 선정 근거(2026-10-21 원문 확인)
#  - 「소음지도의 작성방법」 기후에너지환경부고시 제2026-19호 별표 2 제15호: 1시간 교통량 1,000대 이상인 도로당 한 지점 이상
#    → 사가정로·전농로 각 1지점. 제14호 라목: 외벽 소음은 건물에서 소음원 방향 1.0 m, 반사음 포함
#  - ES 03304.1d 5.1.1: 피해 예상자가 거주하는 건물 경계 1.0~3.5 m, 지면 위 1.2~1.5 m. 5.3.1: 측정지점 2개 이상
#  - ES 03301.1c 5.1.3(도로변지역): 건축물에서 도로 방향 1.0 m
# 좌표·거리는 수치지도 v2.0(2025) 건물·도로 중심선에서 읽은 값(현장 확인 필요). 외벽에서 도로 쪽 1.0 m 지점
POINTS = [
    dict(point_id="A1", type="f",
         location_desc="사가정로 동쪽 접근로 남측, 전농동하우스토리 아파트(20층, 사가정로 122) 외벽에서 도로 방향 1.0 m. "
                       "교차로 중심에서 약 55 m",
         nearest_road="사가정로", height_m="1.5",
         lon_wgs84="127.058450", lat_wgs84="37.577467", x_epsg5186="205163.1", y_epsg5186="553103.6",
         dist_road_centerline_m="16.8", dist_reflector_m="1.0",
         method="ES 03304.1d 5.1.1 · ES 03301.1c 5.1.3 · 소음지도 작성방법 별표 2 제14호 라목", area_class="도로변지역",
         rationale="사가정로(1시간 교통량 1,000대 이상 추정)에 직접 면한 공동주택. 피해 예상 거주 건물이며 모델 벽면 소음도가 높은 쪽",
         model_receiver_id="A1", coord_basis="지도 판독(현장 확인 필요)",
         note="도로폭 27 m(4차로, 수치지도). 아파트 단지 안이면 관리사무소 동의 필요. 교차로 120 m 안(RLS-19 신호 보정 구간)"),
    dict(point_id="A2", type="f",
         location_desc="전농로 남쪽 접근로 서측, 사가정로12길 5 주택(2층) 외벽에서 도로 방향 1.0 m. 교차로 중심에서 약 75 m",
         nearest_road="전농로", height_m="1.5",
         lon_wgs84="127.057503", lat_wgs84="37.576881", x_epsg5186="205079.5", y_epsg5186="553038.5",
         dist_road_centerline_m="23.9", dist_reflector_m="1.0",
         method="ES 03304.1d 5.1.1 · ES 03301.1c 5.1.3 · 소음지도 작성방법 별표 2 제14호 라목", area_class="도로변지역",
         rationale="전농로(1시간 교통량 1,000대 이상 추정)에 면한 맨 앞 주거 건물. 고시의 도로당 1지점 요건",
         model_receiver_id="A2", coord_basis="지도 판독(현장 확인 필요)",
         note="도로폭 25 m(5차로, 수치지도). 도로 중심선에서 약 24 m 들어간 건물 — 앞쪽 공지·주차 여부 현장 확인. 교차로 120 m 안"),
]

# 장비·팀(소음계 정보는 사용자가 나중에 입력)
DEVICES = ["SLM-1", "SLM-2"]
TEAMS = {"1팀": ("A1", "SLM-1", "사가정로"), "2팀": ("A2", "SLM-2", "전농로")}

# 교통량 계수 차종과 RLS-19 차량군 대응
#  RLS-19 2장(번역 원문/RLS19_main.tex): Lkw1 = 허용 총중량 3.5 t 초과 트레일러 없는 화물차 및 버스,
#  Lkw2 = 트레일러 부착 화물차·세미트레일러 연결차. 이륜자동차는 방출 측면에서 Lkw2 로 분류(속도는 v_Pkw).
#  소음지도 작성방법 별표 2 제12호 다목 표: RLS 소형 = 승용·소형트럭(2.5 t 미만)·중형트럭(2.5~3.5 t 미만),
#  대형 = 중형버스·대형트럭(3.5 t 이상)·대형버스, 트레일러 없으면 대형1, 있으면 대형2.
VEH_CLASSES = [  # (계수 차종, 예, RLS-19 차량군)
    ("1 승용·승합", "승용차, SUV, 15인 미만 승합", "Pkw"),
    ("2 소·중형 화물", "3.5 t 이하 화물(1톤·2.5톤 트럭, 밴)", "Pkw"),
    ("3 버스", "15인 이상 버스(시내·마을·광역·관광)", "Lkw1"),
    ("4 대형 화물", "3.5 t 초과, 트레일러 없음(카고·덤프·탱크)", "Lkw1"),
    ("5 트레일러", "트레일러·세미트레일러 연결차", "Lkw2"),
    ("6 이륜차", "오토바이·스쿠터(배달 포함)", "Lkw2(방출만, 속도는 Pkw)"),
]
VEH = [v[0].split(" ", 1)[1] for v in VEH_CLASSES]


def full_rows():
    rows = []
    for p in POINTS:
        r = {k: "" for k in FIELDS}
        r.update(p)
        r["type_name"] = TYPE_NAMES[p["type"]]
        rows.append(r)
    return rows


def write_csv(rows):
    with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def read_csv():
    with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_geojson(rows):
    feats = []
    for r in rows:
        lon, lat = r.get("lon_wgs84", "").strip(), r.get("lat_wgs84", "").strip()
        geom = {"type": "Point", "coordinates": [float(lon), float(lat)]} if lon and lat else None
        props = {k: v for k, v in r.items() if k not in ("lon_wgs84", "lat_wgs84")}
        feats.append({"type": "Feature", "geometry": geom, "properties": props})
    fc = {"type": "FeatureCollection",
          "name": "jeonnong_measurement_points",
          "note": "본 파일은 생성형 AI(Claude)를 활용하여 작성되었음. 좌표 미입력 점은 geometry=null",
          "features": feats}
    GEOJSON_PATH.write_text(json.dumps(fc, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    if "--from-csv" in sys.argv:
        rows = read_csv()
    else:
        rows = full_rows()
        write_csv(rows)
    write_geojson(rows)
    print(f"{len(rows)}개 측정점 → {CSV_PATH.name}, {GEOJSON_PATH.name}")
