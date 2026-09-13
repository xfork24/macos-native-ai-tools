# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概览

本仓库包含两个独立部分：

1. **本地 AI 工具集（`src/`）**：运行在 Apple Silicon Mac 上的独立 CLI 脚本，按推理后端分三类：
   - **Ollama**（服务位于 localhost:11434）：`ocr.py`、`translate.py`、`analyze_image.py`、`chat.py`
   - **MLX**（mlx-audio）：`asr.py`、`tts.py`
   - **PyTorch/Diffusers**（MPS 加速）：`sd15.py`（Stable Diffusion 1.5）
   - 另有模型实验脚本（`phi3-mini.py`、`qwen38.py`、`qwopus35.py`、根目录 `embedding.py`）和 RAG 实验族（见下文）
2. **`hollm/`**：《Hands-On Large Language Models》中文版的 vendored 副本（**未纳入 git 跟踪**）。notebook 设计在远程 GPU 平台（openbayes/Colab）上运行，拥有独立的 `pyproject.toml`/`uv.lock` 环境，不要在本地运行或将其改动提交到本仓库。

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

# Lint
ruff check src/ tests/
```

Ollama 脚本依赖本机 Ollama 服务（`ollama serve`）和已拉取的模型：`glm-ocr:bf16`、`translategemma:12b`、`qwen3-vl:8b`、`Qwen3:8b`。MLX 模型首次使用时自动下载。

## 架构

### CLI 脚本约定
- 每个功能一个独立 argparse 脚本；通用参数：`-v/--verbose`、`-o/--output`、`-m/--model`，Ollama 脚本另有 `--host`、`--timeout`
- 共享工具集中在 `src/utils.py`：`setup_logger`、`resolve_path`、`validate_input_file`、`ensure_output_dir`、`image_to_base64`、`read_input_text`、`detect_chinese_text`

### OllamaClient（src/utils.py）
所有 Ollama 调用都经过这个包装类，并且**所有请求强制 `think=False`**（禁用 Qwen3 思考模式）。新增 Ollama 功能时复用它，不要直接 `import ollama`。

### pipeline.py（多步骤编排）
`pipeline.py` 通过 `from src.ocr import main as ocr_main` 导入各脚本的 `main()`，用 contextlib 重定向 stdout 来串联 OCR → 翻译 → TTS 语音克隆。新增 pipeline 步骤时沿用此模式。

### RAG 实验族（src/ 下多个相似命名文件）
- `rag.py`：混合检索演示 —— BM25（rank_bm25）字面召回 + SentenceTransformer 语义召回 + CrossEncoder 重排
- `qwen3_rag.py` / `qwen3_bm25_rag.py` / `qwen3_rag_pipeline.py` / `qwen3_rag_pipeline_vlm.py`：基于 mlx-lm 的 Qwen3 RAG 变体（本地 4-bit 模型、流式生成）
- 注意导入风格不一致：`pipeline.py` 用 `from src.utils import ...`，而 `qwen3_rag_pipeline.py` 用裸导入 `from qwen3_bm25_rag import ...`。修改导入前先确认脚本的实际运行方式

### 测试风格
`tests/` 用 monkeypatch 将 `sys.modules["transformers"]` 替换为假模块，再通过 `runpy.run_path()` 执行被测脚本，不下载真实模型。为模型加载脚本写测试时沿用此模式。

## 注意事项

- 仓库根的 `.python-version`（3.12）与实际 `.venv`（3.13.11）不一致，以 `.venv` 为准
- `package.json` 是空占位文件，本仓库不是 Node 项目
- `hollm/` 在 git 中为 untracked 状态，属于外部书籍仓库副本
