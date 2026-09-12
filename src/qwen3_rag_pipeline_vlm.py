import os
import sys
import time
from typing import Any, Dict, List, Optional
import requests
from PIL import Image, ImageDraw, ImageFont

from mlx_vlm import load, stream_generate
from mlx_vlm.prompt_utils import apply_chat_template

def prepare_image(image_input: Optional[str]) -> Optional[str]:
    """处理并校验图片路径/URL"""
    if not image_input:
        return None

    # 如果是网络图片，下载到本地临时文件
    if image_input.startswith(("http://", "https://")):
        try:
            os.makedirs("temp_cache", exist_ok=True)
            local_filename = os.path.join("temp_cache", os.path.basename(image_input.split("?")[0]))
            if not os.path.exists(local_filename):
                print(f"📥 正在下载测试图片: {image_input}...")
                resp = requests.get(image_input, timeout=10)
                with open(local_filename, "wb") as f:
                    f.write(resp.content)
            return local_filename
        except Exception as e:
            print(f"⚠️ 图片下载失败 ({e})，降级为纯文本模式")
            return None

    # 如果是本地路径且存在，直接返回
    if os.path.exists(image_input):
        return image_input

    print(f"⚠️ 未找到路径为 '{image_input}' 的图片文件，降级为纯文本模式。")
    return None


def create_dummy_rag_diagram(filepath: str):
    """当本地没有实际图片时，生成一张简单的 RAG 架构测试图"""
    img = Image.new("RGB", (600, 300), color=(240, 240, 240))
    draw = ImageDraw.Draw(img)
    # 画几个代表架构阶段的框
    draw.rectangle([50, 100, 200, 200], fill=(70, 130, 180), outline=(0, 0, 0))
    draw.text((70, 140), "Multi-Recall\n(BM25 + Dense)", fill=(255, 255, 255))

    draw.rectangle([250, 100, 400, 200], fill=(60, 179, 113), outline=(0, 0, 0))
    draw.text((280, 140), "Reranker\n(Qwen3-Rerank)", fill=(255, 255, 255))

    draw.rectangle([450, 100, 580, 200], fill=(220, 20, 60), outline=(0, 0, 0))
    draw.text((480, 140), "Generator\n(VLM)", fill=(255, 255, 255))

    draw.line([(200, 150), (250, 150)], fill=(0, 0, 0), width=3)
    draw.line([(400, 150), (450, 150)], fill=(0, 0, 0), width=3)

    img.save(filepath)
    print(f"🎨 已为您自动生成测试架构图: {filepath}")


class MLXVisionLLMStreamer:

    def __init__(self, model_path: str):
        resolved_path = os.path.expanduser(model_path)
        print(f"[LLM] 正在加载多模态 4-Bit 模型: {resolved_path}...")
        self.model, self.processor = load(resolved_path)
        print("[LLM] 多模态模型与 Processor 加载完毕！\n")

    def build_prompt(
            self, query: str, top_docs: List[Dict[str, Any]], has_image: bool = False
    ) -> str:
        context_str = ""
        for i, doc in enumerate(top_docs, 1):
            text = doc.get("text", doc.get("content", ""))
            context_str += f"[{i}] {text}\n"

        messages = [
            {
                "role": "system",
                "content": (
                    "你是一个精准的知识库问答助手。"
                    "请结合提供的【参考上下文】以及用户传入的图片（如有）回答问题。"
                    "若信息不足，请明确说明缺失部分，严禁捏造事实。"
                ),
            },
            {
                "role": "user",
                "content": f"【参考上下文】：\n{context_str}\n【问题】：{query}",
            },
        ]

        return apply_chat_template(
            self.processor,
            config=self.model.config,
            prompt=messages,
            num_images=1 if has_image else 0,
        )

    def generate_stream(
            self,
            query: str,
            top_docs: List[Dict[str, Any]],
            image_path: Optional[str] = None,
            max_tokens: int = 512,
            temperature: float = 0.0,
    ):
        valid_image_path = prepare_image(image_path)
        has_image = valid_image_path is not None

        prompt = self.build_prompt(query, top_docs, has_image=has_image)

        print(f"🤖 [Qwen3.5-9B-VLM 响应]: ", end="", flush=True)
        start_time = time.time()

        for response in stream_generate(
                model=self.model,
                processor=self.processor,
                prompt=prompt,
                image=valid_image_path if has_image else None,
                max_tokens=max_tokens,
                temp=temperature,
        ):
            text = getattr(response, "text", str(response))
            print(text, end="", flush=True)

        elapsed = time.time() - start_time
        print(f"\n⏱️ [完成，耗时: {elapsed:.2f}s]")
        print("-" * 60)


if __name__ == "__main__":
    MODEL_PATH = "andrevp/Qwen3.5-9B-Distilled-OPUS-Heretic-MLX-VLM-4bit"
    llm = MLXVisionLLMStreamer(model_path=MODEL_PATH)

    # 创建测试目录与 dummy 图
    os.makedirs("test_assets", exist_ok=True)
    dummy_img_path = "test_assets/rag_architecture.png"
    if not os.path.exists(dummy_img_path):
        create_dummy_rag_diagram(dummy_img_path)

    test_cases = [
        {
            "query": "QW3-VL-2B 支持哪些特性？",
            "image_path": None,
            "docs": [
                {"id": 1, "text": "型号为 QW3-VL-2B 的多模态模型支持图文交织输入与 Matryoshka 维度裁切。"},
            ],
        },
        {
            "query": "请结合架构图，说明多路召回后下一步会经过哪个组件？",
            "image_path": dummy_img_path,  # 传入真实创建的测试图片
            "docs": [
                {"id": 2, "text": "架构图中包含 Multi-Recall、Reranker 和 Generator 三个阶段。"},
            ],
        },
    ]

    for case in test_cases:
        print(f"\n================ 检索 Query: '{case['query']}' ================")
        llm.generate_stream(
            query=case["query"],
            top_docs=case["docs"],
            image_path=case["image_path"]
        )