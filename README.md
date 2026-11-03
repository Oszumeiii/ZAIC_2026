# datamate-agent

Agent trả lời câu hỏi tiếng Việt về dữ liệu bảng (CSV/XLSX/SQLite) cho **Zalo AI Challenge 2026, DataMate (Track 2)**.
Chấm theo Penalized Accuracy: đúng +1, abstain 0, sai −1. Vì vậy hệ thống **chỉ trả lời khi chắc**, không thì abstain.

## Cách hoạt động

```
câu hỏi + as_of_date
  └─ dựng ngữ cảnh: RULEBOOK + README + profile cột (tự rút gọn cho vừa token)
      └─ LLM sinh code pandas ×N lần độc lập (N = n_samples)
          └─ chạy code (giới hạn thời gian) → ép/kiểm kiểu kết quả theo `type`
              └─ lỗi hoặc sai kiểu: đưa lỗi lại cho model tự sửa (tối đa max_retry lần)
                  └─ bỏ phiếu: ≥ agree_min lần cho CÙNG kết quả mới trả lời, không thì abstain
```

LLM **không** sinh SQL và **không** được huấn luyện ở đây: nó viết code pandas theo prompt (zero-shot).
"train" nghĩa là chạy pipeline trên tập `train` (có đáp án) để đo PA.

## Cấu trúc

```
app/                  chỉ mã chạy lúc nộp bài (Docker chỉ copy thư mục này)
  config.py           Config: mọi tham số chạy
  main.py             điểm vào: python -m app.main (đọc TEST_DIR, ghi OUT_DIR/submission.jsonl)
  submission.py       ghi và kiểm định submission.jsonl (đủ id, JSON chuẩn, < 5MB)
  text.py             chuẩn hóa tiếng Việt dùng chung
  ingestion/          dataset.py (đọc split, sửa tiêu đề lệch dòng), profiling.py (mô tả cột, gộp sheet)
  llm/                base.py + gemini.py, hf_local.py, vllm_local.py (create_backend chọn theo cấu hình)
  agent/              prompts.py, context.py (rút gọn theo ngân sách token), solver.py (sinh ×N, tự sửa,
                      bỏ phiếu), cache.py, pipeline.py (run_inference)
  tools/              sandbox.py, results.py (clean/coerce/canon), timeutil.py, rules.py
evaluation/           chỉ dùng khi phát triển: scoring.py (PA), run_eval.py (chạy + chấm trên train)
training/             chỉ dùng khi phát triển: export_sft.py (SFT từ lời giải đã đúng)
notebooks/            kaggle_run.ipynb (mỏng, chỉ cấu hình rồi gọi app)
tests/                phản chiếu cấu trúc trên; 26 test chạy bằng backend giả, không cần GPU hay API
Dockerfile, requirements.txt (runtime), requirements-dev.txt
```

Hướng phụ thuộc một chiều: `evaluation`/`training` gọi `app`, không ngược lại. `agent` gọi `llm`, `tools`,
`ingestion`. `tools` và `ingestion` không biết LLM là gì. `torch`/`vllm` chỉ import muộn trong `llm/`.

## Chạy

```bash
pip install -r requirements-dev.txt
pytest                                                     # không cần GPU/API
python -m evaluation.run_eval --backend gemini --max-biz 2 --per-biz 15      # chạy + chấm PA trên train
python -m app.main --backend gemini --test-dir <dir> --out-dir <dir> --limit-test 20
```

Gemini cần biến môi trường `GEMINI_API_KEY` (hoặc Kaggle Secret). Backend local: engine `auto` (có vLLM thì dùng,
lỗi hoặc không có thì chuyển sang transformers + bitsandbytes 4-bit).

## Lưu ý

- Vòng nộp cuối: offline, model ≤ 15B, không gọi API ngoài. Backend Gemini chỉ để phát triển/so sánh.
- Chưa kiểm trên GPU/Kaggle thật: `llm/gemini.py`, `llm/hf_local.py`, `llm/vllm_local.py`. Chưa build `Dockerfile`.
- Luật đặt tên: tên tiếng Anh, chú thích tiếng Việt (`python3 tools/check_naming.py tmp/datamate_agent`).
