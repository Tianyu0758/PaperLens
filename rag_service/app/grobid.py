# 文件说明：封装对 Grobid 的调用，将论文 PDF 提交解析并取得 TEI XML。
# 解析结果继续交给 tei_parser.py 提取正文和元数据，连接或解析服务错误在此统一处理。

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx


class GrobidError(RuntimeError):
    pass


@dataclass(slots=True)
class GrobidClient:
    base_url: str
    timeout_seconds: float = 90.0

    async def process_fulltext(self, pdf_path: Path) -> bytes:
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                with pdf_path.open("rb") as pdf_file:
                    response = await client.post(
                        f"{self.base_url}/api/processFulltextDocument",
                        files={"input": (pdf_path.name, pdf_file, "application/pdf")},
                    )
                response.raise_for_status()
        except (httpx.HTTPError, OSError) as exc:
            raise GrobidError(f"GROBID 解析失败: {exc}") from exc

        content = response.content.strip()
        if not content.startswith(b"<"):
            raise GrobidError("GROBID 返回的不是有效 XML")
        return content
