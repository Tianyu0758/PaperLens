# 文件说明：验证解析、切块、保存和索引切换组成的入库流程及失败补偿。
# 使用 Grobid、Embedding 和向量存储替身，检查成功状态与失败状态是否正确落库。

import logging
from pathlib import Path

import pytest

from app.db import Database
from app.bm25_index import BM25IndexManager
from app.ingestion import IngestionProcessor
from app.repositories import PaperRepository
from tests.test_tei_parser import TEI


class FakeGrobid:
    def __init__(self, result: bytes | Exception) -> None:
        self.result = result

    async def process_fulltext(self, pdf_path: Path) -> bytes:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class CharacterCodec:
    def encode(self, text: str) -> list[int]:
        return [ord(character) for character in text]

    def decode(self, token_ids: list[int]) -> str:
        return "".join(chr(token_id) for token_id in token_ids)


class FakeEmbedding:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if self.fail:
            raise RuntimeError("embedding unavailable")
        return [[1.0, 0.0] for _ in texts]


class FakeVectorStore:
    def __init__(self) -> None:
        self.points = []
        self.deleted = False
        self.activated = False

    async def upsert_staging(self, paper_id, ingestion_id, chunks, vectors):
        self.points = list(zip(chunks, vectors))

    async def count_ingestion(self, ingestion_id):
        return len(self.points)

    async def delete_ingestion(self, ingestion_id):
        self.deleted = True
        self.points.clear()

    async def activate_ingestion(self, ingestion_id):
        self.activated = True


def setup_paper(tmp_path: Path) -> tuple[PaperRepository, Path]:
    database = Database(tmp_path / "papers.db")
    database.initialize()
    repository = PaperRepository(database)
    repository.create_processing("paper-1", "sha-1", "ingestion-1")
    pdf_path = tmp_path / "paper-1.pdf"
    pdf_path.write_bytes(b"%PDF-1.7")
    return repository, pdf_path


@pytest.mark.asyncio
async def test_ingestion_stores_tei_and_metadata(tmp_path: Path) -> None:
    repository, pdf_path = setup_paper(tmp_path)
    processor = IngestionProcessor(
        repository, FakeGrobid(TEI), logging.getLogger("test"), CharacterCodec()
    )

    await processor.process("paper-1", "ingestion-1", pdf_path)

    paper = repository.get("paper-1")
    assert paper["title"] == "Reliable RAG Systems"
    assert paper["year"] == 2026
    assert paper["doi"] == "10.1000/rag"
    assert paper["status"] == "processing"
    assert pdf_path.with_suffix(".tei.xml").read_bytes() == TEI
    assert repository.count_ingestion_chunks("ingestion-1") == 4


@pytest.mark.asyncio
async def test_ingestion_failure_updates_both_states(tmp_path: Path) -> None:
    repository, pdf_path = setup_paper(tmp_path)
    processor = IngestionProcessor(
        repository,
        FakeGrobid(RuntimeError("service unavailable")),
        logging.getLogger("test"),
        CharacterCodec(),
    )

    await processor.process("paper-1", "ingestion-1", pdf_path)

    paper = repository.get("paper-1")
    assert paper["status"] == "failed"
    assert paper["error_message"] == "service unavailable"
    with repository.database.connect() as connection:
        ingestion = connection.execute(
            "SELECT status, error_message FROM ingestions WHERE ingestion_id = ?",
            ("ingestion-1",),
        ).fetchone()
    assert ingestion["status"] == "failed"
    assert ingestion["error_message"] == "service unavailable"


@pytest.mark.asyncio
async def test_ingestion_stages_embeddings_in_vector_store(tmp_path: Path) -> None:
    repository, pdf_path = setup_paper(tmp_path)
    vector_store = FakeVectorStore()
    processor = IngestionProcessor(
        repository,
        FakeGrobid(TEI),
        logging.getLogger("test"),
        CharacterCodec(),
        FakeEmbedding(),
        vector_store,
    )

    await processor.process("paper-1", "ingestion-1", pdf_path)

    assert len(vector_store.points) == 4
    assert repository.count_ingestion_chunks("ingestion-1") == 4
    assert repository.get("paper-1")["status"] == "processing"


@pytest.mark.asyncio
async def test_embedding_failure_compensates_sqlite_and_qdrant(tmp_path: Path) -> None:
    repository, pdf_path = setup_paper(tmp_path)
    vector_store = FakeVectorStore()
    processor = IngestionProcessor(
        repository,
        FakeGrobid(TEI),
        logging.getLogger("test"),
        CharacterCodec(),
        FakeEmbedding(fail=True),
        vector_store,
    )

    await processor.process("paper-1", "ingestion-1", pdf_path)

    assert vector_store.deleted is True
    assert repository.count_ingestion_chunks("ingestion-1") == 0
    assert repository.get("paper-1")["status"] == "failed"


@pytest.mark.asyncio
async def test_full_activation_marks_ready_and_switches_bm25(tmp_path: Path) -> None:
    repository, pdf_path = setup_paper(tmp_path)
    vector_store = FakeVectorStore()
    bm25 = BM25IndexManager(tmp_path / "bm25")
    processor = IngestionProcessor(
        repository,
        FakeGrobid(TEI),
        logging.getLogger("test"),
        CharacterCodec(),
        FakeEmbedding(),
        vector_store,
        bm25,
    )

    await processor.process("paper-1", "ingestion-1", pdf_path)

    paper = repository.get("paper-1")
    assert paper["status"] == "ready"
    assert paper["active_ingestion_id"] == "ingestion-1"
    assert vector_store.activated is True
    assert bm25.snapshot_count(bm25.active_path) == 4
    assert bm25.search("Reliable", limit=1)


@pytest.mark.asyncio
async def test_sqlite_activation_failure_rolls_back_bm25(tmp_path: Path) -> None:
    repository, pdf_path = setup_paper(tmp_path)
    vector_store = FakeVectorStore()
    bm25 = BM25IndexManager(tmp_path / "bm25")
    old_corpus = [
        {
            "chunk_id": "old",
            "paper_id": "old-paper",
            "ingestion_id": "old-ingestion",
            "section_path": "Introduction",
            "section_type": "introduction",
            "page_number": None,
            "chunk_index": 0,
            "text": "stable previous snapshot",
        }
    ]
    bm25.build_staging("old", old_corpus)
    old_switch = bm25.switch_staging("old")
    bm25.finalize_switch(old_switch)
    repository.mark_ready = lambda paper_id, ingestion_id: (_ for _ in ()).throw(
        RuntimeError("sqlite activation failed")
    )
    processor = IngestionProcessor(
        repository,
        FakeGrobid(TEI),
        logging.getLogger("test"),
        CharacterCodec(),
        FakeEmbedding(),
        vector_store,
        bm25,
    )

    await processor.process("paper-1", "ingestion-1", pdf_path)

    assert repository.get("paper-1")["status"] == "failed"
    assert vector_store.deleted is True
    assert bm25.search("previous", limit=1)[0]["chunk_id"] == "old"
