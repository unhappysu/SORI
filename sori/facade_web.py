# -*- coding: utf-8 -*-
"""
앱 오른쪽 창 3D 파사드(three.js, 2026-11-15 품질 개선 3차). matplotlib 3D(plots.draw_facade_3d)는 면을 깊이로
정렬하지 못해 앞뒤가 뒤섞이고 돌릴 때 느렸음 → 앱 안 웹 화면(Qt WebEngine)에서 three.js 로 그림.
  - page_path(): 한 번 만들어 두는 정적 페이지(three.js 를 파일 안에 넣어 인터넷 없이). 자료는 앱이 SORI.show(...) 로 넘김
  - scene_data(): 고른 건물 주변(보이는 범위)의 건물·층별 벽면 값(주간·야간 둘 다)·바닥 격자 색·번호 → JSON 용 dict
계산 없이 결과 표만 씀. 보고서 그림(facade_3d)과 지도 저장은 matplotlib 그대로.
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import json
import os
import tempfile

import numpy as np
from scipy.ndimage import distance_transform_edt, map_coordinates
from shapely.geometry.polygon import orient

from . import AI_NOTICE
from .plots import bid_label_points
from .bldz import roof_fn
from .viewer3d import _hex, _png_uri
from .resources import resource_path

PAGE_VERSION = 1


def _ground_png(grid, bounds, edges, cols, res=1.0):
    """격자 결과(A, (xmin, xmax, ymin, ymax))를 bounds(xmin, ymin, xmax, ymax) 범위의 res(m) RGBA 그림으로:
    계산점 사이는 1차 보간 뒤 5 dB 등급 색(등급 경계가 매끄럽게), 값 없는 곳(건물 안·범위 밖)은 투명"""
    A, (x0, x1, y0, y1) = grid
    ny, nx = A.shape
    dx, dy = (x1 - x0) / nx, (y1 - y0) / ny
    nan = np.isnan(A)
    if nan.all():
        return None
    Af = A[tuple(distance_transform_edt(nan, return_distances=False, return_indices=True))]
    w, h = int(np.ceil((bounds[2] - bounds[0]) / res)), int(np.ceil((bounds[3] - bounds[1]) / res))
    tx = bounds[0] + (np.arange(w) + 0.5) * res
    ty = bounds[3] - (np.arange(h) + 0.5) * res                     # 그림 위쪽 = 북쪽
    TX, TY = np.meshgrid(tx, ty)
    ci, ri = (TX - x0) / dx - 0.5, (TY - y0) / dy - 0.5             # 칸 가운데 = 계산점
    V = map_coordinates(Af, [ri, ci], order=1, mode="nearest")
    out = (ci < -0.5) | (ci > nx - 0.5) | (ri < -0.5) | (ri > ny - 0.5)
    near = nan[np.clip(np.round(ri).astype(int), 0, ny - 1), np.clip(np.round(ci).astype(int), 0, nx - 1)]
    lut = np.array([_hex(c) + (255,) for c in cols], np.uint8)
    rgba = lut[np.clip(np.digitize(V, edges, right=True), 0, len(cols) - 1)]
    rgba[out | near] = 0
    return _png_uri(rgba)


def scene_data(B, FR, F, T, bid, view, fh, grids=None, h=None, edges=(), cols=(), n_labels=30,
               azim=-60.0, elev=28.0, title=""):
    """오른쪽 창 3D 파사드 자료. B: 건물(bid, z0, ztop, floors, geometry), FR·F: 벽면 결과·벽 표,
    T: 층별 색을 칠할 건물 번호(보이는 범위 안 벽면 결과가 있는 건물), bid: 확인 대상, view: 보이는 범위(도형),
    grids: {"LrT": (A, 범위), "LrN": …} 바닥 격자(높이 h), edges·cols: 색 등급. azim·elev: 처음 시점(도, matplotlib 방위).
    좌표는 대상 건물 대표점 기준 m(x 동, y 북), 높이는 보이는 건물 바닥 최저 기준"""
    Bv = B[B.intersects(view)]
    fb = Bv[Bv.bid == bid].iloc[0]
    o = fb.geometry.representative_point()
    ox, oy = o.x, o.y
    zref = float(Bv.z0.min())
    r2 = lambda c: [round(c[0] - ox, 2), round(c[1] - oy, 2)]          # noqa: E731
    blds = []
    for b in Bv.itertuples():
        for g in getattr(b.geometry, "geoms", [b.geometry]):
            p = orient(g, 1.0)
            rf = roof_fn(b)
            tops = [[round(rf(x, y) - zref, 2) for x, y in list(r.coords)[:-1]] for r in [p.exterior] + list(p.interiors)]
            blds.append({"id": int(b.bid), "r": [r2(c) for c in list(p.exterior.coords)[:-1]],
                         "t": tops if getattr(b, "ztops", None) else None,
                         "h": [[r2(c) for c in list(i.coords)[:-1]] for i in p.interiors],
                         # 벽 아래 끝 = 바닥판 밑(−0.2 m). 경사지에서 z0 가 높은 건물이 평평한 바닥 위에 떠 보이지 않게
                         # 받침 벽으로 이어 그림(그리기만, 계산값·층 위치는 z0 그대로, 2026-11-20)
                         "z": [-0.2, round(float(b.ztop) - zref, 2)], "z0": round(float(b.z0) - zref, 2),
                         "f": int(b.floors)})
    # 벽면 사각형(층 × 구간) — 외벽 앞 6 cm, 해 방향 명암 0.80–1.0(3D 뷰어와 같은 규칙)
    S = FR[FR.bid.isin(T)]
    f = F.loc[S.fid]
    off = 0.06
    ax_, ay_ = f.x1.values + S.a.values * (f.x2.values - f.x1.values), f.y1.values + S.a.values * (f.y2.values - f.y1.values)
    bx_, by_ = f.x1.values + S.b.values * (f.x2.values - f.x1.values), f.y1.values + S.b.values * (f.y2.values - f.y1.values)
    z0b = Bv.set_index("bid").z0.reindex(S.bid.values).values
    if "zg" in S:                                    # 층 바닥 = 수음점 지면(지형 따라가기면 벽 위치 지형)
        z0b = S.zg.values
    za = np.where(np.isfinite(z0b), z0b, f.z0.values) + (S.floor.values - 1) * fh - zref
    q = np.c_[ax_ + off * f.nx.values - ox, ay_ + off * f.ny.values - oy, bx_ + off * f.nx.values - ox,
              by_ + off * f.ny.values - oy, za, za + fh].round(2)
    L = np.array([-300.0, -200.0]) / np.hypot(300.0, 200.0)
    shade = 0.80 + 0.20 * (0.5 + 0.5 * (f.nx.values * L[0] + f.ny.values * L[1]))
    fac = {"q": q.ravel().tolist(), "s": np.round(shade * 100).astype(int).tolist(),
           "T": np.round(S.LrT.values * 10).astype(int).tolist(), "N": np.round(S.LrN.values * 10).astype(int).tolist(),
           "b": S.bid.astype(int).tolist(), "fl": S.floor.astype(int).tolist()}
    # 대상 건물 벽마다 값 글자 한 열(가운데 구간, 층별) — 보이는 벽만 고르는 것은 화면에서
    walls = []
    Sb = FR[FR.bid == bid]
    for fid, g in Sb.groupby("fid"):
        w = F.loc[fid]
        pcs = sorted(g.piece.unique())
        r = g[g.piece == pcs[len(pcs) // 2]].sort_values("floor")
        t = float((r.a.iloc[0] + r.b.iloc[0]) / 2)
        x, y = w.x1 + t * (w.x2 - w.x1) + 0.8 * w.nx, w.y1 + t * (w.y2 - w.y1) + 0.8 * w.ny
        walls.append({"x": round(x - ox, 2), "y": round(y - oy, 2), "nx": round(float(w.nx), 3), "ny": round(float(w.ny), 3),
                      "L": round(float(np.hypot(w.x2 - w.x1, w.y2 - w.y1)), 1),
                      "z": [round(float(zb) + (fl - 0.5) * fh - zref, 2)
                            for fl, zb in zip(r.floor, r.zg if "zg" in r else [fb.z0] * len(r))],
                      "T": [round(float(v), 1) for v in r.LrT], "N": [round(float(v), 1) for v in r.LrN]})
    nums = [{"id": b, "x": round(x - ox, 2), "y": round(y - oy, 2), "z": round(zt - zref, 2), "me": bool(me)}
            for b, x, y, zt, me in bid_label_points(Bv, bid, n_labels)]
    gb = view.bounds
    bounds = [round(gb[0] - ox, 2), round(gb[1] - oy, 2), round(gb[2] - ox, 2), round(gb[3] - oy, 2)]
    ground = {}
    for k, gr in (grids or {}).items():
        if gr is not None:
            u = _ground_png(gr, gb, list(edges), list(cols))
            if u:
                ground[k[-1]] = u                                          # "T"·"N"
    bx0, by0, bx1, by1 = fb.geometry.bounds
    ext = float(max(bx1 - bx0, by1 - by0))
    return {"bid": int(bid), "title": title, "bld": blds, "fac": fac, "walls": walls, "nums": nums,
            "bounds": bounds, "ground": ground, "h": h, "edges": list(edges), "cols": list(cols),
            "zc": round((float(fb.z0) + float(fb.ztop)) / 2 - zref, 2), "ztop": round(float(fb.ztop) - zref, 2),
            "size": round(ext, 1), "azim": float(azim), "elev": float(elev), "notice": AI_NOTICE}


def page_path():
    """정적 페이지 파일(임시 폴더, 판마다 한 번 만듦). three.js(MIT)는 파일 안에 넣음"""
    p = os.path.join(tempfile.gettempdir(), f"sori_facade3d_v{PAGE_VERSION}.html")
    vd = resource_path("sori", "vendor")
    js = "".join("<script>\n" + (vd / n).read_text(encoding="utf-8").replace("</script", "<\\/script") + "\n</script>\n"
                 for n in ("three-r128.min.js", "OrbitControls-r128.js"))
    html = PAGE.replace("%%THREE%%", js)
    try:
        with open(p, encoding="utf-8") as fh:
            if fh.read() == html:
                return p
    except OSError:
        pass
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(html)
    return p


def show_js(data, key):
    """페이지에 자료를 넘기는 JavaScript 한 줄"""
    return f"SORI.show({json.dumps(data, ensure_ascii=False, separators=(',', ':'))},'{key[-1]}')"


PAGE = r"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SORI 3D 파사드</title>
<style>
:root{--ink:#222222;--muted:#555555;--line:#d0d0d0;--red:#d00000;--chip:#f4f4f4;color-scheme:light}
html,body{height:100%;margin:0;background:#ffffff;color:var(--ink);overflow:hidden;
 font-family:"Apple SD Gothic Neo","Noto Sans KR","Malgun Gothic",system-ui,sans-serif}
#view{position:fixed;left:0;right:0;top:64px;bottom:22px}
#head{position:fixed;left:0;right:0;top:0;height:64px;background:#ffffff;padding:6px 10px 0;box-sizing:border-box}
#title{font-size:14px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding-right:330px}
#btns{position:absolute;right:8px;top:4px;display:flex;gap:4px}
button{font:inherit;font-size:12px;border:1px solid var(--line);background:var(--chip);color:var(--ink);padding:3px 8px;border-radius:6px;cursor:pointer}
button.on{background:#fde8e8;border-color:#f2b8b8;color:var(--red);font-weight:700}
#legend{display:flex;align-items:flex-start;margin-top:6px;font-size:11px}
#legend b{width:44px;font-size:11px;padding-top:2px}
#lgbar{flex:1;display:flex;position:relative;height:30px}
#lgbar span{flex:1;height:15px;border-right:1px solid #fff}
#lgbar em{position:absolute;top:16px;transform:translateX(-50%);font-style:normal;font-size:10.5px}
#foot{position:fixed;left:0;right:0;bottom:0;height:22px;font-size:11px;color:var(--muted);padding:3px 8px;box-sizing:border-box;
 display:flex;justify-content:space-between;gap:12px;background:#ffffff;white-space:nowrap}
#foot span{overflow:hidden;text-overflow:ellipsis}
#foot i{font-style:normal;font-size:10px;color:#777;flex:none}
.lab{position:fixed;pointer-events:none;transform:translate(-50%,-50%);white-space:nowrap;font-size:11px;font-weight:700;
 background:rgba(255,255,255,.72);padding:0 2px;border-radius:2px;display:none}
.num{position:fixed;pointer-events:none;transform:translate(-50%,-100%);white-space:nowrap;font-size:10.5px;color:#333;
 background:rgba(255,255,255,.88);border:1px solid #888;border-radius:3px;padding:0 3px;display:none}
.num.me{font-size:13px;font-weight:700;color:#fff;background:var(--red);border:1.5px solid #fff;padding:1px 5px}
#tip{position:fixed;pointer-events:none;background:#fff;border:1px solid var(--line);border-radius:6px;padding:3px 7px;font-size:12px;
 display:none;box-shadow:0 2px 8px rgba(0,0,0,.15)}
#msg{position:fixed;inset:0;display:flex;align-items:center;justify-content:center;color:#666;font-size:14px;text-align:center}
</style></head><body>
<div id="head"><div id="title"></div>
 <div id="btns"><button id="bV">소음도 숫자</button><button id="bG">주변 투명</button><button id="bF">바닥 소음지도</button><button id="bR">시점 처음</button></div>
 <div id="legend"><b>dB(A)</b><div id="lgbar"></div></div></div>
<div id="view"></div><div id="labs"></div><div id="tip"></div>
<div id="foot"><span id="note"></span><i id="notice"></i></div>
<div id="msg">3D 파사드 준비 중…</div>
%%THREE%%
<script>
const $=id=>document.getElementById(id);
const S={vals:true,ghost:false,ground:true};
try{Object.assign(S,JSON.parse(localStorage.getItem('sori_fac3d')||'{}'))}catch(e){}
function saveS(){try{localStorage.setItem('sori_fac3d',JSON.stringify(S))}catch(e){}}
const wrap=$('view');
const renderer=new THREE.WebGLRenderer({antialias:true,preserveDrawingBuffer:true});
renderer.setPixelRatio(Math.min(devicePixelRatio,2));wrap.appendChild(renderer.domElement);
const scene=new THREE.Scene();scene.background=new THREE.Color(0xf3f6f8);
const camera=new THREE.PerspectiveCamera(35,1,0.5,5000);
const controls=new THREE.OrbitControls(camera,renderer.domElement);
controls.enableDamping=true;controls.dampingFactor=0.12;controls.maxPolarAngle=Math.PI*0.49;
scene.add(new THREE.HemisphereLight(0xffffff,0x8a8a84,0.62));
const sun=new THREE.DirectionalLight(0xffffff,0.42);sun.position.set(-300,500,200);scene.add(sun);
let D=null,key='T',G=null,lastBid=null,dirty=true;
let FA=null,FC=null,fmeshT=null,fmeshO=null,others=null,edgesO=null,gmesh=null,gtex={},triB=[];
const cols=()=>D.cols.map(c=>new THREE.Color(c));
function cls(v){let i=0;while(i<D.edges.length&&v>D.edges[i])i++;return i}
function legend(){const L=$('lgbar');L.innerHTML='';const n=D.cols.length;
 D.cols.forEach(c=>L.insertAdjacentHTML('beforeend',`<span style="background:${c}"></span>`));
 D.edges.forEach((e,i)=>L.insertAdjacentHTML('beforeend',`<em style="left:${100*(i+1)/n}%">${e}</em>`))}
function dispose(o){o.traverse(x=>{if(x.geometry)x.geometry.dispose();if(x.material){(Array.isArray(x.material)?x.material:[x.material]).forEach(m=>{if(m.map)m.map.dispose();m.dispose()})}})}
function bgeom(list,tri){const P=[];for(const b of list){const z0=b.z[0],z1=b.z[1],n0=P.length;
 const rings=[b.r,...b.h],tops=b.t||rings.map(r=>r.map(()=>z1));   // b.t = 꼭짓점별 지붕(지형 따라가기)
 for(let ri=0;ri<rings.length;ri++){const r=rings[ri],T=tops[ri];for(let i=0;i<r.length;i++){const j=(i+1)%r.length,a=r[i],c=r[j];
  P.push(a[0],z0,-a[1], c[0],z0,-c[1], c[0],T[j],-c[1], a[0],z0,-a[1], c[0],T[j],-c[1], a[0],T[i],-a[1])}}
 const cont=b.r.map(p=>new THREE.Vector2(p[0],p[1])),holes=b.h.map(h=>h.map(p=>new THREE.Vector2(p[0],p[1])));
 let t=[];try{t=THREE.ShapeUtils.triangulateShape(cont,holes)}catch(e){}
 const all=cont.concat(...holes),allT=[].concat(...tops);for(const f of t)for(const k of f){const v=all[k];P.push(v.x,allT[k],-v.y)}
 if(tri)tri.push([n0/9,P.length/9,b.id])}
 const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.Float32BufferAttribute(P,3));g.computeVertexNormals();return g}
function facMesh(idx){const q=D.fac.q,n=idx.length,P=new Float32Array(n*18),C=new Float32Array(n*18);
 idx.forEach((i,j)=>{const x1=q[i*6],y1=q[i*6+1],x2=q[i*6+2],y2=q[i*6+3],z0=q[i*6+4],z1=q[i*6+5];
  P.set([x1,z0,-y1, x2,z0,-y2, x2,z1,-y2, x1,z0,-y1, x2,z1,-y2, x1,z1,-y1],j*18)});
 const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.BufferAttribute(P,3));g.setAttribute('color',new THREE.BufferAttribute(C,3));
 const m=new THREE.Mesh(g,new THREE.MeshBasicMaterial({vertexColors:true,side:THREE.DoubleSide,polygonOffset:true,polygonOffsetFactor:-2,polygonOffsetUnits:-2}));
 m.userData.idx=idx;
 const LP=[];idx.forEach(i=>{LP.push(q[i*6],q[i*6+4]+0.02,-q[i*6+1],q[i*6+2],q[i*6+4]+0.02,-q[i*6+3])});
 const lg=new THREE.BufferGeometry();lg.setAttribute('position',new THREE.Float32BufferAttribute(LP,3));
 m.add(new THREE.LineSegments(lg,new THREE.LineBasicMaterial({color:0x000000,transparent:true,opacity:0.16})));return m}
function paint(){const A=key==='T'?D.fac.T:D.fac.N,c=cols();
 for(const m of [fmeshT,fmeshO]){if(!m)continue;const C=m.geometry.attributes.color.array;
  m.userData.idx.forEach((i,j)=>{const k=D.fac.s[i]/100,cc=c[cls(A[i]/10)];for(let v=0;v<6;v++){C[j*18+v*3]=cc.r*k;C[j*18+v*3+1]=cc.g*k;C[j*18+v*3+2]=cc.b*k}});
  m.geometry.attributes.color.needsUpdate=true}
 if(gmesh){const t=gtex[key];gmesh.visible=S.ground&&!!t;if(t){gmesh.material.map=t;gmesh.material.needsUpdate=true}}
 $('title').textContent=D.title+' · '+(key==='T'?'주간 LrT (06–22시)':'야간 LrN (22–06시)');note();dirty=true}
function note(){$('note').textContent='빨간 번호 = 확인 대상 건물, 흰 번호 = 주변 건물'+(S.ghost?' · 주변 투명':'')+
 (S.ground&&gtex[key]&&D.h!=null?` · 바닥 = 높이 ${D.h} m 격자`:'')+' · 끌기 돌리기 · 오른쪽 끌기 옮기기 · 휠 확대 · 다른 건물 클릭 = 그 건물로'}
function applyOpts(){$('bV').classList.toggle('on',S.vals);$('bG').classList.toggle('on',S.ghost);$('bF').classList.toggle('on',S.ground);
 if(!D)return;others.material.opacity=S.ghost?0.07:1;others.material.transparent=S.ghost;others.material.depthWrite=!S.ghost;
 others.material.needsUpdate=true;edgesO.material.opacity=S.ghost?0.25:0.35;if(fmeshO)fmeshO.visible=!S.ghost;paint()}
function home(){const az=D.azim*Math.PI/180,el=D.elev*Math.PI/180;
 const d=Math.max(55,Math.min(3.0*Math.max(D.size,D.ztop),0.9*(D.bounds[2]-D.bounds[0])));
 controls.target.set(0,D.zc,0);camera.position.set(d*Math.cos(el)*Math.cos(az),D.zc+d*Math.sin(el),-d*Math.cos(el)*Math.sin(az));controls.update();dirty=true}
const labs=$('labs');let walls=[],nums=[];
function build(){if(G){scene.remove(G);dispose(G)}Object.values(gtex).forEach(t=>t.dispose());gtex={};labs.innerHTML='';
 G=new THREE.Group();scene.add(G);const b=D.bounds,W=b[2]-b[0],H=b[3]-b[1],cx=(b[0]+b[2])/2,cy=(b[1]+b[3])/2;
 const pg=new THREE.PlaneGeometry(W*1.6,H*1.6);pg.rotateX(-Math.PI/2);
 const base=new THREE.Mesh(pg,new THREE.MeshLambertMaterial({color:0xe4e6e1}));base.position.set(cx,-0.15,-cy);G.add(base);
 const ld=new THREE.TextureLoader();for(const k in D.ground){const t=ld.load(D.ground[k],()=>{dirty=true});t.magFilter=THREE.LinearFilter;gtex[k]=t}
 const gg=new THREE.PlaneGeometry(W,H);gg.rotateX(-Math.PI/2);
 gmesh=new THREE.Mesh(gg,new THREE.MeshBasicMaterial({transparent:true,depthWrite:false}));gmesh.position.set(cx,-0.05,-cy);G.add(gmesh);
 const tgt=D.bld.filter(x=>x.id===D.bid),oth=D.bld.filter(x=>x.id!==D.bid);triB=[];
 const tm=new THREE.Mesh(bgeom(tgt),new THREE.MeshLambertMaterial({color:0xe2dccf,side:THREE.DoubleSide}));G.add(tm);
 others=new THREE.Mesh(bgeom(oth,triB),new THREE.MeshLambertMaterial({color:0xc4c7cc,side:THREE.DoubleSide}));G.add(others);
 G.add(new THREE.LineSegments(new THREE.EdgesGeometry(tm.geometry,25),new THREE.LineBasicMaterial({color:0xd00000})));
 edgesO=new THREE.LineSegments(new THREE.EdgesGeometry(others.geometry,25),new THREE.LineBasicMaterial({color:0x2b2b2b,transparent:true,opacity:0.35}));G.add(edgesO);
 const iT=[],iO=[];D.fac.b.forEach((x,i)=>(x===D.bid?iT:iO).push(i));
 fmeshT=iT.length?facMesh(iT):null;fmeshO=iO.length?facMesh(iO):null;if(fmeshT)G.add(fmeshT);if(fmeshO)G.add(fmeshO);
 walls=D.walls.map(w=>({w,p:new THREE.Vector3(w.x,(w.z[0]+w.z[w.z.length-1])/2,-w.y),
  els:w.z.map((z,k)=>{const e=document.createElement('div');e.className='lab';labs.appendChild(e);return{e,v:new THREE.Vector3(w.x,z,-w.y),k}})}));
 nums=D.nums.map(n=>{const e=document.createElement('div');e.className='num'+(n.me?' me':'');e.textContent=n.id;labs.appendChild(e);
  return{e,n,v:new THREE.Vector3(n.x,n.z+0.8,-n.y)}});
 const tb=new THREE.Box3().setFromObject(tm);D._tb=[];for(const x of [tb.min.x,tb.max.x])for(const y of [tb.min.y,tb.max.y])for(const z of [tb.min.z,tb.max.z])D._tb.push(new THREE.Vector3(x,y,z));
 applyOpts()}
const v3=new THREE.Vector3(),cdir=new THREE.Vector3();
function scr(v,w,h){v3.copy(v).project(camera);return[(v3.x+1)/2*w,(1-v3.y)/2*h,v3.z<1]}
function place(){if(!D)return;const w=wrap.clientWidth,h=wrap.clientHeight,top=wrap.offsetTop,A=key==='T'?'T':'N',c=D.cols;
 const xs=[],boxes=[];
 // 값 글자: 카메라를 향한 벽만, 화면 가로로 40 px 넘게 떨어진 열만(앞쪽부터)
 const cand=walls.map(o=>{cdir.copy(camera.position).sub(o.p);cdir.y=0;cdir.normalize();
  return{o,s:(o.w.nx*cdir.x-o.w.ny*cdir.z)*Math.min(o.w.L,25)}}).sort((a,b)=>b.s-a.s);
 for(const {o,s} of cand){let ok=S.vals&&s>2.4;let px=null;
  if(ok){px=scr(o.p,w,h);ok=px[2]&&px[0]>-20&&px[0]<w+20&&xs.every(x=>Math.abs(px[0]-x)>40)}
  if(ok)xs.push(px[0]);
  for(const l of o.els){if(!ok){l.e.style.display='none';continue}const p=scr(l.v,w,h),val=o.w[A][l.k];
   l.e.style.display='block';l.e.style.left=p[0]+'px';l.e.style.top=(p[1]+top)+'px';l.e.textContent=val.toFixed(1);
   boxes.push([p[0],p[1],34,14])}}
 // 건물 번호: 대상은 늘, 주변은 대상 건물 화면 사각형 밖이고 다른 글자와 겹치지 않을 때만
 let bx0=1e9,by0=1e9,bx1=-1e9,by1=-1e9;for(const v of D._tb){const p=scr(v,w,h);bx0=Math.min(bx0,p[0]);bx1=Math.max(bx1,p[0]);by0=Math.min(by0,p[1]);by1=Math.max(by1,p[1])}
 for(const o of nums){const p=scr(o.v,w,h);let ok=p[2]&&p[0]>0&&p[0]<w&&p[1]>0&&p[1]<h;
  if(ok&&!o.n.me)ok=!(p[0]>bx0-4&&p[0]<bx1+4&&p[1]>by0-4&&p[1]<by1+4)&&boxes.every(b=>Math.abs(p[0]-b[0])>(b[2]+30)/2||Math.abs(p[1]-8-b[1])>(b[3]+14)/2);
  o.e.style.display=ok?'block':'none';if(ok){o.e.style.left=p[0]+'px';o.e.style.top=(p[1]+top)+'px';boxes.push([p[0],p[1]-8,30,14])}}}
// 고르기: 벽면 → 값 풍선, 클릭한 건물이 다른 건물이면 앱에 알림(console 'SORI:pick:번호')
const ray=new THREE.Raycaster(),mouse=new THREE.Vector2();
function hit(ev){const r=renderer.domElement.getBoundingClientRect();mouse.x=((ev.clientX-r.left)/r.width)*2-1;mouse.y=-((ev.clientY-r.top)/r.height)*2+1;
 ray.setFromCamera(mouse,camera);const objs=[fmeshT,S.ghost?null:fmeshO,others].filter(x=>x&&x.visible);
 const hs=ray.intersectObjects(objs,false);if(!hs.length)return null;const h=hs[0];
 if(h.object===others){const f=h.faceIndex;const t=triB.find(r=>f>=r[0]&&f<r[1]);return t?{bid:t[2]}:null}
 const i=h.object.userData.idx[Math.floor(h.faceIndex/2)];return{bid:D.fac.b[i],i}}
const tip=$('tip');
renderer.domElement.addEventListener('pointermove',e=>{if(e.buttons||!D){tip.style.display='none';return}const r=hit(e);
 if(!r){tip.style.display='none';return}tip.style.display='block';tip.style.left=(e.clientX+12)+'px';tip.style.top=(e.clientY+12)+'px';
 tip.textContent=r.i!=null?`건물 ${r.bid} · ${D.fac.fl[r.i]}층 · ${((key==='T'?D.fac.T:D.fac.N)[r.i]/10).toFixed(1)} dB(A)`:`건물 ${r.bid} — 클릭하면 이 건물로`});
let down=null;renderer.domElement.addEventListener('pointerdown',e=>down=[e.clientX,e.clientY]);
renderer.domElement.addEventListener('pointerup',e=>{if(!down||Math.hypot(e.clientX-down[0],e.clientY-down[1])>4||!D)return;
 const r=hit(e);if(r&&r.bid!==D.bid)console.log('SORI:pick:'+r.bid)});
$('bV').onclick=()=>{S.vals=!S.vals;saveS();applyOpts()};
$('bG').onclick=()=>{S.ghost=!S.ghost;saveS();applyOpts()};
$('bF').onclick=()=>{S.ground=!S.ground;saveS();applyOpts()};
$('bR').onclick=()=>D&&home();
controls.addEventListener('change',()=>{dirty=true});
function resize(){const w=wrap.clientWidth,h=wrap.clientHeight;renderer.setSize(w,h);camera.aspect=w/Math.max(h,1);camera.updateProjectionMatrix();dirty=true}
addEventListener('resize',resize);resize();
window.SORI={show(d,k){D=d;key=k||'T';$('msg').style.display='none';$('notice').textContent='※ '+D.notice;
  legend();build();if(lastBid!==D.bid)home();lastBid=D.bid;return true},
 key(k){if(!D)return false;key=k;paint();return true},
 state(){return D?{bid:D.bid,key,opts:S,nfac:D.fac.b.length,shown:[...labs.children].filter(e=>e.style.display==='block').map(e=>e.textContent)}:null}};
applyOpts();
(function loop(){requestAnimationFrame(loop);controls.update();if(controls.target.y<0.5){const d=0.5-controls.target.y;controls.target.y+=d;camera.position.y+=d;dirty=true}if(camera.position.y<1.5){camera.position.y=1.5;dirty=true}if(dirty){renderer.render(scene,camera);place();dirty=false}})();
</script></body></html>"""
