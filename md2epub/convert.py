#!/usr/bin/env python3
"""MinerU markdown output -> EPUB3 converter.

Pipeline: preprocess md -> (pluggable math layer: MathML via pandoc, or
pre-rendered inline SVG via MathJax/node) -> pandoc epub3 -> build report.

Usage:
    python3 convert.py <hybrid_output_dir> [--math mathml|svg|both]

The pandoc process runs with cwd = <hybrid_output_dir> so that the relative
``images/...`` references in the markdown resolve.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent

DEFAULTS = {
    "md_glob": "*.md",
    "origin_pdf_glob": "*_origin.pdf",
    "colophon_enabled": True,
    "colophon_md": (
        "# 关于本电子书\n\n"
        "本书由 OCR 工具从扫描版自动转换生成，公式与文字可能存在识别错误，"
        "仅供个人学习使用。\n"
    ),
    "chapter_regex": r"^#+\s*第[一二三四五六七八九十]+章",
    "promote_h1_patterns": [
        r"^## (第[一二三四五六七八九十]+章.*)",
        r"^## (部分习题答案与提示)\s*$",
        r"^## (索引)\s*$",
    ],
    "normalize_h1_space": r"^# (第[一二三四五六七八九十]+章)(\S)",
    "keep_h2_prefix": "## §",
    "junk_h2_patterns": [
        r"^（?\(?\d+\)?\)?\s*\S",
        r"^例\s?\d",
        r"^\d+\.\s",
        r"^(目录|I索引)\s*$",
        r"^[A-Z]\s*$",
    ],
    "junk_h2_contains_html_tag": True,
    "page_label_regex": r"^\s*印刷页\s*\d+\s*$",
    "expected": {
        "h1_count": 11,
        "h2_section_count": 32,
        "display_math": 2172,
        "inline_math": 6930,
    },
}

# Display math is always a multi-line block: "$$\n<latex>\n$$".
DISPLAY_MATH_RE = re.compile(r"\$\$\n(.*?)\n\$\$", re.S)
# Inline math follows pandoc's tex_math_dollars rules: opening '$' followed by
# a non-space, closing '$' preceded by a non-space and not followed by a digit.
INLINE_MATH_RE = re.compile(r"\$(?!\s)([^$\n]+?)(?<!\s)\$(?!\d)")
# Combined splitter used by the svg layer (display branch wins at each position).
MATH_SPLIT_RE = re.compile(
    r"\$\$\n(.*?)\n\$\$|\$(?!\s)([^$\n]+?)(?<!\s)\$(?!\d)", re.S
)


class ConvertError(RuntimeError):
    """Fatal, user-facing conversion error."""


def load_config(path: Path) -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))  # deep copy
    if path.exists():
        user = json.loads(path.read_text(encoding="utf-8"))
        for key, value in user.items():
            if key == "expected" and isinstance(value, dict):
                cfg["expected"].update(value)
            else:
                cfg[key] = value
    return cfg


def find_md(hybrid_dir: Path, cfg: dict) -> Path:
    candidates = [p for p in hybrid_dir.glob(cfg["md_glob"]) if p.is_file()]
    if not candidates:
        raise ConvertError(f"no markdown file matching {cfg['md_glob']!r} in {hybrid_dir}")
    return max(candidates, key=lambda p: p.stat().st_size)


def count_math(text: str) -> tuple[int, int]:
    """Return (display_block_count, inline_span_count)."""
    display = len(DISPLAY_MATH_RE.findall(text))
    without_display = DISPLAY_MATH_RE.sub(" ", text)
    inline = len(INLINE_MATH_RE.findall(without_display))
    return display, inline


def _fix_nul_in_headers(text: str) -> str:
    fixed = []
    for line in text.split("\n"):
        if line.lstrip().startswith("#"):
            line = re.sub(r"\x00", " ", line)
            line = re.sub(r" {2,}", " ", line)
        fixed.append(line)
    return "\n".join(fixed)


def _strip_junk_subsup(text: str) -> str:
    # <sub>：</sub> style junk (678x in this book) — punctuation/whitespace-only
    # sub/sup wrappers carry no semantics. Real math subscripts (<sub>2</sub>)
    # are untouched.
    return re.sub(r"<(sub|sup)>[：:，。；、\s]+</\1>", "", text)


def _normalize_inline_math(text: str) -> str:
    # OCR sometimes leaves whitespace just inside the delimiters ("$F(x)=0 $"),
    # which pandoc's strict tex_math_dollars rules would NOT treat as math.
    # Leading/trailing whitespace is insignificant in LaTeX — strip it.
    def repl(match: re.Match) -> str:
        inner, stripped = match.group(1), match.group(1).strip()
        if stripped and stripped != inner:
            return f"${stripped}$"
        return match.group(0)

    return re.sub(r"\$([^$\n]+)\$", repl, text)


# Old-style font switches (Plain TeX) that texmath rejects, mapped to their
# LaTeX command equivalents. Applied only inside math spans.
STYLE_SWITCHES = {
    "bf": "mathbf", "it": "mathit", "sf": "mathsf", "tt": "mathtt",
    "rm": "mathrm", "cal": "mathcal", "scr": "mathscr", "em": "mathit",
}


def _repair_latex(latex: str) -> str:
    for old, new in STYLE_SWITCHES.items():
        # "\bf { ... }" / "\bf X" / "\bf \Pi" -> "\mathbf{…}"
        latex = re.sub(rf"\\{old}\s*\{{([^{{}}]*)\}}", rf"\\{new}{{\1}}", latex)
        latex = re.sub(
            rf"\\{old}\s+(\\[A-Za-z]+|[A-Za-z0-9])", rf"\\{new}{{\1}}", latex
        )
    # Commands texmath rejects but that are semantic no-ops or droppable.
    latex = re.sub(r"\\tiny\s*", "", latex)
    latex = latex.replace(r"\displaylimits", "")
    latex = re.sub(r"\\vphantom\s*\{[^{}]*\}", "", latex)
    latex = latex.replace(r"\textnormal", r"\mathrm")
    # \textbf{…} in texmath takes text, not math — map to the math versions.
    latex = latex.replace(r"\textbf", r"\mathbf")
    latex = latex.replace(r"\textit", r"\mathit")
    latex = latex.replace(r"\emph", r"\mathit")
    return latex


def _apply_to_math(text: str, fn) -> str:
    """Apply fn to the LaTeX body of every math span (display first)."""
    text = DISPLAY_MATH_RE.sub(lambda m: f"$$\n{fn(m.group(1))}\n$$", text)
    return INLINE_MATH_RE.sub(lambda m: f"${fn(m.group(1))}$", text)


def preprocess(text: str, cfg: dict) -> str:
    """Apply the ordered cleanup transforms; returns cleaned markdown."""

    # 1. NUL repair (apostrophes, empty tags, stray bytes in headers).
    text = text.replace("L\x00Hospital", "L'Hospital")
    text = re.sub(r"<(sub|sup)>\x00</\1>", "", text)
    text = _fix_nul_in_headers(text)
    # Remaining strays are intra-token OCR noise ("有2\x00个子集", "f\x00(1)") —
    # drop them outright (headers were already space-fixed above).
    text = text.replace("\x00", "")
    if "\x00" in text:
        raise ConvertError(f"{text.count(chr(0))} NUL bytes remain after repair")

    # 1b. Normalize inline-math delimiters BEFORE taking the baseline count:
    #     whitespace inside "$…$" would make pandoc treat the span as plain
    #     text, silently dropping the formula.
    text = _normalize_inline_math(text)
    # 1c. Repair LaTeX that pandoc's texmath rejects (old-style font switches
    #     and unsupported no-op commands) so it converts to MathML cleanly.
    text = _apply_to_math(text, _repair_latex)
    raw_counts = count_math(text)

    # 2. Drop everything before the first chapter header (front matter +
    #    printed TOC, both superseded by the epub title page and nav).
    chapter_re = re.compile(cfg["chapter_regex"])
    lines = text.split("\n")
    first_chapter = next(
        (i for i, line in enumerate(lines) if chapter_re.match(line.strip())), None
    )
    if first_chapter is None:
        raise ConvertError("no chapter header found; cannot locate body start")
    lines = lines[first_chapter:]

    # 3. Drop running printed-page labels ("印刷页 157" standalone lines).
    page_re = re.compile(cfg["page_label_regex"])
    lines = [line for line in lines if not page_re.match(line)]
    text = "\n".join(lines)

    # 4. Header reclassification.
    promote = [re.compile(p) for p in cfg["promote_h1_patterns"]]
    junk = [re.compile(p) for p in cfg["junk_h2_patterns"]]
    normalize_h1 = re.compile(cfg["normalize_h1_space"])
    keep_prefix = cfg["keep_h2_prefix"]
    out_lines = []
    for line in text.split("\n"):
        m1 = normalize_h1.match(line)
        if m1:  # "# 第三章函数极限…" -> "# 第三章 函数极限…" (keep the tail!)
            out_lines.append(
                f"# {m1.group(1)} {m1.group(2)}{line[m1.end():]}"
            )
            continue
        m2 = re.match(r"^## (.+)$", line)
        if not m2:
            out_lines.append(line)
            continue
        title = m2.group(1)
        promoted = None
        for pat in promote:
            m = pat.match(line)
            if m:
                promoted = "".join(g for g in m.groups() if g)
                break
        if promoted:
            out_lines.append(f"# {promoted}")
        elif line.startswith(keep_prefix):
            out_lines.append(line)  # real § section
        elif any(j.match(title) for j in junk) or (
            cfg["junk_h2_contains_html_tag"] and re.search(r"</?(sub|sup)>", title)
        ):
            out_lines.append(f"**{title}**")  # junk -> bold paragraph
        else:
            out_lines.append(f"### {title}")  # sub-topic, hidden from TOC
    text = "\n".join(out_lines)

    # 5. Junk sub/sup wrappers.
    text = _strip_junk_subsup(text)

    # 6. Postconditions — preprocessing must not touch math, must produce the
    #    expected heading skeleton, and must leave no NULs or page labels.
    if "\x00" in text:
        raise ConvertError("NUL byte survived preprocessing")
    if any(page_re.match(line) for line in text.split("\n")):
        raise ConvertError("page label survived preprocessing")
    post_counts = count_math(text)
    if post_counts != raw_counts:
        raise ConvertError(
            f"math damaged by preprocessing: {raw_counts} -> {post_counts}"
        )
    exp = cfg["expected"]
    expected_h1 = exp["h1_count"] - (1 if cfg.get("colophon_enabled") else 0)
    h1 = sum(1 for line in text.split("\n") if re.match(r"^# ", line))
    h2 = sum(1 for line in text.split("\n") if line.startswith(keep_prefix))
    if h1 != expected_h1:
        raise ConvertError(f"H1 count {h1} != expected {expected_h1}")
    if h2 != exp["h2_section_count"]:
        raise ConvertError(
            f"'{keep_prefix}' section count {h2} != expected {exp['h2_section_count']}"
        )
    print(f"  preprocess ok: H1={h1}, sections={h2}, math={post_counts}")
    return text


# --------------------------------------------------------------------------
# svg math layer
# --------------------------------------------------------------------------

def _hash(latex: str) -> str:
    return hashlib.sha1(latex.encode("utf-8")).hexdigest()


def tokenise_math(text: str) -> tuple[str, dict]:
    """Replace every formula with a placeholder token; collect render jobs."""
    if "⟂" in text:
        raise ConvertError("token character ⟂ already present in source")
    jobs: dict[str, dict] = {}

    def repl(match: re.Match) -> str:
        display = match.group(1) is not None
        latex = match.group(1) if display else match.group(2)
        key = _hash(latex)
        job = jobs.setdefault(key, {"latex": latex, "display": display})
        if job["display"] != display:
            job["display"] = display  # same latex in both roles; block wins
        kind = "MATHD" if job["display"] else "MATHI"
        return f"\n⟂{kind}:{key}⟂\n" if job["display"] else f"⟂MATHI:{key}⟂"

    return MATH_SPLIT_RE.sub(repl, text), jobs


def _png_ok(p: Path) -> bool:
    """A crashed rsvg can leave a 0-byte file that 'exists' — treat as missing."""
    try:
        if p.stat().st_size == 0:
            return False
        from PIL import Image

        with Image.open(p) as im:
            im.verify()
        return True
    except (OSError, ImportError):
        return p.exists() and p.stat().st_size > 0


def _svg_ex_dims(svg: str) -> tuple[float, float] | None:
    """(width_ex, height_ex) of a MathJax svg root, deriving width from the
    viewBox aspect when MathJax emitted width="100%"."""
    m = re.search(r'width="([\d.]+)ex" height="([\d.]+)ex"', svg)
    if m:
        return float(m.group(1)), float(m.group(2))
    h_m = re.search(r'height="([\d.]+)ex"', svg)
    vb = re.search(
        r'viewBox="(-?[\d.]+) (-?[\d.]+) ([\d.]+) ([\d.]+)"', svg
    )
    if not (h_m and vb):
        return None
    h = float(h_m.group(1))
    return h * float(vb.group(3)) / float(vb.group(4)), h


# Smart rendering-style classification (png mode only). MathJax displaystyle
# enlarges operators/fractions; textstyle compresses them. MinerU's $/$$
# classification is noisy, so flip outliers:
#   - display-classified but shallow & narrow -> inline glyphs (a lone
#     "x = y + 1" line otherwise looks bloated)
#   - inline-classified but tall -> display glyphs (\sum with limits etc.
#     otherwise renders too cramped to read)
FLIP_DISPLAY_MAX_H_EX = 2.5
FLIP_DISPLAY_MAX_W_EX = 25.0
FLIP_INLINE_MIN_H_EX = 3.0


def _decide_flips(cache: dict, jobs: dict) -> dict[str, bool]:
    """{key: new_display_flag} for formulas whose rendering style should flip.

    Uses the dims of the CURRENT rendering (entry["rendered_display"] records
    which style produced it; legacy entries match their job flag), so the
    decision is idempotent across rebuilds.
    """
    flips: dict[str, bool] = {}
    for key, job in jobs.items():
        entry = cache.get(key) or {}
        dims = _svg_ex_dims(entry["svg"]) if "svg" in entry else None
        if not dims:
            continue
        w_ex, h_ex = dims
        current = entry.get("rendered_display", job["display"])
        if current and h_ex <= FLIP_DISPLAY_MAX_H_EX and w_ex <= FLIP_DISPLAY_MAX_W_EX:
            flips[key] = False
        elif (
            not current
            and h_ex >= FLIP_INLINE_MIN_H_EX
            and r"\textstyle" not in job["latex"]  # explicit compact request
        ):
            flips[key] = True
    return flips


def render_math_via_node(
    jobs: dict, build_dir: Path, cache_path: Path, force: bool = False
) -> tuple[dict, dict]:
    """Render uncached formulas through render_math.js; persist the cache.

    force: re-render exactly these jobs even if cached (used to replace a
    formula's rendering style under the smart-classification pass).
    """
    cache: dict = {}
    if cache_path.exists():
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    todo = {
        k: v for k, v in jobs.items()
        if force or k not in cache
    }
    if todo:
        in_path = build_dir / "math.in.json"
        out_path = build_dir / "math.out.json"
        in_path.write_text(json.dumps(todo, ensure_ascii=False), encoding="utf-8")
        node = shutil.which("node")
        if node is None:
            raise ConvertError("node not found on PATH (required for svg mode)")
        subprocess.run(
            [node, str(TOOL_DIR / "render_math.js"), str(in_path), str(out_path)],
            cwd=TOOL_DIR,
            check=True,
        )
        results = json.loads(out_path.read_text(encoding="utf-8"))
        errors = results.pop("_errors", {})
        for key, err in errors.items():
            results[key] = {"error": err}
        print(f"  rendered: {len(results) - len(errors)} ok, {len(errors)} failed")
        cache.update(results)
        for key in todo:
            if key in cache:
                cache[key]["rendered_display"] = todo[key]["display"]
        cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    missing = [k for k in jobs if k not in cache]
    if missing:
        raise ConvertError(f"{len(missing)} formulas missing from render results")
    return cache, jobs


def inject_svg_math(
    xhtml: str, cache: dict, jobs: dict, png_meta: dict | None = None
) -> str:
    """Substitute tokens in a pandoc-produced XHTML fragment with math HTML.

    The SVG/PNG markup never passes through pandoc's markdown reader: nested
    <svg> roots (MathJax emits them for tables/stretchy pieces) break its
    html-block state machine. Tokens are inert text, so pandoc leaves them
    alone and we splice the real markup into its output.

    png_meta given -> <img>-referenced PNGs (readers without inline-SVG
    support, e.g. iReader); otherwise inline <svg> elements.
    """
    def _raw_fallback(latex: str) -> str:
        return f'<code class="math-raw">{html.escape(latex, quote=False)}</code>'

    def _img(key: str, display: bool) -> str | None:
        if png_meta is None or key not in png_meta:
            return None
        w_ex, h_ex = png_meta[key]
        alt = html.escape(jobs[key]["latex"], quote=True)
        src = f"../media/eq-{key}.png"
        # Native width/height attributes (1ex≈8px) are the fallback for engines
        # that ignore inline CSS (iReader displays raw intrinsic px otherwise);
        # CSS-capable readers prefer the ex-unit style and scale with font size.
        w_px, h_px = round(w_ex * 8), round(h_ex * 8)
        if display:
            return (
                f'<img src="{src}" width="{w_px}" height="{h_px}" '
                f'style="width:{w_ex}ex" alt="{alt}"/>'
            )
        valign = (cache.get(key) or {}).get("valign_ex", 0)
        return (
            f'<img class="math-inline" src="{src}" '
            f'width="{w_px}" height="{h_px}" '
            f'style="height:{h_ex}ex;vertical-align:{valign}ex" alt="{alt}"/>'
        )

    def display_block(key: str) -> str:
        img = _img(key, display=True)
        if img is not None:
            # &#8203; (zero-width space) defeats engines' "image-only block"
            # special-casing (iReader upscales such blocks to full width).
            return f'\n<div class="math-display">&#8203;\n{img}\n</div>\n'
        entry = cache.get(key) or {}
        if "svg" not in entry:
            return _raw_fallback(jobs[key]["latex"])
        svg = re.sub(r"\s+", " ", entry["svg"]).strip()
        return f'\n<div class="math-display">\n{svg}\n</div>\n'

    def inline_span(key: str) -> str:
        img = _img(key, display=False)
        if img is not None:
            return img
        entry = cache.get(key) or {}
        if "svg" not in entry:
            return _raw_fallback(jobs[key]["latex"])
        svg = re.sub(r"\s+", " ", entry["svg"]).strip()
        valign = entry.get("valign_ex", 0)
        return (
            f'<span class="math-inline" style="vertical-align:{valign}ex">{svg}</span>'
        )

    # Standalone display tokens arrive as <p>⟂MATHD:…⟂</p> — swap the whole
    # paragraph for the div. Tokens in other containers (li, blockquote) are
    # replaced in place, which keeps the div nested legally inside them.
    xhtml = re.sub(
        r"<p>\s*⟂MATHD:([0-9a-f]+)⟂\s*</p>",
        lambda m: display_block(m.group(1)),
        xhtml,
    )
    xhtml = re.sub(r"⟂MATHD:([0-9a-f]+)⟂", lambda m: display_block(m.group(1)), xhtml)
    xhtml = re.sub(r"⟂MATHI:([0-9a-f]+)⟂", lambda m: inline_span(m.group(1)), xhtml)
    if "⟂" in xhtml:
        raise ConvertError("math token survived svg injection into xhtml")
    return xhtml


def _probe_ink_aspect(svg: str, png_dir: Path, key: str) -> float:
    """Ink width/height ratio of a width="100%" stretch-table formula.

    These have no root viewBox (the width lives in a nested data-table svg
    with a degenerate viewBox) — the only reliable aspect source is an actual
    render. rsvg letterboxes (preserveAspectRatio meet), so the ink aspect is
    intrinsic and probe-width independent (verified stable to ±1%).
    """
    import subprocess
    import tempfile

    from PIL import Image

    with tempfile.TemporaryDirectory(dir=png_dir) as td:
        src = Path(td) / "probe.svg"
        out = Path(td) / "probe.png"
        src.write_text(svg, encoding="utf-8")
        subprocess.run(
            ["rsvg-convert", "--width", "800", "-o", str(out), str(src)],
            check=True, capture_output=True,
        )
        inv = Image.eval(Image.open(out).convert("L"), lambda v: 255 - v)
        x0, y0, x1, y1 = inv.getbbox() or (0, 0, 1, 1)
    return max((x1 - x0) / (y1 - y0), 0.05)


# Raster strategy (device-validated): 2x supersampling is sharp on e-ink ppi
# (test1 row D); 32-colour palette PNG compresses line art to ~1/4 of
# grayscale+alpha with no visible loss — e-ink panels physically show 16 gray
# levels. Together ~27% of the old 3xLA size.
RASTER_SCALE = 2
RASTER_COLORS = 32


def render_pngs(cache: dict, png_dir: Path) -> dict[str, tuple[float, float]]:
    """Rasterise every cached SVG formula to PNG (2x, capped 1600px wide).

    Returns {key: (width_ex, height_ex)} parsed from the SVG root — the caller
    needs the ex-dimensions to size the <img> in CSS units so formulas scale
    with the reader's font size.
    """
    from concurrent.futures import ThreadPoolExecutor

    png_dir.mkdir(parents=True, exist_ok=True)
    meta: dict[str, tuple[float, float]] = {}
    jobs: list[tuple[Path, Path, int]] = []

    for key, entry in cache.items():
        if "svg" not in entry:
            continue
        root_tag = entry["svg"][: entry["svg"].index(">")]
        m = re.search(r'width="([\d.]+)ex" height="([\d.]+)ex"', root_tag)
        if m:
            w_ex, h_ex = float(m.group(1)), float(m.group(2))
        else:
            # Stretch tables get width="100%": aspect from the ROOT viewBox,
            # or — when even that is absent — from a probe render. (The first
            # viewBox in the string belongs to a nested svg and is degenerate.)
            h_m = re.search(r'height="([\d.]+)ex"', root_tag)
            if not h_m:
                continue
            h_ex = float(h_m.group(1))
            vb = re.search(
                r'viewBox="(-?[\d.]+) (-?[\d.]+) ([\d.]+) ([\d.]+)"', root_tag
            )
            if vb:
                w_ex = h_ex * float(vb.group(3)) / float(vb.group(4))
            else:
                w_ex = h_ex * _probe_ink_aspect(entry["svg"], png_dir, key)
        meta[key] = (w_ex, h_ex)
        out = png_dir / f"eq-{key}.png"
        if out.exists() and _png_ok(out):
            continue
        svg_file = png_dir / f"eq-{key}.svg"
        svg_file.write_text(entry["svg"], encoding="utf-8")
        jobs.append(
            (svg_file, out, max(min(round(w_ex * 8 * RASTER_SCALE), 1600), 8))
        )

    def _rsvg(job: tuple[Path, Path, int]) -> None:
        svg_file, out, width_px = job
        subprocess.run(
            ["rsvg-convert", "--width", str(width_px), "-o", str(out), str(svg_file)],
            check=True, capture_output=True,
        )

    if jobs:
        with ThreadPoolExecutor(max_workers=8) as pool:
            for _ in pool.map(_rsvg, jobs):
                pass
        for svg_file, _, _ in jobs:
            svg_file.unlink()
        print(f"  rasterised {len(jobs)} formulas -> {png_dir.name}/")

    # Palette quantisation (RGBA→P32): line-art glyphs compress ~4x smaller
    # than grayscale+alpha with no visible loss on 16-level e-ink panels.
    from PIL import Image

    for _, out, _ in jobs:
        try:
            with Image.open(out) as im:
                im.convert("RGBA").quantize(
                    colors=RASTER_COLORS, method=2  # FASTOCTREE
                ).save(out, optimize=True)
        except OSError as exc:
            print(f"  warning: png optimise failed for {out.name}: {exc}")
    return meta


# --------------------------------------------------------------------------
# cover, pandoc, report
# --------------------------------------------------------------------------

def _dark_fraction(jpg: Path) -> float:
    """Fraction of pixels darker than 128 — blank scan pages score < 0.002."""
    from PIL import Image

    with Image.open(jpg) as im:
        hist = im.convert("L").histogram()
    dark = sum(hist[:128])
    return dark / sum(hist)


def make_cover(hybrid_dir: Path, build_dir: Path, cfg: dict) -> Path | None:
    pdfs = sorted(hybrid_dir.glob(cfg["origin_pdf_glob"]))
    if not pdfs:
        print("  warning: no origin pdf found — building without cover")
        return None
    pdf = pdfs[0]
    chosen: Path | None = None
    for page in (1, 2, 3):
        base = build_dir / f"cover_p{page}"
        try:
            subprocess.run(
                [
                    "pdftoppm", "-jpeg", "-jpegopt", "quality=85",
                    "-f", str(page), "-l", str(page), "-scale-to", "1600",
                    str(pdf), str(base),
                ],
                check=True,
                capture_output=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            print(f"  warning: pdftoppm failed on page {page}: {exc}")
            return None
        produced = sorted(build_dir.glob(f"cover_p{page}-*.jpg"))
        if not produced:
            return None
        candidate = produced[0]
        try:
            if _dark_fraction(candidate) < 0.002:
                print(f"  cover: page {page} looks blank, trying next")
                continue
        except ImportError:
            print("  cover: PIL unavailable — skipping blank detection")
        chosen = candidate
        break
    if chosen is None:
        chosen = sorted(build_dir.glob("cover_p1-*.jpg"))[0]
        print("  warning: all candidate cover pages look blank; using page 1")
    final = build_dir / "cover.jpg"
    shutil.copyfile(chosen, final)
    return final


def run_pandoc(
    md_path: Path, mode: str, out_epub: Path, cover: Path | None, hybrid_dir: Path
) -> None:
    cmd = [
        "pandoc", str(md_path),
        "--from", "markdown",
        "--to", "epub3",
        "--split-level=2",
        "--toc", "--toc-depth=2",
        "--metadata-file", str(TOOL_DIR / "metadata.yaml"),
        "--css", str(TOOL_DIR / "epub.css"),
        "--resource-path", ".",
        "--wrap=none",
    ]
    if cover is not None:
        cmd += ["--epub-cover-image", str(cover)]
    if mode == "mathml":
        cmd += ["--mathml"]
    cmd += ["--output", str(out_epub)]
    subprocess.run(cmd, cwd=hybrid_dir, check=True)


# --------------------------------------------------------------------------
# svg mode: chunked assembly (a 55 MB single-file pandoc run gets OOM-killed)
# --------------------------------------------------------------------------

def _split_sections(md_text: str) -> list[dict]:
    """Split at H1/H2 lines; every section becomes one output XHTML file."""
    sections, cur = [], None
    for line in md_text.split("\n"):
        m1 = re.match(r"^# (.+)$", line)
        m2 = re.match(r"^## (.+)$", line)
        if m1 or m2:
            if cur is not None:
                sections.append(cur)
            level = 1 if m1 else 2
            cur = {"level": level, "title": (m1 or m2).group(1).strip(), "lines": [line]}
        elif cur is not None:
            cur["lines"].append(line)
    if cur is not None:
        sections.append(cur)
    return sections


def _md_fragment_to_xhtml(body_md: str, title: str) -> str:
    proc = subprocess.run(
        ["pandoc", "--from", "markdown", "--to", "html", "--wrap=none"],
        input=body_md.encode("utf-8"),
        capture_output=True,
        check=True,
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" '
        'lang="zh-CN" xml:lang="zh-CN">\n'
        "<head>\n"
        f"<title>{html.escape(title)}</title>\n"
        '<link rel="stylesheet" type="text/css" href="../styles/epub.css"/>\n'
        "</head>\n"
        "<body>\n"
        f"{proc.stdout.decode('utf-8')}\n"
        "</body>\n"
        "</html>\n"
    )


def _read_metadata() -> dict:
    import yaml

    meta = yaml.safe_load((TOOL_DIR / "metadata.yaml").read_text(encoding="utf-8"))
    meta.setdefault("author", [])
    if isinstance(meta["author"], str):
        meta["author"] = [meta["author"]]
    return meta


def _nav_and_ncx(toc: list[dict], meta: dict) -> tuple[str, str]:
    """toc: [{'file','title','children':[{'file','title'}]}] in order."""
    esc = lambda s: html.escape(s, quote=True)
    nav_lis, ncx_points, play = [], [], 0
    for top in toc:
        nav_lis.append(f'<li><a href="text/{top["file"]}">{esc(top["title"])}</a>')
        if top["children"]:
            inner = "".join(
                f'<li><a href="text/{c["file"]}">{esc(c["title"])}</a></li>'
                for c in top["children"]
            )
            nav_lis.append(f"<ol>{inner}</ol>")
        nav_lis.append("</li>")
        play += 1
        ncx_points.append(
            f'<navPoint id="np{play}" playOrder="{play}">'
            f"<navLabel><text>{esc(top['title'])}</text></navLabel>"
            f'<content src="text/{top["file"]}"/></navPoint>'
        )
        for child in top["children"]:
            play += 1
            ncx_points.append(
                f'<navPoint id="np{play}" playOrder="{play}">'
                f"<navLabel><text>{esc(child['title'])}</text></navLabel>"
                f'<content src="text/{child["file"]}"/></navPoint>'
            )
    nav = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" '
        'lang="zh-CN" xml:lang="zh-CN">\n'
        "<head><title>目录</title>"
        '<link rel="stylesheet" type="text/css" href="styles/epub.css"/></head>\n'
        "<body>\n"
        '<nav epub:type="toc"><h1>目录</h1>\n'
        f"<ol>{''.join(nav_lis)}</ol>\n"
        "</nav>\n"
        '<nav epub:type="landmarks" hidden="hidden"><h1>导航</h1><ol>\n'
        f'<li><a epub:type="bodymatter" href="text/{toc[1]["file"] if len(toc) > 1 else toc[0]["file"]}">正文</a></li>\n'
        "</ol></nav>\n"
        "</body>\n</html>\n"
    )
    ncx = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
        "<head>\n"
        f'<meta name="dtb:uid" content="{esc(meta["identifier"])}"/>\n'
        "</head>\n"
        f"<docTitle><text>{esc(meta['title'])}</text></docTitle>\n"
        f"<navMap>{''.join(ncx_points)}</navMap>\n"
        "</ncx>\n"
    )
    return nav, ncx


def build_epub_chunked(
    md_text: str, out_epub: Path, cover: Path | None, hybrid_dir: Path,
    cache: dict, jobs: dict, png_meta: dict | None = None,
    png_dir: Path | None = None, css_file: Path | None = None,
) -> None:
    """svg/png-mode assembly: pandoc per H1/H2 section, package the zip ourselves.

    md_text carries math as ⟂MATHx:hash⟂ tokens; the real markup (inline SVG,
    or <img>-referenced PNGs when png_meta is given) is spliced in after
    pandoc (see inject_svg_math). png mode passes the class-scoped
    epub-png.css: bare-img CSS rules make ex-blind engines fall back to
    intrinsic bitmap size.
    """
    meta = _read_metadata()
    sections = _split_sections(md_text)
    # Transient staging in a fresh tempdir — never inside the tool repo, and
    # unique per run (no cleanup races).
    staging = Path(tempfile.mkdtemp(prefix="md2epub-stage-"))
    for sub in ("text", "media", "styles"):
        (staging / "EPUB" / sub).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(
        css_file or TOOL_DIR / "epub.css",
        staging / "EPUB" / "styles" / "epub.css",
    )

    media: dict[str, str] = {}  # original name (images/x.jpg) -> media name
    toc: list[dict] = []
    spine_files: list[str] = []
    for i, sec in enumerate(sections, 1):
        xhtml = _md_fragment_to_xhtml("\n".join(sec["lines"]), sec["title"])
        # Collect + rewrite image references into EPUB/media.
        def _img(match: re.Match) -> str:
            src = match.group(1)
            if src not in media:
                media_name = f"img{len(media):04d}{Path(src).suffix}"
                src_file = hybrid_dir / src
                if src_file.exists():
                    shutil.copyfile(src_file, staging / "EPUB" / "media" / media_name)
                    media[src] = media_name
                else:
                    print(f"  warning: image not found: {src}")
                    media[src] = None
            return f'src="../media/{media[src]}"' if media[src] else 'src=""'

        xhtml = re.sub(r'src="(images/[^"]+)"', _img, xhtml)
        # Content images: tag with figure-img (the png css scopes all img
        # rules by class) and add native px dims so dumb engines size them
        # without any CSS.
        def _augment(match: re.Match) -> str:
            src, name = match.group(1), match.group(2)
            staged = staging / "EPUB" / "media" / name
            try:
                from PIL import Image

                with Image.open(staged) as im:
                    w, h = im.size
                dims = f' width="{w}" height="{h}"'
            except (ImportError, OSError):
                dims = ""
            return f'<img class="figure-img" src="{src}"{dims} />'

        xhtml = re.sub(r'<img src="(\.\./media/(img\d+\.[^"]+))" */>', _augment, xhtml)
        xhtml = inject_svg_math(xhtml, cache, jobs, png_meta)
        if png_meta is not None and png_dir is not None:
            for name in set(
                re.findall(r'src="\.\./media/(eq-[0-9a-f]+\.png)"', xhtml)
            ):
                staged = staging / "EPUB" / "media" / name
                if not staged.exists():
                    shutil.copyfile(png_dir / name, staged)
        fname = f"ch{i:03d}.xhtml"
        (staging / "EPUB" / "text" / fname).write_text(xhtml, encoding="utf-8")
        spine_files.append(fname)
        node = {"file": fname, "title": sec["title"], "children": []}
        if sec["level"] == 1:
            toc.append(node)
        elif toc:
            toc[-1]["children"].append(node)
        else:
            toc.append(node)

    nav, ncx = _nav_and_ncx(toc, meta)
    (staging / "EPUB" / "nav.xhtml").write_text(nav, encoding="utf-8")
    (staging / "EPUB" / "toc.ncx").write_text(ncx, encoding="utf-8")

    # Visible cover page (first spine item) in addition to cover-image property.
    cover_page = None
    if cover is not None:
        cover_page = "cover.xhtml"
        (staging / "EPUB" / "text" / cover_page).write_text(
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<!DOCTYPE html>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" '
            'xmlns:epub="http://www.idpf.org/2007/ops" '
            'lang="zh-CN" xml:lang="zh-CN">\n'
            "<head><title>封面</title>"
            '<link rel="stylesheet" type="text/css" href="../styles/epub.css"/>'
            "</head>\n"
            '<body class="cover-page">'
            '<img src="../media/cover.jpg" alt="封面"/></body>\n'
            "</html>\n",
            encoding="utf-8",
        )

    (staging / "META-INF").mkdir(exist_ok=True)
    (staging / "META-INF" / "container.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
        '  <rootfiles>\n'
        '    <rootfile full-path="EPUB/content.opf" '
        'media-type="application/oebps-package+xml"/>\n'
        "  </rootfiles>\n</container>\n",
        encoding="utf-8",
    )

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" '
        'properties="nav"/>',
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
        '<item id="css" href="styles/epub.css" media-type="text/css"/>',
    ]
    if cover is not None:
        shutil.copyfile(cover, staging / "EPUB" / "media" / "cover.jpg")
        manifest.insert(
            3,
            '<item id="cover-image" href="media/cover.jpg" media-type="image/jpeg" '
            'properties="cover-image"/>',
        )
    manifest += [
        f'<item id="sec{i}" href="text/{f}" media-type="application/xhtml+xml"/>'
        for i, f in enumerate(spine_files, 1)
    ]
    extra_media = sorted(
        p for p in (staging / "EPUB" / "media").iterdir() if p.name != "cover.jpg"
    )
    media_types = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
    manifest += [
        f'<item id="m{i}" href="media/{p.name}" '
        f'media-type="{media_types.get(p.suffix, "application/octet-stream")}"/>'
        for i, p in enumerate(extra_media, 1)
    ]
    if cover_page:
        manifest.insert(
            3,
            f'<item id="cover-page" href="text/{cover_page}" '
            'media-type="application/xhtml+xml"/>',
        )
    spine = "".join(
        f'<itemref idref="sec{i}"/>' for i in range(1, len(spine_files) + 1)
    )
    if cover_page:
        spine = '<itemref idref="cover-page"/>' + spine
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        f'unique-identifier="bookid">\n'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        f'<dc:identifier id="bookid">{html.escape(meta["identifier"])}</dc:identifier>\n'
        f'<dc:title>{html.escape(meta["title"])}</dc:title>\n'
        + "".join(
            f"<dc:creator>{html.escape(a)}</dc:creator>" for a in meta["author"]
        )
        + f'<dc:language>{html.escape(meta.get("lang", "zh-CN"))}</dc:language>\n'
        f'<dc:date>{html.escape(str(meta.get("date", "")))}</dc:date>\n'
        f'<meta property="dcterms:modified">{now}</meta>\n'
        "</metadata>\n"
        f"<manifest>{''.join(manifest)}</manifest>\n"
        f'<spine toc="ncx">{spine}</spine>\n'
        "</package>\n"
    )
    (staging / "EPUB" / "content.opf").write_text(opf, encoding="utf-8")

    out_epub.parent.mkdir(parents=True, exist_ok=True)
    # Everything except mimetype (its ZipInfo pins ZIP_STORED) gets deflated —
    # SVG path data compresses ~5-8x.
    with zipfile.ZipFile(out_epub, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip")
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(staging).as_posix())
    shutil.rmtree(staging, ignore_errors=True)


# --------------------------------------------------------------------------
# failure log — every formula/warning that did not survive conversion cleanly
# --------------------------------------------------------------------------

def _occurrence_index(text: str) -> dict[str, list[dict]]:
    """latex -> [{'kind','chapter','section','idx','context'}] in doc order."""
    chapters = [
        (m.start(), m.group(1).strip())
        for m in re.finditer(r"^# (.+)$", text, re.M)
    ]
    sections = [
        (m.start(), m.group(1).strip())
        for m in re.finditer(r"^## (§.+)$", text, re.M)
    ]
    counters = {"display": 0, "inline": 0}
    occ: dict[str, list[dict]] = {}

    def locate(pos: int, seq: list[tuple[int, str]]) -> str:
        title = ""
        for start, name in seq:
            if start >= pos:
                break
            title = name
        return title

    for m in MATH_SPLIT_RE.finditer(text):
        latex = m.group(1) if m.group(1) is not None else m.group(2)
        kind = "display" if m.group(1) is not None else "inline"
        counters[kind] += 1
        window = text[max(0, m.start() - 80): m.end() + 80]
        context = re.sub(r"\$\$?[^$]*\$\$?", " ", window)
        context = re.sub(r"<[^>]+>|[#>*`⟂]", " ", context)
        context = re.sub(r"\s+", " ", context).strip()
        occ.setdefault(latex, []).append(
            {
                "kind": kind,
                "idx": counters[kind],
                "chapter": locate(m.start(), chapters),
                "section": locate(m.start(), sections),
                "context": context[:70],
            }
        )
    return occ


def _fallback_formulas(epub_path: Path) -> dict[str, int]:
    """mathml epub: pandoc's raw-TeX fallback spans, as {latex: count}."""
    counts: dict[str, int] = {}
    with zipfile.ZipFile(epub_path) as zf:
        for name in sorted(n for n in zf.namelist() if n.endswith(".xhtml")):
            body = zf.read(name).decode("utf-8", "replace")
            for inner in re.findall(
                r'<span class="math[^"]*">(.*?)</span>', body, re.S
            ):
                latex = html.unescape(inner).strip()
                latex = latex.strip("$").strip()
                latex = latex.removeprefix("\\(").removesuffix("\\)").strip()
                if latex:
                    counts[latex] = counts.get(latex, 0) + 1
    return counts


KIND_CN = {"display": "行间", "inline": "行内"}


def _format_failures(
    title: str, note: str, latices: list[str], occ: dict[str, list[dict]]
) -> list[str]:
    out = [f"\n【{title}】共 {len(latices)} 种公式 / "
           f"{sum(len(occ.get(x, [])) for x in latices)} 处出现", note]
    for i, latex in enumerate(latices, 1):
        spots = occ.get(latex, [])
        if not spots:
            out.append(f"  [{i}] LaTeX: {latex}")
            out.append("      （无法回溯到源文件位置）")
            continue
        spot = spots[0]
        where = spot["chapter"]
        if spot["section"]:
            where += f" > {spot['section']}"
        out.append(
            f"  [{i}] 位置: {where}（全书{KIND_CN[spot['kind']]}公式"
            f"第 {spot['idx']} 处）"
        )
        out.append(f"      LaTeX: {latex}")
        if spot["context"]:
            out.append(f"      上下文: …{spot['context']}…")
        if len(spots) > 1:
            extra = ", ".join(f"第{s['idx']}处" for s in spots[1:6])
            out.append(f"      另出现于: {extra}"
                       + (" 等" if len(spots) > 6 else ""))
    return out


def write_failure_log(
    log_path: Path,
    *,
    source_name: str,
    modes: list[str],
    text: str,
    mathml_epub: Path | None,
    svg_cache: dict | None,
    svg_jobs: dict | None,
    warnings: list[str],
) -> None:
    from datetime import datetime

    occ = _occurrence_index(text)
    total = sum(len(v) for v in occ.values())
    lines = [
        "=" * 72,
        "转换失败 / 异常处理清单",
        f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"源文件: {source_name}",
        f"公式总数: {total} 处（行间 {sum(1 for v in occ.values() for s in v if s['kind']=='display')}"
        f" / 行内 {sum(1 for v in occ.values() for s in v if s['kind']=='inline')}）",
        "=" * 72,
    ]

    if "mathml" in modes and mathml_epub is not None:
        counts = _fallback_formulas(mathml_epub)
        matched = [x for x in counts if x in occ]
        unmatched = [x for x in counts if x not in occ]
        lines += _format_failures(
            "mathml 模式 — texmath 无法转换的公式（OCR 乱码）",
            "  说明: pandoc 将这些公式按原始 TeX 文本嵌入"
            f"（<span class=\"math\">，共 {sum(counts.values())} 处）；"
            "成因是 LaTeX 源本身为 OCR 乱码，非转换器缺陷，修复需人工核对原书。",
            matched,
            occ,
        )
        if unmatched:
            lines.append(f"  另有 {len(unmatched)} 条无法回溯定位的回退片段:")
            for latex in unmatched[:10]:
                lines.append(f"      {latex[:100]}")

    if "svg" in modes and svg_cache is not None and svg_jobs is not None:
        failed_hashes = [k for k, v in svg_cache.items() if "svg" not in v]
        failed_latices = [svg_jobs[h]["latex"] for h in failed_hashes if h in svg_jobs]
        lines += _format_failures(
            "svg 模式 — MathJax 渲染失败的公式（OCR 乱码）",
            "  说明: 输出为 <code class=\"math-raw\"> 原始 TeX 文本；"
            "与 mathml 模式的失败高度重叠（同为 OCR 乱码）。",
            failed_latices,
            occ,
        )

    lines.append("\n【其他警告】")
    lines += [f"  - {w}" for w in warnings] or ["  （无）"]
    pct = lambda n: f"{100 * n / total:.2f}%" if total else "n/a"
    ml = sum(len(occ.get(x, [])) for x in _fallback_formulas(mathml_epub)) \
        if "mathml" in modes and mathml_epub is not None else 0
    sv = sum(len(occ.get(svg_jobs[h]["latex"], []))
             for h, v in (svg_cache or {}).items()
             if "svg" not in v and h in (svg_jobs or {}))
    lines += [
        "\n" + "-" * 72,
        f"汇总: 公式 {total} 处；mathml 回退 {ml} 处（{pct(ml)}）；"
        f"svg 回退 {sv} 处（{pct(sv)}）",
        "其余公式均在两种模式下正常渲染。",
    ]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_report(epub_path: Path, mode: str) -> dict:
    with zipfile.ZipFile(epub_path) as zf:
        names = zf.namelist()
        xhtml = [n for n in names if n.endswith(".xhtml")]
        media = [n for n in names if n.endswith((".jpg", ".jpeg", ".png", ".svg"))]
        sample = b"".join(zf.read(n) for n in xhtml)
    if mode == "mathml":
        math_count = sample.count(b"<math ")
    elif mode == "png":
        math_count = sample.count(b'src="../media/eq-')
    else:
        math_count = sample.count(b"<svg")
    return {
        "file": epub_path.name,
        "size_mb": round(epub_path.stat().st_size / 1e6, 2),
        "xhtml_files": len(xhtml),
        "media_files": len(media),
        "math_elements": math_count,
    }


# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("hybrid_dir", type=Path, help="MinerU output directory")
    parser.add_argument(
        "--math", choices=("mathml", "svg", "png", "both"), default="both",
        help="formula rendering mode (default: both = mathml + svg)",
    )
    parser.add_argument(
        "--config", type=Path, default=TOOL_DIR / "book_config.json",
        help="per-book heuristic overrides (json)",
    )
    parser.add_argument("--out-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    hybrid_dir = args.hybrid_dir.resolve()
    if not hybrid_dir.is_dir():
        print(f"error: {hybrid_dir} is not a directory", file=sys.stderr)
        return 2
    cfg = load_config(args.config)
    # Build artifacts (caches, preprocessed md, failure log) live with the
    # book — the directory containing the MinerU output — not in the tool
    # repo: they are book-specific and large.
    out_dir = args.out_dir or hybrid_dir.parent
    build_dir = hybrid_dir.parent / "md2epub-build"
    build_dir.mkdir(parents=True, exist_ok=True)

    md_path = find_md(hybrid_dir, cfg)
    print(f"source md: {md_path.name} ({md_path.stat().st_size / 1e6:.2f} MB)")
    text = preprocess(md_path.read_text(encoding="utf-8"), cfg)
    if cfg.get("colophon_enabled"):
        text = cfg["colophon_md"].rstrip("\n") + "\n\n" + text

    modes = ["mathml", "svg"] if args.math == "both" else [args.math]
    cover = make_cover(hybrid_dir, build_dir, cfg)
    warnings: list[str] = []
    if cover is None:
        warnings.append("未生成封面（找不到 origin pdf 或 pdftoppm 失败）")
    reports = []
    mathml_epub: Path | None = None
    svg_cache = svg_jobs = None
    for mode in modes:
        print(f"[{mode}] building …")
        png_meta = None
        if mode == "mathml":
            md_text = text
        else:
            tokenised, jobs = tokenise_math(text)
            print(f"  formulas: {len(jobs)} unique "
                  f"(from {sum(count_math(text))} occurrences)")
            cache, jobs = render_math_via_node(jobs, build_dir, build_dir / "math_cache.json")
            svg_cache, svg_jobs = cache, jobs
            if "$" in tokenised:
                raise ConvertError("'$' remains after tokenisation — untokenised math?")
            md_text = tokenised
            if mode == "png":
                # Content-aware rendering style: simple display formulas get
                # compact inline glyphs, complex inline ones get full display
                # glyphs (see FLIP_* constants).
                flips = _decide_flips(cache, jobs)
                if flips:
                    n_to_inline = sum(1 for v in flips.values() if not v)
                    print(
                        f"  smart style: {n_to_inline} display->inline, "
                        f"{len(flips) - n_to_inline} inline->display"
                    )
                    cache, _ = render_math_via_node(
                        {
                            k: {**jobs[k], "display": v}
                            for k, v in flips.items()
                        },
                        build_dir,
                        build_dir / "math_cache.json",
                        force=True,
                    )
                    svg_cache = cache
                    for k in flips:  # stale raster from the previous style
                        (build_dir / "png_cache" / f"eq-{k}.png").unlink(
                            missing_ok=True
                        )
                png_meta = render_pngs(cache, build_dir / "png_cache")
        md_out = build_dir / f"preprocessed.{mode}.md"
        md_out.write_text(md_text, encoding="utf-8")
        out_epub = out_dir / f"{md_path.stem}.{mode}.epub"
        if mode == "mathml":
            run_pandoc(md_out, mode, out_epub, cover, hybrid_dir)
            mathml_epub = out_epub
        else:
            build_epub_chunked(
                md_text, out_epub, cover, hybrid_dir, cache, jobs,
                png_meta=png_meta,
                png_dir=build_dir / "png_cache" if png_meta is not None else None,
                css_file=TOOL_DIR / "epub-png.css" if mode == "png" else None,
            )
        report = build_report(out_epub, mode)
        reports.append(report)
        print(f"  -> {report}")

    log_path = build_dir / "failures.log"
    write_failure_log(
        log_path,
        source_name=md_path.name,
        modes=modes,
        text=text,
        mathml_epub=mathml_epub,
        svg_cache=svg_cache,
        svg_jobs=svg_jobs,
        warnings=warnings,
    )
    print(f"failure log: {log_path}")

    print("\n=== summary ===")
    for r in reports:
        print(
            f"{r['file']}: {r['size_mb']} MB, {r['xhtml_files']} xhtml, "
            f"{r['media_files']} media, {r['math_elements']} math elements"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
