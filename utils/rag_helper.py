import math
from collections import defaultdict
from typing import List, Dict, Any

import faiss
import numpy as np
import torch
from loguru import logger

from utils.settings import RagSettings

from sentence_transformers import SentenceTransformer

# from sklearn.cluster import DBSCAN
# 简单的RAG实现
class SimpleRAG:
    def __init__(self, setting: RagSettings):
        self._index = faiss.IndexFlatL2(setting.dim)
        self._dim = setting.dim
        self._embeddings = []
        self._tokenizer = setting.tokenizer
        self._model = setting.model
        self._model.eval()
        self._use_gpu = setting.use_gpu
        self.device = None

        self.model = SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')

    def _encode_in_batches(self, docs: List[str], batch_size: int = 32) -> np.ndarray:
        if self._use_gpu and torch.cuda.is_available():
             self.device = "cuda:0"
             logger.info('your device support cuda, use gpu')
        else:
            self.device ="cpu"
            logger.info('your device don\'t support cuda, use cpu')
        embeddings = []
        for i in range(0, len(docs), batch_size):
            # 获取当前批次的数据
            batch_docs = docs[i:i + batch_size]
            # 对当前批次进行编码
            batch_embeddings = self._encode(batch_docs, device=self.device)
            # 将当前批次的结果添加到结果列表中
            embeddings.append(batch_embeddings)
            logger.debug(f'[SimpleRAG] encode batch {i // batch_size + 1}/{len(docs) // batch_size + 1}')
        # 将所有批次的结果合并为一个numpy数组
        return np.concatenate(embeddings, axis=0)

    def _encode(self, docs: List[str], device:Any) -> np.ndarray:
        return self.model.encode(docs, device=device, convert_to_numpy=True)
        encoded_input = self._tokenizer(docs, padding=True, truncation=True, return_tensors='pt',
                                        max_length=self._model.config.max_position_embeddings).to(device)
        for i, (input_ids, attention_mask) in enumerate(
                zip(encoded_input['input_ids'], encoded_input['attention_mask'])):
            real_token_count = attention_mask.sum().item()  # 计算非padding部分的token总数
            logger.debug(f'[JsonRAG] tokenized, length of input {i + 1} is {real_token_count}')
        with torch.no_grad():
            model_output = self._model(**encoded_input)
            self._model = self._model.to(device)
        text_embedding = model_output.last_hidden_state.mean(dim=1).cpu().detach().numpy()
        return text_embedding

    def add(self, docs: List[str]):
        self._index.add(self._encode_in_batches(docs))
        logger.info(f'[SimpleRAG] add {len(docs)} docs to index')

    def query(self, query: str, k=3) -> List[int]:
        query_embedding = self._encode([query], device=self.device)
        D, I = self._index.search(query_embedding, k)
        for i, (d, j) in enumerate(zip(D[0], I[0])):
            logger.debug(f'[SimpleRAG] similarity rank {i + 1}, distance: {d:.2f}, index: {j}')
        logger.info(f'[SimpleRAG] query finished')
        return I[0]

    def kmeans(self, docs: List[str], max_per_cluster: int = 50, _indices: List[int] = None) -> List[List[int]]:
        if _indices is None:
            _indices = list(range(len(docs)))
        embeddings = self._encode_in_batches(docs).astype(np.float32)
        n_clusters = max(min(int(math.sqrt(len(docs)) * 1.6), len(docs)), 1)
        kmeans = faiss.Kmeans(self._dim, n_clusters, niter=20, verbose=True)
        kmeans.train(embeddings)
        D, I = kmeans.index.search(embeddings, 1)
        x: Dict[int, List[int]] = defaultdict(list)
        for i in range(len(I)):
            x[I[i][0]].append(i)
        clusters = []
        for group in x.values():
            if max_per_cluster is not None and len(group) > max_per_cluster:
                # 递归对子簇再聚类
                sub_docs = [docs[i] for i in group]
                sub_indices = [_indices[i] for i in group]
                sub_clusters = self.kmeans(sub_docs, max_per_cluster, sub_indices)
                clusters.extend(sub_clusters)
            else:
                clusters.append([_indices[i] for i in group])
        return clusters

    # def dbscan(self, docs: List[str], eps=0.5, min_samples=5) -> List[List[int]]:
    #     # 将文档编码为向量
    #     vectors = self._encode(docs)
    #
    #     # 使用DBSCAN进行聚类
    #     clustering = DBSCAN(eps=eps, min_samples=min_samples).fit(vectors)
    #     labels = clustering.labels_
    #
    #     # 根据标签整理聚类结果
    #     clusters = {}
    #     for idx, label in enumerate(labels):
    #         if label not in clusters:
    #             clusters[label] = []
    #         clusters[label].append(idx)
    #
    #     # 转换为所需格式
    #     result = [clusters[i] for i in sorted(clusters.keys())]
    #
    #     return result
