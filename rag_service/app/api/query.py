# 文件说明：提供基于论文证据的问答接口，以及只返回检索结果的证据搜索接口。
# Agent 的 search_paper 使用证据搜索结果自行组织分析，普通 RAG 问答则继续调用生成流程。

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request, status

from app.errors import ServiceUnavailableError
from app.schemas import (
    EvidenceSearchRequest,
    EvidenceSearchResponse,
    QueryRequest,
    QueryResponse,
)


router = APIRouter(tags=["query"])


@router.post("/query", response_model=QueryResponse)
async def query_papers(payload: QueryRequest, request: Request) -> dict:
    paper = request.app.state.paper_repository.get(payload.paper_id)
    if paper is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="paper_id 不存在",
        )
    if paper["status"] != "ready":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="论文尚未完成入库",
        )
    try:
        async with asyncio.timeout(60):
            return await request.app.state.query_service.answer(
                payload.question,
                paper_id=payload.paper_id,
                section_type=payload.section_type,
            )
    except TimeoutError as exc:
        raise ServiceUnavailableError("服务暂时不可用") from exc


@router.post(
    "/papers/{paper_id}/search",
    response_model=EvidenceSearchResponse,
)
async def search_evidence(
    paper_id: str,
    payload: EvidenceSearchRequest,
    request: Request,
) -> dict:
    paper = request.app.state.paper_repository.get(paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="paper_id 不存在")
    if paper["status"] != "ready":
        raise HTTPException(status_code=409, detail="论文尚未完成入库")
    try:
        async with asyncio.timeout(60):
            retrieval = await request.app.state.retriever.retrieve(
                payload.query,
                paper_id=paper_id,
                section_type=payload.section_type,
            )
    except TimeoutError as exc:
        raise ServiceUnavailableError("检索服务暂时不可用") from exc

    results = []
    for candidate in retrieval.candidates[: payload.top_k]:
        results.append(
            {
                "chunk_id": str(candidate["chunk_id"]),
                "paper_id": str(candidate.get("paper_id") or paper_id),
                "text": str(candidate.get("text") or ""),
                "section_path": str(candidate.get("section_path") or "未知章节"),
                "section_type": candidate.get("section_type"),
                "page_number": candidate.get("page_number"),
                "chunk_index": candidate.get("chunk_index"),
                "score": candidate.get("rerank_score", candidate.get("rrf_score")),
            }
        )
    return {
        "paper_id": paper_id,
        "results": results,
        "timings": retrieval.timings,
    }
