import logging
from binance.um_futures import UMFutures
from binance.error import ClientError
import pandas as pd
import asyncio
import time
from datetime import datetime, timedelta

class Exchange:
    def __init__(self, api_key, api_secret, notifier=None):
        self.notifier = notifier
        self.last_sync_time = None
        self.symbol_precisions = {}  # Lưu stepSize, tickSize, minQty, minPrice cho từng symbol
        try:
            # Khởi tạo client KHÔNG truyền recv_window
            self.client = UMFutures(
                key=api_key,
                secret=api_secret
            )
            # Đồng bộ thời gian với server Binance
            self._sync_time()
            # Kiểm tra kết nối bằng cách lấy thông tin tài khoản với recvWindow tăng
            self.client.account(recvWindow=60000)
            self._load_symbol_precisions()
            logging.info("Kết nối thành công đến Binance Futures.")
        except ClientError as e:
            logging.error(f"Lỗi kết nối đến Binance: {e.status_code} - {e.error_message}")
            raise
        except Exception as e:
            logging.error(f"Lỗi không xác định khi khởi tạo Exchange: {e}")
            raise

    def _sync_time(self):
        """Đồng bộ hóa thời gian với server Binance."""
        try:
            # Lấy thời gian server
            server_time = self.client.time()['serverTime']
            # Tính độ lệch thời gian
            local_time = int(time.time() * 1000)
            time_offset = server_time - local_time
            # Cập nhật timestamp offset trong client
            self.client.timestamp_offset = time_offset
            self.last_sync_time = datetime.now()
            logging.info(f"Đã đồng bộ thời gian với server Binance (offset: {time_offset}ms)")
        except Exception as e:
            logging.error(f"Lỗi khi đồng bộ thời gian với server Binance: {e}")
            raise

    def _check_time_sync(self):
        """Kiểm tra và đồng bộ lại thời gian nếu cần."""
        if self.last_sync_time is None or datetime.now() - self.last_sync_time > timedelta(hours=1):
            self._sync_time()

    def _load_symbol_precisions(self):
        """Lấy stepSize, tickSize cho tất cả symbol hỗ trợ và lưu vào dict."""
        try:
            info = self.client.exchange_info()
            for symbol_info in info['symbols']:
                symbol = symbol_info['symbol']
                step_size = None
                tick_size = None
                min_qty = None
                min_price = None
                for f in symbol_info['filters']:
                    if f['filterType'] == 'LOT_SIZE':
                        step_size = float(f['stepSize'])
                        min_qty = float(f['minQty'])
                    if f['filterType'] == 'PRICE_FILTER':
                        tick_size = float(f['tickSize'])
                        min_price = float(f['minPrice'])
                if step_size and tick_size:
                    self.symbol_precisions[symbol] = {
                        'step_size': step_size,
                        'tick_size': tick_size,
                        'min_qty': min_qty,
                        'min_price': min_price
                    }
            logging.info(f"Đã load precision cho {len(self.symbol_precisions)} symbols.")
        except Exception as e:
            logging.error(f"Lỗi khi lấy exchange info để lấy precision: {e}")
            raise

    def _adjust_to_step(self, value, step):
        """Làm tròn value xuống bội số của step."""
        import math
        return math.floor(float(value) / float(step)) * float(step)

    def _adjust_to_tick(self, value, tick):
        import math
        return math.floor(float(value) / float(tick)) * float(tick)

    async def get_usdt_balance(self):
        try:
            self._check_time_sync()
            loop = asyncio.get_event_loop()
            account_info = await loop.run_in_executor(
                None,
                lambda: self.client.account(recvWindow=60000)
            )
            for asset in account_info['assets']:
                if asset['asset'] == 'USDT':
                    return float(asset['walletBalance'])
            return 0.0
        except Exception as e:
            logging.error(f"Lỗi khi lấy số dư USDT: {e}")
            return None

    async def set_leverage(self, symbol, leverage):
        try:
            self._check_time_sync()
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.client.change_leverage(
                    symbol=symbol,
                    leverage=leverage,
                    recvWindow=60000
                )
            )
            logging.info(f"Đã đặt đòn bẩy {leverage}x cho {symbol}.")
        except Exception as e:
            logging.error(f"Lỗi khi đặt đòn bẩy cho {symbol}: {e}")

    async def fetch_ohlcv(self, symbol, timeframe, limit=100):
        try:
            self._check_time_sync()
            loop = asyncio.get_event_loop()
            klines = await loop.run_in_executor(
                None,
                lambda: self.client.klines(
                    symbol=symbol,
                    interval=timeframe,
                    limit=limit,
                    recvWindow=60000
                )
            )
            df = pd.DataFrame(klines, columns=[
                'timestamp','open','high','low','close','volume','close_time',
                'quote_asset_volume','num_trades','taker_buy_base','taker_buy_quote','ignore'
            ])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            for col in ['open','high','low','close','volume']:
                df[col] = pd.to_numeric(df[col])
            return df[['timestamp','open','high','low','close','volume']]
        except Exception as e:
            logging.error(f"Lỗi khi lấy dữ liệu klines cho {symbol}: {e}")
            return None

    async def place_order(self, symbol, side, quantity, entry_price, sl_price, tp_price):
        try:
            self._check_time_sync()
            # --- Lấy precision ---
            precisions = self.symbol_precisions.get(symbol, None)
            if not precisions:
                raise Exception(f"Không tìm thấy precision cho symbol {symbol}")
            step_size = precisions['step_size']
            tick_size = precisions['tick_size']

            def get_decimal_places(number):
                s = str(number)
                if '.' in s:
                    return len(s.split('.')[1].rstrip('0'))
                return 0

            step_decimals = get_decimal_places(step_size)
            tick_decimals = get_decimal_places(tick_size)

            # --- Làm tròn quantity và giá ---
            quantity = self._adjust_to_step(quantity, step_size)
            quantity = round(quantity, step_decimals)
            entry_price = self._adjust_to_tick(entry_price, tick_size)
            entry_price = round(entry_price, tick_decimals)
            sl_price = self._adjust_to_tick(sl_price, tick_size)
            sl_price = round(sl_price, tick_decimals)
            tp_price = self._adjust_to_tick(tp_price, tick_size)
            tp_price = round(tp_price, tick_decimals)
            logging.info(f"Chuẩn bị đặt lệnh {side} cho {symbol}: Qty={quantity}, Entry={entry_price}, SL={sl_price}, TP={tp_price}")
            loop = asyncio.get_event_loop()
            market_order_side = "BUY" if side == "LONG" else "SELL"
            await loop.run_in_executor(
                None,
                lambda: self.client.new_order(
                    symbol=symbol,
                    side=market_order_side,
                    type="MARKET",
                    quantity=quantity,
                    positionSide=side,
                    recvWindow=60000
                )
            )
            logging.info(f"Đã đặt lệnh MARKET {market_order_side} ({side}) thành công.")
            stop_order_side = "SELL" if side == "LONG" else "BUY"
            await loop.run_in_executor(
                None,
                lambda: self.client.new_order(
                    symbol=symbol,
                    side=stop_order_side,
                    type="TAKE_PROFIT_MARKET",
                    stopPrice=tp_price,
                    closePosition=True,
                    timeInForce="GTE_GTC",
                    positionSide=side,
                    recvWindow=60000
                )
            )
            logging.info(f"Đã đặt lệnh TAKE_PROFIT {stop_order_side} ({side}) thành công.")
            await loop.run_in_executor(
                None,
                lambda: self.client.new_order(
                    symbol=symbol,
                    side=stop_order_side,
                    type="STOP_MARKET",
                    stopPrice=sl_price,
                    closePosition=True,
                    timeInForce="GTE_GTC",
                    positionSide=side,
                    recvWindow=60000
                )
            )
            logging.info(f"Đã đặt lệnh STOP_MARKET {stop_order_side} ({side}) thành công.")
            return True
        except Exception as e:
            logging.error(f"Đã xảy ra lỗi khi đặt bộ lệnh cho vị thế {side} của {symbol}: {e}")
            if self.notifier:
                message = self.notifier.format_critical_error_message(symbol, side, str(e))
                self.notifier.send_message(message)
            return False

    async def get_open_position(self, symbol, side):
        try:
            self._check_time_sync()
            loop = asyncio.get_event_loop()
            positions = await loop.run_in_executor(
                None,
                lambda: self.client.get_position_risk(recvWindow=60000)
            )
            for position in positions:
                if position['symbol'] == symbol and position['positionSide'] == side.upper() and float(position['positionAmt']) != 0:
                    return position
            return None
        except Exception as e:
            logging.error(f"Lỗi khi kiểm tra vị thế cho {symbol} ({side}): {e}")
            return None
            
    async def close_market_order(self, symbol, side, quantity):
        try:
            self._check_time_sync()
            precisions = self.symbol_precisions.get(symbol, None)
            if not precisions:
                raise Exception(f"Không tìm thấy precision cho symbol {symbol}")
            step_size = precisions['step_size']
            quantity = self._adjust_to_step(abs(float(quantity)), step_size)
            loop = asyncio.get_event_loop()
            order_side = "SELL" if side.upper() == "LONG" else "BUY"
            await loop.run_in_executor(
                None,
                lambda: self.client.new_order(
                    symbol=symbol,
                    side=order_side,
                    type='MARKET',
                    quantity=quantity,
                    positionSide=side.upper(),
                    recvWindow=60000
                )
            )
            logging.info(f"Đã gửi lệnh MARKET để đóng vị thế {side} cho {symbol}.")
            await loop.run_in_executor(
                None,
                lambda: self.client.cancel_open_orders(
                    symbol=symbol,
                    recvWindow=60000
                )
            )
            logging.info(f"Đã hủy tất cả các lệnh chờ cho {symbol} sau khi đóng vị thế.")
            return True
        except ClientError as e:
            if e.error_code == -2022:
                logging.warning(f"Không thể đóng vị thế {side} cho {symbol} vì có thể nó đã được đóng (lỗi reduceOnly).")
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None,
                    lambda: self.client.cancel_open_orders(
                        symbol=symbol,
                        recvWindow=60000
                    )
                )
                return True
            else:
                logging.error(f"Lỗi ClientError khi đóng vị thế {side} cho {symbol}: {e}")
            return False
        except Exception as e:
            logging.error(f"Lỗi không xác định khi đóng vị thế {side} cho {symbol}: {e}")
            return False