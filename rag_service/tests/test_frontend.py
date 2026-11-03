# 文件说明：验证 RAG 自带网页能够返回页面及静态脚本、样式资源。
# 使用轻量 FastAPI 测试应用检查页面入口，不启动真实检索和模型链路。

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from app.api.frontend import router
from app.config import PROJECT_ROOT


def frontend_client() -> TestClient:
    app = FastAPI()
    app.mount(
        "/static",
        StaticFiles(directory=PROJECT_ROOT / "app" / "static"),
        name="static",
    )
    app.include_router(router)
    return TestClient(app)


def test_index_renders_workspace_and_safe_assets() -> None:
    response = frontend_client().get("/")
    assert response.status_code == 200
    assert "PaperLens" in response.text
    assert 'id="upload-form"' in response.text
    assert 'id="query-form"' in response.text
    assert 'id="citation-list"' in response.text
    assert '/static/styles.css' in response.text
    assert '/static/app.js' in response.text


def test_frontend_static_assets_are_served() -> None:
    client = frontend_client()
    css = client.get("/static/styles.css")
    javascript = client.get("/static/app.js")
    assert css.status_code == 200
    assert "--green:" in css.text
    assert javascript.status_code == 200
    assert 'textContent = result.answer' in javascript.text
    assert 'method: "DELETE"' in javascript.text
