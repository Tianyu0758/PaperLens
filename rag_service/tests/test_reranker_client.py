# 文件说明：验证重排序分数和顺序能够正确合并到候选文本。
# 同时模拟服务失败，确认客户端回退到已有融合排序。

import json
import logging

import httpx
import pytest

from app.reranker_client import RerankerClient


CANDIDATES = [
    {"chunk_id": "a", "text": "relevant", "rrf_score": 2.0},
    {"chunk_id": "b", "text": "irrelevant", "rrf_score": 1.0},
]


@pytest.mark.asyncio
async def test_reranker_client_merges_scores_and_order() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert len(payload["documents"]) == 2
        return httpx.Response(
            200,
            json={
                "results": [
                    {"chunk_id": "b", "score": 0.9},
                    {"chunk_id": "a", "score": 0.2},
                ]
            },
        )

    client = RerankerClient(
        "http://reranker",
        logging.getLogger("test"),
        transport=httpx.MockTransport(handler),
    )
    results = await client.rerank("query", CANDIDATES, top_k=2)
    assert [item["chunk_id"] for item in results] == ["b", "a"]
    assert results[0]["rerank_score"] == 0.9
    assert results[0]["rerank_fallback"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503),
        httpx.Response(200, json={"results": []}),
        httpx.Response(200, json={"results": [{"chunk_id": "unknown", "score": 0.5}]}),
        httpx.Response(200, json={"results": [{"chunk_id": "a", "score": 2.0}]}),
    ],
)
async def test_reranker_client_falls_back_to_rrf(response: httpx.Response) -> None:
    client = RerankerClient(
        "http://reranker",
        logging.getLogger("test"),
        transport=httpx.MockTransport(lambda request: response),
    )
    results = await client.rerank("query", CANDIDATES, top_k=1)
    assert [item["chunk_id"] for item in results] == ["a"]
    assert results[0]["rerank_fallback"] is True
