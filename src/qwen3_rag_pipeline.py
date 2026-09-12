import os
import sys
from typing import Any, Dict, List
from mlx_lm.sample_utils import make_sampler

# MLX 原生流式生成接口
from mlx_lm import load, stream_generate

# 从你已有的模块中导入检索组件（请根据实际文件名微调导入路径）
try:
    from qwen3_bm25_rag import get_reranked_top_k
except ImportError:
    # 假设 get_reranked_top_k 是先前写好的检索与精排逻辑函数
    pass


class MLXLLMStreamer:

    def __init__(self, model_path: str):
        resolved_path = os.path.expanduser(model_path)
        print(f"[LLM] 正在从 {resolved_path} 加载 MLX 4-Bit 模型...")
        self.model, self.tokenizer = load(resolved_path)
        print("[LLM] 模型加载完毕！\n")

    def build_prompt(self, query: str, top_docs: List[Dict[str, Any]]) -> str:
        """组装带 Context 的 System / User 结构化 Prompt"""
        context_str = ""
        for i, doc in enumerate(top_docs, 1):
            text = doc.get("text", doc.get("content", ""))
            context_str += f"[{i}] {text}\n"

        messages = [
            {
                "role": "system",
                "content": (
                    "你是一个精准的知识库问答助手。"
                    "请仅根据提供的【参考上下文】回答用户的问题。"
                    "如果上下文不足以回答问题，请直接回答‘根据已知信息无法回答’，严禁捏造事实。"
                ),
            },
            {
                "role": "user",
                "content": f"【参考上下文】：\n{context_str}\n【问题】：{query}",
            },
        ]

        return self.tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )

    def generate_stream(
        self,
        query: str,
        top_docs: List[Dict[str, Any]],
        max_tokens: int = 512,
        temperature: float = 0.0,
    ):
        """调用 stream_generate 逐 Token 输出结果"""
        prompt = self.build_prompt(query, top_docs)

        print(f"🤖 [Qwen3.8-9B 响应]: ", end="", flush=True)

        sampler = make_sampler(temp=temperature)

        for response in stream_generate(
                model=self.model,
                tokenizer=self.tokenizer,
                prompt=prompt,
                max_tokens=max_tokens,
                sampler=sampler,
        ):
            print(response.text, end="", flush=True)


if __name__ == "__main__":
    # 1. 设置 MLX 4-bit 本地模型路径
    # 如果放在 HuggingFace 默认 cache 中，使用实际 snapshot 路径，例如:
    # MODEL_PATH = "~/.cache/huggingface/hub/models--keXjos--Qwen3.8-9B-mlx-4Bit/snapshots/xxxx"
    MODEL_PATH = "keXjos/Qwen3.8-9B-mlx-4Bit"

    llm = MLXLLMStreamer(model_path=MODEL_PATH)

    # 2. 模拟测试 Query 与检索 Top-K 上下文（实际运行接入你的 get_reranked_top_k）
    test_cases = [
        {
            "query": "QW3-VL-2B 支持哪些特性？",
            "docs": [
                {
                    "id": 4,
                    "text": "型号为 QW3-VL-2B 的多模态模型支持图文交织输入与 Matryoshka 维度裁切。",
                },
                {
                    "id": 2,
                    "text": "下图展示了现代 RAG 系统架构，包含多路召回与 Rerank 阶段。",
                },
            ],
        },
        {
            "query": "如何理解 RAG 中的多路召回架构图？",
            "docs": [
                {
                    "id": 2,
                    "text": "下图展示了现代 RAG 系统架构，包含多路召回与 Rerank 阶段。",
                },
                {
                    "id": 3,
                    "text": "BM25 算法依赖词频（TF）和逆文档频率（IDF），擅长精确字面匹配。",
                },
            ],
        },
    ]

    # 3. 运行流式问答
    for case in test_cases:
        q = case["query"]
        docs = case["docs"]

        print(f"\n================ 检索 Query: '{q}' ================")
        llm.generate_stream(query=q, top_docs=docs)