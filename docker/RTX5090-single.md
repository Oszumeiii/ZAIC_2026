# Chạy public test trên 1 RTX 5090, ngân sách 6 giờ

Với **600 câu trên hai GPU**, dùng [hướng dẫn mặc định mới](RTX5090.md).

Đây là **inference**, không phải training/fine-tune. Chọn profile `rtx5090-6h`
để giảm chi phí trên một GPU. Chưa có benchmark toàn bộ public test trên RTX 5090
cho bản sửa này; tên profile là mục tiêu thời gian, không phải cam kết xử lý đủ câu.
RTX 5090 có [32 GB VRAM theo NVIDIA](https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/rtx-5090/).

## Thay đổi so với profile balanced

| Cấu hình | balanced | rtx5090-6h |
|---|---:|---:|
| Tensor parallel | 2 | 1 |
| Rewrite câu hỏi | Có | Có |
| Retry tại mỗi lớp SQL/JSON/manifest | 5 | 1 |
| CUDA graphs (`enforce_eager`) | Tắt (`True`) | Bật (`False`) |
| Scheduler `max_num_seqs` | Mặc định vLLM | 4 |
| `max_num_batched_tokens` | Mặc định vLLM | 4096, chunked prefill |
| SQL timeout | 60 giây | 20 giây |
| Trần lượt gọi model/câu, gồm cả rewrite và JSON retry | Không riêng | 14 |
| Ngân sách mỗi câu | Toàn bộ thời gian còn lại | Phân bổ theo số câu còn lại, tối đa 120 giây |

Giữ nguyên weights BF16, context 16.384 token, SQL/review tối đa 2.048 token,
manifest tối đa 6.144 token, review nghiệp vụ và tối thiểu hai phiếu đồng thuận
trong tối đa ba mẫu. Không bật thinking. Pipeline chỉ dùng văn bản nên profile
bật `language_model_only=True`. Không giảm context hay tự cắt schema/rulebook
thêm so với cơ chế fit context hiện có.

Theo [hướng dẫn tối ưu vLLM](https://docs.vllm.ai/en/stable/configuration/optimization/),
eager giảm thời gian khởi động nhưng đánh đổi tốc độ decode; giảm giới hạn scheduler
giúp giảm áp lực bộ nhớ. CUDA graphs có thêm chi phí khởi động và bộ nhớ, cần kiểm tra
thực tế trên image đã build. Đây là cấu hình khởi điểm cho workload tuần tự, chưa
phải kết quả chọn tham số qua benchmark. Prefix caching hiện có vẫn được bật.

Một câu thành công ngay ở hai mẫu dùng 5 lượt model (rewrite + SQL/review cho mỗi mẫu).
Bước rewrite đã được bật lại theo yêu cầu. Giảm retry và ngân sách mỗi câu có thể tăng `abstain` ở câu khó; cần
so sánh PA trên train và tỷ lệ trả lời trên test, không chỉ thời gian chạy.

## Build và chạy

Từ repo root trên server Linux có Docker/NVIDIA Container Toolkit và weights đã
chuẩn bị theo [hướng dẫn tải model](A100.md#2-tải-model-và-build-cần-internet):

```bash
export IMAGE=zaic2026_datamate_aio_dncoming:rtx5090
docker build --platform linux/amd64 -t "$IMAGE" .
export INPUT_DIR="$PWD/dataset/public_test"
export RESULT_DIR="$PWD/result_5090_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$RESULT_DIR"
set -o pipefail
docker run --rm --gpus '"device=0"' --network none --shm-size=16g \
  -e DATAMATE_PROFILE=rtx5090-6h \
  -v "$INPUT_DIR:/data:ro" \
  -v "$RESULT_DIR:/result" \
  "$IMAGE" 2>&1 | tee "$RESULT_DIR/console.log"
```

Profile tự chọn TP=1; không đặt `DATAMATE_TENSOR_PARALLEL=2` cùng profile này.
Sử dụng thư mục kết quả mới cho mỗi lần chạy. Code mới cần build lại image;
container đang chạy không tự nhận thay đổi. Chạy trong tmux nếu SSH.

Nếu CUDA graph compilation/capture lỗi hoặc OOM, chạy lại trong thư mục mới với
`-e DATAMATE_ENFORCE_EAGER=1`. Cấu hình fallback có thể chậm hơn; không tự chuyển
chế độ hoặc giảm độ chính xác weights. Nếu OOM ngay khi nạp weights/KV cache,
kiểm tra GPU trống bằng `nvidia-smi`; eager không bảo đảm giải quyết mọi loại OOM.

## Kiểm chứng mục tiêu 6 giờ

Ngân sách worker là 21.300 giây (5 giờ 55 phút), gồm xác minh/nạp model,
manifest, ingestion và inference. Watchdog shell dừng ở 21.540 giây, thêm tối đa
10 giây shutdown. Worker kiểm tra ngân sách trước mỗi lượt model, kể cả retry
JSON và manifest. Một lượt GPU đang chạy không bị ngắt giữa chừng; timeout SQL
vẫn độc lập. Watchdog ngoài cùng bảo vệ giới hạn tổng.

Sau mỗi business, worker dùng thời gian chuẩn bị đã đo để dự trù cho các business
còn lại (tối đa nửa thời gian còn lại), chia phần còn lại cho số câu chưa xử lý.
Câu vượt ngân sách và chưa đủ đồng thuận được ghi `budget_exhausted`/`abstain`;
câu chưa được xử lý vẫn giữ `pending`/`abstain`. Chi phí chuẩn bị business mới có
thể khác nhiều, nên phân bổ này không bảo đảm mọi câu đều được xử lý.

Sau vài business đại diện, có thể đọc báo cáo ngay khi container còn chạy:

```bash
python3 -m docker.runtime_report "$RESULT_DIR"
tail -f "$RESULT_DIR/logs/progress.jsonl"
```

`mean_question_s` cần nhỏ hơn `target_mean_question_s`. `projected_total_s` là
ước lượng từ các business/câu đã đo, bao gồm startup và chuẩn bị. Ví dụ với
500 câu, startup + chuẩn bị tổng 1 giờ thì cần trung bình khoảng **35,4 giây/câu**
để đạt ngân sách 21.300 giây. Không cộng `elapsed_ms` trong log từng câu vì có
phần chuẩn bị dùng chung và thời gian chờ bị trùng.

Kiểm tra `answered_questions`, `budget_exhausted_questions`, lỗi preparation,
`completed_questions/total_questions` trong `logs/completion.json`. Hoàn tất
500 câu nhưng đa số `abstain` không chứng minh chất lượng đạt yêu cầu. Đo lại
trên train bằng cùng image để so sánh độ chính xác, rồi chạy đủ public test.

`logs/run.json` ghi profile và cấu hình thực tế. Trong progress log mới,
`elapsed_s` luôn là tổng thời gian từ lúc bắt đầu; `duration_s` là thời gian riêng
của stage. Script báo cáo dùng định dạng mới này.
