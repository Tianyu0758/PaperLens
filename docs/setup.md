# 在其他电脑安装与运行

本手册从新下载的 PaperLens 源码目录开始。Agent 和 RAG API 可在 Windows 或 Linux 上运行；本地 GPU 模型服务使用 Linux/WSL。使用已有远程模型服务时，运行两个 API 的电脑不需要 GPU 或 WSL。

## 1. 准备软件

| 软件或服务 | 用途 | 准备方式 |
| --- | --- | --- |
| Python 3.12 | Agent 与 RAG API | 安装 64 位 Python；Windows 可用 `py -3.12` 检查 |
| Docker Desktop / Docker Engine + Compose | Grobid、Qdrant 和计算沙箱 | Windows 启动 Docker Desktop；Linux 确认当前用户可运行 Docker |
| 模型 API | Agent 推理、查询扩展和回答 | 准备 DeepSeek 兼容接口、API Key 和实际可用的模型名称 |
| Embedding 与 Reranker | 稠密检索和重排序 | 使用已有服务，或按第 4 节在 GPU 机器启动 |
| Edge 或 Playwright Chromium | HTML 检查与截图 | 可直接使用本机 Edge，也可安装 Chromium |

先确认 `docker version` 能显示 Server 信息。模型 Key 仅填写到本地 `.env`，不要放入代码或网页。

## 2. 创建两个独立虚拟环境

以下命令均从 PaperLens 根目录开始。Agent 与 RAG 都有名为 `app` 的 Python 包，必须按各自工作目录启动；提供的入口会自动切换目录。

### Windows PowerShell

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt -c requirements-lock.txt
py -3.12 -m venv rag_service\.venv
.\rag_service\.venv\Scripts\python.exe -m pip install -r rag_service\requirements-dev.txt -c rag_service\requirements-lock.txt
.\.venv\Scripts\python.exe scripts\manage.py init
```

### Linux

如果发行版没有 `venv`，先通过系统包管理器安装 Python 3.12 的 venv 支持。

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt -c requirements-lock.txt
python3.12 -m venv rag_service/.venv
rag_service/.venv/bin/python -m pip install -r rag_service/requirements-dev.txt -c rag_service/requirements-lock.txt
.venv/bin/python scripts/manage.py init
```

`init` 只创建缺失的根目录 `.env` 和 `rag_service/.env`，再次执行不会覆盖已有配置。锁定文件记录本次 CPU API 环境验证使用的依赖版本；GPU 环境单独安装，不套用这两个锁定文件。

## 3. 配置模型 API、Grobid 和 Qdrant

根目录 `.env` 至少填写：

```dotenv
DEEPSEEK_API_KEY=你的模型访问密钥
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=你的账号实际可用的模型名称
AGENT_PORT=18091
RAG_BASE_URL=http://127.0.0.1:18081
```

`rag_service/.env` 填写同一套模型接口信息，并配置其依赖：

```dotenv
DEEPSEEK_API_KEY=你的模型访问密钥
DEEPSEEK_BASE_URL=https://api.deepseek.com
HYDE_MODEL=你的账号实际可用的模型名称
GENERATION_MODEL=你的账号实际可用的模型名称
RAG_PORT=18081
GROBID_URL=http://127.0.0.1:8070
QDRANT_URL=http://127.0.0.1:6333
QDRANT_API_KEY=
HTML_QDRANT_COLLECTION=paperlens_chunks
```

启动仓库自带的两个基础服务：

```powershell
docker compose up -d
docker compose ps
```

Compose 默认使用 Grobid 0.8.0 和 Qdrant 1.19.0，端口仅绑定本机。Qdrant 数据保存于独立 Docker volume；`docker compose down` 停止服务而保留数据。需要修改镜像、Grobid/Qdrant 端口时，在根目录 `.env` 修改 `GROBID_IMAGE`、`QDRANT_IMAGE`、`GROBID_PORT`、`QDRANT_PORT`，同时更新 RAG 配置中的地址。

使用 Qdrant Cloud 时，可跳过本地 Qdrant，执行 `docker compose up -d grobid`，并把 `QDRANT_URL` 改为集群地址、填写 `QDRANT_API_KEY`。本机回环地址可使用无密钥的实例，远程实例必须配置密钥。不同部署应使用不同集合名；更换 Embedding 模型后应重新入库。

## 4. 准备 Embedding 和 Reranker

### 已经有可访问的模型服务

在 `rag_service/.env` 配置地址和模型标识：

```dotenv
EMBEDDING_URL=http://你的模型服务器:8003
EMBEDDING_MODEL=Qwen/Qwen3-Embedding-0.6B
EMBEDDING_TOKENIZER=Qwen/Qwen3-Embedding-0.6B
RERANKER_URL=http://你的模型服务器:8004
```

Embedding 必须提供 `/v1/embeddings`，返回 1024 维向量；`EMBEDDING_MODEL` 应与该接口接受的模型名一致。`EMBEDDING_TOKENIZER` 用于 RAG 分块，可填 Hugging Face 模型 ID 或本地 tokenizer 目录；它不要求本机具备模型权重或 GPU，首次读取论文时可能需要联网下载 tokenizer。

Reranker 必须兼容本仓库 `reranker_service` 的 `/rerank` 请求与响应结构。通用供应商的同名接口不一定兼容，应先核对契约。两个服务都应提供 `/health`，供环境检查使用。

### 自己在 Linux/WSL 的 NVIDIA GPU 上运行

先确认 `nvidia-smi` 可用；WSL 若未把它加入 PATH，可尝试 `/usr/lib/wsl/lib/nvidia-smi`。驱动、CUDA 和 vLLM 的兼容要求参见 [vLLM 官方安装文档](https://docs.vllm.ai/en/stable/getting_started/installation/gpu/)。本项目的模型启动参数按 vLLM 0.8.5、Transformers 4.51.3 准备，使用独立环境避免与 API 依赖混装：

```bash
# 在 Linux/WSL 的 PaperLens 根目录执行
python3.12 -m venv rag_service/.venv-models
rag_service/.venv-models/bin/python -m pip install -r rag_service/requirements-models.txt
bash rag_service/scripts/start_embedding.sh
```

另开一个终端，从同一目录运行：

```bash
bash rag_service/scripts/start_reranker.sh
```

首次启动会按模型 ID 下载权重，需要网络、存储和足够显存。默认两服务分别使用 30% 和 45% 的 GPU 显存预算，可按硬件调整配置模板中的两个 `*_GPU_MEMORY_UTILIZATION`。如果模型环境在其他位置，启动前设置 `MODEL_PYTHON` 为那个环境的 Python 绝对路径。

已有本地模型文件时，在 RAG 配置中填写（路径包含空格时加引号）：

```dotenv
EMBEDDING_MODEL_PATH="/你的目录/Qwen3-Embedding-0.6B"
RERANKER_MODEL="/你的目录/Qwen3-Reranker-0.6B"
```

`EMBEDDING_MODEL_PATH` 是模型服务的权重位置，`EMBEDDING_MODEL` 是暴露给 API 的服务名，两者可以不同；`EMBEDDING_TOKENIZER` 是 RAG 所在机器能够读取的 tokenizer 位置。相对本地模型路径应以 `./` 开头，基于 `rag_service` 根目录解析。

GPU 位于远程主机时，将模型服务的 `*_HOST` 改为可访问的监听地址，同时在 RAG 配置中填写该主机地址。Windows 连接 WSL 时先验证端口能否访问；若使用 WSL IP，重启 WSL 后应确认 IP 是否变化。

## 5. 检查并启动两个 API

Windows：

```powershell
.\.venv\Scripts\python.exe scripts\manage.py doctor --services
docker pull python:3.12-slim
# 已安装 Edge 时可跳过下一行：
.\.venv\Scripts\python.exe -m playwright install chromium
.\scripts\start-rag.cmd
```

RAG 在前台运行，看到启动完成后，另开一个 PowerShell，从 PaperLens 根目录执行：

```powershell
.\scripts\start-agent.cmd
```

Linux 使用相同配置，分别在两个终端执行：

```bash
rag_service/.venv/bin/python scripts/manage.py rag
.venv/bin/python scripts/manage.py agent
```

工作台为 `http://127.0.0.1:18091/`，Agent 文档为 `http://127.0.0.1:18091/docs`，RAG 文档为 `http://127.0.0.1:18081/docs`。

检查接口：

```powershell
curl.exe -f http://127.0.0.1:18081/health/ready
curl.exe -f http://127.0.0.1:18091/papers
```

`doctor` 检查配置和服务 HTTP 可达性，不执行模型生成，也不证明 API Key 有调用额度或模型语义正确。`/health/ready` 检查 RAG 的 SQLite 与 Qdrant；论文解析与问答仍需通过真实上传验证。

自定义解释器可用 `start-agent.cmd -PythonPath "路径"` 和 `start-rag.cmd -PythonPath "路径"`，也可设置进程环境变量 `PAPERLENS_AGENT_PYTHON` / `PAPERLENS_RAG_PYTHON`。端口可以写入 `.env`，也可以用 `-Port` 参数临时覆盖；修改 RAG 端口后同步更新 `RAG_BASE_URL`。

### 可选：把 RAG API 放在 WSL

在选定的 WSL 发行版中创建 `rag_service/.venv` 并安装 RAG 依赖，然后从 Windows 执行 `start-rag.cmd -Distro "你的发行版名"`。脚本通过 WSL 自身的路径转换进入目录，不假定磁盘挂载路径。也可以设置进程环境变量 `PAPERLENS_WSL_DISTRO`。未设置时默认使用 Windows 本地 Python，不会访问特定发行版。

Windows 和 Linux 的虚拟环境不能共用同一目录；切换运行平台时应重新创建环境或使用另一份源码目录。WSL 自定义 RAG Python 通过其内部环境变量 `PAPERLENS_RAG_PYTHON` 指定。

## 6. 验证使用流程

上传一份可使用的 PDF，等待状态为 ready，然后完成问答、连续追问、生成 HTML、局部修改和下载。逐步操作见 [测试手册](html-testing-guide.md)。

没有模型凭据时，可以在已安装 Agent 依赖的环境中运行 `python scripts/manage.py demo` 查看模拟工作台，默认端口 18192。演示使用固定论文和模拟模型响应，只用于了解交互；真实论文问答应启动 `agent` 与 `rag`。

## 7. 常见问题

- **`python` 打开商店或找不到命令**：Windows 使用 `py -3.12` 创建环境，之后使用虚拟环境里的完整 Python 路径。
- **提示缺少包**：确认给对应服务安装了依赖；Agent、RAG、GPU 模型分别有独立环境。
- **端口已占用**：调整 `.env` 中的端口及对应服务地址；脚本不会自动停止不属于本项目的进程。
- **Grobid 暂未就绪**：首次启动需要加载模型，可用 `docker compose logs --tail 50 grobid` 查看启动状态，稍后重新执行环境检查。
- **Embedding 提示模型不存在**：核对 API 服务名与 `EMBEDDING_MODEL`；本地 tokenizer 路径单独配置。
- **模板值仍未填写**：`doctor` 会把 `replace-*` 等占位值标为缺失，不显示实际密钥。
- **浏览器不可用**：在 Agent 环境执行 `python -m playwright install chromium`。
- **GPU 模型安装失败**：使用 Linux/WSL 与匹配的驱动，按官方文档准备环境；也可连接已有模型服务运行 CPU API。

官方参考：[Qdrant 本地部署](https://qdrant.tech/documentation/quickstart/)、[Qwen Embedding](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)、[Qwen Reranker](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B)。
