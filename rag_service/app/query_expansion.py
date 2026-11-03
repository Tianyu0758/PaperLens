# 文件说明：调用模型把问题转换为英文检索表达和假设性学术段落。
# 英文翻译供 BM25 使用，假设性段落供向量检索使用；输出不符合要求时重试或回退到原问题。

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

from openai import AsyncOpenAI


HYDE_PROMPT = """You are an academic writing assistant.

Given a user question, output exactly two sections:

TRANSLATION: Provide an English translation of the question. If it is already in English, repeat it as-is.

HYPOTHETICAL: Write a 150-200 word academic paper passage in English, imitating the style of a Methods or Results section, including relevant technical terms. It does not need to be factually accurate, only stylistically similar to a research paper.

Question: {question}"""


@dataclass(slots=True)
class ExpandedQuery:
    translation: str
    hypothetical: str
    fallback: bool = False


def parse_hyde_output(text: str) -> ExpandedQuery | None:
    cleaned = re.sub(r"[*_`#]", "", text)
    translation_match = re.search(
        r"TRANSLATION\s*[:：]\s*(.+?)(?=HYPOTHETICAL\s*[:：]|$)",
        cleaned,
        re.IGNORECASE | re.DOTALL,
    )
    hypothetical_match = re.search(
        r"HYPOTHETICAL\s*[:：]\s*(.+)",
        cleaned,
        re.IGNORECASE | re.DOTALL,
    )
    if not translation_match or not hypothetical_match:
        return None
    translation = translation_match.group(1).strip()
    hypothetical = hypothetical_match.group(1).strip()
    if not translation or not hypothetical:
        return None
    return ExpandedQuery(translation, hypothetical)


class QueryExpander:
    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str,
        model: str,
        logger: logging.Logger,
        timeout_seconds: float = 10.0,
        retry_delay_seconds: float = 1.0,
        client=None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.logger = logger
        self.timeout_seconds = timeout_seconds
        self.retry_delay_seconds = retry_delay_seconds
        self._client = client

    def _get_client(self):
        if self._client is None:
            if not self.api_key:
                return None
            self._client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout_seconds,
                max_retries=0,
            )
        return self._client

    async def expand(self, question: str) -> ExpandedQuery:
        client = self._get_client()
        if client is None:
            self.logger.warning("hyde_fallback reason=missing_api_key")
            return ExpandedQuery(question, question, fallback=True)
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                response = await client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {
                            "role": "system",
                            "content": "You are an academic writing assistant.",
                        },
                        {
                            "role": "user",
                            "content": HYDE_PROMPT.format(question=question),
                        },
                    ],
                    temperature=0.7,
                    max_tokens=500,
                    timeout=self.timeout_seconds,
                    extra_body={"thinking": {"type": "disabled"}},
                )
                content = response.choices[0].message.content or ""
                parsed = parse_hyde_output(content)
                if parsed is None:
                    raise ValueError("DeepSeek 返回格式不符合 TRANSLATION/HYPOTHETICAL 协议")
                return parsed
            except Exception as exc:
                last_error = exc
                if attempt == 0:
                    await asyncio.sleep(self.retry_delay_seconds)
        self.logger.warning("hyde_fallback reason=%s", str(last_error)[:200])
        return ExpandedQuery(question, question, fallback=True)
