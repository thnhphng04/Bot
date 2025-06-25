import logging
import asyncio
import json
import importlib
import re
from datetime import datetime, timezone
import collections
import pandas as pd

def to_snake_case(name):
    """Converts a PascalCase string to snake_case."""
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()

class TradingBot:
    def __init__(self, pair_config, risk_config, exchange, notifier=None):
        self.pair_config = pair_config
        self.risk_config = risk_config
        self.exchange = exchange
        self.notifier = notifier
        self.symbol = pair_config['symbol']
        self.timeframe = pair_config['timeframe']
        
        # Quản lý trạng thái kép cho Long và Short
        self.long_position_open = False
        self.short_position_open = False
        self.position_data = {} # Sẽ chứa {'LONG': data, 'SHORT': data}

        # Tải chiến lược động từ config
        strategy_name = pair_config['strategy_name']
        long_params = self.pair_config.get('long', {})
        short_params = self.pair_config.get('short', {})
        
        strategy_module_name = to_snake_case(strategy_name)
        strategy_module = importlib.import_module(f"strategies.{strategy_module_name}")
        StrategyClass = getattr(strategy_module, strategy_name)
        self.strategy = StrategyClass(self.symbol, long_params, short_params)

        
        self.logger = logging.getLogger(self.symbol)
        handler = logging.StreamHandler()

        self.candle_queue = None  # Sẽ là deque chứa 400 nến gần nhất

    async def run(self):
        logging.info(f"Khởi chạy bot cho {self.symbol} ở CHẾ ĐỘ PHÒNG HỘ (Hedge Mode).")
        await self.exchange.set_leverage(self.symbol, self.pair_config['leverage'])
        self._load_state()

        # Lấy 400 nến gần nhất khi khởi tạo
        df_init = await self.exchange.fetch_ohlcv(self.symbol, self.timeframe, limit=400)
        if df_init is None or df_init.empty or len(df_init) < 400:
            logging.error(f"[{self.symbol}] Không lấy đủ 400 nến khi khởi tạo, dừng bot!")
            return
        self.candle_queue = collections.deque(df_init.to_dict('records'), maxlen=400)

        while True:
            try:
                # 1. Kiểm tra và đóng các vị thế hết hạn hoặc đã đóng trên sàn
                await self._check_positions_status()
                
                # 2. Chờ nến mới
                await self._wait_for_next_candle()
                
                # 3. Lấy nến mới nhất
                df_new = await self.exchange.fetch_ohlcv(self.symbol, self.timeframe, limit=1)
                if df_new is None or df_new.empty:
                    logging.warning(f"[{self.symbol}] Không nhận được nến mới, bỏ qua chu kỳ.")
                    continue
                new_candle = df_new.iloc[-1].to_dict()
                # Nếu nến mới có timestamp trùng nến cuối queue thì bỏ qua (tránh duplicate)
                if self.candle_queue and new_candle['timestamp'] == self.candle_queue[-1]['timestamp']:
                    logging.info(f"[{self.symbol}] Nến mới trùng timestamp nến cuối queue, bỏ qua.")
                    continue
                self.candle_queue.append(new_candle)
                # Đảm bảo queue luôn đủ 400 nến
                if len(self.candle_queue) > 400:
                    self.candle_queue.popleft()
                # Convert queue về DataFrame để truyền vào strategy
                df = pd.DataFrame(list(self.candle_queue))
                signal_data = self.strategy.generate_signal(df)
                signal = signal_data.get("signal")

                # 4. Xử lý tín hiệu
                if signal == "LONG" and not self.long_position_open and self.pair_config.get('long', {}).get('enabled', False):
                    logging.info(f"[{self.symbol}] Nhận được tín hiệu LONG.")
                    await self._handle_signal(signal_data)
                
                elif signal == "SHORT" and not self.short_position_open and self.pair_config.get('short', {}).get('enabled', False):
                    logging.info(f"[{self.symbol}] Nhận được tín hiệu SHORT.")
                    await self._handle_signal(signal_data)

            except Exception as e:
                logging.error(f"[{self.symbol}] Lỗi trong vòng lặp chính của bot: {e}", exc_info=True)
                await asyncio.sleep(60)

    async def _handle_signal(self, signal_data):
        side = signal_data['signal'] # 'LONG' or 'SHORT'
        balance = await self.exchange.get_usdt_balance()
        if balance is None or balance <= 0:
            logging.warning(f"[{self.symbol}] Không thể vào lệnh do số dư không hợp lệ: {balance}")
            return

        risk_per_trade = balance * self.risk_config['value']
        entry_price = signal_data['entry_price']
        sl_price = signal_data['stop_loss']
        sl_distance_abs = abs(entry_price - sl_price)

        if sl_distance_abs == 0:
            logging.warning(f"[{self.symbol}] Khoảng cách Stop Loss bằng 0, hủy vào lệnh.")
            return

        # Thêm bộ lọc Fee/Risk Ratio
        sl_price_ratio = sl_distance_abs / entry_price
        fee_risk_ratio = 0.001 / sl_price_ratio # Giả sử phí là 0.1%
        if fee_risk_ratio > 0.2:
            logging.warning(f"[{self.symbol}] Hủy lệnh do tỷ lệ Phí/Rủi ro quá cao: {fee_risk_ratio:.2f}")
            return

        quantity = round(risk_per_trade / sl_distance_abs, 3)
        if quantity <= 0:
            logging.warning(f"[{self.symbol}] Số lượng tính toán không hợp lệ ({quantity}), hủy vào lệnh.")
            return

        # Đặt lệnh
        order_successful = await self.exchange.place_order(
            self.symbol, side, quantity, entry_price, sl_price, signal_data['take_profit']
        )

        if order_successful:
            entry_time = datetime.now(timezone.utc).isoformat()
            self._update_position_state(side, True, {"entry_time": entry_time, "entry_price": entry_price})
            logging.info(f"[{self.symbol}] Đã vào vị thế {side} thành công.")
            
            # Gửi thông báo sau khi xác nhận vào lệnh thành công
            if self.notifier:
                message = self.notifier.format_order_message(
                    self.symbol, side, quantity, entry_price, sl_price, signal_data['take_profit']
                )
                self.notifier.send_message(message)

    async def _check_positions_status(self):
        """Kiểm tra trạng thái các vị thế đang mở (nếu có), luôn đồng bộ với sàn."""
        for side in ["LONG", "SHORT"]:
            position_info = await self.exchange.get_open_position(self.symbol, side)
            if position_info is not None and float(position_info['positionAmt']) != 0:
                # Nếu phát hiện có vị thế mở trên sàn mà state chưa ghi nhận
                if side not in self.position_data:
                    entry_time = datetime.now(timezone.utc).isoformat()  # Không lấy được entry time thực tế, dùng thời điểm phát hiện
                    entry_price = float(position_info['entryPrice'])
                    self._update_position_state(side, True, {"entry_time": entry_time, "entry_price": entry_price})
                    logging.info(f"[{self.symbol}] Phát hiện vị thế {side} mở trên sàn, cập nhật lại state.")
                # Kiểm tra auto close
                entry_time_str = self.position_data.get(side, {}).get('entry_time')
                if not entry_time_str:
                    continue
                entry_time = datetime.fromisoformat(entry_time_str)
                now_ts = datetime.now(timezone.utc)
                holding_duration_hours = (now_ts - entry_time).total_seconds() / 3600
                side_config = self.pair_config.get(side.lower(), {})
                max_holding_hours = side_config.get('params', {}).get('max_position_duration_hours', 72)
                if holding_duration_hours > max_holding_hours:
                    logging.info(f"[{self.symbol}] Vị thế {side} đã vượt quá thời gian nắm giữ tối đa ({holding_duration_hours:.2f}/{max_holding_hours}h).")
                    quantity_to_close = position_info['positionAmt']
                    closed = await self.exchange.close_market_order(self.symbol, side, quantity_to_close)
                    if closed:
                        logging.info(f"[{self.symbol}] Đã đóng thành công vị thế {side}.")
                        self._update_position_state(side, False)
                        if self.notifier:
                            message = self.notifier.format_close_by_timeout_message(self.symbol, side, holding_duration_hours)
                            self.notifier.send_message(message)
                    else:
                        logging.error(f"[{self.symbol}] Thất bại khi cố gắng đóng vị thế {side} do hết hạn.")
            else:
                # Nếu không còn vị thế trên sàn, cập nhật lại state nếu cần
                if side in self.position_data:
                    self._update_position_state(side, False)

    def _update_position_state(self, side, is_open, data=None):
        if side == "LONG":
            self.long_position_open = is_open
        elif side == "SHORT":
            self.short_position_open = is_open
        
        if is_open:
            self.position_data[side] = data
        elif side in self.position_data:
            del self.position_data[side]
            
        self._save_state()
        logging.info(f"[{self.symbol}] Cập nhật trạng thái vị thế {side} thành: {'Mở' if is_open else 'Đóng'}")

    async def _wait_for_next_candle(self):
        # Đây là một cách đơn giản hóa để chờ nến mới.
        # Trong thực tế, có thể sử dụng websocket để có độ chính xác cao hơn.
        now = datetime.utcnow()
        timeframe_minutes = int(self.timeframe.replace('m', '').replace('h', '') * (60 if 'h' in self.timeframe else 1))
        
        minutes_to_wait = timeframe_minutes - (now.minute % timeframe_minutes)
        seconds_to_wait = minutes_to_wait * 60 - now.second + 5 # Chờ thêm 5s cho nến chắc chắn đóng
        
        logging.info(f"[{self.symbol}] Đang chờ {seconds_to_wait:.0f} giây cho nến tiếp theo...")
        await asyncio.sleep(seconds_to_wait)

    def _save_state(self):
        try:
            with open("state.json", 'r+') as f:
                all_states = json.load(f)
                all_states[self.symbol] = {
                    "long_position_open": self.long_position_open,
                    "short_position_open": self.short_position_open,
                    "position_data": self.position_data
                }
                f.seek(0)
                f.truncate()
                json.dump(all_states, f, indent=4)
        except Exception as e:
            logging.error(f"[{self.symbol}] Không thể lưu trạng thái: {e}")

    def _load_state(self):
        try:
            with open("state.json", 'r') as f:
                all_states = json.load(f)
                state = all_states.get(self.symbol)
                if state:
                    self.long_position_open = state.get('long_position_open', False)
                    self.short_position_open = state.get('short_position_open', False)
                    self.position_data = state.get('position_data', {})
                    logging.info(f"[{self.symbol}] Đã tải trạng thái: LONG Open={self.long_position_open}, SHORT Open={self.short_position_open}, Data={self.position_data}")
        except FileNotFoundError:
            logging.warning("Tệp state.json không tồn tại, bắt đầu với trạng thái mới.")
        except Exception as e:
            logging.error(f"[{self.symbol}] Không thể tải trạng thái: {e}") 