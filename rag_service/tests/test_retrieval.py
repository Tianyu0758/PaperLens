# 文件说明：验证 RRF 按名次融合、相似文本去重和混合检索阶段的组合行为。
# 使用可控的召回与重排序替身，避免模型波动影响检索逻辑测试。

import logging
from pathlib import Path

import pytest

from app.bm25_index import BM25IndexManager
from app.query_expansion import ExpandedQuery
from app.retrieval import (
    HybridRetriever,
    deduplicate_by_similarity,
    reciprocal_rank_fusion,
)


class FakeExpander:
    async def expand(self, query):
        return ExpandedQuery("vector retrieval", "hypothetical vector passage")


class FakeEmbedding:
    async def embed(self, texts):
        return [[1.0, 0.0, 0.0]]


class FakeVectorStore:
    def __init__(self):
        self.filters = None

    async def dense_search(self, vector, **kwargs):
        self.filters = kwargs
        return [
            {
                "chunk_id": "dense-only",
                "paper_id": "p1",
                "section_type": "methods",
                "text": "dense result",
                "embedding": [0.0, 1.0, 0.0],
            },
            {
                "chunk_id": "shared",
                "paper_id": "p1",
                "section_type": "methods",
                "text": "shared result",
                "embedding": [1.0, 0.0, 0.0],
            },
        ]

    async def fetch_vectors(self, chunk_ids):
        return {chunk_id: [0.0, 0.0, 1.0] for chunk_id in chunk_ids}


class FakeReranker:
    async def rerank(self, query, candidates, top_k=5):
        return [dict(candidates[-1], rerank_score=0.99, rerank_fallback=False)]


def corpus_item(chunk_id, text):
    return {
        "chunk_id": chunk_id,
        "paper_id": "p1",
        "ingestion_id": "i1",
        "section_path": "Methods",
        "section_type": "methods",
        "page_number": None,
        "chunk_index": 0,
        "text": text,
    }


def test_rrf_uses_rank_not_source_scores() -> None:
    dense = [{"chunk_id": "a", "score": 999}, {"chunk_id": "b", "score": 1}]
    sparse = [{"chunk_id": "b", "score": -500}, {"chunk_id": "c", "score": 1000}]
    results = reciprocal_rank_fusion([dense, sparse])
    assert results[0]["chunk_id"] == "b"
    assert results[0]["rrf_score"] == pytest.approx(1 / 62 + 1 / 61)


def test_similarity_dedup_keeps_higher_ranked_item() -> None:
    items = [
        {"chunk_id": "a", "embedding": [1.0, 0.0], "rrf_score": 2.0},
        {"chunk_id": "b", "embedding": [0.999, 0.001], "rrf_score": 1.0},
        {"chunk_id": "c", "embedding": [0.0, 1.0], "rrf_score": 0.5},
    ]
    assert [item["chunk_id"] for item in deduplicate_by_similarity(items)] == ["a", "c"]


@pytest.mark.asyncio
async def test_hybrid_retrieval_fuses_dense_and_prefiltered_sparse(tmp_path: Path) -> None:
    bm25 = BM25IndexManager(tmp_path / "bm25")
    bm25.build_staging(
        "i1",
        [
            corpus_item("shared", "vector retrieval method"),
            corpus_item("sparse-only", "retrieval benchmark result"),
        ],
    )
    switch = bm25.switch_staging("i1")
    bm25.finalize_switch(switch)
    vector_store = FakeVectorStore()
    retriever = HybridRetriever(
        expander=FakeExpander(),
        embedding=FakeEmbedding(),
        vector_store=vector_store,
        bm25=bm25,
        logger=logging.getLogger("test"),
    )

    result = await retriever.retrieve(
        "如何改进检索", paper_id="p1", section_type="methods"
    )

    assert result.candidates[0]["chunk_id"] == "shared"
    assert {item["chunk_id"] for item in result.candidates} == {
        "shared",
        "dense-only",
        "sparse-only",
    }
    assert vector_store.filters["paper_id"] == "p1"
    assert vector_store.filters["section_type"] == "methods"
    assert result.candidates[-1]["embedding"] == [0.0, 0.0, 1.0]


@pytest.mark.asyncio
async def test_hybrid_retrieval_applies_optional_reranker(tmp_path: Path) -> None:
    bm25 = BM25IndexManager(tmp_path / "bm25")
    bm25.build_staging("i1", [corpus_item("shared", "vector retrieval method")])
    switch = bm25.switch_staging("i1")
    bm25.finalize_switch(switch)
    retriever = HybridRetriever(
        expander=FakeExpander(),
        embedding=FakeEmbedding(),
        vector_store=FakeVectorStore(),
        bm25=bm25,
        logger=logging.getLogger("test"),
        reranker=FakeReranker(),
    )
    result = await retriever.retrieve("query")
    assert len(result.candidates) == 1
    assert result.candidates[0]["rerank_score"] == 0.99
    assert "rerank_ms" in result.timings
