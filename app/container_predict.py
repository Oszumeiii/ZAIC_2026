"""Điểm vào lúc chấm bài trong Docker: `bash /code/predict.sh` -> `python -m app.container_predict`.

Đọc /data (chỉ-đọc), ghi /result/submission.jsonl và /result/logs/. Chạy hoàn toàn ngoại tuyến.

Thứ tự an toàn: file nộp đầy đủ abstain được ghi NGAY; sau đó pipeline Qwen cục bộ (manifest -> DuckDB -> SQL) ghi từng
câu khi có đáp án. Hết ngân sách thời gian, lỗi hoặc bị SIGTERM thì những câu đã ghi vẫn được chấm, phần còn lại là abstain.
Mọi tham số có giá trị mặc định cố định ở đây và trong predict.sh (ban tổ chức không đổi được).
"""
from __future__ import annotations

import json
import os
import random
import shutil
import signal
import sys
import time
import traceback
from dataclasses import dataclass, replace
from pathlib import Path

from app.submission_io import IncrementalSubmission


@dataclass
class ContainerSettings:
    data_dir: str = "/data"
    result_dir: str = "/result"
    work_dir: str = "/tmp/zaic_work"                 # nơi ghi tạm (DuckDB, manifest); /data là chỉ-đọc
    model_dir: str = "/code/models/qwen3.5-9b"
    lora_dir: str = "/code/models/lora"              # nếu có adapter_config.json ở đây thì gắn LoRA
    total_budget_s: float = 19800.0                  # 5 giờ 30: chừa 30 phút của giới hạn 6 giờ (đã gồm nạp model)
    prepare_budget_s: float = 7200.0                 # tối đa cho bước Qwen sinh manifest; quá hạn thì dùng manifest nháp tất định
    margin_s: float = 300.0                          # chừa để ghi nốt
    n_samples: int = 3
    agree_min: int = 2
    temperature: float = 0.7
    seed: int = 0
    limit_questions: int | None = None               # chỉ để chạy thử nhanh (smoke); ban tổ chức không đặt

    @classmethod
    def from_env(cls) -> "ContainerSettings":
        base = cls()
        env = os.environ
        limit = env.get("LIMIT_QUESTIONS")
        return cls(data_dir=env.get("DATA_DIR", base.data_dir), result_dir=env.get("RESULT_DIR", base.result_dir),
                   work_dir=env.get("WORK_DIR", base.work_dir), model_dir=env.get("MODEL_DIR", base.model_dir),
                   lora_dir=env.get("LORA_DIR", base.lora_dir),
                   total_budget_s=float(env.get("TOTAL_BUDGET_S", base.total_budget_s)),
                   prepare_budget_s=float(env.get("PREPARE_BUDGET_S", base.prepare_budget_s)),
                   margin_s=float(env.get("MARGIN_S", base.margin_s)),
                   n_samples=int(env.get("N_SAMPLES", base.n_samples)), agree_min=int(env.get("AGREE_MIN", base.agree_min)),
                   temperature=float(env.get("TEMPERATURE", base.temperature)), seed=int(env.get("SEED", base.seed)),
                   limit_questions=int(limit) if limit else None)


class ContainerPredictor:
    def __init__(self, settings: ContainerSettings):
        self.s = settings
        self.start = time.monotonic()

    def elapsed(self) -> float:
        return time.monotonic() - self.start

    def say(self, message: str) -> None:
        print(f"[container {self.elapsed():7.0f}s] {message}", flush=True)

    def read_questions(self) -> list[dict]:
        path = Path(self.s.data_dir) / "questions.jsonl"
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
        return rows[: self.s.limit_questions] if self.s.limit_questions else rows

    def stage_dataset(self, questions: list[dict]) -> Path:
        """Chép dữ liệu sang thư mục ghi được (pipeline tạo manifest/cache cạnh dữ liệu và kiểm tra đường dẫn business
        nên liên kết tượng trưng tới /data không dùng được). Chỉ chép business có câu hỏi."""
        src, root = Path(self.s.data_dir), Path(self.s.work_dir) / "dataset" / "public_test"
        if root.exists():
            shutil.rmtree(root)
        (root / "data").mkdir(parents=True)
        for name in ("rulebook.md", "calendar.csv"):
            shutil.copy2(src / name, root / name)
        for business in sorted({q["business_id"] for q in questions}):
            shutil.copytree(src / "data" / business, root / "data" / business)
        with (root / "questions.jsonl").open("w", encoding="utf-8") as stream:
            stream.writelines(json.dumps(q, ensure_ascii=False) + "\n" for q in questions)
        return root

    def solve(self, writer: IncrementalSubmission, questions: list[dict]) -> None:
        # Import nặng đặt ở đây để file nộp abstain được ghi trước khi nạp thư viện/model.
        import app.agent.solver as solver_module
        from app.agent.sql_solver import SQLSolver
        from app.ingestion.datasets.loader import DatasetLoader
        from app.local_pipeline import DatasetPreparationPipeline, LocalPipelineConfig, LocalSQLPipeline

        solver_module.MAX_FAIL_STREAK = 10 ** 9       # một chuỗi câu lỗi không được dừng cả lượt chạy: câu lỗi chỉ là abstain
        self.say(f"{len(questions)} câu; dựng dữ liệu tạm ở {self.s.work_dir}")
        self.stage_dataset(questions)
        work = Path(self.s.work_dir)
        config = LocalPipelineConfig(dataset_dir=str(work / "dataset"), out_dir=str(work / "out"), model=self.s.model_dir,
                                     n_samples=self.s.n_samples, agree_min=self.s.agree_min)
        pipeline = LocalSQLPipeline(config)
        lora = Path(self.s.lora_dir)
        pipeline.base = replace(pipeline.base, temperature=self.s.temperature, seed=self.s.seed,
                                local_lora=str(lora) if (lora / "adapter_config.json").is_file() else None)
        self.say(f"nạp model {self.s.model_dir} (LoRA: {pipeline.base.local_lora})")
        llm = pipeline.llm                              # nạp model một lần, dùng cho manifest, viết lại câu hỏi và SQL
        self.say("đã nạp model; sinh manifest bằng Qwen")

        prepared = False
        try:
            deadline = self.start + self.s.prepare_budget_s
            reports = DatasetPreparationPipeline(config, llm).prepare("public_test", deadline=deadline, keep_going=True)
            prepared = all(report["status"] == "ready" for report in reports)
            self.say("manifest: " + ", ".join(f"{r['business_id']}={r['status']}" for r in reports))
        except Exception:
            self.say("bước manifest lỗi, dùng manifest nháp tất định cho business chưa xong:\n" + traceback.format_exc())
        # Business nào chưa có manifest/DuckDB hợp lệ sẽ được dựng ở bước SQL bằng manifest nháp (không cần model).
        cfg = replace(pipeline.split_config("public_test"), require_prepared_database=prepared)

        split = DatasetLoader.load_split(cfg.test_dir)
        solver = SQLSolver(cfg, llm)
        remaining = self.s.total_budget_s - self.s.margin_s - self.elapsed()
        self.say(f"giải câu hỏi, còn {remaining:.0f}s trong ngân sách")
        solver.solve_split(split, "test", time_budget=max(1.0, remaining), on_result=writer.add)

    def run(self) -> int:
        random.seed(self.s.seed)
        Path(self.s.result_dir).mkdir(parents=True, exist_ok=True)
        questions = self.read_questions()
        writer = IncrementalSubmission(self.s.result_dir, [q["id"] for q in questions])
        self.say(f"đã ghi file nộp abstain cho {len(writer.ids)} câu; bắt đầu")

        def on_term(signum, frame):                      # `docker stop` / hết giờ: ghi nốt rồi thoát
            raise SystemExit(143)
        signal.signal(signal.SIGTERM, on_term)
        status = 0
        try:
            self.solve(writer, questions)
        except SystemExit:
            self.say("bị dừng bởi tín hiệu, ghi nốt kết quả đã có")
            status = 143
        except BaseException:
            self.say("LỖI không xử lý được, giữ kết quả đã ghi:\n" + traceback.format_exc())
            status = 1
        finally:
            missing = writer.finalize("not processed (time budget, error or stop)")
            self.say(f"xong: {len(writer.ids) - missing} câu đã có kết quả, {missing} câu abstain")
        return status

    @staticmethod
    def main() -> int:
        return ContainerPredictor(ContainerSettings.from_env()).run()


if __name__ == "__main__":
    sys.exit(ContainerPredictor.main())
