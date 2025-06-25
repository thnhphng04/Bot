import requests
import logging

class TelegramNotifier:
    def __init__(self, token, chat_id):
        self.token = token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{self.token}/sendMessage"

    def send_message(self, text, silent=False):
        payload = {
            'chat_id': self.chat_id,
            'text': text,
            'parse_mode': 'Markdown',
            'disable_notification': silent
        }
        try:
            response = requests.post(self.base_url, data=payload, timeout=5)
            if response.status_code == 200:
                logging.info("Đã gửi thông báo Telegram thành công.")
            else:
                logging.error(f"Lỗi khi gửi thông báo Telegram: {response.status_code} - {response.text}")
        except Exception as e:
            logging.error(f"Ngoại lệ khi gửi thông báo Telegram: {e}")

    def format_order_message(self, symbol, side, quantity, entry, sl, tp):
        message = (
            f"🚀 **TÍN HIỆU MỚI** 🚀\n\n"
            f" cặp tiền: `{symbol}`\n"
            f" hướng: **{side}**\n\n"
            f" khối lượng: `{quantity}`\n"
            f" giá vào lệnh: `{entry}`\n"
            f" cắt lỗ: `{sl}`\n"
            f" chốt lời: `{tp}`"
        )
        return message

    def format_critical_error_message(self, symbol, side, error_message):
        message = (
            f"🚨 **LỖI NGHIÊM TRỌNG** 🚨\n\n"
            f"Không thể đặt lệnh SL/TP cho vị thế **{side}** của cặp `{symbol}`.\n\n"
            f"**VỊ THẾ CÓ THỂ ĐANG MỞ MÀ KHÔNG ĐƯỢC BẢO VỆ!**\n\n"
            f"Vui lòng kiểm tra trên sàn ngay lập tức.\n\n"
            f"Lỗi: `{error_message}`"
        )
        return message

    def format_close_by_timeout_message(self, symbol, side, holding_hours):
        message = (
            f"⌛️ **ĐÓNG LỆNH DO HẾT HẠN** ⌛️\n\n"
            f"cặp tiền: `{symbol}`\n"
            f"hướng: **{side}**\n\n"
            f"Lệnh đã được đóng tự động do đã giữ quá thời gian cho phép ({holding_hours:.2f} giờ)."
        )
        return message 