import math
import threading
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

# 进程级共享的 embedding 模型缓存：不同 SimpleRAG 实例（不同指标/不同任务）复用同一模型，
# 避免每次构造实例都重新加载一遍。
EMBEDDING_MODEL_NAME = 'tomaarsen/static-retrieval-mrl-en-v1'
_MODEL_CACHE: Dict[str, SentenceTransformer] = {}
_MODEL_CACHE_LOCK = threading.Lock()


def _get_shared_model(model_name: str) -> SentenceTransformer:
    model = _MODEL_CACHE.get(model_name)
    if model is None:
        with _MODEL_CACHE_LOCK:
            model = _MODEL_CACHE.get(model_name)
            if model is None:
                logger.info(f'[SimpleRAG] loading embedding model "{model_name}" (shared per process)')
                model = SentenceTransformer(model_name)
                _MODEL_CACHE[model_name] = model
    return model


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
        # 复用进程级共享模型：每个实例不再重复加载 embedding 模型
        self.model = _get_shared_model(EMBEDDING_MODEL_NAME)
        # 向量缓存：相同文本直接命中，避免重复编码（faiss 索引仍保持任务内隔离）
        self._embedding_cache: Dict[str, np.ndarray] = {}
        self._device: Any = None

    def _resolve_device(self) -> str:
        if self._device is None:
            if self._use_gpu and torch.cuda.is_available():
                self._device = "cuda:0"
                logger.info('your device support cuda, use gpu')
            else:
                self._device = "cpu"
                logger.info('your device don\'t support cuda, use cpu')
        return self._device

    def _encode_in_batches(self, docs: List[str], batch_size: int = 32) -> np.ndarray:
        device = self._resolve_device()
        embeddings = []
        for i in range(0, len(docs), batch_size):
            # 获取当前批次的数据
            batch_docs = docs[i:i + batch_size]
            # 对当前批次进行编码
            batch_embeddings = self._encode(batch_docs, device=device)
            # 将当前批次的结果添加到结果列表中
            embeddings.append(batch_embeddings)
            logger.debug(f'[SimpleRAG] encode batch {i // batch_size + 1}/{len(docs) // batch_size + 1}')
        # 将所有批次的结果合并为一个numpy数组
        return np.concatenate(embeddings, axis=0)

    def _encode(self, docs: List[str], device: Any) -> np.ndarray:
        # 相同文本直接复用缓存向量（例如重复查询、重复文档）
        missing = [doc for doc in docs if doc not in self._embedding_cache]
        if missing:
            new_embeddings = self.model.encode(missing, device=device, convert_to_numpy=True)
            for doc, embedding in zip(missing, new_embeddings):
                self._embedding_cache[doc] = embedding
        return np.stack([self._embedding_cache[doc] for doc in docs], axis=0)

    def add(self, docs: List[str]):
        self._index.add(self._encode_in_batches(docs))
        logger.info(f'[SimpleRAG] add {len(docs)} docs to index')

    def query(self, query: str, k=3) -> List[int]:
        query_embedding = self._encode([query], device=self._resolve_device())
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

    def density_cluster(
        self,
        docs: List[str],
        use_umap: bool = False,
        n_components: int = 50,
        min_cluster_size: int = 5
    ) -> List[List[int]]:
        """
        使用降维 + 密度聚类的方法对文档进行聚类
        
        Args:
            docs: 文档列表
            use_umap: 是否使用 UMAP 降维（否则使用 t-SNE）
            n_components: 降维后的维度
            min_cluster_size: HDBSCAN 的最小簇大小
            
        Returns:
            聚类结果：List[List[原始索引]]
        """
        if len(docs) == 0:
            return []
        if len(docs) == 1:
            return [[0]]
            
        # 1. 编码文档
        try:
            embeddings = self._encode_in_batches(docs).astype(np.float32)
        except Exception as e:
            logger.error(f"[SimpleRAG] Encoding failed: {e}")
            return [[i] for i in range(len(docs))]
        
        n_samples = len(embeddings)
        
        # 2. 降维
        logger.info(f"[SimpleRAG] Reducing dimensions from {embeddings.shape[1]} to {n_components}...")
        
        try:
            if use_umap:
                try:
                    import umap
                    reducer = umap.UMAP(n_components=n_components, random_state=42, n_jobs=-1)
                    reduced_embeddings = reducer.fit_transform(embeddings)
                    logger.info("[SimpleRAG] UMAP reduction completed")
                except ImportError:
                    logger.warning("[SimpleRAG] UMAP not found, falling back to t-SNE")
                    use_umap = False
            
            if not use_umap:
                from sklearn.manifold import TSNE
                reducer = TSNE(n_components=min(n_components, n_samples - 1), random_state=42, n_jobs=-1)
                reduced_embeddings = reducer.fit_transform(embeddings)
                logger.info("[SimpleRAG] t-SNE reduction completed")
                
        except Exception as e:
            logger.error(f"[SimpleRAG] Dimensionality reduction failed: {e}, using original embeddings")
            reduced_embeddings = embeddings
        
        # 3. HDBSCAN 聚类
        logger.info(f"[SimpleRAG] Clustering {n_samples} samples with HDBSCAN...")
        
        try:
            # 优先尝试 sklearn 的 HDBSCAN (v1.3+)
            from sklearn.cluster import HDBSCAN
            clusterer = HDBSCAN(min_cluster_size=min(min_cluster_size, n_samples), min_samples=1)
            labels = clusterer.fit_predict(reduced_embeddings)
            logger.info("[SimpleRAG] Using sklearn.cluster.HDBSCAN")
        except ImportError:
            try:
                # 尝试 hdbscan 独立库
                import hdbscan
                clusterer = hdbscan.HDBSCAN(min_cluster_size=min(min_cluster_size, n_samples), min_samples=1)
                labels = clusterer.fit_predict(reduced_embeddings)
                logger.info("[SimpleRAG] Using hdbscan library")
            except ImportError:
                logger.error("[SimpleRAG] HDBSCAN not available, falling back to single cluster")
                return [list(range(n_samples))]
        
        # 4. 整理聚类结果
        clusters_dict = defaultdict(list)
        noise_points = []
        
        for i, label in enumerate(labels):
            if label == -1:
                # 噪声点收集到一起
                noise_points.append(i)
            else:
                clusters_dict[label].append(i)
        
        # 将所有噪声点合并为一个簇（如果有的话）
        clusters = list(clusters_dict.values())
        if noise_points:
            clusters.append(noise_points)
            logger.info(f"[SimpleRAG] Merged {len(noise_points)} noise points into one cluster")
        
        logger.info(f"[SimpleRAG] Density clustering completed: {len(clusters)} clusters")
        
        return [c for c in clusters if c]

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
