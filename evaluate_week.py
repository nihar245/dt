#!/usr/bin/env python3
"""1-Week Paper Trading Replay & Net PnL Performance Audit Engine for NSE/Groww.

Simulates a full 1-week evaluation period (Mon-Fri) on Groww-eligible Nifty 50 stocks:
  1. Market: National Stock Exchange of India (NSE).
  2. Setup: 30-Minute Opening Range Breakout (09:15-09:45 IST).
  3. Execution Trigger: 5-minute candle body close beyond ORB boundary (ignoring wicks).
  4. Stop Loss: Hard range midpoint (or_mid).
  5. Target: 1.8R - 2.0R Reward-to-Risk ratio.
  6. Square-off: Strict intraday flat by 15:15 IST (Groww MIS auto-squareoff rule).
  7. Risk Guardrail: Max 5 trades per daily session.
  8. Groww Friction: Flat ₹60 deduction per round-trip trade (₹20x2 brokerage + STT + GST + exchange turnover fees).
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

# Groww intraday approved top liquid stocks (Category 1 MIS)
GROWW_ELIGIBLE_UNIVERSE = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
    "BHARTIARTL.NS", "ITC.NS", "SBIN.NS", "LT.NS", "BAJFINANCE.NS",
    "HCLTECH.NS", "MARUTI.NS", "SUNPHARMA.NS", "KOTAKBANK.NS", "AXISBANK.NS",
    "TATAMOTORS.NS", "NTPC.NS", "TITAN.NS", "POWERGRID.NS", "TATASTEEL.NS"
]

CACHE_DIR = os.path.join(os.path.dirname(__file__), "trader", "data_cache")
os.makedirs(CACHE_DIR, exist_ok=True)


def fetch_historical_bars(symbol: str, period: str = "10d") -> pd.DataFrame:
    """Fetch 5-minute bars for symbol with local disk caching."""
    cache_file = os.path.join(CACHE_DIR, f"{symbol}_5m.csv")
    if os.path.exists(cache_file):
        try:
            df = pd.read_csv(cache_file, index_col=0, parse_dates=True)
            if not df.empty:
                return df
        except Exception:
            pass

    try:
        df = yf.download(symbol, period=period, interval="5m", progress=False)
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
    except Exception as e:
        return pd.DataFrame()


def evaluate_day_session(date_str: str, universe: list, risk_per_trade: float = 200.0, target_r: float = 1.8, max_daily_trades: int = 5, flat_fee: float = 60.0):
    """Simulate paper trading for one active trading session."""
    candidate_trades = []

    for sym in universe:
        df = fetch_historical_bars(sym)
        if df.empty:
            continue
        df["date"] = df.index.strftime("%Y-%m-%d")
        sess = df[df["date"] == date_str]
        if len(sess) < 20:
            continue

        # 30-min Opening Range (09:15 to 09:45 IST: first 6 bars closing at 09:20, 09:25, 09:30, 09:35, 09:40, 09:45)
        or_bars = sess.between_time("09:15", "09:40")
        if len(or_bars) < 6:
            continue

        or_high = float(or_bars["High"].max())
        or_low = float(or_bars["Low"].min())
        or_mid = round((or_high + or_low) / 2.0, 2)
        or_range = or_high - or_low
        if or_range <= 0 or (or_range / or_mid) < 0.003:  # skip dead flat ranges
            continue

        # Post-ORB bars from 09:45 to 15:00
        post_orb = sess.between_time("09:45", "15:00")
        triggered = False

        for bar_time, bar in post_orb.iterrows():
            o, h, l, c = float(bar["Open"]), float(bar["High"]), float(bar["Low"]), float(bar["Close"])
            body_min = min(o, c)
            body_max = max(o, c)

            # Check candle body close breakout (ignoring false wicks)
            if body_min > or_high:  # Long Breakout
                entry = c
                stop = or_mid
                risk = entry - stop
                if risk <= 0:
                    continue
                target = round(entry + target_r * risk, 2)
                qty = max(1, int(risk_per_trade / risk))
                candidate_trades.append({
                    "symbol": sym,
                    "date": date_str,
                    "time": bar_time.strftime("%H:%M"),
                    "side": "BUY",
                    "entry": entry,
                    "stop": stop,
                    "target": target,
                    "qty": qty,
                    "risk_per_share": risk,
                    "planned_risk": round(qty * risk, 2),
                    "sess": sess,
                    "trigger_time": bar_time
                })
                break

            elif body_max < or_low:  # Short Breakdown
                entry = c
                stop = or_mid
                risk = stop - entry
                if risk <= 0:
                    continue
                target = round(entry - target_r * risk, 2)
                qty = max(1, int(risk_per_trade / risk))
                candidate_trades.append({
                    "symbol": sym,
                    "date": date_str,
                    "time": bar_time.strftime("%H:%M"),
                    "side": "SELL",
                    "entry": entry,
                    "stop": stop,
                    "target": target,
                    "qty": qty,
                    "risk_per_share": risk,
                    "planned_risk": round(qty * risk, 2),
                    "sess": sess,
                    "trigger_time": bar_time
                })
                break

    # Sort candidates by earliest high-conviction trigger time and take up to max_daily_trades
    candidate_trades.sort(key=lambda x: x["time"])
    executed_trades = candidate_trades[:max_daily_trades]

    trade_results = []
    for t in executed_trades:
        sess = t["sess"]
        entry = t["entry"]
        stop = t["stop"]
        target = t["target"]
        qty = t["qty"]
        side = t["side"]
        long = side == "BUY"

        # Subsequent bars from trigger time to 15:15 IST square-off
        subsequent = sess[sess.index > t["trigger_time"]].between_time("09:45", "15:15")
        outcome = "EOD_FLAT"
        exit_price = float(subsequent["Close"].iloc[-1]) if not subsequent.empty else entry
        exit_time = "15:15"

        for b_time, b in subsequent.iterrows():
            bh, bl = float(b["High"]), float(b["Low"])
            if long:
                if bl <= stop:
                    outcome = "STOP_LOSS"
                    exit_price = stop
                    exit_time = b_time.strftime("%H:%M")
                    break
                elif bh >= target:
                    outcome = "TAKE_PROFIT"
                    exit_price = target
                    exit_time = b_time.strftime("%H:%M")
                    break
            else:
                if bh >= stop:
                    outcome = "STOP_LOSS"
                    exit_price = stop
                    exit_time = b_time.strftime("%H:%M")
                    break
                elif bl <= target:
                    outcome = "TAKE_PROFIT"
                    exit_price = target
                    exit_time = b_time.strftime("%H:%M")
                    break

        if long:
            gross_pnl = round(qty * (exit_price - entry), 2)
        else:
            gross_pnl = round(qty * (entry - exit_price), 2)

        net_pnl = round(gross_pnl - flat_fee, 2)

        trade_results.append({
            "date": t["date"],
            "symbol": t["symbol"],
            "side": side,
            "entry_time": t["time"],
            "entry": entry,
            "stop": stop,
            "target": target,
            "qty": qty,
            "exit_time": exit_time,
            "exit_price": round(exit_price, 2),
            "outcome": outcome,
            "gross_pnl": gross_pnl,
            "friction": flat_fee,
            "net_pnl": net_pnl
        })

    return trade_results


def run_full_week_simulation():
    # Last 5 active trading sessions (Monday to Friday)
    test_dates = ["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30"]

    print("=" * 80)
    print("  INDIAN STOCK MARKET (NSE) - 1-WEEK PAPER TRADING EVALUATION REPORT")
    print("  Brokerage & Compliance: Groww MIS Intraday (Category 1 Large-Caps)")
    print("  Benchmark Evaluation Period: 2026-10-26 to 2026-10-30 (5 Full Sessions)")
    print("=" * 80)

    # 1. Baseline Model: ₹200 Risk Per Trade
    print("\n>>> MODEL A: BASELINE RISK PROFILE (₹200 Risk/Trade, Max 5 Trades/Day)")
    print("-" * 80)
    all_baseline_trades = []
    daily_summaries_a = []

    for d in test_dates:
        trades = evaluate_day_session(d, GROWW_ELIGIBLE_UNIVERSE, risk_per_trade=200.0, target_r=1.8, max_daily_trades=5)
        all_baseline_trades.extend(trades)
        day_gross = sum(t["gross_pnl"] for t in trades)
        day_friction = sum(t["friction"] for t in trades)
        day_net = sum(t["net_pnl"] for t in trades)
        wins = sum(1 for t in trades if t["net_pnl"] > 0)
        daily_summaries_a.append({
            "date": d, "trades": len(trades), "wins": wins,
            "gross": round(day_gross, 2), "friction": round(day_friction, 2), "net": round(day_net, 2)
        })

    # Print trade-by-trade breakdown
    print(f"{'Date':<11} {'Symbol':<14} {'Side':<5} {'Qty':<5} {'Entry':<8} {'Exit':<8} {'Outcome':<12} {'Gross':<9} {'Friction':<9} {'Net PnL':<9}")
    print("-" * 95)
    for t in all_baseline_trades:
        net_str = f"{t['net_pnl']:+,.2f}"
        print(f"{t['date']:<11} {t['symbol']:<14} {t['side']:<5} {t['qty']:<5} {t['entry']:<8.2f} {t['exit_price']:<8.2f} {t['outcome']:<12} {t['gross_pnl']:+<9.2f} -₹{t['friction']:<7.2f} ₹{net_str}")

    print("\n--- Daily Session Performance (Model A) ---")
    print(f"{'Date':<11} {'Trades':<8} {'Wins':<6} {'Win Rate':<10} {'Gross PnL':<12} {'Friction':<12} {'Net PnL':<12}")
    print("-" * 75)
    total_net_a = 0.0
    for s in daily_summaries_a:
        wr = f"{(s['wins']/s['trades']*100):.1f}%" if s["trades"] > 0 else "0.0%"
        total_net_a += s["net"]
        print(f"{s['date']:<11} {s['trades']:<8} {s['wins']:<6} {wr:<10} ₹{s['gross']:+<11,.2f} -₹{s['friction']:<10,.2f} ₹{s['net']:+<11,.2f}")
    
    avg_daily_net_a = total_net_a / len(test_dates)
    print("-" * 75)
    print(f"Total 1-Week Net PnL (Model A): ₹{total_net_a:+,.2f} | Average Daily Net: ₹{avg_daily_net_a:+,.2f}/day")

    # 2. Scaled Capital Profile for ₹2,000/day Target
    # To target ₹2,000 net with 1.8R target:
    # 2-3 winning trades need ₹1,000 to ₹1,200 gain each -> risk_per_trade = ₹700
    print("\n" + "=" * 80)
    print(">>> MODEL B: SCALED CAPITAL PROFILE FOR ₹2,000+/DAY TARGET")
    print("    Parameters: ₹750 Risk/Trade, 1.8R Target (~₹1,350/win), Max 5 Trades/Day")
    print("-" * 80)
    all_scaled_trades = []
    daily_summaries_b = []

    for d in test_dates:
        trades = evaluate_day_session(d, GROWW_ELIGIBLE_UNIVERSE, risk_per_trade=750.0, target_r=1.8, max_daily_trades=5)
        all_scaled_trades.extend(trades)
        day_gross = sum(t["gross_pnl"] for t in trades)
        day_friction = sum(t["friction"] for t in trades)
        day_net = sum(t["net_pnl"] for t in trades)
        wins = sum(1 for t in trades if t["net_pnl"] > 0)
        daily_summaries_b.append({
            "date": d, "trades": len(trades), "wins": wins,
            "gross": round(day_gross, 2), "friction": round(day_friction, 2), "net": round(day_net, 2)
        })

    print(f"{'Date':<11} {'Trades':<8} {'Wins':<6} {'Win Rate':<10} {'Gross PnL':<12} {'Friction':<12} {'Net PnL':<12} {'Target ₹2k/day'}")
    print("-" * 85)
    total_net_b = 0.0
    days_above_2k = 0
    for s in daily_summaries_b:
        wr = f"{(s['wins']/s['trades']*100):.1f}%" if s["trades"] > 0 else "0.0%"
        total_net_b += s["net"]
        hit_2k = s["net"] >= 2000.0
        if hit_2k:
            days_above_2k += 1
        status = "PASSED [YES]" if hit_2k else "BELOW [NO]"
        print(f"{s['date']:<11} {s['trades']:<8} {s['wins']:<6} {wr:<10} ₹{s['gross']:+<11,.2f} -₹{s['friction']:<10,.2f} ₹{s['net']:+<11,.2f} {status}")

    avg_daily_net_b = total_net_b / len(test_dates)
    print("-" * 85)
    print(f"Total 1-Week Net PnL (Model B): ₹{total_net_b:+,.2f} | Average Daily Net: ₹{avg_daily_net_b:+,.2f}/day")
    print(f"Sessions Meeting >₹2,000 Net Threshold: {days_above_2k} of 5 days")
    print("=" * 80)

    # Save summary artifact JSON
    report_artifact = {
        "evaluation_period": {"start": test_dates[0], "end": test_dates[-1], "sessions": len(test_dates)},
        "universe": "Groww MIS Approved Nifty 50 Category 1",
        "model_a_baseline": {
            "risk_per_trade_inr": 200.0,
            "total_net_pnl_inr": round(total_net_a, 2),
            "average_daily_net_pnl_inr": round(avg_daily_net_a, 2),
            "daily_summaries": daily_summaries_a
        },
        "model_b_scaled_target": {
            "risk_per_trade_inr": 750.0,
            "total_net_pnl_inr": round(total_net_b, 2),
            "average_daily_net_pnl_inr": round(avg_daily_net_b, 2),
            "days_above_2000": days_above_2k,
            "daily_summaries": daily_summaries_b
        }
    }
    with open(os.path.join(os.path.dirname(__file__), "trader", "week_audit.json"), "w", encoding="utf-8") as fh:
        json.dump(report_artifact, fh, indent=2)


if __name__ == "__main__":
    run_full_week_simulation()
