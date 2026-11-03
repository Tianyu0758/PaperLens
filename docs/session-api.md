# HTML Agent 会话与任务接口

适用新版 HTML 项目，Agent 默认地址为 `http://127.0.0.1:18091`。操作顺序是：上传论文 → 等待 ready → 创建会话 → 在会话中提交问题 → 查看执行过程和成果。

## 论文接口

| 方法与路径 | 用途 |
| --- | --- |
| GET `/papers` | 列出已入库论文 |
| POST `/papers/upload` | multipart 上传 PDF，返回 paper_id 和状态 |
| GET `/papers/{paper_id}/status` | 等待状态变为 ready |
| GET `/papers/{paper_id}/inspect` | 获取摘要、章节与 section_id |
| GET `/papers/{paper_id}/read` | 按 section_id 或 chunk_id 读取，支持 offset、limit 和 ingestion_id |

上传和状态查询由 RAG 服务处理。任务执行期间只分析已经绑定到会话且入库完成的论文。

## 会话与任务

| 方法与路径 | 用途 |
| --- | --- |
| POST `/sessions` | 创建绑定论文的会话 |
| GET `/sessions` | 历史会话列表 |
| GET、PATCH `/sessions/{session_id}` | 获取或重命名会话 |
| GET `/sessions/{session_id}/messages` | 读取用户消息和最终回答，分页保留完整轮次 |
| GET `/sessions/{session_id}/runs` | 查看各轮任务 |
| POST `/sessions/{session_id}/runs` | 提交新问题或演示请求 |
| GET `/runs/{run_id}` | 查看状态、回答、引用及文件 |
| POST `/runs/{run_id}/retry` | 为失败或中断任务创建重试 |
| GET `/runs/{run_id}/events` | 获取可回放的 SSE 执行事件 |

创建会话的请求体包含 user_id，其他会话、任务和文件接口都需要在查询参数中提供同一个 user_id；遗漏返回 422，记录不属于该标识时返回 404。

```json
{"user_id":"html-manual-001","paper_id":"真实paper_id","title":"论文研读与HTML演示"}
```

POST `/sessions/{session_id}/runs?user_id=html-manual-001`：

```json
{"question":"先生成5分钟组会演示大纲，暂时不要生成HTML。","request_id":"outline-01","mode":"agent"}
```

每个会话最多一个 queued/running 任务。同一会话中相同 request_id 和请求内容复用原任务；改变问题或模式返回 409。新问题使用新的 request_id。重试接口接收新的 request_id，沿用原问题与模式。

`qa` 开放论文结构浏览、检索、定向阅读及会话产物列表和读取；`agent` 再开放 Python 计算、演示规划、渲染及检查。两种模式共享当前会话上下文。

## 执行事件

订阅 `/runs/{run_id}/events?user_id=html-manual-001&after=0`。事件含 run_id、seq、event、data 和 created_at；seq 对每个任务递增。

支持 run.queued、run.started、stage.changed、tool.started、tool.completed、tool.failed、artifact.created 和任务完成/失败/中断事件。scope 为 main 或 presentation，parent_call_id 关联演示规划子 Agent 与主调用。

断线后用 after 或 Last-Event-ID 恢复，取两个游标中的较大值；游标超出事件范围返回 400。任务结束且没有后续事件时返回 204。最终回答完成后整体返回，SSE 用于执行进度，不输出模型思维文本。

## 文件与版本

| 方法与路径 | 用途 |
| --- | --- |
| GET `/sessions/{session_id}/artifacts` | 列出当前会话各轮文件 |
| GET `/artifacts/{artifact_id}` | 下载指定文件 |
| GET `/artifacts/{artifact_id}/preview` | 预览 HTML 演示 |

结构化稿 `.slides.json` 保存 deck_id、revision、parent_artifact_id、稳定页面 ID 及证据。修改从指定稿件生成新版本；Markdown 大纲用于阅读，HTML 用于离线放映，检查另存报告和截图。

任务及文件引用保存在 SQLite 和任务目录中。浏览器断线不会取消任务；服务重启保留已完成记录，将未完成任务标记 interrupted，之后可以显式重试。
