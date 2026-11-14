"""Cài đặt và dựng dữ liệu tạm của điểm vào container (không cần GPU, không nạp model)."""
import json

from app.container_predict import ContainerPredictor, ContainerSettings


def make_data(root, businesses=("b1", "b2"), n=4):
    (root / "data").mkdir(parents=True)
    for business in businesses:
        (root / "data" / business).mkdir()
        (root / "data" / business / "README.md").write_text("doc", encoding="utf-8")
    (root / "rulebook.md").write_text("rules", encoding="utf-8")
    (root / "calendar.csv").write_text("date\n", encoding="utf-8")
    questions = [{"id": f"q{i}", "business_id": businesses[i % len(businesses)], "question": "?", "as_of_date": "2026-08-27"} for i in range(n)]
    (root / "questions.jsonl").write_text("".join(json.dumps(q) + "\n" for q in questions), encoding="utf-8")
    return questions


def test_defaults_match_the_documented_fixed_values(monkeypatch):
    for name in ("DATA_DIR", "RESULT_DIR", "TOTAL_BUDGET_S", "N_SAMPLES", "LIMIT_QUESTIONS", "SEED"):
        monkeypatch.delenv(name, raising=False)
    s = ContainerSettings.from_env()
    assert (s.data_dir, s.result_dir, s.n_samples, s.agree_min, s.seed) == ("/data", "/result", 3, 2, 0)
    assert s.total_budget_s == 19800 and s.prepare_budget_s == 7200 and s.limit_questions is None


def test_env_overrides_are_applied(monkeypatch):
    monkeypatch.setenv("LIMIT_QUESTIONS", "10")
    monkeypatch.setenv("TOTAL_BUDGET_S", "100")
    s = ContainerSettings.from_env()
    assert s.limit_questions == 10 and s.total_budget_s == 100.0


def test_read_questions_respects_limit(tmp_path):
    make_data(tmp_path / "in", n=6)
    predictor = ContainerPredictor(ContainerSettings(data_dir=str(tmp_path / "in"), limit_questions=2))
    assert [q["id"] for q in predictor.read_questions()] == ["q0", "q1"]


def test_stage_dataset_copies_only_needed_businesses_into_a_writable_tree(tmp_path):
    questions = make_data(tmp_path / "in", businesses=("b1", "b2", "b3"), n=2)           # q0 -> b1, q1 -> b2
    predictor = ContainerPredictor(ContainerSettings(data_dir=str(tmp_path / "in"), work_dir=str(tmp_path / "work")))
    root = predictor.stage_dataset(questions)
    assert sorted(p.name for p in (root / "data").iterdir()) == ["b1", "b2"]
    assert not (root / "data" / "b1").is_symlink()                                      # bản chép thật, không phải liên kết
    assert (root / "rulebook.md").read_text(encoding="utf-8") == "rules"
    assert len((root / "questions.jsonl").read_text(encoding="utf-8").splitlines()) == 2
