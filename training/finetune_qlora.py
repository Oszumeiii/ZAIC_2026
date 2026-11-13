"""Fine-tune QLoRA từ sft_train.jsonl (mỗi dòng: {"messages": [system, user, assistant]}) rồi (tùy chọn) gộp LoRA vào model gốc.

Dùng trên Colab. Qwen3.5-9B: A100 nạp bf16 đầy đủ + LoRA (--quant auto tự chọn), L4/T4 tự nén 4-bit (QLoRA). CHƯA chạy thử
trên GPU: kiểm bằng --dry-run (chỉ token hóa, in phân bố độ dài và số mẫu vừa) trước khi tốn giờ GPU.

    python -m training.finetune_qlora --data sft_train.jsonl --base Qwen/Qwen3.5-9B --out /content/drive/MyDrive/lora --max-len 8192
    python -m training.finetune_qlora --merge --base Qwen/Qwen3.5-9B --out /content/drive/MyDrive/lora --merged /content/merged
"""
from __future__ import annotations

import argparse
import json
import random

from app.llm.local_common import is_hybrid_model, is_qwen3_family, resolve_model_path, use_4bit, weights_gb

# Mọi lớp tuyến tính của khối chú ý, MLP và lớp linear-attention (Qwen3.5: in_proj_qkv/in_proj_z/out_proj). Tên không có trong model bị bỏ qua.
# in_proj_a/in_proj_b chỉ có vài chục đầu ra nên không gắn LoRA.
LORA_TARGETS = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj",
                "in_proj_qkv", "in_proj_z", "out_proj"]


def build_example(tok, messages: list[dict], max_len: int, chat_kwargs: dict | None = None):
    """Mã hóa một mẫu; chỉ phần trả lời của assistant được tính loss. Mẫu dài hơn max_len bị BỎ (không cắt, vì cắt
    đuôi sẽ làm model học code cụt). `chat_kwargs` phải GIỐNG lúc suy luận (Qwen3.5: enable_thinking=False), nếu không
    phần prompt khác tiền tố của bản đầy đủ và mọi mẫu bị loại."""
    kwargs = chat_kwargs or {}
    prompt = tok.apply_chat_template(messages[:-1], tokenize=False, add_generation_prompt=True, **kwargs)
    full = tok.apply_chat_template(messages, tokenize=False, **kwargs)
    prompt_ids = tok(prompt, add_special_tokens=False)["input_ids"]
    full_ids = tok(full, add_special_tokens=False)["input_ids"]
    if full_ids[:len(prompt_ids)] != prompt_ids or len(full_ids) > max_len:
        return None
    labels = [-100] * len(prompt_ids) + full_ids[len(prompt_ids):]
    return {"input_ids": full_ids, "labels": labels}


def load_examples(tok, path: str, max_len: int, chat_kwargs: dict | None = None):
    rows = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
    examples = [e for e in (build_example(tok, r["messages"], max_len, chat_kwargs) for r in rows) if e]
    print(f"{len(examples)}/{len(rows)} mẫu vừa max_len={max_len}")
    lengths = sorted(len(e["input_ids"]) for e in examples)
    if lengths:
        print(f"độ dài token: min {lengths[0]} | trung vị {lengths[len(lengths) // 2]} | max {lengths[-1]}")
    return examples


def collate(batch, pad_id: int):
    import torch
    width = max(len(b["input_ids"]) for b in batch)
    ids = torch.full((len(batch), width), pad_id, dtype=torch.long)
    labels = torch.full((len(batch), width), -100, dtype=torch.long)
    mask = torch.zeros((len(batch), width), dtype=torch.long)
    for i, b in enumerate(batch):
        n = len(b["input_ids"])
        ids[i, :n], labels[i, :n], mask[i, :n] = (torch.tensor(b["input_ids"]), torch.tensor(b["labels"]),
                                                  torch.ones(n, dtype=torch.long))
    return {"input_ids": ids, "labels": labels, "attention_mask": mask}


def load_causal_lm(path: str, dtype, **kwargs):
    """Nạp model chữ-thuần (Qwen3.5 đa phương thức vẫn nạp được bằng AutoModelForCausalLM, bỏ phần ảnh)."""
    from transformers import AutoModelForCausalLM
    try:
        return AutoModelForCausalLM.from_pretrained(path, dtype=dtype, **kwargs)           # transformers >= 5
    except TypeError:
        return AutoModelForCausalLM.from_pretrained(path, torch_dtype=dtype, **kwargs)


def completion_loss_trainer(trainer_cls):
    """Trainer chỉ tính logits ở các vị trí có nhãn (phần trả lời, vài trăm token) thay vì cả chuỗi ~8k token. Từ vựng
    Qwen3.5 là 248k nên logits cả chuỗi (bf16 + bản float32 của loss) tốn cỡ 8GB và là thủ phạm OOM đầu tiên. CHƯA chạy thử
    trên GPU; hỏng thì chạy lại với --full-logits (loss chuẩn của Trainer)."""

    class CompletionLossTrainer(trainer_cls):
        def compute_loss(self, model, inputs, return_outputs=False, num_items_in_batch=None):
            import torch
            base = model.get_base_model() if hasattr(model, "get_base_model") else model
            hidden = base.model(input_ids=inputs["input_ids"], attention_mask=inputs["attention_mask"]).last_hidden_state
            labels = inputs["labels"][:, 1:]
            keep = labels != -100
            logits = base.lm_head(hidden[:, :-1][keep]).float()
            loss = torch.nn.functional.cross_entropy(logits, labels[keep])
            return (loss, None) if return_outputs else loss

    return CompletionLossTrainer


def train(args):
    import torch
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import AutoTokenizer, BitsAndBytesConfig, Trainer, TrainingArguments

    chat_kwargs = {"enable_thinking": False} if is_qwen3_family(args.base) else {}
    tok = AutoTokenizer.from_pretrained(args.base)        # chỉ tải tokenizer: --dry-run không cần tải cả model
    examples = load_examples(tok, args.data, args.max_len, chat_kwargs)
    if args.dry_run or not examples:
        return
    base = resolve_model_path(args.base)
    random.Random(0).shuffle(examples)
    use_bf16 = torch.cuda.is_bf16_supported()          # T4 không có bf16
    dtype = torch.bfloat16 if use_bf16 else torch.float16
    vram_gb = torch.cuda.get_device_properties(0).total_memory / 1e9
    quant4 = use_4bit(args.quant, vram_gb, weights_gb(base))
    print(f"[train] GPU {vram_gb:.0f}GB | {'QLoRA 4-bit' if quant4 else 'LoRA trên bf16/fp16 đầy đủ'} | "
          f"lai linear-attention: {is_hybrid_model(base)}")
    kw = {"device_map": {"": 0}, "attn_implementation": "sdpa"}
    if quant4:
        kw["quantization_config"] = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                                       bnb_4bit_compute_dtype=dtype, bnb_4bit_use_double_quant=True)
    model = load_causal_lm(base, dtype, **kw)
    if quant4:
        model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)
    else:
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        model.enable_input_require_grads()
    model.config.use_cache = False
    model = get_peft_model(model, LoraConfig(r=args.rank, lora_alpha=2 * args.rank, lora_dropout=0.05,
                                             task_type="CAUSAL_LM", target_modules=LORA_TARGETS))
    model.print_trainable_parameters()
    trainer_cls = Trainer if args.full_logits else completion_loss_trainer(Trainer)
    trainer = trainer_cls(
        model=model, train_dataset=examples, data_collator=lambda b: collate(b, tok.pad_token_id or tok.eos_token_id),
        args=TrainingArguments(
            output_dir=args.out, per_device_train_batch_size=1, gradient_accumulation_steps=args.grad_accum,
            num_train_epochs=args.epochs, learning_rate=args.lr, lr_scheduler_type="cosine", warmup_ratio=0.05,
            bf16=use_bf16, fp16=not use_bf16, gradient_checkpointing=True, logging_steps=5, save_strategy="epoch",
            report_to="none", remove_unused_columns=False, optim="paged_adamw_8bit"))
    trainer.train()
    model.save_pretrained(args.out)
    tok.save_pretrained(args.out)
    print("đã lưu LoRA adapter ->", args.out)


def merge(args):
    """Gộp adapter vào model gốc (bf16, trên CPU) để vLLM nạp trực tiếp ở vòng nộp (không cần peft lúc suy luận)."""
    import torch
    from peft import PeftModel
    from transformers import AutoTokenizer
    path = resolve_model_path(args.base)
    base = load_causal_lm(path, torch.bfloat16, device_map="cpu")
    merged = PeftModel.from_pretrained(base, args.out).merge_and_unload()
    merged.save_pretrained(args.merged, safe_serialization=True)
    AutoTokenizer.from_pretrained(path).save_pretrained(args.merged)
    print("đã lưu model gộp ->", args.merged)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", default="sft_train.jsonl")
    p.add_argument("--base", required=True, help="model GỐC bf16/fp16 (không phải bản AWQ), vd Qwen/Qwen3.5-9B")
    p.add_argument("--out", required=True, help="thư mục lưu LoRA adapter")
    p.add_argument("--max-len", type=int, default=8192)
    p.add_argument("--rank", type=int, default=16)
    p.add_argument("--epochs", type=int, default=2)
    p.add_argument("--lr", type=float, default=1e-4)
    p.add_argument("--grad-accum", type=int, default=16)
    p.add_argument("--quant", choices=["auto", "4bit", "none"], default="auto",
                   help="auto: nén 4-bit (QLoRA) chỉ khi VRAM không đủ cho bf16; none: LoRA trên bf16 đầy đủ (A100)")
    p.add_argument("--full-logits", action="store_true",
                   help="dùng loss chuẩn của Trainer (tính logits cả chuỗi, tốn thêm ~8GB ở 8k token); mặc định chỉ tính ở phần trả lời")
    p.add_argument("--dry-run", action="store_true", help="chỉ token hóa và in phân bố độ dài, không train")
    p.add_argument("--merge", action="store_true")
    p.add_argument("--merged", help="thư mục lưu model đã gộp (dùng với --merge)")
    args = p.parse_args()
    merge(args) if args.merge else train(args)


if __name__ == "__main__":
    main()
