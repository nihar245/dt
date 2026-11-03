#!/usr/bin/env python3
"""Instant Phone Push Notification Engine for NSE Intraday Trades using ntfy.sh.

Features:
  - 100% Free & Open Source, zero registration required.
  - Pushes instantly to phone via ntfy app (Android/iOS) or browser.
  - Sends high-priority alert cards for:
      * BUY / SELL Entry (with exact stock, entry, stop, target, quantity)
      * 14:30 IST Stale Rule / Profit Lock notification
      * Final Target or Stop Loss execution
      * EOD Session Audit summary
"""

import os
import sys
import json
import urllib.request
import urllib.error
from typing import Dict, Any, Optional

TRADER_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(TRADER_DIR, "config.json")

def get_ntfy_topic() -> str:
    """Get ntfy topic from env var or config.json."""
    if os.environ.get("NTFY_TOPIC"):
        return os.environ.get("NTFY_TOPIC").strip()
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                topic = cfg.get("alerts", {}).get("ntfy_topic")
                if topic:
                    return topic.strip()
        except Exception:
            pass
    return "nse_daytrade_alerts_live"

def _ascii_header(text: str) -> str:
    """Ensure HTTP headers contain only latin-1 safe ASCII characters."""
    clean = text.replace("₹", "Rs. ")
    return clean.encode("ascii", "ignore").decode("ascii").strip()

def send_ntfy_notification(
    title: str,
    message: str,
    topic: Optional[str] = None,
    priority: str = "high",  # min, low, default, high, urgent
    tags: str = "chart_with_upwards_trend"
) -> bool:
    """Send push notification to phone via ntfy.sh."""
    target_topic = topic or get_ntfy_topic()
    url = f"https://ntfy.sh/{target_topic}"

    headers = {
        "Title": _ascii_header(title),
        "Priority": priority,
        "Tags": tags,
    }

    try:
        req = urllib.request.Request(
            url,
            data=message.encode("utf-8"),
            headers=headers,
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                print(f"[Notifier] Push notification delivered to phone (Topic: {target_topic})")
                return True
    except Exception as e:
        print(f"[Notifier Warning] Failed to send push notification: {e}")
    return False

def notify_entry_signal(t: Dict[str, Any], topic: Optional[str] = None) -> bool:
    """Format and send morning entry breakout alert."""
    sym = t.get("sym", "").replace(".NS", "-EQ")
    side = t.get("side", "BUY")
    entry = t.get("entry", 0.0)
    stop = t.get("stop", 0.0)
    target = t.get("target", 0.0)
    qty = t.get("qty", 0)
    buy_time = t.get("buy_time", "")
    risk_per_sh = abs(entry - stop)

    title = f"{side} ALERT: {sym} @ Rs. {entry:.2f}"
    msg = (
        f"NSE 30m ORB BREAKOUT CONFIRMED\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Scrip:    {sym} ({side})\n"
        f"Time:     {buy_time}\n"
        f"Entry:    Rs. {entry:.2f}\n"
        f"Stop:     Rs. {stop:.2f} (Risk: Rs. {risk_per_sh:.2f})\n"
        f"Target:   Rs. {target:.2f} (+1.5R)\n"
        f"Quantity: {qty} shares (Rs. 850 risk sized)\n"
        f"Broker:   Flattrade MIS (Zero Brokerage)\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Trade Execution Steps:\n"
        f"1. Open Broker -> Buy {qty} shares @ Rs. {entry:.2f}\n"
        f"2. Place Stop Loss at Rs. {stop:.2f}\n"
        f"3. Place Limit Target Sell at Rs. {target:.2f}\n"
        f"4. At +0.7R profit, trail stop to Entry (Breakeven)"
    )

    return send_ntfy_notification(
        title=title,
        message=msg,
        topic=topic,
        priority="urgent",
        tags="rocket,moneybag"
    )

def notify_exit_signal(t: Dict[str, Any], topic: Optional[str] = None) -> bool:
    """Format and send trade exit / profit lock alert."""
    sym = t.get("sym", "").replace(".NS", "-EQ")
    outcome = t.get("outcome", "")
    exit_price = t.get("exit_price", 0.0)
    net = t.get("net", 0.0)
    gross = t.get("gross", 0.0)
    sell_time = t.get("sell_time", "")

    title = f"{outcome}: {sym} (Net: Rs. {net:+,.2f})"

    msg = (
        f"INTRADAY TRADE COMPLETED\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Scrip:      {sym}\n"
        f"Exit Time:  {sell_time}\n"
        f"Outcome:    {outcome}\n"
        f"Exit Price: Rs. {exit_price:.2f}\n"
        f"Gross PnL:  Rs. {gross:+,.2f}\n"
        f"Net PnL:    Rs. {net:+,.2f} (After Rs. 15 fee)\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Session position is now 100% FLAT."
    )

    return send_ntfy_notification(
        title=title,
        message=msg,
        topic=topic,
        priority="high",
        tags="bell,chart_with_upwards_trend" if net > 0 else "warning"
    )

def notify_no_trade_signal(target_date: str, topic: Optional[str] = None) -> bool:
    """Notify user of disciplined 0-trade day."""
    title = f"0-Trade Day: {target_date}"
    msg = (
        f"Entry window closed at 13:00 IST.\n"
        f"No setup passed all 4 quantitative filters.\n"
        f"100% Capital Preserved (Rs. 0 loss | Rs. 0 brokerage)."
    )
    return send_ntfy_notification(
        title=title,
        message=msg,
        topic=topic,
        priority="default",
        tags="shield"
    )

if __name__ == "__main__":
    test_trade = {
        "sym": "MARICO.NS",
        "side": "BUY",
        "entry": 860.60,
        "stop": 839.62,
        "target": 892.06,
        "qty": 40,
        "buy_time": "10:25 IST"
    }
    print(f"Testing notification to topic: {get_ntfy_topic()}...")
    notify_entry_signal(test_trade)
