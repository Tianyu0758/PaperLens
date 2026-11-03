# 文件说明：协调论文删除，清理向量、BM25 语料和数据库中的对应记录。
# 通过删除状态保留失败后的重试入口，服务启动时可以继续处理尚未完成的删除任务。

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from app.bm25_index import BM25IndexManager
from app.qdrant_store import QdrantChunkStore
from app.repositories import PaperRepository


class PaperDeletionService:
    def __init__(
        self,
        *,
        repository: PaperRepository,
        vector_store: QdrantChunkStore,
        bm25: BM25IndexManager,
        upload_path: Path,
        logger: logging.Logger,
    ) -> None:
        self.repository = repository
        self.vector_store = vector_store
        self.bm25 = bm25
        self.upload_path = upload_path
        self.logger = logger

    async def delete(self, paper_id: str) -> dict[str, str]:
        state = self.repository.begin_deleting(paper_id)
        if state == "deleted":
            return {"paper_id": paper_id, "status": "deleted"}

        snapshot_id = f"delete-{paper_id}-{uuid.uuid4().hex}"
        try:
            await self.vector_store.delete_paper(paper_id)
            corpus = self.repository.list_ready_chunks()
            staging = self.bm25.build_staging(snapshot_id, corpus)
            if self.bm25.snapshot_count(staging) != len(corpus):
                raise RuntimeError("BM25 删除快照数量校验失败")
            switch = self.bm25.switch_staging(snapshot_id)
            try:
                self.repository.finish_deleting(paper_id)
            except Exception:
                self.bm25.rollback_switch(switch)
                raise
            self.bm25.finalize_switch(switch)
            self._delete_files(paper_id)
            self.logger.info(
                "paper_deleted paper_id=%s active_docs=%d", paper_id, len(corpus)
            )
            return {"paper_id": paper_id, "status": "deleted"}
        except Exception:
            self.bm25.discard_staging(snapshot_id)
            self.logger.exception("paper_delete_failed paper_id=%s", paper_id)
            raise

    async def resume_pending(self) -> None:
        for paper in self.repository.list_deleting():
            try:
                await self.delete(paper["paper_id"])
            except Exception:
                self.logger.exception(
                    "paper_delete_recovery_failed paper_id=%s", paper["paper_id"]
                )

    def _delete_files(self, paper_id: str) -> None:
        for suffix in (".pdf", ".tei.xml"):
            try:
                (self.upload_path / f"{paper_id}{suffix}").unlink(missing_ok=True)
            except OSError:
                self.logger.warning(
                    "paper_file_delete_failed paper_id=%s suffix=%s",
                    paper_id,
                    suffix,
                )
