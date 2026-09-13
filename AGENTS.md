# AGENTS.md

Repository-specific guidance for agents working in `/Users/hengliu/workspace/mlx`.

## What this repo is

A collection of standalone Python CLI scripts (in `src/`) that wrap local AI models for OCR, translation, image analysis, LLM chat, ASR, TTS, and Stable Diffusion image generation. Each script is run directly via `python src/<script>.py ...` — there is no package import surface or top-level entrypoint. There is also an unrelated scratch file `embedding.py` at the repo root and a vendored book/exercises tree `hollm/`; treat both as out-of-scope unless asked.

## Environment

- `.python-version` pins `cpython-3.12.14-macos-aarch64-none`. The venv at `.venv/` is the active Python (Python 3.13 per `README.md` — versions disagree; trust `.python-version` if forced to choose).
- Always run scripts via the venv: `source .venv/bin/activate` first. The README also documents `uv pip install -r requirements.txt` for reinstalls.
- No `pyproject.toml`, no CI workflows, no pre-commit config, no formatter/linter config file. `ruff` is listed in `requirements.txt` but is not wired to a config — don't assume `ruff check` works without configuration.

## Architecture notes an agent would miss

- Scripts add the repo root to `sys.path` themselves (`sys.path.insert(0, str(Path(__file__).parent.parent))`) and import `from src.utils import ...`. They are not installable; they must be run as `python src/<script>.py`, not `python -m src.<script>`.
- `src/utils.py` is the shared helper module: `setup_logger`, `resolve_path`, `validate_input_file`, `ensure_output_dir`, `image_to_base64`, `detect_chinese_text`, `read_input_text`, and the `OllamaClient` class. New scripts should reuse these rather than re-implement.
- Two runtime backends, distinguished by which scripts use them:
  - **Ollama** (localhost:11434): `ocr.py`, `translate.py`, `analyze_image.py`, `chat.py`. Configurable via `--host` / `--timeout`.
  - **MLX / diffusers** (local, Apple Silicon): `asr.py`, `tts.py`, `sd15.py`. SD1.5 uses `pipe.enable_model_cpu_offload()` — required for MPS, don't remove.
- Ollama client always sets `think=False` for Qwen3 models; preserve this when touching `src/utils.py:OllamaClient`.
- `src/phi3-mini.py` is a standalone transformers demo (runs `from_pretrained("microsoft/Phi-3-mini-4k-instruct")` with `attn_implementation="eager"`, `trust_remote_code=False`, `device_map="cpu"`). It is intentionally separate from the Ollama-backed scripts and has its own regression test.
- Other files under `src/` (`rag.py`, `qwen3_*.py`, `pipeline.py`, `qwopus35.py`, `qwen38.py`) are exploratory/WIP — confirm with the user before editing unless the request clearly targets one of them.
- `backup/` holds historical reference scripts referenced from the README (e.g. `qwen3-asr.py`, `qwen3-tts-custom-voice.py`, `sd1.5-inference-single.py`). Do not import from here.

## Data layout

- `input/`, `output/`, `models/` are placeholder dirs (gitkeep-only, contents gitignored). Scripts default to `./output` for generated artifacts and `./input` for input files.
- `models/` is empty by default. SD1.5 expects `models/v1-5-pruned-emaonly-fp16.safetensors` (download separately — see README). MLX models auto-download on first run; Ollama models must be pulled via `ollama pull <name>`.
- `test_assets/` contains a single `rag_architecture.png` used by RAG scratch scripts; not for tests.

## Commands worth knowing

- Run a single script: `python src/ocr.py input/image.jpg -v` (each script has its own `--help`).
- Pull Ollama models referenced by the scripts:
  ```
  ollama pull glm-ocr:bf16
  ollama pull translategemma:12b
  ollama pull qwen3-vl:8b
  ollama pull Qwen3:8b
  ```
- Start Ollama if a script fails to connect: `ollama serve`.
- Run the existing test suite (only one test file): `pytest tests/`.

## Testing

- `tests/test_phi3_mini.py` is a regression test that monkeypatches `transformers` and runs `src/phi3-mini.py` via `runpy.run_path`. It asserts `trust_remote_code=False`, `attn_implementation="eager"`, and that the prompt contains no `￼` (object-replacement) character. When changing `src/phi3-mini.py`, update this test to match.
- No fixtures, no snapshot tests, no integration tests. Heavy scripts (OCR/ASR/TTS/SD) require live model services and are not exercised by the test suite — verify manually when touching them.

## Style / workflow conventions that differ from defaults

- Scripts use Chinese-language docstrings and emoji-heavy console output (e.g. `🤖`, `👤`, `✅`). Match the existing tone when adding user-facing messages; do not strip emoji.
- Argparse descriptions include `epilog=` example blocks. Add new scripts in the same shape.
- Every CLI script ends with `if __name__ == '__main__': main()` and traps `KeyboardInterrupt` → `sys.exit(0)` plus a generic `Exception` → `logger.error` + `sys.exit(1)`. Mirror this pattern.
- Ollama-related scripts always accept `--host` and `--timeout` flags with the defaults `http://localhost:11434` and `120`. Keep these flags when adding new Ollama-backed scripts.

## Gotchas

- `src/sd15.py` will exit with "Model file not found" if `models/v1-5-pruned-emaonly-fp16.safetensors` is missing. The README links the Hugging Face source.
- `src/sd15.py` requires `width % 64 == 0` and `height % 64 == 0`; it validates and exits otherwise.
- Black-image SD1.5 output → try `--dtype float32` (documented in README troubleshooting).
- `requirements.txt` is fully pinned with `==` versions; treat it as a lockfile, do not loosen pins casually.
- No `opencode.json`, no `.cursor/rules/`, no `CLAUDE.md`, no `.github/copilot-instructions.md`. This file is the sole agent-instructions source.
