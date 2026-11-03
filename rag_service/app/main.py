# 文件说明：RAG 服务的 FastAPI 启动入口，组装数据库、解析器、索引、检索器及问答流程。
# 生命周期中初始化依赖并挂载接口，关闭时释放客户端连接；各业务步骤由专门服务实现。

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.health import router as health_router
from app.api.frontend import router as frontend_router
from app.api.papers import router as papers_router
from app.api.upload import router as upload_router
from app.api.query import router as query_router
from app.answer_generation import AnswerGenerator
from app.config import PROJECT_ROOT, settings
from app.bm25_index import BM25IndexManager
from app.chunking import LazyHuggingFaceTokenCodec
from app.context_builder import ContextBuilder
from app.db import Database
from app.deletion import PaperDeletionService
from app.errors import register_exception_handlers
from app.embedding import EmbeddingClient
from app.logging_config import configure_logging
from app.grobid import GrobidClient
from app.ingestion import IngestionProcessor
from app.query_expansion import QueryExpander
from app.query_service import QueryService
from app.retrieval import HybridRetriever
from app.reranker_client import RerankerClient
from app.qdrant_store import QdrantChunkStore, create_qdrant_client, ensure_collection
from app.repositories import PaperRepository
from app.upload_service import UploadService


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_directories()
    logger = configure_logging(settings)
    database = Database(settings.sqlite_path)
    database.initialize()
    qdrant = await create_qdrant_client(settings)
    await qdrant.get_collections()
    await ensure_collection(qdrant, settings.qdrant_collection)

    repository = PaperRepository(database)
    app.state.database = database
    app.state.paper_repository = repository
    app.state.qdrant = qdrant
    app.state.upload_service = UploadService(
        repository=repository,
        upload_path=settings.upload_path,
        max_upload_bytes=settings.max_upload_mb * 1024 * 1024,
    )
    bm25 = BM25IndexManager(settings.bm25_index_path)
    embedding = EmbeddingClient(
        base_url=settings.embedding_url,
        model=settings.embedding_model,
    )
    vector_store = QdrantChunkStore(qdrant, settings.qdrant_collection)
    deletion_service = PaperDeletionService(
        repository=repository,
        vector_store=vector_store,
        bm25=bm25,
        upload_path=settings.upload_path,
        logger=logger,
    )
    app.state.deletion_service = deletion_service
    token_codec = LazyHuggingFaceTokenCodec(settings.embedding_tokenizer)
    app.state.ingestion_processor = IngestionProcessor(
        repository=repository,
        grobid=GrobidClient(settings.grobid_url),
        logger=logger,
        token_codec=token_codec,
        embedding=embedding,
        vector_store=vector_store,
        bm25=bm25,
    )
    retriever = HybridRetriever(
        expander=QueryExpander(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            model=settings.hyde_model,
            logger=logger,
        ),
        embedding=embedding,
        vector_store=vector_store,
        bm25=bm25,
        logger=logger,
        reranker=RerankerClient(settings.reranker_url, logger),
        rerank_top_k=settings.rerank_top_k,
    )
    app.state.retriever = retriever
    app.state.query_service = QueryService(
        retriever=retriever,
        context_builder=ContextBuilder(token_codec, logger, max_tokens=4000),
        generator=AnswerGenerator(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            model=settings.generation_model,
            logger=logger,
        ),
        repository=repository,
    )
    logger.info(
        "startup_complete env=%s collection=%s",
        settings.app_env,
        settings.qdrant_collection,
    )
    await deletion_service.resume_pending()
    try:
        yield
    finally:
        await qdrant.close()
        logger.info("shutdown_complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Academic RAG Demo",
        version="0.1.0",
        lifespan=lifespan,
    )
    register_exception_handlers(app)
    app.mount(
        "/static",
        StaticFiles(directory=PROJECT_ROOT / "app" / "static"),
        name="static",
    )
    app.include_router(frontend_router)
    app.include_router(health_router)
    app.include_router(papers_router)
    app.include_router(upload_router)
    app.include_router(query_router)
    return app


app = create_app()
