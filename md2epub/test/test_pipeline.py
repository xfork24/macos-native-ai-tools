"""核心无人值守转换回归测试，不依赖整书或外部模型。"""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET

import analyze_book
import convert
from markdown_regions import protect_code, restore_code
from validate import validate_epub


class PipelineTests(unittest.TestCase):
    def test_code_regions_are_not_headings_or_math(self):
        source = (
            "# Chapter\n\n```python\n# fake\ns = '$x$'\n```\n\n"
            "`$x$` and $y$ and \\$30\n"
        )
        protected, saved = protect_code(source)
        self.assertEqual(restore_code(protected, saved), source)
        self.assertEqual(convert.count_math(source), (0, 1))
        self.assertEqual(len(convert._split_sections(source)), 1)

    def test_formula_roles_have_independent_jobs(self):
        tokenized, jobs = convert.tokenise_math(
            "$x$\n\n$$\nx\n$$\n\n$x$"
        )
        inline = re.findall(r"⟂MATHI:([a-f0-9]+)⟂", tokenized)
        display = re.findall(r"⟂MATHD:([a-f0-9]+)⟂", tokenized)
        self.assertEqual(len(jobs), 2)
        self.assertEqual(inline[0], inline[1])
        self.assertNotEqual(inline[0], display[0])

    def test_preamble_is_not_lost(self):
        sections = convert._split_sections("opening prose\n\n# Chapter\nbody")
        self.assertEqual(len(sections), 2)
        self.assertIn("opening prose", sections[0]["lines"])

    def test_numbered_single_line_display_is_preserved(self):
        cleaned = convert.preprocess(
            "# Book\n\n$$ x=1 \\tag{3} $$", {"prepared": True}
        )
        self.assertIn(r"\text{(3)}", cleaned)
        self.assertEqual(convert.count_math(cleaned), (1, 0))

    def test_missing_config_and_ambiguous_markdown_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            with self.assertRaises(convert.ConvertError):
                convert.load_config(directory / "missing.json")
            (directory / "a.md").write_text("a")
            (directory / "b.md").write_text("b")
            with self.assertRaises(ValueError):
                analyze_book.discover(directory)

    def test_analyzer_removes_bounded_toc_and_recovers_header_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            blocks = [
                {"type": "text", "text": "Book", "text_level": 1, "page_idx": 0},
                {
                    "type": "text",
                    "text": "第1章 介绍了概念。这是正文概览，不是目录。",
                    "page_idx": 0,
                },
                {"type": "text", "text": "目录", "text_level": 2, "page_idx": 1},
                {
                    "type": "text",
                    "text": "第1章 起点……1\n1.1 第一节……2\n第2章 数据……3\n2.1 第二节……4",
                    "page_idx": 1,
                },
                {"type": "text", "text": "第1章", "text_level": 2, "page_idx": 2},
                {"type": "text", "text": "起点", "text_level": 1, "page_idx": 2},
                {"type": "text", "text": "第一章正文。", "page_idx": 2},
                {"type": "text", "text": "第二章引言。", "page_idx": 3},
                {"type": "header", "text": "第2章 数据", "page_idx": 3},
                {"type": "text", "text": "2.1 第二节", "text_level": 2, "page_idx": 4},
                {"type": "page_number", "text": "20", "page_idx": 20},
            ]
            source = "\n\n".join(
                ("#" * block.get("text_level", 0) + " "
                 if block.get("text_level") else "") + block["text"]
                for block in blocks if block["type"] == "text"
            )
            (directory / "book.md").write_text(source)
            (directory / "book_content_list.json").write_text(
                json.dumps(blocks, ensure_ascii=False)
            )
            normalized, _, _ = analyze_book.analyze(directory)
            self.assertIn("不是目录", normalized)
            self.assertNotIn("……", normalized)
            self.assertIn("# 第2章 数据\n\n第二章引言", normalized)

    def test_ncx_keeps_real_hierarchy_and_body_landmark(self):
        toc = [
            {"file": "intro.xhtml", "title": "Introduction", "children": []},
            {
                "file": "ch.xhtml",
                "title": "Chapter",
                "children": [{"file": "sec.xhtml", "title": "Section"}],
            },
        ]
        nav, ncx = convert._nav_and_ncx(
            toc, {"title": "Book", "identifier": "id", "body_title": "Chapter"}
        )
        self.assertIsNotNone(
            ET.fromstring(ncx).find(".//{*}navPoint/{*}navPoint")
        )
        self.assertIn('epub:type="bodymatter" href="text/ch.xhtml"', nav)

    @unittest.skipUnless(shutil.which("node"), "requires node and MathJax")
    def test_cache_retains_inline_and_display_variants(self):
        key = "c" * 40
        with tempfile.TemporaryDirectory() as tmp:
            build = Path(tmp)
            cache_path = build / "math_cache.json"
            inline = {key: {"latex": r"\frac{1}{2}", "display": False}}
            display = {key: {"latex": r"\frac{1}{2}", "display": True}}
            convert.render_math_via_node(inline, build, cache_path)
            convert.render_math_via_node(display, build, cache_path, force=True)
            restored, _ = convert.render_math_via_node(inline, build, cache_path)
            stored = json.loads(cache_path.read_text())
            self.assertEqual(
                len([name for name in stored if name.startswith(key + "@")]), 2
            )
            self.assertFalse(restored[key]["rendered_display"])

    @unittest.skipUnless(shutil.which("rsvg-convert"), "requires librsvg")
    def test_png_canvas_is_reversible_and_repeatable(self):
        key = "b" * 40
        cache = {
            key: {
                "svg": (
                    '<svg xmlns="http://www.w3.org/2000/svg" width="2ex" '
                    'height="2ex" viewBox="0 0 20 20">'
                    '<path fill="black" fill-opacity="0.5" '
                    'd="M1 1H19V19H1Z"/></svg>'
                )
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            convert.render_pngs(cache, directory, display_keys={key})
            first = (directory / f"eq-{key}.png").read_bytes()
            convert.render_pngs(cache, directory, display_keys={key})
            self.assertEqual(first, (directory / f"eq-{key}.png").read_bytes())
            convert.render_pngs(
                cache, directory, display_keys={key}, canvas_px=1000
            )
            convert.render_pngs(cache, directory, display_keys={key})
            self.assertEqual(first, (directory / f"eq-{key}.png").read_bytes())

    def test_png_failure_does_not_fall_back_to_inline_svg(self):
        key = "a" * 40
        jobs = {key: {"latex": "x", "display": False}}
        cache = {
            key: {
                "svg": '<svg width="1ex" height="1ex"/>',
                "valign_ex": 0,
            }
        }
        result = convert.inject_svg_math(
            f"⟂MATHI:{key}⟂", cache, jobs, png_meta={}
        )
        self.assertIn("math-raw", result)
        self.assertNotIn("<svg", result)

    def test_corrupt_epub_fails_without_leaking_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.epub"
            path.write_text("broken")
            first = validate_epub(path)
            self.assertFalse(first["ok"])
            self.assertEqual(first, validate_epub(path))


if __name__ == "__main__":
    unittest.main()
