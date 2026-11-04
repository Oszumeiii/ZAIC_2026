"""Chọn model/engine theo GPU đang có (Colab: T4 / L4 / A100...)."""
from __future__ import annotations

from dataclasses import replace

from app.config import Config

MODEL_14B = "Qwen/Qwen2.5-Coder-14B-Instruct"          # ~14,7B tham số: dưới trần 15B (cần kiểm cách tính của ban tổ chức)
MODEL_14B_AWQ = "Qwen/Qwen2.5-Coder-14B-Instruct-AWQ"  # bản nén 4-bit cho GPU 24GB; CHƯA xác nhận tên repo còn tồn tại
MODEL_7B = "Qwen/Qwen2.5-Coder-7B-Instruct"


def detect_gpu() -> dict:
    """Tên GPU, VRAM (GB) và hỗ trợ bf16 của GPU 0; không có GPU thì vram_gb = 0."""
    try:
        import torch
        if not torch.cuda.is_available():
            return {"name": "none", "vram_gb": 0.0, "bf16": False, "count": 0}
        props = torch.cuda.get_device_properties(0)
        return {"name": props.name, "vram_gb": round(props.total_memory / 1e9, 1),
                "bf16": bool(torch.cuda.is_bf16_supported()), "count": torch.cuda.device_count()}
    except ImportError:
        return {"name": "none", "vram_gb": 0.0, "bf16": False, "count": 0}


def recommend(vram_gb: float) -> dict:
    """Cấu hình model theo VRAM của MỘT GPU. Ngưỡng dựa trên ước tính kích thước trọng số, chưa đo trên máy thật."""
    if vram_gb >= 38:       # A100 40/80GB: 14B bf16 (~29GB) + KV cache
        return {"local_model": MODEL_14B, "local_engine": "vllm", "max_model_len": 16384, "gpu_memory_utilization": 0.90}
    if vram_gb >= 22:       # L4 / A10 24GB: 14B nén 4-bit (~9,5GB)
        return {"local_model": MODEL_14B_AWQ, "local_engine": "vllm", "max_model_len": 16384, "gpu_memory_utilization": 0.90}
    return {"local_model": MODEL_7B, "local_engine": "hf", "max_model_len": 16384, "gpu_memory_utilization": 0.88}


def apply_recommendation(cfg: Config, gpu: dict | None = None) -> Config:
    """Trả Config mới với model/engine theo GPU (các tham số khác giữ nguyên)."""
    gpu = gpu or detect_gpu()
    return replace(cfg, **recommend(gpu["vram_gb"]))
