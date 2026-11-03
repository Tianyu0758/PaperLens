# 文件说明：验证证据上下文在预算内组装，并保持候选文本原有的优先顺序。
# 使用简单计数器检查上下文截取边界，不请求外部模型。

import logging

from app.context_builder import ContextBuilder


class WordCodec:
    def encode(self, text):
        return text.split()


def test_context_builder_stops_at_limit_and_keeps_order() -> None:
    builder = ContextBuilder(WordCodec(), logging.getLogger("test"), max_tokens=5)
    result = builder.build(
        [
            {"chunk_id": "a", "text": "one two three"},
            {"chunk_id": "b", "text": "four five"},
            {"chunk_id": "c", "text": "six"},
        ]
    )
    assert result.text == "one two three\n\nfour five"
    assert [item["chunk_id"] for item in result.candidates] == ["a", "b"]
    assert result.token_count == 5
    assert result.dropped_count == 1
