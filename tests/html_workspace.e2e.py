# 文件说明：在模拟工作台中验证 HTML 预览、证据、图解交互、离线下载与版本恢复。
# 使用真实浏览器和临时服务数据；先核对测试标识，避免对真实应用执行写入检查。

"""Run only against the isolated tests.workspace_server fixture."""
import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright


root=Path(__file__).resolve().parents[1]
output=root/"data/verification/html-workspace"
output.mkdir(parents=True,exist_ok=True)
base=os.getenv("PAPERLENS_TEST_URL","http://127.0.0.1:18192")
checks=[]
with sync_playwright() as p:
    try:
        browser=p.chromium.launch(channel="msedge",headless=True) if os.name == 'nt' else p.chromium.launch(headless=True)
    except Exception:
        browser=p.chromium.launch(headless=True)
    context=browser.new_context(viewport={"width":1440,"height":1000},accept_downloads=True)
    page=context.new_page()
    errors=[]
    page.on("pageerror",lambda e:errors.append(str(e)))
    try:
        assert context.request.get(base+"/__workspace_test__").json()["fixture"]=="paperlens-e2e"
        page.goto(base)
        page.locator('#paper-select option[value="paper-a"]').wait_for(state="attached")
        page.locator('#paper-select').select_option('paper-a')
        page.locator('[data-compose-mode="agent"]').click()
        page.locator('#question').fill('制作 HTML 演示示例')
        page.locator('#send-button').click()
        page.wait_for_function("document.querySelectorAll('.message.assistant').length===1 && !document.querySelector('.task-card:not(.failed)')")
        page.locator('#tab-files').click()
        card=page.locator('.file-card').filter(has_text='.html').first
        card.locator('[data-preview]').click()
        frame=page.frame_locator('#preview-content iframe')
        frame.locator('.slide:not([hidden]) h1').wait_for()
        assert frame.locator('.slide:not([hidden])').get_attribute('data-slide-id')=='cover'
        assert page.locator('#preview-content iframe').get_attribute('sandbox')=='allow-scripts'
        checks.append('HTML preview inside isolated iframe')
        frame.locator('[data-action="next"]').click()
        frame.locator('.slide:not([hidden]) [data-action="evidence"]').click()
        assert frame.locator('#panel-body').inner_text().find('演示')>=0
        frame.locator('[data-action="close-panel"]').click()
        checks.append('Evidence opens with original excerpt')
        frame.locator('[data-action="next"]').click()
        frame.locator('[data-action="next"]').click()
        assert frame.locator('.slide:not([hidden]) .flow-node.is-unrevealed').count()==3
        frame.locator('.slide:not([hidden]) [data-action="reveal"]').click()
        assert frame.locator('.slide:not([hidden]) .flow-node.is-unrevealed').count()==2
        frame.locator('.slide:not([hidden]) [data-action="reveal-all"]').click()
        assert frame.locator('.slide:not([hidden]) .flow-node.is-unrevealed').count()==0
        page.screenshot(path=str(output/'method-preview.png'))
        checks.append('Method diagram supports stepwise reveal')
        frame.locator('[data-action="next"]').click()
        frame.locator('.slide:not([hidden]) [data-action="chart-toggle"]').click()
        assert frame.locator('.slide:not([hidden]) .chart-table').is_visible()
        checks.append('Chart toggles to traceable values')
        page.locator('#preview-dialog [data-close-dialog]').click()
        with page.expect_download() as waiting:
            card.locator('a').first.click()
        download=waiting.value
        local=output/download.suggested_filename
        download.save_as(local)
        offline=context.new_page()
        context.set_offline(True)
        offline.goto(local.as_uri())
        offline.wait_for_function('!!window.PaperLensPresentation')
        offline.locator('[data-action="next"]').click()
        assert offline.locator('#page-status').inner_text()=='2 / 7'
        context.set_offline(False)
        offline.close()
        checks.append('Downloaded single HTML works offline')
        page.reload()
        page.locator('#tab-files').click()
        page.locator('.file-card').filter(has_text='.html').first.wait_for()
        page.locator('#question').fill('修改方法部分，其他页面保留')
        page.locator('#send-button').click()
        page.wait_for_function("document.querySelectorAll('.message.assistant').length===2 && !document.querySelector('.task-card:not(.failed)')")
        page.locator('#tab-files').click()
        assert page.locator('.file-card').filter(has_text='.html').count()==2
        user=page.evaluate("JSON.parse(localStorage.getItem('paperlens.user'))")
        sid=page.evaluate("JSON.parse(localStorage.getItem('paperlens.preferences.'+JSON.parse(localStorage.getItem('paperlens.user')))).session")
        artifacts=context.request.get(f'{base}/sessions/{sid}/artifacts',params={'user_id':user}).json()['artifacts']
        plans=[x for x in artifacts if x['filename'].endswith('.slides.json')]
        first=context.request.get(base+plans[-1]['download_url'],params={'user_id':user}).json()
        second=context.request.get(base+plans[0]['download_url'],params={'user_id':user}).json()
        assert (first['revision'],second['revision'])==(1,2)
        assert second['parent_artifact_id']==plans[-1]['artifact_id']
        assert first['slides'][1]==second['slides'][1]
        checks.append('Refresh recovery and parent-linked revisions retain old version')
        page.screenshot(path=str(output/'versions.png'))
        page.set_viewport_size({'width':390,'height':844})
        page.locator('#open-workspace').click()
        page.locator('#tab-files').click()
        page.locator('.file-card').filter(has_text='.html').first.locator('[data-preview]').click()
        page.frame_locator('#preview-content iframe').locator('.slide:not([hidden])').wait_for()
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        page.screenshot(path=str(output/'mobile-preview.png'))
        checks.append('Mobile preview without horizontal page overflow')
        assert not errors,errors
        (output/'result.json').write_text(json.dumps({'ok':True,'checks':checks,'page_errors':errors},ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'checks':len(checks)},ensure_ascii=True))
    except Exception as exc:
        page.screenshot(path=str(output/'failure.png'))
        (output/'result.json').write_text(json.dumps({'ok':False,'checks':checks,'error':str(exc),'page_errors':errors},ensure_ascii=False,indent=2),encoding='utf-8')
        raise
    finally:
        browser.close()
