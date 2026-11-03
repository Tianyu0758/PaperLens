# 文件说明：提供服务存活与就绪状态接口，区分进程可访问和关键依赖可用。
# 启动脚本通过这些接口等待 RAG 准备完成，也可用于手工排查服务状态。

from fastapi import APIRouter, Depends, Response, status
from qdrant_client import AsyncQdrantClient

from app.db import Database
from app.dependencies import get_database, get_qdrant
from app.schemas import ReadinessResponse


router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready", response_model=ReadinessResponse)
async def readiness(
    response: Response,
    database: Database = Depends(get_database),
    qdrant: AsyncQdrantClient = Depends(get_qdrant),
) -> ReadinessResponse:
    sqlite_ok = False
    qdrant_ok = False
    try:
        with database.connect() as connection:
            sqlite_ok = connection.execute("SELECT 1").fetchone()[0] == 1
    except Exception:
        sqlite_ok = False
    try:
        await qdrant.get_collections()
        qdrant_ok = True
    except Exception:
        qdrant_ok = False

    ready = sqlite_ok and qdrant_ok
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(
        status="ready" if ready else "degraded",
        sqlite=sqlite_ok,
        qdrant=qdrant_ok,
    )
