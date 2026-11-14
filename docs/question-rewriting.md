# Chuẩn hóa câu hỏi tiếng Việt bằng Qwen local

`LocalLLMService.rewrite_question()` có thể sửa lỗi gõ, dấu và cách viết tắt bằng
Qwen cục bộ. Hàm không gọi API bên ngoài; solver giữ câu gốc để đối chiếu và ghi
lỗi nếu quá trình rewrite không thành công.

```python
from app.config import Config
from app.llm import create_backend
from app.llm.local_service import LocalLLMService

config = Config(local_model="/models/Qwen3.5-9B")
rewriter = LocalLLMService(create_backend(config), config)
question = rewriter.rewrite_question(
    "sl sp SKU-001 cua shop ABC ngay 04/05/2026 la bn?",
    protected_terms=["SKU-001", "ABC"],
)
print(question)
```

Mô hình phải có sẵn tại `local_model` trước khi chạy offline. Khi inference, không
tải weights và không gọi dịch vụ từ xa. Đầu vào rỗng gây `ValueError`; phản hồi
JSON sai cấu trúc, thay đổi số liệu/ngày giờ hoặc `protected_terms` gây
`GenerationError`. Các tên/mã quan trọng nên truyền qua `protected_terms`.

Prompt yêu cầu giữ nguyên ý nghĩa và các từ mơ hồ. Kiểm tra bằng code chỉ bảo vệ
chữ số và thuật ngữ được chỉ định, không chứng minh hai câu tương đương về nghĩa.
