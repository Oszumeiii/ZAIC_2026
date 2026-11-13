# datamate-agent

Agent hỏi đáp dữ liệu bảng bằng tiếng Việt, phát triển cho **Zalo AI Challenge 2026, nhiệm vụ DataMate (Track 2)**.

Chạy toàn bộ pipeline SQL trên Colab Pro bằng Qwen local: xem
[notebook](notebooks/colab_pro.ipynb) và [hướng dẫn](docs/local-colab-pipeline.md).
Luồng này tạo manifest, dựng DuckDB cho cả train/public test trước agent, rồi dùng
Qwen để rewrite và sinh SQL; không cần Gemini API key.

Với mỗi câu hỏi về dữ liệu của một doanh nghiệp nhỏ (CSV, XLSX, SQLite), hệ thống sinh mã pandas, thực thi và trả về
đáp án đúng định dạng yêu cầu. Cuộc thi chấm theo **Penalized Accuracy (PA)**: trả lời đúng +1 điểm, từ chối trả lời
(abstain) 0 điểm, trả lời sai −1 điểm. Vì vậy hệ thống chỉ trả lời khi đủ độ tin cậy; các trường hợp còn lại được ghi
nhận là abstain.

## Mục lục

1. [Luồng xử lý](#1-luồng-xử-lý)
2. [Cấu trúc thư mục](#2-cấu-trúc-thư-mục)
3. [Cài đặt](#3-cài-đặt)
4. [Sử dụng](#4-sử-dụng)
5. [Cấu hình](#5-cấu-hình)
6. [Kiểm thử](#6-kiểm-thử)
7. [Trạng thái xác minh và giới hạn](#7-trạng-thái-xác-minh-và-giới-hạn)
8. [Quy ước phát triển](#8-quy-ước-phát-triển)

## 1. Luồng xử lý

```
câu hỏi + as_of_date
  └─ dựng ngữ cảnh: rulebook + README của doanh nghiệp + mô tả cột (tự rút gọn theo ngân sách token)
      ├─ gợi ý tất định do mã tính: khoảng thời gian đã quy đổi, giá trị khớp trong dữ liệu (chịu thiếu dấu, lỗi gõ nhẹ)
      └─ (tùy chọn) vài ví dụ tương tự đã được kiểm đúng từ tập train, ở business khác
      └─ mô hình sinh mã pandas, N lần độc lập (N = n_samples)
          └─ thực thi mã (có giới hạn thời gian), kiểm tra và ép kiểu kết quả theo `type`
              └─ nếu lỗi hoặc sai kiểu: phản hồi lỗi cho mô hình để tự sửa (tối đa max_retry lần)
                  └─ biểu quyết: chỉ trả lời khi ít nhất agree_min lần cho cùng kết quả, ngược lại abstain
```

Lưu ý về phương pháp:

- Mô hình viết **mã pandas**, không sinh truy vấn SQL.
- Mô hình **không được huấn luyện** trong repository này. Hệ thống dùng kỹ thuật prompting (zero-shot). Thuật ngữ
  "train" trong tài liệu chỉ việc chạy pipeline trên tập `train` có đáp án để đo PA.
- Cơ chế biểu quyết giúp loại các câu trả lời không ổn định, nhưng **không phát hiện được lỗi hiểu sai lặp lại ở cả N
  lần sinh**. Đây là giới hạn đã biết của thiết kế hiện tại.

## 2. Cấu trúc thư mục

```
app/                    Mã chạy khi nộp bài (image Docker chỉ chứa thư mục này)
  config.py             Config: toàn bộ tham số vận hành
  main.py               Điểm vào: python -m app.main
  submission.py         Ghi và kiểm định submission.jsonl
  text.py               Chuẩn hóa văn bản tiếng Việt dùng chung
  ingestion/            dataset.py (đọc split, sửa tiêu đề lệch dòng), profiling.py (mô tả cột, gộp sheet),
                        splitting.py (chia business fit/holdout)
  llm/                  base.py, gemini.py, hf_local.py, vllm_local.py; create_backend chọn backend theo cấu hình
  agent/                prompts.py, context.py, solver.py, cache.py, pipeline.py, exemplars.py (kho few-shot)
  tools/                sandbox.py, results.py, timeutil.py (adapter), viet_time.py (phân tích thời gian/ngày),
                        grounding.py (khớp giá trị), rules.py
evaluation/             Chỉ dùng khi phát triển: scoring.py (tính PA), run_eval.py (chạy và chấm trên train)
training/               Chỉ dùng khi phát triển: export_sft.py (dữ liệu SFT), build_exemplars.py (kho few-shot)
notebooks/              kaggle_run.ipynb (cấu hình mỏng, gọi vào app)
tests/                  Kiểm thử, tổ chức phản chiếu cấu trúc của app
Dockerfile
requirements.txt        Thư viện chạy khi nộp bài
requirements-dev.txt    Thư viện phát triển và kiểm thử
```

Nguyên tắc phụ thuộc (một chiều):

- `evaluation` và `training` phụ thuộc `app`; `app` không phụ thuộc hai thư mục này.
- `agent` sử dụng `llm`, `tools` và `ingestion`.
- `tools` và `ingestion` độc lập với LLM.
- `torch` và `vllm` chỉ được import khi cần, bên trong `llm/`, để môi trường chạy kiểm thử không yêu cầu GPU.

## 3. Cài đặt

Yêu cầu: Python 3.10 trở lên.

```bash
pip install -r requirements-dev.txt
```

Biến môi trường:

| Biến | Công dụng |
|---|---|
| `GEMINI_API_KEY` | Khóa API khi dùng backend Gemini (hoặc Kaggle Secret cùng tên) |
| `TEST_DIR` | Thư mục dữ liệu đầu vào cho `python -m app.main` |
| `OUT_DIR` | Thư mục ghi `submission.jsonl` và log |
| `LOCAL_MODEL` | Tên repo Hugging Face hoặc đường dẫn thư mục mô hình local |

## 4. Sử dụng

Đánh giá trên một phần tập train (có đáp án) và in bảng PA:

```bash
python -m evaluation.run_eval --backend gemini --max-biz 2 --per-biz 15
```

Quy trình đo khái quát và dựng kho few-shot. Test dùng business chưa từng thấy, nên chỉ tin PA trên nhóm giữ lại
(holdout), còn mọi thứ rút ra từ train chỉ được dùng nhóm fit:

```bash
# 1. dựng kho ví dụ từ lời giải đã đúng ở nhóm fit
python -m evaluation.run_eval --backend gemini --subset fit --build-exemplars bank.jsonl
# 2. đo PA khái quát trên nhóm holdout, có dùng kho ví dụ
python -m evaluation.run_eval --backend gemini --subset holdout --holdout-n 6 --exemplars bank.jsonl
```

Sinh bài nộp:

```bash
python -m app.main --backend local --test-dir <thu_muc_du_lieu> --out-dir <thu_muc_ket_qua>
```

Kết quả gồm `submission.jsonl` (đủ một dòng cho mỗi câu hỏi, câu chưa xử lý được ghi abstain) và thư mục `logs/` chứa
mã đã sinh cho từng câu. Tệp nộp được kiểm định tự động sau khi ghi.

Trên Kaggle, dùng `notebooks/kaggle_run.ipynb`: tải thư mục dự án lên dưới dạng Kaggle Dataset, chọn `BACKEND`, sau đó
chạy lần lượt các ô.

**Backend:**

- `gemini`: phục vụ phát triển và so sánh. Không phù hợp cho bài nộp cuối vì thể lệ yêu cầu chạy offline.
- `local`: mô hình chạy trên GPU. Engine mặc định `auto`: dùng vLLM nếu khả dụng, nếu không hoặc vLLM không khởi tạo
  được thì chuyển sang transformers kết hợp bitsandbytes 4-bit.

## 5. Cấu hình

Toàn bộ tham số nằm trong `app/config.py` (`Config`). Các tham số chính:

| Tham số | Mặc định | Ý nghĩa |
|---|---|---|
| `backend` | `local` | `local` hoặc `gemini` |
| `n_samples` | 3 | Số lần sinh mã độc lập cho mỗi câu hỏi |
| `agree_min` | 2 | Số lần tối thiểu cho cùng kết quả để được trả lời |
| `max_retry` | 2 | Số lần tự sửa lỗi cho mỗi lời giải |
| `code_timeout_s` | 60 | Giới hạn thời gian thực thi mã (giây) |
| `temperature` | 0.7 | Phải lớn hơn 0 khi `n_samples` > 1 |
| `max_model_len` | 16384 | Tổng token tối đa (prompt và phản hồi), backend local |
| `max_new_tokens` | 1024 | Token tối đa cho phần mã được sinh |
| `prefill_chunk` | 512 | Kích thước đoạn khi nạp prompt (engine transformers), giảm nguy cơ hết VRAM |
| `local_engine` | `auto` | `auto`, `vllm` hoặc `hf` |
| `use_rules` | `False` | Bật luật tất định cho một số dạng câu hỏi đơn giản |
| `use_hints` | `True` | Đưa gợi ý tất định (khoảng thời gian, giá trị khớp) vào prompt |
| `exemplar_path` | `None` | Kho few-shot `.jsonl`; `None` là không dùng |
| `exemplar_k` | 2 | Số ví dụ tương tự đưa vào prompt |
| `min_agree_by_type` | `{}` | Ngưỡng đồng thuận riêng theo loại đáp án, vd `{"table": 3, "unanswerable": 3}` |

## 6. Kiểm thử

```bash
pytest
```

Bộ kiểm thử sử dụng backend giả lập nên không cần GPU hay kết nối API. Các ca bao quát: chuẩn hóa và ép
kiểu kết quả, quy đổi thời gian, đọc dữ liệu và profiling, sandbox, biểu quyết và tự sửa lỗi, cache, cơ chế dừng sớm khi
mô hình lỗi liên tiếp, gợi ý tất định, kho few-shot, ngưỡng đồng thuận theo loại, chia business, ghi và kiểm định
bài nộp, chấm PA, xuất dữ liệu SFT.

## 7. Trạng thái xác minh và giới hạn

`as_of_date` được xử lý theo từng câu hỏi qua `app/tools/viet_time.py`: bộ phân tích tạo các kỳ
bao gồm cả ngày đầu/cuối, kỳ so sánh, chuỗi thời gian, mốc tồn kho/đổi giá, bộ lọc ca và cờ mơ hồ.
Context đưa các vai trò kỳ (chính, so sánh, mốc tham chiếu) cùng trạng thái chưa phân giải vào prompt;
luật tất định chỉ dùng một kỳ chính không mơ hồ, còn các trường hợp phức tạp được chuyển sang LLM.
Khi thực thi code, `parse_cell_date`, `parse_time_of_day` và `in_shift` có sẵn để xử lý dữ liệu ngày/giờ
bẩn. Parser không thay thế rulebook: khi thiếu dữ liệu (ví dụ năm của dd/mm hoặc lịch âm-dương), cần
dùng metadata tương ứng hoặc không suy đoán.

| Thành phần | Trạng thái |
|---|---|
| Logic lõi (ingestion, agent, tools, submission, evaluation) | Đã kiểm thử tự động bằng backend giả lập |
| Điểm vào `app.main`, `evaluation.run_eval` | Đã chạy thử với `--no-llm` trên dữ liệu giả |
| Backend `gemini`, `hf_local`, `vllm_local` | **Chưa chạy trên môi trường thật** (Kaggle/GPU/API) |
| `Dockerfile` | **Chưa build.** Đường dẫn dữ liệu vào/ra và lệnh chạy là giả định, cần đối chiếu thể lệ |
| `requirements.txt` | Chưa ghim phiên bản |
| Hiệu năng (PA) trên dữ liệu thật | **Chưa có số liệu đo.** Gợi ý tất định, few-shot và ngưỡng theo loại chưa được đo tác động |

Giới hạn của thiết kế:

- `tools/sandbox.py` chỉ giới hạn thời gian thực thi, **không phải cơ chế cô lập bảo mật**. Môi trường nộp bài nên chạy
  mã sinh ra trong tiến trình hoặc container tách biệt.
- Chưa có bước kiểm chứng ngữ nghĩa độc lập giữa câu hỏi và kết quả (xem phần phương pháp ở mục 1).
