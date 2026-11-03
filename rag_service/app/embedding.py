# 文件说明：调用 Embedding 服务，把文本列表转换为稠密向量。
# 处理批量请求和返回顺序，并校验向量数量、维度等条件，供入库与稠密检索复用。

from __future__ import annotations

from dataclasses import dataclass

import httpx


class EmbeddingError(RuntimeError):
    pass


@dataclass(slots=True)
class EmbeddingClient:
    base_url: str
    model: str
    dimension: int = 1024
    batch_size: int = 32
    timeout_seconds: float = 60.0
    transport: httpx.AsyncBaseTransport | None = None

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                for start in range(0, len(texts), self.batch_size):
                    batch = texts[start : start + self.batch_size]
                    response = await client.post(
                        f"{self.base_url}/v1/embeddings",
                        json={
                            "input": batch,
                            "model": self.model,
                            "encoding_format": "float",
                        },
                    )
                    response.raise_for_status()
                    data = response.json().get("data", [])
                    ordered = sorted(data, key=lambda item: item["index"])
                    if len(ordered) != len(batch):
                        raise EmbeddingError(
                            f"Embedding 返回数量错误: expected={len(batch)} actual={len(ordered)}"
                        )
                    for item in ordered:
                        vector = item.get("embedding")
                        if not isinstance(vector, list) or len(vector) != self.dimension:
                            actual = len(vector) if isinstance(vector, list) else None
                            raise EmbeddingError(
                                f"Embedding 维度错误: expected={self.dimension} actual={actual}"
                            )
                        vectors.append([float(value) for value in vector])
        except EmbeddingError:
            raise
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise EmbeddingError(f"Embedding 服务调用失败: {exc}") from exc
        return vectors
