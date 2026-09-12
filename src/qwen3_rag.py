import torch
import numpy as np
from PIL import Image
from transformers import AutoModel, AutoTokenizer, AutoProcessor
from transformers import AutoModelForSequenceClassification  # 确保导入了此类

# 自动选择运行设备 (优先使用 GPU/MPS，没有则使用 CPU)
device = "cuda" if torch.cuda.is_available() else ("mps" if torch.backends.mps.is_available() else "cpu")
print(f"当前运行设备: {device}")

# ==========================================
# 1. 初始化 Qwen3 检索模型族
# ==========================================
print("\n正在加载模型...")

# 1.1 纯文本 Embedding 模型 (0.6B)
text_embed_name = "Qwen/Qwen3-Embedding-0.6B"
text_tokenizer = AutoTokenizer.from_pretrained(text_embed_name, trust_remote_code=True)
text_embed_model = AutoModel.from_pretrained(text_embed_name, trust_remote_code=True).to(device)
text_embed_model.eval()

# 1.2 原生多模态 VL Embedding 模型 (2B)
vl_embed_name = "Qwen/Qwen3-VL-Embedding-2B"
vl_processor = AutoProcessor.from_pretrained(vl_embed_name, trust_remote_code=True)
vl_embed_model = AutoModel.from_pretrained(vl_embed_name, trust_remote_code=True).to(device)
vl_embed_model.eval()

# 1.3 重排序 Reranker 模型 (0.6B)
reranker_name = "Qwen/Qwen3-Reranker-0.6B"
rerank_tokenizer = AutoTokenizer.from_pretrained(
    reranker_name, trust_remote_code=True
)
rerank_model = AutoModel.from_pretrained(
    reranker_name, trust_remote_code=True
).to(device)
rerank_model.eval()

# ==========================================
# 2. 向量提取函数封装
# ==========================================
# 设定全局统一的向量空间维度 (例如 1024)
TARGET_DIM = 1024


def get_text_embedding(text: str) -> np.ndarray:
    """提取纯文本 Embedding (Qwen3-Embedding-0.6B)"""
    inputs = text_tokenizer(
        text, return_tensors="pt", truncation=True, max_length=512
    ).to(device)
    with torch.no_grad():
        outputs = text_embed_model(**inputs)
        embedding = outputs.last_hidden_state[:, -1, :]

        # 1. 统一截取到 TARGET_DIM 维度
        embedding = embedding[:, :TARGET_DIM]
        # 2. 截取后重新做 L2 归一化
        embedding = torch.nn.functional.normalize(embedding, p=2, dim=1)

    return embedding.float().cpu().numpy().squeeze()


def get_multimodal_embedding(
        text: str = None, image_path: str = None
) -> np.ndarray:
    """提取多模态 Embedding (Qwen3-VL-Embedding-2B)"""
    image = Image.open(image_path).convert("RGB") if image_path else None

    messages = []
    content = []
    if image:
        content.append({"type": "image"})
    if text:
        content.append({"type": "text", "text": text})
    messages.append({"role": "user", "content": content})

    prompt = vl_processor.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=False
    )

    inputs = vl_processor(
        text=[prompt],
        images=[image] if image else None,
        return_tensors="pt",
        padding=True,
    ).to(device)

    with torch.no_grad():
        outputs = vl_embed_model(**inputs)
        embedding = outputs.last_hidden_state[:, -1, :]

        # 1. 统一截取到 TARGET_DIM 维度
        embedding = embedding[:, :TARGET_DIM]
        # 2. 截取后重新做 L2 归一化
        embedding = torch.nn.functional.normalize(embedding, p=2, dim=1)

    return embedding.float().cpu().numpy().squeeze()


def compute_rerank_score(query: str, doc_text: str) -> float:
    """使用 Qwen3-Reranker-0.6B 进行带 Task Instruction 的交叉打分"""

    # 针对 Reranker 加入明确的检索指令范式，防止大模型将注意力偏向通顺的长句
    prompt = f"判断以下文档是否回答了问题。\n问题：{query}\n文档：{doc_text}"

    inputs = rerank_tokenizer(
        prompt, return_tensors="pt", truncation=True, max_length=512
    ).to(device)

    with torch.no_grad():
        outputs = rerank_model(**inputs)

        if hasattr(outputs, "logits"):
            logits = outputs.logits
        elif hasattr(outputs, "last_hidden_state"):
            logits = outputs.last_hidden_state[:, -1, :1]
        else:
            raise AttributeError("无法获取 Reranker 输出")

        score = logits.squeeze().float().cpu().item()

    return float(score)


# ==========================================
# 3. 构建测试多模态知识库
# ==========================================
# 模拟知识库包含：纯文本段落与带图片的文档页面
knowledge_base = [
    {
        "id": 1,
        "type": "text",
        "content": "Phi-3 是微软推出的轻量级开源大语言模型，采用 Decoder-Only 架构。",
        "image": None
    },
    {
        "id": 2,
        "type": "multimodal",
        "content": "下图展示了现代 RAG 系统架构，包含多路召回与 Rerank 阶段。",
        "image": "rag_architecture.png"  # 假设存在本地图片
    },
    {
        "id": 3,
        "type": "text",
        "content": "BM25 算法依赖词频（TF）和逆文档频率（IDF），擅长精确字面匹配。",
        "image": None
    }
]

# 模拟创建一个简易测试图片 (如果本地没有实际图片)
Image.new('RGB', (200, 200), color='blue').save("rag_architecture.png")

# ==========================================
# 4. 向量库建库与检索流水线
# ==========================================
print("\n[Step 1] 正在为知识库建立向量索引...")
kb_embeddings = []

for item in knowledge_base:
    if item["type"] == "text":
        # 纯文本使用 Qwen3-Embedding-0.6B 建索引
        vec = get_text_embedding(item["content"])
    else:
        # 多模态/带图文档使用 Qwen3-VL-Embedding-2B 建索引
        vec = get_multimodal_embedding(text=item["content"], image_path=item["image"])
    kb_embeddings.append(vec)

kb_embeddings = np.array(kb_embeddings)


def hybrid_multimodal_search(
        query: str, top_k_recall: int = 2, top_k_final: int = 1
):
    print(f"\n================ 用户查询: '{query}' ================")

    # 统一使用多模态 Embedding 提取提问向量，确保与多模态库在同一语义空间对齐
    query_vec = get_multimodal_embedding(text=query)

    # 计算相似度
    scores = np.dot(kb_embeddings, query_vec)

    recalled_indices = np.argsort(scores)[::-1][:top_k_recall]
    recalled_items = [knowledge_base[i] for i in recalled_indices]

    print(f"\n[第一阶段] 向量召回 Top {top_k_recall} 候选集:")
    for item in recalled_items:
        print(f"  - [ID {item['id']}] {item['content']}")

    # --- 第二阶段：精排 (Rerank) ---
    print(
        f"\n[第二阶段] 使用 Qwen3-Reranker-0.6B 进行 Cross-Encoder 交叉重排..."
    )
    rerank_results = []

    for item in recalled_items:
        score = compute_rerank_score(query, item["content"])
        rerank_results.append((item, score))

    rerank_results.sort(key=lambda x: x[1], reverse=True)

    print(f"\n最终选中 Top {top_k_final} 提交给大模型 (LLM):")
    for rank, (item, score) in enumerate(
            rerank_results[:top_k_final], start=1
    ):
        print(
            f"  {rank}. [Rerank 得分: {score:.4f}] ID {item['id']}: {item['content']}"
        )


# ==========================================
# 5. 运行验证
# ==========================================
if __name__ == "__main__":
    # 执行混合检索流程
    hybrid_multimodal_search("RAG 系统架构中的多路召回是指什么？")
