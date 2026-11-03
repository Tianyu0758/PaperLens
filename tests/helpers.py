# 文件说明：提供可控的 RAG、任务执行器和聊天模型替身，供后端测试复用。
# 模拟证据、消息和工具调用，让测试能够验证真实调度逻辑而不请求外部模型。

import asyncio
import json
import threading
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field


class FakeRag:
    def __init__(self):
        self.states: dict[str, str] = {}

    async def paper_status(self, paper_id):
        return {"paper_id": paper_id, "status": self.states.get(paper_id, "ready")}

    async def upload_pdf(self, filename, content):
        return {"paper_id": "uploaded-paper", "status": "ready"}


    async def search(self, paper_id, question, top_k=5):
        return {"results": [{"paper_id": paper_id, "chunk_id": f"chunk-{paper_id}",
            "text": "The method uses a controlled comparison.", "section_path": "Methods", "page_number": 3}],
            "timings": {"dense_ms": 2}}

    async def inspect(self, paper_id):
        return {"paper":{"paper_id":paper_id,"title":"Test paper"},"ingestion_id":"test-ingestion",
                "abstract":"A controlled study.","sections":[{"section_id":"methods","section_path":"Methods"}]}

    async def read(self,paper_id,**kwargs):
        return await self.search(paper_id,"read")


class FakeRunner:
    def __init__(self):
        self.calls = []
        self.gate = threading.Event()
        self.gate.set()

    async def __call__(self, rag, paper_id, question, sandbox, run_dir, *, history, user_message, runtime, mode="agent"):
        self.calls.append({"paper_id": paper_id, "question": question, "history": history, "mode": mode})
        while not self.gate.is_set():
            await asyncio.sleep(0.01)
        call_id = "call-" + runtime.run_id
        call = AIMessage(content="", tool_calls=[{"name": "search_paper", "args": {"question": question}, "id": call_id}])
        runtime.record(call)
        if question == "fail":
            raise RuntimeError("simulated failure")
        runtime.emit("tool.started", tool="search_paper", call_id=call_id)
        payload = await rag.search(paper_id, question)
        observation = ToolMessage(content=json.dumps(payload), tool_call_id=call_id)
        runtime.record(observation)
        runtime.emit("tool.completed", tool="search_paper", call_id=call_id, result_count=1)
        remembered = next((str(item.content) for item in history if item.type == "human" and "BLUE" in str(item.content)), "")
        answer = ("代号是 BLUE" if remembered else "没有先前代号") if question == "代号是什么" else f"{paper_id}: {question}"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "presentation.md").write_text("# " + answer, encoding="utf-8")
        (run_dir / "job_test").mkdir(exist_ok=True)
        (run_dir / "job_test" / "values.csv").write_text("value\n1\n", encoding="utf-8")
        final = AIMessage(content=answer)
        return {"answer": answer, "citations": payload["results"], "tool_calls": [], "evidence_chunk_count": 1,
                "artifacts": ["presentation.md", "job_test/values.csv"], "_messages": [call, observation, final]}


class ScriptedChatModel(BaseChatModel):
    responses: list[AIMessage]
    seen: list[list[BaseMessage]] = Field(default_factory=list)

    @property
    def _llm_type(self):
        return "scripted-academic-test"

    def bind_tools(self, tools: Any, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(list(messages))
        if not self.responses:
            raise AssertionError("Unexpected extra model invocation")
        return ChatResult(generations=[ChatGeneration(message=self.responses.pop(0))])
