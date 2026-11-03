# 文件说明：Agent 服务的 FastAPI 入口，提供会话、任务、SSE 进度、文件下载和演示预览接口。
# 启动时初始化数据库与任务服务，关闭时处理未完成任务；前端页面由同一服务提供。
# 接口根据用户和会话归属查找记录，HTML 预览使用受限的浏览器运行环境。

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterable
from contextlib import asynccontextmanager
from typing import Annotated, Callable

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent

from app.api_schemas import RunCreate, RunRetry, SessionCreate, SessionRename
from app.config import Settings, settings
from app.rag_client import RagClient
from app.service import RunService
from app.store import AgentStore, StoreError, TERMINAL
from app.web import install_workspace

UserId = Annotated[str, Query(min_length=1, max_length=128)]


def public_session(session: dict) -> dict:
    return {key: value for key, value in session.items() if key not in ("context_summary", "summary_through_turn")}


def create_app(config: Settings = settings, *, store: AgentStore | None = None,
               rag: RagClient | None = None, runner: Callable | None = None,
               summarizer: Callable | None = None) -> FastAPI:
    database = store or AgentStore(config.database_path, config.data_dir)
    rag_client = rag or RagClient(config.rag_base_url)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        config.data_dir.mkdir(parents=True, exist_ok=True)
        database.initialize()
        database.recover_interrupted()
        arguments = {"runner": runner} if runner is not None else {}
        service = RunService(database, rag_client, config, summarizer=summarizer, **arguments)
        application.state.store = database
        application.state.service = service
        try:
            yield
        finally:
            await service.close()

    application = FastAPI(title="PaperLens HTML Academic Agent", version="0.4.0", lifespan=lifespan)
    install_workspace(application, rag_client, config)

    @application.exception_handler(StoreError)
    async def store_error(request: Request, exc: StoreError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    async def ready_paper(paper_id: str) -> None:
        try:
            paper = await rag_client.paper_status(paper_id)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise HTTPException(404, "论文不存在") from exc
            raise HTTPException(503, "论文服务暂时不可用") from exc
        except httpx.RequestError as exc:
            raise HTTPException(503, "论文服务暂时不可用") from exc
        if paper["status"] != "ready":
            raise HTTPException(409, "论文尚未完成入库或已删除")

    @application.get("/health")
    async def health() -> dict:
        return {"status": "ok", "rag_base_url": config.rag_base_url}

    @application.post("/sessions", status_code=201)
    async def create_session(payload: SessionCreate) -> dict:
        await ready_paper(payload.paper_id)
        return public_session(database.create_session(payload.user_id, payload.paper_id, payload.title))

    @application.get("/sessions")
    async def list_sessions(user_id: UserId, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)) -> dict:
        return {"sessions": database.list_sessions(user_id, limit, offset)}

    @application.get("/sessions/{session_id}")
    async def get_session(session_id: str, user_id: UserId) -> dict:
        return public_session(database.get_session(session_id, user_id))

    @application.patch("/sessions/{session_id}")
    async def rename_session(session_id: str, user_id: UserId, payload: SessionRename) -> dict:
        return public_session(database.rename_session(session_id, user_id, payload.title))

    @application.get("/sessions/{session_id}/messages")
    async def get_messages(session_id: str, user_id: UserId, limit: int = Query(50, ge=1, le=100),
                           before_turn: int | None = Query(None, ge=1)) -> dict:
        messages = database.visible_messages(session_id, user_id, limit, before_turn)
        return {"session_id": session_id, "messages": messages,
                "next_before_turn": min((message["turn_index"] for message in messages), default=None)}

    @application.get("/sessions/{session_id}/runs")
    async def list_runs(session_id: str, user_id: UserId, limit: int = Query(50, ge=1, le=100),
                        offset: int = Query(0, ge=0)) -> dict:
        return {"runs": database.list_runs(session_id, user_id, limit, offset)}

    @application.get("/sessions/{session_id}/artifacts")
    async def session_artifacts(session_id: str, user_id: UserId, limit: int = Query(100, ge=1, le=200)) -> dict:
        return {"artifacts": database.artifacts(session_id, user_id, limit=limit)}

    @application.post("/sessions/{session_id}/runs", status_code=202)
    async def create_run(session_id: str, user_id: UserId, payload: RunCreate, request: Request) -> dict:
        return request.app.state.service.submit(session_id, user_id, payload.question, payload.request_id, mode=payload.mode)

    @application.get("/runs/{run_id}")
    async def get_run(run_id: str, user_id: UserId) -> dict:
        return database.get_run(run_id, user_id)

    @application.post("/runs/{run_id}/retry", status_code=202)
    async def retry_run(run_id: str, user_id: UserId, payload: RunRetry, request: Request) -> dict:
        previous = database.get_run(run_id, user_id)
        if previous["status"] not in ("failed", "interrupted"):
            raise HTTPException(409, "只有失败或中断任务可以重试")
        return request.app.state.service.submit(previous["session_id"], user_id, previous["question"],
                                                payload.request_id, retry_of=run_id, mode=previous["mode"])

    async def event_access(run_id: str, user_id: UserId,
                           after: int = Query(0, ge=0), last_event_id: Annotated[int | None, Header(ge=0)] = None) -> tuple[str, str, int]:
        run = database.get_run(run_id, user_id)
        cursor = max(after, last_event_id or 0)
        if cursor > run["last_event_seq"]:
            raise HTTPException(400, "事件游标超过已有事件范围")
        if run["status"] in TERMINAL and cursor == run["last_event_seq"]:
            raise HTTPException(204)
        return run_id, user_id, cursor

    @application.get("/runs/{run_id}/events", response_class=EventSourceResponse)
    async def events(request: Request, access: tuple = Depends(event_access)) -> AsyncIterable[ServerSentEvent]:
        run_id, user_id, cursor = access
        heartbeat_at = time.monotonic()
        while True:
            if await request.is_disconnected():
                return
            batch = database.events(run_id, cursor)
            for event in batch:
                cursor = event["seq"]
                yield ServerSentEvent(event=event["event"], id=str(cursor), data=event)
            snapshot = database.get_run(run_id, user_id)
            if snapshot["status"] in TERMINAL and cursor >= snapshot["last_event_seq"]:
                return
            if time.monotonic() - heartbeat_at >= 15:
                yield ServerSentEvent(comment="keep-alive")
                heartbeat_at = time.monotonic()
            if not batch:
                await asyncio.sleep(0.25)

    @application.get("/artifacts/{artifact_id}")
    async def download_artifact(artifact_id: str, user_id: UserId) -> FileResponse:
        artifact, path = database.artifact(artifact_id, user_id)
        return FileResponse(path, filename=artifact["filename"], media_type=artifact["media_type"])

    @application.get("/artifacts/{artifact_id}/preview")
    async def preview_artifact(artifact_id: str, user_id: UserId) -> FileResponse:
        artifact, path = database.artifact(artifact_id, user_id)
        if path.suffix.lower() != ".html" or not path.name.startswith("presentation-"):
            raise HTTPException(415, "该文件不支持演示预览")
        return FileResponse(path, media_type="text/html; charset=utf-8", headers={
            "Content-Security-Policy": "sandbox allow-scripts; default-src 'none'; style-src 'unsafe-inline'; img-src data:; script-src 'unsafe-inline'; connect-src 'none'; frame-ancestors 'self'; base-uri 'none'; form-action 'none'",
            "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})



    return application


app = create_app()
