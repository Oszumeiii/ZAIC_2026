"""Tiện ích chung cho backend local (tải model, đọc cấu hình lượng tử hóa)."""
from __future__ import annotations

import importlib.util
import json
import os


def has_module(*mods: str) -> bool:
    for m in mods:
        try:
            if importlib.util.find_spec(m):
                return True
        except (ImportError, ValueError):
            pass
    return False


def resolve_model_path(name: str) -> str:
    """Đường dẫn thư mục thì dùng luôn; tên repo HF thì tải về /tmp/models (cần Internet)."""
    if os.path.isdir(name):
        return name
    from huggingface_hub import snapshot_download
    dst = "./models/" + name.replace("/", "__")
    print(f"Tải {name} về {dst} (cần bật Internet trong Settings của notebook) ...")
    return snapshot_download(name, local_dir=dst)


def read_quant_config(path: str) -> dict:
    try:
        return json.load(open(os.path.join(path, "config.json"))).get("quantization_config", {}) or {}
    except Exception:
        return {}


def is_prequantized(path: str) -> bool:
    return bool(read_quant_config(path))


def quant_method(path: str) -> str:
    return str(read_quant_config(path).get("quant_method", "")).lower()
