# 文件说明：验证 Embedding 请求的批次、向量返回顺序和维度校验。
# 通过模拟 HTTP 响应检查正确结果与异常数据，不加载真实向量模型。

import httpx
import pytest

from app.embedding import EmbeddingClient, EmbeddingError


@pytest.mark.asyncio
async def test_embedding_batches_orders_and_validates_vectors() -> None:
    batch_sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = __import__("json").loads(request.content)
        batch_sizes.append(len(body["input"]))
        data = [
            {"index": index, "embedding": [float(index), 1.0, 2.0]}
            for index in reversed(range(len(body["input"])))
        ]
        return httpx.Response(200, json={"data": data})

    client = EmbeddingClient(
        "http://embedding",
        "model",
        dimension=3,
        batch_size=2,
        transport=httpx.MockTransport(handler),
    )
    vectors = await client.embed(["a", "b", "c"])

    assert batch_sizes == [2, 1]
    assert vectors == [[0.0, 1.0, 2.0], [1.0, 1.0, 2.0], [0.0, 1.0, 2.0]]


@pytest.mark.asyncio
async def test_embedding_rejects_wrong_dimension() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})

    client = EmbeddingClient(
        "http://embedding",
        "model",
        dimension=3,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(EmbeddingError, match="维度错误"):
        await client.embed(["a"])
