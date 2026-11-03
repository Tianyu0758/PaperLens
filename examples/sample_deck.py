# 文件说明：构造一份覆盖主要模板与交互的模拟演示稿，并生成 JSON、大纲和离线 HTML。
# 测试复用该稿验证流程图、图表、引用与版本行为；直接运行本模块会重新生成示例文件。

from app.presentation_schema import Presentation


def sample_deck() -> Presentation:
    return Presentation.model_validate({
        "deck_id":"example-paperlens-2026", "revision":1, "paper_id":"demo-paper",
        "title":"从论文证据，到清晰的学术表达", "subtitle":"交互式学术演示示例 · 全部数据均为演示数据",
        "audience":"初次接触论文研读助手的同学", "duration_minutes":10,
        "evidence":[{"evidence_id":"ev_demo","paper_id":"demo-paper","section_path":"演示材料 · 非真实论文",
            "text":"本例仅用于演示页面功能，非真实论文实验。示例流程为：读取论文、定位证据、组织讲解、生成演示。示例对照数据：条件A 70/78，条件B 74/83，条件C 77/86。数值不代表任何真实方法的性能。"}],
        "slides":[
            {"slide_id":"cover","template":"cover","title":"从论文证据，到清晰的学术表达", "lead":"稳定的演示骨架 × 可定制图解 × 有依据的连续修改", "bullets":["PaperLens / HTML 学术演示"], "speaker_notes":"这是明确标注为模拟数据的功能示例。"},
            {"slide_id":"question","template":"bullets","title":"一次组会汇报，需要完成哪些工作？", "lead":"从理解论文，到让听众理解论文。", "bullets":["掌握研究问题、方法与实验条件","让关键结论对应可回查的论文证据","为讲解设计合适的图表和逐步展示"],"evidence_ids":["ev_demo"]},
            {"slide_id":"comparison","template":"comparison","title":"从信息罗列，到有结构的讲解", "left_title":"逐条罗列", "left":["研究背景、方法、结果各自独立","听众需要自行连接论证关系"], "right_title":"围绕问题组织", "right":["问题 → 方法 → 实验 → 结论","用图解说明方法，用证据支撑结论"],"evidence_ids":["ev_demo"]},
            {"slide_id":"method","template":"flowchart","title":"方法讲解：把研读过程逐步展开", "lead":"点击“下一步”，按照讲解节奏逐步揭示。", "flow":{"nodes":[{"node_id":"read","label":"理解论文","detail":"浏览结构，定向精读"},{"node_id":"evidence","label":"定位证据","detail":"原文、数据与限制条件"},{"node_id":"plan","label":"组织讲解","detail":"确定重点与叙事顺序"},{"node_id":"present","label":"生成演示","detail":"模板、图解与局部交互"}],"reveal":True},"bullets":["示意图根据演示材料绘制，不是论文原图。"],"evidence_ids":["ev_demo"]},
            {"slide_id":"results","template":"results","title":"让结果可比较，也可核对", "chart":{"title":"示例对照数据 · 非真实实验", "categories":["条件 A","条件 B","条件 C"],"series":[{"name":"对照组","values":[70,74,77]},{"name":"示例组","values":[78,83,86]}],"unit":"演示数值","evidence_ids":["ev_demo"]},"bullets":["切换数据表查看具体数值；点击页脚查看材料来源。"],"evidence_ids":["ev_demo"]},
            {"slide_id":"summary","template":"summary","title":"把内容、证据和展示组织在一起", "bullets":["内容与证据保存在可编辑演示稿中","稳定模板提供一致的讲解体验","局部交互为理解研究内容服务"],"evidence_ids":["ev_demo"],"speaker_notes":"可以在主工作台继续提出修改要求，例如详细解释方法、调整时长或改变图解的展开方式。"},
            {"slide_id":"questions","template":"qa","title":"讨论与提问", "lead":"你希望听众在汇报结束后记住什么？", "bullets":["哪些内容需要展开解释？","哪些结论需要更多证据？","哪些页面适合图解或交互？"]}
        ]})


if __name__ == "__main__":
    from pathlib import Path
    from app.presentation_renderer import render_html
    from app.presentation_schema import markdown_outline
    root = Path(__file__).parent
    deck = sample_deck()
    (root/"presentation.slides.json").write_text(deck.model_dump_json(indent=2),encoding="utf-8")
    (root/"presentation.md").write_text(markdown_outline(deck),encoding="utf-8")
    render_html(deck,root/"presentation.html")
    print("Offline example generated.")
