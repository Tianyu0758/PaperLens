# 文件说明：使用真实浏览器逐页检查生成的 HTML，输出问题、截图和检查状态。
# 检查运行错误、内容溢出、图片、导航以及流程、图表和证据面板等交互。
# 结果供 Agent 定向修订；规则通过不等同于视觉审美或学术结论已经审核。

"""Browser feedback for generated offline decks; no model or network calls."""
from __future__ import annotations

import asyncio
from pathlib import Path


async def inspect_html(path: Path, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    report = {"passed": False, "status": "unavailable", "issues": [], "pages": [],
              "checks": ["runtime", "layout", "navigation", "flow_reveal", "chart_toggle", "evidence"],
              "note": "规则检查不代表学术结论或视觉质量已获人工确认。"}
    try:
        from playwright.async_api import async_playwright
        async with asyncio.timeout(90):
            async with async_playwright() as playwright:
                browser = None
                launch_errors = []
                for options in ({"channel": "msedge"}, {}):
                    try:
                        browser = await playwright.chromium.launch(headless=True, **options)
                        break
                    except Exception as exc:
                        launch_errors.append(str(exc).splitlines()[0])
                if browser is None:
                    raise RuntimeError("浏览器未安装。请安装 Edge 或运行 python -m playwright install chromium。")
                try:
                    context = await browser.new_context(viewport={"width":1440,"height":1000}, device_scale_factor=1)
                    page = await context.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    async def route(request):
                        if request.request.url.startswith("file:"):
                            await request.continue_()
                        else:
                            await request.abort()
                    await page.route("**/*", route)
                    await page.goto(path.resolve().as_uri(), wait_until="load", timeout=15000)
                    await page.wait_for_function("!!window.PaperLensPresentation", timeout=5000)
                    count = await page.evaluate("window.PaperLensPresentation.count")
                    if not 1 <= count <= 30:
                        raise ValueError("演示页数不合法")
                    for i in range(count):
                        await page.evaluate("i=>window.PaperLensPresentation.show(i)",i)
                        await page.wait_for_timeout(35)
                        findings = await page.evaluate("""()=>{const s=document.querySelector('.slide:not([hidden])');
                          const targets=[s.querySelector('h1'),s.querySelector('.slide-body'),...s.querySelectorAll('.comparison article,.node-copy,.results-notes,.chart-wrap')];
                          return {slide_id:s.dataset.slideId,title:s.querySelector('h1').textContent,
                            overflow:targets.filter(e=>e&&(e.scrollHeight>e.clientHeight+3||e.scrollWidth>e.clientWidth+3)).map(e=>e.className||e.tagName),
                            missing_images:[...s.querySelectorAll('img')].filter(e=>!e.complete||!e.naturalWidth).length};}""")
                        for issue in findings["overflow"]:
                            report["issues"].append({"slide_id":findings["slide_id"],"kind":"overflow", "target":issue,
                                "suggestion":"缩短该页内容、拆页或简化图解节点；保留未涉及页面。"})
                        if findings["missing_images"]:
                            report["issues"].append({"slide_id":findings["slide_id"],"kind":"missing_image"})
                        slide = page.locator('.slide:not([hidden])')
                        if await slide.locator('[data-action="reveal-all"]').count():
                            await slide.locator('[data-action="reveal-all"]').click()
                            if await slide.locator('.flow-node.is-unrevealed').count():
                                report["issues"].append({"slide_id":findings["slide_id"],"kind":"flow_interaction"})
                        if await slide.locator('[data-action="chart-toggle"]').count():
                            toggle = slide.locator('[data-action="chart-toggle"]')
                            await toggle.click()
                            if not await slide.locator('.chart-table').is_visible():
                                report["issues"].append({"slide_id":findings["slide_id"],"kind":"chart_interaction"})
                            await toggle.click()
                        if await slide.locator('[data-action="compare"]').count():
                            await slide.locator('[data-action="compare"]').click()
                            if not await slide.locator('.comparison').evaluate("e=>e.classList.contains('focus')"):
                                report["issues"].append({"slide_id":findings["slide_id"],"kind":"comparison_interaction"})
                            await slide.locator('[data-action="compare"]').click()
                        if await slide.locator('[data-action="evidence"]').count():
                            await slide.locator('[data-action="evidence"]').click()
                            if not await page.locator('#panel-body .evidence-entry').count():
                                report["issues"].append({"slide_id":findings["slide_id"],"kind":"missing_evidence"})
                            await page.locator('[data-action="close-panel"]').click()
                        name = f"slide-{i+1:02d}.png"
                        await page.locator('#viewport').screenshot(path=str(output/name), animations="disabled")
                        report["pages"].append({**findings,"screenshot":name})
                    if count>1:
                        await page.evaluate("window.PaperLensPresentation.show(0)")
                        await page.keyboard.press("ArrowRight")
                        if await page.evaluate("window.PaperLensPresentation.index") != 1:
                            report["issues"].append({"kind":"keyboard_navigation"})
                    report["issues"].extend({"kind":"runtime_error","detail":error} for error in errors)
                    report.update(status="needs_revision" if report["issues"] else "passed", passed=not report["issues"], slide_count=count)
                finally:
                    await browser.close()
    except Exception as exc:
        report["issues"].append({"kind":"inspection_unavailable","detail":str(exc)[:1000]})
        report["status"] = "unavailable"
        report["passed"] = False
    return report
