# 文件说明：提供论文列表、状态、删除，以及新增的结构浏览和原文读取接口。
# 接口调用论文仓储、paper_access 和删除服务，并对未就绪或不存在的论文返回明确状态。

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.dependencies import get_paper_repository
from app.repositories import PaperRepository
from app.schemas import DeletePaperResponse, PaperStatusResponse, PaperSummary
from app.config import settings
from app import paper_access


router = APIRouter(prefix="/papers", tags=["papers"])


@router.get("/{paper_id}/inspect")
def inspect_paper(paper_id: str, repository: PaperRepository = Depends(get_paper_repository)) -> dict:
    return paper_access.inspect(repository, paper_id, settings.upload_path)


@router.get("/{paper_id}/read")
def read_paper(paper_id: str, section_id: str | None = None, chunk_id: str | None = None,
               offset: int = Query(0, ge=0), limit: int = Query(8000, ge=100, le=20000),
               ingestion_id: str | None = None,
               repository: PaperRepository = Depends(get_paper_repository)) -> dict:
    return paper_access.read(repository, paper_id, settings.upload_path, section_id=section_id,
                            chunk_id=chunk_id, offset=offset, limit=limit, ingestion_id=ingestion_id)


@router.get("", response_model=list[PaperSummary])
def list_papers(
    limit: int = Query(default=20, ge=1, le=20),
    repository: PaperRepository = Depends(get_paper_repository),
) -> list[dict]:
    return repository.list_ready(limit=limit)


@router.get("/{paper_id}/status", response_model=PaperStatusResponse)
def get_paper_status(
    paper_id: str,
    repository: PaperRepository = Depends(get_paper_repository),
) -> dict:
    paper = repository.get(paper_id)
    if paper is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="paper_id 不存在",
        )
    return paper


@router.delete("/{paper_id}", response_model=DeletePaperResponse)
async def delete_paper(paper_id: str, request: Request) -> dict:
    paper = request.app.state.paper_repository.get(paper_id)
    if paper is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="paper_id 不存在",
        )
    if paper["status"] == "processing":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="论文正在入库，暂时无法删除",
        )
    return await request.app.state.deletion_service.delete(paper_id)
