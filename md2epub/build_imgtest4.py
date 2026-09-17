#!/usr/bin/env python3
"""Calibration EPUB #4: png-formula sizing & alignment on CSS-poor engines.

Device facts so far: iReader ignores ex-unit CSS (formulas fall back to the
native width/height px attrs, vertical-align is lost entirely), treats
max-width:100% as width:100%, upscales image-only blocks (defeated by a
leading ZWSP). This test isolates, one variable per row, the fixes under
consideration — dual-unit cascade (px first, ex second), a pure-px variant,
the HTML4 align="absmiddle" attribute, the px-per-ex scale factor, and wide
display-formula overflow behaviour:

  A    current build (ex-only style + px attrs) — baseline
  B    dual-unit cascade: px first, ex second (the proposed new build)
  C    pure px style (no ex) — does the engine honor CSS px at all?
       (= what iReader effectively renders for row B)
  D    B + align="absmiddle" on the inline img (HTML4 legacy fallback)
  E7/8/9/10  px-per-ex factor k (attrs + px css both scale) — which row's
       formulas best match the body text at the device's default font size
  F    wide display formula (~70ex): F1 full size (B) vs F2 capped 600px

Each row shows the same inline formula inside body text plus the same
display formula. Report the letters (and for E the best k, for F which
fits/overflows).
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
# Book project dir (contains hybrid_auto/ and receives md2epub-build/):
# env MD2EPUB_PROJECT, else first CLI arg, else the current book.
PROJECT = Path(
    os.environ.get("MD2EPUB_PROJECT")
    or (sys.argv[1] if len(sys.argv) > 1 else "/Users/hengliu/Downloads/数学分析(第三版·上册)")
).expanduser()
BUILD = PROJECT / "md2epub-build"
OUT = PROJECT / "公式显示测试4.epub"
WIDE_CAP_PX = 600


def pick(cache: dict) -> tuple[str, str, str] | None:
    """Keys for (inline sample, display sample, wide display ~70ex)."""
    inline = display = wide = None
    taken: set[str] = set()
    for k, e in cache.items():
        if "svg" not in e or k in taken:
            continue
        dims = _svg_ex_dims(e["svg"])
        if not dims:
            continue
        w_ex, h_ex = dims
        if inline is None and 3 <= w_ex <= 12 and 1.2 <= h_ex <= 2.5:
            inline = k
            taken.add(k)
        elif display is None and 12 <= w_ex <= 35 and 2.5 <= h_ex <= 6:
            display = k
            taken.add(k)
        elif wide is None and w_ex >= 55:
            wide = k
            taken.add(k)
        if inline and display and wide:
            break
    if not (inline and display):
        return None
    return inline, display, wide or display


def main() -> None:
    cache = json.loads((BUILD / "math_cache.json").read_text(encoding="utf-8"))
    keys = pick(cache)
    if keys is None:
        raise SystemExit("no suitable sample formulas found in math_cache.json")
    inline_k, display_k, wide_k = keys
    dims = {k: _svg_ex_dims(cache[k]["svg"]) for k in keys}
    valign = {k: cache[k].get("valign_ex", 0) for k in keys}
    print(f"samples: inline={dims[inline_k]} display={dims[display_k]} "
          f"wide={dims[wide_k]}")

    media: dict[str, Path] = {}  # test filename -> source png

    def png(row: str, key: str) -> str:
        name = f"{row}-{key[:8]}.png"
        src = BUILD / "png_cache" / f"eq-{key}.png"
        if not src.exists():
            raise SystemExit(f"missing raster {src} — build the png epub first")
        media[name] = src
        return name

    def inline_img(row: str, kind: str, k: float = 8.0) -> str:
        key = inline_k
        w_ex, h_ex = dims[key]
        v_ex = valign[key]
        w_px, h_px, v_px = round(w_ex * k), round(h_ex * k), round(v_ex * k)
        style = {
            "A": f"height:{h_ex}ex;vertical-align:{v_ex}ex",
            "B": f"height:{h_px}px;height:{h_ex}ex;"
                 f"vertical-align:{v_px}px;vertical-align:{v_ex}ex",
            "C": f"height:{h_px}px;vertical-align:{v_px}px",
        }[kind if kind in "ABC" else "B"]
        align = ' align="absmiddle"' if kind == "D" else ""
        return (
            f'<img class="math-inline" src="../media/{png(row, key)}" '
            f'width="{w_px}" height="{h_px}"{align} '
            f'style="{style}" alt="公式"/>'
        )

    def display_img(row: str, key: str, kind: str, k: float = 8.0) -> str:
        w_ex, h_ex = dims[key]
        w_px, h_px = round(w_ex * k), round(h_ex * k)
        if kind == "cap":  # F2: capped, pure px so no engine can exceed the cap
            scale = min(1.0, WIDE_CAP_PX / w_px)
            w_px, h_px = round(w_px * scale), round(h_px * scale)
            style = f"width:{w_px}px;height:{h_px}px"
        else:
            style = {
                "A": f"width:{w_ex}ex",
                "B": f"width:{w_px}px;width:{w_ex}ex;"
                     f"height:{h_px}px;height:{h_ex}ex",
                "C": f"width:{w_px}px;height:{h_px}px",
            }[kind if kind in "ABC" else "B"]
        return (
            f'<img src="../media/{png(row, key)}" width="{w_px}" '
            f'height="{h_px}" style="{style}" alt="公式"/>'
        )

    def block(letter: str, note: str, kind: str, k: float = 8.0) -> list[str]:
        w_ex, h_ex = dims[display_k]
        return [
            f"<h2>【{letter}】{note}</h2>",
            f"<p>对照文字{inline_img(letter, kind, k)}对照文字，"
            f"公式应与文字同高、基线对齐。</p>",
            f'<div class="math-display">&#8203;\n{display_img(letter, display_k, kind, k)}\n</div>',
            f"<p><small>（上行行间公式对应 {letter}，宽 {w_ex:.1f}ex × 高 {h_ex:.1f}ex）</small></p>",
        ]

    body = [
        "<h1>公式显示测试 4（png 尺寸与对齐校准）</h1>",
        "<p>同一张位图、同一套尺寸数据，每行只变一个量。请回报每行行内公式与"
        "行间公式的表现：尺寸偏大/偏小/合适、对齐偏上/偏下/正常、是否溢出屏幕。</p>",
    ]
    body += block("A", "现状（仅 ex 单位 style + 原生属性）——正式版基线", "A")
    body += block("B", "双单位级联（px 先、ex 后）——拟采用的新正式版", "B")
    body += block("C", "纯 px（无 ex）——检验引擎是否认 CSS px", "C")
    body += block("D", "B + 行内 img 加 align=&quot;absmiddle&quot; 属性", "D")
    for k in (7, 8, 9, 10):
        body += block(f"E{k}", f"系数 k={k}（1ex≈{k}px，属性与 px 同步缩放）", "B", k)
    body += [
        "<h2>【F】宽行间公式（约 70ex）小屏溢出测试</h2>",
        f"<p><small>样本宽 {dims[wide_k][0]:.0f}ex × 高 {dims[wide_k][1]:.1f}ex</small></p>",
        '<p>F1 原尺寸（双单位）：</p>',
        '<div class="math-display">&#8203;\n{}\n</div>'.format(
            display_img("F1", wide_k, "B")
        ),
        f"<p>F2 封顶 {WIDE_CAP_PX}px（纯 px）：</p>",
        '<div class="math-display">&#8203;\n{}\n</div>'.format(
            display_img("F2", wide_k, "cap")
        ),
        "<p><small>（请回报 F1/F2 是否超出屏宽、是否被压扁或截断）</small></p>",
    ]

    xhtml = (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" '
        'lang="zh-CN" xml:lang="zh-CN">\n'
        "<head><title>公式显示测试 4</title>"
        '<link rel="stylesheet" type="text/css" href="../styles/epub.css"/>'
        "</head>\n<body>\n" + "\n".join(body) + "\n</body>\n</html>\n"
    )

    epub = BUILD / "imgtest4_epub"
    if epub.exists():
        shutil.rmtree(epub)
    for sub in ("text", "media", "styles"):
        (epub / "EPUB" / sub).mkdir(parents=True)
    (epub / "EPUB" / "text" / "test.xhtml").write_text(xhtml, encoding="utf-8")
    for name, src in media.items():
        shutil.copyfile(src, epub / "EPUB" / "media" / name)
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
        f'<item id="m{i}" href="media/{name}" media-type="image/png"/>'
        for i, name in enumerate(sorted(media), 1)
    )
    (epub / "EPUB" / "content.opf").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bid">\n'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="bid">urn:uuid:1b4d0a3e-test-4c2f-9a61-imgtest00004</dc:identifier>'
        '<dc:title>公式显示测试 4</dc:title><dc:language>zh-CN</dc:language>'
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
    shutil.rmtree(epub, ignore_errors=True)
    print(f"built {OUT} ({OUT.stat().st_size / 1024:.0f} KB, {len(media)} pngs)")


if __name__ == "__main__":
    main()
