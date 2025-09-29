import math
from collections import defaultdict
from typing import List, Dict, Any

import faiss
import numpy as np
import torch
from loguru import logger

from utils.settings import RagSettings

from sentence_transformers import SentenceTransformer

from collections import defaultdict
import warnings
# from sklearn.cluster import DBSCAN
# 简单的RAG实现
class SimpleRAG:
    def __init__(self, setting: RagSettings):
        self._index = faiss.IndexFlatL2(setting.dim)
        self._dim = setting.dim
        self._embeddings = []
        #self._tokenizer = setting.tokenizer
        #self._model = setting.model
        #self._model.eval()
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

    def kmeans(
        self,
        docs: List[str],
        max_per_cluster: int = 50,
        _indices: List[int] = None,
        max_recursion_depth: int = 3,
        min_points_for_clustering: int =3,
        fallback_strategy: str = "single"  # "single" or "all"
    ) -> List[List[int]]:
        """
        层次化 K-Means 聚类，递归拆分过大的簇，直到每个簇不超过 max_per_cluster。
        
        Args:
            docs: 文档列表
            max_per_cluster: 每个簇最大文档数
            _indices: 原始索引（递归时使用）
            max_recursion_depth: 最大递归深度，防止无限递归
            min_points_for_clustering: 最小聚类点数，低于此值直接返回
            fallback_strategy: 当无法聚类时的策略
                - "single": 所有点归为一个簇
                - "all": 每个点自成一簇
        
        Returns:
            聚类结果：List[List[原始索引]]
        """
        if _indices is None:
            _indices = list(range(len(docs)))
        
        # 终止条件 1：没有文档
        if len(docs) == 0:
            return []
        
        # 终止条件 2：只有一个文档
        if len(docs) == 1:
            return [_indices]
        
        # 终止条件 3：递归太深，直接返回整个簇
        if max_recursion_depth <= 0:
            return [_indices]
        
        # 编码文档
        try:
            embeddings = self._encode_in_batches(docs).astype(np.float32)
        except Exception as e:
            warnings.warn(f"Encoding failed: {e}. Falling back to single cluster.")
            return [_indices]
        
        n_samples = len(embeddings)
        
        # 终止条件 4：样本太少，无法聚类
        if n_samples < min_points_for_clustering:
            if fallback_strategy == "single":
                return [_indices]
            else:
                return [[idx] for idx in _indices]
        
        # 计算目标簇数：确保每簇不超过 max_per_cluster
        n_clusters = max(1, min(n_samples - 1, n_samples // max(1, max_per_cluster // 2)))
        n_clusters = max(1, min(n_clusters, n_samples - 1))  # 确保 n_clusters < n_samples
        
        # 如果只够分一个簇，直接返回
        if n_clusters == 1 or n_samples <= max_per_cluster:
            return [_indices]
        
        # 使用 Faiss 进行 K-Means 聚类
        dimension = embeddings.shape[1]
        
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")  # 忽略 Faiss 的训练样本不足警告
                kmeans = faiss.Kmeans(dimension, n_clusters, niter=20, verbose=False, gpu=False)
                kmeans.train(embeddings)
            
            # 获取聚类分配
            _, I = kmeans.index.search(embeddings, 1)  # shape: (n_samples, 1)
            I = I.flatten()  # 转为一维
            
            # 处理异常：确保索引在 [0, n_clusters) 范围内
            I = np.clip(I, 0, n_clusters - 1)
            
        except (RuntimeError, ValueError, faiss.Error) as e:
            warnings.warn(f"KMeans failed: {e}. Using fallback strategy.")
            if fallback_strategy == "single":
                return [_indices]
            else:
                return [[idx] for idx in _indices]
        
        # 分组
        x: Dict[int, List[int]] = defaultdict(list)
        for i, cluster_id in enumerate(I):
            x[cluster_id].append(i)
        clusters = []
        for group in x.values():
            if not group:
                continue  # 跳过空簇

            if max_per_cluster is not None and len(group) > max_per_cluster:
                sub_docs = [docs[i] for i in group]
                sub_indices = [_indices[i] for i in group]
                sub_clusters = self.kmeans(
                    sub_docs,
                    max_per_cluster,
                    sub_indices,
                    max_recursion_depth - 1,
                    min_points_for_clustering,
                    fallback_strategy
                )
                # 过滤递归返回的空簇
                clusters.extend(sub_cluster for sub_cluster in sub_clusters if sub_cluster)
            else:
                cluster = [_indices[i] for i in group]
                if cluster:  # 理论上不会空，但保持一致性
                    clusters.append(cluster)

        # 最终过滤（可选，双重保险）
        for c in clusters:
            print(c)
        return [c for c in clusters if c and c is not []]

        # clusters = []
        # for group in x.values():
        #     if max_per_cluster is not None and len(group) > max_per_cluster:
        #         # 递归聚类子簇
        #         sub_docs = [docs[i] for i in group]
        #         sub_indices = [_indices[i] for i in group]
        #         sub_clusters = self.kmeans(
        #             sub_docs,
        #             max_per_cluster,
        #             sub_indices,
        #             max_recursion_depth - 1,
        #             min_points_for_clustering,
        #             fallback_strategy
        #         )
        #         clusters.extend(sub_clusters)
        #     else:
        #         clusters.append([_indices[i] for i in group])
        
        # return clusters

    # def kmeans(self, docs: List[str], max_per_cluster: int = 50, _indices: List[int] = None) -> List[List[int]]:
    #     if _indices is None:
    #         _indices = list(range(len(docs)))
    #     embeddings = self._encode_in_batches(docs).astype(np.float32)
    #     n_clusters = max(len(docs)/5, 1)
    #     kmeans = faiss.Kmeans(self._dim, n_clusters, niter=20, verbose=True)
    #     kmeans.train(embeddings)
    #     D, I = kmeans.index.search(embeddings, 1)
    #     x: Dict[int, List[int]] = defaultdict(list)
    #     for i in range(len(I)):
    #         x[I[i][0]].append(i)
    #     clusters = []
    #     for group in x.values():
    #         if max_per_cluster is not None and len(group) > max_per_cluster:
    #             # 递归对子簇再聚类
    #             sub_docs = [docs[i] for i in group]
    #             sub_indices = [_indices[i] for i in group]
    #             sub_clusters = self.kmeans(sub_docs, max_per_cluster, sub_indices)
    #             clusters.extend(sub_clusters)
    #         else:
    #             clusters.append([_indices[i] for i in group])
    #     return clusters


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
