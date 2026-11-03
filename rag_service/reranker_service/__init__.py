# 文件说明：独立重排序服务的包入口，将模型推理相关代码与 RAG 业务分开组织。
# RAG 通过 HTTP 调用本服务，而不是在每次检索中重新加载重排序模型。

"""Standalone Qwen3 reranker service."""
