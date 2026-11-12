# Muse AI Studio (Video • Image • Chat)

Ứng dụng Web Studio toàn diện khai thác trực tiếp sức mạnh của **Muse AI (`muse.ai`)** hỗ trợ:
- 🎬 **Tạo Video (Text-to-Video & Image-to-Video):** Sinh video sắc nét kèm âm thanh nguyên bản (*Native Audio Sync*).
- 🎨 **Tạo Ảnh (Text-to-Image & Image-to-Image):** Đa dạng tỉ lệ (1:1 Vuông, 16:9 Ngang, 9:16 Dọc Reels/TikTok).
- 💬 **Trò chuyện (Muse Spark Chat):** Lên kịch bản video, sáng tạo prompt nghệ thuật.
- 📁 **Bộ sưu tập (Local Gallery):** Tự động tải và lưu trữ tất cả sản phẩm vào thư mục `outputs/`.
- 🔑 **Xác thực OTP Email tự động:** Đăng nhập 1 lần trực tiếp trên giao diện Web, tự động lưu phiên an toàn (`.muse-state/cookies.json`).

---

## ⚡ Yêu cầu trước khi chạy
1. **Bật VPN sang Mỹ (US):** Vì `muse.ai` hạn chế địa lý đối với Việt Nam, hãy bật VPN trên máy tính của bạn trước khi thao tác (khi gửi mã OTP hoặc tạo video/ảnh).
2. **Tài khoản Muse:** Chuẩn bị email tài khoản Muse của bạn để nhận mã xác thực OTP.

---

## 🚀 Cách khởi động ứng dụng

### Cách 1: Khởi động nhanh (Khuyên dùng)
* Nhấp đúp vào file **`start.bat`** trong thư mục này.
* Trình duyệt sẽ tự động mở trang web: **`http://localhost:7860`**.

### Cách 2: Khởi động từ dòng lệnh
```powershell
.\.venv\Scripts\python.exe server.py
```

---

## 🔐 Hướng dẫn xác thực tài khoản lần đầu (Cách 2 - OTP)
1. Bật VPN sang Mỹ trên máy tính.
2. Mở Web App tại `http://localhost:7860`.
3. Bấm nút **"Đăng nhập Muse OTP"** ở góc trên bên phải.
4. Nhập địa chỉ Email của bạn -> Bấm **"Gửi mã OTP về Email"**.
5. Kiểm tra hộp thư Gmail/Email (cả hộp thư rác/Spam), lấy mã 6 chữ số.
6. Nhập mã OTP vào ô trên web -> Bấm **"Xác nhận & Hoàn tất"**.
7. Sau khi xác nhận thành công, trạng thái sẽ chuyển sang màu xanh: **"Đã kết nối: your_email@..."** và bạn có thể thỏa sức tạo video, ảnh và chat!
