# MLX AI 工具集

一套整合本地部署 AI 模型的 Python 脚本工具，提供 OCR、翻译、图片分析、LLM 聊天、ASR 和 TTS 六大功能。

## 功能概览

| 功能 | 脚本 | 模型 | 框架 |
|------|------|------|------|
| OCR 识别 | `src/ocr.py` | glm-ocr:bf16 | Ollama |
| 翻译 | `src/translate.py` | translategemma:12b | Ollama |
| 图片分析 | `src/analyze_image.py` | qwen3-vl:8b | Ollama |
| LLM 聊天 | `src/chat.py` | Qwen3:8b | Ollama |
| 语音转文字 | `src/asr.py` | Qwen3-ASR-1.7B-8bit | MLX |
| 文字转语音 | `src/tts.py` | Qwen3-TTS-1.7B | MLX |

## 环境要求

- Python 3.13.11
- MLX Audio 0.3.1
- Ollama 服务运行在 localhost:11434

## 安装

### 1. 激活虚拟环境

```bash
cd /Users/hengliu/workspace/mlx
source .venv/bin/activate
```

### 2. 安装依赖

```bash
uv pip install -r requirements.txt
```

## 使用方法

### OCR 识别

识别图片中的文本：

```bash
# 基本用法
python src/ocr.py input/image.jpg

# 保存结果
python src/ocr.py input/image.jpg -o output/text.txt

# 指定模型
python src/ocr.py input/image.jpg -m glm-ocr:bf16
```

### 翻译

中英互译，支持自动语言检测：

```bash
# 翻译文本（自动检测目标语言）
python src/translate.py "Hello, world!"

# 翻译文件
python src/translate.py input.txt -o output.txt

# 指定目标语言
python src/translate.py "Hello" --target zh
python src/translate.py "你好" --target en
```

### 图片分析

使用视觉模型分析图片内容：

```bash
# 描述图片
python src/analyze_image.py input/image.jpg -p "描述这张图片"

# 询问具体问题
python src/analyze_image.py input/image.jpg -p "图片中有几个人？"

# 保存结果
python src/analyze_image.py input/image.jpg -p "详细分析这张图片" -o result.txt
```

### LLM 聊天

与 AI 模型进行对话：

```bash
# 单次提问
python src/chat.py "解释什么是机器学习"

# 交互式对话
python src/chat.py -i

# 交互式对话并保存历史
python src/chat.py -i -s chat_history.json

# 使用对话历史
python src/chat.py "继续刚才的话题" -c history.json -s history.json
```

交互模式快捷命令：
- `/quit` 或 `/q` - 退出对话
- `/clear` - 清空对话历史
- `/history` - 查看对话历史

### 语音转文字 (ASR)

将音频文件转录为文字：

```bash
# 基本转录
python src/asr.py input/audio.wav

# 保存结果
python src/asr.py input/audio.wav -o output/transcript.txt

# 指定输出目录
python src/asr.py input/audio.wav --output-dir ./output
```

### 文字转语音 (TTS)

将文字转换为语音，支持两种模式：

#### 预设音色模式（默认）

```bash
# 中文语音
python src/tts.py "你好，世界！" -v Vivian -l cn

# 英文语音
python src/tts.py "Hello, world!" -v Aiden -l en -s 1.2

# 从文件读取
python src/tts.py input.txt -v Vivian -o my_output
```

预设音色参数：
- `-v/--voice`: 音色名称（Vivian、Aiden 等）
- `-l/--lang-code`: 语言代码（cn/en/auto）
- `-s/--speed`: 语速（0.5-3.0，默认 1.0）
- `--instruct`: 合成指令

#### 语音克隆模式

```bash
# 基本用法
python src/tts.py "这是语音克隆测试" --clone --ref-audio ref.wav --ref-text "参考文本"

# 从文件读取
python src/tts.py input.txt --clone --ref-audio ref.wav --ref-text "参考文本" -o clone_output
```

语音克隆参数：
- `--clone`: 启用语音克隆模式
- `--ref-audio`: 参考音频文件路径
- `--ref-text`: 参考文本（必须与参考音频内容匹配）

## 项目结构

```
/Users/hengliu/workspace/mlx/
├── .venv/                    # 虚拟环境（Python 3.13.11）
├── src/                      # 源代码目录
│   ├── __init__.py          # 包初始化文件
│   ├── utils.py             # 通用工具模块
│   ├── ocr.py               # OCR 识别脚本
│   ├── translate.py         # 翻译脚本
│   ├── analyze_image.py     # 图片分析脚本
│   ├── chat.py              # LLM 聊天脚本
│   ├── asr.py               # 语音转文字脚本
│   └── tts.py               # 文字转语音脚本
├── input/                    # 输入文件目录
├── output/                   # 输出文件目录
├── backup/                   # 备份和示例代码
├── requirements.txt          # Python 依赖列表
└── README.md                 # 本文件
```

## 通用参数

所有脚本都支持以下参数：

- `-v, --verbose`: 启用详细输出模式
- `-h, --help`: 显示帮助信息

Ollama 相关脚本（OCR、翻译、图片分析、聊天）支持：
- `--host`: Ollama 服务地址（默认 http://localhost:11434）
- `--timeout`: 请求超时时间（默认 120 秒）
- `-m, --model`: 模型名称

MLX 相关脚本（ASR、TTS）支持：
- `-m, --model`: MLX 模型名称
- `--output-dir`: 输出目录

## 依赖说明

- **ollama** (>=0.4.0): Ollama Python 客户端，用于与 Ollama 服务通信
- **mlx-audio** (==0.3.1): MLX Audio 框架，用于 ASR 和 TTS 功能
- **Pillow** (>=10.0.0): 图像处理库，用于 OCR 和图片分析

## 故障排除

### Ollama 服务未运行

如果看到 "Failed to connect to Ollama service" 错误：

```bash
# 启动 Ollama 服务
ollama serve
```

### 模型未下载

如果看到 "Model not found" 错误：

```bash
# 下载 Ollama 模型
ollama pull glm-ocr:bf16
ollama pull translategemma:12b
ollama pull qwen3-vl:8b
ollama pull Qwen3:8b
```

MLX 模型会在首次使用时自动下载。

### 虚拟环境问题

如果遇到依赖问题：

```bash
# 重新安装依赖
uv pip install -r requirements.txt --force-reinstall
```

## 开发参考

- OCR 示例：`backup/qwen3-asr.py`
- TTS 预设音色示例：`backup/qwen3-tts-custom-voice.py`
- TTS 语音克隆示例：`backup/qwen3-tts-voice-clone.py`

## 许可证

本项目仅供学习和研究使用。
