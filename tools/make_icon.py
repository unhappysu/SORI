# -*- coding: utf-8 -*-
"""앱 아이콘 만들기 — 앱 기획안 표지 틀: 흰 바탕에 빨간(#D00000) 굵은 SORI, 아래 빨간 선.
만드는 파일: sori/assets/SORI.png(창·Dock 아이콘, 1024 px), packaging/icon/SORI.icns(macOS 앱), SORI.ico(Windows exe)
  .venv/bin/python tools/make_icon.py        (macOS 의 iconutil 필요 — icns)
※ Claude(Anthropic의 AI)를 활용하여 제작하였음.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
RED = (208, 0, 0, 255)
FONT = ROOT / "sori" / "fonts" / "NotoSansKR-Bold.ttf"


def draw(size=1024):
    s = size / 1024
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    m, r = 100 * s, 185 * s                                   # macOS 아이콘 격자: 824 px 둥근 사각형
    d.rounded_rectangle([m, m + 8 * s, size - m, size - m + 8 * s], r, fill=(0, 0, 0, 40))   # 옅은 그림자
    d.rounded_rectangle([m, m, size - m, size - m], r, fill=(255, 255, 255, 255), outline=(225, 225, 225, 255),
                        width=max(1, int(3 * s)))
    font = ImageFont.truetype(str(FONT), int(250 * s))
    t = "SORI"
    x0, y0, x1, y1 = d.textbbox((0, 0), t, font=font)
    w, h = x1 - x0, y1 - y0
    cx, cy = size / 2, size / 2 - 40 * s
    d.text((cx - w / 2 - x0, cy - h / 2 - y0), t, font=font, fill=RED)
    ly = cy + h / 2 + 70 * s                                   # 제목 아래 빨간 선(표지의 양옆 선을 줄여 씀)
    d.rectangle([m + 90 * s, ly, size - m - 90 * s, ly + 22 * s], fill=RED)
    return im


def main():
    big = draw(1024)
    png = ROOT / "sori" / "assets" / "SORI.png"
    big.save(png)
    ico = ROOT / "packaging" / "icon" / "SORI.ico"
    big.save(ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    tmp = Path(tempfile.mkdtemp()) / "SORI.iconset"
    tmp.mkdir()
    for n in (16, 32, 128, 256, 512):
        draw(n).save(tmp / f"icon_{n}x{n}.png")
        draw(n * 2).save(tmp / f"icon_{n}x{n}@2x.png")
    icns = ROOT / "packaging" / "icon" / "SORI.icns"
    subprocess.run(["iconutil", "-c", "icns", str(tmp), "-o", str(icns)], check=True)
    shutil.rmtree(tmp.parent)
    for p in (png, ico, icns):
        print(p, p.stat().st_size)


if __name__ == "__main__":
    sys.exit(main())
