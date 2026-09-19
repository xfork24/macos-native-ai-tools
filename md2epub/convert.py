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
import math
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
import contextlib
import os
from markdown_regions import protect_code, restore_code, mask_code, map_prose
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent

def atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    target_mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    fd, temp = tempfile.mkstemp(prefix=path.name+".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
        os.chmod(temp, target_mode)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


DEFAULTS = {
    "md_glob": "*.md",
    "origin_pdf_glob": "*_origin.pdf",
    "colophon_enabled": True,
    "colophon_md": (
        "# 关于本电子书\n\n"
        "本书由 OCR 工具从扫描版自动转换生成，公式与文字可能存在识别错误，"
        "仅供个人学习使用。\n"
    ),
    # Per-book literal text repairs: ordered [from, to] substring replacements
    # applied before math normalization — e.g. raw "a<b 或是 a>b" in prose,
    # which pandoc parses as a pseudo-HTML tag (<b 或是 a>) and passes through
    # verbatim, producing malformed XHTML.
    "text_replacements": [],
    # PNG formula sizing: every px fallback value (native width/height attrs
    # and the px CSS declarations) = ex value * this factor. 8 ≈ 1ex of a
    # 16px body font. Per-book override for device calibration (imgtest4).
    "png_px_per_ex": 8.0,
    # Display-bitmap canvas width, calibrated in RASTER_SCALE-density px
    # (see DISPLAY_CANVAS_PX — the physical bitmap is this times
    # DISPLAY_RASTER_SCALE/RASTER_SCALE): calibrates on-device glyph size
    # on aspect-stretching engines.
    "png_display_canvas_px": 720,
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
DISPLAY_MATH_RE = re.compile(r"(?<!\\)\$\$[ \t]*\n?(.*?)\n?\$\$", re.S)
INLINE_MATH_RE = re.compile(r"(?<![\\$])\$(?![\s$])([^$\n]+?)(?<![\s\\])\$(?![\d$])")
MATH_SPLIT_RE = re.compile(DISPLAY_MATH_RE.pattern + "|" + INLINE_MATH_RE.pattern, re.S)



class ConvertError(RuntimeError):
    """Fatal, user-facing conversion error."""


def load_config(path: Path) -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))  # deep copy
    if not path.is_file():
        raise ConvertError(f"config not found: {path}")
    if path.exists():
        user = json.loads(path.read_text(encoding="utf-8"))
        if user.get("schema_version") == 2:
            cfg = {}
        for key, value in user.items():
            if key == "expected" and isinstance(value, dict) and "expected" in cfg:
                cfg["expected"].update(value)
            else:
                cfg[key] = value
    return cfg


def find_md(hybrid_dir: Path, cfg: dict) -> Path:
    candidates = [p for p in hybrid_dir.glob(cfg["md_glob"]) if p.is_file()]
    if not candidates:
        raise ConvertError(f"no markdown file matching {cfg['md_glob']!r} in {hybrid_dir}")
    if len(candidates) != 1:
        raise ConvertError("multiple markdown files; specify md_glob in config")
    return candidates[0]


def count_math(text: str) -> tuple[int, int]:
    """Return (display_block_count, inline_span_count)."""
    text = mask_code(text)
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
    # Runs of 3+ thin-space commands are OCR blank noise ("\|\cdot\|\,\,\,…"
    # once rasterised as a 3097ex-wide box); collapse to one thin space.
    latex = re.sub(r"(?:\\[,;:]\s*){3,}", r"\\, ", latex)
    # Printed margin equation numbers dragged into the formula ("\tag{1.2}"):
    # page furniture, not mathematics. MathJax answers \tag with a
    # width="100%" full-line table (no root viewBox — unsizable for
    # rasterisation, the probe renders it at internal scale and the bitmap
    # collapses to a vertical sliver), and texmath rejects \tag outright.
    latex = re.sub(r"\\tag\s*\{([^{}]*)\}", lambda m: r"\quad\text{(" + m[1] + ")}", latex)
    # Stray inline delimiters pasted inside a math body by OCR
    # ("$$\(| \sin x | …$$") — texmath rejects them outright.
    latex = re.sub(r"^\\\(\s*", "", latex)
    latex = re.sub(r"\s*\\\)$", "", latex)
    return latex


def _apply_to_math(text: str, fn) -> str:
    """Apply fn to the LaTeX body of every math span (display first)."""
    return map_prose(text, lambda t: MATH_SPLIT_RE.sub(
        lambda m: f"$$\n{fn(m.group(1))}\n$$" if m.group(1) is not None else f"${fn(m.group(2))}$", t))


def _preprocess_legacy(text: str, cfg: dict) -> str:
    """Apply the ordered cleanup transforms; returns cleaned markdown."""

    # 1. NUL repair (apostrophes, empty tags, stray bytes in headers).
    text = text.replace("L\x00Hospital", "L'Hospital")
    text = re.sub(r"<(sub|sup)>\x00</\1>", "", text)
    text = _fix_nul_in_headers(text)
    # Other OCR control-byte noise (BEL \x07, SO \x0e, etc.) crashes xmllint
    # when it lands inside a prose paragraph. The wrapper's normalize step
    # also leaves some of these through; strip them here so every book is
    # safe. Keep \t, \n, \r.
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", text)
    # Remaining strays are intra-token OCR noise ("有2\x00个子集", "f\x00(1)") —
    # drop them outright (headers were already space-fixed above).
    text = text.replace("\x00", "")
    if "\x00" in text:
        raise ConvertError(f"{text.count(chr(0))} NUL bytes remain after repair")

    # 1a. Per-book literal text repairs (see DEFAULTS["text_replacements"]).
    for old, new in cfg.get("text_replacements") or []:
        text = text.replace(old, new)

    # 1b. Normalize inline-math delimiters BEFORE taking the baseline count:
    #     whitespace inside "$…$" would make pandoc treat the span as plain
    #     text, silently dropping the formula.
    text = _normalize_inline_math(text)
    # 1c. Repair LaTeX that pandoc's texmath rejects (old-style font switches
    #     and unsupported no-op commands) so it converts to MathML cleanly.
    text = _apply_to_math(text, _repair_latex)

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
    # 2b. Optionally inject the body's opening H1 — when the part-title page
    #    exists only as an image, MinerU emits the body starting directly
    #    with that part's § sections and no header to anchor them under.
    if cfg.get("body_start_h1"):
        lines.insert(0, f"# {cfg['body_start_h1']}")

    # 3. Drop running printed-page labels ("印刷页 157" standalone lines).
    page_re = re.compile(cfg["page_label_regex"])
    lines = [line for line in lines if not page_re.match(line)]
    text = "\n".join(lines)
    # Baseline AFTER the front-matter drop: discarded matter may itself
    # contain math (e.g. a 符号说明 page), which must not trip the
    # "preprocessing must not touch math" postcondition.
    raw_counts = count_math(text)

    # 4. Header reclassification.
    promote = [re.compile(p) for p in cfg["promote_h1_patterns"]]
    junk = [re.compile(p) for p in cfg["junk_h2_patterns"]]
    normalize_h1 = re.compile(cfg["normalize_h1_space"])
    keep_prefixes = cfg.get("keep_h2_prefixes") or [cfg["keep_h2_prefix"]]
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
        elif any(line.startswith(p) for p in keep_prefixes):
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
    h2 = sum(
        1
        for line in text.split("\n")
        if any(line.startswith(p) for p in keep_prefixes)
    )
    if h1 != expected_h1:
        raise ConvertError(f"H1 count {h1} != expected {expected_h1}")
    if h2 != exp["h2_section_count"]:
        raise ConvertError(
            f"section count {h2} != expected {exp['h2_section_count']}"
        )
    print(f"  preprocess ok: H1={h1}, sections={h2}, math={post_counts}")
    return text


def preprocess(text: str, cfg: dict) -> str:
    """代码保持原样；新版配置已完成结构分析，旧配置仍走原来的分级规则。"""
    protected, saved = protect_code(text)
    if not cfg.get("prepared"):
        return restore_code(_preprocess_legacy(protected, cfg), saved)
    for old, new in cfg.get("text_replacements", []):
        protected = protected.replace(old, new)
    protected = _fix_nul_in_headers(protected).replace("\x00", "")
    # Strip OCR control-byte noise that xmllint rejects in prose.
    protected = re.sub(r"[\x00-\x08\x0b-\x1f]", "", protected)
    protected = _normalize_inline_math(protected)
    protected = _apply_to_math(protected, _repair_latex)
    # Escape pseudo-tags such as a<b; legitimate HTML remains available.
    allowed = {"table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption", "col", "colgroup",
               "sub", "sup", "div", "span", "p", "br", "hr", "img", "a", "em", "strong", "b", "i", "code", "pre"}
    def safe_tag(m):
        value = m.group(0)
        tag = re.match(r"</?([A-Za-z][A-Za-z0-9]*)", value)
        if tag and tag[1].lower() in allowed and re.match(r"</?\w+(?:\s+[\w:-]+\s*=|\s*/?>)", value):
            return value
        return html.escape(value, quote=False)
    # Formula text must not be HTML-escaped before rendering.
    pieces = []
    cursor = 0
    for m in MATH_SPLIT_RE.finditer(protected):
        pieces.append(re.sub(r"</?[A-Za-z][^>\n]*>", safe_tag, protected[cursor:m.start()]))
        pieces.append(m.group(0)); cursor = m.end()
    pieces.append(re.sub(r"</?[A-Za-z][^>\n]*>", safe_tag, protected[cursor:]))
    return restore_code(_strip_junk_subsup(''.join(pieces)), saved)


# --------------------------------------------------------------------------
# svg math layer
# --------------------------------------------------------------------------

def _hash(latex: str) -> str:
    return hashlib.sha1(latex.encode("utf-8")).hexdigest()


def tokenise_math(text: str) -> tuple[str, dict]:
    """Replace every formula with a placeholder token; collect render jobs."""
    if "⟂" in text:
        raise ConvertError("token character ⟂ already present in source")
    text, saved = protect_code(text)
    jobs: dict[str, dict] = {}

    def repl(match: re.Match) -> str:
        display = match.group(1) is not None
        latex = match.group(1) if display else match.group(2)
        key = _hash(("display:" if display else "inline:") + latex)
        job = jobs.setdefault(key, {"latex": latex, "display": display})
        if job["display"] != display:
            job["display"] = display  # same latex in both roles; block wins
        kind = "MATHD" if job["display"] else "MATHI"
        return f"\n⟂{kind}:{key}⟂\n" if job["display"] else f"⟂MATHI:{key}⟂"

    return restore_code(MATH_SPLIT_RE.sub(repl, text), saved), jobs


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

# png-mode display sizing, decided across imgtest4-7 device rounds:
#   - iReader renders an image-only paragraph ~full-width BY ASPECT RATIO:
#     absolute px attrs/styles take no part (imgtest7 — five container
#     markups at attr widths 138-408px all rendered the same "somewhat
#     large"). The ONLY on-device size lever is the bitmap's own geometry.
#   - Display bitmaps are therefore centred on a common transparent canvas
#     DISPLAY_CANVAS_PX wide: stretched to screen width, the glyphs land at
#     ~screen_w * RASTER_SCALE / canvas css px per ex — text parity to 1.3x
#     on 600-750 css px screens (up from 4-5x for narrow formulas).
#   - KOReader/full-CSS engines size by the ex style (the canvas is
#     invisible padding); a canvas-wide box at very large fonts shrink-fits
#     the column at ~0.75x — reported "偏小但可接受" in the same round.
#     Per-book calibration knob: "png_display_canvas_px".
#   - Inline markup cannot exceed iReader's ~44px line clamp whatever the
#     markup (imgtest5/6), so formulas taller than PROMOTE_INLINE_EX are
#     re-flowed onto their own display line (standard math typesetting for
#     tall formulas; the 108 book occurrences are exactly the squeezed ones).
# (Two earlier approaches are dead: px box-capping (iReader clamps it to a
# text line) and strip-splitting bitmaps (cuts tore fractions apart).)
FALLBACK_BOX_PX = (380, 44)  # inline-only px height cap
# display bitmap canvas width, in RASTER_SCALE-density px (the physical
# bitmap is DISPLAY_RASTER_SCALE/RASTER_SCALE times this — see render_pngs)
DISPLAY_CANVAS_PX = 720
PROMOTE_INLINE_EX = 4.8  # taller inline formulas go on their own line
# Inline formulas wider than a full text line can never share one: every
# wrapping engine squeezes them horizontally to fit (iReader clamps the
# line box; KOReader 压宽保高) — re-flow them like the tall ones.
PROMOTE_INLINE_W_EX = 36.0
# Wide display formulas are re-flowed into aligned multi-line LaTeX so no
# single line is width-capped down to unreadable glyphs (a 90ex chain capped
# to 420px shows its characters at under half body size).
WIDE_SPLIT_EX = 46.0
WIDE_SPLIT_LINE_EX = 44.0
# px fallback heights for inline formulas are banded, not proportional: on
# ex-blind engines a 12px neighbour next to a 23px one reads as random size
# jitter (worst inside enumerated lists — device report "同一个列表有大有小").
# Bands snap near-equal formulas to identical boxes; edges (1.8/2.65/3.5ex)
# sit in the natural gaps of this book's ink-height distribution,
# px = px_per_ex * band value. ex stays true, so full-CSS engines keep
# proportional scaling.
INLINE_PX_BANDS = ((1.8, 1.9), (2.65, 2.4), (3.5, 3.0), (float("inf"), 4.0))

# Trivial single-token formulas (x, \alpha, x_n, f') render as plain HTML
# text instead of bitmaps: image-unfriendly engines enforce a minimum inline
# image height (~one text line), which blows single glyphs up oversized and
# blurry (device report: x/y/α 过大过粗). Text is text-size everywhere.
_GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "varepsilon": "ε", "zeta": "ζ", "eta": "η", "theta": "θ", "vartheta": "ϑ",
    "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ",
    "pi": "π", "varpi": "ϖ", "rho": "ρ", "varrho": "ϱ", "sigma": "σ",
    "varsigma": "ς", "tau": "τ", "upsilon": "υ", "phi": "φ", "varphi": "φ",
    "chi": "χ", "psi": "ψ", "omega": "ω", "Gamma": "Γ", "Delta": "Δ",
    "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π", "Sigma": "Σ",
    "Upsilon": "Υ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω", "ell": "ℓ",
}
_TEX_TEXT_RE = re.compile(
    r"^(?:\\(?P<cmd>[a-zA-Z]+)|(?P<lit>[A-Za-z]))"
    r"(?P<prime>'*)"
    r"(?:(?P<op>[_^])(?P<script>\{?[A-Za-z0-9]{1,6}\}?))?$"
)


def _text_symbol_html(latex: str) -> str | None:
    """HTML for trivial single-token formulas, else None."""
    m = _TEX_TEXT_RE.match(latex.strip())
    if not m:
        return None
    ch = _GREEK.get(m["cmd"] or "") if m["cmd"] else m["lit"]
    if ch is None:
        return None
    html = f'<span class="msym">{ch}</span>'
    if m["prime"]:
        html += "<span>′</span>" * len(m["prime"])
    if m["script"]:
        tag = "sub" if m["op"] == "_" else "sup"
        html += f"<{tag}>{m['script'].strip('{}')}</{tag}>"
    return html


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


# Depth-0 relation operators, longest first (regex-alternation safe).
_RELATIONS = (
    "\\Leftrightarrow", "\\Longleftrightarrow", "\\Longrightarrow",
    "\\rightleftharpoons", "\\longrightarrow", "\\leftrightarrow",
    "\\rightarrow", "\\leqslant", "\\geqslant", "\\equiv", "\\leq",
    "\\geq", "\\approx", "\\propto", "\\sim", "\\cong", "\\doteq",
    "=", "<", ">",
)
_REL_RE = re.compile("|".join(re.escape(r) for r in _RELATIONS))
# l/r-suffixed size modifiers open/close a stretched group; the BARE forms
# (\big, \Big, …) only size the following delimiter and carry no pairing —
# counting them as openers made every closing \Big) raise the depth and
# wrecked the phantom-wrap counts (device round: stray \right. -> MathJax
# "Missing \left or extra \right").
_OPEN_DELIMS = (
    "\\left", "\\bigl", "\\Bigl", "\\biggl", "\\Biggl",
)
_CLOSE_DELIMS = ("\\right", "\\bigr", "\\Bigr", "\\biggr", "\\Biggr")


# Secondary break tokens for _break_row (see _break_cuts): standard
# typesetting breaks before binary +/- and at \quad clause separators.
_OP_BREAK_RE = re.compile(r"\+|-|\\quad")
# Term-starting commands: juxtaposed factor chains (nested integrals, sums)
# carry no operators between factors — the factor start is the break point.
_TERM_START_RE = re.compile(r"\\(?:i{1,3}nt|sum|prod|lim)")


def _depth0_relations(latex: str) -> list[int]:
    """Start offsets of relation operators at brace/\\left group depth 0."""
    cuts, depth, i, n = [], 0, 0, len(latex)
    while i < n:
        c = latex[i]
        if c == "{":
            depth += 1
            i += 1
        elif c == "}":
            depth = max(0, depth - 1)
            i += 1
        elif c == "\\":
            m = re.match(r"\\[a-zA-Z]+", latex[i:])
            if not m:  # \\, \; etc. — skip both chars
                i += 2
                continue
            name = m.group(0)
            if name in _OPEN_DELIMS:
                depth += 1
            elif name in _CLOSE_DELIMS:
                depth = max(0, depth - 1)
            elif depth == 0 and _REL_RE.match(latex, i):
                cuts.append(i)  # command-form relation (\\leq, \\Rightarrow…)
            i += len(m.group(0))
        elif depth == 0:
            m = _REL_RE.match(latex, i)
            if m:
                cuts.append(i)
                i += len(m.group(0))
            else:
                i += 1
        else:
            i += 1
    return cuts


# Row-structured display latex: source \begin{array}{r l} chains ("A \\ & = B"),
# our own earlier aligned re-flows, bare multi-line bodies. Other or nested
# environments (cases/matrix — absent in this corpus) are left untouched.
_ROW_ENV_RE = re.compile(
    r"^\\begin\{(array|aligned)\}\s*(?:\{[^{}]*\})?\s*(.*?)\s*\\end\{\1\}$",
    re.S,
)


def _latex_rows(latex: str) -> list[str] | None:
    """Row contents ('&' dropped, whitespace squeezed); None for latex we
    do not re-flow (other/nested environments)."""
    text = latex.strip()
    m = _ROW_ENV_RE.match(text)
    if m:
        body = m.group(2)
        if "\\begin{" in body:
            return None  # nested environment inside — leave the whole formula
    elif "\\begin{" not in text:
        body = text
    else:
        return None
    rows = []
    for r in re.split(r"\\\\|\n", body):
        r = re.sub(r"\s+", " ", r.replace("&", " ")).strip()
        if r:
            rows.append(r)
    return rows or None


def _break_cuts(row: str) -> list[tuple[int, int]]:
    """(offset, wrap) break candidates for _break_row.

    Depth-0 relations (wrap 0) plus secondary tokens — +/-, \\quad,
    term-starting \\int/\\sum/\\lim — at BRACE depth 0. Secondary cuts are
    allowed inside open \\left-class delimiters; wrap records the delimiter
    nesting there, and the split re-issues phantom pairs (\\right. closes
    the upper line, \\left. opens the continuation) — the same idiom the
    book's own print uses for bracketed expansions broken across lines.
    """
    cuts: list[tuple[int, int]] = []
    brace = delim = 0
    i, n = 0, len(row)
    while i < n:
        c = row[i]
        if c == "{":
            brace += 1
            i += 1
        elif c == "}":
            brace = max(0, brace - 1)
            i += 1
        elif c == "\\":
            m = re.match(r"\\[a-zA-Z]+", row[i:])
            if not m:  # \\, \; etc. — skip both chars
                i += 2
                continue
            name = m.group(0)
            if name in _OPEN_DELIMS:
                delim += 1
            elif name in _CLOSE_DELIMS:
                delim = max(0, delim - 1)
            elif brace == 0 and (
                _REL_RE.match(row, i)
                or _OP_BREAK_RE.match(row, i)
                or _TERM_START_RE.match(row, i)
            ):
                cuts.append((i, delim))
            i += len(m.group(0))
        elif brace == 0:
            m = _REL_RE.match(row, i)
            if m:
                cuts.append((i, delim))
                i += len(m.group(0))
            elif _OP_BREAK_RE.match(row, i):
                cuts.append((i, delim))
                i += 1
            else:
                i += 1
        else:
            i += 1
    return cuts


def _break_row(row: str, w_ex: float) -> list[str]:
    """Break one over-wide row before depth-0 relations or secondary break
    tokens (continuation pieces keep their leading operator; cuts inside
    \\left…\\right spans get phantom-pair wrapping — see _break_cuts).
    Unsplittable rows come back whole."""
    if "\\begin{" in row:
        return [row]
    n_lines = max(2, math.ceil(w_ex / (WIDE_SPLIT_LINE_EX - 2.0)))
    cands = _break_cuts(row)
    if len(cands) < n_lines - 1:
        return [row]
    chosen: list[tuple[int, int]] = []
    for t in range(1, n_lines):
        target = len(row) * t / n_lines
        floor = chosen[-1][0] if chosen else 0
        avail = [c for c in cands if c[0] > floor]
        if not avail:
            return [row]
        chosen.append(min(avail, key=lambda c: abs(c[0] - target)))
    # A boundary flanked by break tokens on one side yields a segment with
    # no content — deactivate that boundary (merge across it) rather than
    # emit a junk aligned row. Boundary i sits between segments i and i+1.
    seg_texts = [row[0:chosen[0][0]]] if chosen else [row]
    for k in range(1, len(chosen)):
        seg_texts.append(row[chosen[k - 1][0]:chosen[k][0]])
    if chosen:
        seg_texts.append(row[chosen[-1][0]:])
    active = [True] * len(chosen)
    for i, seg in enumerate(seg_texts):
        if seg.strip() and not re.fullmatch(r"(?:\+|-|\\quad|\s)*", seg):
            continue
        if i > 0:
            active[i - 1] = False
        elif len(chosen) > 0:
            active[0] = False
    bounds = [c for k, c in enumerate(chosen) if active[k]]
    parts: list[str] = []
    prev = 0
    for pos, wrap in bounds:
        parts.append(row[prev:pos].strip())
        prev = pos
    parts.append(row[prev:].strip())
    out: list[str] = []
    for k, part in enumerate(parts):
        if not part or re.fullmatch(r"(?:\+|-|\\quad|\s)*", part):
            continue  # boundary deactivation left it empty
        if k > 0 and k - 1 < len(bounds):
            part = "\\left." * bounds[k - 1][1] + part
        if k < len(bounds):
            part = part + "\\right." * bounds[k][1]
        out.append(part)
    return out


def _align_line(line: str) -> str:
    """Put the alignment & before the line's first depth-0 relation."""
    cuts = _depth0_relations(line)
    if not cuts:
        return line
    c = cuts[0]
    head, tail = line[:c].rstrip(), line[c:].lstrip()
    return f"{head} & {tail}" if head else f"& {tail}"


def _split_wide_latex(latex: str, w_ex: float) -> str | None:
    """Re-flow an over-wide display equation into an aligned block whose
    lines each fit WIDE_SPLIT_LINE_EX.

    Row-structured latex (see _ROW_ENV_RE) is handled row by row: each row
    is measured proportionally to the longest one and broken before
    depth-0 relations exactly like a plain line. Returns None when the
    formula offers no usable re-flow.
    """
    rows = _latex_rows(latex)
    if rows is None:
        return None
    longest = max(len(r) for r in rows)
    lines: list[str] = []
    for row in rows:
        row_w = w_ex * len(row) / longest
        lines += _break_row(row, row_w) if row_w > WIDE_SPLIT_LINE_EX else [row]
    if len(lines) == 1:
        return None  # single row that refused to break
    # Safety net: a cutter bug that unbalances \left/\right pairs would turn
    # the formula into a raw-TeX fallback (worse than a shrunk bitmap) —
    # refuse the re-flow instead. Cross-row source phantoms (\right. on one
    # line, \left. on the next) still balance over the whole block.
    n_open = len(re.findall(r"\\(?:left|bigl|Bigl|biggl|Biggl)\b", " ".join(lines)))
    n_close = len(re.findall(r"\\(?:right|bigr|Bigr|biggr|Biggr)\b", " ".join(lines)))
    if n_open != n_close:
        return None
    row_sep = " \\\\" + "\n"  # LaTeX row break (" \\ "), then newline
    return (
        "\\begin{aligned}\n" + row_sep.join(_align_line(ln) for ln in lines)
        + "\n\\end{aligned}"
    )


def _split_wide_formulas(
    md_text: str, jobs: dict, cache: dict, build_dir: Path,
) -> tuple[str, dict, dict]:
    """Wrap over-wide display formulas (see _split_wide_latex).

    Runs after the first render + style flips: only formulas still wider
    than WIDE_SPLIT_EX at that point are re-flowed, re-rendered under a new
    key (hash of the aligned LaTeX — deterministic, so reruns are idempotent)
    and the tokenised markdown is re-pointed at the new key. The loop runs
    twice: each round measures the ACTUAL render, so a char-proportional
    row estimate that left a line over-wide gets a second, better-informed
    cut. Unsplittable formulas keep the aspect-shrunk box fallback.
    """
    total = 0
    for _round in range(2):
        todo: dict[str, dict] = {}
        for key, job in list(jobs.items()):
            if "text_html" in job or not job["display"]:
                continue
            entry = cache.get(key) or {}
            dims = _svg_ex_dims(entry["svg"]) if "svg" in entry else None
            if not dims or dims[0] <= WIDE_SPLIT_EX:
                continue
            new_latex = _split_wide_latex(job["latex"], dims[0])
            if not new_latex or new_latex == job["latex"]:
                continue
            new_key = _hash(new_latex)
            md_text = md_text.replace(
                f"⟂MATHD:{key}⟂", f"⟂MATHD:{new_key}⟂"
            )
            todo[new_key] = {"latex": new_latex, "display": True}
            del jobs[key]
        if not todo:
            break
        total += len(todo)
        cache, _ = render_math_via_node(
            todo, build_dir, build_dir / "math_cache.json"
        )
        jobs.update(todo)
    if total:
        print(f"  wide-split: re-flowed {total} long equations into aligned lines")
    return md_text, jobs, cache


# Formula-only numbered lines — "(1) $…$;", "(ii) $…$", "3. $…$" — are
# enumerated DISPLAY equations in print. Left as markdown they become pandoc
# <ol> items whose <p> may or may not be image-only (where the trailing
# punctuation landed decides), so weak engines render neighbouring items at
# unrelated sizes: iReader stretches an image-only li paragraph ~full-width
# by bitmap aspect but pins an image next to a text node to its px band, and
# tall members get promoted into full-width displays that split the list
# (empty <li>). Re-flow each onto its own display line with the marker
# embedded (\text{(1)}\quad …): every member takes the same canvas sizing,
# the book's own numbering is kept out of pandoc's renumbering, and the list
# cannot split. Markers are canonicalised to ASCII "(N)" so \text{} never
# needs a CJK glyph.
_MARKER_MATH_LINE_RE = re.compile(
    r"^\s*(?P<mark>[（(]\s*[0-9a-zA-Z]+\s*[)）]|[0-9a-zA-Z]+[.、．)])\s*"
    r"⟂MATH[DI]:(?P<key>[0-9a-f]+)⟂\s*(?P<tail>[。．，,；;、：:）)】\].]*)\s*$"
)
# OCR sometimes splits an equation number from its formula across lines.
# The marker may precede the formula ("(C)", blank, token) or follow it
# (token, blank, "(2)").  A bare marker is parsed by pandoc as an EMPTY list
# item, so merge either ordering first and let the marker+formula re-flow
# below consume it as one unit.
_BARE_MARKER_RE = re.compile(
    r"^\s*(?P<mark>[（(]\s*[0-9a-zA-Z]+\s*[)）]|[0-9a-zA-Z]+[.、．)])\s*$"
)
_LONE_TOKEN_RE = re.compile(
    r"^\s*⟂MATH[DI]:([0-9a-f]+)⟂\s*(?P<tail>[。．，,；;、：:）)】\].]*)\s*$"
)

_FW_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")

# An inline formula orphaned as its own paragraph (OCR split a sentence's
# tail onto its own line — "$B = 0.$" after list item "(v) … A = \\infty$")
# becomes an IMAGE-ONLY <p>: aspect-stretching engines give it the display
# canvas treatment and blow a 2ex fragment up to ~full width (device report
# "B = 0 显示太大"). In print it is the tail of the previous line — merge it
# back there. Only inline tokens (a lone display token is the normal display
# case) whose previous line carries real text (not heading/table/quote, not
# another bare token) are merged.
_LONE_INLINE_LINE_RE = re.compile(r"^\s*⟂MATHI:[0-9a-f]+⟂\s*$")


def _merge_orphan_inline_lines(md_text: str) -> tuple[str, int]:
    """See _LONE_INLINE_LINE_RE. Returns (new md, number of merged lines)."""
    out: list[str] = []
    n = 0
    for line in md_text.split("\n"):
        if _LONE_INLINE_LINE_RE.match(line):
            j = len(out) - 1
            while j >= 0 and not out[j].strip():
                j -= 1
            if (
                j >= 0
                and not out[j].lstrip().startswith(("#", "|", ">"))
                and re.search(r"[\u4e00-\u9fffA-Za-z]", out[j])
            ):
                out[j] = out[j].rstrip() + " " + line.strip()
                del out[j + 1:]
                n += 1
                continue
        out.append(line)
    return "\n".join(out), n



# Lines that pandoc turns into list/heading/table/quote blocks — an inline
# formula token on one of them can never be re-flowed onto its own line
# (that would split the block). Covers the "(1)", "（2）", "a)", "3、" marker
# forms pandoc accepts as list markers, not just "1."/"-".
_LIST_LINE_RE = re.compile(
    r"\s*(?:[-*+•]|\(\d+\)|\([a-zA-Z]\)|（\d+）|(?:\d+|[a-zA-Z]{1,4})[.)、．])\s"
)


def _canonical_marker(mark: str) -> str | None:
    m = re.fullmatch(
        r"[（(]\s*([0-9a-zA-Z]+)\s*[)）]|([0-9a-zA-Z]+)[.、．)]", mark
    )
    inner = (m.group(1) or m.group(2)).translate(_FW_DIGITS) if m else ""
    return f"({inner})" if inner.isascii() and inner else None


def _reflow_numbered_math_lines(
    md_text: str, jobs: dict
) -> tuple[str, dict, list[str]]:
    """See _MARKER_MATH_LINE_RE. Returns (new md, jobs, new display keys)."""
    lines = md_text.split("\n")
    pre: list[str] = []
    i = 0
    while i < len(lines):
        mm = _BARE_MARKER_RE.match(lines[i])
        if mm:
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and _LONE_TOKEN_RE.match(lines[j]):
                pre.append(f"{mm['mark']} {lines[j].strip()}")
                i = j + 1
                continue
            # The printed equation number can also be OCR'd after its
            # formula.  Look past intervening blank lines already emitted,
            # but only consume a lone math token: figure captions such as
            # ``![](diagram.jpg)`` followed by ``(1)`` must stay untouched.
            previous = len(pre) - 1
            while previous >= 0 and not pre[previous].strip():
                previous -= 1
            if previous >= 0 and _LONE_TOKEN_RE.match(pre[previous]):
                token = pre[previous].strip()
                del pre[previous:]
                pre.append(f"{mm['mark']} {token}")
                i += 1
                continue
        pre.append(lines[i])
        i += 1
    md_text = "\n".join(pre)
    out: list[str] = []
    new_keys: list[str] = []
    for line in md_text.split("\n"):
        m = _MARKER_MATH_LINE_RE.match(line)
        if not m or m["key"] not in jobs or "text_html" in jobs[m["key"]]:
            out.append(line)
            continue
        canon = _canonical_marker(m["mark"])
        if canon is None:
            out.append(line)
            continue
        latex = jobs[m["key"]]["latex"]
        new_latex = f"\\text{{{canon}}}\\quad {latex}"
        new_key = _hash(new_latex)
        jobs[new_key] = {"latex": new_latex, "display": True}
        new_keys.append(new_key)
        out.append("")
        out.append(f"⟂MATHD:{new_key}⟂{m['tail']}")
        out.append("")
    if new_keys:
        print(
            f"  numbered display: re-flowed {len(new_keys)} marker+formula "
            "lines onto their own display line"
        )
    return "\n".join(out), jobs, new_keys


def render_math_via_node(
    jobs: dict, build_dir: Path, cache_path: Path, force: bool = False
) -> tuple[dict, dict]:
    """Render formulas through MathJax with independent style variants.

    A formula used in SVG mode with its source role and then flipped by the
    PNG heuristics must retain both renderings.  The persistent variant key
    includes the TeX and actual display flag; the ordinary job key is only
    an in-memory/current-run alias.  ``force`` remains API-compatible, but
    an exact cached variant is already authoritative and is reused.
    """
    cache: dict = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            print("  warning: unreadable formula cache; regenerating")
    todo = {}
    cache_changed = False
    variants = {}
    for key, job in jobs.items():
        signature = _hash(
            "v4:" + str(job["display"]) + ":" + job["latex"]
        )
        variant_key = f"{key}@{signature}"
        variants[key] = (variant_key, signature)
        if variant_key in cache:
            cache[key] = dict(cache[variant_key])
            continue
        legacy_signature = _hash(
            "v3:" + str(job["display"]) + job["latex"]
        )
        if (
            key in cache
            and cache[key].get("signature") == legacy_signature
        ):
            migrated = dict(cache[key])
            migrated["signature"] = signature
            cache[variant_key] = migrated
            cache[key] = dict(migrated)
            cache_changed = True
            continue
        todo[key] = job
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
        for key, entry in results.items():
            entry["rendered_display"] = todo[key]["display"]
            variant_key, signature = variants[key]
            entry["signature"] = signature
            cache[variant_key] = dict(entry)
            cache[key] = dict(entry)
        atomic_text(cache_path, json.dumps(cache, ensure_ascii=False))
    elif cache_changed:
        atomic_text(cache_path, json.dumps(cache, ensure_ascii=False))
    missing = [k for k in jobs if k not in cache]
    if missing:
        raise ConvertError(f"{len(missing)} formulas missing from render results")
    return cache, jobs


def inject_svg_math(
    xhtml: str, cache: dict, jobs: dict, png_meta: dict | None = None,
    px_per_ex: float = 8.0, media_dir: Path | None = None,
    canvas_px: int = DISPLAY_CANVAS_PX,
) -> str:
    """Substitute tokens in a pandoc-produced XHTML fragment with math HTML.

    The math markup never passes through pandoc's markdown reader: nested
    <svg> roots (MathJax emits them for tables/stretchy pieces) break its
    html-block state machine. Tokens are inert text, so pandoc leaves them
    alone and we splice the real markup into its output.

    Every formula becomes an <img> referencing a file in EPUB/media/ —
    PNG bitmaps when png_meta is given, external .svg files otherwise
    (media_dir set). File references, never inline embedding: format
    converters (Kindle azw3 via calibre etc.) drop inline <svg> but pass
    referenced media items through.
    """
    def _raw_fallback(latex: str) -> str:
        return f'<code class="math-raw">{html.escape(latex, quote=False)}</code>'

    def _img(key: str, display: bool) -> str | None:
        if png_meta is None or key not in png_meta:
            return None
        alt = html.escape(jobs[key]["latex"], quote=True)
        box_h = FALLBACK_BOX_PX[1]
        if display:
            # Canvas-box markup (see DISPLAY_CANVAS_PX): the bitmap carries
            # transparent side padding up to the canvas width (render_pngs),
            # so the img box must match the PADDED bitmap's aspect to avoid
            # distortion on attr/px-honoring engines — width = max(canvas,
            # content), height = content height. On iReader (which renders
            # image paragraphs ~full-width by bitmap aspect) the canvas
            # itself calibrates glyph size. NO max-width/height:auto —
            # iReader treats max-width:100% as width:100% (stretch).
            _, w_ex, h_ex = png_meta[key][0]
            canvas_ex = canvas_px / (px_per_ex * RASTER_SCALE)
            if w_ex > canvas_ex:
                # Over-wide content the re-flow pass could not split
                # (multi-row \begin{array} chains etc.): uniformly shrink
                # the box to the canvas width, aspect preserved. Both dims
                # pinned on a box wider than the column makes KOReader clamp
                # the width and keep the height — horizontal squash.
                h_ex = h_ex * canvas_ex / w_ex
                w_ex = canvas_ex
            w_box = max(canvas_ex, w_ex)
            w_attr, h_attr = round(w_box * px_per_ex), round(h_ex * px_per_ex)
            return (
                f'<img class="math-display-img" src="../media/eq-{key}.png" '
                f'width="{w_attr}" height="{h_attr}" '
                f'style="width:{w_attr}px;width:{w_box:g}ex;'
                f'height:{h_attr}px;height:{h_ex:g}ex" alt="{alt}"/>'
            )
        _, w_ex, h_ex = png_meta[key][0]
        valign = (cache.get(key) or {}).get("valign_ex", 0)
        # px fallback banded into a few sizes (see INLINE_PX_BANDS): ex-blind
        # engines showed proportional px as size jitter between neighbouring
        # formulas. The bitmap scales by band/h_ex, so the baseline offset
        # (valign) scales by the same factor or the glyphs would sit off the
        # text baseline. No width attribute (device-verified, imgtest5/6):
        # engines that must fit an inline image into the remaining line width
        # squeeze the width and keep the height when both attrs are pinned
        # (KOReader), breaking the aspect — with height alone every engine
        # derives width from the bitmap ratio, so the aspect can never break.
        band_ex = next(b for lim, b in INLINE_PX_BANDS if h_ex < lim)
        h_px = round(min(band_ex * px_per_ex, box_h))
        v_px = round(valign * px_per_ex * band_ex / h_ex)
        return (
            f'<img class="math-inline" src="../media/eq-{key}.png" '
            f'height="{h_px}" '
            f'style="height:{h_px}px;height:{h_ex}ex;'
            f'vertical-align:{v_px}px;vertical-align:{valign}ex" alt="{alt}"/>'
        )

    def _svg_ref(key: str, entry: dict, display: bool) -> str | None:
        """External .svg file reference (svg mode). None -> caller falls
        back to inline splicing (no media dir) or the raw-TeX fallback."""
        if media_dir is None:
            return None
        dims = _svg_ex_dims(entry["svg"])
        if not dims:
            return None
        w_ex, h_ex = dims
        f = media_dir / f"eq-{key}.svg"
        if not f.exists():
            f.write_text(entry["svg"], encoding="utf-8")
        alt = html.escape(jobs[key]["latex"], quote=True)
        if display:
            canvas_ex = canvas_px / (px_per_ex * RASTER_SCALE)
            if w_ex > canvas_ex:  # un-splittable over-wide: aspect-true shrink
                h_ex = h_ex * canvas_ex / w_ex
                w_ex = canvas_ex
            w_attr, h_attr = round(w_ex * px_per_ex), round(h_ex * px_per_ex)
            return (
                f'<img class="math-display-img" src="../media/eq-{key}.svg" '
                f'width="{w_attr}" height="{h_attr}" '
                f'style="width:{w_attr}px;width:{w_ex:g}ex;'
                f'height:{h_attr}px;height:{h_ex:g}ex" alt="{alt}"/>'
            )
        valign = entry.get("valign_ex", 0)
        h_px, v_px = round(h_ex * px_per_ex), round(valign * px_per_ex)
        return (
            f'<img class="math-inline" src="../media/eq-{key}.svg" '
            f'height="{h_px}" '
            f'style="height:{h_px}px;height:{h_ex:g}ex;'
            f'vertical-align:{v_px}px;vertical-align:{valign:g}ex" alt="{alt}"/>'
        )

    def display_block(key: str, tail: str = "") -> str:
        text_html = jobs[key].get("text_html")
        if text_html:
            return f'\n<div class="math-display">&#8203;\n{text_html}{tail}\n</div>\n'
        img = _img(key, display=True)
        if img is None and png_meta is None:
            entry = cache.get(key) or {}
            if "svg" in entry:
                img = _svg_ref(key, entry, display=True)
        if img is not None:
            # Bare <p> around the block img — the exact container shape of
            # the book's content figures (device-validated full-height
            # rendering). No ZWSP and NO trailing text node: any text in the
            # paragraph opts it out of the image-only sizing path. Punctuation
            # carried over from the promotion re-flow gets its own centred p.
            if tail:
                return f'\n<p>{img}</p>\n<p class="math-tail">{tail}</p>\n'
            return f'\n<p>{img}</p>\n'
        return _raw_fallback(jobs[key]["latex"])

    def inline_span(key: str) -> str:
        text_html = jobs[key].get("text_html")
        if text_html:
            return text_html
        img = _img(key, display=False)
        if img is None and png_meta is None:
            entry = cache.get(key) or {}
            if "svg" in entry:
                img = _svg_ref(key, entry, display=False)
        if img is not None:
            return img
        return _raw_fallback(jobs[key]["latex"])

    # Standalone display tokens arrive as <p>⟂MATHD:…⟂</p> — swap the whole
    # paragraph for the block. Tokens in other containers (li, blockquote)
    # are replaced in place, which keeps the block nested legally inside
    # them. The tail group carries punctuation that promotion re-flow kept
    # on the formula line.
    xhtml = re.sub(
        r"<p>\s*⟂MATHD:([0-9a-f]+)⟂([^<]*)</p>",
        lambda m: display_block(m.group(1), m.group(2)),
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
# for INLINE bitmaps (test1 row D — they display at their px band); 32-colour
# palette PNG compresses line art to ~1/4 of grayscale+alpha with no visible
# loss — e-ink panels physically show 16 gray levels.
RASTER_SCALE = 2
RASTER_COLORS = 32
# DISPLAY bitmaps are a different story: the canvas scheme stretches them to
# ~full screen width on aspect-sizing engines (iReader) and to the ex box on
# full-CSS engines — a 720-px canvas across a 1404-1872 device-px panel
# upsamples ~2x and looks blurry (device report "行间公式模糊"). Display
# formulas rasterise at double density instead: the canvas bitmap doubles to
# canvas_px*(DISPLAY_RASTER_SCALE/RASTER_SCALE) px, the CSS geometry is
# untouched (canvas_ex is density-invariant — see inject_svg_math), so the
# iReader glyph calibration and every markup size stay exactly as calibrated.
DISPLAY_RASTER_SCALE = 4
DISPLAY_RASTER_CAP = 3200  # px cap for display content (1600 for inline)


def render_pngs(
    cache: dict, png_dir: Path, skip: set[str] | None = None,
    display_keys: set[str] | None = None, px_per_ex: float = 8.0,
    canvas_px: int = DISPLAY_CANVAS_PX,
) -> dict[str, list[tuple[str, float, float]]]:
    """从不可变原始位图派生画布；重复执行不会裁剪、重采样或重复应用透明度。"""
    from PIL import Image
    from io import BytesIO
    from concurrent.futures import ThreadPoolExecutor
    png_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = png_dir / "raw-v3"
    raw_dir.mkdir(exist_ok=True)
    meta, tasks = {}, []
    for key, entry in cache.items():
        if "svg" not in entry or key in (skip or set()):
            continue
        dims = _svg_ex_dims(entry["svg"])
        if dims is None:
            root = entry["svg"].split(">", 1)[0]
            hm = re.search(r'height="([\d.]+)ex"', root)
            if not hm:
                continue
            h_ex = float(hm[1])
            dims = (h_ex * _probe_ink_aspect(entry["svg"], png_dir, key), h_ex)
        w_ex, h_ex = dims
        if not all(math.isfinite(v) and v > 0 for v in dims):
            continue
        display = key in (display_keys or set())
        scale = DISPLAY_RASTER_SCALE if display else RASTER_SCALE
        cap = DISPLAY_RASTER_CAP if display else 1600
        width = max(min(round(w_ex * px_per_ex * scale), cap), 8)
        signature = _hash("png-v3:"+entry["svg"]+str((width, RASTER_COLORS)))
        raw = raw_dir / f"{signature}.png"
        meta[key] = [("", w_ex, h_ex)]
        tasks.append((key, entry["svg"], raw, width, display))
    def build(task):
        key, svg, raw, width, display = task
        if not _png_ok(raw):
            proc = subprocess.run(["rsvg-convert", "--width", str(width)],
                                  input=svg.encode(), capture_output=True, check=True)
            with Image.open(BytesIO(proc.stdout)) as im:
                im.convert("RGBA").quantize(colors=RASTER_COLORS, method=2).save(raw, optimize=True)
        with Image.open(raw) as original:
            rgba = original.convert("RGBA")
            canvas_width = round(canvas_px * DISPLAY_RASTER_SCALE / RASTER_SCALE)
            if display and rgba.width < canvas_width:
                canvas = Image.new("RGBA", (canvas_width, rgba.height), (0, 0, 0, 0))
                canvas.paste(rgba, ((canvas_width-rgba.width)//2, 0))
                result = canvas.quantize(colors=RASTER_COLORS, method=2)
            else:
                result = original.copy()
            buf = BytesIO(); result.save(buf, format="PNG", optimize=True)
        target = png_dir / f"eq-{key}.png"
        data = buf.getvalue()
        if not target.exists() or target.read_bytes() != data:
            temp = target.with_suffix(".tmp")
            temp.write_bytes(data); temp.replace(target)
    # Unique role-based keys may share raw images; serialize each raw cache key.
    grouped = {}
    for task in tasks:
        grouped.setdefault(task[2], []).append(task)
    def group_build(group):
        for task in group:
            build(task)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(group_build, grouped.values()))
    print(f"  png: {len(tasks)} current formulas, immutable raw cache")
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
    md_path: Path, mode: str, out_epub: Path, cover: Path | None, hybrid_dir: Path,
    metadata_path: Path | None = None,
) -> None:
    cmd = [
        "pandoc", str(md_path),
        "--from", "markdown",
        "--to", "epub3",
        "--split-level=2",
        "--toc", "--toc-depth=2",
        "--metadata-file", str(metadata_path or TOOL_DIR / "metadata.yaml"),
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
    _add_contributor_metadata(out_epub, _read_metadata(metadata_path))


def _add_contributor_metadata(epub_path: Path, meta: dict) -> None:
    """Add explicit MARC roles that Pandoc's YAML reader does not emit."""
    people = [
        ("translator", "trl", meta.get("translator", [])),
        ("reviewer", "rev", meta.get("reviewer", [])),
    ]
    if not any(names for _, _, names in people):
        return
    fd, tmp_name = tempfile.mkstemp(
        prefix=epub_path.name + ".", suffix=".tmp", dir=epub_path.parent
    )
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with zipfile.ZipFile(epub_path) as source, zipfile.ZipFile(
            tmp, "w", zipfile.ZIP_DEFLATED
        ) as target:
            for info in source.infolist():
                data = source.read(info.filename)
                if info.filename.endswith(".opf"):
                    opf = data.decode("utf-8")
                    additions = []
                    for prefix, role, names in people:
                        for i, name in enumerate(names, 1):
                            ident = f"{prefix}{i}"
                            additions.append(
                                f'<dc:contributor id="{ident}">'
                                f'{html.escape(str(name))}</dc:contributor>'
                                f'<meta refines="#{ident}" property="role" '
                                f'scheme="marc:relators">{role}</meta>'
                            )
                    opf = opf.replace(
                        "</metadata>", "".join(additions) + "</metadata>"
                    )
                    data = opf.encode("utf-8")
                if info.filename == "mimetype":
                    target.writestr(info, data, compress_type=zipfile.ZIP_STORED)
                else:
                    target.writestr(info, data)
        os.chmod(tmp, epub_path.stat().st_mode)
        os.replace(tmp, epub_path)
    finally:
        tmp.unlink(missing_ok=True)


# --------------------------------------------------------------------------
# svg mode: chunked assembly (a 55 MB single-file pandoc run gets OOM-killed)
# --------------------------------------------------------------------------

def _split_sections(md_text: str) -> list[dict]:
    sections, cur = [], None
    for line, visible in zip(md_text.split("\n"), mask_code(md_text).split("\n")):
        m = re.match(r"^(#{1,2}) (.+)$", visible)
        if m:
            if cur is not None:
                sections.append(cur)
            cur = {"level": len(m[1]), "title": line[len(m[1])+1:].strip(), "lines": [line]}
        elif cur is not None:
            cur["lines"].append(line)
        elif line.strip():
            cur = {"level": 1, "title": "正文", "lines": ["# 正文", "", line]}
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


def _read_metadata(path: Path | None = None) -> dict:
    import yaml

    meta = yaml.safe_load((path or TOOL_DIR / "metadata.yaml").read_text(encoding="utf-8"))
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
            f'<content src="text/{top["file"]}"/>'
        )
        for child in top["children"]:
            play += 1
            ncx_points.append(
                f'<navPoint id="np{play}" playOrder="{play}">'
                f"<navLabel><text>{esc(child['title'])}</text></navLabel>"
                f'<content src="text/{child["file"]}"/></navPoint>'
            )
        ncx_points.append("</navPoint>")
    body_node = next((t for t in toc if t["title"] == meta.get("body_title")), toc[0])
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
        f'<li><a epub:type="bodymatter" href="text/{body_node["file"]}">正文</a></li>\n'
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
    metadata_path: Path | None = None, px_per_ex: float = 8.0,
    canvas_px: int = DISPLAY_CANVAS_PX,
) -> None:
    """svg/png-mode assembly: pandoc per H1/H2 section, package the zip ourselves.

    md_text carries math as ⟂MATHx:hash⟂ tokens; the real markup —
    <img> references to media files (PNG bitmaps for png mode, external
    .svg for svg mode) — is spliced in after pandoc (see inject_svg_math).
    Both modes pass the class-scoped epub-png.css: bare-img CSS rules make
    ex-blind engines fall back to intrinsic bitmap size.
    """
    meta = _read_metadata(metadata_path)
    sections = _split_sections(md_text)
    # Transient staging in a fresh tempdir — never inside the tool repo, and
    # unique per run (no cleanup races).
    stage_context = tempfile.TemporaryDirectory(prefix="md2epub-stage-")
    staging = Path(stage_context.name)
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
        xhtml = xhtml.replace('lang="zh-CN"', f'lang="{html.escape(meta.get("lang", "zh-CN"), quote=True)}"')
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
                    raise ConvertError(f"image not found: {src}")
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
        xhtml = inject_svg_math(
            xhtml, cache, jobs, png_meta, px_per_ex,
            media_dir=staging / "EPUB" / "media", canvas_px=canvas_px,
        )
        if png_meta is not None and png_dir is not None:
            for name in set(
                re.findall(r'src="\.\./media/(eq-[^"/]+\.png)"', xhtml)
            ):
                staged = staging / "EPUB" / "media" / name
                if not staged.exists():
                    shutil.copyfile(png_dir / name, staged)
        fname = f"ch{i:03d}.xhtml"
        (staging / "EPUB" / "text" / fname).write_text(xhtml, encoding="utf-8")
        spine_files.append(fname)
        nav_title = re.sub(r"⟂MATH[DI]:([0-9a-f]+)⟂", lambda m: jobs[m[1]]["latex"], sec["title"])
        nav_title = html.unescape(re.sub(r"<[^>]+>", "", nav_title)).replace("\\_", "_")
        node = {"file": fname, "title": nav_title, "children": []}
        if sec["level"] == 1:
            toc.append(node)
        elif toc:
            toc[-1]["children"].append(node)
        else:
            toc.append(node)

    nav, ncx = _nav_and_ncx(toc, meta)
    nav = nav.replace('lang="zh-CN"', f'lang="{html.escape(meta.get("lang", "zh-CN"), quote=True)}"')
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
    media_types = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".svg": "image/svg+xml",
    }
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
    # EPUB2-era legacy markers: older engines (and calibre's cover sniffing)
    # locate the cover via meta[name=cover]/guide instead of properties.
    # epubcheck flags guide as deprecated in EPUB3 — harmless, mainstream
    # publishing tooling still emits it for exactly this compat reason.
    legacy_cover_meta = (
        '<meta name="cover" content="cover-image"/>\n' if cover is not None else ""
    )
    guide = (
        "<guide>\n"
        f'<reference type="cover" title="封面" href="text/{cover_page}"/>\n'
        '<reference type="toc" title="目录" href="nav.xhtml"/>\n'
        "</guide>\n"
        if cover_page
        else ""
    )
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
        + "".join(
            f'<dc:contributor id="translator{i}">{html.escape(a)}</dc:contributor>'
            f'<meta refines="#translator{i}" property="role" '
            f'scheme="marc:relators">trl</meta>'
            for i, a in enumerate(meta.get("translator", []), 1)
        )
        + "".join(
            f'<dc:contributor id="reviewer{i}">{html.escape(a)}</dc:contributor>'
            f'<meta refines="#reviewer{i}" property="role" '
            f'scheme="marc:relators">rev</meta>'
            for i, a in enumerate(meta.get("reviewer", []), 1)
        )
        + f'<dc:language>{html.escape(meta.get("lang", "zh-CN"))}</dc:language>\n'
        f'<dc:date>{html.escape(str(meta.get("date", "")))}</dc:date>\n'
        f'<meta property="dcterms:modified">{now}</meta>\n'
        f"{legacy_cover_meta}"
        "</metadata>\n"
        f"<manifest>{''.join(manifest)}</manifest>\n"
        f'<spine toc="ncx">{spine}</spine>\n'
        f"{guide}"
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
    stage_context.cleanup()


# --------------------------------------------------------------------------
# failure log — every formula/warning that did not survive conversion cleanly
# --------------------------------------------------------------------------

def _occurrence_index(text: str) -> dict[str, list[dict]]:
    """latex -> [{'kind','chapter','section','idx','context'}] in doc order."""
    text = mask_code(text)
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
            "mathml 模式 — texmath 无法转换的公式（渲染失败）",
            "  说明: pandoc 将这些公式按原始 TeX 文本嵌入"
            f"（<span class=\"math\">，共 {sum(counts.values())} 处）；"
            "可能是源识别错误或渲染器不支持；原文已保留，详见定位。",
            matched,
            occ,
        )
        if unmatched:
            lines.append(f"  另有 {len(unmatched)} 条无法回溯定位的回退片段:")
            for latex in unmatched[:10]:
                lines.append(f"      {latex[:100]}")

    if any(m in modes for m in ("svg", "png")) and svg_cache is not None and svg_jobs is not None:
        failed_hashes = [k for k, v in svg_cache.items() if "svg" not in v]
        failed_latices = [svg_jobs[h]["latex"] for h in failed_hashes if h in svg_jobs]
        lines += _format_failures(
            "svg/png 模式 — MathJax 渲染失败的公式（渲染失败）",
            "  说明: 输出为 <code class=\"math-raw\"> 原始 TeX 文本；"
            "原文已保留；具体原因见缓存 error 字段。",
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
    else:  # svg/png: every formula is an <img> reference into EPUB/media/
        math_count = sample.count(b'src="../media/eq-')
        math_count += sample.count(b'class="msym"')  # text-rendered symbols
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
        "--math", choices=("mathml", "svg", "png", "both", "all"), default="both",
        help="formula rendering mode (default: both = mathml + svg)",
    )
    parser.add_argument(
        "--config", type=Path, default=TOOL_DIR / "book_config.json",
        help="per-book heuristic overrides (json)",
    )
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--build-dir", type=Path, default=None)
    parser.add_argument("--source-md", type=Path, default=None)
    args = parser.parse_args(argv)

    hybrid_dir = args.hybrid_dir.resolve()
    if not hybrid_dir.is_dir():
        print(f"error: {hybrid_dir} is not a directory", file=sys.stderr)
        return 2
    cfg = load_config(args.config)
    # Build artifacts (caches, preprocessed md, failure log) live with the
    # book — the directory containing the MinerU output — not in the tool
    # repo: they are book-specific and large.
    out_dir = (args.out_dir or hybrid_dir.parent).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    build_dir = args.build_dir or out_dir / "md2epub-build"
    build_dir.mkdir(parents=True, exist_ok=True)
    # Per-book metadata may live inline in the config (dumped to the build
    # dir for pandoc) instead of the repo's default metadata.yaml.
    metadata_path = TOOL_DIR / "metadata.yaml"
    if isinstance(cfg.get("metadata"), dict):
        import yaml

        metadata_path = build_dir / "metadata.yaml"
        metadata_path.write_text(
            yaml.safe_dump(cfg["metadata"], allow_unicode=True), encoding="utf-8"
        )
    md_path = args.source_md or find_md(hybrid_dir, cfg)
    book_stem = cfg.get("book_name") or md_path.stem
    print(f"source md: {md_path.name} ({md_path.stat().st_size / 1e6:.2f} MB)")
    text = preprocess(md_path.read_text(encoding="utf-8"), cfg)
    if cfg.get("colophon_enabled"):
        text = cfg["colophon_md"].rstrip("\n") + "\n\n" + text

    modes = ["mathml", "svg", "png"] if args.math == "all" else ["mathml", "svg"] if args.math == "both" else [args.math]
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
            if mode == "png":
                # Trivial single-token formulas (x, \alpha, x_n, f') become
                # plain HTML text: px-fallback engines enforce a minimum
                # inline image height, which blows single glyphs up oversized
                # and blurry; text is text-size on every engine.
                n_text = 0
                for job in jobs.values():
                    th = _text_symbol_html(job["latex"])
                    if th:
                        job["text_html"] = th
                        n_text += 1
                if n_text:
                    print(f"  text symbols: {n_text} trivial formulas -> HTML text")
            render_jobs = {k: v for k, v in jobs.items() if "text_html" not in v}
            cache, _ = render_math_via_node(
                render_jobs, build_dir, build_dir / "math_cache.json"
            )
            svg_cache, svg_jobs = cache, jobs
            # Ordinary dollars and dollars in code are not formula errors.
            md_text, code_saved = protect_code(tokenised)
            if mode == "png":
                # Inline tokens living on lines that must never be re-flowed
                # (headings/tables/quotes/list items) cannot take the
                # promoted display path; flipping them to display glyphs
                # only shrinks them inside the clamped text line.
                blocked = {
                    k
                    for line in md_text.split("\n")
                    if line.startswith(("#", "|", ">")) or _LIST_LINE_RE.match(line)
                    for k in re.findall(r"⟂MATHI:([0-9a-f]+)⟂", line)
                }
                # Content-aware rendering style: simple display formulas get
                # compact inline glyphs, complex inline ones get full display
                # glyphs (see FLIP_* constants).
                flips = _decide_flips(
                    cache, {k: v for k, v in jobs.items() if k not in blocked}
                )
                # Stale display renders of now-blocked tokens (flip decided
                # by an earlier build) revert to inline glyphs.
                for k in blocked:
                    if (
                        k in jobs
                        and "text_html" not in jobs[k]
                        and not jobs[k]["display"]
                        and (cache.get(k) or {}).get("rendered_display", False)
                    ):
                        flips[k] = False
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
                md_text, n_merged = _merge_orphan_inline_lines(md_text)
                if n_merged:
                    print(
                        f"  orphan inline: merged {n_merged} formula-only "
                        "lines back into their paragraph"
                    )
                # Enumerated display equations ("(1) $…$;" lines) re-flow as
                # a whole onto display lines — see _reflow_numbered_math_lines.
                md_text, jobs, new_keys = _reflow_numbered_math_lines(
                    md_text, jobs
                )
                if new_keys:
                    cache, _ = render_math_via_node(
                        {k: jobs[k] for k in new_keys},
                        build_dir,
                        build_dir / "math_cache.json",
                    )
                    svg_cache, svg_jobs = cache, jobs
                # Tall inline formulas are clamped to one text line on weak
                # engines whatever the markup (imgtest5/6): re-flow them
                # onto their own display line — standard math typesetting
                # for tall formulas. Over-wide ones (wider than a full text
                # line) are squeezed horizontally by every wrapping engine,
                # so they take the same path. Dims come from the rendered SVG.
                def _dims_ex(k: str) -> tuple[float, float] | None:
                    svg = (cache.get(k) or {}).get("svg")
                    dims = _svg_ex_dims(svg) if svg else None
                    return dims if dims else None

                promote = {
                    k for k, j in jobs.items()
                    if not j["display"] and "text_html" not in j
                    and f"⟂MATHI:{k}⟂" in md_text
                    and (d := _dims_ex(k)) is not None
                    and (d[1] >= PROMOTE_INLINE_EX or d[0] >= PROMOTE_INLINE_W_EX)
                }
                # A promoted key's bitmap gets canvas padding (display
                # treatment), which would wreck any occurrence that must
                # stay inline — keep such keys fully inline instead.
                # _LIST_LINE_RE (not just "1."/"-"): promoting a formula on
                # a "(2)"-style marker line splits the list item — the
                # marker stays as an empty <li> and the line's text is
                # scattered (vol-3 Dini/Abel round). The height-pinned
                # inline markup keeps its aspect on every engine, so the
                # print layout (numbered condition line with inline math)
                # survives.
                blocked = set()
                for line in md_text.split("\n"):
                    if line.startswith(("#", "|", ">")) or _LIST_LINE_RE.match(
                        line
                    ):
                        blocked.update(
                            k for k in promote if f"⟂MATHI:{k}⟂" in line
                        )
                promote -= blocked
                if promote:
                    tok_re = re.compile(
                        r"⟂MATHI:(?P<key>[0-9a-f]+)⟂"
                        r"(?P<post>[。．，,；;、：:）)】\]]*)"
                    )
                    state = {"n": 0}

                    def _promote_repl(m: re.Match) -> str:
                        if m["key"] not in promote:
                            return m.group(0)
                        state["n"] += 1
                        # paragraph split around the display token; trailing
                        # punctuation stays on the formula line
                        return f"\n\n⟂MATHD:{m['key']}⟂{m['post']}\n\n"

                    md_text = tok_re.sub(_promote_repl, md_text)
                    for k in promote:
                        jobs[k]["display"] = True
                    print(
                        f"  promoted {state['n']} tall/wide inline formulas "
                        f"(h >= {PROMOTE_INLINE_EX}ex or "
                        f"w >= {PROMOTE_INLINE_W_EX}ex) onto their own line"
                    )
                # Over-wide display equations (promoted ones included — they are
                # display jobs by the time this runs): re-flow into aligned lines so
                # each line fits the box width and glyphs render near text
                # size instead of width-capped small.
                md_text, jobs, cache = _split_wide_formulas(
                    md_text, jobs, cache, build_dir
                )
                svg_cache, svg_jobs = cache, jobs
                png_meta = render_pngs(
                    {k: cache[k] for k in jobs if k in cache},
                    build_dir / "png_cache",
                    skip={k for k, j in jobs.items() if "text_html" in j},
                    display_keys={k for k, j in jobs.items() if j["display"]},
                    px_per_ex=cfg.get("png_px_per_ex", 8.0),
                    canvas_px=cfg.get(
                        "png_display_canvas_px", DISPLAY_CANVAS_PX
                    ),
                )
        if mode != "mathml":
            md_text = restore_code(md_text, code_saved)
        md_out = build_dir / f"preprocessed.{mode}.md"
        md_out.write_text(md_text, encoding="utf-8")
        out_epub = out_dir / f"{book_stem}.{mode}.epub"
        if mode == "mathml":
            run_pandoc(md_out, mode, out_epub, cover, hybrid_dir, metadata_path)
            mathml_epub = out_epub
        else:
            build_epub_chunked(
                md_text, out_epub, cover, hybrid_dir, cache, jobs,
                png_meta=png_meta,
                png_dir=build_dir / "png_cache" if png_meta is not None else None,
                css_file=TOOL_DIR / "epub-png.css",
                metadata_path=metadata_path,
                px_per_ex=cfg.get("png_px_per_ex", 8.0),
                canvas_px=cfg.get(
                    "png_display_canvas_px", DISPLAY_CANVAS_PX
                ),
            )
        report = build_report(out_epub, mode)
        reports.append(report)
        print(f"  -> {report}")

    log_path = build_dir / "failures.log"
    log_modes = list(modes)
    write_failure_log(
        log_path,
        source_name=md_path.name,
        modes=log_modes,
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
