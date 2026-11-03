# 文件说明：保存本轮运行的工具消息、事件、引用和文件登记信息，供主 Agent 与子 Agent 共用。
# AuditMiddleware 在工具调用前后记录执行过程；RunRuntime 提供产物读取和工作区发现能力。
# 子 Agent 保留独立作用域和父调用标识，便于前端展示委派过程。

from __future__ import annotations

import contextvars
import json
import mimetypes
import time
from typing import Any, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import BaseMessage, ToolMessage

from app.store import AgentStore, StoreError

current_call_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("academic_tool_call", default=None)


class RunRuntime:
    def __init__(self, store: AgentStore, run_id: str, session_id: str, user_id: str,
                 *, scope: str = "main", parent_call_id: str | None = None, citations: list | None = None):
        self.store, self.run_id, self.session_id, self.user_id = store, run_id, session_id, user_id
        self.scope, self.parent_call_id = scope, parent_call_id
        self.citations = citations if citations is not None else []

    def child(self, scope: str = "presentation") -> RunRuntime:
        return RunRuntime(self.store, self.run_id, self.session_id, self.user_id,
                          scope=scope, parent_call_id=current_call_id.get(), citations=self.citations)

    def emit(self, event_type: str, **payload: Any) -> None:
        data = {"scope": self.scope, "parent_call_id": self.parent_call_id, **payload}
        if event_type == "stage.changed":
            self.store.set_stage(self.run_id, payload["stage"], data)
        else:
            self.store.append_event(self.run_id, event_type, data)

    def record(self, message: BaseMessage) -> None:
        self.store.record_message(self.run_id, message, scope=self.scope, parent_call_id=self.parent_call_id)

    def register_outputs(self, paths: list[str]) -> list[dict]:
        records = []
        for relative in dict.fromkeys(paths):
            media_type = mimetypes.guess_type(relative)[0] or "application/octet-stream"
            record, _ = self.store.register_artifact(self.run_id, relative, media_type)
            records.append(record)
        return records

    def read_artifact(self, artifact_id: str, max_chars: int = 30000) -> dict:
        metadata, path = self.store.artifact(artifact_id, self.user_id, session_id=self.session_id)
        if path.suffix.lower() not in (".md", ".txt", ".json", ".csv", ".tsv"):
            raise StoreError(400, "请读取对应的 Markdown 大纲或其他文本产物")
        limit = max(1000, min(max_chars, 60000))
        with path.open("r", encoding="utf-8") as handle:
            content = handle.read(limit + 1)
        source = self.store.get_run(metadata["run_id"], self.user_id)
        return {"artifact": metadata, "content": content[:limit], "truncated": len(content) > limit,
                "citations": source.get("citations", [])}

    def list_artifacts(self, limit: int = 50) -> dict:
        items = self.store.artifacts(self.session_id, self.user_id, limit=max(1, min(limit, 200)))
        for item in items:
            filename = item["filename"]
            item["kind"] = ("presentation_plan" if filename.endswith(".slides.json") else
                            "presentation_html" if filename.endswith(".html") else
                            "inspection_report" if filename.endswith(".check.json") else "file")
            if item["kind"] == "presentation_plan":
                _, path = self.store.artifact(item["artifact_id"], self.user_id, session_id=self.session_id)
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    item.update({k: data.get(k) for k in ("title", "deck_id", "revision", "parent_artifact_id")})
                except (ValueError, OSError):
                    item["warning"] = "结构化稿暂时无法解析"
        return {"artifacts": items, "note": "修改演示优先读取 presentation_plan，按 deck_id 和 revision 识别版本。"}


def _arguments_summary(arguments: dict) -> dict:
    summary = {}
    for key in ("question", "objective", "duration_minutes", "top_k", "artifact_id", "source_artifact_id"):
        if key in arguments:
            value = arguments[key]
            summary[key] = value[:250] if isinstance(value, str) else value
    if "code" in arguments:
        summary["code_chars"] = len(str(arguments["code"]))
    return summary


class AuditMiddleware(AgentMiddleware):
    """Persist native messages and emit observable tool events, without reasoning text."""

    def __init__(self, runtime: RunRuntime):
        self.run_runtime = runtime

    async def aafter_model(self, state: dict, runtime: Any) -> None:
        message = state["messages"][-1]
        if message.type == "ai":
            self.run_runtime.record(message)

    async def awrap_tool_call(self, request: Any, handler: Callable) -> ToolMessage:
        call = request.tool_call
        call_id, name = call["id"], call["name"]
        self.run_runtime.emit("tool.started", call_id=call_id, tool=name,
                              arguments=_arguments_summary(call.get("args", {})))
        started = time.perf_counter()
        token = current_call_id.set(call_id)
        try:
            try:
                message = await handler(request)
            except Exception as exc:
                message = ToolMessage(content=json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False),
                                      tool_call_id=call_id, name=name, status="error")
            if not isinstance(message, ToolMessage):
                raise TypeError("学术工具必须返回 ToolMessage")
            self.run_runtime.record(message)
            try:
                payload = json.loads(message.content) if isinstance(message.content, str) else {}
            except (ValueError, TypeError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            failed = message.status == "error" or payload.get("ok") is False or bool(payload.get("error"))
            self.run_runtime.emit(
                "tool.failed" if failed else "tool.completed", call_id=call_id, tool=name,
                duration_ms=int((time.perf_counter() - started) * 1000),
                result_count=len(payload.get("results", [])),
                **({"error": str(payload.get("error") or payload.get("stderr") or "工具执行失败")[:1000]} if failed else {}),
            )
            return message
        finally:
            current_call_id.reset(token)
