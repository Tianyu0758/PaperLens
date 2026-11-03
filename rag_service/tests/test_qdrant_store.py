# 文件说明：验证向量记录的暂存、计数、激活及按论文或批次删除。
# 使用 Qdrant 替身检查调用条件和载荷，不修改真实远程集合。

import pytest

from app.qdrant_store import QdrantChunkStore


class FakeQdrant:
    def __init__(self) -> None:
        self.points = []
        self.deleted = False
        self.delete_kwargs = None
        self.payload = None

    async def upsert(self, **kwargs):
        self.points.extend(kwargs["points"])

    async def count(self, **kwargs):
        return type("Count", (), {"count": len(self.points)})()

    async def delete(self, **kwargs):
        self.deleted = True
        self.delete_kwargs = kwargs
        self.points.clear()

    async def set_payload(self, **kwargs):
        self.payload = kwargs["payload"]


@pytest.mark.asyncio
async def test_qdrant_store_stages_counts_activates_and_deletes() -> None:
    client = FakeQdrant()
    store = QdrantChunkStore(client, "paper_chunks", batch_size=1)
    chunks = [
        {
            "chunk_id": "2f1d46ad-dbea-4b38-b320-6e13e1e4ef76",
            "section_path": "Methods",
            "section_type": "methods",
            "page_number": None,
            "chunk_index": 0,
            "text": "content",
        }
    ]

    await store.upsert_staging("paper-1", "ingestion-1", chunks, [[1.0, 0.0]])

    assert await store.count_ingestion("ingestion-1") == 1
    assert client.points[0].payload["visible"] is False
    assert client.points[0].vector == {"dense": [1.0, 0.0]}
    await store.activate_ingestion("ingestion-1")
    assert client.payload == {"visible": True}
    await store.delete_ingestion("ingestion-1")
    assert client.deleted is True


@pytest.mark.asyncio
async def test_qdrant_store_deletes_by_paper_id() -> None:
    client = FakeQdrant()
    store = QdrantChunkStore(client, "paper_chunks")

    await store.delete_paper("paper-1")

    condition = client.delete_kwargs["points_selector"].filter.must[0]
    assert condition.key == "paper_id"
    assert condition.match.value == "paper-1"
