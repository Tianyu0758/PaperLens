# 文件说明：协调论文从 PDF 到可检索状态的完整入库过程。
# 依次完成解析、切块、文本保存、向量写入、BM25 快照构建与活动版本切换。
# 通过计数检查和失败补偿处理多个存储之间的一致性，不把跨存储操作视为单个数据库事务。

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.bm25_index import BM25IndexManager, SnapshotSwitch
from app.chunking import TokenCodec, build_chunks
from app.grobid import GrobidClient
from app.qdrant_store import QdrantChunkStore
from app.repositories import PaperRepository
from app.tei_parser import parse_tei


class EmbeddingProvider(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


@dataclass(slots=True)
class IngestionProcessor:
    repository: PaperRepository
    grobid: GrobidClient
    logger: logging.Logger
    token_codec: TokenCodec
    embedding: EmbeddingProvider | None = None
    vector_store: QdrantChunkStore | None = None
    bm25: BM25IndexManager | None = None

    async def process(
        self, paper_id: str, ingestion_id: str, pdf_path: Path
    ) -> None:
        started = time.perf_counter()
        snapshot_switch: SnapshotSwitch | None = None
        try:
            tei_xml = await self.grobid.process_fulltext(pdf_path)
            parsed = parse_tei(tei_xml)
            tei_path = pdf_path.with_suffix(".tei.xml")
            tei_path.write_bytes(tei_xml)
            self.repository.update_metadata(
                paper_id,
                title=parsed.metadata.title,
                year=parsed.metadata.year,
                authors=parsed.metadata.authors,
                doi=parsed.metadata.doi,
            )
            chunks = build_chunks(paper_id, parsed.sections, self.token_codec)
            if not chunks:
                raise ValueError("GROBID 未解析出可索引的正文")
            self.repository.replace_ingestion_chunks(
                paper_id,
                ingestion_id,
                [chunk.as_record() for chunk in chunks],
            )
            grobid_ms = int((time.perf_counter() - started) * 1000)
            self.logger.info(
                "grobid_complete paper_id=%s ingestion_id=%s grobid_ms=%d sections=%d chunks=%d",
                paper_id,
                ingestion_id,
                grobid_ms,
                len(parsed.sections),
                len(chunks),
            )
            if (self.embedding is None) != (self.vector_store is None):
                raise RuntimeError("Embedding 与 Qdrant store 必须同时配置")
            if self.embedding is not None and self.vector_store is not None:
                embed_started = time.perf_counter()
                chunk_records = [chunk.as_record() for chunk in chunks]
                vectors = await self.embedding.embed(
                    [chunk["text"] for chunk in chunk_records]
                )
                embed_ms = int((time.perf_counter() - embed_started) * 1000)
                await self.vector_store.upsert_staging(
                    paper_id, ingestion_id, chunk_records, vectors
                )
                qdrant_count = await self.vector_store.count_ingestion(ingestion_id)
                sqlite_count = self.repository.count_ingestion_chunks(ingestion_id)
                if qdrant_count != sqlite_count or qdrant_count != len(vectors):
                    raise RuntimeError(
                        "入库数量校验失败: "
                        f"sqlite={sqlite_count} qdrant={qdrant_count} vectors={len(vectors)}"
                    )
                self.logger.info(
                    "embedding_staged paper_id=%s ingestion_id=%s embed_ms=%d chunks=%d",
                    paper_id,
                    ingestion_id,
                    embed_ms,
                    len(vectors),
                )
                if self.bm25 is not None:
                    bm25_started = time.perf_counter()
                    corpus = self.repository.list_indexable_chunks(ingestion_id)
                    staging_path = self.bm25.build_staging(ingestion_id, corpus)
                    bm25_count = self.bm25.snapshot_count(staging_path)
                    if bm25_count != len(corpus):
                        raise RuntimeError(
                            f"BM25 数量校验失败: expected={len(corpus)} actual={bm25_count}"
                        )
                    await self.vector_store.activate_ingestion(ingestion_id)
                    snapshot_switch = self.bm25.switch_staging(ingestion_id)
                    self.repository.mark_ready(paper_id, ingestion_id)
                    self.bm25.finalize_switch(snapshot_switch)
                    snapshot_switch = None
                    self.logger.info(
                        "ingestion_activated paper_id=%s ingestion_id=%s bm25_ms=%d active_docs=%d",
                        paper_id,
                        ingestion_id,
                        int((time.perf_counter() - bm25_started) * 1000),
                        bm25_count,
                    )
            # Without a BM25 manager, integration tests intentionally leave data staged.
        except Exception as exc:
            message = str(exc)[:1000]
            if self.bm25 is not None:
                try:
                    if snapshot_switch is not None:
                        self.bm25.rollback_switch(snapshot_switch)
                    else:
                        self.bm25.discard_staging(ingestion_id)
                except Exception:
                    self.logger.exception(
                        "bm25_compensation_failed paper_id=%s ingestion_id=%s",
                        paper_id,
                        ingestion_id,
                    )
            if self.vector_store is not None:
                try:
                    await self.vector_store.delete_ingestion(ingestion_id)
                except Exception:
                    self.logger.exception(
                        "qdrant_compensation_failed paper_id=%s ingestion_id=%s",
                        paper_id,
                        ingestion_id,
                    )
            self.repository.delete_ingestion_chunks(ingestion_id)
            self.repository.mark_failed(paper_id, ingestion_id, message)
            self.logger.exception(
                "ingestion_failed paper_id=%s ingestion_id=%s",
                paper_id,
                ingestion_id,
            )
