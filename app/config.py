"""Cấu hình chạy: một dataclass thay cho các biến toàn cục của notebook."""
from __future__ import annotations

from dataclasses import dataclass, field

BACKENDS = ("local", "gemini")
LOCAL_ENGINES = ("auto", "vllm", "hf")


@dataclass
class Config:
    train_dir: str = "/kaggle/input/datasets/trungnguyen0510/train-track2-zaloai/train"
    test_dir: str = "/kaggle/input/datasets/trungnguyen0510/testpublic-track2-zaloai/public_test"
    out_dir: str = "/kaggle/working"

    max_biz: int | None = 3           # số business train để chạy thử; None = tất cả
    per_biz: int | None = None        # tối đa ngần này câu mỗi business; None = hết
    limit_test: int | None = None     # số câu public test tối đa; None = tất cả
    time_budget_s: float | None = None  # hết giờ thì dừng gọi model, câu còn lại ghi abstain

    use_rules: bool = False
    rule_on: dict = field(default_factory=lambda: {"top": True, "low": True, "revenue": True})

    n_samples: int = 3                # số lần sinh độc lập tối đa cho một câu
    agree_min: int = 2                # cần ít nhất ngần này lần ra CÙNG kết quả mới trả lời
    max_retry: int = 2                # số lần tự sửa lỗi cho mỗi lời giải
    code_timeout_s: int = 60          # giới hạn thời gian chạy một đoạn code
    prompt_version: str = "v5"        # đổi khi sửa prompt để vô hiệu cache cũ

    backend: str = "local"            # "local" | "gemini"
    gemini_model: str = "gemini-3.5-flash"
    thinking: str = "low"             # thinking_level của Gemini
    local_model: str = "Qwen/Qwen2.5-Coder-7B-Instruct-AWQ"   # repo HF hoặc đường dẫn thư mục
    local_lora: str | None = None     # thư mục LoRA adapter sau khi fine-tune
    lora_rank: int = 64
    local_engine: str = "auto"        # "auto" | "vllm" | "hf"
    hf_base_model: str | None = None  # model gốc fp16 khi engine hf gặp bản AWQ/GPTQ
    tensor_parallel: int = 1
    vllm_extra: dict = field(default_factory=lambda: {"enforce_eager": True})
    vllm_env: dict = field(default_factory=lambda: {"VLLM_ENABLE_V1_MULTIPROCESSING": "0"})
    max_model_len: int = 16384        # tổng token tối đa (prompt + trả lời)
    max_new_tokens: int = 1024
    prefill_chunk: int = 512          # engine hf: nạp prompt từng đoạn để khỏi OOM (0 = tắt)
    gpu_memory_utilization: float = 0.88
    temperature: float = 0.7          # phải > 0 để các lần sinh độc lập khác nhau
    chat_kwargs: dict = field(default_factory=dict)
    allow_cpu: bool = False           # False = backend local không có GPU thì dừng ngay

    def __post_init__(self):
        if self.backend not in BACKENDS:
            raise ValueError(f"backend phải thuộc {BACKENDS}, nhận {self.backend!r}")
        if self.local_engine not in LOCAL_ENGINES:
            raise ValueError(f"local_engine phải thuộc {LOCAL_ENGINES}, nhận {self.local_engine!r}")
        if not 1 <= self.agree_min <= self.n_samples:
            raise ValueError("cần 1 <= agree_min <= n_samples")
        if self.n_samples > 1 and self.temperature <= 0:
            raise ValueError("n_samples > 1 cần temperature > 0, nếu không các lần sinh giống hệt nhau")

    @property
    def is_local(self) -> bool:
        return self.backend == "local"

    @property
    def context_limits(self) -> tuple[int, int, int]:
        """(rulebook, readme, profile) tính bằng ký tự. Model local có ngữ cảnh nhỏ hơn Gemini nhiều."""
        return (16000, 4000, 16000) if self.is_local else (30000, 15000, 40000)

    @property
    def token_budget(self) -> int | None:
        """Ngân sách token cho phần ngữ cảnh (chỉ local); 800 token dự phòng cho system + lần retry."""
        return self.max_model_len - self.max_new_tokens - 800 if self.is_local else None
