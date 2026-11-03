# HTML 学术 Agent 启动与测试手册

除明确切换目录的步骤外，所有命令在 PaperLens 仓库根目录执行。

## 1. 准备与启动

先按 [跨机器安装与运行](setup.md) 创建 Agent、RAG 两个虚拟环境，初始化配置，准备 Docker、模型 API 与模型服务。

本机 Edge 可用于检查。没有 Edge 时：

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
```

仓库不包含实际 `.env` 或凭据；不要提交填写后的 `.env`。两个 API 默认直接使用当前操作系统的项目虚拟环境，GPU 服务另在 Linux/WSL 或远程主机准备。

```powershell
.\scripts\start-rag.cmd
# 另开一个终端，再执行：
.\scripts\start-agent.cmd
```

如需把 RAG API 放到 WSL，先在目标发行版准备 Linux 虚拟环境，再使用 `.\scripts\start-rag.cmd -Distro "你的发行版名称"`。默认不会访问 WSL，也不指定任何发行版。

Agent 前台运行，关闭时按 Ctrl+C。访问：

- 工作台：http://127.0.0.1:18091/
- Agent Swagger：http://127.0.0.1:18091/docs
- RAG Swagger：http://127.0.0.1:18081/docs
- 无需论文和模型的示例：http://127.0.0.1:18091/examples/presentation

也可以直接双击 `examples/presentation.html` 离线体验。示例数据已明确标为模拟，不代表真实论文结果。

## 2. 上传并精读论文

1. 打开工作台，点击选择/上传论文，选 PDF。
2. 等待论文解析并入库完成；新安装的数据库初始为空。
3. 在“论文问答”模式输入：

```text
请先浏览论文结构，再根据证据解释研究问题、方法和局限性。
```

4. 在右侧“执行过程”查看结构浏览、检索、定向阅读事件。
5. 点击引用，检查实际原文。没有页码时以章节和原文为准。
6. 连续追问，刷新并切换会话，确认各自上下文能恢复。

## 3. 先大纲、后演示

切换“研究助手”，输入：

```text
先给我一份10分钟的组会演示大纲，听众是初次了解这个方向的同学，暂时不要生成HTML。
```

预期：文件面板有 `.slides.json` 和 `.md`，本轮不应出现新 HTML。查看大纲后输入：

```text
根据刚才的大纲生成HTML演示，方法页用逐步展开的流程图，完成后检查页面与交互。
```

预期：出现 HTML、检查报告和逐页截图。报告若为 needs_revision 或 unavailable，应查看具体问题，不能理解为已通过。

## 4. 直接完成演示

```text
请制作一份5分钟的中文组会HTML演示，总共4至6页，覆盖研究问题、方法图解、实验发现和局限性。只使用论文证据，完成后检查。
```

文件面板点击“预览演示”；点击“独立放映”打开新标签。

- 左右方向键翻页，F 全屏，N 讲者备注。
- 流程图点击“下一步”或“显示全部”。
- 对比页可聚焦本文方法。
- 结果页可切换数据表。
- 点击页脚查看来源原文。

## 5. 连续修改与版本

```text
把刚才方法页的标题改为“方法流程与关键步骤”，其他页面、时长和听众保持不变，生成新版并检查。
```

确认新稿 revision 增加，parent_artifact_id 指向旧稿；原 HTML 和结构化稿仍能下载。旧版与新版可分别放映。没有修改的页面应保留原内容。

## 6. 离线与刷新

下载 HTML 到本地，断网后双击，检查翻页、图解、数据切换、证据和备注。单个 HTML 包含需要的展示资源；`.slides.json` 用于后续编辑，不是放映依赖。

刷新工作台后检查会话和文件。重启 Agent 后，已完成任务及成果保留；运行中任务会标为 interrupted，可显式重试。服务重启不会自动恢复执行到一半的工具。

## 7. Swagger 操作

1. RAG `/docs` 的 POST `/upload` → Try it out → 选择 PDF → Execute，保存 paper_id。
2. GET `/papers/{paper_id}/status` 轮询至 ready。
3. GET `/papers/{paper_id}/inspect` 查看 section_id；GET `/read` 填 section_id。用 next_offset 继续。
4. Agent `/docs` 的 POST `/sessions`：

```json
{"user_id":"html-manual-001","paper_id":"替换为真实paper_id","title":"HTML手工测试"}
```

5. POST `/sessions/{session_id}/runs`，user_id 一直填 `html-manual-001`：

```json
{"question":"先制作5分钟组会大纲，暂时不要生成HTML。","request_id":"outline-01","mode":"agent"}
```

6. GET `/runs/{run_id}` 查看状态；同一个 user_id 不要误填成 request_id。
7. 同会话继续 POST 新问题时使用新的 request_id。同一请求重发使用原 request_id，避免重复执行。
8. SSE 进度地址为 `/runs/{run_id}/events?user_id=html-manual-001&after=0`。Swagger 不适合展示持续事件，优先在工作台看过程。

PowerShell 查看 SSE：

```powershell
curl.exe -N "http://127.0.0.1:18091/runs/替换run_id/events?user_id=html-manual-001&after=0"
```

## 8. 自动验证

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
Push-Location rag_service
.\.venv\Scripts\python.exe -B -m pytest tests -q
Pop-Location
.\.venv\Scripts\python.exe -B scripts\check-example.py
```

Agent 与 RAG 都使用名为 `app` 的包，应分别从各自根目录、使用各自环境运行测试。RAG 测试依赖包含在 `rag_service/requirements-dev.txt` 中。

独立前端测试在另一个窗口启动（使用临时数据库、模拟模型与模拟论文）：

```powershell
.\.venv\Scripts\python.exe -B -m uvicorn tests.workspace_server:app --host 127.0.0.1 --port 18192
```

```powershell
.\.venv\Scripts\python.exe -B tests\html_workspace.e2e.py
```

原工作台十组浏览器回归可安装 `tests/browser/package.json` 中的 Node 开发依赖后运行 `tests/frontend.e2e.cjs`。设 `PAPERLENS_TEST_URL=http://127.0.0.1:18192`；测试先检查模拟服务标记，不能对真实服务运行这些写入测试。

真实外部链路验证会调用模型并向新库上传论文，明确执行以下命令才会发生：

```powershell
.\.venv\Scripts\python.exe -B scripts\live-smoke.py --pdf "你的论文.pdf"
```

它检查新库入库、章节阅读、真实生成、浏览器检查、连续局部修改和父版本关系，结果保存在 `data/verification/live-smoke.json`。测试报告与截图在 `data/verification/`。
