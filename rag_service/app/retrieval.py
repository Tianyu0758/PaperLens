# 文件说明：实现论文混合检索，将稠密召回与 BM25 召回组合成最终证据。
# 两路结果按排名进行 RRF 融合，再去重并重排序，返回候选文本和检索耗时。

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import Protocol

from app.bm25_index import BM25IndexManager
from app.query_expansion import ExpandedQuery, QueryExpander


class EmbeddingProvider(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class VectorSearchProvider(Protocol):
    async def dense_search(self, vector: list[float], **kwargs) -> list[dict]: ...

    async def fetch_vectors(self, chunk_ids: list[str]) -> dict[str, list[float]]: ...


class RerankerProvider(Protocol):
    async def rerank(
        self, query: str, candidates: list[dict], top_k: int = 5
    ) -> list[dict]: ...


@dataclass(slots=True)
class RetrievalResult:
    expanded_query: ExpandedQuery
    candidates: list[dict]
    timings: dict[str, int]


def reciprocal_rank_fusion(
    result_lists: list[list[dict]], *, rrf_k: int = 60, limit: int = 20
) -> list[dict]:
    fused: dict[str, dict] = {}
    for results in result_lists:
        for rank, item in enumerate(results, start=1):
            chunk_id = item["chunk_id"]
            if chunk_id not in fused:
                fused[chunk_id] = {**item, "rrf_score": 0.0}
            else:
                for key, value in item.items():
                    fused[chunk_id].setdefault(key, value)
            fused[chunk_id]["rrf_score"] += 1.0 / (rrf_k + rank)
    return sorted(
        fused.values(), key=lambda item: (-item["rrf_score"], item["chunk_id"])
    )[:limit]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def deduplicate_by_similarity(
    items: list[dict], threshold: float = 0.95
) -> list[dict]:
    kept: list[dict] = []
    for item in items:
        embedding = item.get("embedding")
        if embedding is None:
            kept.append(item)
            continue
        if any(
            other.get("embedding") is not None
            and cosine_similarity(embedding, other["embedding"]) > threshold
            for other in kept
        ):
            continue
        kept.append(item)
    return kept


class HybridRetriever:
    def __init__(
        self,
        *,
        expander: QueryExpander,
        embedding: EmbeddingProvider,
        vector_store: VectorSearchProvider,
        bm25: BM25IndexManager,
        logger: logging.Logger,
        reranker: RerankerProvider | None = None,
        rerank_top_k: int = 5,
    ) -> None:
        self.expander = expander
        self.embedding = embedding
        self.vector_store = vector_store
        self.bm25 = bm25
        self.logger = logger
        self.reranker = reranker
        self.rerank_top_k = max(1, rerank_top_k)

    async def retrieve(
        self,
        query: str,
        *,
        paper_id: str | None = None,
        section_type: str | None = None,
        dense_limit: int = 50,
        sparse_limit: int = 50,
        fused_limit: int = 20,
    ) -> RetrievalResult:
        timings: dict[str, int] = {}
        started = time.perf_counter()
        expanded = await self.expander.expand(query)
        timings["hyde_ms"] = int((time.perf_counter() - started) * 1000)

        started = time.perf_counter()
        hyde_vector = (await self.embedding.embed([expanded.hypothetical]))[0]
        dense = await self.vector_store.dense_search(
            hyde_vector,
            paper_id=paper_id,
            section_type=section_type,
            limit=dense_limit,
        )
        timings["dense_ms"] = int((time.perf_counter() - started) * 1000)

        started = time.perf_counter()
        sparse = self.bm25.search(
            expanded.translation,
            paper_id=paper_id,
            section_type=section_type,
            limit=sparse_limit,
        )
        timings["sparse_ms"] = int((time.perf_counter() - started) * 1000)

        started = time.perf_counter()
        fused = reciprocal_rank_fusion([dense, sparse], limit=fused_limit)
        missing_ids = [item["chunk_id"] for item in fused if item.get("embedding") is None]
        fetched = await self.vector_store.fetch_vectors(missing_ids)
        for item in fused:
            if item.get("embedding") is None:
                item["embedding"] = fetched.get(item["chunk_id"])
        candidates = deduplicate_by_similarity(fused)
        timings["rrf_ms"] = int((time.perf_counter() - started) * 1000)
        if self.reranker is not None and candidates:
            started = time.perf_counter()
            candidates = await self.reranker.rerank(
                query, candidates, top_k=self.rerank_top_k
            )
            timings["rerank_ms"] = int((time.perf_counter() - started) * 1000)
        self.logger.info(
            "retrieval_complete dense=%d sparse=%d fused=%d deduped=%d",
            len(dense),
            len(sparse),
            len(fused),
            len(candidates),
        )
        return RetrievalResult(expanded, candidates, timings)
