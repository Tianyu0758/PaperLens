# 文件说明：根据 tokenizer 的计数把论文章节切成适合检索的文本块。
# 递归处理长文本、保留适量重叠，并为文本块加入章节信息和可重复生成的标识。

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Protocol

from transformers import AutoTokenizer

from app.tei_parser import ParsedSection


CHUNK_NAMESPACE = uuid.UUID("40baa45c-0895-4ef1-8348-5d93d75a1321")
SEPARATORS = ("\n\n", "\n", ". ", " ")


class TokenCodec(Protocol):
    def encode(self, text: str) -> list[int]: ...

    def decode(self, token_ids: list[int]) -> str: ...


class LazyHuggingFaceTokenCodec:
    def __init__(self, model_path: str) -> None:
        self.model_path = model_path
        self._tokenizer = None

    @property
    def tokenizer(self):
        if self._tokenizer is None:
            self._tokenizer = AutoTokenizer.from_pretrained(self.model_path)
        return self._tokenizer

    def encode(self, text: str) -> list[int]:
        return self.tokenizer.encode(text, add_special_tokens=False)

    def decode(self, token_ids: list[int]) -> str:
        return self.tokenizer.decode(token_ids, skip_special_tokens=True)


@dataclass(slots=True)
class ChunkDraft:
    chunk_id: str
    section_path: str
    section_type: str
    page_number: int | None
    chunk_index: int
    text: str

    def as_record(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "section_path": self.section_path,
            "section_type": self.section_type,
            "page_number": self.page_number,
            "chunk_index": self.chunk_index,
            "text": self.text,
        }


def _split_preserving_separator(text: str, separator: str) -> list[str]:
    parts = text.split(separator)
    output: list[str] = []
    for index, part in enumerate(parts):
        if not part:
            continue
        suffix = separator if index < len(parts) - 1 else ""
        output.append(part + suffix)
    return output


def _recursive_fragments(
    text: str,
    codec: TokenCodec,
    max_tokens: int,
    separator_index: int = 0,
) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(codec.encode(text)) <= max_tokens:
        return [text]
    if separator_index >= len(SEPARATORS):
        token_ids = codec.encode(text)
        return [
            codec.decode(token_ids[start : start + max_tokens]).strip()
            for start in range(0, len(token_ids), max_tokens)
        ]

    separator = SEPARATORS[separator_index]
    parts = _split_preserving_separator(text, separator)
    if len(parts) <= 1:
        return _recursive_fragments(text, codec, max_tokens, separator_index + 1)

    fragments: list[str] = []
    for part in parts:
        fragments.extend(
            _recursive_fragments(part, codec, max_tokens, separator_index + 1)
        )
    return fragments


def _pack_with_overlap(
    text: str,
    codec: TokenCodec,
    body_limit: int,
    overlap_tokens: int,
) -> list[str]:
    if len(codec.encode(text)) <= body_limit:
        return [text.strip()]
    fragment_limit = max(1, body_limit - overlap_tokens)
    fragments = _recursive_fragments(text, codec, fragment_limit)
    packed: list[str] = []
    current = ""
    for fragment in fragments:
        candidate = f"{current}\n{fragment}".strip() if current else fragment.strip()
        if current and len(codec.encode(candidate)) > body_limit:
            packed.append(current.strip())
            tail = codec.decode(codec.encode(current)[-overlap_tokens:]).strip()
            current = f"{tail}\n{fragment}".strip()
        else:
            current = candidate
    if current:
        packed.append(current.strip())
    return packed


def build_chunks(
    paper_id: str,
    sections: list[ParsedSection],
    codec: TokenCodec,
    target_tokens: int = 512,
    overlap_tokens: int = 180,
) -> list[ChunkDraft]:
    if overlap_tokens <= 0 or overlap_tokens >= target_tokens:
        raise ValueError("overlap_tokens must be between 1 and target_tokens - 1")
    chunks: list[ChunkDraft] = []
    chunk_index = 0
    for section in sections:
        body = "\n\n".join(section.paragraphs).strip()
        if not body:
            continue
        prefix = f"【{section.section_path}】"
        prefix_tokens = len(codec.encode(prefix + "\n"))
        body_limit = target_tokens - prefix_tokens
        if body_limit <= overlap_tokens:
            raise ValueError(f"section_path is too long: {section.section_path}")
        for child_body in _pack_with_overlap(
            body, codec, body_limit, overlap_tokens
        ):
            enriched = f"{prefix}\n{child_body}".strip()
            content_hash = hashlib.sha256(enriched.encode("utf-8")).hexdigest()
            identity = f"{paper_id}:{chunk_index}:{content_hash}"
            chunks.append(
                ChunkDraft(
                    chunk_id=str(uuid.uuid5(CHUNK_NAMESPACE, identity)),
                    section_path=section.section_path,
                    section_type=section.section_type,
                    page_number=None,
                    chunk_index=chunk_index,
                    text=enriched,
                )
            )
            chunk_index += 1
    return chunks
