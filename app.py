
import os, threading, time
from datetime import datetime
from flask import Flask, send_from_directory, jsonify

app = Flask(__name__)

bot_status = {
    "running": False,
    "last_login": None,
    "last_signal": None,
    "pnl_today": 0,
    "trades_today": 0,
    "token_auto": "ON",
    "logic": "TREND HIGH-LOW FIB 0.5/0.32/2.14",
    "started_at": datetime.now().isoformat()
}

def run_bot():
    global bot_status
    try:
        print("[RENDER] Starting bot thread...")
        bot_module = None
        # Try both names
        try:
            import bot_live_1lot as bot_module
            print("[RENDER] Loaded bot_live_1lot")
        except:
            try:
                import bot_iv_fib_angel_FINAL as bot_module
                print("[RENDER] Loaded bot_iv_fib_angel_FINAL")
            except Exception as e:
                print(f"[RENDER] Failed to load any bot: {e}")
                raise
        
        bot_status["running"] = True
        bot_status["last_login"] = datetime.now().isoformat()
        bot_module.main()
    except Exception as e:
        print(f"[RENDER] Bot thread error: {e}")
        import traceback; traceback.print_exc()
        bot_status["running"] = False
        time.sleep(10)
        run_bot()

@app.route('/')
def home():
    return f"""
    <html><head><title>Nifty Bot</title>
    <style>body{{background:#0f172a;color:white;font-family:Arial;padding:20px}}.card{{background:#1e293b;padding:20px;border-radius:12px;margin:15px 0}}h1{{color:#22c55e}}a{{color:#3b82f6}}</style>
    </head><body>
    <h1>🚀 NIFTY BOT - RENDER LIVE ✅</h1>
    <div class="card"><h2>✅ Status: {'🟢 RUNNING' if bot_status['running'] else '🔴 STARTING...'}</h2><p>Started: {bot_status['started_at']}<br>Logic: {bot_status['logic']}<br>Auto Token: ON</p></div>
    <div class="card"><h2>📊 Links</h2><p><a href="/dashboard">P&L Dashboard</a> | <a href="/status">Status JSON</a> | <a href="/logs">Logs</a></p></div>
    <div class="card"><p>Render FREE: Use UptimeRobot every 5 min to keep alive!</p></div>
    </body></html>
    """

@app.route('/dashboard')
def dashboard():
    try:
        return send_from_directory('.', 'pnl_chart.html')
    except:
        return "<h1>Dashboard - pnl_chart.html not found - Upload it!</h1>"

@app.route('/status')
def status():
    return jsonify(bot_status)

@app.route('/logs')
def logs():
    txt="No logs yet"
    try:
        with open('paper_signals.csv','r') as f:
            txt=f.read()[-3000:]
    except:
        pass
    return f"<pre style='background:#000;color:#0f0;padding:20px'>{txt}</pre>"

if __name__ == '__main__':
    threading.Thread(target=run_bot, daemon=True).start()
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
