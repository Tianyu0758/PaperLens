# 文件说明：封装论文元数据、入库状态、文本块和删除状态的数据库操作。
# 服务层通过 PaperRepository 读写状态；可索引文本块按照论文的活动入库版本筛选。

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.db import Database


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PaperRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_ready(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT paper_id, title, year, authors, doi, status, uploaded_at
                FROM papers
                WHERE status = 'ready'
                ORDER BY uploaded_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get(self, paper_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT paper_id, title, year, authors, doi, status,
                       active_ingestion_id, error_message, uploaded_at, updated_at
                FROM papers
                WHERE paper_id = ?
                """,
                (paper_id,),
            ).fetchone()
        return dict(row) if row else None

    def find_by_sha256(self, file_sha256: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM papers WHERE file_sha256 = ?",
                (file_sha256,),
            ).fetchone()
        return dict(row) if row else None

    def restart_deleted(self, paper_id: str, ingestion_id: str) -> None:
        now = utc_now()
        with self.database.transaction() as connection:
            updated = connection.execute(
                """
                UPDATE papers
                SET status = 'processing', title = NULL, year = NULL,
                    authors = NULL, doi = NULL, active_ingestion_id = NULL,
                    error_message = NULL, uploaded_at = ?, updated_at = ?
                WHERE paper_id = ? AND status = 'deleted'
                """,
                (now, now, paper_id),
            ).rowcount
            if updated != 1:
                raise RuntimeError("论文不处于 deleted 状态")
            connection.execute(
                """
                INSERT INTO ingestions (
                    ingestion_id, paper_id, status, started_at
                ) VALUES (?, ?, 'processing', ?)
                """,
                (ingestion_id, paper_id, now),
            )

    def create_processing(
        self, paper_id: str, file_sha256: str, ingestion_id: str
    ) -> None:
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO papers (
                    paper_id, file_sha256, status, uploaded_at, updated_at
                ) VALUES (?, ?, 'processing', ?, ?)
                """,
                (paper_id, file_sha256, now, now),
            )
            connection.execute(
                """
                INSERT INTO ingestions (
                    ingestion_id, paper_id, status, started_at
                ) VALUES (?, ?, 'processing', ?)
                """,
                (ingestion_id, paper_id, now),
            )

    def update_metadata(
        self,
        paper_id: str,
        *,
        title: str | None,
        year: int | None,
        authors: list[str],
        doi: str | None,
    ) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE papers
                SET title = ?, year = ?, authors = ?, doi = ?, updated_at = ?
                WHERE paper_id = ?
                """,
                (title, year, "; ".join(authors), doi, utc_now(), paper_id),
            )

    def mark_failed(self, paper_id: str, ingestion_id: str, message: str) -> None:
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE papers
                SET status = 'failed', error_message = ?, updated_at = ?
                WHERE paper_id = ?
                """,
                (message, now, paper_id),
            )
            connection.execute(
                """
                UPDATE ingestions
                SET status = 'failed', error_message = ?, finished_at = ?
                WHERE ingestion_id = ?
                """,
                (message, now, ingestion_id),
            )

    def replace_ingestion_chunks(
        self, paper_id: str, ingestion_id: str, chunks: list[dict[str, Any]]
    ) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "DELETE FROM chunks WHERE ingestion_id = ?",
                (ingestion_id,),
            )
            connection.executemany(
                """
                INSERT INTO chunks (
                    chunk_id, paper_id, ingestion_id, section_path, section_type,
                    page_number, chunk_index, text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        chunk["chunk_id"],
                        paper_id,
                        ingestion_id,
                        chunk["section_path"],
                        chunk["section_type"],
                        chunk.get("page_number"),
                        chunk["chunk_index"],
                        chunk["text"],
                    )
                    for chunk in chunks
                ],
            )
            connection.execute(
                "UPDATE ingestions SET chunk_count = ? WHERE ingestion_id = ?",
                (len(chunks), ingestion_id),
            )

    def count_ingestion_chunks(self, ingestion_id: str) -> int:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM chunks WHERE ingestion_id = ?",
                (ingestion_id,),
            ).fetchone()
        return int(row[0])

    def delete_ingestion_chunks(self, ingestion_id: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "DELETE FROM chunks WHERE ingestion_id = ?",
                (ingestion_id,),
            )

    def list_indexable_chunks(self, staging_ingestion_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT c.chunk_id, c.paper_id, c.ingestion_id, c.section_path,
                       c.section_type, c.page_number, c.chunk_index, c.text
                FROM chunks AS c
                JOIN papers AS p ON p.paper_id = c.paper_id
                WHERE (
                    p.status = 'ready'
                    AND c.ingestion_id = p.active_ingestion_id
                ) OR c.ingestion_id = ?
                ORDER BY c.paper_id, c.chunk_index
                """,
                (staging_ingestion_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_ready_chunks(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT c.chunk_id, c.paper_id, c.ingestion_id, c.section_path,
                       c.section_type, c.page_number, c.chunk_index, c.text
                FROM chunks AS c
                JOIN papers AS p ON p.paper_id = c.paper_id
                WHERE p.status = 'ready'
                  AND c.ingestion_id = p.active_ingestion_id
                ORDER BY c.paper_id, c.chunk_index
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def begin_deleting(self, paper_id: str) -> str:
        now = utc_now()
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT status FROM papers WHERE paper_id = ?", (paper_id,)
            ).fetchone()
            if row is None:
                raise KeyError(paper_id)
            current = str(row["status"])
            if current == "deleted":
                return current
            if current == "processing":
                raise ValueError("processing")
            connection.execute(
                """
                UPDATE papers
                SET status = 'deleting', error_message = NULL, updated_at = ?
                WHERE paper_id = ?
                """,
                (now, paper_id),
            )
        return "deleting"

    def finish_deleting(self, paper_id: str) -> None:
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM chunks WHERE paper_id = ?", (paper_id,))
            updated = connection.execute(
                """
                UPDATE papers
                SET status = 'deleted', active_ingestion_id = NULL,
                    error_message = NULL, updated_at = ?
                WHERE paper_id = ? AND status = 'deleting'
                """,
                (now, paper_id),
            ).rowcount
            if updated != 1:
                raise RuntimeError("论文不处于 deleting 状态")

    def list_deleting(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT paper_id, status FROM papers WHERE status = 'deleting'"
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_ready(self, paper_id: str, ingestion_id: str) -> None:
        now = utc_now()
        with self.database.transaction() as connection:
            updated_paper = connection.execute(
                """
                UPDATE papers
                SET status = 'ready', active_ingestion_id = ?, error_message = NULL,
                    updated_at = ?
                WHERE paper_id = ? AND status = 'processing'
                """,
                (ingestion_id, now, paper_id),
            ).rowcount
            updated_ingestion = connection.execute(
                """
                UPDATE ingestions
                SET status = 'ready', error_message = NULL, finished_at = ?
                WHERE ingestion_id = ? AND status = 'processing'
                """,
                (now, ingestion_id),
            ).rowcount
            if updated_paper != 1 or updated_ingestion != 1:
                raise RuntimeError("论文或入库任务不处于 processing 状态")
            connection.execute(
                "UPDATE ingestions SET chunk_count = 0 WHERE ingestion_id = ?",
                (ingestion_id,),
            )
