# Pipeline câu hỏi → SQL → đáp án

Luồng mặc định của `app.main` và `evaluation.run_eval` dùng DuckDB. Chọn
`--query-engine pandas` để chạy lại baseline Python/pandas.

1. Nạp câu hỏi, rulebook, calendar từ thư mục split.
2. Lấy DuckDB/catalog của đúng business trong `--database-dir`.
   Nếu chưa có, fingerprint không khớp nguồn/manifest/rulebook/override, hoặc bật
   `--rebuild-database`, `IngestionPipeline.ingest()` dùng reader và typing
   Raw→Typed→Semantic dùng chung để dựng lại. Nếu business chưa có manifest,
   ingestion tạo manifest v2 nháp local trước, lưu ở `<split>/manifests/`.
3. Dựng context bằng schema vật lý, ánh xạ tên nguồn, vai trò cột, quan hệ,
   facts, unresolved issues, quality, grain, semantic views, completeness,
   mẫu dữ liệu, phạm vi ngày và trạng thái. Đọc README và rulebook đầy đủ.
4. Qwen local có thể rewrite câu hỏi; lỗi rewrite thì giữ câu gốc và ghi lỗi.
   `--no-rewrite` bỏ bước này. Cả câu gốc và câu rewrite đều có trong context.
5. Model sinh JSON chứa `type`, `sql`, `reason`. SQL được thực thi trên DuckDB
   chỉ đọc. Model nhận lỗi SQL để sửa tối đa `max_retry` lần, không nhận đáp án chuẩn.
6. Kiểm tra kiểu/kích thước kết quả; áp dụng đồng thuận theo `n_samples` và
   `agree_min`. Không đạt đồng thuận thì `abstain`. Có bằng chứng không đủ dữ liệu
   thì có thể trả `unanswerable` kèm lý do.
7. Chỉ sau suy luận, evaluation ghép đáp án chuẩn theo ID và chấm đúng/sai/abstain.

Khi bật `sql_review` (worker Docker bật mặc định), SQL chạy thành công vẫn phải
qua kiểm tra nghiệp vụ và assertion trước khi được tính phiếu đồng thuận.
`required_tables` trong review chỉ dùng để khôi phục schema thuộc `omitted_tables`.
Nếu model liệt kê lại bảng đã có trong context, hệ thống tiếp tục kiểm tra review,
không sinh lại SQL chỉ vì danh sách đó khác rỗng. Tên bảng không có trong catalog
được báo `Unknown required tables`; review `fail`/`unknown` hoặc assertion sai vẫn
bị từ chối. Hết lượt thử mà không đủ mẫu hợp lệ sẽ trả `abstain`.

## Chạy thử đúng 5 câu đầu tiên

Đặt weights Qwen sẵn trên máy và trỏ `--local-model` tới thư mục đó. Lệnh chạy
inference local, không đọc `answers.jsonl` và không gọi dịch vụ LLM bên ngoài:

```bash
python -m evaluation.run_eval \
  --backend local --query-engine sql --local-model /models/Qwen3.5-9B \
  --train-dir dataset/train \
  --out-dir outputs/sql_five \
  --database-dir outputs/sql_five/databases \
  --rebuild-database --max-biz 5 --limit-train 5 \
  --n-samples 3 --agree-min 2
```

`--max-biz 5` bảo đảm 5 câu đầu (có tối đa 5 business) không bị lọc bởi mặc định
3 business. `--limit-train` lấy theo thứ tự câu hỏi sau các bộ lọc business.
Bỏ `--rebuild-database` khi chạy lại để dùng database đã dựng. Khi dữ liệu nguồn
hoặc manifest thay đổi, fingerprint tự phát hiện và dựng lại database.

Đầu ra:

- `predictions.jsonl`: đáp án dự đoán của các câu đã chạy.
- `evaluation.jsonl`: đáp án dự đoán/chuẩn và trạng thái chấm theo ID.
- `summary.json`: số câu đúng/sai/abstain, PA, engine và model.
- `logs/train_log.jsonl`: câu gốc, rewrite, SQL, kết quả truy vấn, lỗi sửa SQL,
  số phiếu đồng thuận, đường dẫn và SHA-256 database.
- `logs/llm_cache_train.jsonl`: kết quả thành công để tiếp tục lần sau. Khóa cache
  bao gồm model, prompt, câu hỏi, as_of_date, context, hash database và cấu hình đồng thuận.
- `databases/<business_id>/current/`: DuckDB, catalog và manifest dùng cho lần chạy.
  SQL prompt dùng catalog có cấu trúc; `DATABASE_GUIDE.md` là tài liệu hỗ trợ,
  không phải nguồn metadata thay thế.

Inference không cần đáp án chuẩn:

```bash
python -m app.main --backend local --query-engine sql --local-model /models/Qwen3.5-9B \
  --test-dir dataset/train --out-dir outputs/sql_inference \
  --database-dir outputs/sql_five/databases --limit-test 5
```

Inference ghi `submission.jsonl` đủ ID của split; các câu chưa chạy do giới hạn
được điền `abstain`. Evaluation chỉ chấm các câu đã chọn.

## Thực thi và giới hạn

`SQLExecutor` dùng parser DuckDB để chỉ nhận một câu SELECT/WITH, mở database
read-only, tắt external access và tự tải extension, khóa cấu hình, giới hạn bộ nhớ
512 MB, hai thread, timeout và tối đa 1.000 dòng kết quả. SQL không được đọc file
khác hoặc ghi database. SQL rỗng/NULL, sai kiểu và vượt giới hạn được trả về cho
model sửa, không tự đổi thành 0.

Các helper SQL dùng lại logic hiện có: `vn_date(text)`, `vn_date_year(text, year)`
và `vn_norm(text)`. Calendar được đăng ký là bảng chỉ dùng trong kết nối.

Đồng thuận không chứng minh đáp án đúng. Việc chọn JOIN, xử lý nghiệp vụ và
nhận diện thiếu dữ liệu còn phụ thuộc model; cần đo trên tập đại diện lớn hơn
5 câu. Công cụ export SFT/exemplars hiện dành cho baseline pandas, chưa xuất
dữ liệu huấn luyện SQL.

Tham khảo API:
[DuckDB Python](https://duckdb.org/docs/stable/clients/python/reference/),
[cấu hình truy cập DuckDB](https://www.duckdb.org/docs/current/operations_manual/securing_duckdb/overview),
Qwen structured output được kiểm định bằng JSON Schema trước khi thực thi.
