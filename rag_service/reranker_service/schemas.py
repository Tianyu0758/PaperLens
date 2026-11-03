# 文件说明：定义重排序请求、候选文档和返回结果的数据结构。
# 校验候选标识和返回数量等条件，避免重复候选或不合理参数影响重排序调用。

from pydantic import BaseModel, Field, model_validator


class RerankDocument(BaseModel):
    chunk_id: str = Field(min_length=1)
    text: str = Field(min_length=1)


class RerankRequest(BaseModel):
    query: str = Field(min_length=1)
    documents: list[RerankDocument] = Field(min_length=1, max_length=20)
    top_k: int = Field(default=5, ge=1, le=20)

    @model_validator(mode="after")
    def validate_top_k(self):
        if self.top_k > len(self.documents):
            self.top_k = len(self.documents)
        if len({document.chunk_id for document in self.documents}) != len(self.documents):
            raise ValueError("chunk_id must be unique")
        return self


class RerankItem(BaseModel):
    chunk_id: str
    score: float = Field(ge=0.0, le=1.0)


class RerankResponse(BaseModel):
    results: list[RerankItem]
