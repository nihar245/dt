#!/usr/bin/env python3
"""Multi-Regime Backtesting & Trade Management Optimization Engine for NSE/Groww.

Evaluates:
  Option 2: Robustness across 3 distinct volatility regimes:
    - Regime 1: Bear / Pullback Week (2026-10-19 to 2026-10-23)
    - Regime 2: Bull / Trend Week (2026-10-26 to 2026-10-30)
    - Regime 3: High-Volatility Whiplash Week (2026-10-12 to 2026-10-16)
    - Cumulative 3-Week Full Window (15 trading days)
  
  Option 3: Trade Management Rules Comparison:
    - Rule A (Current Baseline): Target 1.4R, BE at +0.8R
    - Rule B (Config Default): Target 1.5R, BE at +0.7R
    - Rule C (Two-Tier Profit Lock): Target 1.5R, BE at +0.7R, Lock +0.5R at +1.1R
    - Rule D (Ambitious Target): Target 1.8R, BE at +0.8R, Lock +0.8R at +1.3R
    - Rule E (Static No-Trail): Target 1.5R, Static Stop (or_mid), No BE
"""

import os
import sys
import json
import warnings
import pandas as pd
import numpy as np

warnings.filterwarnings("ignore")
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DATA_DIR = os.path.join(os.path.dirname(__file__), "trader", "data_cache_60d")

GROWW_UNIVERSE = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
    "BHARTIARTL.NS", "ITC.NS", "SBIN.NS", "LT.NS", "BAJFINANCE.NS",
    "HCLTECH.NS", "MARUTI.NS", "SUNPHARMA.NS", "KOTAKBANK.NS", "AXISBANK.NS",
    "NTPC.NS", "TITAN.NS", "POWERGRID.NS", "TATASTEEL.NS"
]

def load_cached_bars(symbol: str) -> pd.DataFrame:
    path = os.path.join(DATA_DIR, f"{symbol}_5m.csv")
    if not os.path.exists(path):
        return pd.DataFrame()
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    df["d"] = df.index.strftime("%Y-%m-%d")
    return df

def compute_vwap(df_sess: pd.DataFrame) -> pd.Series:
    tp = (df_sess["High"] + df_sess["Low"] + df_sess["Close"]) / 3.0
    vol = df_sess["Volume"].replace(0, 1)
    cum_tp_vol = (tp * vol).cumsum()
    cum_vol = vol.cumsum()
    return cum_tp_vol / cum_vol

def get_nifty_regime(nifty_df: pd.DataFrame, d: str):
    sess = nifty_df[nifty_df["d"] == d]
    if len(sess) < 20:
        return None
    or_bars = sess.between_time("09:15", "09:40")
    if len(or_bars) < 6:
        return None
    nh = float(or_bars["High"].max())
    nl = float(or_bars["Low"].min())
    nmid = (nh + nl) / 2.0
    nopen = float(sess["Open"].iloc[0])
    nclose = float(sess["Close"].iloc[-1])
    nchg = (nclose / nopen - 1.0) * 100.0
    return {"high": nh, "low": nl, "mid": nmid, "open": nopen, "close": nclose, "chg_pct": nchg, "sess": sess}

def find_candidate_for_day(d: str, nifty_df: pd.DataFrame, symbol_dfs: dict):
    nifty_info = get_nifty_regime(nifty_df, d)
    if not nifty_info:
        return None

    candidates = []
    for sym, df in symbol_dfs.items():
        sess = df[df["d"] == d]
        if len(sess) < 20:
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

        for b_time, b in sess.between_time("09:45", "14:00").iterrows():
            c, o, h, l, v, vwap = float(b["Close"]), float(b["Open"]), float(b["High"]), float(b["Low"]), float(b["Volume"]), float(b["vwap"])
            n_bars = nifty_info["sess"][nifty_info["sess"].index <= b_time]
            if n_bars.empty:
                continue
            n_close = float(n_bars["Close"].iloc[-1])

            # Long breakout
            if min(o, c) > or_h and c > vwap and n_close > nifty_info["mid"] and n_close > nifty_info["open"] and v >= 1.0 * avg_vol:
                risk = c - or_mid
                if risk > 0:
                    qty = max(1, int(850.0 / risk))
                    score = (c - vwap) / vwap * 100.0
                    candidates.append({
                        "date": d, "sym": sym, "side": "BUY", "entry": c, "stop": or_mid,
                        "risk": risk, "qty": qty, "time": b_time, "sess": sess, "score": score
                    })
                    break
            # Short breakdown
            elif max(o, c) < or_l and c < vwap and n_close < nifty_info["mid"] and n_close < nifty_info["open"] and v >= 1.0 * avg_vol:
                risk = or_mid - c
                if risk > 0:
                    qty = max(1, int(850.0 / risk))
                    score = (vwap - c) / vwap * 100.0
                    candidates.append({
                        "date": d, "sym": sym, "side": "SELL", "entry": c, "stop": or_mid,
                        "risk": risk, "qty": qty, "time": b_time, "sess": sess, "score": score
                    })
                    break

    if not candidates:
        return None
    candidates.sort(key=lambda x: -x["score"])
    return candidates[0]

def simulate_trade(cand: dict, policy: dict):
    """Simulate trade execution under a specific trade management policy."""
    entry = cand["entry"]
    stop = cand["stop"]
    risk = cand["risk"]
    qty = cand["qty"]
    long = cand["side"] == "BUY"
    target = round(entry + policy["target_r"] * risk if long else entry - policy["target_r"] * risk, 2)
    fee = 60.0

    subsequent = cand["sess"][cand["sess"].index > cand["time"]].between_time("09:45", "15:15")
    if subsequent.empty:
        return {"outcome": "NO_BARS", "gross": 0.0, "net": -fee, "exit_price": entry}

    exit_price = float(subsequent["Close"].iloc[-1])
    outcome = "EOD_FLAT"
    curr_stop = stop
    be_triggered = False
    lock_triggered = False

    for b_time, b in subsequent.iterrows():
        bh, bl = float(b["High"]), float(b["Low"])
        if long:
            # Check secondary lock trigger
            if policy.get("lock_trigger_r") and not lock_triggered and bh >= (entry + policy["lock_trigger_r"] * risk):
                curr_stop = entry + policy["lock_profit_r"] * risk
                lock_triggered = True
            # Check breakeven trigger
            elif policy.get("be_trigger_r") and not be_triggered and not lock_triggered and bh >= (entry + policy["be_trigger_r"] * risk):
                curr_stop = entry + (fee / qty)
                be_triggered = True

            # Check stop hit
            if bl <= curr_stop:
                if lock_triggered:
                    outcome, exit_price = "LOCK_PROFIT", curr_stop
                elif be_triggered:
                    outcome, exit_price = "BREAKEVEN", curr_stop
                else:
                    outcome, exit_price = "STOP_LOSS", curr_stop
                break
            # Check target hit
            elif bh >= target:
                outcome, exit_price = "TARGET", target
                break
        else:
            # Short side
            if policy.get("lock_trigger_r") and not lock_triggered and bl <= (entry - policy["lock_trigger_r"] * risk):
                curr_stop = entry - policy["lock_profit_r"] * risk
                lock_triggered = True
            elif policy.get("be_trigger_r") and not be_triggered and not lock_triggered and bl <= (entry - policy["be_trigger_r"] * risk):
                curr_stop = entry - (fee / qty)
                be_triggered = True

            if bh >= curr_stop:
                if lock_triggered:
                    outcome, exit_price = "LOCK_PROFIT", curr_stop
                elif be_triggered:
                    outcome, exit_price = "BREAKEVEN", curr_stop
                else:
                    outcome, exit_price = "STOP_LOSS", curr_stop
                break
            elif bl <= target:
                outcome, exit_price = "TARGET", target
                break

    gross = round(qty * (exit_price - entry if long else entry - exit_price), 2)
    net = round(gross - fee, 2)
    return {
        "date": cand["date"], "sym": cand["sym"], "side": cand["side"],
        "entry": entry, "exit_price": exit_price, "outcome": outcome,
        "gross": gross, "fee": fee, "net": net, "target": target
    }

def main():
    print("=" * 80)
    print("MULTI-REGIME ROBUSTNESS & TRADE MANAGEMENT OPTIMIZATION")
    print("=" * 80)

    nifty_df = load_cached_bars("^NSEI")
    symbol_dfs = {sym: load_cached_bars(sym) for sym in GROWW_UNIVERSE}

    regimes = {
        "Regime 1: Bear Pullback (Oct 19 - Oct 23)": ["2026-10-19", "2026-10-21", "2026-10-22", "2026-10-23"],
        "Regime 2: Bull Trend (Oct 26 - Oct 30)":    ["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30"],
        "Regime 3: High Whiplash (Oct 12 - Oct 16)": ["2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16"],
    }

    all_dates = []
    for r_dates in regimes.values():
        all_dates.extend(r_dates)
    all_dates = sorted(list(set(all_dates)))

    policies = {
        "Policy A (Baseline: Target 1.4R, BE@0.8R)": {
            "target_r": 1.4, "be_trigger_r": 0.8, "lock_trigger_r": None, "lock_profit_r": None
        },
        "Policy B (Target 1.5R, BE@0.7R)": {
            "target_r": 1.5, "be_trigger_r": 0.7, "lock_trigger_r": None, "lock_profit_r": None
        },
        "Policy C (Two-Tier: 1.5R, BE@0.7R, Lock@1.1R->0.5R)": {
            "target_r": 1.5, "be_trigger_r": 0.7, "lock_trigger_r": 1.1, "lock_profit_r": 0.5
        },
        "Policy D (Ambitious: 1.8R, BE@0.8R, Lock@1.3R->0.8R)": {
            "target_r": 1.8, "be_trigger_r": 0.8, "lock_trigger_r": 1.3, "lock_profit_r": 0.8
        },
        "Policy E (Static No-Trail: 1.5R, Hard Stop)": {
            "target_r": 1.5, "be_trigger_r": None, "lock_trigger_r": None, "lock_profit_r": None
        }
    }

    # Step 1: Pre-calculate candidate trade for each date
    candidates = {}
    for d in all_dates:
        cand = find_candidate_for_day(d, nifty_df, symbol_dfs)
        candidates[d] = cand

    print("\n[Identified Trades Across 14 Testing Sessions]")
    for d in all_dates:
        c = candidates[d]
        if c:
            print(f"  {d} | {c['sym']:<14} | {c['side']:<4} | Entry: ₹{c['entry']:<9.2f} | Risk: ₹{c['risk']:<6.2f} | Qty: {c['qty']:<4d} | Score: {c['score']:+.2f}%")
        else:
            print(f"  {d} | --- NO TRADE QUALIFIED (Preserved Capital) ---")

    # Step 2: Evaluate each policy across each regime
    overall_policy_results = {p_name: [] for p_name in policies}

    for r_name, r_dates in regimes.items():
        print("\n" + "-" * 80)
        print(f"=== {r_name} ===")
        print("-" * 80)

        for p_name, policy in policies.items():
            trades = []
            for d in r_dates:
                cand = candidates[d]
                if cand:
                    res = simulate_trade(cand, policy)
                    trades.append(res)
                    overall_policy_results[p_name].append(res)
            
            n_trades = len(trades)
            wins = sum(1 for t in trades if t["net"] > 0)
            target_hits = sum(1 for t in trades if t["outcome"] == "TARGET")
            net_pnl = sum(t["net"] for t in trades)
            win_rate = (wins / n_trades * 100.0) if n_trades > 0 else 0.0
            days_target_met = sum(1 for t in trades if t["net"] >= 1000.0)
            
            print(f"  {p_name:<50} | Trades: {n_trades} | Win%: {win_rate:5.1f}% | Net PnL: ₹{net_pnl:+9.2f} | Target Met: {days_target_met} days")

    # Step 3: Cumulative 3-Week Summary
    print("\n" + "=" * 80)
    print("CUMULATIVE 3-WEEK PERFORMANCE MATRIX (14 Active Sessions)")
    print("=" * 80)
    print(f"{'Policy':<52} | {'Trades':<6} | {'Win%':<6} | {'Gross PnL':<11} | {'Friction':<9} | {'Net PnL':<11} | {'>=₹1k Days':<10}")
    print("-" * 115)

    summary_records = []
    for p_name, trades in overall_policy_results.items():
        n_trades = len(trades)
        wins = sum(1 for t in trades if t["net"] > 0)
        win_rate = (wins / n_trades * 100.0) if n_trades > 0 else 0.0
        gross = sum(t["gross"] for t in trades)
        fees = sum(t["fee"] for t in trades)
        net = sum(t["net"] for t in trades)
        target_days = sum(1 for t in trades if t["net"] >= 1000.0)
        summary_records.append({
            "policy": p_name, "trades": n_trades, "win_rate": win_rate,
            "gross": gross, "fees": fees, "net": net, "target_days": target_days,
            "trades_list": trades
        })
        print(f"{p_name:<52} | {n_trades:<6d} | {win_rate:5.1f}% | ₹{gross:+10.2f} | ₹{fees:7.2f} | ₹{net:+10.2f} | {target_days:<10d}")

    # Step 4: Output best policy's trade log
    best = max(summary_records, key=lambda x: x["net"])
    print("\n" + "=" * 80)
    print(f"OPTIMAL POLICY DETAILS: {best['policy']}")
    print("=" * 80)
    for t in best["trades_list"]:
        print(f"  {t['date']} | {t['sym']:<14} | {t['side']:<4} | Outcome: {t['outcome']:<12} | Gross: ₹{t['gross']:+9.2f} | Net: ₹{t['net']:+9.2f}")

    # Output JSON summary for artifact generation
    out_data = {
        "best_policy": best["policy"],
        "records": [{k: v for k, v in r.items() if k != "trades_list"} for r in summary_records],
        "best_trades": best["trades_list"]
    }
    with open("multi_regime_results.json", "w") as f:
        json.dump(out_data, f, indent=2)
    print("\nResults exported to multi_regime_results.json.")

if __name__ == "__main__":
    main()
