# 这是一个真实运行的代码片段，用于生成下方的图
import matplotlib.pyplot as plt
from sentence_transformers import SentenceTransformer
from sklearn.manifold import TSNE
from sklearn.decomposition import PCA
import numpy as np

# 1. 加载真实的预训练模型 (MiniLM)
print("Loading real embedding model...")
model = SentenceTransformer('all-MiniLM-L6-v2')

# 2. 定义具有不同语义距离的词汇组
# - 动物: cat, dog, puppy
# - 科技: computer, laptop, software
# - 食物: apple, banana, burger
# - 抽象概念: galaxy, abstract
word_groups = {
    'Mammals': ['cat', 'dog', 'puppy'],
    'Tech': ['computer', 'laptop', 'software'],
    'Food': ['apple', 'banana', 'burger'],
    'Abstract': ['galaxy', 'abstract']
}

all_words = []
labels = []
for group, words in word_groups.items():
    all_words.extend(words)
    labels.extend([group] * len(words))

# 3. 真实计算 Embedding 向量 (384维)
print(f"Computing embeddings for {len(all_words)} words...")
embeddings = model.encode(all_words)

# 4. 使用 PCA 降维至 2D
print("Reducing dimensions using PCA...")
# tsne = TSNE(n_components=2, perplexity=len(all_words)-1, random_state=42, max_iter=1000)
# embeddings_2d = tsne.fit_transform(embeddings)
pca = PCA(n_components=2)
embeddings_2d = pca.fit_transform(embeddings)

# 5. 绘图
plt.figure(figsize=(10, 8))
colors = {'Mammals': 'blue', 'Tech': 'green', 'Food': 'red', 'Abstract': 'purple'}

for group, color in colors.items():
    indices = [i for i, l in enumerate(labels) if l == group]
    plt.scatter(embeddings_2d[indices, 0], embeddings_2d[indices, 1], c=color, label=group, s=100)

for i, word in enumerate(all_words):
    plt.annotate(word, (embeddings_2d[i, 0], embeddings_2d[i, 1]), xytext=(5, 2), textcoords='offset points', fontsize=12)

plt.title('2D PCA Visualisation of Real Word Embeddings (MiniLM)')
plt.legend()
plt.grid(True, linestyle='--', alpha=0.5)
plt.show()
