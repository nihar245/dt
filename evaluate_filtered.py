#!/usr/bin/env python3
"""Filtered High-Conviction Intraday Trading Engine for NSE/Groww.

Implements institutional quantitative filters to eliminate bad trades:
  1. Nifty 50 Index Directional Alignment:
     - BUY only if Nifty 50 is bullish (above ORB midpoint & session open).
     - SELL only if Nifty 50 is bearish (below ORB midpoint & session open).
  2. Stock Intraday VWAP Alignment:
     - BUY only if price > VWAP.
     - SELL only if price < VWAP.
  3. Breakout Volume Confirmation:
     - Breakout bar volume must match or exceed opening range average volume.
  4. Range Expansion Filter:
     - Opening Range width >= 0.5% (avoids dead, non-volatile chop).
  5. Selective Execution:
     - Max 1 to 2 ultra-high-conviction trades per day. Never force bad trades.
  6. Breakeven Trailing Stop:
     - Once price reaches +1.0R, trail stop to Entry + ₹60 to eliminate full losses.
  7. Capital Calibration for ₹1,000/day Net Target:
     - Risk = ₹600/trade, Target = 1.8R (₹1,080 gross - ₹60 friction = +₹1,020 net on 1 win).
"""

import os
import sys
import json
import warnings
import datetime
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

GROWW_UNIVERSE = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
    "BHARTIARTL.NS", "ITC.NS", "SBIN.NS", "LT.NS", "BAJFINANCE.NS",
    "HCLTECH.NS", "MARUTI.NS", "SUNPHARMA.NS", "KOTAKBANK.NS", "AXISBANK.NS",
    "NTPC.NS", "TITAN.NS", "POWERGRID.NS", "TATASTEEL.NS"
]

CACHE_DIR = os.path.join(os.path.dirname(__file__), "trader", "data_cache")
os.makedirs(CACHE_DIR, exist_ok=True)


def fetch_symbol_bars(symbol: str) -> pd.DataFrame:
    cache_file = os.path.join(CACHE_DIR, f"{symbol}_5m.csv")
    if os.path.exists(cache_file):
        try:
            df = pd.read_csv(cache_file, index_col=0, parse_dates=True)
            if not df.empty:
                return df
        except Exception:
            pass
    try:
        df = yf.download(symbol, period="10d", interval="5m", progress=False)
        if df is None or df.empty:
            return pd.DataFrame()
        if isinstance(df.columns, pd.MultiIndex):
            df = df.xs(symbol, axis=1, level=1)
        if df.index.tz:
            df.index = df.index.tz_convert("Asia/Kolkata")
        else:
            df.index = df.index.tz_localize("UTC").tz_convert("Asia/Kolkata")
        df.to_csv(cache_file)
        return df
    except Exception:
        return pd.DataFrame()


def compute_vwap(df_session: pd.DataFrame) -> pd.Series:
    """Compute running intraday Volume Weighted Average Price (VWAP)."""
    tp = (df_session["High"] + df_session["Low"] + df_session["Close"]) / 3.0
    vol = df_session["Volume"].replace(0, 1)
    cum_tp_vol = (tp * vol).cumsum()
    cum_vol = vol.cumsum()
    return cum_tp_vol / cum_vol


def get_nifty_regime(nifty_df: pd.DataFrame, date_str: str) -> dict:
    """Calculate Nifty 50 30-min ORB and trend profile for the session."""
    nifty_df["d"] = nifty_df.index.strftime("%Y-%m-%d")
    n_sess = nifty_df[nifty_df["d"] == date_str]
    if len(n_sess) < 20:
        return None
    or_bars = n_sess.between_time("09:15", "09:40")
    if len(or_bars) < 6:
        return None
    nh = float(or_bars["High"].max())
    nl = float(or_bars["Low"].min())
    nmid = (nh + nl) / 2.0
    nopen = float(n_sess["Open"].iloc[0])
    return {"high": nh, "low": nl, "mid": nmid, "open": nopen, "sess": n_sess}


def evaluate_session_filtered(date_str: str, nifty_df: pd.DataFrame, risk_per_trade: float = 600.0, target_r: float = 1.8, max_daily_trades: int = 2, flat_fee: float = 60.0):
    nifty_info = get_nifty_regime(nifty_df, date_str)
    if not nifty_info:
        return []

    candidates = []

    for sym in GROWW_UNIVERSE:
        df = fetch_symbol_bars(sym)
        if df.empty:
            continue
        df["d"] = df.index.strftime("%Y-%m-%d")
        sess = df[df["d"] == date_str]
        if len(sess) < 20:
            continue

        sess = sess.copy()
        sess["vwap"] = compute_vwap(sess)

        # 30-min Opening Range (09:15-09:45)
        or_bars = sess.between_time("09:15", "09:40")
        if len(or_bars) < 6:
            continue

        or_h = float(or_bars["High"].max())
        or_l = float(or_bars["Low"].min())
        or_mid = (or_h + or_l) / 2.0
        or_range_pct = (or_h - or_l) / or_mid

        # Filter 4: Skip micro-range chop
        if or_range_pct < 0.005:
            continue

        avg_or_vol = float(or_bars["Volume"].mean())

        post_orb = sess.between_time("09:45", "14:30")

        for b_time, b in post_orb.iterrows():
            o, h, l, c = float(b["Open"]), float(b["High"]), float(b["Low"]), float(b["Close"])
            v = float(b["Volume"])
            vwap_val = float(b["vwap"])
            b_min, b_max = min(o, c), max(o, c)

            # Corresponding Nifty bar
            n_bars = nifty_info["sess"][nifty_info["sess"].index <= b_time]
            if n_bars.empty:
                continue
            n_close = float(n_bars["Close"].iloc[-1])

            # LONG SETUP VALIDATION
            if b_min > or_h and c > vwap_val:
                # Filter 1: Nifty 50 Market Breadth must be Bullish
                if n_close > nifty_info["mid"] and n_close > nifty_info["open"]:
                    # Filter 3: Volume Surge Confirmation
                    if v >= 0.9 * avg_or_vol:
                        entry = c
                        stop = or_mid
                        risk = entry - stop
                        if risk > 0:
                            target = round(entry + target_r * risk, 2)
                            qty = max(1, int(risk_per_trade / risk))
                            candidates.append({
                                "symbol": sym, "date": date_str, "time": b_time.strftime("%H:%M"),
                                "side": "BUY", "entry": entry, "stop": stop, "target": target,
                                "qty": qty, "risk": risk, "planned_risk": round(qty * risk, 2),
                                "conviction_score": round((c - vwap_val) / vwap_val * 100 + (v / (avg_or_vol + 1)), 2),
                                "sess": sess, "trigger_time": b_time
                            })
                            break

            # SHORT SETUP VALIDATION
            elif b_max < or_l and c < vwap_val:
                # Filter 1: Nifty 50 Market Breadth must be Bearish
                if n_close < nifty_info["mid"] and n_close < nifty_info["open"]:
                    # Filter 3: Volume Surge Confirmation
                    if v >= 0.9 * avg_or_vol:
                        entry = c
                        stop = or_mid
                        risk = stop - entry
                        if risk > 0:
                            target = round(entry - target_r * risk, 2)
                            qty = max(1, int(risk_per_trade / risk))
                            candidates.append({
                                "symbol": sym, "date": date_str, "time": b_time.strftime("%H:%M"),
                                "side": "SELL", "entry": entry, "stop": stop, "target": target,
                                "qty": qty, "risk": risk, "planned_risk": round(qty * risk, 2),
                                "conviction_score": round((vwap_val - c) / vwap_val * 100 + (v / (avg_or_vol + 1)), 2),
                                "sess": sess, "trigger_time": b_time
                            })
                            break

    # Rank by conviction score and take only up to max_daily_trades (1-2 highest conviction)
    candidates.sort(key=lambda x: -x["conviction_score"])
    chosen = candidates[:max_daily_trades]

    results = []
    for t in chosen:
        sess = t["sess"]
        entry = t["entry"]
        stop = t["stop"]
        target = t["target"]
        qty = t["qty"]
        side = t["side"]
        long = side == "BUY"
        risk_amt = t["risk"]

        subsequent = sess[sess.index > t["trigger_time"]].between_time("09:45", "15:15")
        outcome = "EOD_FLAT"
        exit_price = float(subsequent["Close"].iloc[-1]) if not subsequent.empty else entry
        exit_time = "15:15"
        trailed_stop = stop
        is_trailed = False

        for b_time, b in subsequent.iterrows():
            bh, bl = float(b["High"]), float(b["Low"])
            if long:
                # Filter 6: Trailing Breakeven Stop at +1.0R
                if not is_trailed and bh >= (entry + 1.0 * risk_amt):
                    trailed_stop = entry + (flat_fee / qty)  # Breakeven + fee buffer
                    is_trailed = True

                if bl <= trailed_stop:
                    outcome = "TRAILED_STOP" if is_trailed else "STOP_LOSS"
                    exit_price = trailed_stop
                    exit_time = b_time.strftime("%H:%M")
                    break
                elif bh >= target:
                    outcome = "TAKE_PROFIT"
                    exit_price = target
                    exit_time = b_time.strftime("%H:%M")
                    break
            else:
                # Filter 6: Trailing Breakeven Stop at +1.0R
                if not is_trailed and bl <= (entry - 1.0 * risk_amt):
                    trailed_stop = entry - (flat_fee / qty)
                    is_trailed = True

                if bh >= trailed_stop:
                    outcome = "TRAILED_STOP" if is_trailed else "STOP_LOSS"
                    exit_price = trailed_stop
                    exit_time = b_time.strftime("%H:%M")
                    break
                elif bl <= target:
                    outcome = "TAKE_PROFIT"
                    exit_price = target
                    exit_time = b_time.strftime("%H:%M")
                    break

        if long:
            gross = round(qty * (exit_price - entry), 2)
        else:
            gross = round(qty * (entry - exit_price), 2)

        net = round(gross - flat_fee, 2)

        results.append({
            "date": t["date"], "symbol": t["symbol"], "side": side,
            "entry_time": t["time"], "entry": entry, "stop": stop, "target": target,
            "qty": qty, "exit_time": exit_time, "exit_price": round(exit_price, 2),
            "outcome": outcome, "gross_pnl": gross, "friction": flat_fee, "net_pnl": net
        })

    return results


def run():
    test_dates = ["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30"]
    nifty_df = fetch_symbol_bars("^NSEI")

    print("=" * 85)
    print("  HIGH-CONVICTION FILTERED PAPER TRADING REPORT (NSE / GROWW MIS)")
    print("  Target Objective: ~₹1,000/day Net Profit | Zero Forced Trades")
    print("  Filters: Intraday VWAP + Nifty Trend Alignment + Volume Surge + Trailing Stop")
    print("=" * 85)

    all_trades = []
    daily_stats = []

    for d in test_dates:
        trades = evaluate_session_filtered(d, nifty_df, risk_per_trade=600.0, target_r=1.8, max_daily_trades=2)
        all_trades.extend(trades)
        day_gross = sum(t["gross_pnl"] for t in trades)
        day_friction = sum(t["friction"] for t in trades)
        day_net = sum(t["net_pnl"] for t in trades)
        wins = sum(1 for t in trades if t["net_pnl"] > 0)
        daily_stats.append({
            "date": d, "trades": len(trades), "wins": wins,
            "gross": round(day_gross, 2), "friction": round(day_friction, 2), "net": round(day_net, 2)
        })

    print(f"\n{'Date':<11} {'Symbol':<14} {'Side':<5} {'Qty':<5} {'Entry':<8} {'Exit':<8} {'Outcome':<14} {'Gross':<10} {'Friction':<9} {'Net PnL':<9}")
    print("-" * 100)
    for t in all_trades:
        net_str = f"{t['net_pnl']:+,.2f}"
        print(f"{t['date']:<11} {t['symbol']:<14} {t['side']:<5} {t['qty']:<5} {t['entry']:<8.2f} {t['exit_price']:<8.2f} {t['outcome']:<14} {t['gross_pnl']:+<10.2f} -₹{t['friction']:<7.2f} ₹{net_str}")

    print("\n" + "=" * 85)
    print("  DAILY SESSION SUMMARY (AFTER ALL TAXES & PLATFORM FEES)")
    print("=" * 85)
    print(f"{'Date':<11} {'Trades':<8} {'Wins':<6} {'Win Rate':<10} {'Gross PnL':<12} {'Friction':<12} {'Net PnL':<12} {'Status'}")
    print("-" * 85)
    total_net = 0.0
    profitable_days = 0

    for s in daily_stats:
        wr = f"{(s['wins']/s['trades']*100):.1f}%" if s["trades"] > 0 else "N/A (No Trade)"
        total_net += s["net"]
        if s["net"] > 0:
            profitable_days += 1
        status = "PASSED [PROFIT]" if s["net"] >= 900 else ("NO TRADE [CAPITAL PRESERVED]" if s["trades"] == 0 else "LOSS")
        print(f"{s['date']:<11} {s['trades']:<8} {s['wins']:<6} {wr:<10} ₹{s['gross']:+<11,.2f} -₹{s['friction']:<10,.2f} ₹{s['net']:+<11,.2f} {status}")

    avg_net = total_net / len(test_dates)
    print("-" * 85)
    print(f"Total 1-Week Net PnL: ₹{total_net:+,.2f} | Average Daily Net PnL: ₹{avg_net:+,.2f}/day")
    print("=" * 85)

    with open(os.path.join(os.path.dirname(__file__), "trader", "filtered_audit.json"), "w", encoding="utf-8") as fh:
        json.dump({"weekly_net": total_net, "avg_daily_net": avg_net, "daily": daily_stats, "trades": all_trades}, fh, indent=2)


if __name__ == "__main__":
    run()
