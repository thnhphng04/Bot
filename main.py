import asyncio
import json
import logging
import sys
from dotenv import load_dotenv
import os

from bot import TradingBot
from exchange import Exchange
from notifications import TelegramNotifier

# --- Cấu hình Logging ---
def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler("multi_symbol_trader.log", encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )

def load_config():
    try:
        with open("config.json") as f:
            return json.load(f)
    except FileNotFoundError:
        logging.error("Tệp config.json không tồn tại. Vui lòng tạo tệp cấu hình.")
        return None
    except json.JSONDecodeError:
        logging.error("Tệp config.json không đúng định dạng. Vui lòng kiểm tra lại.")
        return None

async def main():
    setup_logging()
    load_dotenv()
    
    config = load_config()
    if not config:
        return

    # Khởi tạo Notifier
    notifier = None
    if config.get("telegram", {}).get("enabled", False):
        token = config["telegram"].get("bot_token")
        chat_id = config["telegram"].get("chat_id")
        if "YOUR_TELEGRAM_BOT_TOKEN" in token or not token:
            logging.warning("Thông báo Telegram đã được bật nhưng token chưa được cấu hình. Sẽ bỏ qua.")
        else:
            notifier = TelegramNotifier(token, chat_id)
            logging.info("Đã khởi tạo Telegram Notifier.")

    # Lấy API keys từ biến môi trường nếu có, nếu không thì từ config
    api_key = os.getenv("API_KEY", config.get("api_key"))
    api_secret = os.getenv("API_SECRET", config.get("api_secret"))

    if not api_key or "YOUR_API_KEY" in api_key:
        logging.error("API Key không được cấu hình. Vui lòng đặt trong tệp .env hoặc config.json.")
        return

    try:
        exchange = Exchange(api_key, api_secret, notifier=notifier)
    except Exception as e:
        logging.error(f"Không thể khởi tạo Exchange. Vui lòng kiểm tra API keys và kết nối mạng. Lỗi: {e}")
        return

    tasks = []
    for pair_config in config.get("trading_pairs", []):
        if pair_config.get("enabled", False):
            bot = TradingBot(pair_config, config["risk_settings"], exchange, notifier)
            tasks.append(asyncio.create_task(bot.run()))
        else:
            logging.info(f"Bỏ qua bot cho {pair_config['symbol']} do đã bị tắt trong config.")
            
    if not tasks:
        logging.warning("Không có bot nào được kích hoạt trong config. Server sẽ thoát.")
        if notifier:
            notifier.send_message("Server đã thoát do không có bot nào được kích hoạt.", silent=True)
        return
        
    logging.info(f"Đang khởi chạy {len(tasks)} bot...")
    if notifier:
        notifier.send_message(f"✅ Server đã khởi động thành công với {len(tasks)} bot đang chạy.", silent=True)

    await asyncio.gather(*tasks)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Server đã được dừng bởi người dùng.") 