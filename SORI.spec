# -*- mode: python ; coding: utf-8 -*-
# SORI 앱 패키징 설정 (PyInstaller 6.x, --onedir · --windowed 에 해당)
#   빌드: pyinstaller --noconfirm --clean SORI.spec
#   결과: macOS dist/SORI.app, Windows dist/SORI/SORI.exe
# ※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH)
sys.path.insert(0, str(ROOT))
from sori import __version__  # noqa: E402

datas = []
# sori 패키지의 데이터 파일: templates(설정 서식), vendor(three.js), fonts(Noto Sans KR, OFL)
datas += collect_data_files("sori", excludes=["**/__pycache__/**"])
# 자가 점검·예제용 기준 사례(가상 교차로) 입력과 기준값
datas += [(str(ROOT / "tests" / "reference" / "synthetic"), "tests/reference/synthetic")]
# PROJ·GDAL 데이터: pyinstaller-hooks-contrib 의 pyproj·rasterio·pyogrio 훅이 넣지만, 빠지면 좌표가 틀어지므로 명시
datas += collect_data_files("pyproj")
datas += collect_data_files("rasterio")
datas += collect_data_files("pyogrio")

a = Analysis(
    ["sori_gui.py"],
    pathex=[str(ROOT), str(ROOT / "tests")],
    binaries=[],
    datas=datas,
    # rasterio 등은 Cython 모듈 안에서 import 하는 하위 모듈(rasterio.serde 등)이 자동 탐지되지 않음
    hiddenimports=collect_submodules("sori") + ["reference_case"]
    + collect_submodules("rasterio") + collect_submodules("pyproj") + collect_submodules("pyogrio")
    # 그림 저장 형식(PNG·PDF)별 matplotlib 백엔드는 savefig 때 이름으로 불러와 자동 탐지되지 않음
    + ["matplotlib.backends.backend_agg", "matplotlib.backends.backend_pdf"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 웹 화면(Streamlit)·테스트 도구는 앱에 넣지 않음
    excludes=["streamlit", "streamlit_folium", "folium", "pytest", "IPython", "PySide6", "PyQt5", "PyQt6"],
    noarchive=False,
    optimize=0,
    # numba 의 cache=True 는 원본 .py 파일이 있어야 동작함 → sori 는 .py 원본째 넣음(계산 코드 변경 없이 해결)
    module_collection_mode={"sori": "py", "reference_case": "py"},
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SORI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # --windowed
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, upx_exclude=[], name="SORI")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="SORI.app",
        icon=None,
        bundle_identifier="kr.sori.noisemap",
        version=__version__,
        info_plist={
            "CFBundleName": "SORI",
            "CFBundleDisplayName": "SORI",
            "CFBundleShortVersionString": __version__,
            "NSHighResolutionCapable": True,
            "NSHumanReadableCopyright": "※ Claude(Anthropic의 AI)를 활용하여 제작하였음.",
        },
    )

    # 빌드 후처리: pyproj·rasterio 의 같은 이름 dylib(libproj 등) 충돌 해결 → packaging/macos_fix_dylibs.py 설명 참고
    sys.path.insert(0, str(ROOT / "packaging"))
    import macos_fix_dylibs  # noqa: E402
    _fixed = macos_fix_dylibs.fix_app(Path(DISTPATH) / "SORI.app")
    print(f"[SORI] macOS dylib 충돌 후처리: {len(_fixed)}개 파일 수정, 이름 겹침 {sorted(macos_fix_dylibs.CONFLICTS)}")
