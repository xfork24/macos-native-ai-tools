# AGENTS.md

Repository-specific guidance for agents working in `/Users/hengliu/workspace/mlx`.

## What this repo is

Three largely independent parts:

1. **`src/`** — standalone Python CLI scripts wrapping local AI models for OCR, translation, image analysis, LLM chat, ASR, TTS, speech translation, and Stable Diffusion image generation. Each script is run directly via `python src/<script>.py ...` — there is no package import surface or top-level entrypoint.
2. **`md2epub/`** — a MinerU-OCR-markdown → EPUB3 converter subproject (fully local: no LLM, no network). It has its own detailed `md2epub/README.md`; read it before changing anything there.
3. Scratch/vendored trees, out-of-scope unless asked: `embedding.py` at the repo root and the vendored book/exercises tree `hollm/` (untracked, own `pyproject.toml`, designed for remote GPU platforms — do not run locally or commit changes to it).

`backup/` holds historical reference scripts; `models/` holds SD weights; `input/`/`output/` are scratch dirs for script I/O.

## Environment

- `.python-version` pins `cpython-3.12.14`, but the actual runtime is the venv at `.venv/` (Python 3.13.11). **Trust `.venv`** — always `source .venv/bin/activate` first; the pin file is stale.
- `uv pip install -r requirements.txt` for (re)installs. It is fully pinned with `==` versions; treat it as a lockfile, do not loosen pins casually.
- No `pyproject.toml`, no CI workflows, no pre-commit config. `ruff` (pinned in requirements.txt) runs with default rules and no config file; the repo is not ruff-clean (~21 pre-existing findings in `src/`+`tests/`), so don't treat a non-zero `ruff check` as caused by your change without diffing.
- md2epub additionally needs external commands (`brew install pandoc librsvg poppler`), its own npm package (`cd md2epub && npm install`, mathjax-full), and Python `PyYAML` + `Pillow`.

## Architecture notes an agent would miss

### src/ CLI scripts

- Scripts add the repo root to `sys.path` themselves (`sys.path.insert(0, str(Path(__file__).parent.parent))`) and import `from src.utils import ...`. They are not installable; run as `python src/<script>.py`, not `python -m src.<script>`.
- `src/utils.py` is the shared helper module: `setup_logger`, `resolve_path`, `validate_input_file`, `ensure_output_dir`, `image_to_base64`, `detect_chinese_text`, `read_input_text`, and the `OllamaClient` class. New scripts should reuse these rather than re-implement.
- Runtime backends, distinguished by which scripts use them:
  - **Ollama** (localhost:11434): `ocr.py`, `translate.py`, `analyze_image.py`, `chat.py`. Configurable via `--host` / `--timeout`. All requests go through `OllamaClient`, which always sets `think=False` for Qwen3 models; preserve this when touching `src/utils.py`.
  - **MLX** (local, Apple Silicon): `asr.py`, `tts.py`, `whisper_translate.py` (mlx-whisper, Chinese→English speech translation). Models auto-download on first run.
  - **PyTorch/diffusers** (MPS): `sd15.py`. Uses `pipe.enable_model_cpu_offload()` — required for MPS, don't remove.
- `src/phi3-mini.py` and `src/t5_small.py` are standalone transformers demos (intentionally separate from the Ollama-backed scripts); `phi3-mini.py` has its own regression test.
- `src/pipeline.py` orchestrates OCR → translate → TTS by importing each script's `main()` and redirecting stdout. Note the inconsistent import styles across the RAG family: `pipeline.py` uses `from src.utils import ...` while `qwen3_rag_pipeline.py` uses bare imports (`from qwen3_bm25_rag import ...`) — check how a script is actually run before changing its imports.
- Other files under `src/` (`rag.py`, `qwen3_*.py`, `qwopus35.py`, `qwen38.py`) are exploratory/WIP — confirm with the user before editing unless the request clearly targets one of them.

### md2epub

- **`md2epub.py` is the unified entry point**: analyze MinerU output → auto-generate config → build MathML/SVG/PNG EPUB variants → structural acceptance → atomic replace. `convert.py` is the legacy low-level entry, only used with `book_config_xinjiang_v1~v3.json` to reproduce already-calibrated books; new books must use `md2epub.py`.
- Module chain: `analyze_book.py` (auto-analysis, title recovery) → `convert.py` (~2,200 lines; preprocessing, math tokenization, rendering, assembly) → `validate.py` (EPUB acceptance) + `audit_png.py` (PNG-specific audit). `render_math.js` (node + mathjax-full) renders SVG; `markdown_regions.py` protects code regions from heading/math detection.
- Config cascade: `book_config.template.json` → auto-analysis result → `--config` overrides. Legacy configs containing `chapter_regex` go through a compatibility path.
- **All formulas are external file references** (`<img src="../media/eq-*.{svg,png}"/>`; zero inline `<svg>`, zero `data:` URIs). This is what survives format converters (e.g. calibre → Kindle azw3, which drops inline `<svg>` but passes referenced media through). Do not change this back to inline.
- Caches: `md2epub-build/math_cache.json` and `png_cache/` (immutable raw bitmaps) are reused across runs; changing raster-density constants (`RASTER_SCALE`, `DISPLAY_RASTER_SCALE`, …) requires deleting `png_cache/` to re-raster.
- Concurrency: the output dir is `flock`-locked; never run two conversions of the same book in parallel. The MinerU source dir (`hybrid_auto/`) is never modified.
- The command's positional argument is the `hybrid_auto` directory itself, not the book directory above it. Build artifacts (EPUBs, `md2epub-build/`) land in the book directory outside this repo — don't commit them.

## Data layout

- `input/`, `output/`, `models/` are placeholder dirs (gitkeep-only, contents gitignored). Scripts default to `./output` for generated artifacts and `./input` for input files.
- `models/` is empty by default. SD1.5 expects `models/v1-5-pruned-emaonly-fp16.safetensors` (download separately — see README). Ollama models must be pulled via `ollama pull <name>`.
- **`.gitignore` traps**: `tests/` and `test_assets/` are ignored, and the `tests/` pattern matches at any depth. Consequently `tests/test_phi3_mini.py` is **not tracked in git**, and md2epub's test suite lives in `md2epub/test/` (singular) so it can be tracked. `rag_architecture.png` at the root is also ignored (a copy used by RAG scratch scripts sits in `test_assets/`).

## Commands worth knowing

- Run a single script: `python src/ocr.py input/image.jpg -v` (each script has its own `--help`).
- Start Ollama if a script fails to connect: `ollama serve`. Pull the models referenced by the scripts:
  ```
  ollama pull glm-ocr:bf16
  ollama pull translategemma:12b
  ollama pull qwen3-vl:8b
  ollama pull Qwen3:8b
  ```
- Convert a book: `cd md2epub && python md2epub.py "/path/to/书名/hybrid_auto"` (defaults to `--math all`, producing `.mathml/.svg/.png.epub`; see `--help` and `md2epub/README.md`).
- Root test suite: `pytest tests/` (single test: `pytest tests/test_phi3_mini.py::test_phi3_uses_native_transformers_implementation`).
- md2epub test suite: `cd md2epub && python -m unittest test.test_pipeline` — must run from `md2epub/` because the tests use bare imports.
- Lint: `ruff check src/ tests/` (and `md2epub/` if desired).

## Testing

- `tests/test_phi3_mini.py` is a regression test that monkeypatches `transformers` and runs `src/phi3-mini.py` via `runpy.run_path`. It asserts `trust_remote_code=False`, `attn_implementation="eager"`, and that the prompt contains no `￼` (object-replacement) character. When changing `src/phi3-mini.py`, update this test to match. (Remember: untracked in git — see the gitignore traps above.)
- `md2epub/test/test_pipeline.py` (unittest) is an unattended regression suite covering the conversion core; it needs no whole book and no external model services, but does require the md2epub dir as cwd.
- `md2epub/test/build_imgtest*.py` are device-calibration EPUB builders for on-reader experiments — not unit tests.
- No fixtures, no snapshot tests, no integration tests. Heavy scripts (OCR/ASR/TTS/SD) require live model services and are not exercised by the test suite — verify manually when touching them.

## Style / workflow conventions that differ from defaults

- Scripts use Chinese-language docstrings and emoji-heavy console output (e.g. `🤖`, `👤`, `✅`). Match the existing tone when adding user-facing messages; do not strip emoji.
- Argparse descriptions include `epilog=` example blocks. Add new scripts in the same shape.
- Every CLI script ends with `if __name__ == '__main__': main()` and traps `KeyboardInterrupt` → `sys.exit(0)` plus a generic `Exception` → `logger.error` + `sys.exit(1)`. Mirror this pattern.
- Ollama-related scripts always accept `--host` and `--timeout` flags with the defaults `http://localhost:11434` and `120`. Keep these flags when adding new Ollama-backed scripts.
- md2epub code is deliberately dense (compact expressions, minimal blank lines); match the local style of each file rather than reformatting wholesale.

## Gotchas

- `src/sd15.py` will exit with "Model file not found" if `models/v1-5-pruned-emaonly-fp16.safetensors` is missing. The README links the Hugging Face source.
- `src/sd15.py` requires `width % 64 == 0` and `height % 64 == 0`; it validates and exits otherwise.
- Black-image SD1.5 output → try `--dtype float32` (documented in README troubleshooting).
- Root `package.json` is an empty placeholder (this is not a Node project); `md2epub/package.json` is real (mathjax-full dependency).
- md2epub exit codes: `0` for success and success-with-warnings (check `md2epub-build/report.md` for warnings), `1` for failed acceptance, `130` for user interrupt.
- A `CLAUDE.md` exists at the root and carries the same guidance for Claude Code; keep the two in sync when repository conventions change.
