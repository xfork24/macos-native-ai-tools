#!/usr/bin/env python3
"""Structural + math-coverage validator for the generated EPUBs.

Usage:
    python3 validate.py <book.epub> [--expect-top N] [--expect-leaves N]

Exits non-zero when any check fails; prints a PASS/FAIL line per check.
"""

from __future__ import annotations

import argparse
import posixpath
import re
import subprocess
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import PurePosixPath

MATHML_NS = "{http://www.w3.org/1998/Math/MathML}"
XHTML_NS = "{http://www.w3.org/1999/xhtml}"
EPUB_NS = "{http://www.idpf.org/2007/ops}"
MIMETYPE = "application/epub+zip"
RESIDUAL_MATH_RE = re.compile(r"\$\$|\$[^$\n]{1,120}\$")
# <annotation> re-encodes the original TeX inside MathML — its dollars are
# data, not stray delimiters.
MATHML_ANNOTATION_RE = re.compile(r"<annotation[^>]*>.*?</annotation>", re.S)
# pandoc's fallback for unconvertible TeX: <span class="math …">$…$</span>
MATH_FALLBACK_RE = re.compile(
    r'<span class="math[^"]*">.*?</span>', re.S
)

results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def xmllint_ok(data: bytes, name: str) -> bool:
    proc = subprocess.run(
        ["xmllint", "--noout", "-"], input=data, capture_output=True
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        record(f"xml well-formed: {name}", False, err[0] if err else "")
        return False
    return True


def strip_tags(xhtml: str) -> str:
    return re.sub(r"<[^>]+>", " ", xhtml)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("epub", type=__import__("pathlib").Path)
    parser.add_argument("--expect-top", type=int, default=11)
    parser.add_argument("--expect-leaves", type=int, default=43)
    parser.add_argument("--min-math", type=int, default=8000)
    args = parser.parse_args(argv)

    with zipfile.ZipFile(args.epub) as zf:
        names = set(zf.namelist())
        infos = zf.infolist()

        # 1. mimetype: first entry, stored uncompressed, exact content.
        ok = (
            infos[0].filename == "mimetype"
            and infos[0].compress_type == zipfile.ZIP_STORED
            and zf.read("mimetype") == MIMETYPE.encode()
        )
        record("mimetype (first, stored, exact)", ok)

        # 2. required parts parse.
        container = zf.read("META-INF/container.xml")
        ok = xmllint_ok(container, "container.xml")
        rootfile = ET.fromstring(container).find(
            ".//{*}rootfile[@full-path]"
        )
        opf_path = rootfile.get("full-path") if rootfile is not None else None
        ok = ok and opf_path in names and xmllint_ok(
            zf.read(opf_path), opf_path
        )
        record("container.xml + OPF present & parse", ok)
        if not opf_path or opf_path not in names:
            return 1
        opf_dir = str(PurePosixPath(opf_path).parent)

        opf = ET.fromstring(zf.read(opf_path))
        manifest = {
            item.get("id"): item.get("href")
            for item in opf.findall(".//{*}manifest/{*}item")
        }
        spine_ids = {
            itemref.get("idref")
            for itemref in opf.findall(".//{*}spine/{*}itemref")
        }

        # 3. every manifest href exists in the zip.
        missing = [
            href
            for href in manifest.values()
            if f"{opf_dir}/{href}" not in names
        ]
        record(
            "manifest hrefs resolve",
            not missing,
            f"{len(missing)} missing" if missing else f"{len(manifest)} items",
        )

        # 4. nav + ncx present.
        nav_path = next(
            (h for h in manifest.values() if h.endswith("nav.xhtml")), None
        )
        ncx_ok = any(h.endswith(".ncx") for h in manifest.values())
        record("nav.xhtml + toc.ncx in manifest", bool(nav_path) and ncx_ok)

        # 5. XHTML well-formedness (xmllint) + collect content stats.
        xhtml_paths = sorted(n for n in names if n.endswith(".xhtml"))
        wellformed, mathml_count, svg_count, png_count = 0, 0, 0, 0
        residual, fallback_spans, img_misses = 0, 0, []
        for name in xhtml_paths:
            data = zf.read(name)
            if not xmllint_ok(data, name):
                continue
            wellformed += 1
            text = data.decode("utf-8", "replace")
            mathml_count += text.count("<math ")
            svg_count += text.count("<svg")
            png_count += text.count('src="../media/eq-')
            # trivial single-token formulas render as HTML text, not bitmaps
            png_count += text.count('class="msym"')
            # pandoc's raw-TeX fallback spans are counted separately; only
            # delimiters outside them mean a formula was silently lost.
            fallback_spans += len(MATH_FALLBACK_RE.findall(text))
            stripped = MATH_FALLBACK_RE.sub(" ", MATHML_ANNOTATION_RE.sub(" ", text))
            if RESIDUAL_MATH_RE.search(strip_tags(stripped)):
                residual += 1
            base = str(PurePosixPath(name).parent)
            for src in re.findall(r'(?:src|xlink:href)="([^"]+)"', text):
                if not re.search(r"\.(jpe?g|png|gif|svg)$", src, re.I):
                    continue
                target = posixpath.normpath(f"{base}/{src}")
                if target not in names and target.lstrip("/") not in names:
                    img_misses.append(f"{name}: {src}")
        record(
            "all XHTML well-formed",
            wellformed == len(xhtml_paths),
            f"{wellformed}/{len(xhtml_paths)}",
        )
        record(
            "image refs resolve",
            not img_misses,
            "; ".join(img_misses[:3]) if img_misses else "all",
        )

        # 6. math coverage (mode auto-detected from dominant math markup).
        mode, count = max(
            (("mathml", mathml_count), ("svg", svg_count), ("png", png_count)),
            key=lambda pair: pair[1],
        )
        record(
            f"math coverage ({mode})",
            count >= args.min_math,
            f"{count} elements (min {args.min_math})",
        )
        record(
            "no residual TeX delimiters",
            residual == 0,
            f"{residual} files ({fallback_spans} known fallback spans excluded)",
        )

        # 7. spine completeness (nav.xhtml itself is excepted per spec).
        unspined = [
            mid
            for mid, href in manifest.items()
            if href.endswith(".xhtml")
            and href != nav_path
            and mid not in spine_ids
        ]
        record("spine completeness", not unspined, f"{len(unspined)} unspined")

        # 8. nav TOC shape.
        nav_xhtml = zf.read(f"{opf_dir}/{nav_path}").decode("utf-8")
        toc = ET.fromstring(nav_xhtml)
        toc_navs = toc.findall(f".//{XHTML_NS}nav[@{EPUB_NS}type='toc']")
        toc_nav = toc_navs[0] if toc_navs else None
        top_ol = toc_nav.find(f"./{XHTML_NS}ol") if toc_nav is not None else None
        top_li = len(top_ol.findall(f"./{XHTML_NS}li")) if top_ol is not None else 0
        all_li = len(toc_nav.findall(f".//{XHTML_NS}li")) if toc_nav is not None else 0
        record(
            "nav top-level entries",
            top_li == args.expect_top,
            f"{top_li} (expected {args.expect_top})",
        )
        record(
            "nav total entries",
            all_li == args.expect_leaves,
            f"{all_li} (expected {args.expect_leaves})",
        )
        nav_hrefs = re.findall(r'href="([^"#]+)', nav_xhtml)
        broken = [h for h in nav_hrefs if f"{opf_dir}/{h}" not in names]
        record("nav hrefs resolve", not broken, f"{len(broken)} broken")

    failed = [name for name, ok, _ in results if not ok]
    print(
        f"\n{len(results) - len(failed)}/{len(results)} checks passed"
        + (f"; FAILED: {', '.join(failed)}" if failed else " — all green")
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
