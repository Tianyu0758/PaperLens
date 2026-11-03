# 文件说明：管理 BM25 稀疏索引的分词、构建、持久化、切换和检索。
# 文本先提取英文词并去除停用词；全局索引以文本块为语料，限定论文或章节时建立局部缓存索引。
# 入库通过暂存快照切换主索引，失败时可回退到先前快照。

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import bm25s


ENGLISH_TOKEN_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
MANIFEST_NAME = "manifest.json"


def english_only(text: str) -> str:
    return " ".join(ENGLISH_TOKEN_PATTERN.findall(text))


def tokenize_texts(texts: list[str]):
    normalized = [english_only(text) for text in texts]
    return bm25s.tokenize(
        normalized,
        stopwords="en",
        return_ids=True,
        show_progress=False,
    )


@dataclass(slots=True)
class SnapshotSwitch:
    active_path: Path
    backup_path: Path | None


class BM25IndexManager:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.active_path = root / "active"
        self._active = None
        self._local_cache: dict[tuple[str | None, str | None], tuple[Any, list[dict]]] = {}

    def staging_path(self, ingestion_id: str) -> Path:
        return self.root / f"staging-{ingestion_id}"

    def build_staging(self, ingestion_id: str, corpus: list[dict]) -> Path:
        staging = self.staging_path(ingestion_id)
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True, exist_ok=False)
        try:
            if corpus:
                retriever = bm25s.BM25(k1=1.2, b=0.75)
                retriever.index(
                    tokenize_texts([item["text"] for item in corpus]),
                    show_progress=False,
                )
                retriever.save(staging, corpus=corpus, show_progress=False)
            (staging / MANIFEST_NAME).write_text(
                json.dumps(
                    {"version": 1, "doc_count": len(corpus)},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            if self.snapshot_count(staging) != len(corpus):
                raise RuntimeError("BM25 staging 文档数量校验失败")
            return staging
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def snapshot_count(self, path: Path) -> int:
        manifest = json.loads((path / MANIFEST_NAME).read_text(encoding="utf-8"))
        return int(manifest["doc_count"])

    def discard_staging(self, ingestion_id: str) -> None:
        shutil.rmtree(self.staging_path(ingestion_id), ignore_errors=True)

    def switch_staging(self, ingestion_id: str) -> SnapshotSwitch:
        staging = self.staging_path(ingestion_id)
        if not staging.is_dir():
            raise RuntimeError(f"BM25 staging 不存在: {ingestion_id}")
        self.root.mkdir(parents=True, exist_ok=True)
        backup = None
        if self.active_path.exists():
            backup = self.root / f"backup-{uuid.uuid4().hex}"
            os.replace(self.active_path, backup)
        try:
            os.replace(staging, self.active_path)
        except Exception:
            if backup is not None and backup.exists():
                os.replace(backup, self.active_path)
            raise
        self._clear_cache()
        return SnapshotSwitch(self.active_path, backup)

    def finalize_switch(self, switch: SnapshotSwitch) -> None:
        if switch.backup_path is not None:
            shutil.rmtree(switch.backup_path, ignore_errors=True)

    def rollback_switch(self, switch: SnapshotSwitch) -> None:
        failed = self.root / f"failed-active-{uuid.uuid4().hex}"
        if switch.active_path.exists():
            os.replace(switch.active_path, failed)
        if switch.backup_path is not None and switch.backup_path.exists():
            os.replace(switch.backup_path, switch.active_path)
        shutil.rmtree(failed, ignore_errors=True)
        self._clear_cache()

    def _clear_cache(self) -> None:
        self._active = None
        self._local_cache.clear()

    def _load_active(self):
        if not self.active_path.is_dir():
            return None
        if self.snapshot_count(self.active_path) == 0:
            return None
        if self._active is None:
            self._active = bm25s.BM25.load(
                self.active_path,
                load_corpus=True,
                show_progress=False,
            )
        return self._active

    def _get_index(
        self, paper_id: str | None, section_type: str | None
    ) -> tuple[Any, list[dict]] | None:
        active = self._load_active()
        if active is None:
            return None
        corpus = list(active.corpus or [])
        if paper_id is None and section_type is None:
            return active, corpus
        key = (paper_id, section_type)
        if key in self._local_cache:
            return self._local_cache[key]
        selected = [
            item
            for item in corpus
            if (paper_id is None or item["paper_id"] == paper_id)
            and (section_type is None or item["section_type"] == section_type)
        ]
        if not selected:
            return None
        local = bm25s.BM25(k1=1.2, b=0.75)
        local.index(tokenize_texts([item["text"] for item in selected]), show_progress=False)
        self._local_cache[key] = (local, selected)
        return local, selected

    def search(
        self,
        query: str,
        *,
        paper_id: str | None = None,
        section_type: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        if not english_only(query):
            return []
        index = self._get_index(paper_id, section_type)
        if index is None or limit <= 0:
            return []
        retriever, corpus = index
        k = min(limit, len(corpus))
        documents, scores = retriever.retrieve(
            tokenize_texts([query]),
            corpus=corpus,
            k=k,
            show_progress=False,
        )
        results = []
        for document, score in zip(documents[0], scores[0], strict=True):
            item = dict(document)
            item["score"] = float(score)
            results.append(item)
        return results
