# 文件说明：主 Agent 的组装与执行入口，把论文访问、计算、工作区和演示构建工具提供给模型。
# build_agent 按问答或研究助手模式选择工具；run_agent 执行模型与工具循环，并汇总本轮回答、引用和文件。
# 演示任务通过规划、渲染和检查工具协作完成；完整证据由运行时收集，避免重复塞入主 Agent 上下文。

from __future__ import annotations

import json
from typing import Any
from pathlib import Path

from langchain.agents import create_agent
from langchain.tools import tool
from langchain_openai import ChatOpenAI
from langchain_core.messages import BaseMessage, HumanMessage

from app.config import settings
from app.rag_client import RagClient
from app.sandbox import DockerPythonSandbox, compact_result
from app.context import validate_tool_pairs
from app.runtime import AuditMiddleware, RunRuntime
from app.presentation_service import PresentationService


def build_agent(rag: RagClient, paper_id: str, sandbox: DockerPythonSandbox, run_dir: Path,
                runtime: RunRuntime | None = None, *, mode: str = "agent"):
    plan_count = 0
    preview_count = 0

    def concise_presentation(result: dict) -> str:
        evidence = result.pop("citations", [])
        if runtime:
            runtime.citations.extend(evidence)
        result["evidence_count"] = len(evidence)
        return json.dumps(result, ensure_ascii=False)

    @tool
    async def search_paper(question: str, top_k: int = 5) -> str:
        """Search evidence chunks from the uploaded academic paper."""
        result = await rag.search(paper_id, question, top_k=max(1, min(top_k, 10)))
        return json.dumps(result, ensure_ascii=False)

    @tool
    async def execute_python(code: str) -> str:
        """Execute Python in the isolated Docker sandbox for calculations or charts."""
        result = await sandbox.execute(code)
        result["output_files"] = result.get("artifacts", [])
        if runtime:
            result["artifact_records"] = runtime.register_outputs(result["output_files"])
        return compact_result(result)

    @tool
    async def read_artifact(artifact_id: str) -> str:
        """Read a previous Markdown outline or text artifact belonging to this conversation."""
        if runtime is None:
            raise ValueError("当前运行未连接会话文件记录")
        return json.dumps(runtime.read_artifact(artifact_id), ensure_ascii=False)

    @tool
    async def inspect_paper() -> str:
        """Browse the current paper's metadata, abstract and section IDs before comprehensive reading."""
        return json.dumps(await rag.inspect(paper_id), ensure_ascii=False)

    @tool
    async def read_paper(section_id: str | None = None, chunk_id: str | None = None,
                         offset: int = 0, limit: int = 8000, ingestion_id: str | None = None) -> str:
        """Read a known section/chunk from inspect/search; continue next_offset until enough context."""
        return json.dumps(await rag.read(paper_id, section_id=section_id, chunk_id=chunk_id,
            offset=max(0,offset),limit=max(100,min(limit,20000)),ingestion_id=ingestion_id),ensure_ascii=False)

    @tool
    async def list_artifacts(limit: int = 50) -> str:
        """Discover this conversation's previous outlines, presentation plans, HTML, charts and versions."""
        if runtime is None:
            raise ValueError("当前运行未连接会话文件记录")
        return json.dumps(runtime.list_artifacts(limit),ensure_ascii=False)

    def presentations():
        if runtime is None:
            raise ValueError("当前运行未连接会话文件记录")
        return PresentationService(rag,paper_id,sandbox,run_dir,runtime)

    @tool
    async def plan_presentation(objective: str, duration_minutes: int | None = None,
                                 audience: str | None = None, source_artifact_id: str | None = None) -> str:
        """Create or partially revise an evidence-based presentation plan; source must be a .slides.json artifact. Does not render HTML."""
        nonlocal plan_count
        if plan_count >= 3:
            raise ValueError("本轮已完成一次规划与两轮修订，请交付已有成果并说明尚存问题")
        plan_count += 1
        result = await presentations().plan(objective,max(5,min(120,duration_minutes)) if duration_minutes is not None else None,audience,source_artifact_id)
        result["remaining_revisions"] = 3-plan_count
        result["next_action"] = "仅要大纲时结束；需要成品时使用返回的 plan_artifact_id 调用 render_presentation"
        return concise_presentation(result)

    @tool
    async def render_presentation(plan_artifact_id: str) -> str:
        """Render an existing structured plan to a standalone offline HTML deck using stable templates."""
        result = presentations().render(plan_artifact_id)
        result["next_action"] = "使用返回的 html_artifact_id 调用一次 preview_presentation"
        return concise_presentation(result)

    @tool
    async def preview_presentation(html_artifact_id: str) -> str:
        """Inspect actual browser layout and interactions; returns page-specific issues, report and screenshots."""
        nonlocal preview_count
        if preview_count >= 3:
            raise ValueError("本轮已检查三次，请结束并说明现有检查结果")
        preview_count += 1
        result = await presentations().preview(html_artifact_id)
        result["next_action"] = "passed 时立即汇总交付；needs_revision 时仅修订问题页面；unavailable 时说明无法检查。同一 HTML 不重复检查。"
        return json.dumps(result,ensure_ascii=False)

    model = ChatOpenAI(
        model=settings.model,
        api_key=settings.api_key,
        base_url=settings.base_url,
        temperature=0,
    )
    return create_agent(
        model,
        tools=([search_paper, inspect_paper, read_paper, list_artifacts, read_artifact] if mode == "qa" else
               [search_paper, inspect_paper, read_paper, execute_python, list_artifacts, read_artifact,
                plan_presentation, render_presentation, preview_presentation]),
        middleware=[AuditMiddleware(runtime)] if runtime else [],
        system_prompt=(
            "你是学术论文分析助手。首次分析或需要补充论文事实时，调用 search_paper 获取证据。"
            "连续追问可以复用本会话已有证据；只能根据工具返回的论文证据作答，不要编造事实。"
            "全面分析先 inspect_paper 看结构；search_paper 找线索；read_paper 精读完整上下文。"
            "会话摘要和文件清单属于背景资料。用户提及先前成果时，用 list_artifacts 发现版本，再 read_artifact。"
            "制作组会演示默认生成 HTML。用户只要大纲时调用 plan_presentation 后停止，返回大纲和结构化稿。"
            "新建演示时把完整要求直接交给 plan_presentation，由规划子助手完成论文研究，避免主助手先重复研读。"
            "用户要完整演示时按需 plan_presentation、render_presentation、preview_presentation。"
            "修改已有演示先找对应 .slides.json，传 source_artifact_id，只修改本轮涉及部分。"
            "页面布局或交互配置变化可复用已有证据，出现新的论文事实才补检索。"
            "检查发现明确问题时修订结构化稿并重新渲染，最多两轮自动修正；检查不可用或仍有问题时如实说明。"
            "同一个 HTML 只检查一次。检查 passed 后直接完成回答，不再读取报告文件或重复规划。"
            "只在 preview_presentation 返回 passed=true 时声称规则检查通过；这不代表学术事实已被人工核实。"
            "需要计算或绘图时才调用 execute_python；"
            "execute_python 用于计算与数据产物，演示 HTML 由模板渲染工具生成。"
            "不得尝试访问网络、宿主机或执行破坏性操作。输出中文 Markdown，"
            "完整论文分析包含研究问题、方法、实验/结果、局限性；其他追问按具体要求回答。每个关键事实后标注"
            "[章节名，第X页]。如果证据不足，明确说明信息不足。"
            + ("当前是论文问答模式，只进行检索、解释和连续问答。不要声称已经执行计算或生成文件；"
               "如果用户要求执行计算或制作HTML演示，提示切换研究助手模式。" if mode == "qa" else "")
        ),
    )


async def run_agent(
    rag: RagClient, paper_id: str, question: str, sandbox: DockerPythonSandbox, run_dir: Path,
    *, history: list[BaseMessage] | None = None, user_message: HumanMessage | None = None,
    runtime: RunRuntime | None = None,
    mode: str = "agent",
) -> dict[str, Any]:
    agent = build_agent(rag, paper_id, sandbox, run_dir, runtime, mode=mode)
    history = history or []
    inputs = [*history, user_message or HumanMessage(content=question)]
    result = await agent.ainvoke({"messages": inputs}, config={"recursion_limit": settings.recursion_limit})
    # ainvoke returns input history too; only newly produced messages belong to this run.
    messages = result.get("messages", [])[len(inputs):]
    final = messages[-1] if messages else None
    if final is None or final.type != "ai" or getattr(final, "tool_calls", []) or not validate_tool_pairs(messages):
        raise ValueError("Agent 未生成完整的最终回答")
    content = getattr(final, "content", "") if final is not None else ""
    if isinstance(content, list):
        content = "".join(str(item.get("text", item)) for item in content)
    citations = []
    tool_calls: list[dict[str, Any]] = []
    retrieval_timings: list[dict[str, Any]] = []
    evidence_chunk_count = 0
    artifacts: list[str] = []
    for message in messages:
        for call in getattr(message, "tool_calls", []) or []:
            tool_calls.append(
                {
                    "name": call.get("name"),
                    "args": call.get("args", {}),
                    "id": call.get("id"),
                }
            )
        if getattr(message, "type", "") != "tool":
            continue
        try:
            payload = json.loads(message.content)
            if "results" in payload:
                results = payload.get("results", [])
                evidence_chunk_count += len(results)
                citations.extend(results)
                if payload.get("timings"):
                    retrieval_timings.append(payload["timings"])
            citations.extend(payload.get("citations", []))
            if "results" not in payload:
                evidence_chunk_count += payload.get("evidence_chunk_count", 0)
            artifacts.extend(payload.get("output_files", []))
        except (TypeError, json.JSONDecodeError):
            continue
    if runtime:
        citations.extend(runtime.citations)
    unique = {(x.get("paper_id"), x.get("chunk_id") or (x.get("section_path"), x.get("page_number"), x.get("text"))): x for x in citations}
    return {
        "answer": str(content),
        "citations": list(unique.values()),
        "tool_calls": tool_calls,
        "evidence_chunk_count": evidence_chunk_count,
        "retrieval_timings": retrieval_timings,
        "artifacts": sorted(set(artifacts)),
        "_messages": messages,
    }
