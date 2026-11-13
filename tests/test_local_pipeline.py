"""Full integration with a scripted local transport; real ingestion and DuckDB execution."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

from app.config import Config
from app.local_pipeline import LocalPipelineConfig, LocalSQLPipeline
from app.llm.base import GenerationError
from app.llm.local_service import LocalLLMService
from app.manifests.service import BusinessManifestService
from evaluation.fakes import write_split


class LocalTransport:
    def __init__(self, manifest):
        self.manifest = manifest
        self.calls = []

    def tag(self):
        return "scripted-local"

    def count_tokens(self, text):
        return len(text) // 4

    def prompt_tokens(self, system, prompt):
        return self.count_tokens(system + prompt) + 10

    def generate_with_options(self, system, prompt, **options):
        self.calls.append((system, prompt, options))
        request = json.loads(prompt)
        if "target_source" in request:
            source = request["target_source"]
            result = deepcopy(self.manifest)
            result["tables"] = [t for t in result["tables"] if
                                (t["source_file"], t["source_object"]) == (source["source_file"], source["source_object"])]
            result["tables"][0]["columns"] = [c for c in result["tables"][0]["columns"]
                                               if c["source_name"] in {v["source_name"] for v in source["columns"]}]
        elif "protected_terms" in request:
            result = {"question": request["question"]}
        else:
            result = {"type": "number", "sql": "SELECT sum(thanh_tien) FROM sales WHERE trang_thai != 'huy'",
                      "reason": "Loại đơn hủy"}
        return [json.dumps(result, ensure_ascii=False)]


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    # Even an accidental import of the remote client is a failure in this path.
    monkeypatch.setitem(sys.modules, "app.llm.gemini", None)
    for split in ("train", "public_test"):
        root = tmp_path / "dataset" / split
        write_split(root, n_questions=2, with_answers=split == "train")
        (root / "calendar.csv").write_text("date,lunar_day,lunar_month,lunar_year,lunar_leap,holiday\n2026-08-27,15,7,2026,False,\n")
        (root / "data/b1/products.csv").write_text("id,name\n1,Coffee\n")
    train = tmp_path / "dataset/train"
    manifest = BusinessManifestService.build_draft(train / "data/b1", (train / "rulebook.md").read_text())
    transport = LocalTransport(manifest)
    cfg = LocalPipelineConfig(dataset_dir=str(tmp_path / "dataset"), out_dir=str(tmp_path / "out"),
                              n_samples=1, agree_min=1)
    model_cfg = Config(max_new_tokens=cfg.sql_tokens, max_model_len=cfg.max_model_len)
    service = LocalLLMService(transport, model_cfg, manifest_tokens=cfg.manifest_tokens)
    return LocalSQLPipeline(cfg, llm=service), transport


def test_raw_data_to_manifests_duckdb_evaluation_and_submission(pipeline):
    runner, transport = pipeline
    submission = runner.run_all()
    rows = [json.loads(line) for line in submission.read_text().splitlines()]
    assert len(rows) == 2 and all(row["answer"] == 130000 for row in rows)
    report = json.loads((Path(runner.config.out_dir) / "train/summary.json").read_text())
    assert report["correct"] == 2 and report["query_engine"] == "sql"
    for split in ("train", "public_test"):
        assert not (Path(runner.config.dataset_dir) / split / "manifests").exists()
        database = Path(runner.config.out_dir) / "databases" / split / "b1/current/data.duckdb"
        assert database.is_file()
    manifest_calls = [c for c in transport.calls if "target_source" in json.loads(c[1])]
    assert len(manifest_calls) == 4  # two independent objects in each split
    assert any('"protected_terms"' in c[1] for c in transport.calls)
    before = len(transport.calls)
    reports = runner.prepare_datasets()
    assert all(row["reused"] for rows in reports.values() for row in rows)
    runner.evaluate()
    runner.predict_public_test()
    assert len(transport.calls) == before
    assert all('"answers"' not in prompt for _, prompt, _ in transport.calls)


def test_agent_requires_preparation_and_detects_source_changes(pipeline):
    runner, _ = pipeline
    with pytest.raises(RuntimeError, match="Missing prepared"):
        runner.evaluate()
    runner.prepare_datasets()
    path = Path(runner.config.dataset_dir) / "train/data/b1/sales.csv"
    path.write_text(path.read_text().replace("90000", "90001"))
    with pytest.raises(RuntimeError, match="Stale database"):
        runner.evaluate()


def test_local_json_retry_and_rewrite_guard():
    cfg = Config(max_retry=1)
    transport = LocalTransport({})
    replies = iter(['not json', '{"question":"Mua 13 sản phẩm"}'])
    transport.generate_with_options = lambda *a, **kw: [next(replies)]
    service = LocalLLMService(transport, cfg)
    with pytest.raises(GenerationError, match="changed numbers"):
        service.rewrite_question("Mua 12 sản phẩm")


def test_context_overflow_stops_before_generation():
    transport = LocalTransport({})
    service = LocalLLMService(transport, Config(max_model_len=50))
    with pytest.raises(GenerationError, match="context budget"):
        service.generate_json("system", "long prompt" * 30, {"type": "object"})
    assert not transport.calls


def test_manifest_batches_cover_wide_source_without_duplicate_tables(tmp_path):
    from app.manifests.local import LocalManifestGenerator
    business = tmp_path / "data" / "wide"
    business.mkdir(parents=True)
    (business / "README.md").write_text("Wide source")
    (business / "wide.csv").write_text(",".join(f"c{i}" for i in range(25)) + "\n" + ",".join("1" for _ in range(25)) + "\n")
    transport = LocalTransport(BusinessManifestService.build_draft(business))
    service = LocalLLMService(transport, Config())
    path = LocalManifestGenerator(service).generate(business, "", tmp_path / "manifests")
    result = json.loads(path.read_text())
    assert len(result["tables"]) == 1
    assert len(result["tables"][0]["columns"]) == 25
    assert len(transport.calls) == 3
