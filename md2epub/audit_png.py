#!/usr/bin/env python3
"""Device-defect audit for the png epub (problems 1 & 2 of the 2026-09 round).

Checks the failure shapes the device round reported:
  1. empty <li> — split/damaged enumerated formulas (pandoc list marker left
     without content)
  2. inline img px heights — must land on the INLINE_PX_BANDS values only
  3. display img widths — must not exceed the canvas box (360px @ 45ex*8)
  4. named passages — the five limit laws (ch004) and the property-(C)
     inequality (ch023) must be uniform standalone display imgs
  5. over-wide display bitmaps — content wider than the 1440px canvas gets
     no padding and renders aspect-shrunk (small glyphs); wide-split should
     have re-flowed everything except structurally unbreakable formulas
  6. image-only inline paragraphs — a math-inline img alone in a <p> is an
     OCR-orphaned sentence tail; aspect-stretching engines give it the
     display canvas treatment and blow it up to ~full width (device report
     "B = 0 显示太大"). The merge pass must leave zero of these

Usage: python3 audit_png.py <book.png.epub>
"""
from __future__ import annotations

import re
import sys
import zipfile
from collections import Counter

P = chr(0x27C2)  # never type the token glyph directly


def main(epub_path: str) -> int:
    zf = zipfile.ZipFile(epub_path)
    xhtml = sorted(n for n in zf.namelist() if n.endswith(".xhtml"))
    empty_li: list[str] = []
    inline_h: Counter[str] = Counter()
    display_w: Counter[str] = Counter()
    over_w: list[str] = []
    display_srcs: set[str] = set()
    inline_only_p: list[str] = []
    for name in xhtml:
        body = zf.read(name).decode("utf-8", "replace")
        for li in re.findall(r"<li>(.*?)</li>", body, re.S):
            if not re.sub(r"\s+", "", li):
                empty_li.append(name)
        for h in re.findall(
            r'<img class="math-inline"[^>]*height="(\d+)"', body
        ):
            inline_h[h] += 1
        for w in re.findall(
            r'<img class="math-display-img"[^>]*width="(\d+)"', body
        ):
            display_w[w] += 1
            if int(w) > 360:
                over_w.append(f"{name}: w={w}")
        display_srcs.update(
            re.findall(
                r'class="math-display-img" src="\.\./media/(eq-[^"/]+\.png)"',
                body,
            )
        )
        inline_only_p += [
            f"{name}: {alt[:50]}"
            for alt in re.findall(
                r"<p>\s*(<img class=\"math-inline\"[^>]*/>)\s*</p>", body
            )
            for alt in re.findall(r'alt="([^"]*)"', alt)
        ]

    print(f"xhtml files: {len(xhtml)}")
    print(f"1) empty <li>: {len(empty_li)} {sorted(set(empty_li))}")
    print(f"6) image-only inline paragraphs: {len(inline_only_p)}")
    for s in inline_only_p[:5]:
        print(f"   {s}")
    print(f"2) inline height bands (px): {dict(sorted(inline_h.items()))}")
    print(f"3) display widths >360px: {len(over_w)}; max w = "
          f"{max((int(w) for w in display_w), default=0)}px")

    # 5) over-wide display bitmaps (content wider than the 1440px canvas)
    from io import BytesIO

    from PIL import Image

    over_canvas = []
    for src in sorted(display_srcs):
        with Image.open(BytesIO(zf.read(f"EPUB/media/{src}"))) as im:
            if im.size[0] > 1440:
                over_canvas.append(f"{src}: {im.size[0]}px")
    print(f"5) display bitmaps >1440px (un-padded, glyph-shrunk): "
          f"{len(over_canvas)}")
    for s in over_canvas[:5]:
        print(f"   {s}")

    # 4) named passages
    hits = {"laws": None, "propC": None}
    for name in xhtml:
        body = zf.read(name).decode("utf-8", "replace")
        if "极限四则运算法则" in body or (
            hits["laws"] is None and "lim" in body and "法则" in body
        ):
            if re.search(r"运算法则|四则运算", body):
                hits["laws"] = name
        if "证明要用到以下不等式" in body:
            hits["propC"] = name
    for label, name in hits.items():
        if not name:
            print(f"4) {label}: passage NOT FOUND")
            continue
        body = zf.read(name).decode("utf-8", "replace")
        idx = body.find("运算法则" if label == "laws" else "以下不等式")
        window = body[idx: idx + 2600]
        alts = re.findall(r'alt="\\text\{\((?:[^)"])\)[^"]*"', window)
        has_ol = "<ol" in window
        n_disp = len(re.findall(r'math-display-img', window))
        print(f"4) {label} ({name}): {n_disp} display imgs, "
              f"marker-embedded alts={len(alts)}, residual <ol> in window="
              f"{has_ol}")
        if label == "laws" and has_ol:
            # show what the residual list actually contains
            ol = re.search(r"<ol.*?</ol>", window, re.S)
            if ol:
                items = re.findall(r"<li>(.*?)</li>", ol.group(0), re.S)
                print("   residual ol items:")
                for it in items[:6]:
                    text = re.sub(r"<[^>]+>", "", it)
                    print(f"     - {text.strip()[:60]!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
