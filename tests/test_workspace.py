# 文件说明：验证统一页面资源、同源论文接口以及运行模式的保存和权限边界。
# 同时检查上传限制、错误展示、旧数据库迁移，以及问答模式不能执行计算工具。

import json
import sqlite3
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.agent import run_agent
from app.config import settings
from app.main import create_app
from app.runtime import RunRuntime
from app.store import AgentStore
from tests.helpers import FakeRag, FakeRunner, ScriptedChatModel


class WorkspaceRag(FakeRag):
    async def list_papers(self, limit=20):
        return [{"paper_id": "paper-a", "title": "A real paper title", "status": "ready"}][:limit]

    async def upload_pdf(self, filename, content):
        self.uploaded = (filename, content)
        return {"paper_id": "new-paper", "status": "processing"}


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.config = replace(settings, data_dir=Path(self.directory.name), max_upload_mb=1)
        self.rag, self.runner = WorkspaceRag(), FakeRunner()
        self.client = TestClient(create_app(self.config, rag=self.rag, runner=self.runner)).__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.directory.cleanup()

    def test_workspace_assets_and_same_origin_paper_bridge(self):
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("PaperLens", page.text)
        self.assertIn("/assets/app.js", page.text)
        self.assertEqual(self.client.get("/assets/app.js").status_code, 200)
        self.assertEqual(self.client.get("/assets/styles.css").status_code, 200)
        self.assertEqual(self.client.get("/papers").json()[0]["paper_id"], "paper-a")
        self.assertEqual(self.client.get("/papers/paper-a/status").json()["status"], "ready")
        response = self.client.post("/papers/upload", files={"file": ("论文.pdf", b"%PDF-1.7\ntest", "application/pdf")})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "processing")
        self.assertEqual(self.rag.uploaded, ("论文.pdf", b"%PDF-1.7\ntest"))

    def test_invalid_and_oversize_uploads_never_reach_rag(self):
        for name, content, expected in [("bad.txt", b"%PDF-1.7", 415), ("bad.pdf", b"not-pdf", 400),
                                         ("big.pdf", b"%PDF-" + b"a" * (1024 * 1024), 413)]:
            response = self.client.post("/papers/upload", files={"file": (name, content, "application/pdf")})
            self.assertEqual(response.status_code, expected, response.text)
        self.assertFalse(hasattr(self.rag, "uploaded"))

    def test_unavailable_rag_is_reported_without_breaking_workspace(self):
        with patch.object(self.rag, "list_papers", new=AsyncMock(side_effect=httpx.ConnectError("offline"))):
            response = self.client.get("/papers")
        self.assertEqual(response.status_code, 503)
        self.assertIn("RAG", response.json()["detail"])
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_mode_is_persisted_deduplicated_and_preserved_by_retry(self):
        sid = self.client.post("/sessions", json={"user_id": "alice", "paper_id": "paper-a"}).json()["session_id"]
        url = f"/sessions/{sid}/runs?user_id=alice"
        payload = {"question": "fail", "request_id": "same", "mode": "qa"}
        run = self.client.post(url, json=payload).json()
        self.assertEqual(run["mode"], "qa")
        for _ in range(100):
            result = self.client.get(f"/runs/{run['run_id']}?user_id=alice").json()
            if result["status"] == "failed": break
            time.sleep(.01)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.runner.calls[0]["mode"], "qa")
        self.assertEqual(self.client.post(url, json=payload).json()["run_id"], run["run_id"])
        self.assertEqual(self.client.post(url, json={**payload, "mode": "agent"}).status_code, 409)
        retried = self.client.post(f"/runs/{run['run_id']}/retry?user_id=alice", json={"request_id": "retry"})
        self.assertEqual(retried.status_code, 202)
        self.assertEqual(retried.json()["mode"], "qa")
        self.assertEqual(self.client.post(url, json={**payload, "mode": "invalid"}).status_code, 422)

    def test_existing_database_migrates_without_losing_records(self):
        path = Path(self.directory.name) / "old.db"
        db = sqlite3.connect(path)
        db.execute("""CREATE TABLE runs (run_id TEXT PRIMARY KEY,session_id TEXT,request_id TEXT,turn_index INTEGER,
            question TEXT,status TEXT,stage TEXT,error TEXT,result_json TEXT DEFAULT '{}',retry_of TEXT,
            created_at TEXT,started_at TEXT,finished_at TEXT)""")
        db.execute("INSERT INTO runs(run_id,session_id,question,status) VALUES('old-run','old-session','original','completed')")
        db.commit(); db.close()
        store = AgentStore(path, Path(self.directory.name))
        store.initialize(); store.initialize()
        with store.connect() as connection:
            row = dict(connection.execute("SELECT * FROM runs WHERE run_id='old-run'").fetchone())
        self.assertEqual(row["mode"], "agent")
        self.assertEqual(row["question"], "original")


class QAModeTests(unittest.IsolatedAsyncioTestCase):
    async def test_qa_mode_cannot_execute_python_even_if_model_requests_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); store = AgentStore(root / "test.db", root); store.initialize()
            session = store.create_session("alice", "paper-a")
            run, _ = store.create_run(session["session_id"], "alice", "calculate", "qa", mode="qa")
            store.start_run(run["run_id"])
            runtime = RunRuntime(store, run["run_id"], session["session_id"], "alice")
            model = ScriptedChatModel(responses=[AIMessage(content="", tool_calls=[{
                "name": "execute_python", "args": {"code": "print(1)"}, "id": "disabled-call"}]),
                AIMessage(content="请切换研究助手模式以执行计算。")])
            sandbox = AsyncMock()
            with patch("app.agent.ChatOpenAI", return_value=model):
                result = await run_agent(FakeRag(), "paper-a", "calculate", sandbox, root, runtime=runtime, mode="qa")
            sandbox.execute.assert_not_awaited()
            self.assertIn("研究助手", result["answer"])
