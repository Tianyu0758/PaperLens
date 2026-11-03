# 文件说明：验证证据上下文交给回答生成器后的重试与错误处理行为。
# 用模型客户端替身模拟成功和连续失败，不调用真实语言模型。

from types import SimpleNamespace

import pytest

from app.answer_generation import AnswerGenerator
from app.errors import BusinessError


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


@pytest.mark.asyncio
async def test_generator_retries_and_returns_answer() -> None:
    client = FakeClient([RuntimeError("temporary"), "基于上下文的答案"])
    generator = AnswerGenerator(
        api_key="key",
        base_url="https://example.invalid",
        model="model",
        logger=__import__("logging").getLogger("test"),
        retry_delay_seconds=0,
        client=client,
    )
    result = await generator.generate("问题", "上下文")
    assert result.text == "基于上下文的答案"
    assert client.chat.completions.calls == 2


@pytest.mark.asyncio
async def test_generator_raises_business_error_after_three_failures() -> None:
    client = FakeClient([RuntimeError("x"), RuntimeError("y"), RuntimeError("z")])
    generator = AnswerGenerator(
        api_key="key",
        base_url="https://example.invalid",
        model="model",
        logger=__import__("logging").getLogger("test"),
        retry_delay_seconds=0,
        client=client,
    )
    with pytest.raises(BusinessError, match="生成服务暂时不可用"):
        await generator.generate("问题", "上下文")
    assert client.chat.completions.calls == 3
