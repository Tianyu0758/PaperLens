# 文件说明：根据检索到的证据上下文调用模型，生成带来源提示的论文回答。
# 封装回答生成、重试和失败处理；证据检索与上下文组装在上游模块完成。

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from openai import AsyncOpenAI

from app.errors import BusinessError


SYSTEM_PROMPT = (
    "你是一个学术论文问答助手。只根据提供的上下文回答问题，不要编造信息。"
    "回答具体数值、年份、比例、数据集、模型、指标和平台名称时，优先从上下文逐字提取并保留原文。"
    "先检查所有上下文片段，再判断证据是否缺失；只有上下文确实没有相关信息时，才明确说明无法确定。"
    "上下文中的【】内为该片段所属章节。"
)


@dataclass(slots=True)
class GeneratedAnswer:
    text: str
    generate_ms: int


class AnswerGenerator:
    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str,
        model: str,
        logger: logging.Logger,
        timeout_seconds: float = 30.0,
        retry_delay_seconds: float = 2.0,
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
                raise BusinessError("生成服务暂时不可用，请稍后重试")
            self._client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout_seconds,
                max_retries=0,
            )
        return self._client

    async def generate(self, question: str, context: str) -> GeneratedAnswer:
        client = self._get_client()
        started = time.perf_counter()
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": (
                                f"上下文：\n{context}\n\n问题：{question}\n\n"
                                "请直接给出清晰、准确的答案。"
                            ),
                        },
                    ],
                    temperature=0.3,
                    max_tokens=1200,
                    timeout=self.timeout_seconds,
                    extra_body={"thinking": {"type": "disabled"}},
                )
                content = (response.choices[0].message.content or "").strip()
                if not content:
                    raise ValueError("DeepSeek 返回了空答案")
                return GeneratedAnswer(
                    text=content,
                    generate_ms=int((time.perf_counter() - started) * 1000),
                )
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(self.retry_delay_seconds)
        self.logger.warning("generation_failed reason=%s", str(last_error)[:200])
        raise BusinessError("生成服务暂时不可用，请稍后重试")
