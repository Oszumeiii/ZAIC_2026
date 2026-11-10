"""Test dòng lệnh điểm vào."""
import argparse

from app.main import add_common_args, config_from_args


def test_cli_parses_config():
    p = add_common_args(argparse.ArgumentParser())
    cfg = config_from_args(p.parse_args(["--backend", "gemini", "--max-biz", "2", "--per-biz", "15"]))
    assert (cfg.backend, cfg.max_biz, cfg.per_biz) == ("gemini", 2, 15)


def test_cli_exemplars_and_no_hints():
    p = add_common_args(argparse.ArgumentParser())
    cfg = config_from_args(p.parse_args(["--backend", "gemini", "--exemplars", "bank.jsonl", "--no-hints"]))
    assert cfg.exemplar_path == "bank.jsonl" and cfg.use_hints is False


def test_cli_max_model_len_flag():
    p = add_common_args(argparse.ArgumentParser())
    assert config_from_args(p.parse_args(["--backend", "gemini", "--max-model-len", "12288"])).max_model_len == 12288
