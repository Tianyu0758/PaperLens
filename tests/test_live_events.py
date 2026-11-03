# 文件说明：验证 SSE 事件在任务完成前即可到达，并能观察执行过程。
# 使用本地测试服务检查浏览器断开订阅不会取消后台任务。

from __future__ import annotations

import asyncio
import json
import socket
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import httpx
import uvicorn
from langchain_core.messages import AIMessage

from app.config import settings
from app.main import create_app
from tests.helpers import FakeRag


class LiveEventTests(unittest.IsolatedAsyncioTestCase):
    async def test_progress_arrives_before_completion_and_disconnect_does_not_cancel_run(self):
        gate = asyncio.Event()

        async def runner(rag, paper_id, question, sandbox, run_dir, *, runtime, **kwargs):
            runtime.emit("tool.started", tool="search_paper", call_id="live-call")
            await gate.wait()
            runtime.emit("tool.completed", tool="search_paper", call_id="live-call", result_count=1)
            return {"answer": "completed after disconnect", "citations": [], "artifacts": [],
                    "_messages": [AIMessage(content="completed after disconnect")]}

        with tempfile.TemporaryDirectory() as directory:
            app = create_app(replace(settings, data_dir=Path(directory)), rag=FakeRag(), runner=runner)
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", log_level="error", access_log=False, ws="none"))
            task = asyncio.create_task(server.serve(sockets=[listener]))
            try:
                async with asyncio.timeout(5):
                    while not server.started:
                        await asyncio.sleep(0.01)
                async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=5, trust_env=False) as client:
                    created = await client.post("/sessions", json={"user_id": "alice", "paper_id": "paper"})
                    self.assertEqual(created.status_code, 201, created.text)
                    session = created.json()["session_id"]
                    run = (await client.post(f"/sessions/{session}/runs?user_id=alice", json={"question": "q", "request_id": "live"})).json()["run_id"]
                    cursor = None
                    async with client.stream("GET", f"/runs/{run}/events?user_id=alice") as response:
                        self.assertEqual(response.status_code, 200)
                        async for line in response.aiter_lines():
                            if line.startswith("data: "):
                                event = json.loads(line[6:])
                                if event["event"] == "tool.started":
                                    cursor = event["seq"]
                                    break
                    self.assertIsNotNone(cursor)
                    self.assertEqual((await client.get(f"/runs/{run}?user_id=alice")).json()["status"], "running")
                    gate.set()
                    async with asyncio.timeout(5):
                        while (await client.get(f"/runs/{run}?user_id=alice")).json()["status"] != "completed":
                            await asyncio.sleep(0.01)
                    resumed = await client.get(f"/runs/{run}/events?user_id=alice", headers={"Last-Event-ID": str(cursor)})
                    events = [json.loads(line[6:]) for line in resumed.text.splitlines() if line.startswith("data: ")]
                    self.assertEqual(events[0]["seq"], cursor + 1)
                    self.assertEqual(events[-1]["event"], "run.completed")
            finally:
                gate.set()
                server.should_exit = True
                await asyncio.wait_for(task, 5)
                listener.close()
