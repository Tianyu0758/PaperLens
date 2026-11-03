# 文件说明：将已经排序的证据拼成回答模型可使用的有限上下文。
# 按照 tokenizer 预算保留文本与引用对应关系，供 query_service.py 后续生成回答。

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol


class TokenEncoder(Protocol):
    def encode(self, text: str) -> list[int]: ...


@dataclass(slots=True)
class AssembledContext:
    text: str
    candidates: list[dict]
    token_count: int
    dropped_count: int


class ContextBuilder:
    def __init__(
        self,
        token_encoder: TokenEncoder,
        logger: logging.Logger,
        max_tokens: int = 4000,
    ) -> None:
        self.token_encoder = token_encoder
        self.logger = logger
        self.max_tokens = max_tokens

    def build(self, candidates: list[dict]) -> AssembledContext:
        parts: list[str] = []
        used: list[dict] = []
        total_tokens = 0
        for candidate in candidates:
            text = str(candidate.get("text") or "").strip()
            if not text:
                continue
            chunk_tokens = len(self.token_encoder.encode(text))
            if total_tokens + chunk_tokens > self.max_tokens:
                break
            parts.append(text)
            used.append(candidate)
            total_tokens += chunk_tokens

        dropped_count = len(candidates) - len(used)
        if dropped_count:
            self.logger.info(
                "context_truncated tokens=%d used=%d dropped=%d",
                total_tokens,
                len(used),
                dropped_count,
            )
        return AssembledContext(
            text="\n\n".join(parts),
            candidates=used,
            token_count=total_tokens,
            dropped_count=dropped_count,
        )
