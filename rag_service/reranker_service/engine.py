# 文件说明：加载 Qwen3 重排序模型并计算问题与候选文本的相关性分数。
# 把候选文本转换为模型输入，执行评分并按相关性返回结果，供服务接口复用。
# 输入格式与 yes/no 评分参考 Qwen3-Reranker 官方模型卡示例（Apache-2.0）。
# 来源和许可范围见仓库根目录 THIRD_PARTY_NOTICES.md 及 licenses/Apache-2.0.txt。

from __future__ import annotations

import math

from transformers import AutoTokenizer
from vllm import LLM, SamplingParams
from vllm.inputs.data import TokensPrompt


TASK = "Given an academic search query, retrieve relevant passages that answer the query."


class RerankerEngine:
    def __init__(
        self,
        model_path: str,
        *,
        max_model_len: int = 2048,
        gpu_memory_utilization: float = 0.45,
        dtype: str = "half",
    ) -> None:
        if not model_path.strip():
            raise RuntimeError("RERANKER_MODEL must be a model ID or local model directory")
        self.max_model_len = max_model_len
        self.tokenizer = AutoTokenizer.from_pretrained(model_path)
        self.yes_id = self.tokenizer("yes", add_special_tokens=False).input_ids[0]
        self.no_id = self.tokenizer("no", add_special_tokens=False).input_ids[0]
        self.sampling = SamplingParams(
            temperature=0,
            max_tokens=1,
            logprobs=2,
            allowed_token_ids=[self.yes_id, self.no_id],
        )
        self.model = LLM(
            model=model_path,
            task="generate",
            max_model_len=max_model_len,
            gpu_memory_utilization=gpu_memory_utilization,
            dtype=dtype,
            enforce_eager=True,
        )

    def _prompt(self, query: str, document: str) -> TokensPrompt:
        # 使用模型公开约定的 system/user 格式，并保留最终 yes/no 判断所需的后缀。
        # 不能依赖某台机器上修改过的 tokenizer 模板或自定义 query/document 角色。
        prefix = ('<|im_start|>system\nJudge whether the Document answers the Query under the Instruct. '
                  'Reply with only "yes" or "no".<|im_end|>\n<|im_start|>user\n')
        body = f"<Instruct>: {TASK}\n\n<Query>: {query}\n\n<Document>: {document}"
        suffix = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
        prefix_ids = self.tokenizer.encode(prefix, add_special_tokens=False)
        suffix_ids = self.tokenizer.encode(suffix, add_special_tokens=False)
        remaining = self.max_model_len - len(prefix_ids) - len(suffix_ids)
        if remaining < 1:
            raise ValueError("RERANKER_MAX_MODEL_LEN is too small for the ranking prompt")
        token_ids = prefix_ids + self.tokenizer.encode(body, add_special_tokens=False)[:remaining] + suffix_ids
        return TokensPrompt(prompt_token_ids=token_ids)

    def score(self, query: str, documents: list[str]) -> list[float]:
        prompts = [self._prompt(query, document) for document in documents]
        outputs = self.model.generate(prompts, self.sampling, use_tqdm=False)
        scores: list[float] = []
        for output in outputs:
            logprobs = output.outputs[0].logprobs[-1]
            yes_logprob = logprobs[self.yes_id].logprob if self.yes_id in logprobs else -10.0
            no_logprob = logprobs[self.no_id].logprob if self.no_id in logprobs else -10.0
            yes_probability = math.exp(yes_logprob)
            no_probability = math.exp(no_logprob)
            scores.append(yes_probability / (yes_probability + no_probability))
        return scores
