"""전농사거리 소음현황조사 — 측정점 계획 목록과 CSV/GeoJSON 생성.

※ 본 스크립트는 생성형 AI(Claude)를 활용하여 작성되었음.

측정점의 좌표·거리는 현장 답사 후 사용자가 채운다(빈칸). 이 파일의 목록은
계획 단계의 후보이며, 위치 설명의 도로명은 수치지도(docs/review/arterials.csv)의
도로명에서 가져왔다.

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

# 측정점 유형: a 도로변 기준점, b 주거지 파사드 전면, c 이면도로/배후지, d 시뮬레이션 검증점
TYPE_NAMES = {
    "a": "도로변 기준점",
    "b": "주거지 파사드 전면",
    "c": "이면도로·배후지",
    "d": "시뮬레이션 검증점",
}

FIELDS = [
    "point_id", "type", "type_name", "location_desc", "nearest_road",
    "lon_wgs84", "lat_wgs84", "x_epsg5186", "y_epsg5186",
    "height_m", "dist_carriageway_edge_m", "dist_road_centerline_m", "dist_reflector_m",
    "method", "area_class", "rationale", "model_receiver_id", "coord_basis", "photo_no", "note",
]

# height_m: 계획값(공정시험기준 범위 1.2~1.5 m 안에서 1.5 m로 통일). F2 는 층 높이에 따름.
# area_class: 도로변지역/일반지역 — 도로단에서 차선수×10 m 안이면 도로변지역(ES 03301.1c 5.1.1 주 1)
POINTS = [
    dict(point_id="R1", type="a",
         location_desc="사가정로 보도, 교차로 중심에서 사가정로를 따라 [  ] m. 건물 전면에서 도로 방향 1.0 m",
         nearest_road="사가정로", height_m="1.5",
         method="ES 03301.1c(도로변지역)·ES 03304.1d", area_class="도로변지역",
         rationale="고정점: 측정 시간 전체 연속 측정, 다른 점의 시간 변동 보정 기준. 간선 교통 지배 지점",
         note="연속측정이면 높이 1.2~5.0 m 가능(ES 03301.1c 5.1.3)"),
    dict(point_id="R2", type="a",
         location_desc="전농로 남측 접근로 보도, 교차로에서 [  ] m. 건물 전면에서 도로 방향 1.0 m",
         nearest_road="전농로", height_m="1.5",
         method="ES 03301.1c(도로변지역)·ES 03304.1d", area_class="도로변지역",
         rationale="두 번째 간선 접근로의 도로변 소음. 도로교통 방법의 측정지점 2개 이상 요건(5.3.1) 충족"),
    dict(point_id="F1", type="b",
         location_desc="사가정로변 주거 건물 [건물명/번지] 경계에서 음원 방향 1.0~3.5 m",
         nearest_road="사가정로", height_m="1.5",
         method="ES 03304.1d 5.1.1", area_class="도로변지역",
         rationale="간선도로에 면한 주거 파사드의 피해 현황. 모델 파사드 수음점과 대응"),
    dict(point_id="F2", type="b",
         location_desc="전농로 또는 전농로25길변 공동주택 [  ]층 세대 창문 밖 0.5~1.0 m",
         nearest_road="전농로", height_m="",
         method="ES 03304.1d 5.1.3", area_class="도로변지역",
         rationale="2층 이상 세대의 높이별 노출. 거주자 동의가 있을 때만",
         note="외부 측정 불가로 창문 경계면에서 측정하면 +1.5 dB(A)(ES 03304.1d 5.1.3)"),
    dict(point_id="B1", type="c",
         location_desc="사가정로5길 또는 전농로29길(일방 1차로) 주거지, 반경 3.5 m 안 반사물 없는 곳",
         nearest_road="사가정로5길", height_m="1.5",
         method="ES 03301.1c(일반지역/도로변지역 판정 후)", area_class="[판정]",
         rationale="이면도로 주거지. 모델에서 교통량이 가정값인 이면도로의 영향 확인",
         note="1차로 이면도로는 별표 1의 '도로'(차선 2개 이상)에 해당하지 않을 수 있음 — 지역 구분 판정 필요"),
    dict(point_id="B2", type="c",
         location_desc="간선도로에서 떨어진 주거 블록 내부(사가정로17길 방향 배후지)",
         nearest_road="사가정로17길", height_m="1.5",
         method="ES 03301.1c(일반지역)", area_class="일반지역",
         rationale="간선도로가 건물에 가려진 배후지의 차폐 효과 확인"),
    dict(point_id="V1", type="d",
         location_desc="사가정로변 개활 지점, 교차로 중심에서 120 m 이상 떨어진 곳, 반경 3.5 m 안 반사물 없음",
         nearest_road="사가정로", height_m="1.5",
         method="본 조사 자체 설정(검증용) + ES 03301.1c 측정 조건 준용", area_class="도로변지역",
         rationale="반사·신호 대기 영향이 작은 곳에서 음원 모델(RLS-19) 확인. 모델 격자 수음점을 같은 좌표·높이에 둠",
         note="모델 점 수음점(receivers.points.path)으로 같은 좌표·높이 계산"),
    dict(point_id="V2", type="d",
         location_desc="교차로 중심에서 120 m 이내 사가정로 또는 전농로변 개활 지점",
         nearest_road="사가정로", height_m="1.5",
         method="본 조사 자체 설정(검증용) + ES 03301.1c 측정 조건 준용", area_class="도로변지역",
         rationale="신호교차로 보정(RLS-19 식 8, 120 m 안)과 정지·출발 소음의 영향 확인"),
    dict(point_id="V3", type="d",
         location_desc="건물로 일부 가려진 지점(사가정로 뒤편 첫 블록), 반경 3.5 m 안 반사물 없음",
         nearest_road="사가정로", height_m="1.5",
         method="본 조사 자체 설정(검증용)", area_class="[판정]",
         rationale="회절·차폐 계산(ISO 9613-2) 확인", note="장비 4대 이상일 때만"),
]


def full_rows():
    rows = []
    for p in POINTS:
        r = {k: "" for k in FIELDS}
        r.update(p)
        r["type_name"] = TYPE_NAMES[p["type"]]
        r["coord_basis"] = "미입력"
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
