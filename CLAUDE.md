# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概览

本仓库包含三个独立部分：

1. **本地 AI 工具集（`src/`）**：运行在 Apple Silicon Mac 上的独立 CLI 脚本，按推理后端分三类：
   - **Ollama**（服务位于 localhost:11434）：`ocr.py`、`translate.py`、`analyze_image.py`、`chat.py`
   - **MLX**（mlx-audio / mlx-whisper）：`asr.py`、`tts.py`、`whisper_translate.py`
   - **PyTorch/Diffusers**（MPS 加速）：`sd15.py`（Stable Diffusion 1.5）
   - 另有模型实验脚本（`phi3-mini.py`、`qwen38.py`、`qwopus35.py`、`t5_small.py`、根目录 `embedding.py`）和 RAG 实验族（见下文）
2. **`md2epub/`**：MinerU OCR 输出 → EPUB3 电子书转换器（纯本地、无 LLM、无网络），详见下文架构说明及 `md2epub/README.md`
3. **`hollm/`**：《Hands-On Large Language Models》中文版的 vendored 副本（**未纳入 git 跟踪**）。notebook 设计在远程 GPU 平台（openbayes/Colab）上运行，拥有独立的 `pyproject.toml`/`uv.lock` 环境，不要在本地运行或将其改动提交到本仓库。

`backup/` 为参考示例脚本，`models/` 存放 SD 权重，`input/`/`output/` 为脚本的输入输出目录。

## 常用命令

```bash
# 环境
source .venv/bin/activate            # Python 3.13.11
uv pip install -r requirements.txt

# 运行脚本（从仓库根目录运行）
python src/ocr.py input/image.jpg
python src/chat.py "解释什么是机器学习"
python src/tts.py "你好" -v Vivian -l cn

# 测试
pytest tests/
pytest tests/test_phi3_mini.py::test_phi3_uses_native_transformers_implementation   # 单个测试

# md2epub 测试（必须从 md2epub/ 目录运行，bare import 依赖 cwd）
cd md2epub && python -m unittest test.test_pipeline

# Lint
ruff check src/ tests/
```

Ollama 脚本依赖本机 Ollama 服务（`ollama serve`）和已拉取的模型：`glm-ocr:bf16`、`translategemma:12b`、`qwen3-vl:8b`、`Qwen3:8b`。MLX 模型首次使用时自动下载。

md2epub 额外依赖外部命令（`brew install pandoc librsvg poppler`）、本目录 npm 包（`cd md2epub && npm install`，mathjax-full 渲染公式）及 Python 包 `PyYAML`、`Pillow`。转换命令的参数是 MinerU 的 `hybrid_auto` 目录（非其上一级书籍目录）。

## 架构

### CLI 脚本约定
- 每个功能一个独立 argparse 脚本；通用参数：`-v/--verbose`、`-o/--output`、`-m/--model`，Ollama 脚本另有 `--host`、`--timeout`
- 脚本自行把仓库根加入 `sys.path` 后 `from src.utils import ...`，因此只能 `python src/<script>.py` 运行，不能用 `python -m src.<script>`
- 共享工具集中在 `src/utils.py`：`setup_logger`、`resolve_path`、`validate_input_file`、`ensure_output_dir`、`image_to_base64`、`read_input_text`、`detect_chinese_text`
- 用户可见输出使用中文 docstring 和 emoji（🤖、✅ 等），新增脚本保持同样风格

### OllamaClient（src/utils.py）
所有 Ollama 调用都经过这个包装类，并且**所有请求强制 `think=False`**（禁用 Qwen3 思考模式）。新增 Ollama 功能时复用它，不要直接 `import ollama`。

### pipeline.py（多步骤编排）
`pipeline.py` 通过 `from src.ocr import main as ocr_main` 导入各脚本的 `main()`，用 contextlib 重定向 stdout 来串联 OCR → 翻译 → TTS 语音克隆。新增 pipeline 步骤时沿用此模式。

### RAG 实验族（src/ 下多个相似命名文件）
- `rag.py`：混合检索演示 —— BM25（rank_bm25）字面召回 + SentenceTransformer 语义召回 + CrossEncoder 重排
- `qwen3_rag.py` / `qwen3_bm25_rag.py` / `qwen3_rag_pipeline.py` / `qwen3_rag_pipeline_vlm.py`：基于 mlx-lm 的 Qwen3 RAG 变体（本地 4-bit 模型、流式生成）
- 注意导入风格不一致：`pipeline.py` 用 `from src.utils import ...`，而 `qwen3_rag_pipeline.py` 用裸导入 `from qwen3_bm25_rag import ...`。修改导入前先确认脚本的实际运行方式

### md2epub（MinerU → EPUB3 转换器）
子项目有独立 README（`md2epub/README.md`，含完整的使用、校准与设备兼容性记录，改动前先读）。核心结构：

- **`md2epub.py` 是统一入口**：分析输入 → 自动生成配置 → 依次构建 MathML/SVG/PNG 三版 EPUB → 结构与内容覆盖验收 → 原子替换成品。`convert.py` 是旧版底层入口，仅配合 `book_config_xinjiang_v1~v3.json` 复现已校准的书；新书一律用 `md2epub.py`
- **模块分工**：`analyze_book.py`（MinerU 输出自动分析、标题恢复）→ `convert.py`（预处理、公式 token 化、渲染与装配，约 2200 行，核心）→ `validate.py`（EPUB 结构验收）+ `audit_png.py`（PNG 版专项审计）；`render_math.js`（node + mathjax-full 渲染 SVG）；`markdown_regions.py`（围栏/缩进/行内代码区域保护，代码不参与标题与公式识别）
- **配置级联**：`book_config.template.json` → 自动识别结果 → `--config` 覆盖。含 `chapter_regex` 的旧版完整配置走兼容流程
- **所有公式一律外链文件引用**（`<img src="../media/eq-*.{svg,png}"/>`，XHTML 中零内联 `<svg>`、零 `data:` URI）——这是跨格式（Kindle azw3 转换等）存活的关键设计，不能改回内联
- **缓存**：`math_cache.json`（MathJax 渲染缓存）与 `png_cache/`（不可变原始位图）跨运行复用；修改栅格密度相关常量（`RASTER_SCALE`/`DISPLAY_RASTER_SCALE` 等）需删除 `png_cache/` 重栅
- **并发**：输出目录用 `flock` 加锁，同一本书不可并行转换；MinerU 原始输出（`hybrid_auto/`）永不被修改
- **测试**：`md2epub/test/test_pipeline.py`（unittest，不依赖整书与外部模型）；`test/build_imgtest*.py` 是设备校准用 EPUB 生成器，不是单元测试

### 测试风格
`tests/` 用 monkeypatch 将 `sys.modules["transformers"]` 替换为假模块，再通过 `runpy.run_path()` 执行被测脚本，不下载真实模型。为模型加载脚本写测试时沿用此模式。

## 注意事项

- 仓库根的 `.python-version`（3.12）与实际 `.venv`（3.13.11）不一致，以 `.venv` 为准
- `.gitignore` 忽略了 `tests/` 和 `test_assets/`（且 `tests/` 模式会匹配任意层级同名目录）——因此 `tests/test_phi3_mini.py` 实际**未被 git 跟踪**，md2epub 的测试放在 `test/`（单数）目录才得以跟踪
- `package.json` 是空占位文件，本仓库不是 Node 项目；`md2epub/package.json` 是真实的（mathjax-full 依赖）
- `hollm/` 在 git 中为 untracked 状态，属于外部书籍仓库副本
- md2epub 的构建产物（EPUB、`md2epub-build/`）输出到书籍目录（仓库外），不要把它们提交进来
