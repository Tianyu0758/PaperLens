# 文件说明：组织一次论文问答，把范围检查、混合检索、上下文拼接和回答生成连接起来。
# 返回回答、引用及耗时；没有有效证据时给出对应结果，避免无依据地进入生成流程。

from __future__ import annotations

import time
from typing import Protocol

from app.answer_generation import AnswerGenerator
from app.context_builder import ContextBuilder
from app.repositories import PaperRepository


class RetrievalProvider(Protocol):
    async def retrieve(self, query: str, **kwargs): ...


class QueryService:
    def __init__(
        self,
        *,
        retriever: RetrievalProvider,
        context_builder: ContextBuilder,
        generator: AnswerGenerator,
        repository: PaperRepository,
    ) -> None:
        self.retriever = retriever
        self.context_builder = context_builder
        self.generator = generator
        self.repository = repository

    async def answer(
        self,
        question: str,
        *,
        paper_id: str,
        section_type: str | None = None,
    ) -> dict:
        started = time.perf_counter()
        retrieval = await self.retriever.retrieve(
            question,
            paper_id=paper_id,
            section_type=section_type,
        )
        timings = dict(retrieval.timings)
        if not retrieval.candidates:
            timings["total_ms"] = int((time.perf_counter() - started) * 1000)
            return {
                "answer": "没有找到相关内容",
                "citations": [],
                "timings": timings,
            }

        assembled = self.context_builder.build(retrieval.candidates)
        if not assembled.candidates:
            timings["total_ms"] = int((time.perf_counter() - started) * 1000)
            return {
                "answer": "没有找到相关内容",
                "citations": [],
                "timings": timings,
            }
        generated = await self.generator.generate(question, assembled.text)
        timings["generate_ms"] = generated.generate_ms
        timings["total_ms"] = int((time.perf_counter() - started) * 1000)

        paper_titles: dict[str, str] = {}
        citations: list[dict] = []
        seen: set[tuple] = set()
        for candidate in assembled.candidates:
            candidate_paper_id = str(candidate.get("paper_id") or paper_id)
            if candidate_paper_id not in paper_titles:
                paper = self.repository.get(candidate_paper_id)
                paper_titles[candidate_paper_id] = (
                    str(paper.get("title") or "标题未知") if paper else "标题未知"
                )
            citation = {
                "paper_title": paper_titles[candidate_paper_id],
                "section_path": str(candidate.get("section_path") or "章节未知"),
                "page_number": candidate.get("page_number"),
            }
            identity = tuple(citation.values())
            if identity not in seen:
                seen.add(identity)
                citations.append(citation)
        return {
            "answer": generated.text,
            "citations": citations,
            "timings": timings,
        }
