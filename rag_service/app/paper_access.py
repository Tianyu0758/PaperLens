# 文件说明：提供论文结构浏览与定向原文读取，补充检索之外的阅读能力。
# 优先读取 TEI 完整段落，缺失时回退到活动文本块；支持分段读取及入库版本检查。
# 章节和原文位置可以回查，未解析出的页码或原论文图片不会自行补造。

"""Read-only, bounded access to the active paper, with TEI sections when available."""
from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import HTTPException

from app.repositories import PaperRepository
from app.tei_parser import parse_tei


def document(repository: PaperRepository, paper_id: str, upload_path: Path) -> dict:
    paper = repository.get(paper_id)
    if paper is None:
        raise HTTPException(404, "论文不存在")
    if paper["status"] != "ready":
        raise HTTPException(409, "论文尚未就绪")
    with repository.database.connect() as connection:
        rows = connection.execute(
            "SELECT * FROM chunks WHERE paper_id=? AND ingestion_id=? ORDER BY chunk_index",
            (paper_id, paper["active_ingestion_id"]),
        ).fetchall()
    chunks = [dict(row) for row in rows]
    groups: dict[str, dict] = {}
    for chunk in chunks:
        key = chunk["section_path"] or "Untitled"
        group = groups.setdefault(key, {"section_path": key, "section_type": chunk["section_type"],
                                        "chunks": [], "paragraphs": []})
        group["chunks"].append(chunk)
        group["paragraphs"].append(chunk["text"])
    # TEI preserves full paragraphs without overlapping chunk boundaries.
    tei = (upload_path / f"{paper_id}.tei.xml").resolve()
    source = "chunks"
    if tei.is_relative_to(upload_path.resolve()) and tei.is_file():
        parsed = parse_tei(tei.read_bytes())
        for section in parsed.sections:
            group = groups.setdefault(section.section_path, {"section_path": section.section_path,
                "section_type": section.section_type, "chunks": [], "paragraphs": []})
            if not group.get("tei"):
                group["paragraphs"] = []
            group["paragraphs"].extend(section.paragraphs)
            group["tei"] = True
        source = "tei"
    sections = []
    for group in groups.values():
        text = "\n\n".join(group["paragraphs"])
        sid = "sec_" + hashlib.sha256(group["section_path"].encode()).hexdigest()[:16]
        sections.append({"section_id": sid, "section_path": group["section_path"],
            "section_type": group["section_type"], "text": text, "chars": len(text),
            "chunk_ids": [c["chunk_id"] for c in group["chunks"]],
            "pages": sorted({c["page_number"] for c in group["chunks"] if c["page_number"] is not None})})
    return {"paper": paper, "sections": sections, "chunks": chunks, "source": source,
            "ingestion_id": paper["active_ingestion_id"]}


def inspect(repository: PaperRepository, paper_id: str, upload_path: Path) -> dict:
    doc = document(repository, paper_id, upload_path)
    abstract = next((s["text"] for s in doc["sections"] if s["section_type"] == "abstract"), "")
    return {"paper": doc["paper"], "ingestion_id": doc["ingestion_id"], "source": doc["source"],
        "abstract": abstract[:5000], "abstract_truncated": len(abstract) > 5000,
        "sections": [{k: v for k, v in s.items() if k != "text"} for s in doc["sections"]],
        "capabilities": {"section_reading": True, "original_figure_extraction": False},
        "note": "页码仅在解析结果提供时显示；chunks 来源可能包含重叠内容。"}


def read(repository: PaperRepository, paper_id: str, upload_path: Path, *, section_id: str | None,
         chunk_id: str | None, offset: int, limit: int, ingestion_id: str | None) -> dict:
    doc = document(repository, paper_id, upload_path)
    if ingestion_id and ingestion_id != doc["ingestion_id"]:
        raise HTTPException(409, "论文版本已变化，请重新 inspect_paper")
    if bool(section_id) == bool(chunk_id):
        raise HTTPException(422, "请指定 section_id 或 chunk_id 其中一个")
    if section_id:
        item = next((s for s in doc["sections"] if s["section_id"] == section_id), None)
    else:
        item = next((c for c in doc["chunks"] if c["chunk_id"] == chunk_id), None)
    if item is None:
        raise HTTPException(404, "原文位置不存在")
    text = item["text"]
    if offset > len(text):
        raise HTTPException(422, "offset 超过原文长度")
    end = min(offset + limit, len(text))
    evidence = {"paper_id": paper_id, "section_path": item["section_path"],
        "section_id": section_id, "chunk_id": chunk_id, "page_number": item.get("page_number"),
        "source_id": f"{section_id or chunk_id}:{offset}:{end}", "text": text[offset:end]}
    return {"paper_id": paper_id, "ingestion_id": doc["ingestion_id"], "source": doc["source"],
            "offset": offset, "total_chars": len(text), "next_offset": end if end < len(text) else None,
            "results": [evidence]}
