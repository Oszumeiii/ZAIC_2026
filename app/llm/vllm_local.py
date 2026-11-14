"""Backend local bằng vLLM (nhanh, có prefix cache; cài dễ xung đột torch trên Kaggle)."""
from __future__ import annotations

import os

from app.config import Config
from .base import GenerationError, LLMBackend


class VLLMBackend(LLMBackend):
    supports_batch = True
    engine = "vllm"

    def __init__(self, cfg: Config, path: str, n_gpu: int):
        from transformers import AutoTokenizer
        self.cfg = cfg
        self.reset_seed(2026)
        os.environ.update({k: str(v) for k, v in cfg.vllm_env.items()})     # phải đặt TRƯỚC khi import vllm
        tp = max(1, min(cfg.tensor_parallel, n_gpu))
        if tp > 1 and os.environ.get("VLLM_ENABLE_V1_MULTIPROCESSING") == "0":
            os.environ.pop("VLLM_ENABLE_V1_MULTIPROCESSING")                # TP>1 bắt buộc nhiều tiến trình
        try:
            from vllm import LLM
        except Exception as e:
            raise RuntimeError(
                f"Không import được vllm ({type(e).__name__}: {str(e)[:200]}).\n"
                "Thường do `pip install vllm` đổi bản torch lệch với torchaudio/torchvision có sẵn của Kaggle.\n"
                "Cách chắc nhất: Stop session để reset môi trường, KHÔNG cài vllm, dùng local_engine='auto' hoặc 'hf'."
            ) from e
        kw = dict(model=path, dtype="bfloat16", max_model_len=cfg.max_model_len, gpu_memory_utilization=cfg.gpu_memory_utilization,
                  tensor_parallel_size=tp, enable_prefix_caching=True)
        if cfg.local_lora:
            kw.update(enable_lora=True, max_lora_rank=cfg.lora_rank)
        kw.update(cfg.vllm_extra)
        self.llm = LLM(**kw)
        self.tok = AutoTokenizer.from_pretrained(path)

    def tag(self) -> str:
        c = self.cfg
        return f"local|{c.local_model}|{c.local_lora}|vllm|T{c.temperature}|L{c.max_model_len}"

    def count_tokens(self, text: str) -> int:
        return len(self.tok(text, add_special_tokens=False).input_ids)

    def reset_seed(self, seed):
        self.seed, self.calls = int(seed), 0

    def prompt_tokens(self, system, prompt):
        text = self.tok.apply_chat_template(
            [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            tokenize=False, add_generation_prompt=True, **self.cfg.chat_kwargs)
        return len(self.tok(text, add_special_tokens=False).input_ids)

    def generate(self, system: str, prompt: str, n: int = 1) -> list[str]:
        return self.generate_with_options(system, prompt, n=n)

    def generate_with_options(self, system, prompt, n=1, max_new_tokens=None, temperature=None):
        from vllm import SamplingParams
        from .local_common import sampling_kwargs
        cfg = self.cfg
        budget = cfg.max_new_tokens if max_new_tokens is None else max_new_tokens
        temp = cfg.temperature if temperature is None else temperature
        if self.prompt_tokens(system, prompt) + budget > cfg.max_model_len:
            raise GenerationError('Context exceeds local model budget')
        params = sampling_kwargs(cfg.local_model, temp) if temp > 0 else {'temperature': 0.0}
        sp = SamplingParams(**params, max_tokens=budget, n=n, seed=(self.seed + self.calls) % (2**32))
        self.calls += 1
        kw = {}
        if cfg.local_lora:
            from vllm.lora.request import LoRARequest
            kw['lora_request'] = LoRARequest('sft', 1, cfg.local_lora)
        if cfg.chat_kwargs:
            kw['chat_template_kwargs'] = cfg.chat_kwargs
        try:
            out = self.llm.chat([{'role': 'system', 'content': system},
                                 {'role': 'user', 'content': prompt}], sp, use_tqdm=False, **kw)
        except Exception as error:
            raise GenerationError(repr(error)[:300]) from error
        self.last_reasoning = [getattr(o, 'reasoning_content', None) for o in out[0].outputs]
        if not any(v is not None for v in self.last_reasoning):
            self.last_reasoning = None
        return [o.text for o in out[0].outputs]
