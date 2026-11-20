# Phân Tích Mã

Website phân tích cổ phiếu Việt Nam: bộ lọc, chấm điểm từng mã so với các mã cùng ngành, cờ đỏ.
Dữ liệu được cập nhật tự động mỗi ngày bằng GitHub Actions.

## Cấu trúc
| Tệp | Vai trò |
|---|---|
| `index.html` | Website (GitHub Pages) |
| `.github/workflows/cap-nhat-du-lieu.yml` | Lịch chạy tự động 16:00 thứ Hai – thứ Sáu |
| `script/capnhat.py` | Lấy danh sách mã, giá, BCTC (vnstock) → `data/` |
| `data/du_lieu.json` | Dữ liệu website đọc (do workflow tạo) |
| `data/fundamentals.csv`, `data/price_history.csv` | Dữ liệu lưu trữ để tính toán |

## Cách hoạt động
- Mỗi ngày: lấy giá của tất cả mã; cập nhật báo cáo tài chính cho 250 mã cũ nhất (xoay vòng, khoảng 1 tuần phủ hết thị trường).
- Chạy tay: tab **Actions** → *Cập nhật dữ liệu thị trường* → **Run workflow**.

Công cụ hỗ trợ phân tích, không phải khuyến nghị đầu tư. Dữ liệu từ thư viện mã nguồn mở vnstock; hãy kiểm chứng với báo cáo tài chính gốc.
