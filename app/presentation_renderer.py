# 文件说明：将经过校验的结构化演示稿转换为可离线放映的单文件 HTML。
# 按模板绘制正文、对比、流程图和实验图表，并内嵌样式、脚本、图片及引用原文。
# 模型文本先转义，页面交互由固定脚本提供；脚本内容参与 CSP 哈希计算。

"""Offline single-file HTML renderer. All model-supplied strings are escaped."""
from __future__ import annotations

import base64
import hashlib
import html
from types import SimpleNamespace
from pathlib import Path
from typing import Callable

from app.presentation_schema import Presentation, Slide

ASSETS = Path(__file__).with_name("presentation_assets")


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def points(items: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{esc(x)}</li>" for x in items) + "</ul>" if items else ""


def flow(slide: Slide) -> str:
    spec = slide.flow
    nodes = spec.nodes
    width = 1100
    edges = spec.edges or [SimpleNamespace(source=nodes[i].node_id,target=nodes[i+1].node_id,label="") for i in range(len(nodes)-1)]
    levels = {n.node_id:0 for n in nodes}
    indegree = {n.node_id:0 for n in nodes}
    for edge in edges:
        indegree[edge.target] += 1
    queue = [key for key,value in indegree.items() if value==0]
    visited = 0
    while queue:
        key = queue.pop(0)
        visited += 1
        for edge in edges:
            if edge.source == key:
                levels[edge.target] = max(levels[edge.target], levels[key]+1)
                indegree[edge.target] -= 1
                if indegree[edge.target] == 0:
                    queue.append(edge.target)
    groups = [[n for n in nodes if levels[n.node_id]==level] for level in range(max(levels.values())+1)]
    layered = visited==len(nodes) and len(groups)<=5 and max(map(len,groups))<=2
    cols = len(groups) if layered else min(len(nodes),4)
    cell = width/cols
    rows = max(map(len,groups)) if layered else (len(nodes)+cols-1)//cols
    positions = {}
    for i,n in enumerate(nodes):
        if layered:
            column = levels[n.node_id]
            group = groups[column]
            row = group.index(n)
            x,y = column*cell+12, row*170+12+(rows-len(group))*85
        else:
            row = i//cols
            column = i%cols if row%2==0 else cols-1-i%cols
            x,y = column*cell+12,row*170+12
        positions[n.node_id]=(x,y)
    markup = []
    for i, n in enumerate(nodes):
        x, y = positions[n.node_id]
        # HTML inside foreignObject wraps Chinese and long labels predictably.
        markup.append(f'<g class="flow-node" data-step="{i}"><rect x="{x}" y="{y}" width="{cell-24}" height="130" rx="16"/>'
                      f'<foreignObject x="{x+12}" y="{y+12}" width="{cell-48}" height="108">'
                      f'<div xmlns="http://www.w3.org/1999/xhtml" class="node-copy"><strong>{esc(n.label)}</strong><span>{esc(n.detail)}</span></div></foreignObject></g>')
    for edge in edges:
        a, b = positions[edge.source], positions[edge.target]
        step = next(i for i, n in enumerate(nodes) if n.node_id == edge.target)
        if b[0]>a[0]:
            x1, y1, x2, y2 = a[0]+cell-24, a[1]+65, b[0]-4, b[1]+65
        elif b[0]<a[0]:
            x1, y1, x2, y2 = a[0],a[1]+65,b[0]+cell-20,b[1]+65
        else:
            x1, y1, x2, y2 = a[0]+(cell-24)/2, a[1]+130, b[0]+(cell-24)/2, b[1]
        route = f'M{x1} {y1} H{(x1+x2)/2} V{y2} H{x2}' if a[0]!=b[0] else f'M{x1} {y1} L{x2} {y2}'
        markup.append(f'<g class="flow-edge" data-step="{step}"><path d="{route}" marker-end="url(#arrow-{esc(slide.slide_id)})"/>'
                      f'<text x="{(x1+x2)/2}" y="{(y1+y2)/2-8}" text-anchor="middle">{esc(edge.label)}</text></g>')
    buttons = '<button type="button" data-action="reveal">下一步</button><button type="button" data-action="reveal-all">显示全部</button>' if spec.reveal else ""
    return (f'<div class="flow-wrap" data-reveal="{str(spec.reveal).lower()}"><svg class="method-flow" role="img" aria-label="{esc(slide.title)}" viewBox="0 0 {width} {170*rows}">'
            f'<defs><marker id="arrow-{esc(slide.slide_id)}" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8 Z" class="arrow"/></marker></defs>'
            + "".join(markup) + f'</svg><div class="component-controls">{buttons}<span class="step-label"></span><span class="diagram-source">依据论文证据绘制的示意图</span></div></div>')


def chart(slide: Slide) -> str:
    spec = slide.chart
    colors = ["#087f8c", "#e0a24c", "#6558a8"]
    maximum = max(0.0, *(v for s in spec.series for v in s.values))
    minimum = min(0.0, *(v for s in spec.series for v in s.values))
    span = maximum - minimum or 1
    n = len(spec.categories)
    group = 960 / n
    bar_width = min(72, group / (len(spec.series)+1))
    baseline = 300 - (0-minimum) / span * 240
    bars = [f'<line x1="75" y1="{baseline}" x2="1060" y2="{baseline}" class="axis"/>']
    for i, category in enumerate(spec.categories):
        center = 100 + group*i + group/2
        bars.append(f'<text x="{center}" y="344" text-anchor="middle" class="category">{esc(category[:24])}</text>')
        for j, series in enumerate(spec.series):
            value = series.values[i]
            y = 300-(value-minimum)/span*240
            x = center + (j-len(spec.series)/2)*bar_width
            bars.append(f'<rect x="{x}" y="{min(y,baseline)}" width="{bar_width-6}" height="{max(abs(y-baseline),1)}" rx="4" fill="{colors[j]}"/>'
                        f'<text x="{x+(bar_width-6)/2}" y="{y-9 if value>=0 else y+23}" text-anchor="middle" class="value">{value:g}</text>')
    legend = "".join(f'<span><i style="background:{colors[j]}"></i>{esc(s.name)}</span>' for j, s in enumerate(spec.series))
    rows = "".join(f'<tr><th>{esc(c)}</th>' + "".join(f'<td>{s.values[i]:g}</td>' for s in spec.series) + '</tr>' for i, c in enumerate(spec.categories))
    return (f'<div class="chart-wrap"><div class="component-controls"><strong>{esc(spec.title)} {esc(spec.unit)}</strong>'
            '<button type="button" data-action="chart-toggle">查看数据表</button></div>'
            f'<div class="chart-visual"><svg viewBox="0 0 1140 370" role="img" aria-label="{esc(spec.title)}">' + "".join(bars) + f'</svg><div class="legend">{legend}</div></div>'
            '<div class="chart-table" hidden><table><thead><tr><th>实验条件</th>' + "".join(f'<th>{esc(s.name)}</th>' for s in spec.series) + f'</tr></thead><tbody>{rows}</tbody></table></div></div>')


def body(slide: Slide, image_resolver: Callable | None) -> str:
    if slide.template == "comparison":
        return (f'<div class="component-controls"><button type="button" data-action="compare">聚焦本文方法</button></div>'
                f'<div class="comparison"><article class="compare-left"><h2>{esc(slide.left_title)}</h2>{points(slide.left)}</article>'
                f'<article><h2>{esc(slide.right_title)}</h2>{points(slide.right)}</article></div>')
    if slide.template == "flowchart":
        return flow(slide) + points(slide.bullets)
    if slide.template == "results":
        return '<div class="results-layout'+(' has-notes' if slide.bullets else '')+'">'+chart(slide)+('<aside class="results-notes">'+points(slide.bullets)+'</aside>' if slide.bullets else '')+'</div>'
    if slide.template == "figure":
        if image_resolver is None:
            raise ValueError("缺少图片读取器")
        mime, data = image_resolver(slide.image_artifact_id)
        if mime not in ("image/png", "image/jpeg") or len(data) > 4*1024*1024:
            raise ValueError("图解只支持 4 MB 以内的 PNG/JPEG")
        return f'<figure><img alt="{esc(slide.image_caption)}" src="data:{mime};base64,{base64.b64encode(data).decode()}"/><figcaption>{esc(slide.image_caption)}</figcaption></figure>'
    return points(slide.bullets)


def render_html(deck: Presentation, output: Path, *, source_artifact_id: str = "", image_resolver: Callable | None = None) -> None:
    css = (ASSETS / "slides.css").read_text(encoding="utf-8")
    script = (ASSETS / "slides.js").read_text(encoding="utf-8")
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    citations = {e.evidence_id: e for e in deck.evidence}
    slides = []
    for i, slide in enumerate(deck.slides):
        refs = list(dict.fromkeys([*slide.evidence_ids, *(slide.chart.evidence_ids if slide.chart else [])]))
        evidence_html = ""
        for eid in refs:
            e = citations[eid]
            location = e.section_path + (f" · 第{e.page_number}页" if e.page_number else "")
            evidence_html += f'<article class="evidence-entry"><h3>{esc(location)}</h3><blockquote>{esc(e.text)}</blockquote><small>{esc(e.chunk_id or e.source_id or e.evidence_id)}</small></article>'
        slides.append(f'<section class="slide template-{slide.template}" data-slide-id="{esc(slide.slide_id)}" data-index="{i}" aria-label="{i+1}. {esc(slide.title)}"'+(' hidden' if i else '')+'>'
                      f'<div class="eyebrow">PAPERLENS / {esc(deck.title[:60])}</div><h1>{esc(slide.title)}</h1>'
                      +(f'<p class="lead">{esc(slide.lead)}</p>' if slide.lead else '')
                      +f'<div class="slide-body">{body(slide,image_resolver)}</div><footer class="slide-footer"><span>{i+1:02d} / {len(deck.slides):02d}</span>'
                      +(f'<button type="button" data-action="evidence">查看 {len(refs)} 条论文证据</button>' if refs else '<span>学术论文研读与组会汇报</span>')
                      +f'</footer><template class="slide-evidence">{evidence_html}</template><template class="slide-notes"><p>{esc(slide.speaker_notes)}</p></template></section>')
    toc = "".join(f'<button type="button" data-goto="{i}"><span>{i+1:02d}</span>{esc(s.title)}</button>' for i,s in enumerate(deck.slides))
    document = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="paperlens-source" content="{esc(source_artifact_id)}"><meta name="paperlens-deck" content="{esc(deck.deck_id)}"><meta name="paperlens-revision" content="{deck.revision}">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'sha256-{digest}'; connect-src 'none'; base-uri 'none'; form-action 'none'">
<title>{esc(deck.title)} · v{deck.revision}</title><style>{css}</style></head><body>
<header class="toolbar"><div><strong>PaperLens</strong><span>学术演示 · v{deck.revision}</span></div><nav><button data-action="toc" type="button">目录</button><button data-action="notes" type="button">讲者备注</button><button data-action="fullscreen" type="button">全屏</button></nav></header>
<main><div id="viewport"><div id="stage">{''.join(slides)}</div></div></main>
<nav class="navigation"><button type="button" data-action="previous" aria-label="上一页">← 上一页</button><span id="page-status" aria-live="polite"></span><button type="button" data-action="next" aria-label="下一页">下一页 →</button></nav>
<div class="progress"><div id="progress-fill"></div></div><p class="keyboard-hint">← → 翻页 · F 全屏 · N 备注 · Esc 关闭面板</p>
<dialog id="panel"><header><h2 id="panel-title"></h2><button type="button" data-action="close-panel" aria-label="关闭">关闭 ×</button></header><div id="panel-body"></div></dialog>
<template id="toc-content">{toc}</template><script>{script}</script></body></html>'''
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")
