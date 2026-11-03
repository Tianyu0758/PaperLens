# 文件说明：提供统一工作台页面与静态资源，并将论文列表、上传、结构浏览和原文读取转发给 RAG。
# 浏览器访问 Agent 的同源接口即可使用工作台；RAG 连接错误会转换成适合界面展示的响应。

"""Same-origin workspace and a small, explicit bridge to the existing RAG API."""
from collections.abc import Awaitable
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import Settings
from app.rag_client import RagClient

STATIC = Path(__file__).resolve().parent / "static"


async def rag_response(operation: Awaitable[Any]) -> Any:
    try:
        return await operation
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        detail = "论文服务暂时不可用，请稍后重试"
        if 400 <= status < 500:
            try:
                detail = exc.response.json().get("detail", detail)
            except ValueError:
                pass
        raise HTTPException(status if 400 <= status < 500 else 502, detail) from exc
    except httpx.TimeoutException as exc:
        raise HTTPException(504, "论文服务响应超时，请稍后重试") from exc
    except httpx.RequestError as exc:
        raise HTTPException(503, "暂时无法连接论文服务，请确认 RAG 已启动") from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(502, "论文服务返回的数据格式异常") from exc


def install_workspace(app: FastAPI, rag: RagClient, config: Settings) -> None:
    app.mount("/assets", StaticFiles(directory=STATIC), name="workspace-assets")

    @app.get("/", include_in_schema=False)
    async def workspace() -> FileResponse:
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/examples/presentation", include_in_schema=False)
    async def example_presentation() -> FileResponse:
        return FileResponse(STATIC.parent.parent / "examples" / "presentation.html", media_type="text/html")

    @app.get("/papers", tags=["workspace"])
    async def papers(limit: int = Query(20, ge=1, le=20)) -> list:
        return await rag_response(rag.list_papers(limit))

    @app.post("/papers/upload", tags=["workspace"])
    async def upload(file: UploadFile = File(...)) -> dict:
        try:
            if not file.filename or not file.filename.lower().endswith(".pdf"):
                raise HTTPException(415, "请选择 PDF 文件")
            content = bytearray()
            while chunk := await file.read(1024 * 1024):
                content.extend(chunk)
                if len(content) > config.max_upload_mb * 1024 * 1024:
                    raise HTTPException(413, f"PDF 不能超过 {config.max_upload_mb} MB")
            if not content.startswith(b"%PDF-"):
                raise HTTPException(400, "文件不是有效的 PDF")
            return await rag_response(rag.upload_pdf(file.filename, bytes(content)))
        finally:
            await file.close()

    @app.get("/papers/{paper_id}/status", tags=["workspace"])
    async def paper_status(paper_id: str) -> dict:
        return await rag_response(rag.paper_status(paper_id))

    @app.get("/papers/{paper_id}/inspect", tags=["workspace"])
    async def inspect_paper(paper_id: str) -> dict:
        return await rag_response(rag.inspect(paper_id))

    @app.get("/papers/{paper_id}/read", tags=["workspace"])
    async def read_paper(paper_id: str, section_id: str | None = None, chunk_id: str | None = None,
                         offset: int = Query(0, ge=0), limit: int = Query(8000, ge=100, le=20000),
                         ingestion_id: str | None = None) -> dict:
        return await rag_response(rag.read(paper_id,section_id=section_id,chunk_id=chunk_id,
                                         offset=offset,limit=limit,ingestion_id=ingestion_id))
