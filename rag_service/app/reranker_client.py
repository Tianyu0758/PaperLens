# 文件说明：调用独立重排序服务，根据用户问题为候选文本重新打分和排序。
# 服务异常时保留前面的融合排序作为回退，供混合检索器继续提供结果。

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx


@dataclass(slots=True)
class RerankerClient:
    base_url: str
    logger: logging.Logger
    timeout_seconds: float = 30.0
    transport: httpx.AsyncBaseTransport | None = None

    async def rerank(
        self, query: str, candidates: list[dict], top_k: int = 5
    ) -> list[dict]:
        if not candidates or top_k <= 0:
            return []
        fallback = [dict(item, rerank_fallback=True) for item in candidates[:top_k]]
        candidate_by_id = {item["chunk_id"]: item for item in candidates}
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                response = await client.post(
                    f"{self.base_url}/rerank",
                    json={
                        "query": query,
                        "documents": [
                            {"chunk_id": item["chunk_id"], "text": item["text"]}
                            for item in candidates
                        ],
                        "top_k": min(top_k, len(candidates)),
                    },
                )
                response.raise_for_status()
                results = response.json().get("results")
            if not isinstance(results, list) or not results:
                raise ValueError("Reranker 返回空结果")
            seen: set[str] = set()
            ranked: list[dict] = []
            for result in results:
                chunk_id = result.get("chunk_id")
                score = result.get("score")
                if (
                    chunk_id not in candidate_by_id
                    or chunk_id in seen
                    or not isinstance(score, (int, float))
                    or not 0.0 <= float(score) <= 1.0
                ):
                    raise ValueError("Reranker 返回格式错误")
                seen.add(chunk_id)
                ranked.append(
                    dict(
                        candidate_by_id[chunk_id],
                        rerank_score=float(score),
                        rerank_fallback=False,
                    )
                )
            if len(ranked) > top_k:
                raise ValueError("Reranker 返回数量超过 top_k")
            return ranked
        except Exception as exc:
            self.logger.warning("rerank_fallback reason=%s", str(exc)[:200])
            return fallback
