#!/usr/bin/env python3
"""Diagnostic EPUB #7: sizing display-formula blocks by their true specs.

Book-build device report (figure-pattern display formulas):
  - multi-line formulas: readable but "a bit large"
  - narrow single-line formulas: WAY too large
That gradient matches a full-width STRETCH of bare-<p> block images
(narrow 150px attrs -> 4x stretch, wide 420px attrs -> 1.3x), consistent
with the old finding that iReader upscales image-only blocks. KOReader
normal. So: find a block markup that renders at EXACTLY the px attrs, then
tune the scale per formula height.

Rows (N = narrow single-line ~13ex, M = multi-line, T = tall inline today):
  A  current build markup (class + dual-unit style + 2x attrs) — baseline
  B  bare <p>, attrs only (no class, no style) — pure intrinsic behaviour
  C  bare <p>, attrs + px-only style (no ex anywhere)
  D  A + max-width:none prepended (kill an internal img{max-width:100%})
  E  table wrapper <table><tr><td align="center"><img attrs/></td></tr></table>
     (EPUB2-era shrinkwrap scaffolding, the oldest compat trick there is)
  F  C markup at scale 1.6 (multi-line size preview once stretch is fixed)
  G  C markup at scale 1.3 on T — PREVIEW of promoting tall inline formulas
     to their own centered line (108 occurrences >=4.8ex in this book)
  H  T inline, current book markup — anchor showing today's line-height squash

Report per row: 宽度正常/被拉到全屏宽, 高度, 是否居中, 字符大小与正文是否相称.
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
OUT = PROJECT / "公式显示测试7.epub"
K = 8  # px per ex


def root_dims(entry: dict) -> tuple[float, float] | None:
    if "svg" not in entry:
        return None
    svg = entry["svg"]
    if 'width="100%"' in svg[: svg.index(">")]:
        return None
    return _svg_ex_dims(svg)


def pick(cache: dict) -> dict[str, str]:
    # Token kind comes from the built markdown — the smart-style flips mean
    # narrow display formulas carry inline-rendered bitmaps and vice versa.
    md = (BUILD / "preprocessed.png.md").read_text(encoding="utf-8")
    inline_keys = set(re.findall(r"⟂MATHI:([0-9a-f]+)⟂", md))
    display_keys = set(re.findall(r"⟂MATHD:([0-9a-f]+)⟂", md))
    want = {
        # narrow single-line display (inline-rendered after the flip)
        "N": lambda k, w, h: k in display_keys and 9 <= w <= 18 and h <= 2.5,
        # multi-line display
        "M": lambda k, w, h: k in display_keys and 20 <= w <= 38 and 8 <= h <= 14,
        # tall formula that is INLINE today (the clamped crowd)
        "T": lambda k, w, h: k in inline_keys and 8 <= w <= 30 and 4.8 <= h <= 7,
    }
    found: dict[str, str] = {}
    for k, e in cache.items():
        d = root_dims(e)
        if not d:
            continue
        for name, ok in want.items():
            if name not in found and ok(k, *d):
                found[name] = k
    missing = [n for n in want if n not in found]
    if missing:
        raise SystemExit(f"no sample for {missing} in math_cache.json")
    return found


def main() -> None:
    cache = json.loads((BUILD / "math_cache.json").read_text(encoding="utf-8"))
    keys = pick(cache)
    dims = {n: root_dims(cache[k]) for n, k in keys.items()}
    iva = {n: cache[k].get("valign_ex", 0) for n, k in keys.items()}
    for n in "NMT":
        w, h = dims[n]
        print(f"  {n}: {keys[n][:8]}  {w:.1f}ex x {h:.1f}ex  inline={cache[keys[n]].get('rendered_display') is False}")

    media: dict[str, Path] = {}

    def src_png(row: str, key: str) -> str:
        name = f"{row}.png"
        p = BUILD / "png_cache" / f"eq-{key}.png"
        if not p.exists():
            raise SystemExit(f"missing raster {p}")
        media[name] = p
        return name

    def a(w_ex: float, h_ex: float, s: float) -> tuple[int, int]:
        return round(w_ex * K * s), round(h_ex * K * s)

    def cur(row: str, sample: str, s: float = 2.0, extra: str = "") -> str:
        """A: exact current book build markup."""
        w_ex, h_ex = dims[sample]
        w, h = a(w_ex, h_ex, s)
        return (
            f'<p><img class="math-display-img" src="../media/{src_png(row, keys[sample])}" '
            f'width="{w}" height="{h}" '
            f'style="{extra}width:{w}px;width:{w_ex}ex;'
            f'height:{h}px;height:{h_ex}ex" alt="公式"/></p>'
        )

    def bare(row: str, sample: str, s: float = 2.0) -> str:
        """B: attrs only."""
        w, h = a(*dims[sample], s)
        return (
            f'<p><img src="../media/{src_png(row, keys[sample])}" '
            f'width="{w}" height="{h}" alt="公式"/></p>'
        )

    def pxstyle(row: str, sample: str, s: float = 2.0) -> str:
        """C: attrs + px-only style (no ex, no class)."""
        w, h = a(*dims[sample], s)
        return (
            f'<p><img src="../media/{src_png(row, keys[sample])}" '
            f'width="{w}" height="{h}" style="width:{w}px;height:{h}px" alt="公式"/></p>'
        )

    def tabled(row: str, sample: str, s: float = 2.0) -> str:
        """E: EPUB2-era table shrinkwrap."""
        w, h = a(*dims[sample], s)
        return (
            f'<table style="margin:0.8em auto"><tr><td style="text-align:center">'
            f'<img src="../media/{src_png(row, keys[sample])}" '
            f'width="{w}" height="{h}" alt="公式"/></td></tr></table>'
        )

    def inline_now(row: str, sample: str) -> str:
        """H: exact current inline markup (height only + dual-unit valign)."""
        _, h_ex = dims[sample]
        v = iva[sample]
        h_px, v_px = round(h_ex * K), round(v * K)
        return (
            f'<img class="math-inline" src="../media/{src_png(row, keys[sample])}" '
            f'height="{h_px}" '
            f'style="height:{h_px}px;height:{h_ex}ex;'
            f'vertical-align:{v_px}px;vertical-align:{v}ex" alt="公式"/>'
        )

    wN, hN = dims["N"]
    wM, hM = dims["M"]
    wT, hT = dims["T"]
    lead = "设函数在闭区间上连续且逐段可微，则对任意给定的正数与实数列"
    trail = "，结论恒立。"

    body = [
        "<h1>公式显示测试 7（行间公式按规格定尺寸）</h1>",
        "<p>每行回报三件事：<b>宽度</b>（正常/被拉到全屏宽）、<b>高度</b>（与公式"
        "应有高度是否相符）、<b>是否居中</b>。N=窄单行公式，M=多行公式，"
        "T=当前被压成行等高的复杂行内公式。</p>",
        f"<h2>【A1】N（{wN:.0f}ex×{hN:.1f}ex）当前书内标记——基线</h2>",
        cur("A1", "N"),
        f"<h2>【A2】M（{wM:.0f}ex×{hM:.1f}ex）当前书内标记——基线</h2>",
        cur("A2", "M"),
        f"<h2>【B1】N·裸 p + 仅原生属性（无类无 style）</h2>",
        bare("B1", "N"),
        f"<h2>【B2】M·同上</h2>",
        bare("B2", "M"),
        f"<h2>【C1】N·裸 p + 属性 + 纯 px style（无 ex）</h2>",
        pxstyle("C1", "N"),
        f"<h2>【C2】M·同上</h2>",
        pxstyle("C2", "M"),
        f"<h2>【D】N·A 标记 + max-width:none 前置</h2>",
        cur("D", "N", extra="max-width:none;"),
        f"<h2>【E1】N·table 包裹（EPUB2 收缩容器）</h2>",
        tabled("E1", "N"),
        f"<h2>【E2】M·同上</h2>",
        tabled("E2", "M"),
        f"<h2>【F】M·C 标记 + 1.6× 属性（修好拉伸后的多行尺寸预览）</h2>",
        pxstyle("F", "M", s=1.6),
        f"<h2>【G】T（{wT:.0f}ex×{hT:.1f}ex）·C 标记 + 1.3×——复杂行内公式"
        "改排为独立居中行的效果预览</h2>",
        pxstyle("G", "T", s=1.3),
        f"<h2>【H】T·当前行内标记（正文行内）——今天被压成行等高的样子</h2>",
        f"<p>{lead}{inline_now('H', 'T')}{trail}</p>",
    ]

    xhtml = (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" '
        'lang="zh-CN" xml:lang="zh-CN">\n'
        "<head><title>公式显示测试 7</title>"
        '<link rel="stylesheet" type="text/css" href="../styles/epub.css"/>'
        "</head>\n<body>\n" + "\n".join(body) + "\n</body>\n</html>\n"
    )

    stage = BUILD / "imgtest7_epub"
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
        '<dc:identifier id="bid">urn:uuid:1b4d0a3e-test-4c2f-9a61-imgtest00007</dc:identifier>'
        '<dc:title>公式显示测试 7</dc:title><dc:language>zh-CN</dc:language>'
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
