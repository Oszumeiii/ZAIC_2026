"""Test dòng lệnh điểm vào."""
import argparse

from app.main import add_common_args, config_from_args


def test_cli_parses_config():
    p = add_common_args(argparse.ArgumentParser())
    cfg = config_from_args(p.parse_args(["--backend", "gemini", "--max-biz", "2", "--per-biz", "15"]))
    assert (cfg.backend, cfg.max_biz, cfg.per_biz) == ("gemini", 2, 15)
