# 文件说明：重排序模型服务的 FastAPI 入口，管理模型加载生命周期并提供评分接口。
# 接收查询与候选文本，调用 engine.py 推理，返回可供 RAG 合并的候选分数和顺序。

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from reranker_service.engine import RerankerEngine
from reranker_service.schemas import RerankItem, RerankRequest, RerankResponse


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.engine = RerankerEngine(
        os.getenv("RERANKER_MODEL", "Qwen/Qwen3-Reranker-0.6B"),
        max_model_len=int(os.getenv("RERANKER_MAX_MODEL_LEN", "2048")),
        gpu_memory_utilization=float(os.getenv("RERANKER_GPU_MEMORY_UTILIZATION", "0.45")),
        dtype=os.getenv("RERANKER_DTYPE", "half"),
    )
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Qwen3 Reranker Service", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/rerank", response_model=RerankResponse)
    def rerank(request: Request, payload: RerankRequest) -> RerankResponse:
        scores = request.app.state.engine.score(
            payload.query,
            [document.text for document in payload.documents],
        )
        ranked = sorted(
            zip(payload.documents, scores, strict=True),
            key=lambda item: (-item[1], item[0].chunk_id),
        )[: payload.top_k]
        return RerankResponse(
            results=[
                RerankItem(chunk_id=document.chunk_id, score=score)
                for document, score in ranked
            ]
        )

    return app


app = create_app()
