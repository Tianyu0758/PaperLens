# 文件说明：把 FastAPI 请求中的数据库、论文仓储和 Qdrant 客户端提供给接口函数。
# 接口通过依赖注入复用启动时创建的资源，减少重复初始化和全局耦合。

from fastapi import Request
from qdrant_client import AsyncQdrantClient

from app.db import Database
from app.repositories import PaperRepository


def get_database(request: Request) -> Database:
    return request.app.state.database


def get_paper_repository(request: Request) -> PaperRepository:
    return request.app.state.paper_repository


def get_qdrant(request: Request) -> AsyncQdrantClient:
    return request.app.state.qdrant
