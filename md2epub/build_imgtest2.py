#!/usr/bin/env python3
"""Diagnostic EPUB #2: localize the display-formula blow-up structurally.

All rows link the production epub-png.css and use the same display raster.
The only variable is the surrounding markup of the <img>.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import zipfile
from pathlib import Path

TOOL = Path(__file__).resolve().parent
# Book project dir (contains hybrid_auto/ and receives md2epub-build/):
# env MD2EPUB_PROJECT, else first CLI arg, else the current book.
PROJECT = Path(
    os.environ.get("MD2EPUB_PROJECT")
    or (sys.argv[1] if len(sys.argv) > 1 else "/Users/hengliu/Downloads/数学分析(第三版·上册)")
).expanduser()
BUILD = PROJECT / "md2epub-build"
SRC = BUILD / "imgtest"          # rasters from build_imgtest.py
OUT = PROJECT / "公式显示测试2.epub"


def img_tag(src: str, w: int, h: int, cls: str = "", style: str = "") -> str:
    c = f' class="{cls}"' if cls else ""
    s = f' style="{style}"' if style else ""
    return f'<img{c} src="../media/{src}" width="{w}" height="{h}"{s} alt="公式"/>'


def main() -> None:
    import json

    cache = json.loads((BUILD / "math_cache.json").read_text())
    key = next(
        k for k, e in cache.items()
        if "svg" in e and (m := re.search(
            r'width="([\d.]+)ex" height="([\d.]+)ex"', e["svg"]))
        and float(m.group(1)) > 50 and e["svg"].count("<svg") == 1
    )
    m = re.search(r'width="([\d.]+)ex" height="([\d.]+)ex"', cache[key]["svg"])
    w_ex, h_ex = float(m.group(1)), float(m.group(2))
    w8, h8 = round(w_ex * 8), round(h_ex * 8)

    IMG = img_tag("display-3.png", w8, h8, style=f"width:{w_ex}ex")
    IMG_CLS = img_tag("display-3.png", w8, h8, cls="math-inline",
                      style=f"width:{w_ex}ex")

    rows = [
        ("G", "div 包裹（上一正式版形态，预期过大）",
         f'<div class="math-display">\n{IMG}\n</div>'),
        ("H", "div 包裹 + 零宽空格（当前正式版形态）",
         f'<div class="math-display">&#8203;\n{IMG}\n</div>'),
        ("I", "p 包裹 + 零宽空格（无 div）",
         f'<p class="math-display">&#8203;{IMG}</p>'),
        ("J", "div 包裹 + img 挂 math-inline 类（CSS display:inline）",
         f'<div class="math-display">\n{IMG_CLS}\n</div>'),
        ("K", "p 带可见文字 + img（测试一的 A 行 + 本样式表）",
         f'<p>对照文字：{IMG}</p>'),
        ("L", "span 包裹（inline 上下文）",
         f'<span>{IMG}</span>'),
    ]
    body = ["<h1>公式显示测试 2</h1>",
            "<p>同一样式表、同一行间公式位图，仅外层结构不同。",
            "请记录每行尺寸：正常 / 过大 / 缺失。</p>"]
    for letter, note, el in rows:
        body.append(f"<h2>【{letter}】{note}</h2>")
        body.append(f"<p>{el}</p>")
        body.append(f"<p><small>（上行对应 {letter}）</small></p>")
    xhtml = (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" lang="zh-CN" xml:lang="zh-CN">\n'
        "<head><title>公式显示测试 2</title>"
        '<link rel="stylesheet" type="text/css" href="../styles/epub.css"/>'
        "</head>\n<body>\n" + "\n".join(body) + "\n</body>\n</html>\n"
    )

    epub = BUILD / "imgtest2_epub"
    if epub.exists():
        shutil.rmtree(epub)
    for sub in ("text", "media", "styles"):
        (epub / "EPUB" / sub).mkdir(parents=True)
    (epub / "EPUB" / "text" / "test.xhtml").write_text(xhtml, encoding="utf-8")
    shutil.copyfile(SRC / "display-3.png", epub / "EPUB" / "media" / "display-3.png")
    shutil.copyfile(TOOL / "epub-png.css", epub / "EPUB" / "styles" / "epub.css")
    (epub / "META-INF").mkdir()
    (epub / "META-INF" / "container.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
        '  <rootfiles><rootfile full-path="EPUB/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles>\n</container>\n',
        encoding="utf-8",
    )
    (epub / "EPUB" / "content.opf").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bid">\n'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="bid">urn:uuid:1b4d0a3e-test-4c2f-9a61-imgtest00002</dc:identifier>'
        '<dc:title>公式显示测试 2</dc:title><dc:language>zh-CN</dc:language>'
        '<meta property="dcterms:modified">2026-09-16T00:00:00Z</meta></metadata>\n'
        '<manifest><item id="t" href="text/test.xhtml" '
        'media-type="application/xhtml+xml"/>'
        '<item id="css" href="styles/epub.css" media-type="text/css"/>'
        '<item id="img" href="media/display-3.png" media-type="image/png"/>'
        '</manifest>\n<spine><itemref idref="t"/></spine>\n</package>\n',
        encoding="utf-8",
    )
    OUT.unlink(missing_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip")
        for p in sorted(epub.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(epub).as_posix())
    print(f"built {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
