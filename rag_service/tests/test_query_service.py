# 文件说明：验证问答服务如何连接检索、上下文构建和回答生成。
# 使用各阶段替身，检查无证据时不生成回答，以及正常结果中的引用与耗时。

from types import SimpleNamespace

import pytest

from app.answer_generation import GeneratedAnswer
from app.context_builder import AssembledContext
from app.query_service import QueryService


class FakeRepository:
    def get(self, paper_id):
        return {"paper_id": paper_id, "title": "A Study"}


class FakeContextBuilder:
    def build(self, candidates):
        return AssembledContext("context", candidates, 10, 0)


class FakeGenerator:
    def __init__(self):
        self.calls = 0

    async def generate(self, question, context):
        self.calls += 1
        return GeneratedAnswer("answer", 12)


class FakeRetriever:
    def __init__(self, candidates):
        self.candidates = candidates

    async def retrieve(self, query, **kwargs):
        return SimpleNamespace(candidates=self.candidates, timings={"hyde_ms": 1})


@pytest.mark.asyncio
async def test_query_service_skips_generation_for_empty_retrieval() -> None:
    generator = FakeGenerator()
    service = QueryService(
        retriever=FakeRetriever([]),
        context_builder=FakeContextBuilder(),
        generator=generator,
        repository=FakeRepository(),
    )
    result = await service.answer("question", paper_id="p1")
    assert result["answer"] == "没有找到相关内容"
    assert result["citations"] == []
    assert generator.calls == 0


@pytest.mark.asyncio
async def test_query_service_returns_citations_and_timings() -> None:
    candidate = {
        "paper_id": "p1",
        "text": "【Methods】content",
        "section_path": "Methods",
        "page_number": None,
    }
    service = QueryService(
        retriever=FakeRetriever([candidate]),
        context_builder=FakeContextBuilder(),
        generator=FakeGenerator(),
        repository=FakeRepository(),
    )
    result = await service.answer("question", paper_id="p1")
    assert result["answer"] == "answer"
    assert result["citations"] == [
        {
            "paper_title": "A Study",
            "section_path": "Methods",
            "page_number": None,
        }
    ]
    assert result["timings"]["generate_ms"] == 12
    assert "total_ms" in result["timings"]
