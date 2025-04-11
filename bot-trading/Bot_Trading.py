import time
import pandas as pd
from binance.client import Client
from binance.enums import *
from ta.volatility import BollingerBands
from ta.trend import EMAIndicator, SMAIndicator, ADXIndicator
from ta.momentum import RSIIndicator
from dotenv import load_dotenv
from decimal import Decimal, ROUND_DOWN
import os

ORDER_TYPE_TRAILING_STOP_MARKET = "TRAILING_STOP_MARKET"

# Load API từ .env
load_dotenv()
api_key = os.getenv('API_KEY')
api_secret = os.getenv('API_SECRET')
client = Client(api_key, api_secret, testnet=True)

# === Cấu hình chiến lược ===
symbol = "BTCUSDT"
interval = "5m"
INTERVAL_SECONDS = 300
SLEEP_BUFFER = 5
initial_capital = 10
leverage = 20
risk_percent = 10
max_open_trades = 10
sl_pct = 0.02
tp_pct = 0.06
trailing_start = 0.015
trailing_buffer = 0.0075

# === Lấy thông tin precision ===
def get_symbol_precision(symbol):
    info = client.futures_exchange_info()
    for s in info['symbols']:
        if s['symbol'] == symbol:
            for f in s['filters']:
                if f['filterType'] == 'PRICE_FILTER':
                    tick_size = float(f['tickSize'])
                if f['filterType'] == 'LOT_SIZE':
                    step_size = float(f['stepSize'])
            return tick_size, step_size
    return 0.01, 0.001  # fallback

tick_size, step_size = get_symbol_precision(symbol)

def round_step(value, step):
    return float((Decimal(str(value)).quantize(Decimal(str(step)), rounding=ROUND_DOWN)))

# === Lấy dữ liệu nến ===
def get_klines(symbol, interval, limit=100):
    data = client.futures_klines(symbol=symbol, interval=interval, limit=limit)
    df = pd.DataFrame(data, columns=[
        'time', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_asset_volume', 'number_of_trades',
        'taker_buy_base_volume', 'taker_buy_quote_volume', 'ignore'
    ])
    df['close'] = df['close'].astype(float)
    df['open'] = df['open'].astype(float)
    df['high'] = df['high'].astype(float)
    df['low'] = df['low'].astype(float)
    df['volume'] = df['volume'].astype(float)
    return df

# === Tính toán indicator ===
def calculate_indicators(df):
    df['ma200'] = SMAIndicator(df['close'], window=200).sma_indicator()
    df['ma50'] = SMAIndicator(df['close'], window=50).sma_indicator()
    df['ema20'] = EMAIndicator(df['close'], window=20).ema_indicator()
    df['ema100'] = EMAIndicator(df['close'], window=100).ema_indicator()
    df['rsi'] = RSIIndicator(df['close'], window=14).rsi()
    bb = BollingerBands(df['close'], window=14, window_dev=2)
    df['bb_upper'] = bb.bollinger_hband()
    df['bb_lower'] = bb.bollinger_lband()
    adx = ADXIndicator(df['high'], df['low'], df['close'], window=14)
    df['adx'] = adx.adx()
    df['plus_di'] = adx.adx_pos()
    df['minus_di'] = adx.adx_neg()
    return df

# === Tính khối lượng giao dịch ===
def calculate_quantity(entry_price):
    trade_risk = initial_capital * (risk_percent / 100)
    position_size = trade_risk / sl_pct
    quantity = position_size * leverage / entry_price
    return round_step(quantity, step_size)

# === Kiểm tra lệnh đang mở ===
def get_open_position_count(symbol):
    positions = client.futures_position_information(symbol=symbol)
    for p in positions:
        if p['symbol'] == symbol:
            amt = float(p['positionAmt'])
            return 1 if amt != 0 else 0
    return 0

# === Kiểm tra điều kiện vào lệnh ===
def check_conditions(df):
    if len(df) < 200:
        print(f"❗ Không đủ dữ liệu (hiện tại chỉ có {len(df)} dòng).")
        return False, False

    latest = df.iloc[-1]
    previous = df.iloc[-2]

    candle_bullish = latest['close'] > latest['open'] and previous['close'] < previous['open']
    candle_bearish = latest['close'] < latest['open'] and previous['close'] > previous['open']
    super_volume = latest['volume'] > df['volume'].rolling(20).mean().iloc[-1] * 1.0
    adx_filter = latest['adx'] > 10
    trend_up = latest['close'] > latest['ma200']
    trend_down = latest['close'] < latest['ma200']

    breakout_up = latest['close'] > latest['bb_upper'] and candle_bullish and super_volume and adx_filter
    breakout_down = latest['close'] < latest['bb_lower'] and candle_bearish and super_volume and adx_filter
    ema_cross_up = latest['ema20'] > latest['ema100'] and df['ema20'].iloc[-2] < df['ema100'].iloc[-2] and latest['rsi'] > 50
    ema_cross_down = latest['ema20'] < latest['ema100'] and df['ema20'].iloc[-2] > df['ema100'].iloc[-2] and latest['rsi'] < 50
    rsi_extreme_long = latest['rsi'] < 40 and trend_up
    rsi_extreme_short = latest['rsi'] > 60 and trend_down

    long_condition = breakout_up or ema_cross_up or rsi_extreme_long
    short_condition = breakout_down or ema_cross_down or rsi_extreme_short

    print(f"\n📊 Close: {latest['close']}, BB_up: {latest['bb_upper']}, BB_low: {latest['bb_lower']}")
    print(f"📈 breakout_up: {breakout_up}, breakout_down: {breakout_down}")
    print(f"💥 super_volume: {super_volume}, adx_filter: {adx_filter}")
    print(f"📊 EMA cross up/down: {ema_cross_up}/{ema_cross_down}")
    print(f"💡 RSI: {latest['rsi']} | RSI long: {rsi_extreme_long} | RSI short: {rsi_extreme_short}")
    print(f"============================")

    return long_condition, short_condition

# === Vòng lặp kiểm tra tín hiệu (không vào lệnh) ===
while True:
    try:
        df = get_klines(symbol, interval, limit=250)
        df = calculate_indicators(df)
        long_cond, short_cond = check_conditions(df)
        current_price = df['close'].iloc[-1]
        open_trades = get_open_position_count(symbol)

        if open_trades < max_open_trades:
            if long_cond:
                print(f"🚨 [Tín hiệu] GỢI Ý MỞ LỆNH LONG tại {current_price}")
            elif short_cond:
                print(f"🚨 [Tín hiệu] GỢI Ý MỞ LỆNH SHORT tại {current_price}")
            else:
                print("⏸️ Không có tín hiệu đủ điều kiện.")
        else:
            print(f"⚠️ Đã đạt số lượng lệnh tối đa ({open_trades}/{max_open_trades})")

    except Exception as e:
        print("❌ Lỗi:", e)

    now = int(time.time())
    wait_time = INTERVAL_SECONDS - (now % INTERVAL_SECONDS) + 1
    print(f"⏳ Chờ đến mốc nến 5 phút tiếp theo ({wait_time} giây)...")
    time.sleep(wait_time)
