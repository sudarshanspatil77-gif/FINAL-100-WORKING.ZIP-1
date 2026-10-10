
"""
NIFTY BOT - TREND HIGH-LOW FIB - RENDER ENV READY - AUTO TOKEN
 Works with config.json (local) AND ENV vars (Render)
"""
import os, sys, json, time, math, logging
from datetime import datetime
from dataclasses import dataclass
from typing import Dict, Any
import requests
import pandas as pd
import numpy as np

# SETTINGS
SYMBOLS = ["NIFTY"]
EMA_LENGTH = 20
FIB_ENTRY = 0.50
FIB_SL = 0.32
FIB_TARGET = 2.414
LOT_SIZE = 1
POLL_SECONDS = 30

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
ENABLE_LIVE_ORDER = os.getenv("ENABLE_LIVE_ORDER", "False").lower() == "true"

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

NIFTY_TOKEN = "99926000"
NIFTY_EXCH = "NSE"

obj = None
config = {}

def load_config():
    global config, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ENABLE_LIVE_ORDER
    # Try config.json first (local)
    try:
        with open("config.json","r") as f:
            config=json.load(f)
        print(f"[CONFIG] Loaded from config.json: {list(config.keys())}")
    except:
        print("[CONFIG] config.json not found - using ENV vars (Render)")
        config = {}
        # Load from ENV
        config["angel_api_key"] = os.getenv("ANGEL_API_KEY", "")
        config["angel_client_id"] = os.getenv("ANGEL_CLIENT_ID", "")
        config["angel_password"] = os.getenv("ANGEL_PASSWORD", "")
        config["angel_totp_secret"] = os.getenv("ANGEL_TOTP_SECRET", "")
        config["telegram_bot_token"] = os.getenv("TELEGRAM_BOT_TOKEN", "")
        config["telegram_chat_id"] = os.getenv("TELEGRAM_CHAT_ID", "")
        config["enable_live"] = os.getenv("ENABLE_LIVE_ORDER", "False")

    # Telegram from config or ENV
    if not TELEGRAM_BOT_TOKEN:
        TELEGRAM_BOT_TOKEN = config.get("telegram_bot_token") or config.get("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or ""
    if not TELEGRAM_CHAT_ID:
        TELEGRAM_CHAT_ID = config.get("telegram_chat_id") or config.get("TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID") or ""
    
    print(f"[TELEGRAM] Bot: {'YES' if TELEGRAM_BOT_TOKEN else 'NO'} | Live: {ENABLE_LIVE_ORDER}")

def get_key(cfg, *names):
    for n in names:
        if n in cfg and cfg[n]:
            return cfg[n]
        for k in cfg.keys():
            if k.lower() == n.lower() and cfg[k]:
                return cfg[k]
    # ENV fallback
    for n in names:
        v = os.getenv(n.upper())
        if v:
            return v
    return None

def angel_login():
    global obj
    api_key = get_key(config, "angel_api_key","api_key")
    client_id = get_key(config, "angel_client_id","client_id","client_code")
    password = get_key(config, "angel_password","password","pin")
    totp_secret = get_key(config, "angel_totp_secret","totp_secret","totp")
    
    if not api_key or not client_id or not password or not totp_secret:
        print("❌ Angel keys missing - Check ENV vars or config.json")
        print(f"API_KEY: {'YES' if api_key else 'NO'} | CLIENT: {'YES' if client_id else 'NO'} | PWD: {'YES' if password else 'NO'} | TOTP: {'YES' if totp_secret else 'NO'}")
        print("ENV check:", os.getenv("ANGEL_API_KEY")[:5]+"..." if os.getenv("ANGEL_API_KEY") else "NO ENV")
        # Don't exit on Render - wait for market
        time.sleep(10)
        return None
    try:
        from SmartApi import SmartConnect
        import pyotp
        totp = pyotp.TOTP(totp_secret).now()
        print(f"[LOGIN] TOTP {totp} | Client {client_id}")
        obj = SmartConnect(api_key=api_key)
        data = obj.generateSession(client_id, password, totp)
        if not data.get("status"):
            print(f"Login Failed: {data}")
            return None
        print(f"✅ Login OK: {data['data']['clientcode']} | JWT Auto Regenerated!")
        return obj
    except Exception as e:
        print(f"Login Error: {e}")
        import traceback; traceback.print_exc()
        return None

@dataclass
class Signal:
    symbol: str
    option_type: str
    reason: str
    underlying_price: float
    entry: float
    sl: float
    target: float
    fib_entry: float
    fib_sl: float
    fib_target: float
    timestamp: str
    strike: float = 0.0

def ema(series, length): return series.ewm(span=length, adjust=False).mean()

def add_indicators(df):
    df=df.copy()
    df["ema20"]=ema(df["close"], EMA_LENGTH)
    df["green"]=df["close"]>df["open"]
    df["red"]=df["close"]<df["open"]
    return df

def recent_swing(df, lookback=30):
    x=df.tail(lookback)
    high=float(x["high"].max())
    low=float(x["low"].min())
    return low, high

def make_signal(symbol, df1, df5):
    if df1 is None or df5 is None or len(df1)<EMA_LENGTH+5:
        return None
    df1=add_indicators(df1)
    df5=add_indicators(df5)
    underlying=float(df1.iloc[-1]["close"])
    low, high = recent_swing(df5, lookback=30)
    c5=df5.iloc[-1]
    bull_5m = c5["close"]>c5["ema20"] and c5["green"]
    bear_5m = c5["close"]<c5["ema20"] and c5["red"]
    
    # Fib levels
    rng = high - low
    if rng <=0:
        return None
    fib_50_bull = high - rng*FIB_ENTRY
    fib_32_bull = high - rng*FIB_SL
    fib_214_bull = high + rng*FIB_TARGET
    fib_50_bear = low + rng*FIB_ENTRY
    fib_32_bear = low + rng*FIB_SL
    fib_214_bear = low - rng*FIB_TARGET
    
    # Check proximity to Fib 0.5 (within 0.2%)
    curr = df1.iloc[-1]["close"]
    if abs(curr - fib_50_bull)/curr < 0.002 and bull_5m:
        return Signal(symbol=symbol, option_type="CE", reason=f"BULL FIB 0.50 + EMA20 | High {high} Low {low}", underlying_price=underlying, entry=fib_50_bull, sl=fib_32_bull, target=fib_214_bull, fib_entry=FIB_ENTRY, fib_sl=FIB_SL, fib_target=FIB_TARGET, timestamp=datetime.now().isoformat())
    if abs(curr - fib_50_bear)/curr < 0.002 and bear_5m:
        return Signal(symbol=symbol, option_type="PE", reason=f"BEAR FIB 0.50 + EMA20 | High {high} Low {low}", underlying_price=underlying, entry=fib_50_bear, sl=fib_32_bear, target=fib_214_bear, fib_entry=FIB_ENTRY, fib_sl=FIB_SL, fib_target=FIB_TARGET, timestamp=datetime.now().isoformat())
    return None

def get_candle_df(exch, token, interval, days=1):
    global obj
    if obj is None:
        return None
    try:
        from datetime import timedelta
        to_date = datetime.now()
        from_date = to_date - timedelta(days=days)
        historicParam = {"exchange": exch, "symboltoken": token, "interval": interval, "fromdate": from_date.strftime("%Y-%m-%d %H:%M"), "todate": to_date.strftime("%Y-%m-%d %H:%M")}
        resp = obj.getCandleData(historicParam)
        if not resp or not resp.get("data"):
            return None
        data=resp["data"]
        df=pd.DataFrame(data, columns=["timestamp","open","high","low","close","volume"])
        df["timestamp"]=pd.to_datetime(df["timestamp"])
        return df
    except Exception as e:
        logging.warning(f"Candle error {token}: {e}")
        return None

def get_market_data(symbol):
    if symbol=="NIFTY":
        exch=NIFTY_EXCH
        token=NIFTY_TOKEN
    else:
        exch="BSE"
        token="99919000"
    df1 = get_candle_df(exch, token, "ONE_MINUTE", 2)
    df5 = get_candle_df(exch, token, "FIVE_MINUTE", 3)
    if df1 is None or df5 is None:
        now=datetime.now()
        dummy=pd.DataFrame([{"timestamp":now,"open":22000,"high":22100,"low":21900,"close":22050,"volume":1000}]*50)
        return dummy, dummy
    return df1, df5

def telegram_send(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[TELEGRAM SKIP] {msg[:100]}")
        return False
    try:
        url=f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id":TELEGRAM_CHAT_ID,"text":msg}, timeout=10)
        return True
    except Exception as e:
        print(f"Telegram error: {e}")
        return False

def ask_confirmation(sig):
    msg=f"🚨 SIGNAL\n{ sig.symbol } BUY { sig.option_type }\nStrike: { sig.strike }\nUnderlying: { sig.underlying_price }\nEntry Fib 0.50: { sig.entry:.2f }\nSL Fib 0.32: { sig.sl:.2f }\nTarget Fib 2.14: { sig.target:.2f }\n\nReason: { sig.reason }\nTime: { sig.timestamp }"
    telegram_send(msg)
    # On Render - auto approve for paper, no input
    if os.getenv("PORT"):
        print("[RENDER] Auto PAPER - No input needed")
        return True
    ans=input(f"{sig.symbol} {sig.option_type} - YES/NO: ").strip().upper()
    return ans in ("YES","Y")

last_key=None

def run_once():
    global last_key, obj
    if obj is None:
        obj = angel_login()
        if obj is None:
            print("[WAIT] Login failed - retry in 60s - Market closed?")
            time.sleep(60)
            return
    for sym in SYMBOLS:
        try:
            df1, df5 = get_market_data(sym)
            sig = make_signal(sym, df1, df5)
            if sig is None:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] {sym} Scanning... No Fib 0.50")
                continue
            sig.strike = round(sig.underlying_price/50)*50
            key=(sig.symbol, sig.option_type, sig.timestamp[:16])
            if key==last_key:
                continue
            last_key=key
            print(f"✅ SIGNAL: {sig.symbol} {sig.option_type} Entry {sig.entry:.2f} SL {sig.sl:.2f} Target {sig.target:.2f}")
            telegram_send(f"✅ CONFIRMED\n{sym} BUY {sig.option_type} Strike {sig.strike}\nEntry {sig.entry:.2f} SL {sig.sl:.2f} Target {sig.target:.2f}")
        except Exception as e:
            logging.exception(f"{sym} error: {e}")

def main():
    global obj
    load_config()
    print("="*60)
    print("NIFTY BOT - TREND HIGH-LOW FIB 0.50 - RENDER ENV READY")
    print("="*60)
    obj=angel_login()
    while True:
        run_once()
        time.sleep(POLL_SECONDS)

if __name__=="__main__":
    main()
