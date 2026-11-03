#!/usr/bin/env python3
"""Live Paper Trading Simulation & Trade Journaling Engine for NSE/Flattrade.

Features:
  - 100% Simulation / Paper Trading by default (Zero financial risk).
  - Optional Live Trading via Flattrade API (--live flag).
  - Instant Phone Alerts via ntfy.sh (--notify flag or config alerts).
  - Dynamically loads all symbols from trader/universe.txt (Nifty 100).
  - Fast multi-threaded batch fetching with yfinance.
  - Real-time live polling loop (--watch flag) that monitors candles every 60s until 13:00 IST.
  - Complete Flattrade OMS integration with Zero-Brokerage friction (~₹15 statutory fee).
  - Exact time logging: Buy Time (entry) & Sell Time (exit).
  - Mid-session awareness: reports [IN_FLIGHT] while trade is open.
  - Enforces all validated rules:
      * 30-min Opening Range Breakout (09:15-09:45 IST)
      * No entries before 09:45, no entries after 13:00
      * VWAP directional alignment
      * Volume surge confirmation (>= 1.0x ORB avg volume)
      * Nifty 50 macro trend alignment
      * ₹850 risk sizing (Target 1.5R, ₹15 Flattrade friction)
      * Breakeven trail at +0.7R
      * 14:30 Stale Trade rule (lock profit if > +0.4R, cut stagnant if < -0.3R)
      * Max 1-2 trades/day session cap
  - Writes live outputs to:
      * trader/paper_trades.json (raw structured data)
      * trader/paper_journal.md  (human-readable markdown log)
"""

import os
import sys
import time
import json
import logging
import warnings
import datetime
import pandas as pd
import yfinance as yf

# Silence all deprecation, future, and user warnings (e.g. pandas inplace, sort_index, drop)
warnings.filterwarnings("ignore")
warnings.simplefilter(action="ignore", category=FutureWarning)
warnings.simplefilter(action="ignore", category=UserWarning)
warnings.simplefilter(action="ignore", category=DeprecationWarning)
try:
    warnings.filterwarnings("ignore", message=".*inplace keyword.*")
    warnings.filterwarnings("ignore", message=".*drop.*")
except Exception:
    pass

logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.CRITICAL)

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TRADER_DIR = os.path.join(os.path.dirname(__file__), "trader")
CONFIG_PATH = os.path.join(TRADER_DIR, "config.json")
JOURNAL_PATH = os.path.join(TRADER_DIR, "paper_journal.md")
TRADES_JSON_PATH = os.path.join(TRADER_DIR, "paper_trades.json")
UNIVERSE_PATH = os.path.join(TRADER_DIR, "universe.txt")

try:
    from trader.flattrade_broker import FlattradeBroker
except ImportError:
    try:
        from flattrade_broker import FlattradeBroker
    except ImportError:
        FlattradeBroker = None

try:
    from trader.phone_notifier import notify_entry_signal, notify_exit_signal, notify_no_trade_signal
except ImportError:
    try:
        from phone_notifier import notify_entry_signal, notify_exit_signal, notify_no_trade_signal
    except ImportError:
        notify_entry_signal = None
        notify_exit_signal = None
        notify_no_trade_signal = None

def check_and_send_alerts(trade_record: dict, cfg: dict, force_notify: bool = False):
    """Dispatch phone alerts via ntfy.sh without repeating alerts for the same event."""
    if not notify_entry_signal:
        return
    alerts_enabled = cfg.get("alerts", {}).get("enabled", True) or force_notify
    if not alerts_enabled:
        return

    state_dir = os.path.join(TRADER_DIR, "state")
    os.makedirs(state_dir, exist_ok=True)
    alert_state_file = os.path.join(state_dir, "alerted.json")
    alerted = set()
    if os.path.exists(alert_state_file):
        try:
            with open(alert_state_file, "r") as f:
                alerted = set(json.load(f))
        except Exception:
            pass

    sym = trade_record.get("sym")
    d = trade_record.get("date")
    outcome = trade_record.get("outcome")

    if outcome == "IN_FLIGHT":
        key = f"{d}_{sym}_entry"
        if key not in alerted:
            notify_entry_signal(trade_record)
            alerted.add(key)
            with open(alert_state_file, "w") as f:
                json.dump(list(alerted), f)
    elif outcome != "IN_FLIGHT":
        key = f"{d}_{sym}_exit_{outcome}"
        if key not in alerted:
            notify_exit_signal(trade_record)
            alerted.add(key)
            with open(alert_state_file, "w") as f:
                json.dump(list(alerted), f)

def load_universe() -> list:
    """Dynamically load universe tickers from trader/universe.txt."""
    if os.path.exists(UNIVERSE_PATH):
        try:
            symbols = []
            with open(UNIVERSE_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    sym = line.split("|")[0].strip()
                    if sym and sym not in symbols:
                        symbols.append(sym)
            if symbols:
                return symbols
        except Exception:
            pass
    return [
        "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
        "BHARTIARTL.NS", "ITC.NS", "SBIN.NS", "LT.NS", "BAJFINANCE.NS",
        "HCLTECH.NS", "MARUTI.NS", "SUNPHARMA.NS", "KOTAKBANK.NS", "AXISBANK.NS",
        "NTPC.NS", "TITAN.NS", "POWERGRID.NS", "TATASTEEL.NS"
    ]

def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "budget": 50000,
        "max_loss_per_trade_inr": 850,
        "default_target_rule": "1.5R",
        "breakeven_at_r": 0.7,
        "no_entries_before": "09:45",
        "no_entries_after": "13:00",
        "stale_exit_by": "14:30",
        "stale_profit_threshold_r": 0.4,
        "stale_loss_cutoff_r": -0.3,
        "flat_by": "15:15",
        "broker": "flattrade",
        "friction_flat_fee": 15,
        "max_trades_per_day": 2,
        "paper": True,
        "alerts": {
            "enabled": True,
            "provider": "ntfy",
            "ntfy_topic": "nse_daytrade_alerts_live"
        }
    }

def fetch_live_5m_bars(symbol: str) -> pd.DataFrame:
    try:
        df = yf.download(symbol, period="5d", interval="5m", progress=False)
        if df is None or df.empty:
            return pd.DataFrame()
        if isinstance(df.columns, pd.MultiIndex):
            df = df.xs(symbol, axis=1, level=1)
        if df.index.tz:
            df.index = df.index.tz_convert("Asia/Kolkata")
        else:
            df.index = df.index.tz_localize("UTC").tz_convert("Asia/Kolkata")
        df["d"] = df.index.strftime("%Y-%m-%d")
        return df
    except Exception:
        return pd.DataFrame()

def fetch_all_universe_bars(symbols: list) -> dict:
    """Fetch 5m bars for all symbols in fast multi-threaded batch."""
    results = {}
    try:
        data = yf.download(symbols, period="5d", interval="5m", group_by="ticker", progress=False, threads=True)
        if data is not None and not data.empty:
            for sym in symbols:
                try:
                    if isinstance(data.columns, pd.MultiIndex) and sym in data.columns.levels[0]:
                        df_sym = data[sym].dropna(how="all").copy()
                        if not df_sym.empty:
                            if df_sym.index.tz:
                                df_sym.index = df_sym.index.tz_convert("Asia/Kolkata")
                            else:
                                df_sym.index = df_sym.index.tz_localize("UTC").tz_convert("Asia/Kolkata")
                            df_sym["d"] = df_sym.index.strftime("%Y-%m-%d")
                            results[sym] = df_sym
                except Exception:
                    pass
    except Exception:
        pass

    # Fallback for any missing symbols
    missing = [s for s in symbols if s not in results]
    if missing and len(missing) < 15:
        for sym in missing:
            df = fetch_live_5m_bars(sym)
            if not df.empty:
                results[sym] = df

    return results

def compute_vwap(df_sess: pd.DataFrame) -> pd.Series:
    tp = (df_sess["High"] + df_sess["Low"] + df_sess["Close"]) / 3.0
    vol = df_sess["Volume"].replace(0, 1)
    cum_tp_vol = (tp * vol).cumsum()
    cum_vol = vol.cumsum()
    return cum_tp_vol / cum_vol

def get_nifty_regime(nifty_df: pd.DataFrame, d: str):
    sess = nifty_df[nifty_df["d"] == d]
    if len(sess) < 6:
        return None
    or_bars = sess.between_time("09:15", "09:40")
    if len(or_bars) < 6:
        return None
    nh = float(or_bars["High"].max())
    nl = float(or_bars["Low"].min())
    nmid = (nh + nl) / 2.0
    nopen = float(sess["Open"].iloc[0])
    return {"high": nh, "low": nl, "mid": nmid, "open": nopen, "sess": sess}

def write_paper_journal(trades: list, fee_per_trade: float = 15.0):
    """Write human-readable paper journal markdown with Flattrade fee model."""
    with open(TRADES_JSON_PATH, "w") as f:
        json.dump(trades, f, indent=2)

    closed_trades = [t for t in trades if t["outcome"] != "IN_FLIGHT"]
    total_gross = sum(t["gross"] for t in closed_trades)
    total_fees = sum(t["friction"] for t in closed_trades)
    total_net = sum(t["net"] for t in closed_trades)
    wins = sum(1 for t in closed_trades if t["net"] > 0)
    win_rate = (wins / len(closed_trades) * 100.0) if closed_trades else 0.0

    lines = [
        "# Intraday Paper Trading Live Journal & Execution Audit",
        "",
        f"**Last Updated:** {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S IST')}  ",
        f"**Broker Engine:** Flattrade (Zero Brokerage | Est. Friction: ₹{fee_per_trade:.2f}/trade)  ",
        f"**Capital Risk Model:** ₹850 Risk per Trade | Target: 1.5R | Breakeven at +0.7R",
        "",
        "---",
        "",
        "## Summary Performance (Closed Trades)",
        "",
        f"- **Total Paper Trades Executed:** {len(closed_trades)} (Active In-Flight: {len(trades) - len(closed_trades)})",
        f"- **Win Rate:** {win_rate:.1f}% ({wins}/{len(closed_trades)})",
        f"- **Cumulative Gross PnL:** ₹{total_gross:+,.2f}",
        f"- **Total Platform Friction:** -₹{total_fees:,.2f}",
        f"- **Cumulative Net PnL (After Fees):** **₹{total_net:+,.2f}**",
        "",
        "---",
        "",
        "## Detailed Trade Log (Exact Buy & Sell Times)",
        "",
        "| Date | Symbol | Side | Buy Time | Sell Time | Qty | Entry (₹) | Exit (₹) | Target (₹) | Stop (₹) | Outcome | Gross (₹) | Fee (₹) | Net (₹) |",
        "| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- | :---: | :---: | :---: |"
    ]

    for t in trades:
        status_str = f"**{t['outcome']}**" if t['outcome'] == "IN_FLIGHT" else t['outcome']
        lines.append(
            f"| {t['date']} | `{t['sym']}` | {t['side']} | {t['buy_time']} | {t['sell_time']} | "
            f"{t['qty']} | {t['entry']:.2f} | {t['exit_price']:.2f} | {t['target']:.2f} | {t['stop']:.2f} | "
            f"{status_str} | {t['gross']:+,.2f} | -{t['friction']:.2f} | **{t['net']:+,.2f}** |"
        )

    lines.append("")
    with open(JOURNAL_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

def scan_for_candidates(target_date: str, cfg: dict):
    """Scan Nifty 100 universe for breakout setup confirming all 4 rules."""
    nifty_df = fetch_live_5m_bars("^NSEI")
    if nifty_df.empty:
        return None, "Could not fetch Nifty 50 bars."

    nifty_info = get_nifty_regime(nifty_df, target_date)
    if not nifty_info:
        return None, f"Opening range not complete yet for Nifty on {target_date}."

    universe = load_universe()
    all_bars = fetch_all_universe_bars(universe)

    candidates = []
    for sym in universe:
        df = all_bars.get(sym)
        if df is None or df.empty:
            continue
        sess = df[df["d"] == target_date]
        if len(sess) < 6:
            continue
        sess = sess.copy()
        sess["vwap"] = compute_vwap(sess)
        or_bars = sess.between_time("09:15", "09:40")
        if len(or_bars) < 6:
            continue

        or_h = float(or_bars["High"].max())
        or_l = float(or_bars["Low"].min())
        or_mid = (or_h + or_l) / 2.0
        if (or_h - or_l) / or_mid < 0.005:
            continue
        avg_vol = float(or_bars["Volume"].mean())

        for b_time, b in sess.between_time(cfg["no_entries_before"], cfg["no_entries_after"]).iterrows():
            c, o, h, l, v, vwap = float(b["Close"]), float(b["Open"]), float(b["High"]), float(b["Low"]), float(b["Volume"]), float(b["vwap"])
            n_bars = nifty_info["sess"][nifty_info["sess"].index <= b_time]
            if n_bars.empty:
                continue
            n_close = float(n_bars["Close"].iloc[-1])

            # Long breakout
            if min(o, c) > or_h and c > vwap and n_close > nifty_info["mid"] and n_close > nifty_info["open"] and v >= 1.0 * avg_vol:
                risk = c - or_mid
                if risk > 0:
                    qty = max(1, int(cfg["max_loss_per_trade_inr"] / risk))
                    candidates.append({
                        "date": target_date, "sym": sym, "side": "BUY", "entry": c, "stop": or_mid,
                        "risk": risk, "qty": qty, "buy_time": b_time.strftime("%H:%M IST"),
                        "time_obj": b_time, "sess": sess, "score": (c - vwap) / vwap * 100
                    })
                    break
            # Short breakdown
            elif max(o, c) < or_l and c < vwap and n_close < nifty_info["mid"] and n_close < nifty_info["open"] and v >= 1.0 * avg_vol:
                risk = or_mid - c
                if risk > 0:
                    qty = max(1, int(cfg["max_loss_per_trade_inr"] / risk))
                    candidates.append({
                        "date": target_date, "sym": sym, "side": "SELL", "entry": c, "stop": or_mid,
                        "risk": risk, "qty": qty, "buy_time": b_time.strftime("%H:%M IST"),
                        "time_obj": b_time, "sess": sess, "score": (vwap - c) / vwap * 100
                    })
                    break

    if not candidates:
        return None, "No setup passed all quantitative filters."

    candidates.sort(key=lambda x: -x["score"])
    return candidates[0], None

def evaluate_date_simulation(target_date: str, cfg: dict, is_paper: bool = True, force_notify: bool = False):
    """Run simulated or live evaluation for a specific target date."""
    print(f"\nEvaluating Simulation Paper Trade for: {target_date}")
    universe = load_universe()
    print(f"Scanning {len(universe)} universe symbols for {target_date} (Nifty 100)...")

    chosen, err = scan_for_candidates(target_date, cfg)
    if not chosen:
        print(f">>> {err or 'No setup passed all quantitative filters today.'} Capital preserved (₹0 loss).")
        return None

    # Setup parameters
    target_r = 1.5
    be_r = cfg["breakeven_at_r"]
    entry = chosen["entry"]
    stop = chosen["stop"]
    risk = chosen["risk"]
    qty = chosen["qty"]
    long = chosen["side"] == "BUY"
    target = round(entry + target_r * risk if long else entry - target_r * risk, 2)
    fee = float(cfg.get("friction_flat_fee", 15))  # Flattrade default ₹15

    # Flattrade Order Ticket / Execution
    broker = FlattradeBroker(is_paper=is_paper) if FlattradeBroker else None
    if broker:
        broker.place_order(
            symbol=chosen["sym"],
            side=chosen["side"],
            qty=qty,
            price=entry,
            stop_loss=stop,
            target=target,
            price_type="LMT",
            product_type="M"
        )
    else:
        print(f"\n[ORDER TRIGGERED]")
        print(f"  Symbol:   {chosen['sym']} ({chosen['side']})")
        print(f"  Buy Time: {chosen['buy_time']}")
        print(f"  Entry:    ₹{chosen['entry']:.2f}")
        print(f"  Stop:     ₹{chosen['stop']:.2f} (Risk/share: ₹{chosen['risk']:.2f})")
        print(f"  Qty:      {qty} shares (₹850 risk sized)")

    # Simulate / Trace exit
    sub = chosen["sess"][chosen["sess"].index > chosen["time_obj"]].between_time("09:45", cfg["flat_by"])
    curr_stop = stop
    be_active = False
    exit_price = float(sub["Close"].iloc[-1]) if not sub.empty else entry
    sell_time = sub.index[-1].strftime("%H:%M IST") if not sub.empty else chosen["buy_time"]
    outcome = "EOD_FLAT"

    for b_time, b in sub.iterrows():
        bh, bl, bc = float(b["High"]), float(b["Low"]), float(b["Close"])
        time_str = b_time.strftime("%H:%M")

        # Breakeven Trail
        if be_r and not be_active:
            fav_r = (bh - entry) / risk if long else (entry - bl) / risk
            if fav_r >= be_r:
                curr_stop = entry + (fee / qty) if long else entry - (fee / qty)
                be_active = True

        # Stop Hit
        if (long and bl <= curr_stop) or (not long and bh >= curr_stop):
            outcome = "BREAKEVEN" if be_active else "STOP_LOSS"
            exit_price = curr_stop
            sell_time = b_time.strftime("%H:%M IST")
            break

        # Target Hit
        if (long and bh >= target) or (not long and bl <= target):
            outcome = "TARGET"
            exit_price = target
            sell_time = b_time.strftime("%H:%M IST")
            break

        # 14:30 Stale Trade Rule
        if time_str >= cfg.get("stale_exit_by", "14:30"):
            fav_r = (bc - entry) / risk if long else (entry - bc) / risk
            if fav_r > cfg.get("stale_profit_threshold_r", 0.4):
                outcome = "1430_PROFIT_CLOSE"
                exit_price = bc
                sell_time = b_time.strftime("%H:%M IST")
                break
            elif fav_r < cfg.get("stale_loss_cutoff_r", -0.3):
                outcome = "1430_CUT_EARLY"
                exit_price = bc
                sell_time = b_time.strftime("%H:%M IST")
                break

    # Real-time mid-session awareness:
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    is_live_today = (target_date == today_str)
    last_bar_time = sub.index[-1].strftime("%H:%M") if not sub.empty else "09:45"
    if outcome == "EOD_FLAT" and is_live_today and last_bar_time < cfg.get("flat_by", "15:15"):
        outcome = "IN_FLIGHT"
        sell_time = f"{sub.index[-1].strftime('%H:%M IST')} (Live)"

    gross = round(qty * (exit_price - entry if long else entry - exit_price), 2)
    net = round(gross - fee, 2)

    trade_record = {
        "date": target_date,
        "sym": chosen["sym"],
        "side": chosen["side"],
        "buy_time": chosen["buy_time"],
        "sell_time": sell_time,
        "qty": qty,
        "entry": round(entry, 2),
        "exit_price": round(exit_price, 2),
        "target": round(target, 2),
        "stop": round(stop, 2),
        "outcome": outcome,
        "gross": gross,
        "friction": fee,
        "net": net
    }

    status_header = "[FLATTRADE RESULT - FINAL]" if outcome != "IN_FLIGHT" else "[FLATTRADE STATUS - ACTIVE IN-FLIGHT]"
    print(f"\n{status_header}")
    print(f"  Sell Time / Status: {sell_time}")
    print(f"  Outcome:            {outcome}")
    print(f"  Exit/Current Price: ₹{exit_price:.2f}")
    pnl_label = "Net PnL:           " if outcome != "IN_FLIGHT" else "Unrealized Net PnL:"
    print(f"  Gross PnL:          ₹{gross:+,.2f}")
    print(f"  Flattrade Fee:      -₹{fee:.2f} (Zero Brokerage)")
    print(f"  {pnl_label} ₹{net:+,.2f}")

    # Phone Alert Dispatch
    check_and_send_alerts(trade_record, cfg, force_notify=force_notify)

    return trade_record

def run_watch_loop(target_date: str, cfg: dict, is_paper: bool = True, force_notify: bool = False):
    """Continuous live polling loop monitoring every 60s until 13:00 IST or exit."""
    print("\n" + "=" * 75)
    print("LIVE FLATTRADE WATCHER & REAL-TIME POLLING ENGINE")
    print("=" * 75)
    print(f"Monitoring Mode: {'100% Simulation / Paper' if is_paper else '*** REAL LIVE TRADING ***'}")
    print(f"Target Session:  {target_date}")
    print(f"Entry Window:    {cfg['no_entries_before']} to {cfg['no_entries_after']} IST")
    print("Polling every 60 seconds. Press Ctrl+C anytime to stop.\n")

    active_trade = None

    while True:
        now_ist = datetime.datetime.now()
        now_time_str = now_ist.strftime("%H:%M")

        # 1. Before Open
        if now_time_str < "09:15":
            print(f"[{now_time_str} IST] Market has not opened yet. Sleeping 60s...")
            time.sleep(60)
            continue

        # 2. Opening Range Forming
        if now_time_str < cfg["no_entries_before"]:
            print(f"[{now_time_str} IST] 30-min Opening Range forming (09:15 - {cfg['no_entries_before']} IST). Watching volume...")
            time.sleep(60)
            continue

        # 3. Scanning for Candidate (if no active trade yet)
        if active_trade is None:
            if now_time_str > cfg["no_entries_after"]:
                print(f"\n[{now_time_str} IST] Entry window cutoff ({cfg['no_entries_after']} IST) reached.")
                print(">>> 0-Trade Day confirmed. 100% Capital preserved (₹0.00 loss). Watcher exiting.")
                if notify_no_trade_signal:
                    notify_no_trade_signal(target_date)
                break

            print(f"[{now_time_str} IST] Scanning 100 Nifty 100 symbols for institutional breakout...")
            record = evaluate_date_simulation(target_date, cfg, is_paper=is_paper, force_notify=force_notify)
            if record:
                # Setup triggered
                existing = []
                if os.path.exists(TRADES_JSON_PATH):
                    try:
                        with open(TRADES_JSON_PATH, "r") as f:
                            existing = json.load(f)
                    except Exception:
                        pass
                existing = [r for r in existing if r["date"] != target_date]
                existing.append(record)
                write_paper_journal(existing, fee_per_trade=cfg.get("friction_flat_fee", 15))

                if record["outcome"] == "IN_FLIGHT":
                    active_trade = record
                    print(f"\n>>> Trade triggered on {record['sym']}! Now monitoring in-flight position...")
                else:
                    print(f"\n>>> Trade finalized with outcome: {record['outcome']}. Done for today.")
                    break
            else:
                print(f"[{now_time_str} IST] No confirmed breakout yet. Waiting 60s for next bar...")
                time.sleep(60)
                continue

        # 4. Monitoring In-Flight Position
        if active_trade:
            print(f"[{now_time_str} IST] Tracking open position on {active_trade['sym']}...")
            record = evaluate_date_simulation(target_date, cfg, is_paper=is_paper, force_notify=force_notify)
            if record:
                existing = [r for r in existing if r["date"] != target_date]
                existing.append(record)
                write_paper_journal(existing, fee_per_trade=cfg.get("friction_flat_fee", 15))

                if record["outcome"] != "IN_FLIGHT":
                    print(f"\n>>> Position closed! Outcome: {record['outcome']} | Net: ₹{record['net']:+,.2f}")
                    break

            time.sleep(45)

def main():
    cfg = load_config()
    universe = load_universe()
    is_live = "--live" in sys.argv
    is_paper = not is_live
    is_watch = "--watch" in sys.argv
    force_notify = "--notify" in sys.argv or cfg.get("alerts", {}).get("enabled", True)

    mode_label = "*** LIVE REAL ORDERS ***" if is_live else "100% Simulation Paper Trading"
    print("=" * 75)
    print("NSE INTRADAY TRADER (NIFTY 100 | FLATTRADE ZERO BROKERAGE)")
    print("=" * 75)
    print(f"Mode:     {mode_label}")
    print(f"Broker:   Flattrade (Friction: ₹{cfg.get('friction_flat_fee', 15):.2f}/trade)")
    print(f"Alerts:   {'ntfy.sh Phone Push ACTIVE' if force_notify else 'Disabled'}")
    print(f"Universe: {len(universe)} symbols (Loaded dynamically from trader/universe.txt)")
    print(f"Strategy: 30m ORB + VWAP + Volume Surge + Nifty Alignment")
    print(f"Window:   {cfg['no_entries_before']} to {cfg['no_entries_after']} IST | Cutoff: {cfg['stale_exit_by']}")

    # Determine date
    target_date = datetime.datetime.now().strftime("%Y-%m-%d")
    for arg in sys.argv[1:]:
        if arg.startswith("--date"):
            target_date = sys.argv[sys.argv.index(arg) + 1] if len(sys.argv) > sys.argv.index(arg) + 1 else arg.split("=")[-1]

    if is_watch:
        try:
            run_watch_loop(target_date, cfg, is_paper=is_paper, force_notify=force_notify)
        except KeyboardInterrupt:
            print("\n[Watcher Stopped by User] Exiting safely.")
    else:
        record = evaluate_date_simulation(target_date, cfg, is_paper=is_paper, force_notify=force_notify)
        if record:
            existing = []
            if os.path.exists(TRADES_JSON_PATH):
                try:
                    with open(TRADES_JSON_PATH, "r") as f:
                        existing = json.load(f)
                except Exception:
                    pass
            existing = [r for r in existing if r["date"] != target_date]
            existing.append(record)
            write_paper_journal(existing, fee_per_trade=cfg.get("friction_flat_fee", 15))
            print(f"\nJournal updated at {JOURNAL_PATH}")

if __name__ == "__main__":
    main()
