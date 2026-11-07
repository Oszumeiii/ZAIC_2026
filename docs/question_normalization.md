# Chuẩn hóa câu hỏi tiếng Việt

Tính năng chạy hoàn toàn offline, không gọi LLM/API và không thêm phụ thuộc. Bộ so gần đúng dùng `difflib`
có sẵn trong Python, phù hợp với từ điển nhỏ và cùng thư viện với grounding hiện có.

## Cách hoạt động

`app.normalization.QuestionNormalizer.normalize()` trả về `NormalizationResult`; gọi `to_dict()` để lấy
JSON-compatible metadata gồm câu hỏi gốc, câu chuẩn hóa, các sửa đổi và phương pháp, từ chưa rõ nghĩa và
các ứng viên. `has_ambiguity` bằng true cả khi chỉ có ứng viên fuzzy chưa được chấp nhận.

1. Chuẩn hóa NFC, khoảng trắng, ký tự ẩn và một số dấu câu full-width. Giữ nguyên văn literal trong dấu
   nháy/backtick, mã, số, ngày, tiền, URL/email và biểu thức SQL. Không bỏ dấu vĩnh viễn khỏi câu hỏi.
2. Bảo vệ các giá trị thực thể do **ValueIndex hiện có** cung cấp, kể cả cụm khớp gần đúng từ grounding.
3. So từ/cụm đầy đủ bằng khóa không dấu, cụm dài trước; thứ tự từ điển cố định; chỉ thay một lượt trên
   các span của đầu vào đã làm sạch, không chạy vòng thay thế đệ quy.
   Từ có dấu phải khớp cách viết được liệt kê tường minh: không được xem `nhiêu` là `nhiều` hoặc `họng` là `hong`.
   Một từ ASCII đơn không tự được thêm dấu chỉ vì trùng khóa của từ chuẩn (`vay`, `chua`, `voi`, `so` vẫn giữ nguyên).
   Các cụm không dấu đã có trong từ điển, kể cả cụm trộn có dấu/không dấu, vẫn được xử lý.
4. Từ đa nghĩa chỉ được mở rộng khi cả cách diễn đạt gần nó và schema/README cùng ủng hộ duy nhất một nghĩa.
   Ví dụ `dt tháng này` cần schema bán hàng để hiểu là doanh thu. `t7` được để nguyên vì chưa có quy tắc
   đủ chắc để phân biệt thứ Bảy và tháng 7. `k` sau số không bị biến thành phủ định.
5. Fuzzy tìm ứng viên trong từ vựng chuẩn, so không dấu, cùng số từ. Chỉ tự sửa khi đủ ngưỡng, cách biệt
   với ứng viên thứ hai và có thêm bằng chứng từ schema/README cùng tín hiệu ngôn ngữ. Tên viết hoa chưa
   biết không bị fuzzy sửa. Không đủ chắc thì giữ nguyên, có thể trả ứng viên trong metadata.

Ngưỡng mặc định: từ `.91`, cụm `.90`, từ dài tối đa 4 ký tự `.96`, cách biệt ứng viên `.08`. Không fuzzy
chuỗi có số hoặc dưới 4 ký tự. Có thể cấu hình bằng `QuestionNormalizer(word_threshold=..., phrase_threshold=...,
short_threshold=..., margin=...)`. Điểm similarity là **heuristic**, không phải xác suất đúng đã hiệu chuẩn.

Offsets `start/end` là vị trí ký tự trên đầu vào sau chuẩn hóa Unicode/khoảng trắng, không phải byte offset
trên câu gốc. `original_question` luôn là chuỗi gốc chính xác.

## Từ điển đóng gói

| JSON trong `app/data/normalization/` | Số mục |
|---|---:|
| `abbreviations.json` | 32 |
| `common_typos.json` | 93 |
| `slang.json` | 20 |
| `business_terms.json` | 78 |
| `temporal_phrases.json` | 31 |
| `ambiguous_terms.json` | 9 |
| Tổng | 263 |

Một mục có thể có nhiều variants. Ngoài phần ngôn ngữ thông dụng, lần đối chiếu dữ liệu thực đã bổ sung
69 biến thể chọn thủ công từ **2.289 câu train-fit**, có 669 lượt xuất hiện. 711 câu của 6 business khác
được giữ lại để kiểm tra. Không dùng từ vựng hay đáp án public_test để tạo/tinh chỉnh từ điển. Bằng chứng
ở `docs/normalization_train_evidence.json`, chỉ ghi biến thể và số lần gặp, không chép toàn bộ câu hỏi.
Thuật ngữ kinh doanh bao gồm bán hàng, tồn kho, nợ, thanh toán, giá vốn,
lợi nhuận, hạn dùng, nhân viên/chấm công và hoàn/hủy. Từ địa phương chỉ được thay trong ngữ cảnh đã chỉ rõ;
tên thực tế trong dữ liệu luôn được ưu tiên giữ nguyên. Loader báo lỗi nếu hai nghĩa khác nhau có cùng khóa.

Ví dụ:

```text
sp nào bán nhìu nhất thg trc -> sản phẩm nào bán nhiều nhất tháng trước
doanh thuu thág này bn       -> doanh thu tháng này bao nhiêu
san pham nao ban chay nhat   -> sản phẩm nào bán chạy nhất
dt tháng này                -> giữ nguyên + doanh thu / điện thoại / diện tích
SP-001 bán bao nhiêu         -> giữ nguyên
```

## Điểm tích hợp và tương thích

`ContextBuilder.build()` gọi normalizer sau khi chuẩn bị bảng, trước `_extra()` và `_build()`. Nó tái sử dụng
`biz.cache['value_index']`, không tạo cơ chế truy vấn doanh nghiệp mới. Grounding vẫn chạy trên câu gốc;
các hit bổ sung từ bản chuẩn hóa được hợp vào gợi ý. Bản chuẩn hóa hỗ trợ nhận diện cụm thời gian bằng
`describe_period()` hiện có; **không sửa logic tính ngày**. Kho few-shot và chọn rulebook vẫn nhận câu gốc.

Prompt có `CÂU HỎI GỐC`, `CÂU HỎI ĐÃ CHUẨN HÓA`, tối đa 8 gợi ý sửa đổi và 8 từ chưa rõ nghĩa. System
prompt nói rõ câu gốc là nguồn ý định chính, phải đối chiếu schema/README/rulebook và không xem ứng viên
là kết luận. Những mục này được tính vào ngân sách context hiện có. Câu gốc và bản chuẩn hóa không bị cắt.

Không đổi model/provider, cách sinh code, sandbox, luật nghiệp vụ, dữ liệu nguồn, biểu quyết, ý nghĩa
abstain/unanswerable hay schema `submission.jsonl`. Nhánh luật tất định nếu bật vẫn nhận câu hỏi gốc.

Mặc định bật:

```python
Config(enable_question_normalization=True)
```

Tắt để trở về **nguyên prompt/system cũ**, đồng thời tái sử dụng cách tạo gợi ý cũ:

```bash
python -m app.main --no-question-normalization ...
```

Hoặc `Config(enable_question_normalization=False)`. `--no-hints` tắt danh sách gợi ý sửa đổi và gợi ý grounding,
nhưng vẫn giữ bản chuẩn hóa và cảnh báo đa nghĩa. Tắt toàn bộ chuẩn hóa phải dùng cờ riêng ở trên.
Hash cache thay đổi qua system/context nên không dùng nhầm kết quả giữa hai chế độ.
Docker đã `COPY app ./app`, vì vậy các JSON nằm trong `app/data` được đóng gói mà không cần sửa Dockerfile.

## Kiểm thử và số đo

```bash
python -X utf8 -B -m unittest discover -s tests/normalization -v
python -X utf8 -B -m evaluation.evaluate_normalization
# Khi đã có dependencies dev và chạy trên Unix/Linux:
python -m pytest
```

Bộ đánh giá 70 câu synthetic cố định bao gồm câu đúng, viết tắt, lỗi gõ, không dấu, kết hợp, thời gian,
từ đa nghĩa, mã/thực thể và các ca không được sửa. Báo cáo JSON tại `docs/normalization_evaluation.json`.
Định nghĩa incorrect replacement rate ở cấp **câu**: số câu đã thay nhưng khác gold / tổng câu đã thay;
không phải lỗi trên mỗi token. Tỷ lệ exact tính trên toàn bộ 70 câu có gold xác định, gồm ca giữ nguyên.
Tỷ lệ bảo toàn đa nghĩa dựa trên 8 từ cần giữ nguyên và metadata chưa giải nghĩa; bảo toàn thực thể dựa
trên 17 chuỗi cần giữ. Đây là bộ kiểm tra nhỏ do người triển khai biên soạn, không phải benchmark độc lập.

Trong môi trường triển khai Windows này: 30 test unittest đạt. Chưa có pytest và không tải thêm qua mạng.
Đã gọi trực tiếp toàn bộ 112 trường hợp test function cũ bằng adapter fixture cục bộ (không phải pytest),
trên cả bản gốc và bản thay đổi: cùng 98 đạt/14 lỗi (87,5%), không thêm lỗi mới. Các lỗi đều nằm trong nhóm thực thi
code/solver/training phụ thuộc sandbox Unix: Windows không có `signal.SIGALRM`. Chưa xác minh timeout trên
Linux. Test tích hợp về số lần gọi model/schema kết quả dùng backend giả và mock sandbox có chủ đích.
**Cập nhật runtime sau lần audit này:** đã thêm fallback tiến trình con trên Windows, giữ nguyên nhánh SIGALRM
trên Unix. 112 test function cũ hiện đạt khi gọi trực tiếp qua adapter; xem `windows_runtime_regression.json`.
Các số 98/14 ở trên là kết quả lịch sử trước khi sửa tương thích Windows.
Không thay sandbox để khắc phục giới hạn môi trường, vì nằm ngoài phạm vi yêu cầu.
Chi tiết từng lỗi trước/sau được ghi trong `docs/normalization_regression.json`.

Đối chiếu dữ liệu train/public_test thật: xem [báo cáo dữ liệu](normalization_data_review.md). Script tái lập
`python -m evaluation.audit_normalization_data --train-dir <train> --test-dir <public_test> --with-train-values --output <report.json>`
chỉ dùng câu train cho audit ngôn ngữ; test chỉ kiểm hợp đồng dữ liệu. Không đọc answers hoặc sinh nhãn từ test.

Chưa có bộ E2E phù hợp với model local/GPU để so sánh PA bật/tắt. Không suy ra cải thiện độ đúng câu trả lời
hay PA chỉ từ số đo chuẩn hóa. Từ lạ, tên không có trong ValueIndex, lỗi nặng và ngữ cảnh mơ hồ vẫn cần
LLM/grounding phía sau xử lý; chọn giữ nguyên thay vì đoán. ValueIndex vẫn giữ giới hạn cột tối đa 5.000
giá trị phân biệt của triển khai hiện tại, không mở rộng chiến lược nạp/tìm dữ liệu.

## Các file thay đổi

- Mới: `app/normalization/{__init__,dictionary,context_rules,fuzzy_matcher,normalizer}.py`.
- Mới: 6 JSON trong `app/data/normalization/` liệt kê ở bảng trên.
- Sửa: `app/config.py`, `app/main.py`, `app/agent/context.py`, `app/agent/prompts.py`.
- Mới: `tests/normalization/test_normalizer.py`, `tests/normalization/test_integration.py`.
- Sửa: `tests/agent/test_context.py` (assert nhãn câu hỏi gốc mới; vẫn kiểm thứ tự grounding).
- Mới: `evaluation/normalization_cases.json`, `evaluation/evaluate_normalization.py`.
- Bổ sung sau đối chiếu train: `evaluation/audit_normalization_data.py`, `docs/normalization_data_review.md`,
  `docs/normalization_train_evidence.json`, `docs/normalization_real_data_audit.json`.
- Tài liệu: `README.md`, `docs/question_normalization.md`, `docs/normalization_evaluation.json`, `docs/normalization_regression.json`.
