# 文件说明：协调演示规划、局部修订、版本保存、HTML 渲染和浏览器检查。
# 规划子 Agent 获取论文证据；格式化模型生成新稿或修改补丁；程序保留旧版并登记新产物。
# 完整证据来自工具观察，修订通过稳定页面 ID 合并，未涉及的页面沿用原内容。

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from pathlib import Path

from langchain.agents import create_agent
from langchain.tools import tool
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from app.config import settings
from app.presentation_schema import Evidence, Presentation, markdown_outline
from app.presentation_renderer import render_html
from app.runtime import AuditMiddleware, RunRuntime
from app.sandbox import compact_result


def evidence_catalog(items: list[dict]) -> list[Evidence]:
    unique = {}
    for item in items:
        text = str(item.get("text") or "")[:20000]
        if not text:
            continue
        key = str(item.get("chunk_id") or item.get("source_id") or item.get("section_path")) + text
        eid = "ev_" + hashlib.sha256(key.encode()).hexdigest()[:16]
        unique[eid] = Evidence(evidence_id=eid, paper_id=item["paper_id"], text=text,
            section_path=item.get("section_path") or "未知章节", chunk_id=item.get("chunk_id"),
            source_id=item.get("source_id"), page_number=item.get("page_number"))
    return list(unique.values())


def apply_patch_to_plan(old: Presentation, patch: dict) -> dict:
    allowed = {"updates", "remove_slide_ids", "slide_order", "title", "subtitle", "audience", "duration_minutes"}
    if set(patch) - allowed:
        raise ValueError("修改稿请使用 updates/remove_slide_ids/slide_order，不要替换整个 slides")
    data = old.model_dump()
    slides = {s["slide_id"]: s for s in data["slides"]}
    order = list(slides)
    for update in patch.get("updates", []):
        sid = update["slide_id"]
        if sid not in slides:
            order.append(sid)
        slides[sid] = {**slides.get(sid, {}), **update}
    for sid in patch.get("remove_slide_ids", []):
        if sid not in slides:
            raise ValueError("要删除的页面不存在：" + sid)
        del slides[sid]
        order.remove(sid)
    if "slide_order" in patch:
        new_order = patch["slide_order"]
        if len(new_order) != len(set(new_order)) or set(new_order) != set(slides):
            raise ValueError("slide_order 必须恰好包含修改后的全部页面 ID")
        order = new_order
    data["slides"] = [slides[sid] for sid in order]
    data.update({k: patch[k] for k in ("title", "subtitle", "audience", "duration_minutes") if k in patch})
    return data


class PresentationService:
    def __init__(self, rag, paper_id: str, sandbox, run_dir: Path, runtime: RunRuntime):
        self.rag, self.paper_id, self.sandbox = rag, paper_id, sandbox
        self.run_dir, self.runtime = run_dir, runtime

    def load(self, artifact_id: str) -> tuple[dict, Path, Presentation]:
        metadata, path = self.runtime.store.artifact(artifact_id, self.runtime.user_id,
                                                   session_id=self.runtime.session_id)
        if not path.name.endswith(".slides.json") or path.stat().st_size > 4*1024*1024:
            raise ValueError("请使用 list_artifacts 返回的结构化演示稿 .slides.json")
        deck = Presentation.model_validate_json(path.read_text(encoding="utf-8"))
        if deck.paper_id != self.paper_id:
            raise ValueError("演示稿不属于当前论文")
        return metadata, path, deck

    def next_revision(self, deck_id: str) -> int:
        pattern = re.compile(r"presentation-" + re.escape(deck_id[:8]) + r"-v(\d+)-")
        versions = [0]
        with self.runtime.store.connect() as db:
            rows = db.execute("SELECT filename FROM artifacts WHERE session_id=?", (self.runtime.session_id,))
            for row in rows:
                match = pattern.match(row[0])
                if match:
                    versions.append(int(match[1]))
        return max(versions)+1

    async def plan(self, objective: str, duration_minutes: int | None = None,
                   audience: str | None = None, source_artifact_id: str | None = None) -> dict:
        previous = self.load(source_artifact_id)[2] if source_artifact_id else None
        duration_minutes = duration_minutes if duration_minutes is not None else (previous.duration_minutes if previous else 15)
        audience = audience or (previous.audience if previous else "相关方向研究生")
        child = self.runtime.child("presentation")

        @tool
        async def inspect_paper() -> str:
            """Browse the paper's abstract, section IDs and structure before planning."""
            return json.dumps(await self.rag.inspect(self.paper_id), ensure_ascii=False)

        @tool
        async def search_paper(question: str, top_k: int = 5) -> str:
            """Find supporting original-paper evidence for a particular question."""
            return json.dumps(await self.rag.search(self.paper_id, question, max(1,min(top_k,10))), ensure_ascii=False)

        @tool
        async def read_paper(section_id: str | None = None, chunk_id: str | None = None,
                             offset: int = 0, limit: int = 8000) -> str:
            """Read a known section or chunk; continue with next_offset if truncated."""
            return json.dumps(await self.rag.read(self.paper_id, section_id=section_id, chunk_id=chunk_id,
                              offset=max(offset,0), limit=max(100,min(limit,20000))), ensure_ascii=False)

        @tool
        async def read_artifact(artifact_id: str) -> str:
            """Read a relevant source artifact belonging to this conversation."""
            return json.dumps(self.runtime.read_artifact(artifact_id), ensure_ascii=False)

        @tool
        async def execute_python(code: str) -> str:
            """Calculate or prepare chart data in the isolated sandbox."""
            result = await self.sandbox.execute(code)
            result["artifact_records"] = child.register_outputs(result.get("artifacts", []))
            return compact_result(result)

        model = ChatOpenAI(model=settings.model, api_key=settings.api_key,
                           base_url=settings.base_url, temperature=0)
        agent = create_agent(model, tools=[inspect_paper,search_paper,read_paper,read_artifact,execute_python],
            middleware=[AuditMiddleware(child)], system_prompt=(
                "你是学术演示规划助手。负责证据、讲解结构、逐页模板和局部交互，不生成 HTML 代码。"
                "新稿先 inspect_paper，再 search_paper/read_paper 收集证据。修改稿复用已有有效证据，仅在需要新事实时补读。"
                "结合听众和时长，规划背景、问题、方法、实验、局限、总结及预计问答。"
                "方法图解标为基于论文绘制的示意；图表只能使用证据中可核对的数字，不得编造实验值。"
                "可选模板 cover/section/bullets/comparison/flowchart/results/summary/qa/figure。"
                "局部交互仅使用流程分步、左右对比聚焦、图表与数据表切换、证据展开。"
                "控制密度：封面最多3条短要点；流程节点标题简短，细节放备注；实验图表页最多3条短结论。"
                "保留未要求修改的页面和 slide_id，描述本轮需要修改的内容。最终输出中文逐页方案。"))
        task = f"要求：{objective}\n听众：{audience}\n目标时长：{duration_minutes} 分钟。"
        if previous:
            task += "\n已有结构化稿（只修改本轮涉及部分）：\n" + previous.model_dump_json()
        child.emit("stage.changed", stage="planning_presentation", summary="正在规划学术演示")
        question = HumanMessage(content=task)
        child.record(question)
        result = await agent.ainvoke({"messages": [question]}, config={"recursion_limit": settings.recursion_limit})
        messages = result.get("messages", [])
        final = messages[-1] if messages else None
        if final is None or final.type != "ai" or getattr(final,"tool_calls",[]):
            raise ValueError("演示规划未完整结束")
        gathered = [e.model_dump() for e in previous.evidence] if previous else []
        for message in messages:
            if message.type == "tool":
                try:
                    payload = json.loads(message.content)
                    gathered.extend(payload.get("results", []))
                except (ValueError, TypeError):
                    pass
        # Preserve existing IDs so an unchanged slide stays byte-for-byte equivalent.
        catalog = {e.evidence_id:e for e in previous.evidence} if previous else {}
        for evidence in evidence_catalog(gathered):
            catalog.setdefault(evidence.evidence_id, evidence)
        if not catalog:
            raise ValueError("未获得论文证据，暂不能生成事实性演示稿")
        if len(catalog) > 150:
            raise ValueError("证据过多，请缩小本次演示的研究范围")
        child.emit("stage.changed", stage="formatting_presentation", summary="正在保存可编辑演示稿")
        from app.presentation_schema import Slide
        schema = json.dumps(Slide.model_json_schema(), ensure_ascii=False)
        instruction = ("返回合法 JSON 对象，字段 title/subtitle/audience/duration_minutes/slides。slides 必须符合以下 schema。"
                       "不使用 presentation 等包装字段。slide_id 用稳定英文 ID；方法流程图用 flow，实验图表用 chart；证据引用仅用给定 evidence_id。")
        if previous:
            instruction = ("返回局部修改 JSON：{updates:[{slide_id:原页面ID,...仅修改字段}],remove_slide_ids:[],"
                           "slide_order:[可选的全部页面ID]}。可以修改 title/subtitle/audience/duration_minutes。"
                           "没有变化的页面不放进 updates；不调整顺序时不输出 slide_order。不得输出 slides 字段。")
        instruction += "引用统一使用 evidence_ids；lead/bullets 不重复堆叠章节名或页码，不写页码待补等占位内容。"
        prompt = instruction + "\n页面 schema：\n" + schema + "\n任务：\n" + task + "\n规划草稿：\n" + str(final.content)
        prompt += "\n可信证据目录（引用 ID，禁止自行添加证据）：\n" + json.dumps([e.model_dump() for e in catalog.values()],ensure_ascii=False)
        formatter = model.with_structured_output(method="json_mode")
        last_error = None
        for attempt in range(2):
            raw = await formatter.ainvoke(prompt + ("\n上次校验错误，请修正：" + str(last_error)[:3000] if last_error else ""))
            try:
                if not isinstance(raw, dict):
                    raise ValueError("演示稿必须为 JSON 对象")
                data = apply_patch_to_plan(previous, raw) if previous else raw
                deck_id = previous.deck_id if previous else uuid.uuid4().hex
                data.update(schema_version="academic-html/v1", deck_id=deck_id,
                    revision=self.next_revision(deck_id), parent_artifact_id=source_artifact_id,
                    paper_id=self.paper_id, evidence=[e.model_dump() for e in catalog.values()])
                data.setdefault("audience", audience)
                data.setdefault("duration_minutes", duration_minutes)
                deck = Presentation.model_validate(data)
                break
            except (ValueError, KeyError, TypeError) as exc:
                last_error = exc
        else:
            raise ValueError(f"结构化演示稿校验失败：{last_error}")
        return self.save_plan(deck)

    def save_plan(self, deck: Presentation) -> dict:
        if deck.paper_id != self.paper_id:
            raise ValueError("演示稿不属于当前论文")
        self.run_dir.mkdir(parents=True, exist_ok=True)
        stem = f"presentation-{deck.deck_id[:8]}-v{deck.revision}-{uuid.uuid4().hex[:6]}"
        paths = [stem+".slides.json", stem+".md"]
        (self.run_dir/paths[0]).write_text(deck.model_dump_json(indent=2),encoding="utf-8")
        (self.run_dir/paths[1]).write_text(markdown_outline(deck),encoding="utf-8")
        records = self.runtime.register_outputs(paths)
        return {"plan_artifact_id":records[0]["artifact_id"],"outline_artifact_id":records[1]["artifact_id"],
            "deck_id":deck.deck_id,"revision":deck.revision,"parent_artifact_id":deck.parent_artifact_id,
            "title":deck.title,"slide_count":len(deck.slides),"output_files":paths,"artifact_records":records,
            "citations":[e.model_dump() for e in deck.evidence],"evidence_chunk_count":len(deck.evidence)}

    def render(self, plan_artifact_id: str) -> dict:
        _, path, deck = self.load(plan_artifact_id)
        name = path.name.removesuffix(".slides.json")+f"-{uuid.uuid4().hex[:6]}.html"
        def image(artifact_id):
            metadata, image_path = self.runtime.store.artifact(artifact_id, self.runtime.user_id,
                                                              session_id=self.runtime.session_id)
            return metadata["media_type"], image_path.read_bytes()
        self.runtime.emit("stage.changed",stage="rendering_presentation",summary="正在生成离线 HTML 演示")
        render_html(deck,self.run_dir/name,source_artifact_id=plan_artifact_id,image_resolver=image)
        records = self.runtime.register_outputs([name])
        return {"html_artifact_id":records[0]["artifact_id"],"plan_artifact_id":plan_artifact_id,
            "deck_id":deck.deck_id,"revision":deck.revision,"slide_count":len(deck.slides),
            "output_files":[name],"artifact_records":records,"citations":[e.model_dump() for e in deck.evidence]}

    async def preview(self, html_artifact_id: str) -> dict:
        from app.presentation_inspector import inspect_html
        _, path = self.runtime.store.artifact(html_artifact_id, self.runtime.user_id,
                                             session_id=self.runtime.session_id)
        if path.suffix.lower() != ".html":
            raise ValueError("请选择 HTML 演示产物")
        self.runtime.emit("stage.changed",stage="checking_presentation",summary="正在检查版式与交互")
        folder = "preview-"+uuid.uuid4().hex[:8]
        output = self.run_dir/folder
        report = await inspect_html(path, output)
        report["html_artifact_id"] = html_artifact_id
        match = re.search(r'<meta name="paperlens-source" content="([^"]+)"',path.read_text(encoding="utf-8"))
        report["plan_artifact_id"] = match[1] if match else None
        report_path = output/"presentation.check.json"
        report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        files = [str(p.relative_to(self.run_dir)).replace("\\","/") for p in output.iterdir() if p.is_file()]
        records = self.runtime.register_outputs(files)
        self.runtime.emit("presentation.checked",passed=report["passed"],issue_count=len(report["issues"]))
        return {**report,"output_files":files,"artifact_records":records}
