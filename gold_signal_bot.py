#!/usr/bin/env python3
"""
Gold (XAUUSD) Telegram signal bot.

Strategy: EMA 9/21 crossover, filtered by EMA 50 trend, with ATR-based
stop loss and two take profits. Checks once per closed candle and sends
a Telegram message when a signal appears.

No third-party packages needed (standard library only), so it runs on
Termux (Android) or any Python 3.8+ machine.

Setup:
  1. Telegram: message @BotFather -> /newbot -> copy the TOKEN.
  2. Send any message to your new bot, then open
     https://api.telegram.org/bot<TOKEN>/getUpdates and copy "chat":{"id":...}
  3. Free API key for price data: https://twelvedata.com (free plan is enough).
  4. Set environment variables and run:
       export TG_TOKEN="123456:ABC..."
       export TG_CHAT_ID="123456789"
       export TD_API_KEY="your_twelvedata_key"
       python3 gold_signal_bot.py

This sends signals only. It does not place trades. Test on demo first;
no strategy wins every trade.
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request

# ---------------- settings ----------------
SYMBOL = "XAU/USD"
INTERVAL = "5min"        # 1min, 5min, 15min, 30min, 1h ...
FAST, SLOW, TREND = 9, 21, 50
ATR_LEN = 14
SL_ATR_MULT = 1.5        # stop loss distance = 1.5 x ATR
TP1_R, TP2_R = 1.5, 3.0  # take profits as multiples of risk
CHECK_EVERY_SEC = 60
STATE_FILE = "last_signal.txt"
# ------------------------------------------

TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")
TD_API_KEY = os.environ.get("TD_API_KEY", "")


def http_get_json(url):
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.loads(r.read().decode())


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    data = urllib.parse.urlencode(
        {"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"}
    ).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=20) as r:
        return json.loads(r.read().decode())


def get_candles():
    """Returns candles oldest -> newest as dicts with float OHLC."""
    q = urllib.parse.urlencode(
        {"symbol": SYMBOL, "interval": INTERVAL, "outputsize": 200, "apikey": TD_API_KEY}
    )
    data = http_get_json(f"https://api.twelvedata.com/time_series?{q}")
    if "values" not in data:
        raise RuntimeError(f"Data error: {data}")
    rows = list(reversed(data["values"]))
    return [
        {
            "t": r["datetime"],
            "o": float(r["open"]),
            "h": float(r["high"]),
            "l": float(r["low"]),
            "c": float(r["close"]),
        }
        for r in rows
    ]


def ema(values, length):
    k = 2 / (length + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def atr(candles, length):
    trs = []
    for i, c in enumerate(candles):
        if i == 0:
            trs.append(c["h"] - c["l"])
        else:
            pc = candles[i - 1]["c"]
            trs.append(max(c["h"] - c["l"], abs(c["h"] - pc), abs(c["l"] - pc)))
    out = [sum(trs[:length]) / length] * length
    for tr in trs[length:]:
        out.append((out[-1] * (length - 1) + tr) / length)
    return out


def analyse(candles):
    """Uses only CLOSED candles (drops the last one, which may still be forming)."""
    candles = candles[:-1]
    closes = [c["c"] for c in candles]
    f, s, t = ema(closes, FAST), ema(closes, SLOW), ema(closes, TREND)
    a = atr(candles, ATR_LEN)

    crossed_up = f[-2] <= s[-2] and f[-1] > s[-1]
    crossed_down = f[-2] >= s[-2] and f[-1] < s[-1]
    price, risk = closes[-1], a[-1] * SL_ATR_MULT

    if crossed_up and price > t[-1]:
        side = "BUY"
        sl, tp1, tp2 = price - risk, price + risk * TP1_R, price + risk * TP2_R
    elif crossed_down and price < t[-1]:
        side = "SELL"
        sl, tp1, tp2 = price + risk, price - risk * TP1_R, price - risk * TP2_R
    else:
        return None
    return {"side": side, "time": candles[-1]["t"], "entry": price,
            "sl": sl, "tp1": tp1, "tp2": tp2}


def format_signal(s):
    icon = "🟢" if s["side"] == "BUY" else "🔴"
    return (
        f"{icon} <b>XAUUSD {s['side']}</b> ({INTERVAL})\n"
        f"Entry: <b>{s['entry']:.2f}</b>\n"
        f"Stop Loss: <b>{s['sl']:.2f}</b>\n"
        f"TP1: <b>{s['tp1']:.2f}</b>\n"
        f"TP2: <b>{s['tp2']:.2f}</b>\n"
        f"Candle: {s['time']}\n\n"
        f"Tip: move SL to entry once TP1 hits. Risk 1-2% per trade."
    )


def load_last():
    try:
        return open(STATE_FILE).read().strip()
    except OSError:
        return ""


def save_last(v):
    with open(STATE_FILE, "w") as fh:
        fh.write(v)


def main():
    if not (TG_TOKEN and TG_CHAT_ID and TD_API_KEY):
        sys.exit("Set TG_TOKEN, TG_CHAT_ID and TD_API_KEY environment variables first.")

    # ONCE=1 -> check a single time and exit (used by GitHub Actions)
    if os.environ.get("ONCE") == "1":
        last = load_last()
        sig = analyse(get_candles())
        if sig and sig["time"] != last:
            send_telegram(format_signal(sig))
            save_last(sig["time"])
            print("Signal sent:", sig["side"], sig["time"])
        else:
            print("No new signal.")
        return

    send_telegram("✅ Gold signal bot started.")
    print("Bot running. Ctrl+C to stop.")
    last = load_last()

    while True:
        try:
            sig = analyse(get_candles())
            if sig and sig["time"] != last:
                send_telegram(format_signal(sig))
                last = sig["time"]
                save_last(last)
                print("Signal sent:", sig["side"], sig["time"])
        except Exception as e:  # keep running through network hiccups
            print("Error:", e)
        time.sleep(CHECK_EVERY_SEC)


if __name__ == "__main__":
    main()
