# 文件说明：封装 Agent 到 RAG 服务的异步 HTTP 调用，提供上传、状态查询、检索、结构浏览和原文读取。
# Agent 工具通过此客户端访问论文数据；上传返回状态后，由工作台查询论文是否就绪。

from __future__ import annotations

from urllib.parse import quote, urlsplit

import httpx


class RagClient:
    """供 Agent 和工作台调用的 RAG 服务客户端。

    search、inspect、read 分别支撑 agent.py 中的 search_paper、
    inspect_paper、read_paper 工具；其他方法供工作台管理论文使用。
    本类只负责发送 HTTP 请求并解析 JSON，实际解析、检索和读取由 RAG 服务完成。
    各请求通过 raise_for_status() 检查响应；HTTP 错误、连接失败和超时交给调用方处理。
    """

    def __init__(self, base_url: str) -> None:
        """初始化 RAG 服务地址，并决定是否读取系统代理等环境配置。

        参数：
            base_url：RAG 服务根地址，例如 http://127.0.0.1:18081。

        去掉地址末尾的斜杠，方便后续拼接接口路径。
        访问 localhost 或回环 IP 时关闭 trust_env，避免本机请求被环境代理转发；
        访问其他主机时保留 httpx 默认的环境配置读取行为。此时不会发送请求。
        """
        self.base_url = base_url.rstrip("/")
        self.trust_env = urlsplit(self.base_url).hostname not in {"localhost", "127.0.0.1", "::1"}


    async def search(self, paper_id: str, query: str, top_k: int = 5) -> dict:
        """围绕问题检索指定论文中的证据，供 search_paper 工具调用。

        参数：
            paper_id：目标论文 ID。
            query：检索问题或关键词，不是待生成的回答。
            top_k：最多返回多少条证据，默认 5；服务端允许 1～20。

        向 /papers/{paper_id}/search 发送 POST 请求，由 RAG 服务执行检索。
        返回包含 paper_id、results（证据文本、章节、页码等）和 timings 的字典。
        本方法不生成最终回答，Agent 会根据检索到的证据继续分析。
        """
        async with httpx.AsyncClient(timeout=90, trust_env=self.trust_env) as client:
            response = await client.post(
                f"{self.base_url}/papers/{quote(paper_id, safe='')}/search",
                json={"query": query, "top_k": top_k},
            )
            response.raise_for_status()
            return response.json()

    async def inspect(self, paper_id: str) -> dict:
        """浏览论文整体结构，供 inspect_paper 工具在精读前了解论文。

        参数：
            paper_id：已完成入库的论文 ID。

        请求 /papers/{paper_id}/inspect，返回论文元数据、摘要、章节目录、
        章节 ID、可用页码及当前入库版本 ingestion_id 等信息。
        摘要最多返回 5000 个字符，是否截断由 abstract_truncated 标记。
        返回结果不含各章节全文；Agent 可用章节 ID 再调用 read 定向阅读。
        """
        async with httpx.AsyncClient(timeout=30, trust_env=self.trust_env) as client:
            response = await client.get(f"{self.base_url}/papers/{quote(paper_id, safe='')}/inspect")
            response.raise_for_status()
            return response.json()

    async def read(self, paper_id: str, *, section_id: str | None = None, chunk_id: str | None = None,
                   offset: int = 0, limit: int = 8000, ingestion_id: str | None = None) -> dict:
        """读取指定章节或文本块的原文，供 read_paper 工具补充上下文。

        参数：
            paper_id：目标论文 ID。
            section_id：inspect 返回的章节 ID，与 chunk_id 必须且只能指定一个。
            chunk_id：search 返回的文本块 ID，可用于查看命中证据的原文。
            offset：从目标原文的第几个字符开始读，从 0 计数。
            limit：本次最多读取的字符数，默认 8000；单位不是 token 或页数。
            ingestion_id：可选的入库版本 ID；版本变化时服务端拒绝读取，
                提醒调用方重新浏览论文，避免使用旧章节或文本块定位。

        请求 /papers/{paper_id}/read，未提供的可选参数不会放进查询字符串。
        返回 results 中的原文证据及 total_chars、next_offset 等分页信息。
        next_offset 非空时，可将其作为下一次的 offset 继续读取。
        """
        params = {k: v for k, v in dict(section_id=section_id, chunk_id=chunk_id, offset=offset,
                                       limit=limit, ingestion_id=ingestion_id).items() if v is not None}
        async with httpx.AsyncClient(timeout=30, trust_env=self.trust_env) as client:
            response = await client.get(f"{self.base_url}/papers/{quote(paper_id, safe='')}/read", params=params)
            response.raise_for_status()
            return response.json()

    async def paper_status(self, paper_id: str) -> dict:
        """查询论文处理状态，供工作台在上传后轮询入库进度。

        参数：
            paper_id：上传接口返回的论文 ID。

        请求 /papers/{paper_id}/status，返回 paper_id、status、
        error_message 和 updated_at。只有状态为 ready 才能进行检索和阅读。
        本方法只查询一次，不负责循环等待；查询间隔和结束条件由调用方决定。
        """
        async with httpx.AsyncClient(timeout=15, trust_env=self.trust_env) as client:
            response = await client.get(f"{self.base_url}/papers/{quote(paper_id, safe='')}/status")
            response.raise_for_status()
            return response.json()

    async def list_papers(self, limit: int = 20) -> list[dict]:
        """获取已就绪论文的摘要列表，供工作台选择会话要绑定的论文。

        参数：
            limit：最多返回的论文数量，默认 20；服务端允许 1～20。

        请求 /papers，返回列表，每项包含论文 ID、标题、作者、年份、状态等。
        当前接口只列出 ready 论文；查看上传后尚未就绪的论文应使用 paper_status。
        """
        async with httpx.AsyncClient(timeout=15, trust_env=self.trust_env) as client:
            response = await client.get(f"{self.base_url}/papers", params={"limit": limit})
            response.raise_for_status()
            return response.json()

    async def upload_pdf(self, filename: str, content: bytes) -> dict:
        """将 PDF 提交给 RAG 服务，并返回论文 ID 和当前处理状态。

        参数：
            filename：上传文件名，例如 paper.pdf，用于 multipart 文件字段。
            content：PDF 的原始二进制内容，不是本地路径或 Base64 字符串。

        以 multipart/form-data 请求 /upload，文件字段名为 file。
        返回包含 paper_id 和 status 的字典。新论文的解析与入库在服务端后台执行，
        接口返回不代表已经入库完成；调用方应使用 paper_status 确认 ready。
        """
        async with httpx.AsyncClient(timeout=90, trust_env=self.trust_env) as client:
            response = await client.post(f"{self.base_url}/upload",
                                         files={"file": (filename, content, "application/pdf")})
            response.raise_for_status()
            return response.json()
