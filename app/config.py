# 文件说明：读取新项目的环境变量，统一管理模型、RAG 地址、存储路径和任务限制。
# Settings 提供各模块共用的配置；数据目录限定在本项目内部，保持工作区文件隔离。

from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env", encoding="utf-8-sig")


def data_directory() -> Path:
    path = (ROOT / os.getenv("AGENT_DATA_DIR", "data")).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError("AGENT_DATA_DIR 必须位于 PaperLens 项目目录内")
    return path


@dataclass(frozen=True)
class Settings:
    rag_base_url: str = os.getenv("RAG_BASE_URL", "http://127.0.0.1:18081").rstrip("/")
    api_key: str | None = os.getenv("DEEPSEEK_API_KEY")
    base_url: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    model: str = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    data_dir: Path = data_directory()
    sandbox_image: str = os.getenv("SANDBOX_IMAGE", "python:3.12-slim")
    sandbox_timeout_seconds: int = int(os.getenv("SANDBOX_TIMEOUT_SECONDS", "30"))
    sandbox_memory: str = os.getenv("SANDBOX_MEMORY", "512m")
    sandbox_cpus: str = os.getenv("SANDBOX_CPUS", "0.5")
    docker_bin: str = os.getenv("DOCKER_BIN", "docker")
    context_max_tokens: int = int(os.getenv("AGENT_CONTEXT_MAX_TOKENS", "16000"))
    run_timeout_seconds: int = int(os.getenv("AGENT_RUN_TIMEOUT_SECONDS", "900"))
    max_upload_mb: int = int(os.getenv("AGENT_MAX_UPLOAD_MB", "50"))
    recursion_limit: int = int(os.getenv("AGENT_RECURSION_LIMIT", "48"))

    @property
    def database_path(self) -> Path:
        return self.data_dir / "agent.db"


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
