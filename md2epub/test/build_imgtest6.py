#!/usr/bin/env python3
"""Diagnostic EPUB #6: escaping the iReader tall-display clamp.

imgtest5 device results:
  - iReader native: ALL display variants with pinned/auto size (width-only,
    pinned dual, inline-style display:block) clamp tall formula images to
    ~one text line (J at 2.7ex/22px fine, K at 8.6ex/69px clamped — the
    boundary ~28px ≈ body line-height). Content figures NEVER clamp: their
    markup is class figure-img (display:block + max-width:100% +
    height:auto FROM THE STYLESHEET) with large native attrs.
  - KOReader: every display variant fine; only inline imgs carrying a
    width attr get horizontally squeezed (width-less G3 fine).

So the clamp escape for tall display formulas is somewhere in: where the
rules come from (class vs inline style), height:auto, max-width:100%, the
line box, or a sized wrapper. Rows (one variable each):

  G3a  wide inline, no width attr — the now-official inline markup, anchor
  G5   TALL inline (~4-5ex), no width attr — does the clamp hit tall inlines
  M1   mid-height display (~5ex), current pinned markup — locate the clamp
       threshold between 2.7ex (fine) and 8.6ex (clamped)
  L1   figure-pattern A: attrs = target px + style width:{ex}ex;
       max-width:100%; height:auto
  L2   L1 inside a px-width wrapper div (exact width if div width honored)
  L3   current pinned markup + line-height:{h}px on the .math-display div
       (raises the line box the clamp seems to use)
  L4   true figure replica: class="figure-img" + 2x intrinsic attrs
  L5   current pinned markup + class-based display:block (img.mdb in the
       stylesheet — inline-style display:block failed in imgtest5)
  N1   L2 pattern on the narrow-tall sample (exact 204px wrapper)
  N2   L1 pattern on the narrow-tall sample (expects full-width stretch)

Report per row: 正常 / 压成一行 / 拉宽(全宽) / 溢出, aspect kept or broken.
"""
from __future__ import annotations

import json
import os
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
OUT = PROJECT / "公式显示测试6.epub"
K = 8  # px per ex (current build factor)
MDB_CSS = "\nimg.mdb { display: block; margin: 0 auto; }\n"


def root_dims(entry: dict) -> tuple[float, float] | None:
    """Root-tag ex dims, skipping stretch (width="100%") formulas."""
    if "svg" not in entry:
        return None
    svg = entry["svg"]
    if 'width="100%"' in svg[: svg.index(">")]:
        return None
    return _svg_ex_dims(svg)


def pick(cache: dict) -> dict[str, str]:
    """Samples: wide inline I, tall inline T, mid display M, wide-tall W,
    narrow-tall N."""
    want = {
        "I": lambda w, h: 18 <= w <= 35 and h <= 2.5,
        "T": lambda w, h: w <= 25 and 3.5 <= h <= 5.5,
        "M": lambda w, h: 12 <= w <= 35 and 5.0 <= h <= 6.5,
        "W": lambda w, h: 38 <= w <= 60 and 9 <= h <= 16,
        "N": lambda w, h: 16 <= w <= 30 and h >= 8,
    }
    found: dict[str, str] = {}
    for k, e in cache.items():
        d = root_dims(e)
        if not d:
            continue
        for name, ok in want.items():
            if name not in found and k not in found.values() and ok(*d):
                found[name] = k
    missing = [n for n in want if n not in found]
    if missing:
        raise SystemExit(f"no sample for {missing} in math_cache.json")
    return found


def main() -> None:
    cache = json.loads((BUILD / "math_cache.json").read_text(encoding="utf-8"))
    keys = pick(cache)
    dims = {n: root_dims(cache[k]) for n, k in keys.items()}
    for n in "ITMWN":
        w, h = dims[n]
        print(f"  {n}: {keys[n][:8]}  {w:.1f}ex x {h:.1f}ex")
    iva = cache[keys["I"]].get("valign_ex", 0)

    media: dict[str, Path] = {}

    def src_png(name: str, key: str) -> str:
        if not name.endswith(".png"):
            name += ".png"
        p = BUILD / "png_cache" / f"eq-{key}.png"
        if not p.exists():
            raise SystemExit(f"missing raster {p}")
        media[name] = p
        return name

    def inline_now(sample: str, row: str) -> str:
        """The post-imgtest5 official inline markup: height only, no width."""
        w_ex, h_ex = dims[sample]
        h_px, v_px = round(h_ex * K), round(iva * K)
        name = src_png(row, keys[sample])
        return (
            f'<img class="math-inline" src="../media/{name}" '
            f'height="{h_px}" '
            f'style="height:{h_px}px;height:{h_ex}ex;'
            f'vertical-align:{v_px}px;vertical-align:{iva}ex" alt="公式"/>'
        )

    def pinned(row: str, sample: str, extra_style: str = "",
               img_class: str = "") -> str:
        w_ex, h_ex = dims[sample]
        w_px, h_px = round(w_ex * K), round(h_ex * K)
        name = src_png(row, keys[sample])
        cls = f'class="{img_class}" ' if img_class else ""
        return (
            f'<img {cls}src="../media/{name}" width="{w_px}" height="{h_px}" '
            f'style="{extra_style}width:{w_px}px;width:{w_ex}ex;'
            f'height:{h_px}px;height:{h_ex}ex" alt="公式"/>'
        )

    def figpat(row: str, sample: str) -> str:
        """Figure pattern A: target-px attrs + ex width + max-width/auto."""
        w_ex, h_ex = dims[sample]
        w_px, h_px = round(w_ex * K), round(h_ex * K)
        name = src_png(row, keys[sample])
        return (
            f'<img src="../media/{name}" width="{w_px}" height="{h_px}" '
            f'style="width:{w_ex}ex;max-width:100%;height:auto" alt="公式"/>'
        )

    def fig_replica(row: str, sample: str) -> str:
        """Exactly what content figures get (2x intrinsic attrs + class)."""
        w_ex, h_ex = dims[sample]
        name = src_png(row, keys[sample])
        return (
            f'<img class="figure-img" src="../media/{name}" '
            f'width="{round(w_ex * K * 2)}" height="{round(h_ex * K * 2)}"/>'
        )

    lead = "设函数在闭区间上连续且逐段可微，则对任意给定的正数与实数列"
    trail = "，结论恒立。"
    wW, hW = dims["W"]
    wN, hN = dims["N"]
    wM, hM = dims["M"]
    wT, hT = dims["T"]

    def show(img: str, wrap_style: str = "") -> str:
        st = f' style="{wrap_style}"' if wrap_style else ""
        return f'<div class="math-display"{st}>&#8203;\n{img}\n</div>'

    body = [
        "<h1>公式显示测试 6（掌阅高公式钳制逃逸）</h1>",
        "<p>G 组行内公式看是否与文字同高、宽窄正常；M/L/N 组行间公式看是否"
        "<b>被压成一行高</b>、是否被<b>拉宽到全屏宽</b>、纵横比是否正常、"
        "宽度与正文是否相称。</p>",
        "<h2>【G3a】宽行内公式·新正式标记（无 width 属性）——锚点</h2>",
        f"<p>{lead}{inline_now('I', 'G3a')}{trail}</p>",
        f"<h2>【G5】高行内公式（{wT:.0f}ex×{hT:.1f}ex）·同上标记</h2>",
        f"<p>{lead}{inline_now('T', 'G5')}{trail}</p>",
        f"<h2>【M1】中等高行间公式（{wM:.0f}ex×{hM:.1f}ex）·当前钉宽高标记"
        "——钳制阈值定位</h2>",
        show(pinned("M1", "M")),
        f"<h2>【L1】宽高多行公式（{wW:.0f}ex×{hW:.1f}ex）·figure 模式："
        "目标px属性 + ex宽 + max-width:100% + height:auto</h2>",
        show(figpat("L1", "W")),
        "<h2>【L2】同公式·L1 外包定宽 div（宽度=目标 394px，居中）</h2>",
        show(figpat("L2", "W"), wrap_style=f"width:{round(wW * K)}px;margin:0 auto"),
        "<h2>【L3】同公式·当前标记 + 行高抬到公式高（line-height:74px）</h2>",
        show(pinned("L3", "W"), wrap_style=f"line-height:{round(hW * K)}px"),
        "<h2>【L4】同公式·完全复刻插图标记（figure-img 类 + 2×固有属性）</h2>",
        show(fig_replica("L4", "W")),
        "<h2>【L5】同公式·当前标记 + 类级 display:block（样式表 img.mdb）</h2>",
        show(pinned("L5", "W", img_class="mdb")),
        f"<h2>【N1】窄高多行公式（{wN:.0f}ex×{hN:.1f}ex）·L2 模式"
        "（定宽 204px）</h2>",
        show(figpat("N1", "N"), wrap_style=f"width:{round(wN * K)}px;margin:0 auto"),
        f"<h2>【N2】窄高多行公式·L1 模式（预期可能被拉宽）</h2>",
        show(figpat("N2", "N")),
    ]

    xhtml = (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" '
        'lang="zh-CN" xml:lang="zh-CN">\n'
        "<head><title>公式显示测试 6</title>"
        '<link rel="stylesheet" type="text/css" href="../styles/epub.css"/>'
        "</head>\n<body>\n" + "\n".join(body) + "\n</body>\n</html>\n"
    )

    stage = BUILD / "imgtest6_epub"
    if stage.exists():
        shutil.rmtree(stage)
    for sub in ("text", "media", "styles"):
        (stage / "EPUB" / sub).mkdir(parents=True)
    (stage / "EPUB" / "text" / "test.xhtml").write_text(xhtml, encoding="utf-8")
    for name, src in media.items():
        shutil.copyfile(src, stage / "EPUB" / "media" / name)
    # The shipped css + the test-only block class (L5 isolates class-based
    # display:block from everything else figure-img carries).
    css = (TOOL / "epub-png.css").read_text(encoding="utf-8") + MDB_CSS
    (stage / "EPUB" / "styles" / "epub.css").write_text(css, encoding="utf-8")
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
        '<dc:identifier id="bid">urn:uuid:1b4d0a3e-test-4c2f-9a61-imgtest00006</dc:identifier>'
        '<dc:title>公式显示测试 6</dc:title><dc:language>zh-CN</dc:language>'
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
