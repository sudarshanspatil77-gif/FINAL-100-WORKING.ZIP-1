# NIFTY BOT PRO — Support Reversal + Breakout Retest

Includes:
- `tradingview_nifty_support_reversal.pine`: Pine Script v6 strategy for chart signals/backtesting.
- `app.py`: Angel One SmartAPI Python worker + Render health/diagnostic endpoints + Telegram notifications.

## Important limitations / safety

1. **Live order mode is OFF by default.** `LIVE_TRADING_ENABLED=false` simulates order placement, but it still needs Angel One login and market data to calculate signals.
2. The Python version buys the nearest-expiry ATM NIFTY CE on bullish setups and PE on bearish setups. It is a starter, not production-certified.
3. SL/target/trailing SL are monitored by the Python process; the code does **not** place a broker-side protective stop order. If Render/app/network/API fails, exits may not trigger. Do not use live orders until you have independently tested a broker-side risk-control plan.
4. The Python worker uses a software premium stop (initially 20% below entry) and target based on 2R. The Pine strategy's stop is index-chart based. Index points and option premiums are not interchangeable.
5. The state file on a standard Render filesystem may be lost on redeploy/restart. For persistent state, attach a Render persistent disk and set `BOT_STATE_PATH=/data/bot_state.json`; confirm the disk is mounted before live testing.
6. SmartAPI instrument-master fields and broker API responses can change. The code checks the schema and logs failures, but no live broker test has been performed here.
7. `MAX_OPTION_PREMIUM` defaults to ₹500. Adjust only after checking available capital, lot size, and risk.

## Local setup (Windows 10)

1. Install Python 3.11 (enable **Add Python to PATH**).
2. Extract the ZIP into a folder, open Command Prompt in that folder.
3. Run:

   ```bat
   py -3.11 -m venv .venv
   .venv\Scripts\activate
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   copy .env.example .env
   notepad .env
   ```

4. Fill in your own Angel One and Telegram credentials in `.env`. Do not send credentials to anyone or commit `.env` to GitHub.
5. Keep `LIVE_TRADING_ENABLED=false` for the first tests.
6. Start the service:

   ```bat
   python app.py
   ```

7. Open `http://127.0.0.1:10000/health`, `/diagnostics`, and `/test_telegram` in a browser. `/test_telegram` should send a Telegram message if configured.
8. Check terminal logs and `/diagnostics`. Confirm candles match the NIFTY chart and verify option symbol, expiry, strike, lot size, LTP, risk sizing, and order responses before considering live trading.

## TradingView setup

1. Open TradingView → Pine Editor.
2. Paste `tradingview_nifty_support_reversal.pine` and click **Add to chart**.
3. Test on NIFTY index chart at 5-minute timeframe first. The Pine strategy simulates index long/short entries; a short index trade is a bearish/PUT-buy signal concept, not a backtest of actual option premiums. Set commission/slippage assumptions to your actual trading costs.
4. Create alerts from the strategy's alert conditions. TradingView alerts are not automatically connected to Angel One; a separate authenticated webhook/order bridge is required for that.

## Render.com setup

1. Push these files to a **private** GitHub repository (do not upload `.env`).
2. Create a Render Web Service from the repository. Build: `pip install -r requirements.txt`; start: `gunicorn app:app --workers 1 --threads 4 --timeout 120`.
3. Add the variables from `.env.example` in Render Environment. Keep `LIVE_TRADING_ENABLED=false`.
4. Open `/health`, `/diagnostics`, and `/test_telegram`. Review Render logs for login, candle, instrument-master and Telegram errors.
5. Use a persistent disk and `BOT_STATE_PATH=/data/bot_state.json` if you need state to survive restarts.

## Live activation checklist

Do not enable live orders until all items are verified:
- Candle values and timestamps match the live chart and only closed candles are used.
- Instrument master returns correct current expiry, option symbol, token and lot size.
- Paper signal and simulated entry/exit alerts are correct over several sessions.
- Quantity/risk calculations fit the actual account and broker lot size.
- Order status is verified from Angel One order book (not merely an API response).
- A broker-side protective exit / outage plan is implemented and tested.
- Persistent state, duplicate prevention after restart, daily limits and square-off logic are tested.

Only then set `LIVE_TRADING_ENABLED=true` after reviewing the source code. This is not financial advice and does not guarantee profit.
