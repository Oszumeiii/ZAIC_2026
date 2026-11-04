"""Fine-tune QLoRA từ sft_train.jsonl (mỗi dòng: {"messages": [system, user, assistant]}) rồi (tùy chọn) gộp LoRA vào model gốc.

Dùng trên Colab (A100 cho 14B; T4 chỉ nên 7B và độ dài ngắn). CHƯA chạy thử trên GPU: kiểm bằng --dry-run
(chỉ token hóa, in phân bố độ dài) trước khi tốn giờ GPU.

    python -m training.finetune_qlora --data sft_train.jsonl --base Qwen/Qwen2.5-Coder-7B-Instruct --out /content/drive/MyDrive/lora --max-len 8192
    python -m training.finetune_qlora --merge --base Qwen/Qwen2.5-Coder-7B-Instruct --out /content/drive/MyDrive/lora --merged /content/drive/MyDrive/merged
"""
from __future__ import annotations

import argparse
import json
import random


def build_example(tok, messages: list[dict], max_len: int):
    """Mã hóa một mẫu; chỉ phần trả lời của assistant được tính loss. Mẫu dài hơn max_len bị BỎ (không cắt, vì cắt
    đuôi sẽ làm model học code cụt)."""
    prompt = tok.apply_chat_template(messages[:-1], tokenize=False, add_generation_prompt=True)
    full = tok.apply_chat_template(messages, tokenize=False)
    prompt_ids = tok(prompt, add_special_tokens=False)["input_ids"]
    full_ids = tok(full, add_special_tokens=False)["input_ids"]
    if full_ids[:len(prompt_ids)] != prompt_ids or len(full_ids) > max_len:
        return None
    labels = [-100] * len(prompt_ids) + full_ids[len(prompt_ids):]
    return {"input_ids": full_ids, "labels": labels}


def load_examples(tok, path: str, max_len: int):
    rows = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
    examples = [e for e in (build_example(tok, r["messages"], max_len) for r in rows) if e]
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


def train(args):
    import torch
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import (AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, Trainer, TrainingArguments)

    tok = AutoTokenizer.from_pretrained(args.base)
    examples = load_examples(tok, args.data, args.max_len)
    if args.dry_run or not examples:
        return
    random.Random(0).shuffle(examples)
    use_bf16 = torch.cuda.is_bf16_supported()          # T4 không có bf16
    dtype = torch.bfloat16 if use_bf16 else torch.float16
    model = AutoModelForCausalLM.from_pretrained(
        args.base, device_map={"": 0}, attn_implementation="sdpa",
        quantization_config=BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                               bnb_4bit_compute_dtype=dtype, bnb_4bit_use_double_quant=True))
    model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)
    model = get_peft_model(model, LoraConfig(
        r=args.rank, lora_alpha=2 * args.rank, lora_dropout=0.05, task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]))
    model.print_trainable_parameters()
    trainer = Trainer(
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
    from transformers import AutoModelForCausalLM, AutoTokenizer
    base = AutoModelForCausalLM.from_pretrained(args.base, torch_dtype=torch.bfloat16, device_map="cpu")
    merged = PeftModel.from_pretrained(base, args.out).merge_and_unload()
    merged.save_pretrained(args.merged, safe_serialization=True)
    AutoTokenizer.from_pretrained(args.base).save_pretrained(args.merged)
    print("đã lưu model gộp ->", args.merged)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", default="sft_train.jsonl")
    p.add_argument("--base", required=True, help="model GỐC fp16 (không phải bản AWQ)")
    p.add_argument("--out", required=True, help="thư mục lưu LoRA adapter")
    p.add_argument("--max-len", type=int, default=8192)
    p.add_argument("--rank", type=int, default=16)
    p.add_argument("--epochs", type=int, default=2)
    p.add_argument("--lr", type=float, default=1e-4)
    p.add_argument("--grad-accum", type=int, default=16)
    p.add_argument("--dry-run", action="store_true", help="chỉ token hóa và in phân bố độ dài, không train")
    p.add_argument("--merge", action="store_true")
    p.add_argument("--merged", help="thư mục lưu model đã gộp (dùng với --merge)")
    args = p.parse_args()
    merge(args) if args.merge else train(args)


if __name__ == "__main__":
    main()
