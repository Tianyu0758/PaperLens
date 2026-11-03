# 文件说明：定义 RAG 接口的请求与响应结构，包括论文状态、上传、查询、检索证据和引用。
# 使用 Pydantic 约束字段与取值，让接口文档、输入检查和返回格式保持一致。

from typing import Literal

from pydantic import BaseModel, Field


PaperStatus = Literal["processing", "ready", "failed", "deleting", "deleted"]


class PaperSummary(BaseModel):
    paper_id: str
    title: str | None = None
    year: int | None = None
    authors: str | None = None
    doi: str | None = None
    status: PaperStatus
    uploaded_at: str


class PaperStatusResponse(BaseModel):
    paper_id: str
    status: PaperStatus
    error_message: str | None = None
    updated_at: str


class ReadinessResponse(BaseModel):
    status: Literal["ready", "degraded"]
    sqlite: bool
    qdrant: bool


class UploadResponse(BaseModel):
    paper_id: str
    status: PaperStatus


class DeletePaperResponse(BaseModel):
    paper_id: str
    status: Literal["deleted"]


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    paper_id: str = Field(min_length=1)
    section_type: str | None = None


class Citation(BaseModel):
    paper_title: str
    section_path: str
    page_number: int | None = None


class QueryResponse(BaseModel):
    answer: str
    citations: list[Citation]
    timings: dict[str, int]


class EvidenceSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    section_type: str | None = None
    top_k: int = Field(default=5, ge=1, le=20)


class EvidenceChunk(BaseModel):
    chunk_id: str
    paper_id: str
    text: str
    section_path: str
    section_type: str | None = None
    page_number: int | None = None
    chunk_index: int | None = None
    score: float | None = None


class EvidenceSearchResponse(BaseModel):
    paper_id: str
    results: list[EvidenceChunk]
    timings: dict[str, int]
