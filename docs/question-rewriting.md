# Chuẩn hóa câu hỏi tiếng Việt bằng Gemini

`GeminiBackend.rewrite_question()` sửa lỗi gõ, thiếu dấu, viết tắt và cách nhắn tin
dựa trên ngữ cảnh. Hàm chỉ viết lại câu hỏi, không trả lời hoặc quy đổi thời gian.

Đặt `GEMINI_API_KEY` (hoặc `GEMINI_APIKEY`) trong biến môi trường hoặc file `.env`
ở gốc repo. Có thể truyền trực tiếp `api_key` khi tạo backend. Không commit API key.

```python
from app.config import Config
from app.llm.gemini import GeminiBackend

rewriter = GeminiBackend(Config())  # dùng gemini_model/thinking trong Config
original = "hnay doanh thu bn, ko tinh don huy?"
question = rewriter.rewrite_question(original)
print(question)
# Ví dụ kết quả: Hôm nay doanh thu bao nhiêu, không tính đơn hủy?

question = rewriter.rewrite_question(
    "sl sp SKU-001 cua shop ABC ngay 04/05/2026 la bn?",
    protected_terms=["SKU-001", "ABC"],
)
```

Tái sử dụng một backend cho nhiều câu để dùng chung API client. Để chọn model hoặc
truyền key từ chương trình, dùng `GeminiBackend(Config(gemini_model=model), api_key=key)`.

Gọi hàm trước `resolve_time(question, as_of, calendar)` hoặc bước dựng context nếu
muốn parser nhận câu đã chuẩn hóa. Giữ lại câu gốc để đối chiếu. Hàm chưa tự động
được bật trong solver; luồng inference mặc định vẫn giữ nguyên.

Đầu ra là `str`. Đầu vào rỗng gây `ValueError`; sai kiểu gây `TypeError`.
Phản hồi JSON sai cấu trúc, thay đổi số liệu/ngày giờ hoặc các `protected_terms`
sẽ gây `GenerationError`. Lỗi API dùng cơ chế retry hiện có; hết quota ngày gây
`QuotaExhausted`. Nếu muốn dùng câu gốc khi rewrite thất bại, caller có thể bắt
`GenerationError` và ghi nhận lỗi trước khi tiếp tục.

Prompt yêu cầu giữ nguyên ý nghĩa và các từ mơ hồ. Kiểm tra bằng code chỉ bảo vệ
chữ số và các thuật ngữ được chỉ định, không chứng minh được hai câu tương đương
về nghĩa. Các tên/mã quan trọng nên truyền qua `protected_terms`.
