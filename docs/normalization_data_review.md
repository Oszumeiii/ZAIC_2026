# Đối chiếu chuẩn hóa với dữ liệu thực tế

Đã đọc dữ liệu ở `D:/DaiHoc/Nam3/ZAIC`. Hai split thực tế là `train` và `public_test`, không có thư mục `test`.

| Split | Câu hỏi | Doanh nghiệp |
|---|---:|---:|
| train | 3000 | 30 |
| public_test | 1000 | 14 |

Cả hai hợp lệ về các trường `id`, `business_id`, `question`, `as_of_date`; không có ID trùng,
không thiếu thư mục business được tham chiếu. Dữ liệu gồm CSV, XLSX và SQLite, phù hợp loader hiện có.
Không sửa bất kỳ file dữ liệu nguồn nào và không đọc `answers.jsonl` trong lần audit này.

## Các thiếu sót đã xác định và sửa

- Từ điển cũ chủ yếu là bộ ngôn ngữ tự soạn; thiếu `dthu`, `hqua`, `thangs`, `tieenf`, `hangf`, `cuwar`,
  `dduwowcj` và nhiều dấu vết gõ Telex xuất hiện trong train.
- So khớp không dấu không được coi mọi cách đặt dấu là tương đương: `nhiêu` không phải `nhiều`,
  `họng` không phải tiếng lóng `hong` = `không`. Đã thêm kiểm tra hình thức có dấu tường minh.
- Không tự thêm dấu vào từ ASCII đơn chỉ dựa vào canonical key: `vay`, `chua`, `voi`, `roi`, `so`,
  `la`, `cua` có thể đã là từ hợp lệ. Các cụm an toàn đã có từ điển vẫn khôi phục được dấu.
- Thêm `match_canonical=false` cho mục Telex chỉ được kích hoạt bởi biến thể tường minh, tránh mở
  rộng phạm vi thay thế ngoài ý định. Không dùng một bộ giải mã Telex chung cho tên thương hiệu.

## Nguồn tạo từ điển

Chia theo helper hiện có `holdout_businesses(..., 6)`: **2289 câu fit** và
**711 câu holdout**. Chỉ fit dùng để chọn thêm biến thể. Không tinh chỉnh dựa
trên từ vựng test, không chép đáp án, không tạo luật dựa trên mã câu hỏi/business.

Đã chọn thủ công **69 biến thể**, tổng **669
lượt xuất hiện trong fit**. Từ điển hiện có **263 mục** (mỗi mục có thể có nhiều biến thể).
Nguồn từng biến thể và tần suất: [normalization_train_evidence.json](normalization_train_evidence.json).
Khi thử độc lập 69 biến thể được chọn, bản trước mở rộng đúng 1/69, bản sau 69/69. Đây là coverage trên danh sách
đã chọn từ fit, không phải accuracy trên toàn bộ câu hỏi.

Ví dụ tổng quát, không chép câu hỏi thật:

```text
dthu hqua bn                     -> doanh thu hôm qua bao nhiêu
cuwar hangf bán dduwowcj bn       -> cửa hàng bán được bao nhiêu
thangs này có bao nhieeu tieenf   -> tháng này có bao nhiêu tiền
sp bán dc nhieeuf nhất           -> sản phẩm bán được nhiều nhất
nhiêu / họng / vay / chua        -> giữ nguyên
```

Tên như `Namilux`, `Powerflexx`, `Plusssz`, mã sản phẩm và tên khách không được thu gom thành luật sửa
ngôn ngữ. Vẫn để ValueIndex/grounding xử lý theo dữ liệu của business lúc chạy.

## Audit toàn bộ train

Đã chạy cả bản trước và bản sau trên toàn bộ train, nạp schema/README và chỉ mục giá trị thực.
Bảo vệ tên dùng tất cả khớp chính xác từ ValueIndex; fuzzy grounding được kiểm riêng bằng integration test.
Script chỉ lọc các value có từ đầu xuất hiện trong câu hỏi để tránh dựng lại key không liên quan; đây là
điều kiện cần để có khớp chính xác, không làm đổi kết quả bảo vệ exact entity.

| Nhóm | Câu hỏi | Câu thay đổi trước | Câu thay đổi sau | Lỗi chạy sau | Va chạm “nhiêu/họng” trước → sau | Literal số/mã thay đổi sau |
|---|---:|---:|---:|---:|---:|---:|
| Fit | 2289 | 1278 | 1146 | 0 | 412 → 0 | 0 |
| Holdout | 711 | 389 | 350 | 0 | 107 → 0 | 0 |

Số câu thay đổi không đo chất lượng: nhiều thay thế hơn có thể đồng nghĩa sửa sai nhiều hơn. Các câu train
không có nhãn chuẩn hóa thủ công đầy đủ, nên không báo “accuracy chuẩn hóa train” hay PA từ các số này.
Audit không gọi LLM, không sinh code và không tính nghiệp vụ.

Với public_test chỉ kiểm hợp đồng đầu vào (cấu trúc/ID/thư mục), không dùng câu hỏi để tạo hay chỉnh từ điển.
Do đó chưa kết luận độ phủ từ vựng hoặc độ đúng câu trả lời trên test.

Chi tiết: [normalization_real_data_audit.json](normalization_real_data_audit.json).

## Kiểm thử

- 30 test unittest đạt, bao gồm kiểm bảo toàn dấu, bảo vệ tên và khẳng định audit không chuẩn hóa/học từ test.
- Bộ gold tự biên soạn mở rộng: 70/70 câu đúng; không có thay thế sai trong bộ này;
  giữ 8/8 từ đa nghĩa và 17/17 chuỗi thực thể. Đây là bộ nhỏ, không phải benchmark độc lập.
- 112 test function cũ gọi trực tiếp bằng adapter fixture: cùng 98 đạt/14 lỗi trước và sau, không có lỗi mới.
  Không có pytest trong môi trường; các lỗi liên quan sandbox Unix `SIGALRM` không có trên Windows.

Cập nhật sau lần đối chiếu dữ liệu: fallback tiến trình con đã khắc phục hạn chế Windows. Bộ 112 test function
cũ hiện đạt khi gọi trực tiếp qua adapter, cùng 6 test runtime mới. Xem `windows_runtime_regression.json`;
các số 98/14 phía trên ghi lại trạng thái lúc thực hiện audit ngôn ngữ.

## Tái lập

```bash
python -m evaluation.audit_normalization_data --train-dir "D:/DaiHoc/Nam3/ZAIC/train" --test-dir "D:/DaiHoc/Nam3/ZAIC/public_test" --with-train-values --output audit.json
python -m evaluation.evaluate_normalization
python -m unittest discover -s tests/normalization -v
```

Audit mặc định chỉ kiểm bản hiện tại. Có thể thêm `--baseline-repo <bản-code-cũ>` để so trước/sau.
Kết quả so trước/sau trong báo cáo lưu lại bản trước khi áp dụng thay đổi lần này.
Tính năng vẫn bật/tắt bằng `Config(enable_question_normalization=...)` hoặc `--no-question-normalization`.
Không đổi model/provider, codegen, luật nghiệp vụ, logic lịch, loader hay JSON bài nộp.
