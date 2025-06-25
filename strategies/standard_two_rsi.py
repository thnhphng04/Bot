from ta.momentum import RSIIndicator
from ta.volatility import AverageTrueRange
import pandas as pd

class StandardTwoRSI:
    def __init__(self, symbol, long_params, short_params):
        self.symbol = symbol
        self.long_enable = long_params.get("enabled", False)
        self.short_enable = short_params.get("enabled", False)
        self.long_params = long_params.get('params',{})
        self.short_params = short_params.get('params',{})

    def generate_signal(self, df: pd.DataFrame):
        # --- Tính toán chỉ báo chung ---
        

        # --- Logic cho Lệnh Long ---
        if self.long_enable:
            

            lp = self.long_params
            fast_rsi = RSIIndicator(df['close'], window=lp['fast_RSI_window']).rsi()
            slow_rsi = RSIIndicator(df['close'], window=lp['slow_RSI_window']).rsi()
            atr = AverageTrueRange(df['high'], df['low'], df['close'], window=lp['fast_RSI_window']).average_true_range()

            fast_prev = fast_rsi.iloc[-2]
            fast_curr = fast_rsi.iloc[-1]
            slow_curr = slow_rsi.iloc[-1]

            #print(self.symbol, fast_prev, fast_curr, slow_curr)
            
            if fast_prev < lp['fast_RSI_threshold'] and fast_curr > lp['fast_RSI_threshold'] and slow_curr > lp['slow_RSI_threshold']:
                close = df['close'].iloc[-1]
                atr_val = atr.iloc[-1]
                stop_loss = close - atr_val * lp['atr_multiplier']
                take_profit = close + lp['tp_sl_ratio'] * (close - stop_loss)
                return {
                    "signal": "LONG",
                    "entry_price": close,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                }
            #else:
                #print(self.symbol, ":Không đủ điều kiện long")

        # --- Logic cho Lệnh Short ---
        if self.short_enable:
            


            sp = self.short_params
            fast_rsi = RSIIndicator(df['close'], window=sp['fast_RSI_window']).rsi()
            slow_rsi = RSIIndicator(df['close'], window=sp['slow_RSI_window']).rsi()
            atr = AverageTrueRange(df['high'], df['low'], df['close'], window=sp['fast_RSI_window']).average_true_range()

            fast_prev = fast_rsi.iloc[-2]
            fast_curr = fast_rsi.iloc[-1]
            slow_curr = slow_rsi.iloc[-1]

            #print(self.symbol , fast_prev, fast_curr, slow_curr)

            # Điều kiện vào lệnh short: RSI nhanh cắt xuống dưới ngưỡng từ trên, và RSI chậm dưới ngưỡng
            if fast_prev > sp['fast_RSI_threshold'] and fast_curr < sp['fast_RSI_threshold'] and slow_curr < sp['slow_RSI_threshold']:
                close = df['close'].iloc[-1]
                atr_val = atr.iloc[-1]
                stop_loss = close + atr_val * sp['atr_multiplier']
                take_profit = close - sp['tp_sl_ratio'] * (stop_loss - close)
                return {
                    "signal": "SHORT",
                    "entry_price": close,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                }
            #else:
                #print(self.symbol, ": Không đủ điều kiện short")
        
        return {"signal": "NONE"} 