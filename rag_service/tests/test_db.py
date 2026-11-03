# 文件说明：验证 SQLite 表结构、WAL 模式和论文入库状态的基本保存行为。
# 同时检查论文列表只返回 ready 状态的数据，使用临时数据库隔离测试。

from pathlib import Path

from app.db import Database
from app.repositories import PaperRepository


def test_database_uses_wal_and_creates_state(tmp_path: Path) -> None:
    database = Database(tmp_path / "papers.db")
    database.initialize()
    repository = PaperRepository(database)
    repository.create_processing("paper-1", "sha-1", "ingestion-1")

    paper = repository.get("paper-1")
    assert paper is not None
    assert paper["status"] == "processing"
    assert paper["uploaded_at"].endswith("+00:00")

    with database.connect() as connection:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        ingestion = connection.execute(
            "SELECT status FROM ingestions WHERE ingestion_id = ?",
            ("ingestion-1",),
        ).fetchone()
    assert journal_mode == "wal"
    assert ingestion["status"] == "processing"


def test_ready_list_only_returns_ready_papers(tmp_path: Path) -> None:
    database = Database(tmp_path / "papers.db")
    database.initialize()
    repository = PaperRepository(database)
    repository.create_processing("paper-1", "sha-1", "ingestion-1")
    assert repository.list_ready() == []
