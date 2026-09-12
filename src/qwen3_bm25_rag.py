import jieba
import numpy as np
from rank_bm25 import BM25Okapi
from qwen3_rag import get_multimodal_embedding, compute_rerank_score


# ==========================================
# 1. RRF (Reciprocal Rank Fusion) 融合算法
# ==========================================
def reciprocal_rank_fusion(
        bm25_results: list[dict], vector_results: list[dict], k: int = 60
) -> list[dict]:
    """使用 RRF 算法平滑无缝融合两路不同量纲的排序结果

    公式: Score(doc) = sum(1 / (k + rank_i))
    """
    rrf_scores = {}
    doc_map = {}

    # 处理 BM25 召回列表
    for rank, doc in enumerate(bm25_results, start=1):
        doc_id = doc["id"]
        doc_map[doc_id] = doc
        rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + (1.0 / (k + rank))

    # 处理 向量 召回列表
    for rank, doc in enumerate(vector_results, start=1):
        doc_id = doc["id"]
        doc_map[doc_id] = doc
        rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + (1.0 / (k + rank))

    # 按 RRF 得分降序排列
    sorted_docs = sorted(
        rrf_scores.items(), key=lambda x: x[1], reverse=True
    )
    return [doc_map[doc_id] for doc_id, score in sorted_docs]


# ==========================================
# 2. 知识库建库（构建双路索引）
# ==========================================
# 模拟包含精确文本、图文、专有型号的知识库
knowledge_base = [
    {
        "id": 1,
        "content": "Phi-3 是微软推出的轻量级开源大语言模型，采用 Decoder-Only 架构。",
        "image": None,
    },
    {
        "id": 2,
        "content": "下图展示了现代 RAG 系统架构，包含多路召回与 Rerank 阶段。",
        "image": "rag_architecture.png",
    },
    {
        "id": 3,
        "content": "BM25 算法依赖词频（TF）和逆文档频率（IDF），擅长精确字面匹配。",
        "image": None,
    },
    {
        "id": 4,
        "content": "型号为 QW3-VL-2B 的多模态模型支持图文交织输入与 Matryoshka 维度裁切。",
        "image": None,
    },
]

# --- 2.1 构建 BM25 词法倒排索引 ---
corpus_tokenized = [
    list(jieba.cut(doc["content"])) for doc in knowledge_base
]
bm25_index = BM25Okapi(corpus_tokenized)

# --- 2.2 构建 Qwen3-VL 密集向量索引 ---
# (继承此前定义的 get_multimodal_embedding 函数)
vector_index = []
for doc in knowledge_base:
    vec = get_multimodal_embedding(
        text=doc["content"], image_path=doc["image"]
    )
    vector_index.append(vec)
vector_index = np.array(vector_index)


# ==========================================
# 3. 词法 + 向量双路召回检索流水线
# ==========================================
def dual_pipeline_search(query: str, top_k_recall: int = 3, top_k_final: int = 2):
    print(
        f"\n================ 检索 Query: '{query}' ================"
    )

    # --- 路 1: BM25 词法检索 ---
    query_tokens = list(jieba.cut(query))
    bm25_scores = bm25_index.get_scores(query_tokens)
    bm25_top_indices = np.argsort(bm25_scores)[::-1][:top_k_recall]
    bm25_recalled = [knowledge_base[i] for i in bm25_top_indices]

    print(
        f"\n[路 1 - BM25 召回]: {[doc['id'] for doc in bm25_recalled]}"
    )

    # --- 路 2: Qwen3-VL 密集向量检索 ---
    query_vec = get_multimodal_embedding(text=query)
    vector_scores = np.dot(vector_index, query_vec)
    vector_top_indices = np.argsort(vector_scores)[::-1][:top_k_recall]
    vector_recalled = [knowledge_base[i] for i in vector_top_indices]

    print(
        f"[路 2 - Vector 召回]: {[doc['id'] for doc in vector_recalled]}"
    )

    # --- 融合层: RRF 去重与重排 ---
    fused_candidates = reciprocal_rank_fusion(
        bm25_recalled, vector_recalled, k=60
    )
    print(
        f"[RRF 融合去重后候选集]: {[doc['id'] for doc in fused_candidates]}"
    )

    # --- 精排层: Qwen3-Reranker 交叉打分 ---
    print(f"\n[精排阶段] Qwen3-Reranker-0.6B 交叉打分中...")
    rerank_results = []
    for doc in fused_candidates:
        score = compute_rerank_score(query, doc["content"])
        rerank_results.append((doc, score))

    rerank_results.sort(key=lambda x: x[1], reverse=True)

    print(f"\n最终选中 Top {top_k_final} 输出:")
    for rank, (doc, score) in enumerate(
            rerank_results[:top_k_final], start=1
    ):
        print(
            f"  {rank}. [Rerank Score: {score:.4f}] ID {doc['id']}: {doc['content']}"
        )


# ==========================================
# 4. 测试验证
# ==========================================
if __name__ == "__main__":
    # 测试 1: 侧重专有名词/准确型号搜索 (BM25 优势)
    dual_pipeline_search("QW3-VL-2B 支持哪些特性？")

    # 测试 2: 侧重长文本语义理解与图文 (向量 优势)
    dual_pipeline_search("如何理解 RAG 中的多路召回架构图？")
