"""Test dòng lệnh điểm vào."""
import argparse
import pytest

from app.main import add_common_args, config_from_args


def test_cli_parses_config():
    p = add_common_args(argparse.ArgumentParser())
    cfg = config_from_args(p.parse_args(["--backend", "local", "--max-biz", "2", "--per-biz", "15"]))
    assert (cfg.backend, cfg.max_biz, cfg.per_biz) == ("local", 2, 15)


def test_cli_exemplars_and_no_hints():
    p = add_common_args(argparse.ArgumentParser())
    cfg = config_from_args(p.parse_args(["--backend", "local", "--exemplars", "bank.jsonl", "--no-hints"]))
    assert cfg.exemplar_path == "bank.jsonl" and cfg.use_hints is False


def test_cli_max_model_len_flag():
    p = add_common_args(argparse.ArgumentParser())
    assert config_from_args(p.parse_args(["--backend", "local", "--max-model-len", "12288"])).max_model_len == 12288


def test_cli_accepts_competition_runtime_paths_and_tensor_parallelism():
    parser = add_common_args(argparse.ArgumentParser())
    cfg = config_from_args(parser.parse_args([
        "--manifest-dir", "/tmp/manifests",
        "--tensor-parallel", "2",
    ]))
    assert cfg.manifest_dir == "/tmp/manifests"
    assert cfg.tensor_parallel == 2


def test_cli_hf_quant_flag():
    p = add_common_args(argparse.ArgumentParser())
    assert config_from_args(p.parse_args(["--backend", "local", "--hf-quant", "none"])).hf_quant == "none"
    assert config_from_args(p.parse_args(["--backend", "local"])).hf_quant == "auto"


def test_cli_rejects_unsupported_backend():
    parser = add_common_args(argparse.ArgumentParser())
    with pytest.raises(SystemExit):
        parser.parse_args(["--backend", "remote-api"])


def test_qwen3_models_turn_thinking_off_by_default():
    p = add_common_args(argparse.ArgumentParser())
    cfg = config_from_args(p.parse_args(["--backend", "local", "--local-model", "Qwen/Qwen3.5-9B"]))
    assert cfg.chat_kwargs == {"enable_thinking": False}


def test_other_models_keep_chat_kwargs_empty():
    p = add_common_args(argparse.ArgumentParser())
    cfg = config_from_args(p.parse_args(["--backend", "local", "--local-model", "Qwen/Qwen2.5-Coder-7B-Instruct"]))
    assert cfg.chat_kwargs == {}
