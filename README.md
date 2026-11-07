# datamate-agent

Agent hỏi đáp dữ liệu bảng bằng tiếng Việt, phát triển cho **Zalo AI Challenge 2026, nhiệm vụ DataMate (Track 2)**.

Chuẩn hóa câu hỏi offline mặc định bật: xử lý viết tắt/lỗi gõ có căn cứ, giữ câu gốc, bảo vệ mã và tên trong dữ liệu,
giữ lại từ đa nghĩa chưa rõ. Tắt bằng `--no-question-normalization` hoặc `Config(enable_question_normalization=False)`.
Xem [thiết kế, từ điển, số đo và giới hạn kiểm thử](docs/question_normalization.md).
Từ điển đã được đối chiếu với train thực tế; xem [nguồn biến thể và kiểm tra train/public_test](docs/normalization_data_review.md).

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
app/                    Mã chạy chính trong cả hai target Docker
  config.py             Config: toàn bộ tham số vận hành
  main.py               Điểm vào: python -m app.main
  submission.py         Ghi và kiểm định submission.jsonl
  text.py               Chuẩn hóa văn bản tiếng Việt dùng chung
  ingestion/            dataset.py (đọc split, sửa tiêu đề lệch dòng), profiling.py (mô tả cột, gộp sheet),
                        splitting.py (chia business fit/holdout)
  llm/                  base.py, gemini.py, hf_local.py, vllm_local.py; create_backend chọn backend theo cấu hình
  agent/                prompts.py, context.py, solver.py, cache.py, pipeline.py, exemplars.py (kho few-shot)
  tools/                sandbox.py, results.py, timeutil.py, grounding.py (khớp giá trị), rules.py
evaluation/             Chỉ dùng khi phát triển: scoring.py (tính PA), run_eval.py (chạy và chấm trên train)
training/               Chỉ dùng khi phát triển: export_sft.py (dữ liệu SFT), build_exemplars.py (kho few-shot)
notebooks/              kaggle_run.ipynb (cấu hình mỏng, gọi vào app)
tests/                  Kiểm thử, tổ chức phản chiếu cấu trúc của app
Dockerfile
requirements.txt        Thư viện chạy khi nộp bài
requirements-gemini.txt Thư viện CPU cho target Docker gemini
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

### Chạy bằng Docker trên Windows / PowerShell

Dockerfile có hai target. Container đóng gói chương trình và thư viện; target `local` vẫn cần trọng số model
và GPU, còn target `gemini` cần API key và kết nối mạng, không cần host model hay GPU.
Cả hai target có `evaluation` và `training` để chạy lệnh chấm train; target `local` giữ cấu hình offline.

| Target | Build | Nhu cầu |
|---|---|---|
| `gemini` | `docker build --target gemini -t datamate:gemini .` | API Google, CPU, mạng |
| `local` (mặc định) | `docker build --target local -t datamate:local .` | Model local đã tải, GPU NVIDIA |

Mở Docker Desktop ở chế độ Linux containers, sau đó chạy trong PowerShell:

```powershell
cd "D:\DaiHoc\Project\AI_khuvuc\ZAIC2026\ZAIC_2026"
docker version
docker build --target gemini -t datamate:gemini .

$DataRoot = "D:\DaiHoc\Nam3\ZAIC"
$OutputRoot = Join-Path $DataRoot "output"
New-Item -ItemType Directory -Force -Path "$OutputRoot\train", "$OutputRoot\public_test" | Out-Null

# File .env nằm cùng thư mục với Dockerfile; mở và dán key sau dấu = rồi lưu.
notepad .env
```

Trong file `.env`, nhập đúng một dòng (thay giá trị bên phải bằng key thật, không cần dấu nháy):

```dotenv
GEMINI_API_KEY=dan_key_cua_ban_vao_day
```

Nếu chưa có file `.env`, tạo nó bên cạnh Dockerfile rồi nhập dòng trên. `.env` được bỏ qua bởi Git và Docker
build context. Docker đọc nó qua `--env-file .env` khi chạy; thay key không cần build lại image.
Key không đặt trong Dockerfile/build args/mã nguồn. Chạy lệnh từ thư mục chứa `.env`, hoặc dùng đường dẫn tuyệt đối
cho `--env-file`. Đây là cách nạp biến của Docker; chạy Python trực tiếp không tự đọc file `.env`.
Target API không dùng `--network none`. Mọi lần chạy Gemini gửi câu hỏi và ngữ cảnh dữ liệu cho Google;
chỉ dùng khi dữ liệu được phép đưa qua API. Với dữ liệu cuộc thi yêu cầu giữ kín/offline, dùng target local ở dưới.
Các lệnh dưới đây là hướng dẫn, không phải xác nhận đã gửi dữ liệu train/public_test qua API.

Chạy thử 5 câu của 1 business train và chấm PA:

```powershell
docker run --rm --env-file .env `
  --mount "type=bind,source=$DataRoot,target=/datasets,readonly" `
  --mount "type=bind,source=$OutputRoot\train,target=/output" `
  datamate:gemini python -m evaluation.run_eval `
  --backend gemini --gemini-model gemini-3.5-flash-lite `
  --train-dir /datasets/train --out-dir /output `
  --max-biz 1 --per-biz 5 --run-name gemini_train
```

Sinh bài nộp cho 5 câu public_test trước:

```powershell
docker run --rm --env-file .env `
  --mount "type=bind,source=$DataRoot,target=/datasets,readonly" `
  --mount "type=bind,source=$OutputRoot\public_test,target=/output" `
  datamate:gemini python -m app.main `
  --backend gemini --gemini-model gemini-3.5-flash-lite `
  --test-dir /datasets/public_test --out-dir /output `
  --limit-test 5 --run-name gemini_public
```

Bỏ `--limit-test 5` để chạy toàn bộ 1.000 câu. File `submission.jsonl` luôn đủ ID; những câu chưa chạy được
ghi `abstain`. Kết quả trên Windows nằm ở `D:\DaiHoc\Nam3\ZAIC\output\public_test\submission.jsonl`.
Lệnh chấm train đọc `answers.jsonl` để chấm sau suy luận; `app.main` chỉ sinh bài nộp, không chấm train.
Mount cả split (có `questions.jsonl`, `rulebook.md`, `calendar.csv`, `data/`), không chỉ mount riêng thư mục `data/`.

Không cần chạy `Read-Host`, đặt `$env:GEMINI_API_KEY` hoặc dùng `Remove-Item Env:` khi dùng `--env-file`.
Không commit/chia sẻ file `.env`; Docker không đưa file này vào image.

Chạy offline bằng GPU và model đã có trong một thư mục trên máy:

```powershell
docker build --target local -t datamate:local .

# Thay bằng thư mục chứa config.json, tokenizer và trọng số model thực tế của bạn.
$ModelDir = "D:\Models\datamate-model"

docker run --rm --gpus all --network none `
  --mount "type=bind,source=$DataRoot,target=/datasets,readonly" `
  --mount "type=bind,source=$OutputRoot\public_test,target=/output" `
  --mount "type=bind,source=$ModelDir,target=/models/model,readonly" `
  datamate:local python3 -m app.main `
  --backend local --local-model /models/model `
  --test-dir /datasets/public_test --out-dir /output --limit-test 5
```

Model chưa được đóng gói vào image: dòng `COPY models/model` vẫn là comment. Có thể dùng
`python3 -m evaluation.run_eval --backend local --train-dir /datasets/train --out-dir /output`
để chấm train trong target local với các mount model/GPU tương tự. Chuẩn hóa câu hỏi mặc định bật trong cả hai target.

Script smoke test Gemini riêng và dữ liệu thử sinh ra từ lần kiểm tra trước đã được dọn; chạy trực tiếp
`app.main` hoặc `evaluation.run_eval` như trên. Backend `app/llm/gemini.py` được giữ vì target API cần nó.

Đã build thành công `datamate:gemini` và chạy toàn bộ pytest trên Linux trong container tạm:
**148 test đạt, 108 subtest đạt**. Đã kiểm tra mount train/public_test thật với `--no-llm --network none`:
lệnh chấm train chạy được và submission public_test đủ 1.000 ID, kiểm định hợp lệ. Các đáp án của lần
kiểm tra đường dẫn đều là `abstain`, không phải kết quả trả lời bằng LLM. Target GPU local chưa được build/chạy GPU.

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

| Thành phần | Trạng thái |
|---|---|
| Logic lõi (ingestion, agent, tools, submission, evaluation) | Đã kiểm thử tự động bằng backend giả lập |
| Điểm vào `app.main`, `evaluation.run_eval` | Đã chạy thử với `--no-llm` trên dữ liệu giả |
| Backend `gemini` | Đã chạy API `gemini-3.5-flash-lite` trên 3 câu dữ liệu giả: 3/3 đúng |
| Backend `hf_local`, `vllm_local` | **Chưa chạy trên GPU thật** |
| `Dockerfile` | Target `gemini` đã build và kiểm thử trên Linux; target `local` GPU/offline chưa build |
| `requirements.txt` | Chưa ghim phiên bản |
| Hiệu năng (PA) trên dữ liệu thật | **Chưa có số liệu đo.** Gợi ý tất định, few-shot và ngưỡng theo loại chưa được đo tác động |

Giới hạn của thiết kế:

- `tools/sandbox.py` chỉ giới hạn thời gian thực thi, **không phải cơ chế cô lập bảo mật**. Môi trường nộp bài nên chạy
  mã sinh ra trong tiến trình hoặc container tách biệt.
- Kiểm tra runtime Windows: 6 test tiến trình con/timeout đạt; 112 test function cũ gọi trực tiếp bằng adapter fixture
  đều đạt sau sửa tương thích Windows (`docs/windows_runtime_regression.json`). Sau đó đã chạy pytest thật trong
  container Linux: 148 test và 108 subtest đạt.
- Chưa có bước kiểm chứng ngữ nghĩa độc lập giữa câu hỏi và kết quả (xem phần phương pháp ở mục 1).
