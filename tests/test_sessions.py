# 文件说明：验证会话与任务 API 的持久化、请求去重、并发约束、事件回放和文件访问。
# 覆盖连续追问、分页、服务重启、超时和当前接口边界，使用隔离数据库与执行器。

from __future__ import annotations

import concurrent.futures
import json
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.config import settings
from app.main import create_app
from app.store import AgentStore, StoreError
from tests.helpers import FakeRag, FakeRunner


class SessionAPITests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.config = replace(settings, data_dir=Path(self.directory.name), run_timeout_seconds=5)
        self.rag, self.runner = FakeRag(), FakeRunner()
        self.app = create_app(self.config, rag=self.rag, runner=self.runner)
        self.client = TestClient(self.app).__enter__()

    def tearDown(self):
        self.runner.gate.set()
        self.client.__exit__(None, None, None)
        self.directory.cleanup()

    def session(self, paper="paper-a", user="alice"):
        response = self.client.post("/sessions", json={"user_id": user, "paper_id": paper})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["session_id"]

    def submit(self, session, question="分析论文", request_id="req-1", user="alice"):
        response = self.client.post(f"/sessions/{session}/runs", params={"user_id": user},
                                    json={"question": question, "request_id": request_id})
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()["run_id"]

    def done(self, run_id, user="alice"):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            response = self.client.get(f"/runs/{run_id}", params={"user_id": user})
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
            if result["status"] in ("completed", "failed", "interrupted"):
                return result
            time.sleep(0.01)
        self.fail("Task did not finish")

    def test_follow_up_restores_native_tool_messages_and_isolates_sessions(self):
        session = self.session()
        first = self.done(self.submit(session, "记住代号 BLUE"))
        self.assertEqual(first["status"], "completed", first)
        second = self.done(self.submit(session, "代号是什么", "req-2"))
        self.assertEqual(second["answer"], "代号是 BLUE")
        history = self.runner.calls[-1]["history"]
        assistant_call = next(item for item in history if getattr(item, "tool_calls", []))
        observation = next(item for item in history if item.type == "tool")
        self.assertEqual(assistant_call.tool_calls[0]["id"], observation.tool_call_id)
        other = self.session("paper-b")
        third = self.done(self.submit(other, "代号是什么"))
        self.assertEqual(third["answer"], "没有先前代号")
        self.assertEqual(third["paper_id"], "paper-b")
        visible = self.client.get(f"/sessions/{session}/messages?user_id=alice").json()["messages"]
        self.assertEqual([item["role"] for item in visible], ["user", "assistant", "user", "assistant"])
        self.assertTrue(visible[-1]["artifacts"])

    def test_duplicate_request_and_one_active_run(self):
        session = self.session()
        self.runner.gate.clear()
        first = self.submit(session)
        duplicate = self.submit(session)
        self.assertEqual(first, duplicate)
        conflict = self.client.post(f"/sessions/{session}/runs?user_id=alice", json={"question": "另一个问题", "request_id": "req-2"})
        self.assertEqual(conflict.status_code, 409)
        changed_payload = self.client.post(f"/sessions/{session}/runs?user_id=alice", json={"question": "更改原问题", "request_id": "req-1"})
        self.assertEqual(changed_payload.status_code, 409)
        self.runner.gate.set()
        self.assertEqual(self.done(first)["status"], "completed")
        self.assertEqual(self.submit(session), first)
        self.assertEqual(len(self.runner.calls), 1)

    def test_completed_history_and_downloads_survive_application_restart(self):
        session = self.session()
        run_id = self.submit(session)
        result = self.done(run_id)
        artifact = next(item for item in result["artifact_records"] if item["filename"] == "presentation.md")
        self.client.__exit__(None, None, None)
        self.app = create_app(self.config, rag=self.rag, runner=self.runner)
        self.client = TestClient(self.app).__enter__()
        self.assertEqual(self.client.get("/sessions?user_id=alice").json()["sessions"][0]["session_id"], session)
        self.assertEqual(len(self.client.get(f"/sessions/{session}/messages?user_id=alice").json()["messages"]), 2)
        self.assertEqual(self.client.get(f"/runs/{run_id}?user_id=alice").json()["answer"], result["answer"])
        response = self.client.get(artifact["download_url"], params={"user_id": "alice"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("分析论文", response.text)

    def test_events_replay_resume_and_terminal_204(self):
        run_id = self.submit(self.session())
        result = self.done(run_id)
        response = self.client.get(f"/runs/{run_id}/events?user_id=alice")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("text/event-stream", response.headers["content-type"])
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        self.assertEqual([item["seq"] for item in events], list(range(1, len(events) + 1)))
        self.assertEqual(events[-1]["event"], "run.completed")
        resumed = self.client.get(f"/runs/{run_id}/events?user_id=alice&after=1", headers={"Last-Event-ID": "3"})
        replay = [json.loads(line[6:]) for line in resumed.text.splitlines() if line.startswith("data: ")]
        self.assertEqual(replay[0]["seq"], 4)
        self.assertEqual(self.client.get(f"/runs/{run_id}/events?user_id=alice&after={result['last_event_seq']}").status_code, 204)
        self.assertEqual(self.client.get(f"/runs/{run_id}/events?user_id=alice&after=9999").status_code, 400)

    def test_failed_partial_tools_excluded_and_retry_has_new_run(self):
        session = self.session()
        failed_id = self.submit(session, "fail")
        self.assertEqual(self.done(failed_id)["status"], "failed")
        result = self.done(self.submit(session, "代号是什么", "req-2"))
        self.assertEqual(result["status"], "completed")
        self.assertFalse(any(getattr(item, "tool_calls", []) for item in self.runner.calls[-1]["history"]))
        response = self.client.post(f"/runs/{failed_id}/retry?user_id=alice", json={"request_id": "retry-1"})
        self.assertEqual(response.status_code, 202, response.text)
        self.assertNotEqual(response.json()["run_id"], failed_id)
        self.assertEqual(response.json()["retry_of"], failed_id)
        self.done(response.json()["run_id"])

    def test_owner_scoping_covers_sessions_runs_events_and_files(self):
        session = self.session()
        run_id = self.submit(session)
        artifact = self.done(run_id)["artifact_records"][0]
        for route in (f"/sessions/{session}", f"/sessions/{session}/messages", f"/sessions/{session}/runs",
                      f"/sessions/{session}/artifacts", f"/runs/{run_id}", f"/runs/{run_id}/events", artifact["download_url"]):
            self.assertEqual(self.client.get(route, params={"user_id": "bob"}).status_code, 404, route)
        self.assertEqual(self.client.get(f"/runs/{run_id}").status_code, 422)
        self.assertEqual(self.client.get(f"/runs/{run_id}/events").status_code, 422)
        self.assertEqual(self.client.get("/sessions?user_id=bob").json()["sessions"], [])

    def test_pagination_keeps_whole_turns(self):
        session = self.session()
        self.done(self.submit(session))
        self.done(self.submit(session, "继续", "req-2"))
        page = self.client.get(f"/sessions/{session}/messages?user_id=alice&limit=1").json()
        self.assertEqual(len(page["messages"]), 2)
        self.assertEqual({item["turn_index"] for item in page["messages"]}, {2})
        older = self.client.get(f"/sessions/{session}/messages?user_id=alice&limit=1&before_turn={page['next_before_turn']}").json()
        self.assertEqual(len(older["messages"]), 2)
        self.assertEqual({item["turn_index"] for item in older["messages"]}, {1})

    def test_shutdown_marks_active_task_interrupted(self):
        session = self.session()
        self.runner.gate.clear()
        run_id = self.submit(session)
        self.client.__exit__(None, None, None)
        self.app = create_app(self.config, rag=self.rag, runner=self.runner)
        self.client = TestClient(self.app).__enter__()
        self.assertEqual(self.client.get(f"/runs/{run_id}?user_id=alice").json()["status"], "interrupted")
        self.runner.gate.set()
        retry = self.client.post(f"/runs/{run_id}/retry?user_id=alice", json={"request_id": "retry-1"})
        self.assertEqual(retry.status_code, 202)
        self.assertEqual(self.done(retry.json()["run_id"])["status"], "completed")

    def test_upload_create_session_and_download_with_current_routes(self):
        response = self.client.post("/papers/upload", files={"file": ("paper.pdf", b"%PDF-1.7\ntest", "application/pdf")})
        self.assertEqual(response.status_code, 200, response.text)
        session = self.session(response.json()["paper_id"])
        result = self.done(self.submit(session))
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["paper_id"], "uploaded-paper")
        artifact = result["artifact_records"][0]
        download = self.client.get(artifact["download_url"], params={"user_id":"alice"})
        self.assertEqual(download.status_code, 200)

    def test_removed_compatibility_routes_are_not_exposed(self):
        paths = self.client.get("/openapi.json").json()["paths"]
        self.assertNotIn("/runs", paths)
        self.assertNotIn("/runs/{run_id}/artifacts/{filename}", paths)
        self.assertEqual(self.client.post("/runs").status_code, 404)

    def test_validation_and_missing_paper(self):
        self.rag.states["not-ready"] = "processing"
        self.assertEqual(self.client.post("/sessions", json={"user_id": "alice", "paper_id": "not-ready"}).status_code, 409)
        session = self.session()
        self.assertEqual(self.client.post(f"/sessions/{session}/runs?user_id=alice", json={"question": "   ", "request_id": "r"}).status_code, 422)
        self.assertEqual(self.client.post("/sessions", json={"user_id": "alice"}).status_code, 422)
        self.assertEqual(self.client.get("/openapi.json").status_code, 200)

    def test_task_timeout_releases_session_for_next_request(self):
        self.client.__exit__(None, None, None)
        config = replace(self.config, run_timeout_seconds=0.1)
        self.app = create_app(config, rag=self.rag, runner=self.runner)
        self.client = TestClient(self.app).__enter__()
        session = self.session()
        self.runner.gate.clear()
        with self.assertLogs("app.service", level="ERROR"):
            failed = self.done(self.submit(session))
        self.assertEqual(failed["status"], "failed")
        self.assertIn("超时", failed["error"])
        self.runner.gate.set()
        self.assertEqual(self.done(self.submit(session, "继续", "req-2"))["status"], "completed")


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.store = AgentStore(self.root / "agent.db", self.root)
        self.store.initialize()
        self.session = self.store.create_session("alice", "paper")

    def tearDown(self):
        self.directory.cleanup()

    def test_startup_recovers_persisted_queued_and_running_tasks(self):
        first, _ = self.store.create_run(self.session["session_id"], "alice", "q", "r")
        other = self.store.create_session("alice", "other")
        second, _ = self.store.create_run(other["session_id"], "alice", "q", "r")
        self.store.start_run(second["run_id"])
        reopened = AgentStore(self.root / "agent.db", self.root)
        reopened.initialize()
        self.assertEqual(reopened.recover_interrupted(), 2)
        self.assertEqual(reopened.recover_interrupted(), 0)
        for run in (first, second):
            self.assertEqual(reopened.get_run(run["run_id"], "alice")["status"], "interrupted")
            self.assertEqual(reopened.events(run["run_id"])[-1]["event"], "run.interrupted")

    def test_concurrent_submissions_are_atomic(self):
        def submit(index):
            try:
                return self.store.create_run(self.session["session_id"], "alice", "question", f"r-{index}")[0]["status"]
            except StoreError as error:
                return error.status_code
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(submit, range(2)))
        self.assertCountEqual(results, ["queued", 409])

    def test_artifact_path_cannot_escape_run_directory(self):
        run, _ = self.store.create_run(self.session["session_id"], "alice", "q", "r")
        self.store.start_run(run["run_id"])
        (self.root / "private.txt").write_text("private", encoding="utf-8")
        with self.assertRaises(StoreError):
            self.store.register_artifact(run["run_id"], "../../private.txt", "text/plain")
        with self.assertRaises(StoreError):
            self.store.resolve_run_file("../..", "private.txt")
