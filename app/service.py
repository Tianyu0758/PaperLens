# 文件说明：管理后台任务的生命周期，连接论文就绪检查、上下文恢复、Agent 执行与结果持久化。
# RunService 接收任务后异步运行，处理超时、失败、服务关闭和文件登记，供 HTTP 接口调用。

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

from app.agent import run_agent
from app.config import Settings
from app.context import ContextBuilder, validate_tool_pairs
from app.rag_client import RagClient
from app.runtime import RunRuntime
from app.sandbox import DockerPythonSandbox
from app.store import AgentStore, StoreError

logger = logging.getLogger(__name__)


class RunService:
    def __init__(self, store: AgentStore, rag: RagClient, settings: Settings,
                 *, runner: Callable = run_agent, summarizer: Callable | None = None):
        self.store, self.rag, self.settings, self.runner = store, rag, settings, runner
        self.context = ContextBuilder(store, settings, summarizer)
        self.tasks: dict[str, asyncio.Task] = {}

    def submit(self, session_id: str, user_id: str, question: str, request_id: str,
               *, retry_of: str | None = None, mode: str = "agent") -> dict:
        session = self.store.get_session(session_id, user_id)
        if session["paper_id"] is None:
            raise StoreError(409, "会话尚未绑定论文，请先上传 PDF")
        run, created = self.store.create_run(session_id, user_id, question, request_id, retry_of=retry_of, mode=mode)
        if created:
            task = asyncio.create_task(self.execute(run["run_id"], user_id), name=run["run_id"])
            self.tasks[run["run_id"]] = task
            task.add_done_callback(lambda done: self.tasks.pop(run["run_id"], None))
        return self.store.get_run(run["run_id"], user_id)

    async def execute(self, run_id: str, user_id: str) -> None:
        started = time.perf_counter()
        if not self.store.start_run(run_id):
            return
        try:
            async with asyncio.timeout(self.settings.run_timeout_seconds):
                run = self.store.get_run(run_id, user_id)
                runtime = RunRuntime(self.store, run_id, run["session_id"], user_id)
                paper_id = run["paper_id"]
                self.store.set_stage(run_id, "validating_paper")
                paper = await self.rag.paper_status(paper_id)
                if paper["status"] != "ready":
                    raise ValueError("论文尚未就绪或已删除，请检查论文库")
                user_message = self.store.user_message(run_id)
                self.store.set_stage(run_id, "loading_context")
                history = await self.context.build(runtime, user_message)
                run_dir = self.settings.data_dir / "runs" / run_id
                sandbox = DockerPythonSandbox(run_dir, image=self.settings.sandbox_image,
                    timeout_seconds=self.settings.sandbox_timeout_seconds,
                    memory=self.settings.sandbox_memory, cpus=self.settings.sandbox_cpus,
                    docker_bin=self.settings.docker_bin)
                self.store.set_stage(run_id, "analyzing")
                result = await self.runner(self.rag, paper_id, run["question"], sandbox, run_dir,
                                          history=history, user_message=user_message, runtime=runtime, mode=run["mode"])
                messages = result.pop("_messages", [])
                if not messages or messages[-1].type != "ai" or getattr(messages[-1], "tool_calls", []) or not validate_tool_pairs(messages):
                    raise ValueError("Agent 返回的本轮消息不完整")
                if not isinstance(result.get("answer"), str) or not result["answer"].strip():
                    raise ValueError("Agent 最终回答为空")
                runtime.register_outputs(result.get("artifacts", []))
                result["artifact_records"] = self.store.artifacts(run["session_id"], user_id, run_id=run_id)
                result["timings"] = {**result.get("timings", {}), "total_ms": int((time.perf_counter() - started) * 1000)}
                self.store.complete_run(run_id, result, messages)
        except asyncio.CancelledError:
            self.store.end_run(run_id, "interrupted", "服务正在关闭，任务已中断，可重试")
            raise
        except Exception as exc:
            logger.exception("agent_run_failed run_id=%s", run_id)
            error = "任务执行超时，可重试" if isinstance(exc, TimeoutError) else str(exc)
            self.store.end_run(run_id, "failed", error,
                               {"total_ms": int((time.perf_counter() - started) * 1000)})

    async def close(self) -> None:
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.store.recover_interrupted()
