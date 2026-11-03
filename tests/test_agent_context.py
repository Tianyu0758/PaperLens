# 文件说明：验证主 Agent 的原生工具消息、上下文恢复、历史压缩及消息配对。
# 同时检查问答和研究模式的工具范围，以及失败轮次和会话文件的隔离。

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agent import run_agent
from app.config import settings
from app.context import ContextBuilder, summarize_dialogue, validate_tool_pairs
from app.runtime import RunRuntime
from app.sandbox import DockerPythonSandbox
from app.store import AgentStore
from tests.helpers import FakeRag, ScriptedChatModel


class AgentContextTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.config = replace(settings, data_dir=self.root)
        self.store = AgentStore(self.root / "agent.db", self.root)
        self.store.initialize()
        self.session = self.store.create_session("alice", "paper-a")
        self.rag = FakeRag()

    async def asyncTearDown(self):
        self.directory.cleanup()

    def new_run(self, question, request_id):
        run, _ = self.store.create_run(self.session["session_id"], "alice", question, request_id)
        self.store.start_run(run["run_id"])
        runtime = RunRuntime(self.store, run["run_id"], self.session["session_id"], "alice")
        directory = self.root / "runs" / run["run_id"]
        sandbox = DockerPythonSandbox(directory)
        return run, runtime, directory, sandbox

    async def invoke(self, question, request_id, model):
        run, runtime, directory, sandbox = self.new_run(question, request_id)
        user = self.store.user_message(run["run_id"])
        history = await ContextBuilder(self.store, self.config).build(runtime, user)
        with patch("app.agent.ChatOpenAI", return_value=model):
            result = await run_agent(self.rag, "paper-a", question, sandbox, directory,
                                     history=history, user_message=user, runtime=runtime)
        messages = result.pop("_messages")
        self.store.complete_run(run["run_id"], result, messages)
        return run, result

    async def test_real_langchain_loop_persists_calls_and_counts_only_current_turn(self):
        first_model = ScriptedChatModel(responses=[
            AIMessage(content="", tool_calls=[{"name": "search_paper", "args": {"question": "method"}, "id": "native-call-1"}]),
            AIMessage(content="方法包含两部分。[Methods，第3页]"),
        ])
        run, result = await self.invoke("解释方法", "first", first_model)
        self.assertEqual(result["evidence_chunk_count"], 1)
        self.assertEqual(len(result["tool_calls"]), 1)
        self.assertEqual(result["citations"][0]["chunk_id"], "chunk-paper-a")
        events = self.store.events(run["run_id"])
        start = next(item for item in events if item["event"] == "tool.started")
        self.assertEqual(start["data"]["call_id"], "native-call-1")
        self.assertEqual(events[-1]["event"], "run.completed")
        follow_up = ScriptedChatModel(responses=[AIMessage(content="第二部分是对照实验，沿用前面的证据。")])
        _, second = await self.invoke("详细解释第二部分", "second", follow_up)
        self.assertEqual(second["evidence_chunk_count"], 0)
        self.assertEqual(second["citations"], [])
        self.assertEqual(second["tool_calls"], [])
        restored = follow_up.seen[0]
        call = next(message for message in restored if getattr(message, "tool_calls", []))
        observation = next(message for message in restored if message.type == "tool")
        self.assertEqual(call.tool_calls[0]["id"], observation.tool_call_id)
        self.assertTrue(validate_tool_pairs(restored))

    async def test_tool_failure_records_event_and_keeps_protocol_paired(self):
        async def fail(*args, **kwargs):
            raise RuntimeError("retrieval unavailable")
        self.rag.search = fail
        model = ScriptedChatModel(responses=[
            AIMessage(content="", tool_calls=[{"name": "search_paper", "args": {"question": "method"}, "id": "failed-call"}]),
            AIMessage(content="检索失败，现有证据不足。"),
        ])
        run, _ = await self.invoke("解释方法", "first", model)
        event = next(item for item in self.store.events(run["run_id"]) if item["event"] == "tool.failed")
        self.assertIn("retrieval unavailable", event["data"]["error"])
        messages = self.store.completed_turns(self.session["session_id"])[0][1]
        self.assertTrue(validate_tool_pairs(messages))
        self.assertEqual(next(item for item in messages if item.type == "tool").tool_call_id, "failed-call")

    async def test_tools_are_grouped_by_read_only_and_research_capabilities(self):
        from app.agent import build_agent
        _, runtime, directory, sandbox = self.new_run("tools", "tools")
        with patch("app.agent.ChatOpenAI"), patch("app.agent.create_agent") as factory:
            build_agent(self.rag,"paper-a",sandbox,directory,runtime,mode="agent")
            names={tool.name for tool in factory.call_args.kwargs["tools"]}
            self.assertEqual(names,{"search_paper","inspect_paper","read_paper","list_artifacts","read_artifact","execute_python","plan_presentation","render_presentation","preview_presentation"})
            build_agent(self.rag,"paper-a",sandbox,directory,runtime,mode="qa")
            self.assertEqual({tool.name for tool in factory.call_args.kwargs["tools"]},{"search_paper","inspect_paper","read_paper","list_artifacts","read_artifact"})

    async def test_context_compaction_retains_raw_history_and_reuses_saved_summary(self):
        for index in range(3):
            run, _, _, _ = self.new_run("问题" + str(index), str(index))
            self.store.complete_run(run["run_id"], {"answer": "BLUE"}, [AIMessage(content="旧证据" * 3000)])
        _, runtime, _, _ = self.new_run("继续追问", "current")
        calls = []

        async def summarize(config, previous, turns):
            calls.append([turn for turn, _ in turns])
            return "前三轮讨论的代号为 BLUE，主要关注论文的方法。"

        builder = ContextBuilder(self.store, replace(self.config, context_max_tokens=6500), summarize)
        history = await builder.build(runtime, HumanMessage(content="继续追问"))
        self.assertTrue(any("BLUE" in str(item.content) for item in history))
        self.assertEqual(calls, [[1, 2, 3]])
        await builder.build(runtime, HumanMessage(content="另一个追问"))
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(self.store.completed_turns(self.session["session_id"])[0][1][-1].content), 9000)

    async def test_artifact_reader_rejects_other_sessions(self):
        run, runtime, directory, _ = self.new_run("q", "first")
        (directory / "notes.md").write_text("private session", encoding="utf-8")
        source = runtime.register_outputs(["notes.md"])[0]
        other = self.store.create_session("alice", "paper-b")
        other_run, _ = self.store.create_run(other["session_id"], "alice", "q", "other")
        other_runtime = RunRuntime(self.store, other_run["run_id"], other["session_id"], "alice")
        with self.assertRaisesRegex(Exception, "文件不存在"):
            other_runtime.read_artifact(source["artifact_id"])

    async def test_summary_processes_middle_of_long_answers(self):
        prompts = []

        class Summarizer:
            async def ainvoke(self, messages):
                prompts.append(messages[-1]["content"])
                return SimpleNamespace(content="已合并的上下文摘要")

        messages = [HumanMessage(content="用户问题"), AIMessage(content="A" * 8000 + "B" * 8000 + "C" * 3000)]
        with patch("app.context.ChatOpenAI", return_value=Summarizer()):
            summary = await summarize_dialogue(self.config, "", [(1, messages)])
        self.assertEqual(summary, "已合并的上下文摘要")
        self.assertEqual(len(prompts), 4)
        self.assertTrue(any("B" * 8000 in prompt for prompt in prompts))
        self.assertTrue(any("C" * 3000 in prompt for prompt in prompts))

    def test_tool_pair_validation_rejects_orphans(self):
        self.assertFalse(validate_tool_pairs([ToolMessage(content="x", tool_call_id="missing")]))
        self.assertFalse(validate_tool_pairs([AIMessage(content="", tool_calls=[{"name": "search", "args": {}, "id": "x"}]), AIMessage(content="final")]))
