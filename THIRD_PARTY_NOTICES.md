# 第三方组件与素材声明

PaperLens 的原创代码与文档采用根目录 [MIT 许可](LICENSE)。第三方组件、模型、论文原文及其衍生展示内容保留各自的权利和许可，不因本仓库采用 MIT 而改变。

## Qwen 模型与参考实现

- 模型：[Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)、[Qwen3-Reranker-0.6B](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B)。两者的官方模型卡标注 Apache-2.0。
- 使用方式参考：[Qwen3-Embedding 官方项目](https://github.com/QwenLM/Qwen3-Embedding) 和上述模型卡。
- `rag_service/reranker_service/engine.py` 的输入格式、yes/no token 评分方式参考官方模型使用示例；项目增加了服务配置、输入预算限制与批量评分封装。此处保留来源说明及 [Apache-2.0 全文](licenses/Apache-2.0.txt)。
- 仓库不包含模型权重、分词器文件或第三方推理运行时。使用者需自行获取，并遵守模型及运行时的许可和使用条款。

## 架构图生成工具

`docs/assets/architecture.svg` 由 Mermaid 12.2.0 根据仓库中的 `.mmd` 源文件生成。Mermaid 采用 MIT 许可，Copyright (c) 2014–2022 Knut Sveidqvist；许可全文保留在 [licenses/Mermaid-MIT.txt](licenses/Mermaid-MIT.txt)。仓库没有打包 Mermaid JavaScript 或 Node 依赖。

## 论文与演示素材

真实联调、截图及视频研读的论文为：

> Yaosheng Yang, Meishan Zhang, Wenliang Chen, Wei Zhang, Haofen Wang, and Min Zhang. 2018. *Adversarial Learning for Chinese NER from Crowd Annotations*. Proceedings of the Thirty-Second AAAI Conference on Artificial Intelligence, pp. 1627–1634.

[出版方页面](https://ojs.aaai.org/index.php/AAAI/article/view/11507) · [出版方 PDF](https://ojs.aaai.org/index.php/AAAI/article/download/11507/11366)

原 PDF 标注 Copyright © 2018, Association for the Advancement of Artificial Intelligence. All rights reserved。论文的文字、实验数据、结论及图表不属于 PaperLens 的原创成果，不适用本仓库的 MIT 许可。截图和视频中的有限原文片段用于展示引用回查功能；中文分析和方法示意图是模型基于原文生成的研读材料，应回查论文。复用这些素材时，应单独核实原文权利及适用条件。

仓库不分发原论文 PDF 或完整解析文本。`examples/presentation.html` 与对应结构化稿使用明确标注的模拟数据；真实截图中的论文实验指标不代表 PaperLens 的性能。

## 安装时获取的依赖

以下组件通过 pip、Docker 或其他安装方式单独获取，没有将其源码、二进制或模型权重嵌入本仓库。下表列出主要直接依赖的许可，实际安装版本及传递依赖请以其随附声明为准。

| 组件 | 上游 | 许可 |
| --- | --- | --- |
| FastAPI | [fastapi/fastapi](https://github.com/fastapi/fastapi) | MIT |
| Uvicorn | [Kludex/uvicorn](https://github.com/Kludex/uvicorn) | BSD-3-Clause |
| HTTPX | [encode/httpx](https://github.com/encode/httpx) | BSD-3-Clause |
| python-multipart | [Kludex/python-multipart](https://github.com/Kludex/python-multipart) | Apache-2.0（当前锁定版本） |
| python-dotenv | [theskumar/python-dotenv](https://github.com/theskumar/python-dotenv) | BSD-3-Clause |
| LangChain / langchain-openai | [langchain-ai/langchain](https://github.com/langchain-ai/langchain) | MIT |
| Pydantic | [pydantic/pydantic](https://github.com/pydantic/pydantic) | MIT |
| Playwright | [microsoft/playwright-python](https://github.com/microsoft/playwright-python) | Apache-2.0 |
| lxml | [lxml/lxml](https://github.com/lxml/lxml) | BSD-3-Clause；另含依赖声明 |
| Qdrant / qdrant-client | [qdrant/qdrant](https://github.com/qdrant/qdrant)、[qdrant/qdrant-client](https://github.com/qdrant/qdrant-client) | Apache-2.0 |
| BM25S | [xhluca/bm25s](https://github.com/xhluca/bm25s) | MIT |
| Transformers | [huggingface/transformers](https://github.com/huggingface/transformers) | Apache-2.0 |
| OpenAI Python SDK | [openai/openai-python](https://github.com/openai/openai-python) | Apache-2.0 |
| Jinja | [pallets/jinja](https://github.com/pallets/jinja) | BSD-3-Clause |
| vLLM | [vllm-project/vllm](https://github.com/vllm-project/vllm) | Apache-2.0 |
| GROBID | [kermitt2/grobid](https://github.com/kermitt2/grobid) | Apache-2.0；其模型与依赖以镜像声明为准 |

截图与视频使用操作系统已有字体，仓库不分发字体文件。用户自行配置的模型 API 受服务提供方的条款约束。
