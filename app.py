# -*- coding: utf-8 -*-
"""
도로 소음지도 만들기 — 웹 화면(Streamlit)
실행:  streamlit run app.py   (또는 run_app.bat / run_app.sh 더블클릭)
※ 본 프로그램은 Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import io
import json
import logging
import re
import shutil
import sys
import time
import traceback
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st
import yaml

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from noisemap import AI_NOTICE, __version__                     # noqa: E402
from noisemap import config as CFG                               # noqa: E402

try:
    import folium
    from streamlit_folium import st_folium
    HAVE_MAP = True
except ImportError:                                              # pragma: no cover
    HAVE_MAP = False

st.set_page_config(page_title="도로 소음지도 만들기", page_icon="🔊", layout="wide")

PROJECTS = ROOT / "projects"
CRS_DEFAULT = 5186

# ---------------------------------------------------------------- 쉬운 말 ↔ 코드
ROAD_TYPES = {"시·구 도로 (일반 도심 도로)": "gemeinde",
              "시·도 연결 도로": "landesstrasse",
              "국도": "bundesstrasse",
              "고속도로·자동차전용도로": "autobahn"}
SURFACES = {"수치지도 값 그대로": "",
            "일반 아스팔트": "ref",
            "밀입도 아스팔트(AC 11 이하)": "AC11",
            "저소음 포장(LOA)": "LOA",
            "배수성 포장(PA 8)": "PA8",
            "배수성 포장(PA 11)": "PA11",
            "평탄한 블록 포장": "pflaster_even",
            "일반 보도블록·자갈": "pflaster_other"}
JTYPES = {"신호등 교차로": "signal", "회전교차로": "roundabout", "신호 없는 교차로": "other"}
PRESETS = {
    "빠른 미리보기": dict(step=10.0, og=1, of=1, facade=False,
                     desc="10 m 간격, 건물 벽면 계산 없음. 몇 분 안에 대략적인 모습을 봅니다."),
    "표준": dict(step=5.0, og=1, of=1, facade=True,
               desc="5 m 간격 + 건물 벽면(층별). 반사는 1번까지. 보통 수십 분 이내."),
    "정밀 (RLS-19 기준 충족)": dict(step=5.0, og=1, of=2, facade=True,
                              desc="건물 벽면에서 반사 2번까지(RLS-19 3.6). 가장 오래 걸립니다."),
}
inv = lambda d: {v: k for k, v in d.items()}


# ================================================================ 공통 도구
def S():
    return st.session_state


def init_state():
    d = dict(proj="내_프로젝트", map_folders=[], dem=None, green=None, center=None, size=[480.0, 520.0],
             last_click=None, results=None, crs=CRS_DEFAULT, rho=0.8, ground="general", T=15.0, RH=70.0,
             floor_h=3.0, alias="", road_type="gemeinde", fac_radius=None, heights=[4.0])
    for k, v in d.items():
        S().setdefault(k, v)


def pdir():
    name = re.sub(r"[^\w가-힣\-]+", "_", S().proj).strip("_") or "프로젝트"
    p = PROJECTS / name
    p.mkdir(parents=True, exist_ok=True)
    return p


def extract_zip(src, dest, depth=0):
    """수치지도 압축 해제 (한글 파일명 cp949 보정, 압축 안의 압축까지 2단계)"""
    dest = Path(dest); dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    with zipfile.ZipFile(src) as z:
        for info in z.infolist():
            name = info.filename
            if not info.flag_bits & 0x800:
                try:
                    name = name.encode("cp437").decode("cp949")
                except (UnicodeEncodeError, UnicodeDecodeError):
                    pass
            target = (dest / name).resolve()
            if not str(target).startswith(str(root)):
                continue                                         # 경로 이탈 방지
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as a, open(target, "wb") as b:
                shutil.copyfileobj(a, b)
            if target.suffix.lower() == ".zip" and depth < 2:
                extract_zip(target, target.with_suffix(""), depth + 1)


def save_upload(up, folder):
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    p = folder / up.name
    with open(p, "wb") as f:
        f.write(up.getbuffer())
    if p.suffix.lower() == ".zip":
        extract_zip(p, folder / p.stem)
        return folder / p.stem
    return p


def find_raster(path):
    path = Path(path)
    if path.is_file():
        return str(path)
    for ext in ("*.tif", "*.tiff", "*.img", "*.asc"):
        hit = sorted(path.rglob(ext))
        if hit:
            return str(hit[0])
    return None


def find_vector(path):
    path = Path(path)
    if path.is_file():
        return str(path)
    hit = sorted(path.rglob("*.gpkg")) + sorted(path.rglob("*.shp"))
    return str(hit[0]) if hit else None


def make_config(write=True, **over):
    """화면에서 고른 값 → config.yaml (보고서 재현용으로 프로젝트 폴더에 저장)"""
    s = S(); P = pdir()
    preset = over.get("preset", PRESETS["표준"])
    alias = {}
    for part in re.split(r"[,\n]", s.alias or ""):
        if "=" in part:
            a, b = part.split("=", 1)
            if a.strip() and b.strip():
                alias[a.strip()] = b.strip()
    w, h = s.size
    c = {
        "project": {"name": s.proj, "output_dir": "output"},
        "crs": int(s.crs),
        "input": {
            "map": {"folders": [str(f) for f in s.map_folders]},
            "dem": {"path": s.dem, "use_as": "auxiliary"},
            "traffic": {"path": "traffic.csv", "default_road_type": s.road_type},
            "junctions": {"path": "junctions.csv" if (P / "junctions.csv").exists() else None},
            "ground": {"method": s.ground, "default_G": 0.0, "soft_polygons": s.green},
            "road_alias": alias,
        },
        "model": {"floor_height": float(s.floor_h)},
        "receivers": {
            "center": list(s.center) if s.center else None,
            "grid": {"enabled": True, "size": [float(w), float(h)], "step": float(preset["step"]),
                     "heights": [float(x) for x in s.heights]},
            "facade": {"enabled": bool(preset["facade"]),
                       "radius": float(s.fac_radius or 0.45 * min(w, h))},
        },
        "propagation": {"temperature_C": float(s.T), "humidity_pct": float(s.RH),
                        "reflection": {"rho": float(s.rho), "max_order_grid": int(preset["og"]),
                                       "max_order_facade": int(preset["of"])}},
    }
    path = P / "config.yaml"
    if write:
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# 웹 화면에서 만든 설정 ({time.strftime('%Y-%m-%d %H:%M')})\n# ※ {AI_NOTICE}\n")
            yaml.safe_dump(c, f, allow_unicode=True, sort_keys=False)
    return CFG.load(path)


@st.cache_data(show_spinner=False)
def scan_layers(folders, patterns):
    from noisemap.inputs import find_files
    import pyogrio
    out = {}
    for key, pat in patterns.items():
        fs = find_files(list(folders), pat)
        n = 0
        for f in fs:
            try:
                n += pyogrio.read_info(f)["features"]
            except Exception:
                pass
        out[key] = (len(fs), n)
    return out


@st.cache_data(show_spinner="도로 자료를 읽는 중…")
def road_layer_wgs(folders, crs, encoding, pattern):
    """지도 표시용 도로선(WGS84)과 자료 범위"""
    import geopandas as gpd
    from noisemap.inputs import find_files
    fs = find_files(list(folders), pattern)
    if not fs:
        return None, None
    g = pd.concat([gpd.read_file(f, encoding=encoding) for f in fs], ignore_index=True)
    g = gpd.GeoDataFrame(g, geometry="geometry", crs=g.crs or crs)
    if g.crs.to_epsg() != crs:
        g = g.to_crs(crs)
    bounds = tuple(g.total_bounds)
    keep = [c for c in ("도로명", "차로수") if c in g.columns]
    w = g[keep + ["geometry"]].copy()
    w["geometry"] = w.geometry.simplify(1.0)
    return w.to_crs(4326).to_json(), bounds


def to_wgs(x, y, crs):
    from pyproj import Transformer
    lon, lat = Transformer.from_crs(crs, 4326, always_xy=True).transform(x, y)
    return lat, lon


def from_wgs(lat, lon, crs):
    from pyproj import Transformer
    return Transformer.from_crs(4326, crs, always_xy=True).transform(lon, lat)


def glossary():
    with st.expander("📖 용어 풀이 (처음이라면 펼쳐 보세요)"):
        st.markdown("""
- **dB(A) (데시벨)**: 소리 크기 단위. 3 dB 차이는 에너지 2배, 10 dB 차이는 사람이 느끼기에 약 2배 시끄러움.
- **LrT / LrN**: 낮(06~22시) / 밤(22~06시) 평균 소음 레벨. 독일 도로소음 기준 RLS-19의 '평가 소음 레벨'.
- **격자 소음지도**: 지역을 바둑판처럼 나눠 각 칸의 소음을 계산한 지도(지면 위 일정 높이).
- **건물 벽면(파사드) 소음**: 건물 바깥벽 바로 앞(5 cm), 층마다 창문 높이에서 계산한 소음.
- **반사**: 소리가 건물 벽에 부딪혀 되돌아오는 것. 건물이 빽빽한 도로에서는 소음이 커짐.
- **수치지도**: 국토지리정보원이 만든 디지털 지도(건물·도로·등고선 등). 국토정보플랫폼에서 무료로 받음.
""")


# ================================================================ 1단계
def step1():
    st.header("1단계 · 지도 자료 넣기")
    st.write("소음을 계산할 지역의 **수치지도(건물·도로·등고선)**가 필요합니다. 셋 중 편한 방법을 고르세요.")
    t1, t2, t3 = st.tabs(["📦 압축파일 올리기", "📁 내 컴퓨터 폴더 지정", "🧪 예제 자료로 체험"])
    with t1:
        st.caption("국토정보플랫폼(map.ngii.go.kr) → 공간정보 → 수치지도 → '수치지형도 v2.0' SHP 를 도엽별로 받은 zip 파일을 "
                   "그대로 올리면 됩니다. 여러 도엽을 한꺼번에 올려도 자동으로 합칩니다.")
        ups = st.file_uploader("수치지도 압축파일 (.zip, 여러 개 가능)", type=["zip"], accept_multiple_files=True)
        if ups and st.button("올린 파일 풀기", type="primary"):
            with st.spinner("압축을 푸는 중…"):
                for u in ups:
                    save_upload(u, pdir() / "data" / "map")
            folder = str(pdir() / "data" / "map")
            if folder not in S().map_folders:
                S().map_folders.append(folder)
            st.success("압축 해제 완료"); scan_layers.clear(); road_layer_wgs.clear()
    with t2:
        st.caption("자료가 크면 업로드 대신 이미 풀어 둔 폴더 경로를 적으세요 (예: C:\\소음\\data\\map). 하위 폴더도 찾습니다.")
        p = st.text_input("수치지도 폴더 경로")
        if st.button("이 폴더 사용") and p:
            if Path(p).exists():
                S().map_folders = [str(Path(p).resolve())]
                scan_layers.clear(); road_layer_wgs.clear()
                st.success("폴더 등록 완료")
            else:
                st.error("폴더를 찾을 수 없습니다. 경로를 다시 확인하세요.")
    with t3:
        st.caption("실제 자료가 없을 때 연습용 가상 교차로(실제 지역 아님)를 만들어 끝까지 따라 해 볼 수 있습니다.")
        if st.button("예제 자료 만들기"):
            import make_synthetic_data as M
            with st.spinner("예제 자료 생성 중…"):
                M.main(str(pdir()))
            S().map_folders = [str(pdir() / "data" / "map")]
            S().dem = str(pdir() / "data" / "dem" / "dem_5179.tif")
            S().green = str(pdir() / "data" / "green.gpkg")
            S().center = [M.CX, M.CY]; S().size = [300.0, 300.0]; S().crs = 5186
            scan_layers.clear(); road_layer_wgs.clear()
            st.success("예제 자료 준비 완료. 왼쪽에서 2단계로 넘어가세요.")

    st.subheader("선택 자료 (없어도 됩니다)")
    c1, c2 = st.columns(2)
    with c1:
        d = st.file_uploader("지형 높이 자료 DEM (.tif / .img / .zip)", type=["tif", "tiff", "img", "zip"],
                             help="등고선만으로도 계산됩니다. DEM이 있으면 등고선이 없는 곳을 보완합니다.")
        if d is not None and st.button("DEM 등록"):
            S().dem = find_raster(save_upload(d, pdir() / "data" / "dem"))
            st.success(f"DEM: {S().dem}")
    with c2:
        g = st.file_uploader("공원·녹지 범위 (.gpkg 또는 SHP 묶음 .zip)", type=["gpkg", "zip"],
                             help="잔디·흙 같은 부드러운 땅은 소리를 더 흡수합니다. 없으면 모두 포장된 땅으로 계산합니다.")
        if g is not None and st.button("녹지 등록"):
            S().green = find_vector(save_upload(g, pdir() / "data" / "green"))
            st.success(f"녹지: {S().green}")

    st.subheader("자료 확인")
    if not S().map_folders:
        st.info("아직 수치지도가 없습니다.")
        return
    pats = CFG.DEFAULTS["input"]["map"]["layers"]
    info = scan_layers(tuple(S().map_folders), dict(pats))
    names = {"building": "건물", "road": "도로중심선", "contour": "등고선", "spot": "표고점"}
    rows = []
    for k, (nf, nobj) in info.items():
        ok = nobj > 0
        need = k in ("building", "road")
        rows.append({"자료": names[k], "파일 수": nf, "객체 수": nobj,
                     "상태": "✅ 있음" if ok else ("❌ 없음 (필수)" if need else "⚠️ 없음 (평지로 계산)")})
    rows.append({"자료": "DEM", "파일 수": int(bool(S().dem)), "객체 수": "-", "상태": "✅ 있음" if S().dem else "선택 안 함"})
    rows.append({"자료": "녹지", "파일 수": int(bool(S().green)), "객체 수": "-", "상태": "✅ 있음" if S().green else "선택 안 함"})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    if all(info[k][1] > 0 for k in ("building", "road")):
        st.success("필수 자료가 준비되었습니다 → 2단계로")


# ================================================================ 2단계
def step2():
    st.header("2단계 · 계산할 위치와 범위 정하기")
    if not S().map_folders:
        st.warning("먼저 1단계에서 수치지도를 넣어 주세요."); return
    crs = int(S().crs)
    gj, bounds = road_layer_wgs(tuple(S().map_folders), crs, "cp949", CFG.DEFAULTS["input"]["map"]["layers"]["road"])
    if bounds is None:
        st.error("도로 자료를 찾지 못했습니다."); return
    if S().center is None:
        S().center = [(bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2]
    left, right = st.columns([3, 1])
    with right:
        st.markdown("**계산 범위 (m)**")
        w = st.slider("가로", 100, 1500, int(S().size[0]), 20)
        h = st.slider("세로", 100, 1500, int(S().size[1]), 20)
        S().size = [float(w), float(h)]
        st.markdown("**중심 좌표** (지도를 클릭하면 바뀜)")
        x = st.number_input("X (동쪽)", value=float(S().center[0]), format="%.1f")
        y = st.number_input("Y (북쪽)", value=float(S().center[1]), format="%.1f")
        S().center = [x, y]
        st.caption(f"좌표계 EPSG:{crs} · 자료 범위 {bounds[2]-bounds[0]:.0f} × {bounds[3]-bounds[1]:.0f} m")
        if not (bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3]):
            st.error("중심이 자료 범위 밖입니다.")
    with left:
        if not HAVE_MAP:
            st.info("지도 표시 패키지(streamlit-folium)가 없어 좌표로만 지정합니다.")
            return
        st.caption("교차로 등 소음을 보고 싶은 곳을 **클릭**하세요. 빨간 사각형이 계산 범위입니다. "
                   "(배경지도는 인터넷이 필요합니다)")
        lat, lon = to_wgs(S().center[0], S().center[1], crs)
        m = folium.Map(location=[lat, lon], zoom_start=16, tiles="OpenStreetMap", control_scale=True)
        folium.GeoJson(gj, name="도로", style_function=lambda f: {"color": "#3050d0", "weight": 2},
                       tooltip=folium.GeoJsonTooltip(fields=[c for c in ("도로명", "차로수")
                                                             if c in json.loads(gj)["features"][0]["properties"]])
                       if json.loads(gj)["features"] else None).add_to(m)
        x0, y0, x1, y1 = CFG.grid_bbox({"receivers": {"center": S().center, "grid": {"bbox": None, "size": S().size}}})
        corners = [to_wgs(px, py, crs) for px, py in ((x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0))]
        folium.PolyLine(corners, color="red", weight=3).add_to(m)
        folium.Marker([lat, lon], tooltip="계산 중심").add_to(m)
        out = st_folium(m, height=560, use_container_width=True, returned_objects=["last_clicked"], key="map2")
        clk = (out or {}).get("last_clicked")
        if clk and clk != S().last_click:
            S().last_click = clk
            S().center = list(from_wgs(clk["lat"], clk["lng"], crs))
            st.rerun()


# ================================================================ 3단계
def _prefill(R):
    """기존 traffic.csv 가 있으면 화면 표에 채워 넣음"""
    from noisemap.inputs import read_csv_any
    t = pdir() / "traffic.csv"
    old = read_csv_any(t).set_index("key") if t.exists() else pd.DataFrame()
    rows = []
    for r in R.itertuples():
        o = old.loc[r.key] if r.key in old.index else {}
        num = lambda k: pd.to_numeric(o.get(k, ""), errors="coerce") if len(o) else np.nan
        rows.append({
            "도로": r.key if r.key_type == "name" else f"(이름 없음) {r.key}",
            "_key": r.key, "_type": r.key_type,
            "차로수": f"{r.lanes_min}" if r.lanes_min == r.lanes_max else f"{r.lanes_min}~{r.lanes_max}",
            "연장(m)": round(r.length),
            "도로 종류": inv(ROAD_TYPES).get(o.get("road_type", "") if len(o) else "", inv(ROAD_TYPES)[S().road_type]),
            "하루 통행량(대)": num("DTV"),
            "중형 트럭 %": num("p1_day"),
            "대형 트럭·버스 %": num("p2_day"),
            "제한속도(km/h)": num("v_pkw"),
            "노면": inv(SURFACES).get(o.get("surface", "") if len(o) else "", "수치지도 값 그대로"),
            "자료 출처·메모": o.get("note", "") if len(o) else "",
        })
    return pd.DataFrame(rows)


def save_traffic(df):
    rows = []
    g = lambda v: "" if pd.isna(v) else f"{float(v):g}"
    for _, r in df.iterrows():
        p1, p2, v = g(r["중형 트럭 %"]), g(r["대형 트럭·버스 %"]), g(r["제한속도(km/h)"])
        rows.append(dict(key=r["_key"], key_type=r["_type"], road_type=ROAD_TYPES[r["도로 종류"]],
                         DTV=g(r["하루 통행량(대)"]), M_day="", M_night="",
                         p1_day=p1, p2_day=p2, p1_night=p1, p2_night=p2, v_pkw=v, v_lkw=v,
                         surface=SURFACES[r["노면"]], note=r["자료 출처·메모"] or ""))
    pd.DataFrame(rows).to_csv(pdir() / "traffic.csv", index=False, encoding="utf-8-sig")


def step3():
    st.header("3단계 · 도로별 교통량 넣기")
    if not S().map_folders or S().center is None:
        st.warning("1·2단계를 먼저 마쳐 주세요."); return
    st.write("계산 범위 안 도로 목록입니다. **아는 칸만 채우고 나머지는 비워 두세요.** "
             "비운 칸은 독일 RLS-19 표준값이나 차로수 기준 가정값이 들어가며, 결과 보고서에 '가정값'으로 표시됩니다.")
    with st.expander("💡 교통량은 어디서 구하나요?"):
        st.markdown("""
- **서울시 교통정보(TOPIS)** → 교통량 조사자료: 지점별·시간대별 교통량
- **국가교통DB(ktdb.go.kr)** → 도로 구간별 교통량
- **도로교통량 통계연보**(국토교통부): 국도·지방도 일교통량
- 직접 세기: 15분간 지나가는 차를 세어 ×4 하면 시간당 통행량(대략적인 값)

*하루 통행량*은 양방향을 합친 숫자입니다. *중형 트럭*은 3.5톤 넘는 2축 화물차, *대형 트럭·버스*는 3축 이상 화물차·버스·오토바이입니다(RLS-19 분류).
""")
    try:
        cfg = make_config()
        with st.spinner("도로와 교차로를 분석하는 중…"):
            from noisemap import build
            R, Jc, road = build.road_table(cfg)
    except Exception as e:
        st.error(f"도로 자료를 읽지 못했습니다: {e}"); return
    if not len(R):
        st.warning("범위 안에 도로가 없습니다. 2단계에서 범위를 넓혀 보세요."); return
    base = _prefill(R)
    edited = st.data_editor(
        base, hide_index=True, width="stretch", key="traffic_editor",
        column_order=["도로", "차로수", "연장(m)", "도로 종류", "하루 통행량(대)", "중형 트럭 %",
                      "대형 트럭·버스 %", "제한속도(km/h)", "노면", "자료 출처·메모"],
        disabled=["도로", "차로수", "연장(m)"],
        column_config={
            "도로 종류": st.column_config.SelectboxColumn(options=list(ROAD_TYPES), required=True,
                                                     help="비어 있는 칸의 표준값(RLS-19 표 2)을 정하는 데 씁니다."),
            "하루 통행량(대)": st.column_config.NumberColumn(min_value=0, step=100, format="%d",
                                                       help="양방향 합계, 하루(24시간) 기준"),
            "중형 트럭 %": st.column_config.NumberColumn(min_value=0.0, max_value=100.0, step=0.5),
            "대형 트럭·버스 %": st.column_config.NumberColumn(min_value=0.0, max_value=100.0, step=0.5),
            "제한속도(km/h)": st.column_config.NumberColumn(min_value=10, max_value=130, step=10),
            "노면": st.column_config.SelectboxColumn(options=list(SURFACES), required=True),
        })
    blank = edited[edited["하루 통행량(대)"].isna()]
    if len(blank):
        fb = CFG.DEFAULTS["input"]["traffic"]["fallback_dtv_by_lanes"]
        st.warning(f"하루 통행량이 비어 있는 도로 {len(blank)}개는 차로수 기준 가정값을 씁니다 "
                   f"(1차로 {fb[1]:,}대 … 6차로 {fb[6]:,}대). 결과의 정확도가 떨어질 수 있어요.")
    else:
        st.success("모든 도로의 통행량이 입력되었습니다.")
    S()["_traffic_df"] = edited

    st.subheader("교차로 종류 확인")
    st.caption("신호등 교차로 근처에서는 차가 멈췄다 출발해 소음이 커집니다(최대 +3 dB). 자동으로 추정했으니 실제와 다르면 고쳐 주세요.")
    if len(Jc):
        from shapely.geometry import Point
        names = []
        for j in Jc.itertuples():
            near = road[road.distance(Point(j.x, j.y)) < 15]
            nm = sorted({n for n in near.rname if n})
            if (near.rname == "").any() or not nm:
                nm.append("이름 없는 도로")
            names.append(" × ".join(nm[:3]))
        jt = pd.DataFrame({"교차로": names, "X": Jc.x.round(1), "Y": Jc.y.round(1),
                           "종류": [inv(JTYPES)[t] for t in Jc.type]})
        jp = pdir() / "junctions.csv"
        if jp.exists():
            old = pd.read_csv(jp)
            for i, r in jt.iterrows():
                d = np.hypot(old.x - r.X, old.y - r.Y)
                if len(d) and d.min() < 30:
                    jt.loc[i, "종류"] = inv(JTYPES)[old.type.iloc[int(d.argmin())]]
        je = st.data_editor(jt, hide_index=True, width="stretch", disabled=["교차로", "X", "Y"],
                            column_config={"종류": st.column_config.SelectboxColumn(options=list(JTYPES), required=True)},
                            key="junc_editor")
        S()["_junc_df"] = je
    else:
        st.info("범위 안에서 교차로를 찾지 못했습니다.")
        S()["_junc_df"] = None
    if st.button("💾 교통량·교차로 저장", type="primary"):
        save_all(); st.success(f"저장했습니다: {pdir() / 'traffic.csv'}")


def save_all():
    if S().get("_traffic_df") is not None:
        save_traffic(S()["_traffic_df"])
    je = S().get("_junc_df")
    if je is not None and len(je):
        pd.DataFrame({"x": je.X, "y": je.Y, "type": [JTYPES[t] for t in je["종류"]], "note": je["교차로"]}).to_csv(
            pdir() / "junctions.csv", index=False, encoding="utf-8-sig")


# ================================================================ 4단계
def step4():
    st.header("4단계 · 계산하고 결과 보기")
    if not S().map_folders or S().center is None:
        st.warning("1·2단계를 먼저 마쳐 주세요."); return
    if not (pdir() / "traffic.csv").exists() and S().get("_traffic_df") is None:
        st.warning("3단계에서 교통량을 확인·저장하지 않았습니다. 이대로 계산하면 모든 도로에 가정값이 쓰입니다.")
    c1, c2 = st.columns([2, 1])
    with c1:
        pname = st.radio("계산 수준", list(PRESETS), index=1, horizontal=True)
        st.caption(PRESETS[pname]["desc"])
    with c2:
        S().heights = st.multiselect("격자 계산 높이 (m)", [1.5, 4.0, 7.5, 10.0, 15.0],
                                     default=S().heights or [4.0],
                                     help="4 m는 유럽 소음지도 기준 높이입니다. 여러 개 고르면 높이별 지도가 나옵니다.")
    if not S().heights:
        st.error("높이를 하나 이상 고르세요."); return
    if st.button("▶ 계산 시작", type="primary", width="stretch"):
        run_all(PRESETS[pname])
    show_results()


class _ListHandler(logging.Handler):
    def __init__(self, box):
        super().__init__(); self.lines = []; self.box = box

    def emit(self, rec):
        if rec.levelno >= logging.WARNING or "완료" in rec.getMessage() or "레이어" in rec.getMessage():
            self.lines.append(("⚠️ " if rec.levelno >= logging.WARNING else "• ") + rec.getMessage())
            self.box.markdown("\n\n".join(self.lines[-12:]))


def run_all(preset):
    save_all()
    cfg = make_config(preset=preset)
    out = Path(cfg["project"]["output_dir"])
    if out.exists():
        shutil.rmtree(out)
    log = logging.getLogger("noisemap"); log.setLevel(logging.INFO)
    with st.status("계산 중… (창을 닫지 마세요)", expanded=True) as stt:
        logbox = st.empty()
        h = _ListHandler(logbox); log.addHandler(h)
        bar = st.progress(0.0, text="준비 중")
        t0 = time.time()
        try:
            from noisemap import build, run, plots
            bar.progress(0.02, text="① 지도 자료로 계산 모델 만드는 중")
            build.main(cfg)
            bar.progress(0.10, text="② 소음 계산 준비 중 (처음 한 번은 컴파일에 1분 정도 걸립니다)")

            def prog(label, done, total, eta):
                bar.progress(min(0.10 + 0.82 * done / total, 0.92),
                             text=f"② {label} 계산 {done:,}/{total:,} 지점 · 남은 시간 약 {eta / 60:.1f}분")
            run.main(cfg, progress=prog)
            bar.progress(0.94, text="③ 그림 그리는 중")
            files = plots.main(cfg)
            bar.progress(0.97, text="③ 보고서용 고품질 지도·3D 만드는 중")
            from noisemap import maps_hq, viewer3d
            files += maps_hq.main(cfg)
            if (out / "facade_result.csv").exists():
                files.append(viewer3d.main(cfg))
            bar.progress(1.0, text="완료")
            S().results = {"out": str(out), "files": files, "sec": time.time() - t0}
            stt.update(label=f"계산 완료 ({(time.time() - t0) / 60:.1f}분)", state="complete", expanded=False)
        except Exception as e:
            stt.update(label="계산 중 오류", state="error")
            st.error(f"문제가 생겼습니다: {e}")
            with st.expander("자세한 오류 내용 (도움 요청 시 복사해 주세요)"):
                st.code(traceback.format_exc())
        finally:
            log.removeHandler(h)


def _overlay_map(out, cfg_crs):
    import matplotlib
    from noisemap.plots import classes
    G = pd.read_csv(out / "grid_result.csv")
    d = np.load(out / "model_rasters.npz")
    gx, gy = d["gx"], d["gy"]
    hs = sorted(G.h.unique())
    c1, c2 = st.columns(2)
    h = c1.selectbox("높이", hs, format_func=lambda v: f"{v:g} m")
    key = c2.radio("시간대", ["LrT", "LrN"], format_func=lambda k: "낮 (LrT)" if k == "LrT" else "밤 (LrN)",
                   horizontal=True)
    Gh = G[G.h == h]
    step = gx[1] - gx[0] if len(gx) > 1 else 5.0
    A = np.full((len(gy), len(gx)), np.nan)
    A[np.round((Gh.y - gy[0]) / step).astype(int), np.round((Gh.x - gx[0]) / step).astype(int)] = Gh[key]
    cmap, norm, _ = classes(30, 5)
    rgba = cmap(norm(np.nan_to_num(A, nan=0)))
    rgba[np.isnan(A), 3] = 0
    s_, w_ = to_wgs(gx[0] - step / 2, gy[0] - step / 2, cfg_crs)
    n_, e_ = to_wgs(gx[-1] + step / 2, gy[-1] + step / 2, cfg_crs)
    m = folium.Map(location=[(s_ + n_) / 2, (w_ + e_) / 2], zoom_start=17, tiles="OpenStreetMap", control_scale=True)
    folium.raster_layers.ImageOverlay(rgba, bounds=[[s_, w_], [n_, e_]], opacity=0.65, origin="lower",
                                      mercator_project=True).add_to(m)
    st_folium(m, height=520, use_container_width=True, returned_objects=[], key=f"res_{h}_{key}")
    st.caption("색: 초록(조용함) → 노랑 → 빨강 → 보라(매우 시끄러움). 5 dB 간격, 범례는 아래 그림 참고. "
               "※ 지도 위 겹쳐보기는 미리보기용(좌표 근사)이며, 정확한 지도는 아래 그림과 GeoTIFF를 쓰세요.")


def show_results():
    r = S().results
    if not r:
        return
    out = Path(r["out"])
    if not out.exists():
        return
    st.divider()
    st.subheader("결과")
    info = json.load(open(out / "run_info.json", encoding="utf-8"))
    binfo = json.load(open(out / "build_info.json", encoding="utf-8"))
    rr = info["results_range"]
    cols = st.columns(4)
    gk = [k for k in rr if k.startswith("grid")]
    if gk:
        cols[0].metric("격자 최대 (낮)", f"{rr[gk[0]]['LrT'][1]:.1f} dB")
        cols[1].metric("격자 최대 (밤)", f"{rr[gk[0]]['LrN'][1]:.1f} dB")
    if "facade" in rr:
        cols[2].metric("건물 벽면 최대 (낮)", f"{rr['facade']['LrT'][1]:.1f} dB")
    cols[3].metric("가정값 쓴 도로 조각", f"{binfo['roads_with_fallback_traffic']}개",
                   help="교통량을 입력하지 않아 차로수 기준 값을 쓴 도로 객체 수")
    if binfo["roads_with_fallback_traffic"]:
        st.warning("일부 도로에 가정 교통량이 쓰였습니다. 보고서에는 output/traffic_used.csv 의 traffic_src 열을 근거로 밝히세요.")
    tabs = st.tabs(["🗺️ 지도 위에서 보기", "🖼️ 소음지도 그림", "🏢 건물 벽면", "🧊 3D", "⬇️ 내려받기"])
    with tabs[0]:
        if HAVE_MAP and (out / "grid_result.csv").exists():
            _overlay_map(out, int(S().crs))
    with tabs[1]:
        imgs = sorted(out.glob("HQ_grid_*.png")) + sorted(out.glob("HQ_exceed_*.png")) + \
            sorted(out.glob("grid_*_isophone.png")) + sorted(out.glob("grid_*_grid.png"))
        if imgs:
            sel = st.selectbox("그림", imgs, format_func=lambda p: p.stem.replace("grid_", "").replace("_", " "))
            st.image(str(sel), width="stretch")
    with tabs[2]:
        imgs = sorted(out.glob("HQ_facade_*.png")) + sorted(out.glob("HQ_building_*.png")) + sorted(out.glob("facade_*.png"))
        if imgs:
            sel = st.selectbox("그림 ", imgs, format_func=lambda p: p.stem)
            st.image(str(sel), width="stretch")
            fr = pd.read_csv(out / "facade_result.csv")
            top = fr.sort_values("LrT", ascending=False).head(10)[["bid", "floor", "LrT", "LrN", "LrT_cmp", "LrN_cmp"]]
            st.markdown("**가장 시끄러운 벽면 10곳** (LrT_cmp = 기준 비교용 정수 올림값, RLS-19 3.1)")
            st.dataframe(top.rename(columns={"bid": "건물번호", "floor": "층"}).round(1), hide_index=True)
        else:
            st.info("이번 계산에서는 건물 벽면을 계산하지 않았습니다('표준' 이상 선택).")
    with tabs[3]:
        h3 = out / "noise3d.html"
        if h3.exists():
            import streamlit.components.v1 as components
            st.caption("드래그로 돌리고, 벽면을 클릭하면 층별 값이 나옵니다. 내려받은 zip 안의 noise3d.html 을 브라우저로 열어도 됩니다(인터넷 필요).")
            components.html(h3.read_text(encoding="utf-8"), height=720, scrolling=False)
        else:
            st.info("3D는 '표준' 이상(건물 벽면 계산)에서 만들어집니다.")
    with tabs[4]:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for p in out.iterdir():
                if p.suffix != ".npz":
                    z.write(p, f"output/{p.name}")
            for extra in ("config.yaml", "traffic.csv", "junctions.csv"):
                if (pdir() / extra).exists():
                    z.write(pdir() / extra, extra)
            z.writestr("README_결과.txt", f"{AI_NOTICE}\n계산 프로그램 noisemap {__version__} (RLS-19 + ISO 9613-2)\n"
                                         "config.yaml 로 같은 계산을 다시 할 수 있습니다.\n")
        st.download_button("결과 전체 내려받기 (.zip)", buf.getvalue(), file_name=f"{pdir().name}_소음지도결과.zip",
                           mime="application/zip", type="primary")
        st.caption("포함: 그림(PNG), 결과표(CSV), GeoTIFF(QGIS용), 사용한 교통량·교차로·설정, 계산 정보(JSON)")


# ================================================================ 사이드바 / 화면 구성
def sidebar():
    with st.sidebar:
        st.title("🔊 도로 소음지도")
        st.caption("RLS-19(음원) + ISO 9613-2(전파)")
        S().proj = st.text_input("프로젝트 이름", S().proj, help="결과가 projects/이름 폴더에 저장됩니다.")
        step = st.radio("단계", ["1. 지도 자료", "2. 위치·범위", "3. 교통량", "4. 계산·결과"], key="step")
        with st.expander("⚙️ 고급 설정 (모르면 그대로 두세요)"):
            S().crs = st.number_input("좌표계 EPSG", value=int(S().crs), step=1,
                                      help="수치지도 1:1,000은 5186(중부원점)")
            S().road_type = ROAD_TYPES[st.selectbox("기본 도로 종류", list(ROAD_TYPES),
                                                    index=list(ROAD_TYPES.values()).index(S().road_type))]
            S().rho = st.slider("건물 벽 반사계수 ρ", 0.25, 1.0, float(S().rho), 0.05,
                                help="ISO 9613-2 표 4: 창 있는 건물 벽 0.8, 매끈한 벽 1.0")
            S().ground = st.radio("지면 계산법", ["general", "alternative"],
                                  index=0 if S().ground == "general" else 1,
                                  format_func=lambda v: "일반법(권장)" if v == "general" else "대안법(녹지 위주 지역)")
            S().T = st.number_input("기온 °C", value=float(S().T), step=1.0)
            S().RH = st.number_input("상대습도 %", value=float(S().RH), step=5.0)
            S().floor_h = st.number_input("층 높이 m", value=float(S().floor_h), step=0.1)
            S().alias = st.text_input("같은 도로로 볼 이름", S().alias, placeholder="전농로25길=전농로",
                                      help="한 도로가 구간마다 이름이 다를 때. 쉼표로 여러 개")
        st.divider()
        st.caption(f"noisemap {__version__}\n\n{AI_NOTICE}")
    return step


def main():
    init_state()
    step = sidebar()
    glossary()
    {"1": step1, "2": step2, "3": step3, "4": step4}[step[0]]()
    st.divider()
    st.caption(f"※ {AI_NOTICE} 계산 결과의 검토 책임은 사용자에게 있습니다.")


main()
