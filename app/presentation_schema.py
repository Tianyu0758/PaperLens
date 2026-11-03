# 文件说明：定义可编辑演示稿，以及页面模板、证据、流程图和数据图表的结构。
# 校验证据归属、页面 ID、图表数据长度和节点连线等关系，并能导出可读的 Markdown 大纲。
# 结构化稿是后续修订与渲染的主要依据，模型配置内容和组件，不直接提供执行脚本。

"""Canonical content, evidence and component configuration; never executable model HTML."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator


class Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(Spec):
    evidence_id: str
    paper_id: str
    section_path: str
    text: str = Field(max_length=20000)
    chunk_id: str | None = None
    source_id: str | None = None
    page_number: int | None = None


class FlowNode(Spec):
    node_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")
    label: str = Field(min_length=1, max_length=60)
    detail: str = Field(default="", max_length=180)


class FlowEdge(Spec):
    source: str
    target: str
    label: str = Field(default="", max_length=40)


class Flow(Spec):
    nodes: list[FlowNode] = Field(min_length=2, max_length=8)
    edges: list[FlowEdge] = Field(default_factory=list, max_length=16)
    reveal: bool = True

    @model_validator(mode="after")
    def valid_graph(self):
        ids = [n.node_id for n in self.nodes]
        if len(set(ids)) != len(ids):
            raise ValueError("流程节点 ID 必须唯一")
        if any(e.source not in ids or e.target not in ids for e in self.edges):
            raise ValueError("流程连线引用了不存在的节点")
        return self


class ChartSeries(Spec):
    name: str = Field(min_length=1, max_length=40)
    values: list[FiniteFloat] = Field(min_length=1, max_length=8)


class Chart(Spec):
    title: str = Field(default="", max_length=100)
    categories: list[str] = Field(min_length=1, max_length=8)
    series: list[ChartSeries] = Field(min_length=1, max_length=3)
    unit: str = Field(default="", max_length=30)
    evidence_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def dimensions(self):
        if any(len(s.values) != len(self.categories) for s in self.series):
            raise ValueError("图表数据长度必须与 categories 一致")
        return self


class Slide(Spec):
    slide_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,59}$")
    template: Literal["cover", "section", "bullets", "comparison", "flowchart", "results", "summary", "qa", "figure"]
    title: str = Field(min_length=1, max_length=100)
    lead: str = Field(default="", max_length=260)
    bullets: list[str] = Field(default_factory=list, max_length=6)
    left_title: str = Field(default="已有方法", max_length=60)
    left: list[str] = Field(default_factory=list, max_length=5)
    right_title: str = Field(default="本文方法", max_length=60)
    right: list[str] = Field(default_factory=list, max_length=5)
    flow: Flow | None = None
    chart: Chart | None = None
    image_artifact_id: str | None = None
    image_caption: str = Field(default="", max_length=240)
    speaker_notes: str = Field(default="", max_length=3000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=12)
    estimated_seconds: int = Field(default=60, ge=10, le=600)

    @model_validator(mode="after")
    def complete_template(self):
        if self.template == "flowchart" and self.flow is None:
            raise ValueError("flowchart 页面需要 flow")
        if self.template == "results" and self.chart is None:
            raise ValueError("results 页面需要可追溯的 chart 数据")
        if self.template == "comparison" and (not self.left or not self.right):
            raise ValueError("comparison 页面需要左右两栏内容")
        if self.template == "figure" and not self.image_artifact_id:
            raise ValueError("figure 页面需要已注册图片产物 ID")
        if any(len(x) > 240 for x in [*self.bullets, *self.left, *self.right]):
            raise ValueError("单条要点过长，请拆页")
        return self


class Presentation(Spec):
    schema_version: Literal["academic-html/v1"] = "academic-html/v1"
    deck_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,80}$")
    revision: int = Field(ge=1)
    parent_artifact_id: str | None = None
    paper_id: str
    title: str = Field(min_length=1, max_length=160)
    subtitle: str = Field(default="", max_length=240)
    audience: str = Field(default="相关方向研究生", max_length=240)
    duration_minutes: int = Field(default=15, ge=5, le=120)
    slides: list[Slide] = Field(min_length=1, max_length=30)
    evidence: list[Evidence] = Field(min_length=1, max_length=150)

    @model_validator(mode="after")
    def references(self):
        ids = [s.slide_id for s in self.slides]
        if len(ids) != len(set(ids)):
            raise ValueError("每页 slide_id 必须唯一，修改时保留未变页面的 ID")
        evidence_ids = [e.evidence_id for e in self.evidence]
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("证据 ID 重复")
        allowed = set(evidence_ids)
        if any(e.paper_id != self.paper_id for e in self.evidence):
            raise ValueError("证据不属于当前论文")
        for slide in self.slides:
            refs = [*slide.evidence_ids, *(slide.chart.evidence_ids if slide.chart else [])]
            if set(refs) - allowed:
                raise ValueError(f"{slide.slide_id}: 引用了不存在的证据")
            if slide.template not in ("cover", "section", "qa") and not slide.evidence_ids:
                raise ValueError(f"{slide.slide_id}: 事实页面必须附带证据")
        return self


def markdown_outline(deck: Presentation) -> str:
    lines = [f"# {deck.title}", "", deck.subtitle,
             f"\n版本：{deck.revision} · 时长：{deck.duration_minutes} 分钟 · 听众：{deck.audience}"]
    sources = {e.evidence_id: e for e in deck.evidence}
    for i, slide in enumerate(deck.slides, 1):
        lines.extend(["", f"## {i}. {slide.title}", f"页面：{slide.slide_id} · 模板：{slide.template}", "", slide.lead])
        lines.extend(f"- {text}" for text in slide.bullets)
        if slide.template == "comparison":
            lines.extend([f"\n### {slide.left_title}", *[f"- {x}" for x in slide.left],
                          f"\n### {slide.right_title}", *[f"- {x}" for x in slide.right]])
        if slide.flow:
            lines.extend(f"- {n.label}：{n.detail}" for n in slide.flow.nodes)
        if slide.chart:
            lines.append("\n图表：" + slide.chart.title)
            lines.extend(f"- {s.name}：" + "；".join(f"{c}={v:g}" for c, v in zip(slide.chart.categories, s.values)) for s in slide.chart.series)
        if slide.image_caption:
            lines.append("\n图解说明：" + slide.image_caption)
        for eid in dict.fromkeys([*slide.evidence_ids, *(slide.chart.evidence_ids if slide.chart else [])]):
            e = sources[eid]
            location = e.section_path + (f"，第{e.page_number}页" if e.page_number else "")
            lines.append(f"\n证据 [{eid}]：{location}\n> {e.text[:1200]}")
        if slide.speaker_notes:
            lines.append("\n讲者备注：" + slide.speaker_notes)
    return "\n".join(lines).strip() + "\n"
