import time
import pandas as pd
from binance.client import Client
from binance.enums import *
from ta.volatility import BollingerBands
from ta.trend import EMAIndicator, SMAIndicator, ADXIndicator
from ta.momentum import RSIIndicator
from dotenv import load_dotenv
import os

# 🛠️ Thêm dòng này để định nghĩa loại lệnh trailing stop
ORDER_TYPE_TRAILING_STOP_MARKET = "TRAILING_STOP_MARKET"

# Load API từ .env
load_dotenv()
api_key = os.getenv('API_KEY')
api_secret = os.getenv('API_SECRET')

client = Client(api_key, api_secret, testnet=True)


symbol = "BTCUSDT"
interval = Client.KLINE_INTERVAL_5MINUTE
INTERVAL_SECONDS = 5 * 60

capital_per_trade = 10
leverage = 20
risk_pct = 0.10
tp_pct = 0.06
sl_pct = 0.02
trailing_start = 0.015
trailing_buffer = 0.0075
max_open_trades = 10

tick_size = 0.1
step_size = 0.001

open_positions = []

# ===== HÀM HỖ TRỢ =====
def round_step(value, step):
    return round(round(value / step) * step, int(abs(math.log10(step))))

def get_klines(symbol, interval, limit=250):
    klines = client.get_klines(symbol=symbol, interval=interval, limit=limit)
    df = pd.DataFrame(klines, columns=[
        'timestamp', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_asset_volume', 'number_of_trades',
        'taker_buy_base_volume', 'taker_buy_quote_volume', 'ignore'
    ])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df[['open', 'high', 'low', 'close', 'volume']] = df[['open', 'high', 'low', 'close', 'volume']].astype(float)
    return df

def calculate_indicators(df):
    df['ema20'] = df['close'].ewm(span=20).mean()
    df['rsi'] = compute_rsi(df['close'], 14)
    df['upper'], df['middle'], df['lower'] = bbands(df['close'], 20, 2)
    return df

def compute_rsi(series, period):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def bbands(price, period=20, num_std=2):
    sma = price.rolling(window=period).mean()
    std = price.rolling(window=period).std()
    upper = sma + (std * num_std)
    lower = sma - (std * num_std)
    return upper, sma, lower

def check_conditions(df):
    last = df.iloc[-1]
    prev = df.iloc[-2]

    long_cond = (
        last['close'] > last['upper'] and
        last['rsi'] > 60 and
        last['close'] > last['ema20'] and
        last['close'] > prev['high']
    )
    short_cond = (
        last['close'] < last['lower'] and
        last['rsi'] < 40 and
        last['close'] < last['ema20'] and
        last['close'] < prev['low']
    )
    return long_cond, short_cond

def calculate_quantity(price):
    usdt_amount = capital_per_trade
    return round_step(usdt_amount * leverage / price, step_size)

def get_open_position_count(symbol):
    positions = client.futures_position_information(symbol=symbol)
    for pos in positions:
        if float(pos['positionAmt']) != 0:
            return 1
    return 0

def place_order(direction, entry_price):
    quantity = calculate_quantity(entry_price)
    side = SIDE_BUY if direction == "LONG" else SIDE_SELL
    opposite_side = SIDE_SELL if direction == "LONG" else SIDE_BUY

    print(f"[ORDER] {direction} | Entry: {entry_price}, Qty: {quantity}")

    client.futures_create_order(
        symbol=symbol,
        side=side,
        type=ORDER_TYPE_MARKET,
        quantity=quantity
    )

    open_positions.append({
        "entry_time": int(time.time()),
        "entry_price": entry_price,
        "direction": direction,
        "quantity": quantity
    })

    trailing_stop_callback = round(trailing_buffer * 100, 1)
    activation_price = entry_price * (1 + trailing_start) if direction == "LONG" else entry_price * (1 - trailing_start)
    activation_price = round_step(activation_price, tick_size)

    client.futures_create_order(
        symbol=symbol,
        side=opposite_side,
        type=ORDER_TYPE_TRAILING_STOP_MARKET,
        quantity=quantity,
        activationPrice=activation_price,
        callbackRate=trailing_stop_callback,
        timeInForce=TIME_IN_FORCE_GTC,
        reduceOnly=True
    )

    tp_price = entry_price * (1 + tp_pct) if direction == "LONG" else entry_price * (1 - tp_pct)
    tp_price = round_step(tp_price, tick_size)

    client.futures_create_order(
        symbol=symbol,
        side=opposite_side,
        type=ORDER_TYPE_LIMIT,
        price=tp_price,
        quantity=quantity,
        timeInForce=TIME_IN_FORCE_GTC,
        reduceOnly=True
    )

def check_timeout_positions():
    global open_positions
    now = int(time.time())
    still_open = []

    for pos in open_positions:
        elapsed = now - pos["entry_time"]
        if elapsed > 15 * INTERVAL_SECONDS:
            print(f"⏰ Đóng lệnh sau 15 nến: {pos['direction']} @ {pos['entry_price']} (sau {elapsed//60} phút)")
            side = SIDE_SELL if pos["direction"] == "LONG" else SIDE_BUY
            try:
                client.futures_create_order(
                    symbol=symbol,
                    side=side,
                    type=ORDER_TYPE_MARKET,
                    quantity=pos["quantity"],
                    reduceOnly=True
                )
            except Exception as e:
                print(f"❌ Lỗi khi đóng lệnh quá hạn: {e}")
        else:
            still_open.append(pos)

    open_positions = still_open

# ===== VÒNG LẶP CHÍNH =====
while True:
    try:
        df = get_klines(symbol, interval)
        df = calculate_indicators(df)
        long_cond, short_cond = check_conditions(df)
        current_price = df['close'].iloc[-1]

        check_timeout_positions()

        if get_open_position_count(symbol) < max_open_trades:
            if long_cond:
                print("🚀 Tín hiệu LONG hợp lệ")
                place_order("LONG", current_price)
            elif short_cond:
                print("🔻 Tín hiệu SHORT hợp lệ")
                place_order("SHORT", current_price)
            else:
                print("⏸️ Không có tín hiệu đủ điều kiện.")
        else:
            print(f"⚠️ Đã đủ số lượng lệnh ({max_open_trades})")

    except Exception as e:
        print("❌ Lỗi:", e)

    now = int(time.time())
    wait_time = INTERVAL_SECONDS - (now % INTERVAL_SECONDS) + 1
    print(f"⏳ Chờ đến mốc nến tiếp theo ({wait_time} giây)...")
    time.sleep(wait_time)
