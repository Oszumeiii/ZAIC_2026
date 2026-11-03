"""Backend local bằng transformers (+ bitsandbytes 4-bit). Chậm hơn vLLM nhưng ổn định, chạy được trên T4/P100."""
from __future__ import annotations

import os
import re

from app.config import Config
from .base import GenerationError, LLMBackend
from .local_common import has_module, is_prequantized, quant_method, resolve_model_path


def hf_model_path(cfg: Config, path: str) -> str:
    """Engine hf không đọc được AWQ/GPTQ nếu thiếu thư viện -> dùng model gốc fp16 (nén 4-bit khi nạp)."""
    qm = quant_method(path)
    need = {"awq": ("awq",), "gptq": ("gptqmodel", "auto_gptq")}.get(qm)
    if need is None or has_module(*need):
        return path
    if cfg.hf_base_model:
        base = cfg.hf_base_model
    elif not os.path.isdir(cfg.local_model):
        base = re.sub(r"-(AWQ|GPTQ.*)$", "", cfg.local_model, flags=re.I)
    else:
        raise RuntimeError(f"Model {qm.upper()} cần thư viện tương ứng khi chạy engine hf. Đặt hf_base_model = repo "
                           "model gốc fp16 (vd 'Qwen/Qwen2.5-Coder-7B-Instruct') hoặc chạy engine vllm.")
    print(f"Engine hf không đọc được {qm.upper()} -> dùng model gốc {base} (nén 4-bit khi nạp).")
    return resolve_model_path(base)


def prefill_chunked(model, ids, chunk: int):
    """Nạp prompt vào KV-cache từng đoạn `chunk` token (chừa token cuối cho generate). Attention mỗi đoạn chỉ
    chunk×L thay vì L×L (prompt ~9k token cần ~8,7 GiB ma trận attention, nổ VRAM T4)."""
    import torch
    from transformers import DynamicCache
    cache, last = DynamicCache(), ids.shape[1] - 1
    with torch.no_grad():
        for start in range(0, last, chunk):
            model(input_ids=ids[:, start:min(start + chunk, last)], past_key_values=cache, use_cache=True)
    return cache


class HFBackend(LLMBackend):
    supports_batch = True
    engine = "hf"

    def __init__(self, cfg: Config, path: str, n_gpu: int):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        self.cfg, self._torch = cfg, torch
        path = hf_model_path(cfg, path)
        self.tok = AutoTokenizer.from_pretrained(path)
        use_cuda = n_gpu > 0
        kw: dict = {"attn_implementation": "sdpa"}
        if use_cuda:
            kw["device_map"] = {"": 0}        # 1 GPU: 7B 4-bit chỉ ~5,5GB; chia qua 2 T4 chỉ thêm độ trễ
            if not is_prequantized(path):     # model gốc fp16 -> nén 4-bit NF4 khi nạp (khớp cách QLoRA huấn luyện)
                from transformers import BitsAndBytesConfig
                kw["quantization_config"] = BitsAndBytesConfig(
                    load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.float16,
                    bnb_4bit_use_double_quant=True)
        dtype = torch.float16 if use_cuda else torch.float32
        try:
            model = AutoModelForCausalLM.from_pretrained(path, dtype=dtype, **kw)          # transformers mới
        except TypeError:
            model = AutoModelForCausalLM.from_pretrained(path, torch_dtype=dtype, **kw)
        if cfg.local_lora:
            from peft import PeftModel
            model = PeftModel.from_pretrained(model, cfg.local_lora)
        self.model = model.eval()

    def tag(self) -> str:
        c = self.cfg
        return f"local|{c.local_model}|{c.local_lora}|hf|T{c.temperature}|L{c.max_model_len}"

    def count_tokens(self, text: str) -> int:
        return len(self.tok(text, add_special_tokens=False).input_ids)

    def generate(self, system: str, prompt: str, n: int = 1) -> list[str]:
        """n lời giải trong MỘT lượt (prefill một lần rồi nhân bản cache); hết VRAM thì tách lẻ từng mẫu."""
        torch, cfg, tok = self._torch, self.cfg, self.tok
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        text = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True, **cfg.chat_kwargs)
        ids = tok(text, return_tensors="pt", add_special_tokens=False)["input_ids"].to(self.model.device)
        prompt_len = ids.shape[1]

        def run(k: int) -> list[str]:
            sample = (dict(do_sample=True, temperature=cfg.temperature, top_p=0.95)
                      if cfg.temperature > 0 else dict(do_sample=False))
            x, extra = ids, {}
            if cfg.prefill_chunk:
                cache = prefill_chunked(self.model, ids, cfg.prefill_chunk)
                if k > 1:
                    cache.batch_repeat_interleave(k)
                extra["past_key_values"] = cache
            if k > 1:
                x = ids.repeat(k, 1)
            with torch.no_grad():
                out = self.model.generate(input_ids=x, attention_mask=torch.ones_like(x),
                                          max_new_tokens=cfg.max_new_tokens, pad_token_id=tok.eos_token_id,
                                          **extra, **sample)
            return [tok.decode(o[prompt_len:], skip_special_tokens=True) for o in out]

        try:
            try:
                return run(n)
            except Exception as e:
                if n == 1:
                    raise
                torch.cuda.empty_cache()
                print(f"[local] sinh batch {n} lỗi ({type(e).__name__}: {str(e)[:120]}) -> sinh lần lượt từng mẫu")
                return [run(1)[0] for _ in range(n)]
        except Exception as e:
            torch.cuda.empty_cache()                 # thường là OOM khi prompt quá dài
            raise GenerationError(repr(e)[:300]) from e
