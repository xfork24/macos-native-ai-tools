#!/usr/bin/env python3
"""Diagnostic EPUB #3: which SVG subset does the iReader engine rasterize?

Earlier tests showed MathJax's raw SVG blank both inline and external — but
that output mixes several possibly-fatal ingredients (ex units, defs/use/
xlink, nested svg viewports, currentColor, data-* attrs). This test isolates
one variable per row using the flattened path-only variant (flatten_svg.py):

  A  hand-written trivial svg (paths + px root, #000) — engine baseline
  B  flattened formula (paths + matrix, px root, default black fill)
  C  flattened stretchy formula (nested viewport resolved)
  D  = B with fill="currentColor"
  E  original MathJax svg (defs/use/xlink intact), px root, #000
  F  original MathJax svg, px root, currentColor   (old failing row, control)
  G  = B with ex-unit root dims
  H  flattened svg embedded directly in the XHTML (no <img> file)

Each row shows both a simple and a stretchy formula. Report the letters.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from flatten_svg import flatten, _svg_ex_dims  # noqa: E402

TOOL = Path(__file__).resolve().parent
# Book project dir (contains hybrid_auto/ and receives md2epub-build/):
# env MD2EPUB_PROJECT, else first CLI arg, else the current book.
PROJECT = Path(
    os.environ.get("MD2EPUB_PROJECT")
    or (sys.argv[1] if len(sys.argv) > 1 else "/Users/hengliu/Downloads/数学分析(第三版·上册)")
).expanduser()
BUILD = PROJECT / "md2epub-build"
OUT = PROJECT / "公式显示测试3.epub"


def pick(cache: dict) -> tuple[str, str]:
    """A simple (1 use, tiny) and a stretchy (nested svg, root viewBox) key."""
    simple = stretch = None
    for k, e in cache.items():
        if "svg" not in e:
            continue
        s = e["svg"]
        root = s[: s.index(">")]
        if "viewBox=" not in root:
            continue
        if simple is None and s.count("<use") == 1 and len(s) < 2500:
            simple = k
        if stretch is None and s.count("<svg") >= 2 and len(s) < 6000:
            stretch = k
        if simple and stretch:
            break
    return simple, stretch


def main() -> None:
    cache = json.loads((BUILD / "math_cache.json").read_text(encoding="utf-8"))
    simple_k, stretch_k = pick(cache)

    variants: dict[str, dict[str, tuple[str, int, int]]] = {}
    # B/D/G/H variants via flatten(); E/F via root-dims substitution.
    for row, fill in (("B", None), ("C", None), ("D", "currentColor"), ("H", None)):
        for tag, key in (("simple", simple_k), ("stretch", stretch_k)):
            flat, w, h = flatten(cache[key]["svg"], fill=fill)
            variants.setdefault(row, {})[tag] = (flat, w, h)
    for row in ("G",):
        for tag, key in (("simple", simple_k), ("stretch", stretch_k)):
            flat, w, h = flatten(cache[key]["svg"])
            w_ex, h_ex = _svg_ex_dims(cache[key]["svg"])
            ex_root = re.sub(
                r'viewBox="0 0 \d+ \d+" width="\d+" height="\d+"',
                f'viewBox="0 0 {w} {h}" width="{w_ex}ex" height="{h_ex}ex"',
                flat, count=1,
            )
            variants.setdefault(row, {})[tag] = (ex_root, w, h)
    for row, fill in (("E", "#000000"), ("F", "currentColor")):
        for tag, key in (("simple", simple_k), ("stretch", stretch_k)):
            svg = cache[key]["svg"]
            w_ex, h_ex = _svg_ex_dims(svg)
            w, h = round(w_ex * 8), round(h_ex * 8)
            if fill == "#000000":
                svg = svg.replace('stroke="currentColor"', 'stroke="#000000"')
                svg = svg.replace('fill="currentColor"', 'fill="#000000"')
            svg = re.sub(
                r'width="[\d.]+ex" height="[\d.]+ex"',
                f'width="{w}" height="{h}"', svg, count=1,
            )
            variants.setdefault(row, {})[tag] = (re.sub(r"\s+", " ", svg).strip(), w, h)

    # Row A: hand-written trivial svg — a fraction-bar-like glyph pair.
    a_svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 36 44" '
        'width="36" height="44">'
        '<path d="M4 6h28v3H4z"/>'
        '<path d="M4 34h28v3H4z"/>'
        '<path d="M11 20h14v3H11z"/>'
        "</svg>"
    )
    variants["A"] = {"simple": (a_svg, 36, 44), "stretch": (a_svg, 36, 44)}

    media: dict[str, str] = {}  # filename -> svg text

    def img_for(row: str, tag: str) -> str:
        svg, w, h = variants[row][tag]
        if row == "H":
            va = cache[simple_k if tag == "simple" else stretch_k].get("valign_ex", 0)
            return (
                f'<span class="math-inline" style="vertical-align:{va}ex">{svg}</span>'
            )
        name = f"{row}-{tag}.svg"
        media[name] = svg
        cls = ' class="math-inline"' if tag == "simple" else ""
        style = f"height:{h / 8}ex" if tag == "simple" else f"width:{w / 8}ex"
        return (
            f'<img{cls} src="../media/{name}" width="{w}" height="{h}" '
            f'style="{style}" alt="公式"/>'
        )

    rows = [
        ("A", "手写平凡svg（纯path+px根）——引擎基线"),
        ("B", "压平公式（纯path+matrix，px根，默认黑）"),
        ("C", "压平含伸缩件的公式（嵌套svg已解析）"),
        ("D", "压平公式 + fill=currentColor"),
        ("E", "原始MathJax（defs/use/xlink保留）+ px根 + #000"),
        ("F", "原始MathJax + px根 + currentColor（旧失败复现）"),
        ("G", "压平公式 + ex单位根"),
        ("H", "压平公式直接内嵌XHTML（无外链文件）"),
    ]
    tag_cn = {"simple": "简单型", "stretch": "伸缩型"}
    body = [
        "<h1>公式显示测试 3（SVG 兼容性矩阵）</h1>",
        "<p>同一样式表。每行仅一个变量不同。请记录每行两个公式的状态："
        "正常显示 / 空白缺失 / 尺寸异常。</p>",
    ]
    for letter, note in rows:
        body.append(f"<h2>【{letter}】{note}</h2>")
        body.append(f"<p>对照文字{img_for(letter, 'simple')}对照文字</p>")
        body.append(f"<p>{img_for(letter, 'stretch')}</p>")
        body.append(f"<p><small>（上行对应 {letter}）</small></p>")

    xhtml = (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" '
        'lang="zh-CN" xml:lang="zh-CN">\n'
        "<head><title>公式显示测试 3</title>"
        '<link rel="stylesheet" type="text/css" href="../styles/epub.css"/>'
        "</head>\n<body>\n" + "\n".join(body) + "\n</body>\n</html>\n"
    )

    epub = BUILD / "imgtest3_epub"
    if epub.exists():
        shutil.rmtree(epub)
    for sub in ("text", "media", "styles"):
        (epub / "EPUB" / sub).mkdir(parents=True)
    (epub / "EPUB" / "text" / "test.xhtml").write_text(xhtml, encoding="utf-8")
    for name, svg in media.items():
        (epub / "EPUB" / "media" / name).write_text(svg, encoding="utf-8")
    shutil.copyfile(TOOL / "epub-png.css", epub / "EPUB" / "styles" / "epub.css")
    (epub / "META-INF").mkdir()
    (epub / "META-INF" / "container.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
        '  <rootfiles><rootfile full-path="EPUB/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles>\n</container>\n',
        encoding="utf-8",
    )
    items = "".join(
        f'<item id="m{i}" href="media/{name}" media-type="image/svg+xml"/>'
        for i, name in enumerate(sorted(media), 1)
    )
    (epub / "EPUB" / "content.opf").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bid">\n'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="bid">urn:uuid:1b4d0a3e-test-4c2f-9a61-imgtest00003</dc:identifier>'
        '<dc:title>公式显示测试 3</dc:title><dc:language>zh-CN</dc:language>'
        '<meta property="dcterms:modified">2026-09-17T00:00:00Z</meta></metadata>\n'
        f'<manifest><item id="t" href="text/test.xhtml" '
        f'media-type="application/xhtml+xml"/>'
        f'<item id="css" href="styles/epub.css" media-type="text/css"/>{items}'
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
