# 文件说明：验证删除操作能协调数据库、BM25 和向量记录，并支持重复调用。
# 模拟向量存储失败，检查删除状态能保留供后续重试。

import logging
from pathlib import Path

import pytest

from app.bm25_index import BM25IndexManager
from app.db import Database
from app.deletion import PaperDeletionService
from app.repositories import PaperRepository


class FakeVectorStore:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.deleted_papers: list[str] = []

    async def delete_paper(self, paper_id: str) -> None:
        if self.fail:
            raise RuntimeError("qdrant unavailable")
        self.deleted_papers.append(paper_id)


def ready_repository(tmp_path: Path) -> PaperRepository:
    database = Database(tmp_path / "papers.db")
    database.initialize()
    repository = PaperRepository(database)
    repository.create_processing("paper-1", "sha-1", "ingestion-1")
    repository.replace_ingestion_chunks(
        "paper-1",
        "ingestion-1",
        [
            {
                "chunk_id": "chunk-1",
                "section_path": "Methods",
                "section_type": "methods",
                "page_number": None,
                "chunk_index": 0,
                "text": "retrieval method",
            }
        ],
    )
    repository.mark_ready("paper-1", "ingestion-1")
    return repository


@pytest.mark.asyncio
async def test_delete_removes_three_stores_and_is_idempotent(tmp_path: Path) -> None:
    repository = ready_repository(tmp_path)
    bm25 = BM25IndexManager(tmp_path / "bm25")
    bm25.build_staging("initial", repository.list_ready_chunks())
    bm25.finalize_switch(bm25.switch_staging("initial"))
    vector_store = FakeVectorStore()
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "paper-1.pdf").write_bytes(b"pdf")
    service = PaperDeletionService(
        repository=repository,
        vector_store=vector_store,
        bm25=bm25,
        upload_path=uploads,
        logger=logging.getLogger("test"),
    )

    first = await service.delete("paper-1")
    second = await service.delete("paper-1")

    assert first == second == {"paper_id": "paper-1", "status": "deleted"}
    assert vector_store.deleted_papers == ["paper-1"]
    assert repository.get("paper-1")["status"] == "deleted"
    assert repository.count_ingestion_chunks("ingestion-1") == 0
    assert bm25.snapshot_count(bm25.active_path) == 0
    assert bm25.search("retrieval") == []
    assert not (uploads / "paper-1.pdf").exists()


@pytest.mark.asyncio
async def test_delete_failure_keeps_deleting_for_retry(tmp_path: Path) -> None:
    repository = ready_repository(tmp_path)
    service = PaperDeletionService(
        repository=repository,
        vector_store=FakeVectorStore(fail=True),
        bm25=BM25IndexManager(tmp_path / "bm25"),
        upload_path=tmp_path / "uploads",
        logger=logging.getLogger("test"),
    )

    with pytest.raises(RuntimeError, match="qdrant unavailable"):
        await service.delete("paper-1")

    assert repository.get("paper-1")["status"] == "deleting"
