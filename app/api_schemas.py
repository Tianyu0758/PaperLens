# 文件说明：定义会话创建、重命名、任务提交和重试请求的数据格式。
# 通过 Pydantic 校验用户标识、问题、请求去重标识和运行模式，供 main.py 的 HTTP 接口使用。

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SessionCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    user_id: str = Field(min_length=1, max_length=128)
    paper_id: str = Field(min_length=1, max_length=128)
    title: str = Field(default="新对话", min_length=1, max_length=80)


class SessionRename(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    title: str = Field(min_length=1, max_length=80)


class RunCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    question: str = Field(min_length=1, max_length=8000)
    request_id: str = Field(min_length=1, max_length=128)
    mode: Literal["agent", "qa"] = "agent"


class RunRetry(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    request_id: str = Field(min_length=1, max_length=128)
