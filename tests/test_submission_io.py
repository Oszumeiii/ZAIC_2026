"""Ghi bài nộp và nhật ký theo định dạng của ban tổ chức (chỉ thư viện chuẩn)."""
import json

from app.submission_io import IncrementalSubmission


def read_lines(path):
    return [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]


def test_submission_is_complete_and_abstain_right_after_creation(tmp_path):
    IncrementalSubmission(str(tmp_path), ["q1", "q2", "q3"])
    rows = read_lines(tmp_path / "submission.jsonl")
    assert [r["id"] for r in rows] == ["q1", "q2", "q3"]
    assert all(r == {"id": r["id"], "type": "abstain"} for r in rows)


def test_add_writes_log_immediately_and_flushes_submission_periodically(tmp_path):
    writer = IncrementalSubmission(str(tmp_path), ["q1", "q2", "q3"], flush_every=2)
    writer.add({"id": "q1", "type": "number", "answer": 5}, {"elapsed_s": 1.2344, "sql": "SELECT 5", "answer": 5, "source": "sql"})
    assert read_lines(tmp_path / "logs" / "questions.jsonl")[0]["code"] == "SELECT 5"
    assert read_lines(tmp_path / "submission.jsonl")[0]["type"] == "abstain"       # chưa tới ngưỡng flush
    writer.add({"id": "q2", "type": "string", "answer": "a"}, {"elapsed_s": 0.5})
    rows = read_lines(tmp_path / "submission.jsonl")
    assert rows[0] == {"id": "q1", "type": "number", "answer": 5} and rows[1]["answer"] == "a"
    assert rows[2]["type"] == "abstain"


def test_log_record_has_required_fields_with_integer_milliseconds(tmp_path):
    writer = IncrementalSubmission(str(tmp_path), ["q1"])
    writer.add({"id": "q1", "type": "number", "answer": 48250000}, {"elapsed_s": 8.4206, "sql": "SELECT 1", "answer": 48250000})
    record = read_lines(tmp_path / "logs" / "questions.jsonl")[0]
    assert record["id"] == "q1" and record["answer"] == {"type": "number", "answer": 48250000}
    assert record["elapsed_ms"] == 8421 and isinstance(record["elapsed_ms"], int)
    assert record["thinking"] is None and record["output"] == 48250000


def test_negative_or_bad_elapsed_becomes_non_negative_integer():
    assert IncrementalSubmission.record({"id": "q", "type": "abstain"}, {"elapsed_s": -3})["elapsed_ms"] == 0
    assert IncrementalSubmission.record({"id": "q", "type": "abstain"}, {"elapsed_s": "x"})["elapsed_ms"] == 0
    assert IncrementalSubmission.record({"id": "q", "type": "abstain"}, {})["elapsed_ms"] == 0


def test_duplicate_and_unknown_ids_are_ignored(tmp_path):
    writer = IncrementalSubmission(str(tmp_path), ["q1"], flush_every=1)
    writer.add({"id": "q1", "type": "number", "answer": 1}, {})
    writer.add({"id": "q1", "type": "number", "answer": 2}, {})
    writer.add({"id": "zzz", "type": "number", "answer": 3}, {})
    assert len(read_lines(tmp_path / "logs" / "questions.jsonl")) == 1
    assert read_lines(tmp_path / "submission.jsonl")[0]["answer"] == 1


def test_finalize_logs_abstain_for_unprocessed_questions_and_leaves_no_temp_file(tmp_path):
    writer = IncrementalSubmission(str(tmp_path), ["q1", "q2"])
    writer.add({"id": "q1", "type": "number", "answer": 1}, {"elapsed_s": 1})
    assert writer.finalize() == 1
    logs = read_lines(tmp_path / "logs" / "questions.jsonl")
    assert [r["id"] for r in logs] == ["q1", "q2"]
    assert logs[1]["answer"] == {"type": "abstain"} and logs[1]["elapsed_ms"] == 0 and "thinking" in logs[1]
    assert not (tmp_path / "submission.jsonl.tmp").exists()


def test_non_finite_numbers_never_break_the_file(tmp_path):
    writer = IncrementalSubmission(str(tmp_path), ["q1"], flush_every=1)
    writer.add({"id": "q1", "type": "number", "answer": float("nan")}, {})
    assert read_lines(tmp_path / "submission.jsonl")[0] == {"id": "q1", "type": "abstain"}
