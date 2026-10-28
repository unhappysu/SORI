# -*- coding: utf-8 -*-
"""
3D 웹 뷰어 생성: 지형 위 격자 소음지도 + 모든 대상 건물의 층별 벽면 소음 (three.js, 단일 HTML)
  python -c "from sori import config, viewer3d; viewer3d.main(config.load('config.yaml'))"
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import base64
import io
import json
import math
import os

import numpy as np
import pandas as pd
import geopandas as gpd
from rasterio import features
from rasterio.transform import from_origin
from scipy.ndimage import map_coordinates, distance_transform_edt
from shapely.geometry import Point
from shapely.geometry.polygon import orient

from . import AI_NOTICE, __version__
from .config import center, grid_bbox
from .maps_hq import DIN_EDGES, DIN_COLS, EXC_EDGES, EXC_COLS, DEFAULT_LIMITS, traffic_summary

GROUND_BG = (226, 230, 222)
ROAD_RGB = (150, 150, 150)
MINOR_RGB = (190, 190, 190)


def _hex(c):
    c = c.lstrip("#"); return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _classify(V, edges=DIN_EDGES, cols=DIN_COLS):
    idx = np.digitize(V, edges, right=True)
    lut = np.array([_hex(c) for c in cols], np.uint8)
    return lut[np.clip(idx, 0, len(cols) - 1)]


def _png_uri(rgb):
    from PIL import Image
    buf = io.BytesIO(); Image.fromarray(rgb).save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def build_data(cfg, view_r=None, tex_res=1.0, mesh_step=5.0):
    out = cfg["project"]["output_dir"]
    cx, cy = center(cfg)
    fh = float(cfg["model"]["floor_height"])
    R = float(view_r or cfg["receivers"]["facade"]["radius"] + 60.0)
    d = np.load(os.path.join(out, "model_rasters.npz"))
    gnd = d["gnd"]; rx0 = float(d["x0"]); ry0 = float(d["y0"]); rres = float(d["res"])

    def zg(x, y):
        x = np.atleast_1d(np.asarray(x, float)); y = np.atleast_1d(np.asarray(y, float))
        col = (x - rx0) / rres - 0.5; row = (ry0 - y) / rres - 0.5
        return map_coordinates(gnd, [row, col], order=1, mode="nearest")

    # ---- 지면 메시(북→남 행, 서→동 열)
    n = int(round(2 * R / mesh_step)) + 1
    xs = cx - R + np.arange(n) * mesh_step
    ys = cy + R - np.arange(n) * mesh_step
    X, Y = np.meshgrid(xs, ys)
    Zg = zg(X.ravel(), Y.ravel())
    zref = float(np.floor(Zg.min()))
    # ---- 지면 텍스처(1 m): 격자 소음 → DIN 색, 도로·건물 마스크
    G = pd.read_csv(os.path.join(out, "grid_result.csv"))
    h0 = sorted(G.h.unique())[0]
    G = G[np.isclose(G.h, h0)]
    gx, gy = d["gx"], d["gy"]; step = float(gx[1] - gx[0])
    m = int(round(2 * R / tex_res))
    tx = cx - R + (np.arange(m) + 0.5) * tex_res
    ty = cy + R - (np.arange(m) + 0.5) * tex_res
    TX, TY = np.meshgrid(tx, ty)
    tr = from_origin(cx - R, cy + R, tex_res, tex_res)
    bld = gpd.read_file(os.path.join(out, "buildings.gpkg"))
    rd = gpd.read_file(os.path.join(out, "roads.gpkg"))
    mainl = cfg["model"]["main_min_lanes"]
    band = [g.buffer(max(w, 4) / 2, cap_style=2) for g, w in zip(rd[rd.lanes >= mainl].geometry,
                                                              rd[rd.lanes >= mainl]["width"].fillna(6))]
    mband = [g.buffer(max(w, 2.5) / 2, cap_style=2) for g, w in zip(rd[rd.lanes < mainl].geometry,
                                                                rd[rd.lanes < mainl]["width"].fillna(3))]
    road_m = features.rasterize(((g, 1) for g in band), out_shape=(m, m), transform=tr, fill=0, dtype="uint8") if band else np.zeros((m, m), np.uint8)
    minor_m = features.rasterize(((g, 1) for g in mband), out_shape=(m, m), transform=tr, fill=0, dtype="uint8") if mband else np.zeros((m, m), np.uint8)
    textures = {}
    lim = dict(DEFAULT_LIMITS, **(cfg.get("plots", {}).get("limits") or {}))
    gb = grid_bbox(cfg)
    inside = (TX >= gb[0]) & (TX <= gb[2]) & (TY >= gb[1]) & (TY <= gb[3])
    for key in ("LrT", "LrN"):
        A = np.full((len(gy), len(gx)), np.nan)
        A[np.round((G.y - gy[0]) / step).astype(int), np.round((G.x - gx[0]) / step).astype(int)] = G[key].values
        nan = np.isnan(A)
        if nan.all():
            continue
        idx = distance_transform_edt(nan, return_distances=False, return_indices=True)
        Af = A[tuple(idx)]
        V = map_coordinates(Af, [(TY - gy[0]) / step, (TX - gx[0]) / step], order=1, mode="nearest")
        # 등급 지도와 기준 초과 지도(초과량 = V − 기준값, maps_hq.exceed_map 과 같은 등급·색)
        for tk, rgb in ((key, _classify(V)), (key + "_x", _classify(V - float(lim[key]), EXC_EDGES, EXC_COLS))):
            rgb[~inside] = GROUND_BG
            rgb[minor_m == 1] = (0.55 * rgb[minor_m == 1] + 0.45 * np.array(MINOR_RGB)).astype(np.uint8)
            rgb[road_m == 1] = ROAD_RGB
            textures[tk] = _png_uri(rgb)
    # ---- 건물
    view = Point(cx, cy).buffer(R)
    B = bld[bld.intersects(view)]
    FR = pd.read_csv(os.path.join(out, "facade_result.csv"))
    F = pd.read_csv(os.path.join(out, "facades.csv"), index_col="fid")
    has = set(FR.bid.unique())
    blds = []
    for b in B.itertuples():
        p = orient(b.geometry, 1.0)
        ring = lambda r: [[round(x - cx, 2), round(y - cy, 2)] for x, y in list(r.coords)[:-1]]
        nm = b.bname if isinstance(b.bname, str) and b.bname not in ("", "nan", "None") else ""
        blds.append({"id": int(b.bid), "r": ring(p.exterior), "h": [i for i in (list(map(ring, p.interiors)))],
                     "z": [round(b.z0 - zref, 2), round(b.ztop - zref, 2)], "f": int(b.floors), "n": nm,
                     "c": int(b.bid in has)})
    # ---- 벽면 사각형(층 × 구간)
    f = F.loc[FR.fid]
    off = 0.06
    x1 = f.x1.values + FR.a.values * (f.x2.values - f.x1.values) + off * f.nx.values - cx
    y1 = f.y1.values + FR.a.values * (f.y2.values - f.y1.values) + off * f.ny.values - cy
    x2 = f.x1.values + FR.b.values * (f.x2.values - f.x1.values) + off * f.nx.values - cx
    y2 = f.y1.values + FR.b.values * (f.y2.values - f.y1.values) + off * f.ny.values - cy
    za = f.z0.values + (FR.floor.values - 1) * fh - zref
    quads = np.c_[x1, y1, x2, y2, za, za + fh].round(2)
    fac = {"q": quads.ravel().tolist(), "T": np.round(FR.LrT.values * 10).astype(int).tolist(),
           "N": np.round(FR.LrN.values * 10).astype(int).tolist(), "b": FR.bid.astype(int).tolist(),
           "fl": FR.floor.astype(int).tolist()}
    # 기준 초과 벽면·층 수와 건물 수(반올림 전 값으로 셈, maps_hq.exceed_map 과 같은 기준)
    xstat = {k[-1]: [int((FR[k] > float(lim[k])).sum()), int(FR.loc[FR[k] > float(lim[k]), "bid"].nunique())]
             for k in ("LrT", "LrN")}
    # ---- 도로명 라벨
    labels = []
    big = rd[(rd.lanes >= 4) & rd.axis.str.len().gt(0) & ~rd.axis.str.startswith("_")]
    for nm, g in big.groupby("axis"):
        geom = g.geometry.union_all().intersection(Point(cx, cy).buffer(R * 0.8))
        parts = [q for q in getattr(geom, "geoms", [geom]) if q.geom_type == "LineString"]
        if not parts:
            continue
        q = max(parts, key=lambda s: s.length)
        if q.length < 40:
            continue
        pt = q.interpolate(0.55, normalized=True)
        labels.append({"t": nm, "x": round(pt.x - cx, 1), "y": round(pt.y - cy, 1), "z": round(float(zg(pt.x, pt.y)[0]) - zref + 3, 1)})
    T = pd.read_csv(os.path.join(out, "traffic_used.csv"))
    traffic, n_fallback = traffic_summary(T)      # M 에서 DTV 를 역산하지 않음(maps_hq 와 같은 표기)
    return {
        "name": cfg["project"]["name"], "R": R, "n": n, "step": mesh_step,
        "zg": np.round(Zg - zref, 2).tolist(), "tex": textures, "bld": blds, "fac": fac, "labels": labels,
        "edges": DIN_EDGES, "cols": DIN_COLS, "h0": float(h0), "fh": fh,
        "lim": {"T": float(lim["LrT"]), "N": float(lim["LrN"]), "label": lim.get("label", "")},
        "xedges": EXC_EDGES, "xcols": EXC_COLS, "xstat": xstat,
        "meta": {"traffic": traffic, "fallback": n_fallback,
                 "notice": AI_NOTICE, "ver": __version__, "nq": int(len(FR)), "nb": int(len(has))},
    }


HTML = r"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>%%TITLE%%</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;600;700&display=swap" rel="stylesheet">
<style>
:root{--bg:#eef2f5;--panel:rgba(255,255,255,.93);--ink:#1d2329;--muted:#5b6670;--line:#d5dbe0;--accent:#c8361d;--chip:#f3f5f7;--sky:#cfe3f2;
 box-sizing:border-box;padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px);color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#11161b;--panel:rgba(24,30,37,.92);--ink:#e9eef2;--muted:#9aa7b2;--line:#33404b;--accent:#ff7a59;--chip:#1f2830;--sky:#1a2633;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#11161b;--panel:rgba(24,30,37,.92);--ink:#e9eef2;--muted:#9aa7b2;--line:#33404b;--accent:#ff7a59;--chip:#1f2830;--sky:#1a2633;color-scheme:dark}
html,body{height:100%;margin:0}
body{background:var(--bg);color:var(--ink);font-family:"Noto Sans KR","Apple SD Gothic Neo","Malgun Gothic",system-ui,sans-serif;overflow:hidden}
#view{position:fixed;inset:0}
.panel{position:fixed;background:var(--panel);border:1px solid var(--line);border-radius:12px;box-shadow:0 6px 24px rgba(0,0,0,.12);backdrop-filter:blur(6px)}
#top{left:14px;top:calc(14px + env(safe-area-inset-top,0px));padding:12px 14px;max-width:min(430px,calc(100vw - 28px))}
#top h1{font-size:17px;margin:0 0 2px;font-weight:700}
#top p{margin:0;color:var(--muted);font-size:12.5px;line-height:1.45}
.row{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}
button{font:inherit;font-size:13px;border:1px solid var(--line);background:var(--chip);color:var(--ink);padding:6px 10px;border-radius:8px;cursor:pointer}
button.on{background:var(--accent);border-color:var(--accent);color:#fff}
#legend{right:14px;top:calc(14px + env(safe-area-inset-top,0px));padding:10px 12px;font-size:12px}
#legend b{display:block;margin-bottom:6px;font-size:12.5px}
.lg{display:flex;align-items:center;gap:7px;line-height:18px}
.sw{width:22px;height:13px;border-radius:3px;border:1px solid rgba(0,0,0,.25)}
#lgx{margin-top:7px;padding-top:6px;border-top:1px solid var(--line);color:var(--muted);font-size:11.5px;line-height:1.5;max-width:210px;display:none}
#lgx b{color:var(--accent);display:inline;font-size:inherit;margin:0}
.ex{color:var(--accent);font-weight:700}
#info{left:14px;bottom:calc(14px + env(safe-area-inset-bottom,0px));padding:12px 14px;width:min(330px,calc(100vw - 28px));font-size:13px;display:none}
#info h2{font-size:14.5px;margin:0 0 4px}
#info .big{font-size:22px;font-weight:700}
#bars{margin-top:8px;max-height:30vh;overflow:auto;padding-right:4px}
.bar{display:grid;grid-template-columns:34px 1fr 64px;gap:6px;align-items:center;font-size:11.5px;margin:2px 0}
.bar i{display:block;height:10px;border-radius:3px}
#foot{right:14px;bottom:calc(14px + env(safe-area-inset-bottom,0px));padding:8px 10px;font-size:11px;color:var(--muted);max-width:min(380px,calc(100vw - 28px))}
.lab{position:fixed;pointer-events:none;font-size:13px;font-weight:700;color:#1d2329;text-shadow:0 0 3px #fff,0 0 3px #fff,0 0 6px #fff;white-space:nowrap;transform:translate(-50%,-50%)}
#tip{position:fixed;pointer-events:none;background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:5px 8px;font-size:12px;display:none}
@media (max-width:700px){#legend{top:auto;bottom:calc(70px + env(safe-area-inset-bottom,0px));font-size:11px}#foot{display:none}}
</style></head><body>
<div id="view"></div>
<div id="top" class="panel">
 <h1>%%TITLE%%</h1>
 <p>지면: 격자 소음지도(높이 <span id="h0"></span> m) · 건물 벽면: 층별 소음 (외벽 앞 5 cm)<br>드래그 회전 · 오른쪽 드래그 이동 · 휠 확대 · 벽면 클릭 시 층별 값</p>
 <div class="row"><button id="bT" class="on">주간 LrT</button><button id="bN">야간 LrN</button>
 <button id="bX">기준 초과</button><button id="bG" class="on">지면 지도</button><button id="bO" class="on">주변 건물</button><button id="bR">시점 초기화</button></div>
</div>
<div id="legend" class="panel"><b id="lgt">LrT [dB(A)]</b><div id="lgb"></div><div id="lgx"></div></div>
<div id="info" class="panel"><h2 id="iname"></h2><div id="isub" style="color:var(--muted);font-size:12px"></div>
 <div style="margin-top:6px">선택 층 <span id="ifl"></span>: <span class="big" id="ival"></span> dB(A)</div><div id="iex" style="font-size:12px"></div><div id="bars"></div></div>
<div id="foot" class="panel"></div>
<div id="tip"></div>
<script type="application/json" id="data">%%DATA%%</script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js"></script>
<script>
const D=JSON.parse(document.getElementById('data').textContent);
document.getElementById('h0').textContent=D.h0;
const foot=document.getElementById('foot');
foot.innerHTML=`음원 RLS-19 · 전파 ISO 9613-2 · 수치지형도 v2.0<br>교통량: ${D.meta.traffic} 대/일 (가정값), 기타 ${D.meta.fallback}개 도로는 차로수 기준 가정값<br>벽면 ${D.meta.nq.toLocaleString()}지점 · 건물 ${D.meta.nb}동 · SORI v${D.meta.ver}<br>※ ${D.meta.notice}`;
const css=v=>getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const cols=D.cols.map(c=>new THREE.Color(c)),xcols=D.xcols.map(c=>new THREE.Color(c));
function cls(v){let i=0;while(i<D.edges.length&&v>D.edges[i])i++;return i}
function xcls(e){let i=0;while(i<D.xedges.length&&e>D.xedges[i])i++;return i}
let key='T',ex=false;
const lim=()=>D.lim[key];
// 표시 모드에 따른 색: 등급(5 dB) 또는 기준 초과량(기준 이하는 회색)
const hexOf=v=>ex?D.xcols[xcls(v-lim())]:D.cols[cls(v)];
const colOf=v=>ex?xcols[xcls(v-lim())]:cols[cls(v)];
const exTxt=v=>{const e=v-lim();return e>0?`기준 +${e.toFixed(1)} dB`:`기준 이하(${e.toFixed(1)})`};
// legend
function legend(){const L=document.getElementById('lgb'),X=document.getElementById('lgx');L.innerHTML='';
 const nm=key==='T'?'LrT 주간':'LrN 야간';
 if(!ex){document.getElementById('lgt').textContent=nm+' [dB(A)]';const e=D.edges;X.style.display='none';
  for(let i=D.cols.length-1;i>=0;i--){const t=i===0?`≤ ${e[0]}`:i===D.cols.length-1?`> ${e[e.length-1]}`:`${e[i-1]} – ${e[i]}`;
   L.insertAdjacentHTML('beforeend',`<div class="lg"><span class="sw" style="background:${D.cols[i]}"></span>${t}</div>`)}return}
 document.getElementById('lgt').textContent=`${nm} − ${lim()} dB (기준 초과량)`;const e=D.xedges;
 for(let i=D.xcols.length-1;i>=0;i--){const t=i===0?`기준 이하 (≤ ${lim()})`:i===D.xcols.length-1?`초과 > ${e[e.length-1]} dB`:`초과 ${e[i-1]} – ${e[i]} dB`;
  L.insertAdjacentHTML('beforeend',`<div class="lg"><span class="sw" style="background:${D.xcols[i]}"></span>${t}</div>`)}
 const [nf,nb]=D.xstat[key],nt=D.meta.nq;X.style.display='block';
 X.innerHTML=`초과 벽면·층 <b>${nf.toLocaleString()}</b> / ${nt.toLocaleString()} (${(100*nf/nt).toFixed(1)} %)<br>초과 건물 <b>${nb}</b>동 / ${D.meta.nb}동<br>${D.lim.label}`}
legend();
// three
const wrap=document.getElementById('view');
const renderer=new THREE.WebGLRenderer({antialias:true,preserveDrawingBuffer:true});
renderer.setPixelRatio(Math.min(devicePixelRatio,2));wrap.appendChild(renderer.domElement);
const scene=new THREE.Scene();
const camera=new THREE.PerspectiveCamera(40,1,1,5000);
const controls=new THREE.OrbitControls(camera,renderer.domElement);
controls.enableDamping=true;controls.maxPolarAngle=Math.PI*0.495;
function home(){camera.position.set(D.R*0.55,D.R*0.62,D.R*0.95);controls.target.set(0,10,0);controls.update()}
home();
scene.add(new THREE.HemisphereLight(0xffffff,0x8a8a80,0.75));
const sun=new THREE.DirectionalLight(0xffffff,0.55);sun.position.set(-300,500,200);scene.add(sun);
function setBg(){scene.background=new THREE.Color(css('--sky')||'#cfe3f2')}
setBg();matchMedia('(prefers-color-scheme: dark)').addEventListener('change',setBg);
// ground
const n=D.n,W=(n-1)*D.step;
const gg=new THREE.PlaneGeometry(W,W,n-1,n-1);gg.rotateX(-Math.PI/2);
const gp=gg.attributes.position;for(let i=0;i<gp.count;i++)gp.setY(i,D.zg[i]);gg.computeVertexNormals();
const loader=new THREE.TextureLoader();const tex={};
for(const k of ['LrT','LrN','LrT_x','LrN_x']){if(D.tex[k]){const t=loader.load(D.tex[k]);t.anisotropy=8;tex[k]=t}}
const gmat=new THREE.MeshLambertMaterial({map:tex.LrT,color:0xffffff});
const plain=new THREE.MeshLambertMaterial({color:0xdfe3dc});
const ground=new THREE.Mesh(gg,gmat);scene.add(ground);
// buildings (walls + roofs)
function bgeom(list){const P=[];for(const b of list){const z0=b.z[0],z1=b.z[1];
 const rings=[b.r,...b.h];
 for(const r of rings){for(let i=0;i<r.length;i++){const a=r[i],c=r[(i+1)%r.length];
  P.push(a[0],z0,-a[1], c[0],z0,-c[1], c[0],z1,-c[1], a[0],z0,-a[1], c[0],z1,-c[1], a[0],z1,-a[1])}}
 const cont=b.r.map(p=>new THREE.Vector2(p[0],p[1]));const holes=b.h.map(h=>h.map(p=>new THREE.Vector2(p[0],p[1])));
 let tri=[];try{tri=THREE.ShapeUtils.triangulateShape(cont,holes)}catch(e){}
 const all=cont.concat(...holes);for(const t of tri)for(const k of t){const v=all[k];P.push(v.x,z1,-v.y)}}
 const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.Float32BufferAttribute(P,3));g.computeVertexNormals();return g}
const mainB=D.bld.filter(b=>b.c),otherB=D.bld.filter(b=>!b.c);
const bmatA=new THREE.MeshLambertMaterial({color:0xe9e4da,side:THREE.DoubleSide});
const bmatO=new THREE.MeshLambertMaterial({color:0xd9dde3,side:THREE.DoubleSide,transparent:true,opacity:0.92});
scene.add(new THREE.Mesh(bgeom(mainB),bmatA));
const others=new THREE.Mesh(bgeom(otherB),bmatO);scene.add(others);
// facade quads
const q=D.fac.q,nq=q.length/6;const FP=new Float32Array(nq*18),FC=new Float32Array(nq*18);
for(let i=0;i<nq;i++){const x1=q[i*6],y1=q[i*6+1],x2=q[i*6+2],y2=q[i*6+3],z0=q[i*6+4],z1=q[i*6+5];
 const v=[x1,z0,-y1, x2,z0,-y2, x2,z1,-y2, x1,z0,-y1, x2,z1,-y2, x1,z1,-y1];FP.set(v,i*18)}
const fg=new THREE.BufferGeometry();fg.setAttribute('position',new THREE.BufferAttribute(FP,3));
fg.setAttribute('color',new THREE.BufferAttribute(FC,3));
const fmat=new THREE.MeshBasicMaterial({vertexColors:true,side:THREE.DoubleSide,polygonOffset:true,polygonOffsetFactor:-2,polygonOffsetUnits:-2});
const fmesh=new THREE.Mesh(fg,fmat);scene.add(fmesh);
// floor lines for readability
const LP=[];for(let i=0;i<nq;i++){const x1=q[i*6],y1=q[i*6+1],x2=q[i*6+2],y2=q[i*6+3],z0=q[i*6+4];LP.push(x1,z0+0.02,-y1,x2,z0+0.02,-y2)}
const lg=new THREE.BufferGeometry();lg.setAttribute('position',new THREE.Float32BufferAttribute(LP,3));
scene.add(new THREE.LineSegments(lg,new THREE.LineBasicMaterial({color:0x000000,transparent:true,opacity:0.18})));
function paint(){const A=key==='T'?D.fac.T:D.fac.N;for(let i=0;i<nq;i++){const c=colOf(A[i]/10);
 for(let k=0;k<6;k++){FC[i*18+k*3]=c.r;FC[i*18+k*3+1]=c.g;FC[i*18+k*3+2]=c.b}}fg.attributes.color.needsUpdate=true;
 if(groundOn)gmat.map=tex[(key==='T'?'LrT':'LrN')+(ex?'_x':'')];gmat.needsUpdate=true;legend();if(sel>=0)showInfo(sel)}
let groundOn=true;
// labels
const labs=D.labels.map(l=>{const el=document.createElement('div');el.className='lab';el.textContent=l.t;document.body.appendChild(el);return{el,v:new THREE.Vector3(l.x,l.z,-l.y)}});
// picking
const ray=new THREE.Raycaster(),mouse=new THREE.Vector2();let sel=-1;
const byB={};for(let i=0;i<nq;i++){(byB[D.fac.b[i]]=byB[D.fac.b[i]]||[]).push(i)}
const binfo={};for(const b of D.bld)binfo[b.id]=b;
function pick(ev){const r=renderer.domElement.getBoundingClientRect();mouse.x=((ev.clientX-r.left)/r.width)*2-1;mouse.y=-((ev.clientY-r.top)/r.height)*2+1;
 ray.setFromCamera(mouse,camera);const h=ray.intersectObject(fmesh);return h.length?Math.floor(h[0].faceIndex/2):-1}
function showInfo(i){sel=i;const bid=D.fac.b[i],b=binfo[bid]||{},A=key==='T'?D.fac.T:D.fac.N;
 document.getElementById('info').style.display='block';
 document.getElementById('iname').textContent=b.n||`건물 #${bid}`;
 document.getElementById('isub').textContent=`${b.f||'?'}층 · ${key==='T'?'주간 LrT':'야간 LrN'} · 클릭한 벽면 기준`;
 document.getElementById('ifl').textContent=D.fac.fl[i]+'층';document.getElementById('ival').textContent=(A[i]/10).toFixed(1);
 document.getElementById('iex').innerHTML=`<span class="${A[i]/10>lim()?'ex':''}">${exTxt(A[i]/10)}</span> · 기준 ${lim()} dB(A)`;
 const fl={};for(const j of byB[bid]){const f=D.fac.fl[j];fl[f]=Math.max(fl[f]??-99,A[j]/10)}
 const fs=Object.keys(fl).map(Number).sort((a,b)=>b-a);const mx=Math.max(...Object.values(fl)),mn=Math.min(...Object.values(fl),mx-10);
 document.getElementById('bars').innerHTML='<div style="font-size:11.5px;color:var(--muted);margin:4px 0">층별 최고값(건물 전체 벽면)</div>'+fs.map(f=>{const v=fl[f];
  return `<div class="bar"><span>${f}층</span><i style="width:${10+90*(v-mn)/(mx-mn+1e-6)}%;background:${hexOf(v)}"></i><span class="${v>lim()?'ex':''}">${v.toFixed(1)} dB</span></div>`}).join('')}
let down=null;renderer.domElement.addEventListener('pointerdown',e=>down=[e.clientX,e.clientY]);
renderer.domElement.addEventListener('pointerup',e=>{if(down&&Math.hypot(e.clientX-down[0],e.clientY-down[1])<5){const i=pick(e);if(i>=0)showInfo(i);else{document.getElementById('info').style.display='none';sel=-1}}});
const tip=document.getElementById('tip');
renderer.domElement.addEventListener('pointermove',e=>{if(e.buttons){tip.style.display='none';return}const i=pick(e);
 if(i<0){tip.style.display='none';return}const A=key==='T'?D.fac.T:D.fac.N;tip.style.display='block';tip.style.left=(e.clientX+12)+'px';tip.style.top=(e.clientY+12)+'px';
 tip.textContent=`${(binfo[D.fac.b[i]]||{}).n||'건물 #'+D.fac.b[i]} · ${D.fac.fl[i]}층 · ${(A[i]/10).toFixed(1)} dB(A) · ${exTxt(A[i]/10)}`});
// buttons
const $=id=>document.getElementById(id);
$('bT').onclick=()=>{key='T';$('bT').classList.add('on');$('bN').classList.remove('on');paint()};
$('bN').onclick=()=>{key='N';$('bN').classList.add('on');$('bT').classList.remove('on');paint()};
$('bX').onclick=()=>{ex=!ex;$('bX').classList.toggle('on',ex);paint()};
$('bG').onclick=()=>{groundOn=!groundOn;$('bG').classList.toggle('on',groundOn);ground.material=groundOn?gmat:plain;paint()};
$('bO').onclick=()=>{others.visible=!others.visible;$('bO').classList.toggle('on',others.visible)};
$('bR').onclick=home;
paint();
function resize(){const w=wrap.clientWidth,h=wrap.clientHeight;renderer.setSize(w,h);camera.aspect=w/h;camera.updateProjectionMatrix()}
addEventListener('resize',resize);resize();
const v3=new THREE.Vector3();
(function loop(){requestAnimationFrame(loop);controls.update();renderer.render(scene,camera);
 const w=renderer.domElement.clientWidth,h=renderer.domElement.clientHeight;
 for(const l of labs){v3.copy(l.v).project(camera);const vis=v3.z<1&&Math.abs(v3.x)<1.1&&Math.abs(v3.y)<1.1;
  l.el.style.display=vis?'block':'none';l.el.style.left=((v3.x+1)/2*w)+'px';l.el.style.top=((1-v3.y)/2*h)+'px'}})();
</script></body></html>"""


CDN_TAGS = ('<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>\n'
            '<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js"></script>')


def _offline(html):
    """three.js(MIT 라이선스)를 파일 안에 넣어 인터넷 없이 열리게 함"""
    vd = os.path.join(os.path.dirname(__file__), "vendor")
    js = [open(os.path.join(vd, f), encoding="utf-8").read() for f in ("three-r128.min.js", "OrbitControls-r128.js")]
    inline = "".join(f"<script>\n{j.replace('</script', '<\\/script')}\n</script>\n" for j in js)
    html = html.replace(CDN_TAGS, inline)
    html = html.replace('<link rel="preconnect" href="https://fonts.googleapis.com">\n', "")
    return "\n".join(l for l in html.split("\n") if "fonts.googleapis.com/css2" not in l)


def main(cfg, path=None, offline=True):
    """offline=True: 인터넷 없이 열리는 파일(약 +0.6 MB). False: three.js를 CDN에서 불러옴(게시용)"""
    D = build_data(cfg)
    title = f"SORI Map · {cfg['project']['name']} 3D 소음지도"
    html = HTML.replace("%%TITLE%%", title).replace("%%DATA%%", json.dumps(D, ensure_ascii=False, separators=(",", ":")))
    if offline:
        html = _offline(html)
    path = path or os.path.join(cfg["project"]["output_dir"], "noise3d.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path
