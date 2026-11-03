# 文件说明：验证重排序接口输入的约束，尤其是返回数量与候选标识。
# 检查 top_k 不超过候选数量，并拒绝重复的文本块标识。

import pytest
from pydantic import ValidationError

from reranker_service.schemas import RerankRequest


def test_top_k_is_capped_to_document_count() -> None:
    request = RerankRequest(
        query="query",
        documents=[{"chunk_id": "a", "text": "document"}],
        top_k=5,
    )
    assert request.top_k == 1


def test_duplicate_chunk_ids_are_rejected() -> None:
    with pytest.raises(ValidationError):
        RerankRequest(
            query="query",
            documents=[
                {"chunk_id": "a", "text": "one"},
                {"chunk_id": "a", "text": "two"},
            ],
        )
