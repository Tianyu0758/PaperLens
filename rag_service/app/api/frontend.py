# 文件说明：提供 RAG 副本自带的简单网页入口，渲染论文上传与问答页面。
# 它使用 RAG 自身的静态资源，与主 Agent 的统一工作台分别提供服务。

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates


TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=TEMPLATE_DIR)
router = APIRouter(include_in_schema=False)


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"page_title": "PaperLens · 学术论文问答"},
    )
