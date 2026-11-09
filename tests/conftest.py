"""Dữ liệu giả + backend giả dùng chung cho các test (không cần GPU hay API)."""
import json
import os

import pandas as pd
import pytest

from app.config import Config
from app.llm.base import GenerationError, LLMBackend

SALES = pd.DataFrame({
    "ngay": ["2026-08-24", "2026-08-25", "2026-08-26", "2026-08-27"],
    "ten_hang": ["Ca phe", "Tra", "Ca phe", "Tra"],
    "so_luong": [3, 1, 2, 1],
    "thanh_tien": [90000, 20000, 60000, 20000],
    "trang_thai": ["hoan thanh", "hoan thanh", "huy", "hoan thanh"],
})

GOOD_CODE = ('```python\ndf = tables["sales"].copy()\ndf = df[df["trang_thai"] != "huy"]\n'
             'result_type = "number"\nresult = float(df["thanh_tien"].sum())\n```')   # = 130000
OTHER_CODE = '```python\nresult_type = "number"\nresult = 1.0\n```'


class FakeBackend(LLMBackend):
    """Trả lần lượt các văn bản đã chuẩn bị (lặp lại phần tử cuối khi hết)."""

    def __init__(self, replies, batch=True, fail=False):
        self.replies, self.supports_batch, self.fail, self.calls = list(replies), batch, fail, 0

    def tag(self):
        return "fake"

    def generate(self, system, prompt, n=1):
        self.calls += 1
        if self.fail:
            raise GenerationError("hỏng giả lập")
        return [self.replies[min(self.calls - 1 + i, len(self.replies) - 1)] for i in range(n)]


def write_split(root, n_questions=4, with_answers=True):
    os.makedirs(f"{root}/data/b1", exist_ok=True)
    SALES.to_csv(f"{root}/data/b1/sales.csv", index=False)
    open(f"{root}/data/b1/README.md", "w", encoding="utf-8").write("Cửa hàng cà phê.")
    open(f"{root}/rulebook.md", "w", encoding="utf-8").write("Loại đơn hủy. Làm tròn 2 chữ số.")
    qs = [{"id": f"q{i}", "business_id": "b1", "question": "Tổng doanh thu?", "as_of_date": "2026-08-27"}
          for i in range(n_questions)]
    with open(f"{root}/questions.jsonl", "w", encoding="utf-8") as f:
        f.writelines(json.dumps(q, ensure_ascii=False) + "\n" for q in qs)
    if with_answers:
        with open(f"{root}/answers.jsonl", "w", encoding="utf-8") as f:
            f.writelines(json.dumps({"id": q["id"], "type": "number", "answer": 130000}) + "\n" for q in qs)


@pytest.fixture
def split_dirs(tmp_path):
    write_split(tmp_path / "train")
    write_split(tmp_path / "test", with_answers=False)
    return str(tmp_path / "train"), str(tmp_path / "test"), str(tmp_path / "out")


@pytest.fixture
def cfg(split_dirs):
    train, test, out = split_dirs
    return Config(train_dir=train, test_dir=test, out_dir=out, backend="gemini", query_engine="pandas", max_biz=None,
                  n_samples=3, agree_min=2)
