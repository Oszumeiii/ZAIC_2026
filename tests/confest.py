"""Fixture dùng chung. Dữ liệu giả và backend giả nằm ở datamate_testkit.py (xem lý do ở đó)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))     # để `import datamate_testkit` chạy ở mọi chế độ import

import pytest  # noqa: E402

from app.config import Config  # noqa: E402
from datamate_testkit import write_split  # noqa: E402


@pytest.fixture
def split_dirs(tmp_path):
    write_split(tmp_path / "train")
    write_split(tmp_path / "test", with_answers=False)
    return str(tmp_path / "train"), str(tmp_path / "test"), str(tmp_path / "out")


@pytest.fixture
def cfg(split_dirs):
    train, test, out = split_dirs
    return Config(train_dir=train, test_dir=test, out_dir=out, backend="gemini", max_biz=None,
                  n_samples=3, agree_min=2)
