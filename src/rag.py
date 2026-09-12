import numpy as np
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer, CrossEncoder

# ==========================================
# 1. 准备测试文档库与模拟预处理
# ==========================================
documents = [
    "K-V Cache（键值缓存）是一种在 LLM 文本生成过程中牺牲内存换取解码时间的优化技术。",
    "Phi-3 是微软推出的轻量级开源大语言模型，采用 32 层 Decoder-Only 串行架构。",
    "BM25 是一种基于词频和逆文档频率（TF-IDF 演进）的经典文本检索算法，擅长字面精确匹配。",
    "逻辑回归 (Logistic Regression) 是一种通过 Sigmoid 函数将线性输出映射为概率的二分类模型。",
    "在 RAG 系统中，混合检索（Hybrid Search）结合了 BM25 的字面召回与 Embedding 的语义召回。",
    "Transformers 库的 generate 方法在同时传入 max_new_tokens 和 max_length 时会抛出警告。",
]

# BM25 需要按词切分文本（英文用 split，中文简单字/词切分）
tokenized_corpus = [doc.replace(" ", "") for doc in documents]  # 中文按字切分简易处理
tokenized_corpus_words = [[char for char in doc] for doc in tokenized_corpus]

# ==========================================
# 2. 初始化检索模型与嵌入索引
# ==========================================
print("正在加载检索模型...")

# 2.1 词法检索：初始化 BM25
bm25 = BM25Okapi(tokenized_corpus_words)

# 2.2 语义检索：初始化轻量 Embedding 模型并生成文档向量
embedding_model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
doc_embeddings = embedding_model.encode(documents, normalize_embeddings=True)

# 2.3 精细重排：初始化 Reranker (Cross-Encoder) 模型
reranker = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")


# ==========================================
# 3. 混合检索 + Reranker 重排序主逻辑
# ==========================================
def hybrid_search_with_rerank(query: str, top_k_recall: int = 4, top_k_final: int = 2):
    print(f"\n================ 用户查询: '{query}' ================")

    # --------------------------------------------------
    # 步骤 A：多路召回（第一阶段 - 召回）
    # --------------------------------------------------
    # 1. BM25 召回
    tokenized_query = [char for char in query.replace(" ", "")]
    bm25_scores = bm25.get_scores(tokenized_query)
    bm25_top_indices = np.argsort(bm25_scores)[::-1][:top_k_recall]

    # 2. Embedding 向量召回
    query_embedding = embedding_model.encode(query, normalize_embeddings=True)
    cosine_scores = np.dot(doc_embeddings, query_embedding)
    vector_top_indices = np.argsort(cosine_scores)[::-1][:top_k_recall]

    # 3. 合并两路召回结果（取并集去重）
    recalled_indices = list(set(bm25_top_indices).union(set(vector_top_indices)))
    recalled_docs = [documents[i] for i in recalled_indices]

    print(f"\n[第一阶段] 多路召回结果 (共 {len(recalled_docs)} 条候选文档):")
    for doc in recalled_docs:
        print(f"  - {doc}")

    # --------------------------------------------------
    # 步骤 B：Cross-Encoder 交叉重排（第二阶段 - 重排）
    # --------------------------------------------------
    # 构建 Query 与候选文档的配对: [[Query, Doc1], [Query, Doc2], ...]
    rerank_pairs = [[query, doc] for doc in recalled_docs]

    # Reranker 交叉打分
    rerank_scores = reranker.predict(rerank_pairs)

    # 按照 Reranker 分数排序
    ranked_results = sorted(
        zip(recalled_docs, rerank_scores),
        key=lambda x: x[1],
        reverse=True
    )

    # --------------------------------------------------
    # 步骤 C：截取最终 Top-K 送给 LLM
    # --------------------------------------------------
    final_context = ranked_results[:top_k_final]

    print(f"\n[第二阶段] Reranker 精排最终结果 (选出 Top {top_k_final}):")
    for rank, (doc, score) in enumerate(final_context, start=1):
        print(f"  {rank}. [得分: {score:.4f}] {doc}")

    return final_context


# ==========================================
# 4. 运行验证
# ==========================================
if __name__ == "__main__":
    # 测试 1: 侧重精准词法匹配的查询
    hybrid_search_with_rerank("BM25 算法的核心逻辑是什么？")

    # 测试 2: 侧重隐式语义理解的查询
    hybrid_search_with_rerank("如何避免模型推理解码时占用过多的运行内存？")