# 文件说明：读取 RAG 配置，管理解析、向量模型、重排序、模型接口与各类存储路径。
# 外部环境文件仅复用指定凭据；论文数据库、文件和索引路径保持在独立 RAG 目录内。

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv, dotenv_values


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env", encoding="utf-8-sig")
for external_env_name in ("QDRANT_ENV_FILE", "DEEPSEEK_ENV_FILE"):
    external_env = os.getenv(external_env_name)
    if external_env:
        # An explicitly supplied external credential file must override the
        # checked-in local template values (for example, QDRANT_URL placeholder).
        # Share credentials only. Never inherit another project's storage or collection.
        credentials = dotenv_values(Path(external_env), encoding="utf-8-sig")
        for name in ("QDRANT_URL", "QDRANT_API_KEY", "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL"):
            if credentials.get(name):
                os.environ[name] = credentials[name]


def _path_setting(name: str, default: str) -> Path:
    value = Path(os.getenv(name, default))
    path = (value if value.is_absolute() else PROJECT_ROOT / value).resolve()
    if not path.is_relative_to(PROJECT_ROOT):
        raise ValueError(f"{name} must stay inside this independent rag_service")
    return path


def _model_setting(name: str, default: str) -> str:
    """模型 ID 原样使用，本地相对 tokenizer 目录按 RAG 根目录解析。"""
    value = os.getenv(name, default)
    path = Path(value).expanduser()
    if path.is_absolute():
        return str(path)
    if value.startswith('.') or (PROJECT_ROOT / path).exists():
        return str((PROJECT_ROOT / path).resolve())
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    app_env: str = os.getenv("APP_ENV", "development")
    grobid_url: str = os.getenv("GROBID_URL", "http://localhost:8070").rstrip("/")
    embedding_url: str = os.getenv("EMBEDDING_URL", "http://localhost:8003").rstrip("/")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B")
    embedding_tokenizer: str = _model_setting("EMBEDDING_TOKENIZER", os.getenv("EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B"))
    reranker_url: str = os.getenv("RERANKER_URL", "http://localhost:8004").rstrip("/")
    qdrant_url: str | None = os.getenv("QDRANT_URL")
    qdrant_api_key: str | None = os.getenv("QDRANT_API_KEY") or None
    qdrant_collection: str = os.getenv("HTML_QDRANT_COLLECTION", "paperlens_chunks")
    deepseek_api_key: str | None = os.getenv("DEEPSEEK_API_KEY")
    deepseek_base_url: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    hyde_model: str = os.getenv("HYDE_MODEL", "deepseek-v4-flash")
    generation_model: str = os.getenv("GENERATION_MODEL", "deepseek-v4-flash")
    bm25_index_path: Path = _path_setting("BM25_INDEX_PATH", "./data/bm25_index")
    sqlite_path: Path = _path_setting("SQLITE_PATH", "./data/papers.db")
    log_path: Path = _path_setting("LOG_PATH", "./data/logs")
    upload_path: Path = _path_setting("UPLOAD_PATH", "./uploads")
    max_upload_mb: int = int(os.getenv("MAX_UPLOAD_MB", "50"))
    rerank_top_k: int = int(os.getenv("RERANK_TOP_K", "5"))

    def ensure_directories(self) -> None:
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self.bm25_index_path.mkdir(parents=True, exist_ok=True)
        self.log_path.mkdir(parents=True, exist_ok=True)
        self.upload_path.mkdir(parents=True, exist_ok=True)

    def validate_qdrant(self) -> None:
        """本机无鉴权 Qdrant 可不填密钥；远程实例仍要求显式配置凭据。"""
        if not self.qdrant_url or urlsplit(self.qdrant_url).scheme not in {"http", "https"}:
            raise RuntimeError("QDRANT_URL must be an http(s) endpoint")
        if urlsplit(self.qdrant_url).hostname not in {"localhost", "127.0.0.1", "::1"} and not self.qdrant_api_key:
            raise RuntimeError("QDRANT_API_KEY must be configured for a remote Qdrant endpoint")


settings = Settings()
