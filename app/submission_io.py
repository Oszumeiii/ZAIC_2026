"""Ghi bài nộp và nhật ký từng câu theo định dạng của ban tổ chức (Docker guideline mục 3.2 và 3.3).

Chỉ dùng thư viện chuẩn để kiểm thử riêng được. Nguyên tắc: có đáp án nào là ghi ngay; lỡ bị dừng giữa chừng
thì những câu đã ghi vẫn được chấm, câu còn lại là abstain.
"""
from __future__ import annotations

import json
import os


class IncrementalSubmission:
    """Giữ đáp án trong bộ nhớ và ghi lại `submission.jsonl` nguyên tử sau mỗi `flush_every` câu.

    Ngay khi tạo đối tượng, file nộp đã đầy đủ mọi id với abstain (hợp lệ nếu chạy chết sớm).
    Mỗi câu một dòng ở `logs/questions.jsonl` với id, answer, elapsed_ms, thinking, code, output.
    """

    def __init__(self, result_dir: str, ids, flush_every: int = 5):
        self.ids = list(dict.fromkeys(ids))               # giữ thứ tự file câu hỏi, bỏ id trùng
        self.rows: dict = {}
        self.logged: set = set()
        self.flush_every = max(1, flush_every)
        self.path = os.path.join(result_dir, "submission.jsonl")
        self.log_dir = os.path.join(result_dir, "logs")
        os.makedirs(self.log_dir, exist_ok=True)
        self.log_path = os.path.join(self.log_dir, "questions.jsonl")
        self._log = open(self.log_path, "a", encoding="utf-8")
        self._write_submission()

    @staticmethod
    def dump_line(obj: dict) -> str:
        """Một dòng JSON chuẩn (cấm NaN/Infinity). Không ghi được thì câu đó thành abstain thay vì hỏng cả file."""
        try:
            return json.dumps(obj, ensure_ascii=False, default=str, allow_nan=False)
        except ValueError:
            return json.dumps({"id": obj.get("id"), "type": "abstain"}, ensure_ascii=False)

    def _write_submission(self) -> None:
        rows = [self.rows.get(i) or {"id": i, "type": "abstain"} for i in self.ids]
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as stream:
            for row in rows:
                stream.write(self.dump_line(row) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, self.path)                        # đổi tên nguyên tử: người đọc không bao giờ thấy file dở

    @staticmethod
    def record(sub: dict, log: dict) -> dict:
        """Bản ghi nhật ký của một câu. `thinking` là null vì tắt chế độ suy nghĩ; `code` là SQL đã chạy (nếu có)."""
        answer = {key: value for key, value in sub.items() if key != "id"}
        try:
            elapsed_ms = max(0, int(round(float(log.get("elapsed_s") or 0.0) * 1000)))
        except (TypeError, ValueError):
            elapsed_ms = 0
        return {"id": sub["id"], "answer": answer, "elapsed_ms": elapsed_ms, "thinking": log.get("thinking"),
                "code": log.get("sql") or None, "output": log.get("answer"), "source": log.get("source"),
                "agree": log.get("agree"), "intent": log.get("intent"), "note": str(log.get("note") or "")[:300],
                "rewritten_question": log.get("rewritten_question")}

    def _append_log(self, record: dict) -> None:
        self._log.write(self.dump_line(record) + "\n")
        self._log.flush()
        self.logged.add(record["id"])

    def add(self, sub: dict, log: dict) -> None:
        """Gọi sau mỗi câu: nhớ đáp án, ghi nhật ký ngay, và cứ `flush_every` câu thì ghi lại file nộp."""
        if sub["id"] not in self.ids or sub["id"] in self.logged:
            return
        self.rows[sub["id"]] = sub
        self._append_log(self.record(sub, log))
        if len(self.logged) % self.flush_every == 0:
            self._write_submission()

    def finalize(self, note: str = "not processed") -> int:
        """Ghi nốt: câu chưa có bản ghi nhật ký được ghi abstain với elapsed_ms 0. Trả số câu chưa được xử lý."""
        missing = [i for i in self.ids if i not in self.logged]
        for qid in missing:
            self._append_log({"id": qid, "answer": {"type": "abstain"}, "elapsed_ms": 0, "thinking": None,
                              "code": None, "output": None, "source": "abstain", "note": note})
        self._write_submission()
        self._log.close()
        return len(missing)
