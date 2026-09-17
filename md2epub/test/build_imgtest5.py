#!/usr/bin/env python3
"""Diagnostic EPUB #5: the two remaining formula defects on iReader.

Device-reported symptoms (after the dual-unit cascade + width-only rebuild):
  1. some INLINE formulas are horizontally squeezed
  2. some MULTI-LINE display formulas render far too small — "compressed
     into a single text line" — and single-line display formulas only as
     tall as the text could stand to be somewhat larger

Leading hypothesis (unifies all evidence): the engine clamps INLINE-level
images to the line box — keeping the width — so tall inline images get
horizontally squeezed (1) and tall display images (currently display:inline
inside .math-display) get clamped to ~one line height (2). Content figures
(display:block, img.figure-img) are often taller than a line and have never
been squashed — block-level images seem exempt.

Rows (one variable each; same bitmap, same dims):
  G1  wide inline formula, current markup, late in a full line — baseline
  G2  same formula promoted to a display block — candidate fix
  G3  inline, width attr omitted (height + valign only)
  G4  inline wrapped in <span style="white-space:nowrap">
  K1  NARROW-tall multi-line display, current pinned markup — KEY row:
      its width fits the column already, so if K1 is still clamped to one
      line the engine clamps height regardless of width (hypothesis proven)
  K2  K1 + display:block on the img — lead fix, narrow case
  H1  wide-tall multi-line display, width-only (current build) — baseline
  H2  same, pinned dual units (attrs + px/ex CSS)
  H3  same, pinned dual + display:block — lead fix, wide case
  H4  same, display:block + width-only (no height anywhere; may overflow)
  J1  modest single-line display, pinned dual, px k=8 (current size)
  J2  same at px k=10 — user size preference ("display can be larger")

Report per row: squeezed / correct / too small / overflow, aspect kept or
broken. K1 vs K2 and H2 vs H3 decide the fix.
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
from convert import _svg_ex_dims  # noqa: E402

TOOL = Path(__file__).resolve().parent
PROJECT = Path(
    os.environ.get("MD2EPUB_PROJECT")
    or (sys.argv[1] if len(sys.argv) > 1 else "/Users/hengliu/Downloads/input")
).expanduser()
BUILD = PROJECT / "md2epub-build"
OUT = PROJECT / "公式显示测试5.epub"
K = 8  # px per ex (current build factor)


def root_dims(entry: dict) -> tuple[float, float] | None:
    """Root-tag ex dims, skipping stretch (width="100%") formulas — the
    whole-string search in _svg_ex_dims mis-reads those (nested viewBox)."""
    if "svg" not in entry:
        return None
    svg = entry["svg"]
    if 'width="100%"' in svg[: svg.index(">")]:
        return None
    return _svg_ex_dims(svg)


def pick(cache: dict) -> dict[str, str]:
    """Sample keys: wide inline I, wide-tall W, narrow-tall N, modest D."""
    want = {
        "I": lambda w, h: 18 <= w <= 35 and h <= 2.5,
        "W": lambda w, h: 38 <= w <= 60 and 9 <= h <= 16,
        "N": lambda w, h: 16 <= w <= 30 and h >= 8,
        "D": lambda w, h: 12 <= w <= 25 and 2.5 <= h <= 6,
    }
    found: dict[str, str] = {}
    for k, e in cache.items():
        d = root_dims(e)
        if not d:
            continue
        for name, ok in want.items():
            if name not in found and ok(*d):
                found[name] = k
    missing = [n for n in want if n not in found]
    if missing:
        raise SystemExit(f"no sample for {missing} in math_cache.json")
    return found


def main() -> None:
    cache = json.loads((BUILD / "math_cache.json").read_text(encoding="utf-8"))
    keys = pick(cache)
    dims = {n: root_dims(cache[k]) for n, k in keys.items()}
    for n in "IWND":
        w, h = dims[n]
        print(f"  {n}: {keys[n][:8]}  {w:.1f}ex x {h:.1f}ex")
    iva = cache[keys["I"]].get("valign_ex", 0)

    media: dict[str, Path] = {}

    def src_png(name: str, key: str) -> str:
        if not name.endswith(".png"):
            name += ".png"  # weak engines sniff media type by extension
        p = BUILD / "png_cache" / f"eq-{key}.png"
        if not p.exists():
            raise SystemExit(f"missing raster {p}")
        media[name] = p
        return name

    def g_img(kind: str) -> str:
        w_ex, h_ex = dims["I"]
        w_px, h_px, v_px = round(w_ex * K), round(h_ex * K), round(iva * K)
        style = (f"height:{h_px}px;height:{h_ex}ex;"
                 f"vertical-align:{v_px}px;vertical-align:{iva}ex")
        name = src_png(f"G{kind}", keys["I"])
        if kind == "3":  # width attr omitted
            return (f'<img class="math-inline" src="../media/{name}" '
                    f'height="{h_px}" style="{style}" alt="公式"/>')
        img = (f'<img class="math-inline" src="../media/{name}" '
               f'width="{w_px}" height="{h_px}" style="{style}" alt="公式"/>')
        if kind == "4":  # nowrap span
            return f'<span style="white-space:nowrap">{img}</span>'
        return img

    def d_img(row: str, sample: str, kind: str, k: float = K) -> str:
        w_ex, h_ex = dims[sample]
        w_px, h_px = round(w_ex * k), round(h_ex * k)
        name = src_png(row, keys[sample])
        block = "display:block;margin:0 auto;" if "b" in kind else ""
        if kind in ("wo", "bwo"):  # width-only (no height attr/CSS at all)
            return (f'<img src="../media/{name}" width="{w_px}" '
                    f'style="{block}width:{w_px}px;width:{w_ex}ex" alt="公式"/>')
        return (f'<img src="../media/{name}" width="{w_px}" height="{h_px}" '
                f'style="{block}width:{w_px}px;width:{w_ex}ex;'
                f'height:{h_px}px;height:{h_ex}ex" alt="公式"/>')

    lead = "设函数在闭区间上连续且逐段可微，则对任意给定的正数与实数列"
    trail = "，结论恒成立。"
    wW, hW = dims["W"]
    wN, hN = dims["N"]
    wD, hD = dims["D"]

    def show(row: str, img: str) -> list[str]:
        return [f'<div class="math-display">&#8203;\n{img}\n</div>']

    body = [
        "<h1>公式显示测试 5（两个残留缺陷定位）</h1>",
        "<p>同一位图、同一尺寸数据，每行只变一个量。G 组看行内公式是否被"
        "<b>水平压缩</b>；K/H 组看多行行间公式是否被<b>压到一行文字高</b>、"
        "纵横比是否失真；J 组比较行间公式的合适大小。</p>",
        "<h2>【G1】宽行内公式·当前正式版标记（长段落行中后段）——基线</h2>",
        f"<p>{lead}{g_img('1')}{trail}</p>",
        "<h2>【G2】同一公式改为行间独占一行——候选修复</h2>",
        *show("G2", g_img("1")),
        "<h2>【G3】行内·省略 width 属性（只留高度/对齐）</h2>",
        f"<p>{lead}{g_img('3')}{trail}</p>",
        "<h2>【G4】行内·外包 white-space:nowrap 的 span</h2>",
        f"<p>{lead}{g_img('4')}{trail}</p>",
        f"<h2>【K1】窄而高多行公式（{wN:.0f}ex×{hN:.0f}ex，宽度不超栏宽）"
        "·当前钉宽高标记——<b>关键判定行</b></h2>",
        "<p><small>（若本行也被压到一行高 → 引擎与宽度无关地钳制行内图高）"
        "</small></p>",
        *show("K1", d_img("K1", "N", "pin")),
        "<h2>【K2】同公式·img 改 display:block——首席候选修复</h2>",
        *show("K2", d_img("K2", "N", "bpin")),
        f"<h2>【H1】宽而高多行公式（{wW:.0f}ex×{hW:.0f}ex）·width-only"
        "（当前正式版）——基线</h2>",
        *show("H1", d_img("H1", "W", "wo")),
        "<h2>【H2】同公式·钉宽高（属性+双单位 CSS）</h2>",
        *show("H2", d_img("H2", "W", "pin")),
        "<h2>【H3】同公式·钉宽高 + display:block——首席候选修复</h2>",
        *show("H3", d_img("H3", "W", "bpin")),
        "<h2>【H4】同公式·display:block + 仅宽度（可能溢出屏宽）</h2>",
        *show("H4", d_img("H4", "W", "bwo")),
        f"<h2>【J1】单行行间公式（{wD:.0f}ex×{hD:.1f}ex）·钉宽高，px 系数 k=8"
        "（当前大小）</h2>",
        *show("J1", d_img("J1", "D", "pin")),
        "<h2>【J2】同公式·px 系数 k=10（放大约一档，ex 不变）</h2>",
        *show("J2", d_img("J2", "D", "pin", k=10.0)),
    ]

    xhtml = (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" '
        'lang="zh-CN" xml:lang="zh-CN">\n'
        "<head><title>公式显示测试 5</title>"
        '<link rel="stylesheet" type="text/css" href="../styles/epub.css"/>'
        "</head>\n<body>\n" + "\n".join(body) + "\n</body>\n</html>\n"
    )

    stage = BUILD / "imgtest5_epub"
    if stage.exists():
        shutil.rmtree(stage)
    for sub in ("text", "media", "styles"):
        (stage / "EPUB" / sub).mkdir(parents=True)
    (stage / "EPUB" / "text" / "test.xhtml").write_text(xhtml, encoding="utf-8")
    for name, src in media.items():
        shutil.copyfile(src, stage / "EPUB" / "media" / name)
    shutil.copyfile(TOOL / "epub-png.css", stage / "EPUB" / "styles" / "epub.css")
    (stage / "META-INF").mkdir()
    (stage / "META-INF" / "container.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
        '  <rootfiles><rootfile full-path="EPUB/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles>\n</container>\n',
        encoding="utf-8",
    )
    items = "".join(
        f'<item id="m{i}" href="media/{name}" media-type="image/png"/>'
        for i, name in enumerate(sorted(media), 1)
    )
    (stage / "EPUB" / "content.opf").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bid">\n'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="bid">urn:uuid:1b4d0a3e-test-4c2f-9a61-imgtest00005</dc:identifier>'
        '<dc:title>公式显示测试 5</dc:title><dc:language>zh-CN</dc:language>'
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
        for p in sorted(stage.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(stage).as_posix())
    shutil.rmtree(stage, ignore_errors=True)
    print(f"built {OUT} ({OUT.stat().st_size / 1024:.0f} KB, {len(media)} pngs)")


if __name__ == "__main__":
    main()
