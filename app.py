"""NIFTY BOT PRO - Support Reversal + Breakout Retest
Angel One SmartAPI. Live orders are disabled unless LIVE_TRADING_ENABLED=true.
Software-managed SL/target require this process to remain online.
"""
from __future__ import annotations
import os, json, time, logging, threading
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Optional, Dict, Any

import requests
import pandas as pd
from flask import Flask, jsonify
from dotenv import load_dotenv
from SmartApi import SmartConnect
import pyotp

load_dotenv()
TZ = ZoneInfo("Asia/Kolkata")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("niftybot")
# सर्व्हर सुरू होताच लगेच Telegram टेस्ट मेसेज पाठवण्यासाठी:
try:
    send_telegram_msg("🤖 System Check: Bot & Telegram Connected Successfully!")
except Exception as e:
    print(f"Telegram Init Error: {e}")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=port)

LIVE = os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "true"
PAPER = not LIVE
TIMEFRAME_MIN = int(os.getenv("TIMEFRAME_MIN", "5"))
RISK_PERCENT = float(os.getenv("RISK_PERCENT", "0.5"))
CAPITAL = float(os.getenv("ACCOUNT_CAPITAL_INR", "50000"))
DAILY_LOSS_LIMIT_PERCENT = float(os.getenv("DAILY_LOSS_LIMIT_PERCENT", "1.5"))
MAX_TRADES_PER_DAY = int(os.getenv("MAX_TRADES_PER_DAY", "3"))
RR = float(os.getenv("RISK_REWARD", "2.0"))
MAX_PREMIUM_INR = float(os.getenv("MAX_OPTION_PREMIUM", "500"))
MAX_SPREAD_PCT = float(os.getenv("MAX_SPREAD_PCT", "2.0"))
STATE_PATH = Path(os.getenv("BOT_STATE_PATH", "bot_state.json"))
MASTER_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
NIFTY_TOKEN = os.getenv("NIFTY_INDEX_TOKEN", "99926000")
NIFTY_SYMBOL = os.getenv("NIFTY_INDEX_SYMBOL", "Nifty 50")

api = None
api_lock = threading.RLock()
worker_started = False
last_status: Dict[str, Any] = {"started": False, "mode": "LIVE" if LIVE else "PAPER", "last_cycle": None, "last_error": None, "last_signal": None}


def now_ist() -> datetime:
    return datetime.now(TZ)


def read_state() -> dict:
    try:
        if STATE_PATH.exists():
            return json.loads(STATE_PATH.read_text())
    except Exception as e:
        log.exception("Could not read state file: %s", e)
    return {"date": str(now_ist().date()), "trades_today": 0, "realized_pnl": 0.0, "last_signal_key": None, "position": None, "paper_position": None}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str))
    tmp.replace(STATE_PATH)


def reset_day_if_needed(state: dict) -> dict:
    today = str(now_ist().date())
    if state.get("date") != today:
        state.update({"date": today, "trades_today": 0, "realized_pnl": 0.0, "last_signal_key": None})
    return state


def telegram(message: str) -> None:
    token, chat_id = os.getenv("TELEGRAM_BOT_TOKEN", ""), os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        log.info("Telegram not configured; message: %s", message)
        return
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat_id, "text": message}, timeout=10)
        if not r.ok:
            log.error("Telegram HTTP %s: %s", r.status_code, r.text[:300])
    except Exception:
        log.exception("Telegram send failed")


def angel_login():
    global api
    required = ["ANGEL_API_KEY", "ANGEL_CLIENT_CODE", "ANGEL_PIN", "ANGEL_TOTP_SECRET"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise RuntimeError("Missing environment variables: " + ", ".join(missing))
    with api_lock:
        obj = SmartConnect(api_key=os.environ["ANGEL_API_KEY"])
        otp = pyotp.TOTP(os.environ["ANGEL_TOTP_SECRET"]).now()
        result = obj.generateSession(os.environ["ANGEL_CLIENT_CODE"], os.environ["ANGEL_PIN"], otp)
        if not result or not result.get("status"):
            raise RuntimeError(f"Angel One login failed: {str(result)[:500]}")
        api = obj
        log.info("Angel One session login succeeded")
        telegram(f"NIFTY BOT: Angel One login successful. Mode={'LIVE' if LIVE else 'PAPER'}")
        return result


def ensure_api():
    if api is None:
        angel_login()
    return api


def get_candles() -> pd.DataFrame:
    client = ensure_api()
    end = now_ist()
    start = end - timedelta(days=5)
    params = {"exchange": "NSE", "symboltoken": NIFTY_TOKEN, "interval": "FIVE_MINUTE",
              "fromdate": start.strftime("%Y-%m-%d %H:%M"), "todate": end.strftime("%Y-%m-%d %H:%M")}
    with api_lock:
        result = client.getCandleData(params)
    if not result or not result.get("status") or not result.get("data"):
        raise RuntimeError(f"Angel One candle API failed: {str(result)[:500]}")
    df = pd.DataFrame(result["data"], columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna().drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    # Exclude the current, not-yet-closed candle. Timestamps may arrive without timezone.
    cutoff = now_ist().replace(tzinfo=None) - timedelta(minutes=TIMEFRAME_MIN)
    df = df[df["timestamp"] <= cutoff]
    if len(df) < 35:
        raise RuntimeError(f"Not enough closed candles from Angel One: {len(df)}")
    return df


def calculate_signal(df: pd.DataFrame) -> Optional[dict]:
    x = df.copy()
    x["ema20"] = x["close"].ewm(span=20, adjust=False).mean()
    x["volma20"] = x["volume"].rolling(20).mean()
    prev_support = x["low"].rolling(20).min().shift(1)
    prev_resistance = x["high"].rolling(20).max().shift(1)
    prev_close = x["close"].shift(1)
    tr = pd.concat([(x["high"]-x["low"]), (x["high"]-prev_close).abs(), (x["low"]-prev_close).abs()], axis=1).max(axis=1)
    x["atr14"] = tr.rolling(14).mean()
    i = len(x)-1
    cur, prev = x.iloc[i], x.iloc[i-1]
    support, resistance = prev_support.iloc[i], prev_resistance.iloc[i]
    if pd.isna(support) or pd.isna(resistance) or pd.isna(cur["volma20"]) or pd.isna(cur["atr14"]):
        return None
    bullish = cur["close"] > cur["open"]
    bearish = cur["close"] < cur["open"]
    volume_ok = cur["volume"] >= cur["volma20"]
    atr = float(cur["atr14"])
    # Bullish support reversal and resistance breakout-retest.
    support_reversal = cur["low"] <= support + 0.25*atr and cur["close"] > support and bullish and cur["close"] > cur["ema20"] and volume_ok
    prev_res = prev_resistance.iloc[i-1]
    breakout_retest = (not pd.isna(prev_res) and prev["close"] > prev_res and cur["low"] <= prev_res + 0.20*atr and cur["close"] > prev_res and bullish and cur["close"] > cur["ema20"] and volume_ok)
    # Bearish resistance rejection and support breakdown-retest generate PUT-buy signals.
    resistance_reversal = cur["high"] >= resistance - 0.25*atr and cur["close"] < resistance and bearish and cur["close"] < cur["ema20"] and volume_ok
    prev_sup = prev_support.iloc[i-1]
    breakdown_retest = (not pd.isna(prev_sup) and prev["close"] < prev_sup and cur["high"] >= prev_sup - 0.20*atr and cur["close"] < prev_sup and bearish and cur["close"] < cur["ema20"] and volume_ok)
    if support_reversal or breakout_retest:
        side = "CE"
        reason = "SUPPORT_REVERSAL" if support_reversal else "BREAKOUT_RETEST"
        entry = float(cur["close"])
        swing_low = float(x["low"].iloc[max(0, i-5):i+1].min())
        sl = min(swing_low, float(support)) - 0.05*atr
        risk = entry-sl
        if risk <= 0 or risk > entry*0.025: return None
        target = entry + RR*risk
    elif resistance_reversal or breakdown_retest:
        side = "PE"
        reason = "RESISTANCE_REVERSAL" if resistance_reversal else "BREAKDOWN_RETEST"
        entry = float(cur["close"])
        swing_high = float(x["high"].iloc[max(0, i-5):i+1].max())
        sl = max(swing_high, float(resistance)) + 0.05*atr
        risk = sl-entry
        if risk <= 0 or risk > entry*0.025: return None
        target = entry - RR*risk
    else:
        return None
    return {"side": side, "reason": reason, "timestamp": str(cur["timestamp"]), "index_entry": entry,
            "support": float(support), "resistance": float(resistance), "index_sl": sl, "index_target": target,
            "atr": atr, "ema20": float(cur["ema20"]), "volume": float(cur["volume"]),
            "volume_avg": float(cur["volma20"]), "signal_key": f"{cur['timestamp']}|{reason}"}


def get_ltp(exchange: str, symbol: str, token: str) -> float:
    client = ensure_api()
    with api_lock:
        result = client.ltpData(exchange, symbol, str(token))
    if not result or not result.get("status") or not result.get("data") or result["data"].get("ltp") is None:
        raise RuntimeError(f"LTP failed for {symbol}: {str(result)[:300]}")
    return float(result["data"]["ltp"])


def get_option_contract(spot: float, side: str = "CE") -> dict:
    """Choose nearest-expiry ATM NIFTY CE for bullish signal. Verify master schema at runtime."""
    r = requests.get(MASTER_URL, timeout=20)
    r.raise_for_status()
    data = r.json()
    df = pd.DataFrame(data)
    needed = {"name", "exch_seg", "instrumenttype", "symbol", "token", "expiry", "strike", "lotsize"}
    if not needed.issubset(df.columns):
        raise RuntimeError(f"Angel One instrument master schema changed. Columns: {list(df.columns)[:30]}")
    df = df[(df["name"] == "NIFTY") & (df["exch_seg"] == "NFO") & (df["instrumenttype"] == "OPTIDX") & df["symbol"].astype(str).str.endswith(side)].copy()
    today = now_ist().date()
    df["expiry_dt"] = pd.to_datetime(df["expiry"], format="%d%b%Y", errors="coerce").dt.date
    df["strike_num"] = pd.to_numeric(df["strike"], errors="coerce") / 100.0
    df["lotsize_num"] = pd.to_numeric(df["lotsize"], errors="coerce")
    df = df[df["expiry_dt"].notna() & (df["expiry_dt"] >= today) & df["strike_num"].notna()]
    if df.empty:
        raise RuntimeError("No valid NIFTY CE contracts in Angel One instrument master")
    expiry = min(df["expiry_dt"])
    day = df[df["expiry_dt"] == expiry].copy()
    day["distance"] = (day["strike_num"] - spot).abs()
    row = day.sort_values("distance").iloc[0]
    return {"symbol": str(row["symbol"]), "token": str(row["token"]), "expiry": str(row["expiry"]),
            "strike": float(row["strike_num"]), "lot_size": int(row["lotsize_num"])}


def place_market_order(contract: dict, transaction_type: str, quantity: int) -> dict:
    if not LIVE:
        return {"paper": True, "status": "SIMULATED", "symbol": contract["symbol"], "transaction_type": transaction_type, "quantity": quantity}
    client = ensure_api()
    params = {"variety": "NORMAL", "tradingsymbol": contract["symbol"], "symboltoken": contract["token"],
              "transactiontype": transaction_type, "exchange": "NFO", "ordertype": "MARKET", "producttype": "INTRADAY",
              "duration": "DAY", "price": "0", "squareoff": "0", "stoploss": "0", "quantity": str(quantity)}
    with api_lock:
        result = client.placeOrder(params)
    if not result:
        raise RuntimeError("Angel One placeOrder returned empty response")
    # SmartAPI versions differ: successful response may be a string order ID or dict.
    log.info("Order response: %s", str(result)[:500])
    return {"response": result, "symbol": contract["symbol"], "transaction_type": transaction_type, "quantity": quantity}


def within_session() -> bool:
    n = now_ist()
    return n.weekday() < 5 and (n.hour, n.minute) >= (9, 20) and (n.hour, n.minute) <= (15, 10)


def open_trade(signal: dict, state: dict) -> None:
    contract = get_option_contract(signal["index_entry"], signal["side"])
    premium = get_ltp("NFO", contract["symbol"], contract["token"])
    if premium <= 0 or premium > MAX_PREMIUM_INR:
        raise RuntimeError(f"Option premium ₹{premium:.2f} outside configured limit ₹{MAX_PREMIUM_INR:.2f}; no trade")
    # Premium-based SL/target are derived from option premium because index points do not map linearly to option price.
    premium_sl = round(premium * 0.80, 2)
    premium_target = round(premium + (premium-premium_sl)*RR, 2)
    risk_per_unit = premium-premium_sl
    risk_budget = CAPITAL * RISK_PERCENT / 100.0
    lots_by_risk = int(risk_budget // (risk_per_unit * contract["lot_size"])) if risk_per_unit > 0 else 0
    lots_by_capital = int(CAPITAL // (premium * contract["lot_size"])) if premium > 0 else 0
    lots = min(lots_by_risk, lots_by_capital)
    if lots < 1:
        raise RuntimeError(f"Risk/capital too small for one lot. Premium={premium}, lot_size={contract['lot_size']}, risk budget={risk_budget:.2f}")
    qty = lots * contract["lot_size"]
    order = place_market_order(contract, "BUY", qty)
    position = {"contract": contract, "quantity": qty, "lots": lots, "entry_premium": premium,
                "stop_premium": premium_sl, "initial_stop_premium": premium_sl, "target_premium": premium_target,
                "entry_time": str(now_ist()), "signal": signal, "order": order, "high_water": premium,
                "status": "OPEN", "paper": PAPER}
    key = "paper_position" if PAPER else "position"
    state[key] = position
    state["trades_today"] = int(state.get("trades_today", 0)) + 1
    state["last_signal_key"] = signal["signal_key"]
    save_state(state)
    msg = (f"NIFTY BOT { 'PAPER' if PAPER else 'LIVE' } BUY {signal['side']}\n{signal['reason']}\nOption: {contract['symbol']}\n"
           f"Qty: {qty} ({lots} lot(s))\nPremium: ₹{premium:.2f}\nSL: ₹{premium_sl:.2f}\nTarget: ₹{premium_target:.2f}\n"
           f"Index close: {signal['index_entry']:.2f}\nOrder: {str(order)[:250]}")
    telegram(msg)
    log.info(msg.replace("\n", " | "))


def manage_position(state: dict, key: str) -> None:
    pos = state.get(key)
    if not pos or pos.get("status") != "OPEN":
        return
    c = pos["contract"]
    ltp = get_ltp("NFO", c["symbol"], c["token"])
    pos["high_water"] = max(float(pos.get("high_water", ltp)), ltp)
    # Premium trailing stop: trail upward by 20% from high-water; never loosen the stop.
    trailing = round(float(pos["high_water"]) * 0.80, 2)
    pos["stop_premium"] = max(float(pos["stop_premium"]), trailing)
    exit_reason = None
    if ltp <= float(pos["stop_premium"]): exit_reason = "TRAILING_SL"
    elif ltp >= float(pos["target_premium"]): exit_reason = "FIB/RR_TARGET"
    if exit_reason:
        order = place_market_order(c, "SELL", int(pos["quantity"]))
        pnl = (ltp - float(pos["entry_premium"])) * int(pos["quantity"])
        pos.update({"status": "CLOSED", "exit_premium": ltp, "exit_time": str(now_ist()), "exit_reason": exit_reason, "exit_order": order, "pnl_estimate": pnl})
        state["realized_pnl"] = float(state.get("realized_pnl", 0)) + pnl
        state[key] = None
        save_state(state)
        telegram(f"NIFTY BOT {'PAPER' if PAPER else 'LIVE'} EXIT\n{c['symbol']}\nReason: {exit_reason}\nExit: ₹{ltp:.2f}\nEstimated P&L: ₹{pnl:.2f}\nOrder: {str(order)[:250]}")
    else:
        state[key] = pos
        save_state(state)


def cycle() -> None:
    state = reset_day_if_needed(read_state())
    # Always monitor existing position, including outside entry session.
    active_key = "paper_position" if PAPER else "position"
    if state.get(active_key):
        manage_position(state, active_key)
        state = reset_day_if_needed(read_state())
    if not within_session():
        last_status["last_cycle"] = str(now_ist())
        last_status["note"] = "Outside entry session"
        save_state(state)
        return
    if state.get(active_key): return
    daily_limit = CAPITAL * DAILY_LOSS_LIMIT_PERCENT / 100.0
    if float(state.get("realized_pnl", 0)) <= -daily_limit:
        last_status["note"] = "Daily loss limit reached"
        return
    if int(state.get("trades_today", 0)) >= MAX_TRADES_PER_DAY:
        last_status["note"] = "Daily trade limit reached"
        return
    df = get_candles()
    signal = calculate_signal(df)
    last_status["last_cycle"] = str(now_ist())
    if not signal:
        last_status["note"] = "No confirmed setup"
        return
    last_status["last_signal"] = signal
    if signal["signal_key"] == state.get("last_signal_key"):
        last_status["note"] = "Duplicate signal blocked"
        return
    open_trade(signal, state)


def worker_loop():
    last_status["started"] = True
    while True:
        try:
            cycle()
            last_status["last_error"] = None
        except Exception as e:
            last_status["last_error"] = str(e)
            log.exception("Bot cycle failed")
            telegram(f"NIFTY BOT ERROR ({'LIVE' if LIVE else 'PAPER'}): {str(e)[:700]}")
        time.sleep(30)


@app.get("/")
def root():
    return jsonify({"service": "NIFTY BOT PRO Support Reversal", "mode": "LIVE" if LIVE else "PAPER", "live_enabled": LIVE, "health": "/health", "diagnostics": "/diagnostics", "test_telegram": "/test_telegram"})

@app.get("/health")
def health():
    return jsonify({"ok": True, "time_ist": str(now_ist()), "mode": "LIVE" if LIVE else "PAPER", "worker_started": worker_started})

@app.get("/diagnostics")
def diagnostics():
    state = reset_day_if_needed(read_state())
    safe_status = dict(last_status)
    return jsonify({"time_ist": str(now_ist()), "mode": "LIVE" if LIVE else "PAPER", "angel_login": api is not None,
                    "telegram_configured": bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID")),
                    "state": state, "status": safe_status, "warning": "SL/target are software-managed; keep worker online. No broker-side protective SL is placed."})

@app.get("/test_telegram")
def test_telegram():
    if not os.getenv("TELEGRAM_BOT_TOKEN") or not os.getenv("TELEGRAM_CHAT_ID"):
        return jsonify({"ok": False, "error": "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing"}), 400
    telegram(f"NIFTY BOT Telegram test successful at {now_ist()}")
    return jsonify({"ok": True, "message": "Telegram test attempted; check Telegram and Render logs."})


def start_worker_once():
    global worker_started
    if not worker_started:
        worker_started = True
        threading.Thread(target=worker_loop, daemon=True, name="trading-worker").start()

# Gunicorn imports this module; start worker at import time.
start_worker_once()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
