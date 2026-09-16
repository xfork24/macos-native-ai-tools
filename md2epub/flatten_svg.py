#!/usr/bin/env python3
"""Flatten MathJax SVG output to a maximal-compatibility path-only SVG.

MathJax emits `<defs>` glyph libraries referenced by `<use xlink:href>`,
nested `<svg>` viewports for stretchy pieces, ex-unit root dims and
`fill="currentColor"` — any of which can defeat a primitive rasterizer.
The flattened output uses only the lowest common denominator of SVG:

    <svg xmlns viewBox="0 0 W H" width="W" height="H">
      <path transform="matrix(a,b,c,d,e,f)" d="…"/>
      …
    </svg>

- every `<use>` becomes the referenced glyph `<path>` with the composed
  affine transform of its ancestors
- nested `<svg>` viewports collapse into the transform chain
- coordinates are baked into px space and the viewBox normalised to
  `0 0 W H`, so engines that ignore the viewBox still see px geometry
- no defs/use/xlink/data-*/style/role/stroke — nothing but paths

Usage (module):
    from flatten_svg import flatten
    flat, w_px, h_px = flatten(svg_string)          # fill omitted (black)
    flat, w_px, h_px = flatten(svg_string, fill="currentColor")

CLI:
    python3 flatten_svg.py <cache.json> <outdir> [--fill currentColor]
        flattens every cached formula; idempotent (skips unchanged).
    python3 flatten_svg.py --selftest [project_dir]
        renders 3 representative formulas before/after with rsvg-convert
        and reports pixel differences (should be ~0). project_dir defaults
        to $MD2EPUB_PROJECT, else the current book's dir.
"""

from __future__ import annotations

import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
PX_PER_EX = 8.0

# Affine transform (a, b, c, d, e, f): x' = a*x + c*y + e, y' = b*x + d*y + f
Mat = tuple[float, float, float, float, float, float]
IDENTITY: Mat = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _mul(m1: Mat, m2: Mat) -> Mat:
    """m1 ∘ m2: apply m2 first, then m1."""
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


_TRANSFORM_RE = re.compile(
    r"(matrix|translate|scale|rotate)\s*\(([^)]*)\)"
)

_ANGLE_CACHE: dict[float, tuple[float, float]] = {}


def _cos_sin(deg: float) -> tuple[float, float]:
    import math

    key = round(deg, 6)
    if key not in _ANGLE_CACHE:
        rad = math.radians(deg)
        _ANGLE_CACHE[key] = (math.cos(rad), math.sin(rad))
    return _ANGLE_CACHE[key]


def _parse_transform(text: str) -> Mat:
    m: Mat = IDENTITY
    if not text:
        return m
    for name, raw in _TRANSFORM_RE.findall(text.strip()):
        nums = [float(n) for n in re.split(r"[\s,]+", raw.strip()) if n]
        if name == "matrix" and len(nums) == 6:
            t: Mat = tuple(nums)  # type: ignore[assignment]
        elif name == "translate":
            x = nums[0]
            y = nums[1] if len(nums) > 1 else 0.0
            t = (1.0, 0.0, 0.0, 1.0, x, y)
        elif name == "scale":
            x = nums[0]
            y = nums[1] if len(nums) > 1 else x
            t = (x, 0.0, 0.0, y, 0.0, 0.0)
        elif name == "rotate":
            ang = nums[0]
            cos, sin = _cos_sin(ang)
            if len(nums) == 3:
                cx, cy = nums[1], nums[2]
                # rotate(θ, cx, cy) = translate(cx,cy) ∘ rotate ∘ translate(-cx,-cy)
                t = _mul(
                    (1, 0, 0, 1, cx, cy),
                    _mul((cos, sin, -sin, cos, 0, 0), (1, 0, 0, 1, -cx, -cy)),
                )
            else:
                t = (cos, sin, -sin, cos, 0.0, 0.0)
        else:
            continue
        m = _mul(m, t)
    return m


def _tag(el: ET.Element) -> str:
    """Local tag name, namespace-stripped."""
    t = el.tag
    return t.split("}", 1)[1] if t.startswith("{") else t


def _attr(el: ET.Element, name: str) -> str | None:
    if name in el.attrib:
        return el.attrib[name]
    return el.attrib.get(f"{{{XLINK_NS}}}{name}")


def _floats(text: str, count: int, default: list[float]) -> list[float]:
    nums = [float(n) for n in re.split(r"[\s,]+", text.strip()) if n]
    if not nums:
        return list(default)
    return (nums + default)[:count]


def _collect_defs(root: ET.Element) -> dict[str, str]:
    glyphs: dict[str, str] = {}
    for defs in root.findall(f"{{{SVG_NS}}}defs"):
        for path in defs.findall(f"{{{SVG_NS}}}path"):
            pid = path.get("id")
            d = path.get("d")
            if pid and d:
                glyphs[pid.lstrip("#")] = d
    return glyphs


def _viewport_matrix(el: ET.Element) -> Mat:
    """Nested <svg x y width height viewBox> → its viewBox→viewport mapping."""
    x, y = _floats(el.get("x", "0"), 2, [0.0, 0.0])
    w, h = _floats(el.get("width", "0"), 2, [1.0, 1.0])
    vbx, vby, vbw, vbh = _floats(
        el.get("viewBox", "0 0 1 1"), 4, [0.0, 0.0, 1.0, 1.0]
    )
    sx = w / vbw if vbw else 1.0
    sy = h / vbh if vbh else 1.0
    return _mul(
        (1, 0, 0, 1, x, y),
        _mul((sx, 0, 0, sy, 0, 0), (1, 0, 0, 1, -vbx, -vby)),
    )


def _walk(
    el: ET.Element, m: Mat, glyphs: dict[str, str], paths: list[str]
) -> None:
    tag = _tag(el)
    if tag == "defs":
        return
    if tag == "svg":  # nested viewport
        m = _mul(m, _viewport_matrix(el))
    elif tag in ("g", "a"):
        m = _mul(m, _parse_transform(el.get("transform", "")))
    elif tag == "use":
        href = _attr(el, "href")
        d = glyphs.get((href or "").lstrip("#"))
        if d is None:
            code = (href or "").rstrip().split("-")[-1].upper()
            if code in _INVISIBLE_GLYPHS:
                return  # zero-width spacer, no geometry to emit
            raise ValueError(f"use references unknown glyph {href!r}")
        m2 = _mul(m, _parse_transform(el.get("transform", "")))
        ux, uy = _floats(el.get("x", "0"), 2, [0.0, 0.0])
        if ux or uy:
            m2 = _mul(m2, (1, 0, 0, 1, ux, uy))
        paths.append(_path_el(d, m2))
        return
    elif tag == "path":
        d = el.get("d")
        if d:
            paths.append(_path_el(d, _mul(m, _parse_transform(el.get("transform", "")))))
        return
    elif tag == "rect":
        # MathJax emits plain axis-aligned rects (\rule etc.); data-hitbox
        # ones are invisible interaction aids — drop those.
        if "data-hitbox" not in el.attrib:
            x, y = _floats(el.get("x", "0"), 2, [0.0, 0.0])
            w, h = _floats(el.get("width", "0"), 2, [0.0, 0.0])
            if w and h:
                d = f"M{_fmt(x)} {_fmt(y)}h{_fmt(w)}v{_fmt(h)}h{_fmt(-w)}z"
                paths.append(
                    _path_el(d, _mul(m, _parse_transform(el.get("transform", ""))))
                )
        return
    elif tag == "text":
        # Characters outside MathJax's fonts (CJK in \text{}) — keep as
        # <text>, composed transform positions it; system font renders it.
        m2 = _mul(m, _parse_transform(el.get("transform", "")))
        bits = [f'transform="matrix({",".join(_fmt(v) for v in m2)})"']
        for attr in ("x", "y", "font-size", "font-family", "font-style",
                     "font-weight", "text-anchor"):
            if el.get(attr):
                bits.append(f'{attr}="{el.get(attr)}"')
        text = (el.text or "").replace("&", "&amp;").replace("<", "&lt;")
        paths.append(f"<text {' '.join(bits)}>{text}</text>")
        return
    else:
        # title/desc/aria containers — recurse without a transform
        pass
    for child in el:
        _walk(child, m, glyphs, paths)


def _fmt(n: float) -> str:
    s = f"{n:.4f}".rstrip("0").rstrip(".")
    return s if s else "0"


def _path_el(d: str, m: Mat) -> str:
    a, b, c, dd, e, f = m
    return (
        f'<path transform="matrix({_fmt(a)},{_fmt(b)},{_fmt(c)},'
        f'{_fmt(dd)},{_fmt(e)},{_fmt(f)})" d="{d}"/>'
    )


def _svg_ex_dims(svg: str) -> tuple[float, float]:
    """(w_ex, h_ex) from the ROOT tag only. Raises for stretch tables
    (width="100%" without a root viewBox): their aspect lives in nested
    data-table svgs and cannot be derived statically — callers must
    fall back to a probe render / PNG raster for those."""
    root = svg[: svg.index(">")]
    m = re.search(r'width="([\d.]+)ex" height="([\d.]+)ex"', root)
    if m:
        return float(m.group(1)), float(m.group(2))
    h_m = re.search(r'height="([\d.]+)ex"', root)
    vb = re.search(r'viewBox="(-?[\d.]+) (-?[\d.]+) ([\d.]+) ([\d.]+)"', root)
    if h_m and vb:
        h = float(h_m.group(1))
        return h * float(vb.group(3)) / float(vb.group(4)), h
    raise ValueError("stretch table: no root viewBox")


# Invisible/zero-width glyphs MathJax references without defining a path
# (nbsp, function-application, zwsp, thin space, invisible operators…).
_INVISIBLE_GLYPHS = {
    "20", "A0", "2000", "2001", "2002", "2003", "2004", "2005", "2006", "2007",
    "2008", "2009", "200A", "200B", "202F", "205F", "2061", "2062", "2063",
    "2064",
}


def flatten(svg: str, fill: str | None = None) -> tuple[str, int, int]:
    """MathJax svg string → (flat svg string, w_px, h_px)."""
    root = ET.fromstring(svg)
    if _tag(root) != "svg":
        raise ValueError("root element is not <svg>")
    glyphs = _collect_defs(root)
    w_ex, h_ex = _svg_ex_dims(svg)
    h_px = max(round(h_ex * PX_PER_EX), 4)
    w_px = max(round(h_ex * PX_PER_EX * _aspect(root)), 4)

    vbx, vby, vbw, vbh = _floats(root.get("viewBox"), 4, [0, 0, w_px, h_px])
    sx = w_px / vbw if vbw else 1.0
    sy = h_px / vbh if vbh else 1.0
    # root viewBox → px space, origin normalised to (0,0)
    base = _mul((sx, 0, 0, sy, 0, 0), (1, 0, 0, 1, -vbx, -vby))

    paths: list[str] = []
    for child in root:
        _walk(child, base, glyphs, paths)

    fill_attr = f' fill="{fill}"' if fill else ""
    body = "".join(paths)
    out = (
        f'<svg xmlns="{SVG_NS}" viewBox="0 0 {w_px} {h_px}" '
        f'width="{w_px}" height="{h_px}"{fill_attr}>{body}</svg>'
    )
    return out, w_px, h_px


def _aspect(root: ET.Element) -> float:
    """viewBox width/height ratio (1.0 if absent)."""
    vb = root.get("viewBox")
    if not vb:
        return 1.0
    *_, w, h = _floats(vb, 4, [0, 0, 1, 1])
    return w / h if h else 1.0


# --------------------------------------------------------------------------
# CLI: batch-flatten the render cache; --selftest for pixel verification
# --------------------------------------------------------------------------

def _flatten_cache(cache_path: Path, outdir: Path, fill: str | None) -> None:
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    outdir.mkdir(parents=True, exist_ok=True)
    done = skipped = failed = 0
    for key, entry in cache.items():
        if "svg" not in entry:
            continue
        out = outdir / f"eq-{key}.svg"
        try:
            flat, _, _ = flatten(entry["svg"], fill=fill)
        except ValueError as exc:
            print(f"  warning: flatten failed for {key}: {exc}")
            failed += 1
            continue
        if out.exists() and out.read_text(encoding="utf-8") == flat:
            skipped += 1
            continue
        out.write_text(flat, encoding="utf-8")
        done += 1
    print(f"flattened {done}, unchanged {skipped}, failed {failed} -> {outdir}/")


def _render(svg_text: str, tmp: Path, name: str, w: int, h: int) -> Path:
    import subprocess

    src = tmp / f"{name}.svg"
    src.write_text(svg_text, encoding="utf-8")
    out = tmp / f"{name}.png"
    subprocess.run(
        ["rsvg-convert", "--width", str(w), "--height", str(h),
         "-o", str(out), str(src)],
        check=True, capture_output=True,
    )
    return out


def _selftest(cache_path: Path) -> int:
    """Flatten 3 representative formulas and diff rsvg renders pixel-wise."""
    import subprocess
    import tempfile

    from PIL import Image, ImageChops

    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    simple = nested = repeated = None
    for key, entry in cache.items():
        if "svg" not in entry:
            continue
        s = entry["svg"]
        if simple is None and s.count("<use") <= 2 and len(s) < 2500:
            simple = (key, s)
        if nested is None and s.count("<svg") >= 2 and len(s) < 6000:
            nested = (key, s)
        if repeated is None and s.count("<use") >= 4 and s.count("<svg") == 1:
            repeated = (key, s)
        if simple and nested and repeated:
            break
    cases = [
        ("simple", simple),
        ("nested-viewport", nested),
        ("repeated-glyph", repeated),
    ]
    failures = 0
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for name, case in cases:
            if case is None:
                print(f"[skip] no {name} sample found")
                continue
            key, orig = case
            flat, w_px, h_px = flatten(orig)
            try:
                a = _render(orig, tmp, f"{name}-orig", w_px, h_px)
                b = _render(flat, tmp, f"{name}-flat", w_px, h_px)
            except subprocess.CalledProcessError as exc:
                print(f"[FAIL] {name}: rsvg error {exc.stderr.decode()[:120]}")
                failures += 1
                continue
            ia = Image.open(a).convert("L")
            ib = Image.open(b).convert("L")
            if ia.size != ib.size:
                print(f"[FAIL] {name}: size {ia.size} != {ib.size}")
                failures += 1
                continue
            diff = ImageChops.difference(ia, ib)
            hist = diff.histogram()
            total = sum(hist)
            changed = total - hist[0]
            rms = (sum(i * i * n for i, n in enumerate(hist)) / total) ** 0.5
            verdict = "ok" if rms < 8.0 else "DIFFERS"
            print(
                f"[{verdict}] {name} ({key[:8]}): {w_px}x{h_px}px, "
                f"changed-pixels {changed}/{total} ({100 * changed / total:.1f}%), "
                f"rms {rms:.2f}"
            )
            if rms >= 8.0:
                failures += 1
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if args[:1] == ["--selftest"]:
        # Build artifacts live with the book, not in this repo (see convert.py).
        project = Path(
            args[1] if len(args) > 1
            else os.environ.get("MD2EPUB_PROJECT", "/Users/hengliu/Downloads/数学分析(第三版·上册)")
        ).expanduser()
        return _selftest(project / "md2epub-build" / "math_cache.json")
    if len(args) < 2:
        print(__doc__)
        return 2
    fill = None
    rest = args[2:]
    if "--fill" in rest:
        fill = rest[rest.index("--fill") + 1]
    _flatten_cache(Path(args[0]), Path(args[1]), fill)
    return 0


if __name__ == "__main__":
    sys.exit(main())
