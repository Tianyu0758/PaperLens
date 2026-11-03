# 文件说明：验证 HTML 演示的完整工具循环、只出大纲、局部修订与版本关系。
# 使用模型替身和真实浏览器检查渲染、溢出、引用、文件归属及不可信文本处理。

from __future__ import annotations

import asyncio
import copy
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.agent import run_agent
from app.config import settings
from app.main import create_app
from app.presentation_inspector import inspect_html
from app.presentation_renderer import render_html
from app.presentation_schema import Presentation
from app.presentation_service import PresentationService, apply_patch_to_plan, evidence_catalog
from app.runtime import RunRuntime
from app.sandbox import DockerPythonSandbox
from app.store import AgentStore, StoreError
from examples.sample_deck import sample_deck
from tests.helpers import FakeRag, ScriptedChatModel


class DemoRag(FakeRag):
    async def search(self,paper_id,question,top_k=5):
        e=sample_deck().evidence[0]
        return {"results":[{"paper_id":paper_id,"chunk_id":"demo-evidence","section_path":e.section_path,"text":e.text}]}


class PlannerModel(ScriptedChatModel):
    payload: dict

    def with_structured_output(self,**kwargs):
        payload=self.payload
        class Formatter:
            async def ainvoke(self,prompt):
                return copy.deepcopy(payload)
        return Formatter()


class MasterModel(ScriptedChatModel):
    def _generate(self,messages,stop=None,run_manager=None,**kwargs):
        from langchain_core.outputs import ChatResult, ChatGeneration
        last=messages[-1]
        payload=json.loads(last.content) if last.type=="tool" else {}
        name=getattr(last,"name",None)
        if name=="plan_presentation":
            call={"name":"render_presentation","args":{"plan_artifact_id":payload["plan_artifact_id"]},"id":"render"}
        elif name=="render_presentation":
            call={"name":"preview_presentation","args":{"html_artifact_id":payload["html_artifact_id"]},"id":"preview"}
        elif name=="preview_presentation":
            response=AIMessage(content="演示已生成，规则检查状态："+payload["status"])
            return ChatResult(generations=[ChatGeneration(message=response)])
        else:
            call={"name":"plan_presentation","args":{"objective":"制作演示示例，必须标明模拟数据"},"id":"plan"}
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="",tool_calls=[call]))])


class PresentationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.store=AgentStore(self.root/"agent.db",self.root)
        self.store.initialize()
        self.session=self.store.create_session("alice","demo-paper")
        self.rag=DemoRag()
        self.new_run()

    def new_run(self):
        self.run,_=self.store.create_run(self.session["session_id"],"alice","制作 HTML",str(len(self.store.list_runs(self.session["session_id"],"alice"))))
        self.store.start_run(self.run["run_id"])
        self.runtime=RunRuntime(self.store,self.run["run_id"],self.session["session_id"],"alice")
        self.directory=self.root/"runs"/self.run["run_id"]
        self.sandbox=DockerPythonSandbox(self.directory)
        self.service=PresentationService(self.rag,"demo-paper",self.sandbox,self.directory,self.runtime)

    async def asyncTearDown(self):
        self.temp.cleanup()

    def planner(self):
        data=sample_deck().model_dump()
        eid=evidence_catalog([{"paper_id":"demo-paper","chunk_id":"demo-evidence",
            "section_path":data["evidence"][0]["section_path"],"text":data["evidence"][0]["text"]}])[0].evidence_id
        data=json.loads(json.dumps(data).replace("ev_demo",eid))
        return PlannerModel(responses=[
            AIMessage(content="",tool_calls=[{"name":"inspect_paper","args":{},"id":"child-inspect"}]),
            AIMessage(content="",tool_calls=[{"name":"search_paper","args":{"question":"演示证据"},"id":"child-search"}]),
            AIMessage(content="按证据组织七页演示，全部数据标为模拟。")],payload=data)

    async def test_real_agent_tool_loop_plans_renders_and_checks(self):
        with patch("app.agent.ChatOpenAI",return_value=MasterModel(responses=[])),patch("app.presentation_service.ChatOpenAI",return_value=self.planner()):
            result=await run_agent(self.rag,"demo-paper","制作 HTML",self.sandbox,self.directory,runtime=self.runtime)
        self.assertEqual([c["name"] for c in result["tool_calls"]],["plan_presentation","render_presentation","preview_presentation"])
        self.assertIn("passed",result["answer"])
        self.assertTrue(result["citations"])
        plan_message=next(m for m in result["_messages"] if m.type=="tool" and m.name=="plan_presentation")
        self.assertNotIn("citations",json.loads(plan_message.content))
        files=self.store.artifacts(self.session["session_id"],"alice")
        self.assertTrue(any(f["filename"].endswith(".slides.json") for f in files))
        self.assertTrue(any(f["filename"].endswith(".html") for f in files))
        self.assertTrue(any(f["filename"].endswith(".check.json") for f in files))
        events=self.store.events(self.run["run_id"])
        self.assertTrue(any(e["data"].get("scope")=="presentation" and e["data"].get("parent_call_id")=="plan" for e in events))

    async def test_outline_only_stops_before_html_and_keeps_editable_plan(self):
        master=ScriptedChatModel(responses=[AIMessage(content="",tool_calls=[
            {"name":"plan_presentation","args":{"objective":"只生成大纲"},"id":"outline"}]),
            AIMessage(content="大纲已保存，可以继续讨论。")])
        with patch("app.agent.ChatOpenAI",return_value=master),patch("app.presentation_service.ChatOpenAI",return_value=self.planner()):
            await run_agent(self.rag,"demo-paper","先给大纲，不要 HTML",self.sandbox,self.directory,runtime=self.runtime)
        files=self.store.artifacts(self.session["session_id"],"alice")
        self.assertTrue(any(f["filename"].endswith(".slides.json") for f in files))
        self.assertFalse(any(f["filename"].endswith(".html") for f in files))

    async def test_revision_reuses_evidence_and_preserves_audience_and_duration(self):
        old=sample_deck().model_copy(update={"duration_minutes":5,"audience":"初学者"})
        saved=self.service.save_plan(old)
        model=PlannerModel(responses=[AIMessage(content="只修改方法页标题，其余内容保留。")],
                           payload={"updates":[{"slide_id":"method","title":"新的方法标题"}]})
        with patch("app.presentation_service.ChatOpenAI",return_value=model):
            revised=await self.service.plan("只修改方法页标题",source_artifact_id=saved["plan_artifact_id"])
        new=self.service.load(revised["plan_artifact_id"])[2]
        self.assertEqual(new.duration_minutes,5)
        self.assertEqual(new.audience,"初学者")
        self.assertEqual(new.slides[1],old.slides[1])
        self.assertEqual(new.slides[3].title,"新的方法标题")

    async def test_partial_revision_preserves_other_slides_and_immutable_parent(self):
        old=sample_deck()
        saved=self.service.save_plan(old)
        _,old_path=self.store.artifact(saved["plan_artifact_id"],"alice")
        old_bytes=old_path.read_bytes()
        self.store.complete_run(self.run["run_id"],{"answer":"v1"},[AIMessage(content="v1")])
        self.new_run()
        new=apply_patch_to_plan(old,{"updates":[{"slide_id":"method","lead":"按照新的讲解节奏展开。"}]})
        new.update(revision=self.service.next_revision(old.deck_id),parent_artifact_id=saved["plan_artifact_id"])
        revised=self.service.save_plan(Presentation.model_validate(new))
        self.assertEqual(revised["revision"],2)
        self.assertEqual(old_path.read_bytes(),old_bytes)
        restored=self.service.load(revised["plan_artifact_id"])[2]
        self.assertEqual(restored.slides[1],old.slides[1])
        self.assertEqual(restored.parent_artifact_id,saved["plan_artifact_id"])
        listed=self.runtime.list_artifacts()["artifacts"]
        self.assertEqual({x["revision"] for x in listed if x["kind"]=="presentation_plan"},{1,2})

    async def test_semantic_missing_source_or_invalid_chart_is_rejected(self):
        raw=sample_deck().model_dump()
        raw["slides"][1]["evidence_ids"]=["invented"]
        with self.assertRaises(ValueError): Presentation.model_validate(raw)
        raw=sample_deck().model_dump()
        raw["slides"][4]["chart"]["series"][0]["values"]=[1]
        with self.assertRaises(ValueError): Presentation.model_validate(raw)
        raw=sample_deck().model_dump()
        raw["slides"][4]["chart"]["series"][0]["values"][0]=float("nan")
        with self.assertRaises(ValueError): Presentation.model_validate(raw)

    async def test_workspace_ownership_and_paper_boundary(self):
        saved=self.service.save_plan(sample_deck())
        with self.assertRaises(StoreError): self.store.artifact(saved["plan_artifact_id"],"mallory")
        other=self.store.create_session("alice","demo-paper")
        foreign=RunRuntime(self.store,self.run["run_id"],other["session_id"],"alice")
        service=PresentationService(self.rag,"demo-paper",self.sandbox,self.directory,foreign)
        with self.assertRaises(StoreError): service.load(saved["plan_artifact_id"])
        service=PresentationService(self.rag,"different-paper",self.sandbox,self.directory,self.runtime)
        with self.assertRaises(ValueError): service.load(saved["plan_artifact_id"])

    async def test_malicious_content_is_text_and_overflow_is_detected(self):
        raw=sample_deck().model_dump()
        raw["slides"]=raw["slides"][1:2]
        raw["slides"][0]["title"]="<script>window.injected=true</script>"
        raw["slides"][0]["bullets"]=["这是用于检测文字溢出的较长说明。"*12]*6
        deck=Presentation.model_validate(raw)
        path=self.directory/"malicious.html"
        render_html(deck,path)
        self.assertNotIn("<script>window.injected=true</script>",path.read_text(encoding="utf-8"))
        report=await inspect_html(path,self.directory/"inspection")
        self.assertEqual(report["status"],"needs_revision")
        self.assertTrue(any(i["kind"]=="overflow" for i in report["issues"]))
        self.assertFalse(any(i["kind"]=="runtime_error" for i in report["issues"]))

    async def test_preview_endpoint_checks_ownership_and_file_type(self):
        saved=self.service.save_plan(sample_deck())
        rendered=self.service.render(saved["plan_artifact_id"])
        app=create_app(replace(settings,data_dir=self.root),store=self.store,rag=self.rag)
        # No lifespan: it would mark the deliberately active fixture run interrupted.
        client=TestClient(app)
        url=f"/artifacts/{rendered['html_artifact_id']}/preview"
        self.assertEqual(client.get(url+"?user_id=mallory").status_code,404)
        result=client.get(url+"?user_id=alice")
        self.assertEqual(result.status_code,200)
        self.assertIn("sandbox allow-scripts",result.headers["content-security-policy"])
        self.assertNotIn("attachment",result.headers.get("content-disposition",""))
        self.assertEqual(client.get(f"/artifacts/{saved['plan_artifact_id']}/preview?user_id=alice").status_code,415)


if __name__=="__main__": unittest.main()
