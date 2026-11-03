# 文件说明：验证英文翻译和 HyDE 段落的格式解析、失败重试及回退。
# 通过模型响应替身覆盖正常输出与不合要求的输出。

from types import SimpleNamespace

import pytest

from app.query_expansion import QueryExpander, parse_hyde_output


class FakeCompletions:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1
        output = next(self.outputs)
        if isinstance(output, Exception):
            raise output
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=output))]
        )


class FakeClient:
    def __init__(self, outputs):
        self.chat = SimpleNamespace(completions=FakeCompletions(outputs))


def test_parse_hyde_output_accepts_markdown_and_fullwidth_colon() -> None:
    result = parse_hyde_output(
        "**TRANSLATION：** What improves retrieval?\n\n# HYPOTHETICAL: Dense retrieval improves recall."
    )
    assert result is not None
    assert result.translation == "What improves retrieval?"
    assert result.hypothetical == "Dense retrieval improves recall."


@pytest.mark.asyncio
async def test_expander_retries_once_then_succeeds() -> None:
    client = FakeClient(
        [RuntimeError("temporary"), "TRANSLATION: query\nHYPOTHETICAL: academic passage"]
    )
    expander = QueryExpander(
        api_key="key",
        base_url="https://example.invalid",
        model="model",
        logger=__import__("logging").getLogger("test"),
        retry_delay_seconds=0,
        client=client,
    )
    result = await expander.expand("问题")
    assert result.translation == "query"
    assert result.fallback is False
    assert client.chat.completions.calls == 2


@pytest.mark.asyncio
async def test_expander_falls_back_after_invalid_outputs() -> None:
    client = FakeClient(["invalid", "still invalid"])
    expander = QueryExpander(
        api_key="key",
        base_url="https://example.invalid",
        model="model",
        logger=__import__("logging").getLogger("test"),
        retry_delay_seconds=0,
        client=client,
    )
    result = await expander.expand("原始问题")
    assert result.translation == "原始问题"
    assert result.hypothetical == "原始问题"
    assert result.fallback is True
