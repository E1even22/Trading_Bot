import time
import pandas as pd
from binance.client import Client
from binance.enums import *
from ta.volatility import BollingerBands
from ta.trend import EMAIndicator, SMAIndicator, ADXIndicator
from ta.momentum import RSIIndicator
from dotenv import load_dotenv
from decimal import Decimal
from binance.um_futures import UMFutures
import os

# 🛠️ Thêm dòng này để định nghĩa loại lệnh trailing stop
ORDER_TYPE_TRAILING_STOP_MARKET = "TRAILING_STOP_MARKET"

# Load API từ .env
load_dotenv()
api_key = os.getenv('API_KEY')
api_secret = os.getenv('API_SECRET')

symbol = "BTCUSDT"
interval = "5m"
leverage = 20
risk = 0.10
max_open_trades = 10
capital_per_trade = 10

# ==== Kết nối API ====
client = UMFutures(api_key=api_key, api_secret=api_secret)
client.change_leverage(symbol=symbol, leverage=leverage)
twm = ThreadedWebsocketManager(api_key=api_key, api_secret=api_secret)
twm.start()

# ==== Tính chỉ báo kỹ thuật ====
def calculate_indicators(df):
    df['ema'] = EMAIndicator(df['close'], window=20).ema_indicator()
    bb = BollingerBands(df['close'], window=20, window_dev=2)
    df['bb_upper'] = bb.bollinger_hband()
    df['bb_lower'] = bb.bollinger_lband()
    df['rsi'] = RSIIndicator(df['close'], window=14).rsi()
    df['adx'] = ADXIndicator(df['high'], df['low'], df['close'], window=14).adx()
    df['volume_sma'] = df['volume'].rolling(window=20).mean()
    return df

# ==== Kiểm tra điều kiện vào lệnh ====
def check_conditions(df):
    c = df.iloc[-1]
    prev = df.iloc[-2]
    cond_long = (
        c['close'] > c['bb_upper'] and
        c['close'] > c['ema'] and
        c['rsi'] > 60 and
        c['adx'] > 25 and
        c['volume'] > c['volume_sma']
    )
    cond_short = (
        c['close'] < c['bb_lower'] and
        c['close'] < c['ema'] and
        c['rsi'] < 40 and
        c['adx'] > 25 and
        c['volume'] > c['volume_sma']
    )
    return cond_long, cond_short

# ==== Gửi lệnh ====
def place_order(side, entry_price):
    qty = round((capital_per_trade * leverage) / entry_price, 3)
    order_side = "BUY" if side == "LONG" else "SELL"
    client.new_order(symbol=symbol, side=order_side, type="MARKET", quantity=qty)
    print(f"🟢 Đã vào lệnh {side} với {qty} {symbol} tại giá {entry_price}")

# ==== Kiểm tra số lệnh đang mở ====
def get_open_position_count():
    positions = client.get_position_risk(symbol=symbol)
    amt = float(positions[0]['positionAmt'])
    return 1 if amt != 0 else 0

# ==== Lưu trữ dữ liệu ====
df_klines = pd.DataFrame()

# ==== Xử lý WebSocket ====
def handle_socket(msg):
    global df_klines
    if msg['e'] != 'kline': return
    k = msg['k']
    if not k['x']: return  # Chỉ xử lý khi nến đã đóng

    new_row = {
        'time': pd.to_datetime(k['t'], unit='ms'),
        'open': float(k['o']),
        'high': float(k['h']),
        'low': float(k['l']),
        'close': float(k['c']),
        'volume': float(k['v']),
    }

    df_klines.loc[len(df_klines)] = new_row
    if len(df_klines) > 100:
        df_klines = df_klines[-100:]

    if len(df_klines) >= 20:
        df_klines = calculate_indicators(df_klines)
        long_cond, short_cond = check_conditions(df_klines)
        open_trades = get_open_position_count()
        current_price = df_klines['close'].iloc[-1]

        if open_trades < max_open_trades:
            if long_cond:
                print("🚀 Tín hiệu vào LONG realtime")
                place_order("LONG", current_price)
            elif short_cond:
                print("🔻 Tín hiệu vào SHORT realtime")
                place_order("SHORT", current_price)

# ==== Bắt đầu WebSocket ====
twm.start_kline_socket(callback=handle_socket, symbol=symbol.lower(), interval=interval)

print("✅ Bot realtime đang chạy...")

# Chạy mãi mãi
while True:
    time.sleep(1)

