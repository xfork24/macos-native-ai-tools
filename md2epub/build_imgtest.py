#!/usr/bin/env python3
"""Build a one-page diagnostic EPUB: 6 image-sizing strategies side by side.

Opens on any reader; the row labels that render at the right size tell us
which strategy that reader's engine supports. Copy to the device, compare,
report the letters.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

# Book project dir (contains hybrid_auto/ and receives md2epub-build/):
# env MD2EPUB_PROJECT, else first CLI arg, else the current book.
PROJECT = Path(
    os.environ.get("MD2EPUB_PROJECT")
    or (sys.argv[1] if len(sys.argv) > 1 else "/Users/hengliu/Downloads/数学分析(第三版·上册)")
).expanduser()
BUILD = PROJECT / "md2epub-build"
OUT = PROJECT / "公式显示测试.epub"

W_NOTE = "（放大看是否清晰）"


def main() -> None:
    cache = json.loads((BUILD / "math_cache.json").read_text())
    # one small (inline-ish, <12ex) and one wide (display-ish, >50ex) formula;
    # both plain single-svg so the sizing comparison stays apples-to-apples.
    inline_key = display_key = None
    for k, e in cache.items():
        if "svg" not in e:
            continue
        m = re.search(r'width="([\d.]+)ex" height="([\d.]+)ex"', e["svg"])
        if not (m and e["svg"].count("<svg") == 1):
            continue
        w = float(m.group(1))
        if inline_key is None and w < 12:
            inline_key = k
        elif display_key is None and w > 50:
            display_key = k
        if inline_key and display_key:
            break

    def dims(key: str) -> tuple[float, float]:
        m = re.search(r'width="([\d.]+)ex" height="([\d.]+)ex"', cache[key]["svg"])
        return float(m.group(1)), float(m.group(2))

    tdir = BUILD / "imgtest"
    tdir.mkdir(exist_ok=True)
    for tag, key in (("inline", inline_key), ("display", display_key)):
        svg = cache[key]["svg"]
        w, h = dims(key)
        sf = tdir / f"{tag}.svg"
        sf.write_text(svg)
        for zoom in (1, 2, 3):
            subprocess.run(
                ["rsvg-convert", "--width", str(round(w * 8 * zoom)),
                 "-o", str(tdir / f"{tag}-{zoom}.png"), str(sf)],
                check=True, capture_output=True,
            )
        # external svg variant with px root dims
        px = re.sub(r'width="[\d.]+ex" height="[\d.]+ex"',
                    f'width="{round(w * 8)}" height="{round(h * 8)}"', svg, count=1)
        (tdir / f"{tag}-px.svg").write_text(px)

    def img(src: str, w: float, h: float, style: str = "", cls: str = "") -> str:
        s = f' style="{style}"' if style else ""
        c = f' class="{cls}"' if cls else ""
        return f'<img{c} src="../media/{src}" width="{round(w)}" height="{round(h)}"{s} alt="公式"/>'

    rows: list[tuple[str, str, dict[str, str]]] = [
        ("A", "3×位图＋尺寸属性＋CSS（现正式版做法）", {}),
        ("B", "3×位图＋尺寸属性，无CSS无class", {}),
        ("C", "1×位图（固有尺寸即显示尺寸）", {}),
        ("D", "2×位图＋尺寸属性", {}),
        ("E", "外链SVG（根尺寸为px）＋尺寸属性", {}),
        ("F", "内联SVG元素（对照组，预期空白）", {}),
    ]
    body = ["<h1>公式显示测试</h1>",
            "<p>同一组公式按六种策略渲染。请记录：哪些行尺寸正常、哪些行公式缺失/过大/模糊。</p>"]
    tag_cn = {"inline": "行内型", "display": "行间型"}
    for letter, note, _ in rows:
        body.append(f"<h2>【{letter}】{note}</h2>")
        for tag in ("inline", "display"):
            w, h = dims(inline_key if tag == "inline" else display_key)
            w8, h8 = w * 8, h * 8
            if letter == "A":
                style = f"height:{h}ex" if tag == "inline" else f"width:{w}ex"
                el = img(f"{tag}-3.png", w8, h8, style=style, cls="math-inline")
            elif letter == "B":
                el = img(f"{tag}-3.png", w8, h8)
            elif letter == "C":
                el = f'<img src="../media/{tag}-1.png" alt="公式"/>'
            elif letter == "D":
                el = img(f"{tag}-2.png", w8, h8)
            elif letter == "E":
                el = img(f"{tag}-px.svg", w8, h8)
            else:
                el = re.sub(r"\s+", " ", cache[
                    inline_key if tag == "inline" else display_key]["svg"]).strip()
            body.append(f"<p>{tag_cn[tag]}： {el}</p>")
    xhtml = (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" lang="zh-CN" xml:lang="zh-CN">\n'
        "<head><title>公式显示测试</title></head>\n<body>\n"
        + "\n".join(body) + "\n</body>\n</html>\n"
    )

    epub = BUILD / "imgtest_epub"
    if epub.exists():
        import shutil
        shutil.rmtree(epub)
    (epub / "EPUB" / "text").mkdir(parents=True)
    (epub / "EPUB" / "media").mkdir(parents=True)
    (epub / "EPUB" / "text" / "test.xhtml").write_text(xhtml, encoding="utf-8")
    for f in tdir.iterdir():
        if f.suffix in (".png", ".svg"):
            import shutil
            shutil.copyfile(f, epub / "EPUB" / "media" / f.name)
    (epub / "META-INF").mkdir()
    (epub / "META-INF" / "container.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
        '  <rootfiles><rootfile full-path="EPUB/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles>\n</container>\n',
        encoding="utf-8",
    )
    items = "".join(
        f'<item id="m{i}" href="media/{f.name}" '
        f'media-type="{"image/png" if f.suffix == ".png" else "image/svg+xml"}"/>'
        for i, f in enumerate(sorted(tdir.iterdir()), 1)
    )
    (epub / "EPUB" / "content.opf").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bid">\n'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="bid">urn:uuid:1b4d0a3e-test-4c2f-9a61-imgtest00001</dc:identifier>'
        '<dc:title>公式显示测试</dc:title><dc:language>zh-CN</dc:language>'
        '<meta property="dcterms:modified">2026-09-16T00:00:00Z</meta></metadata>\n'
        f'<manifest><item id="t" href="text/test.xhtml" '
        f'media-type="application/xhtml+xml"/>{items}</manifest>\n'
        '<spine><itemref idref="t"/></spine>\n</package>\n',
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
