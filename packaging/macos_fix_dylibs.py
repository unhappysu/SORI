# -*- coding: utf-8 -*-
"""
macOS 앱 빌드 후처리: 패키지마다 들어 있는 같은 이름의 동적 라이브러리 충돌 해결.

문제(2026-10-29 확인): pyproj 와 rasterio 휠이 각자 libproj.25.9.8.1.dylib 를 넣어 두는데,
rasterio 판은 심볼 이름이 internal_proj_* 로 바뀌어 있음. PyInstaller 는 둘 다 @rpath/libproj… 로
바꾸고 최상위에 rasterio 판 하나만 연결해 두므로, 앱에서 pyproj 를 불러오면
"Symbol not found: _proj_context_create" 로 실패함(libtiff·libzstd·liblzma·libjpeg 도 이름이 겹침).
Windows(delvewheel)·Linux(auditwheel) 휠은 라이브러리 이름에 해시를 붙여 이 문제가 없음.

해결: Contents/Frameworks/<패키지>/__dot__dylibs 가 있는 패키지마다, 그 패키지 안의 Mach-O 파일이
@rpath/<이름> 으로 가리키는 라이브러리가 자기 __dot__dylibs 에 있으면 @loader_path 상대경로로 바꾸고,
그 라이브러리의 install name(id)도 패키지별로 다르게 바꿈 → 휠 원래 구조처럼 자기 사본을 씀.
수정한 파일과 앱 전체를 임시 서명(ad-hoc)으로 다시 서명함.

  python packaging/macos_fix_dylibs.py dist/SORI.app
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import os
import subprocess
import sys
from pathlib import Path

CONFLICTS = set()                       # 두 패키지 이상에 같은 이름으로 들어 있는 라이브러리
MACHO_MAGIC = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe"}


def is_macho(p):
    try:
        with open(p, "rb") as f:
            return f.read(4) in MACHO_MAGIC
    except OSError:
        return False


def deps(p):
    out = subprocess.run(["otool", "-L", str(p)], capture_output=True, text=True, check=True).stdout
    return [ln.strip().split(" (")[0] for ln in out.splitlines()[1:] if ln.strip()]


def fix_app(app):
    fw = Path(app) / "Contents" / "Frameworks"
    changed = []
    names = {}
    for libdir in fw.glob("*/__dot__dylibs"):
        for p in libdir.iterdir():
            names.setdefault(p.name, set()).add(libdir.parent.name)
    global CONFLICTS
    CONFLICTS = {n for n, pk in names.items() if len(pk) > 1}
    for libdir in sorted(fw.glob("*/__dot__dylibs")):
        pkg = libdir.parent
        own = {p.name for p in libdir.iterdir() if p.is_file() and not p.is_symlink()}
        for f in sorted(pkg.rglob("*")):
            if f.is_symlink() or not f.is_file() or not is_macho(f):
                continue
            args = []
            for d in deps(f):
                name = d.rsplit("/", 1)[-1]
                if d.startswith("@rpath/") and name in own and not (f.parent == libdir and name == f.name):
                    rel = os.path.relpath(libdir / name, f.parent)
                    args += ["-change", d, f"@loader_path/{rel}"]
            if f.parent == libdir and f.name in CONFLICTS:
                # 라이브러리 자신의 id 도 패키지별로 구분(헤더 여유 공간이 모자라면 생략 — 경로 수정만으로도 동작)
                r = subprocess.run(["install_name_tool", "-id", f"@rpath/{pkg.name}/{f.name}", str(f)],
                                   capture_output=True, text=True)
                if r.returncode == 0:
                    args.append("(id)")
            if args:
                cmd = [a for a in args if a != "(id)"]
                if cmd:
                    r = subprocess.run(["install_name_tool", *cmd, str(f)], capture_output=True, text=True)
                    if r.returncode != 0:
                        raise RuntimeError(f"install_name_tool 실패: {f}\n{r.stderr}")
                subprocess.run(["codesign", "--force", "--sign", "-", str(f)], check=True, capture_output=True)
                changed.append(f.relative_to(fw))
    subprocess.run(["codesign", "--force", "--sign", "-", str(app)], check=True, capture_output=True)
    return changed


if __name__ == "__main__":
    ch = fix_app(sys.argv[1] if len(sys.argv) > 1 else "dist/SORI.app")
    print(f"이름이 겹치는 라이브러리: {', '.join(sorted(CONFLICTS))}")
    print(f"수정한 파일 {len(ch)}개")
    for c in ch:
        print(" ", c)
