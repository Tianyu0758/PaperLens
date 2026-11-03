# 文件说明：为浏览器回归提供独立模拟服务，使用临时数据库、模拟论文和模拟回答。
# 生成带模拟标记的演示与测试文件，并提供测试标识接口，避免连接真实模型或生产数据。

"""Isolated browser-test server. No production database, model or RAG calls."""
import asyncio
import tempfile
from dataclasses import replace
from pathlib import Path

from langchain_core.messages import AIMessage

from app.config import settings
from app.main import create_app
from tests.helpers import FakeRunner
from tests.test_workspace import WorkspaceRag
from app.presentation_service import PresentationService
from examples.sample_deck import sample_deck


class BrowserRag(WorkspaceRag):
    def __init__(self):
        super().__init__()
        self.upload_checks = 0

    async def list_papers(self, limit=20):
        return [{"paper_id": "paper-a", "title": "Learning from Evidence: A Controlled Study", "authors": "Alex Chen; Lin Wang", "year": 2025, "status": "ready"},
                {"paper_id": "paper-b", "title": "Research Methods and Reproducible Evaluation", "authors": "M. Taylor", "year": 2024, "status": "ready"},
                *([{"paper_id":"new-paper", "title":"Uploaded Research Paper", "status":"ready"}] if self.upload_checks >= 2 else [])][:limit]

    async def paper_status(self, paper_id):
        if paper_id == "new-paper":
            self.upload_checks += 1
            return {"paper_id": paper_id, "status": "ready" if self.upload_checks >= 2 else "processing"}
        return await super().paper_status(paper_id)


class BrowserRunner(FakeRunner):
    def __init__(self):
        super().__init__()
        self.failed_sessions = set()

    async def __call__(self, rag, paper_id, question, sandbox, run_dir, **kwargs):
        await asyncio.sleep(5 if "slow" in question else .6)
        session_id = kwargs["runtime"].session_id
        if question == "fail" and session_id not in self.failed_sessions:
            self.failed_sessions.add(session_id)
            raise RuntimeError("用于验证重试的测试错误")
        result = await super().__call__(rag, paper_id, "retry passed" if question == "fail" else question, sandbox, run_dir, **kwargs)
        result["answer"] = "## 研究发现\n\n" + result["answer"] + "\n\n**关键结论**：根据受控实验分析研究方法。\n\n| 方法 | 结果 |\n| --- | --- |\n| 对照组 | 1 |\n| 新方法 | 2 |\n\n<script>window.injected=true</script>"
        deck = sample_deck().model_copy(deep=True)
        deck.paper_id = paper_id
        for evidence in deck.evidence:
            evidence.paper_id = paper_id
        service = PresentationService(rag,paper_id,sandbox,run_dir,kwargs["runtime"])
        deck.revision = service.next_revision(deck.deck_id)
        old_plans = [item for item in kwargs["runtime"].list_artifacts()["artifacts"] if item["kind"] == "presentation_plan"]
        deck.parent_artifact_id = old_plans[0]["artifact_id"] if old_plans else None
        if "修改" in question:
            deck.slides[3].lead = "本轮修改：逐步讲解方法过程。"
        saved = service.save_plan(deck)
        rendered = service.render(saved["plan_artifact_id"])
        result["artifacts"].extend([*saved["output_files"],*rendered["output_files"]])
        result["_messages"][-1] = AIMessage(content=result["answer"])
        return result


directory = tempfile.TemporaryDirectory(prefix="paperlens-browser-")
app = create_app(replace(settings, data_dir=Path(directory.name)), rag=BrowserRag(), runner=BrowserRunner())


@app.get("/__workspace_test__", include_in_schema=False)
async def fixture_marker():
    return {"fixture": "paperlens-e2e"}
