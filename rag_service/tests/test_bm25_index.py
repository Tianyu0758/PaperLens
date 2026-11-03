# 文件说明：验证英文分词、BM25 快照保存加载、范围过滤以及索引回滚。
# 使用临时语料和目录，检查索引切换后检索结果与可用性是否符合预期。

from pathlib import Path

from app.bm25_index import BM25IndexManager, english_only


def chunk(
    chunk_id: str,
    paper_id: str,
    section_type: str,
    text: str,
) -> dict:
    return {
        "chunk_id": chunk_id,
        "paper_id": paper_id,
        "ingestion_id": f"ingestion-{paper_id}",
        "section_path": section_type.title(),
        "section_type": section_type,
        "page_number": None,
        "chunk_index": 0,
        "text": text,
    }


def test_english_only_removes_non_english_tokens() -> None:
    assert english_only("中文 RAG 检索 123 alpha-beta GPT4") == "RAG alpha-beta GPT4"


def test_snapshot_persistence_and_prefiltered_search(tmp_path: Path) -> None:
    manager = BM25IndexManager(tmp_path / "bm25")
    corpus = [
        chunk("c1", "p1", "methods", "dense vector retrieval embedding model"),
        chunk("c2", "p1", "results", "retrieval evaluation benchmark"),
        chunk("c3", "p2", "methods", "database transaction consistency"),
    ]
    manager.build_staging("i1", corpus)
    switch = manager.switch_staging("i1")
    manager.finalize_switch(switch)

    global_results = manager.search("vector retrieval", limit=2)
    assert global_results[0]["chunk_id"] == "c1"

    paper_results = manager.search("retrieval", paper_id="p2", limit=50)
    assert [item["paper_id"] for item in paper_results] == ["p2"]

    section_results = manager.search(
        "retrieval", paper_id="p1", section_type="results", limit=50
    )
    assert [item["chunk_id"] for item in section_results] == ["c2"]

    reloaded = BM25IndexManager(tmp_path / "bm25")
    assert reloaded.search("transaction", limit=1)[0]["chunk_id"] == "c3"


def test_snapshot_switch_can_rollback(tmp_path: Path) -> None:
    manager = BM25IndexManager(tmp_path / "bm25")
    manager.build_staging("old", [chunk("old", "p1", "methods", "stable old data")])
    old_switch = manager.switch_staging("old")
    manager.finalize_switch(old_switch)

    manager.build_staging("new", [chunk("new", "p2", "methods", "temporary new data")])
    new_switch = manager.switch_staging("new")
    assert manager.search("temporary", limit=1)[0]["chunk_id"] == "new"

    manager.rollback_switch(new_switch)
    assert manager.search("stable", limit=1)[0]["chunk_id"] == "old"
