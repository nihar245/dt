#!/usr/bin/env python3
import os, sys, json, warnings, datetime, pandas as pd, yfinance as yf
warnings.filterwarnings("ignore")
if hasattr(sys.stdout, "reconfigure"):
    try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except: pass

from evaluate_filtered import fetch_symbol_bars, compute_vwap, get_nifty_regime, GROWW_UNIVERSE

def evaluate_ultra_selective():
    test_dates = ["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30"]
    nifty_df = fetch_symbol_bars("^NSEI")
    
    all_trades = []
    daily_stats = []

    for d in test_dates:
        nifty_info = get_nifty_regime(nifty_df, d)
        candidates = []
        for sym in GROWW_UNIVERSE:
            df = fetch_symbol_bars(sym)
            if df.empty: continue
            df["d"] = df.index.strftime("%Y-%m-%d")
            sess = df[df["d"] == d]
            if len(sess) < 20: continue
            sess = sess.copy()
            sess["vwap"] = compute_vwap(sess)
            or_bars = sess.between_time("09:15", "09:40")
            if len(or_bars) < 6: continue
            or_h, or_l = float(or_bars["High"].max()), float(or_bars["Low"].min())
            or_mid = (or_h + or_l) / 2.0
            if (or_h - or_l) / or_mid < 0.006: continue # strong volatility expansion only
            avg_vol = float(or_bars["Volume"].mean())

            for b_time, b in sess.between_time("09:45", "14:00").iterrows():
                c, o, h, l, v, vwap = float(b["Close"]), float(b["Open"]), float(b["High"]), float(b["Low"]), float(b["Volume"]), float(b["vwap"])
                n_bars = nifty_info["sess"][nifty_info["sess"].index <= b_time]
                if n_bars.empty: continue
                n_close = float(n_bars["Close"].iloc[-1])

                # Long: Stock > ORH & VWAP, Nifty > OR_MID & OPEN, Volume > OR_AVG
                if min(o, c) > or_h and c > vwap and n_close > nifty_info["mid"] and n_close > nifty_info["open"] and v >= 1.0 * avg_vol:
                    risk = c - or_mid
                    if risk > 0:
                        candidates.append({"sym": sym, "side": "BUY", "entry": c, "stop": or_mid, "target": round(c + 1.4 * risk, 2), "risk": risk, "qty": max(1, int(850.0 / risk)), "sess": sess, "time": b_time, "score": (c - vwap)/vwap * 100})
                        break
                # Short: Stock < ORL & VWAP, Nifty < OR_MID & OPEN, Volume > OR_AVG
                elif max(o, c) < or_l and c < vwap and n_close < nifty_info["mid"] and n_close < nifty_info["open"] and v >= 1.0 * avg_vol:
                    risk = or_mid - c
                    if risk > 0:
                        candidates.append({"sym": sym, "side": "SELL", "entry": c, "stop": or_mid, "target": round(c - 1.4 * risk, 2), "risk": risk, "qty": max(1, int(850.0 / risk)), "sess": sess, "time": b_time, "score": (vwap - c)/vwap * 100})
                        break
        
        candidates.sort(key=lambda x: -x["score"])
        # Take ONLY the single best trade of the day (max 1 trade), or 0 if nothing qualifies
        chosen = candidates[:1]
        
        day_trades = []
        for t in chosen:
            entry, stop, target, qty, long = t["entry"], t["stop"], t["target"], t["qty"], t["side"] == "BUY"
            subsequent = t["sess"][t["sess"].index > t["time"]].between_time("09:45", "15:15")
            exit_price, outcome = float(subsequent["Close"].iloc[-1]), "EOD_FLAT"
            trailed = False
            trailed_stop = stop
            for b_time, b in subsequent.iterrows():
                bh, bl = float(b["High"]), float(b["Low"])
                if long:
                    if not trailed and bh >= (entry + 0.8 * t["risk"]):
                        trailed_stop = entry + (60.0 / qty)
                        trailed = True
                    if bl <= trailed_stop:
                        outcome, exit_price = ("TRAILED_STOP" if trailed else "STOP"), trailed_stop
                        break
                    elif bh >= target:
                        outcome, exit_price = "TARGET", target
                        break
                else:
                    if not trailed and bl <= (entry - 0.8 * t["risk"]):
                        trailed_stop = entry - (60.0 / qty)
                        trailed = True
                    if bh >= trailed_stop:
                        outcome, exit_price = ("TRAILED_STOP" if trailed else "STOP"), trailed_stop
                        break
                    elif bl <= target:
                        outcome, exit_price = "TARGET", target
                        break
            gross = round(qty * (exit_price - entry if long else entry - exit_price), 2)
            net = round(gross - 60.0, 2)
            day_trades.append({"date": d, "sym": t["sym"], "side": t["side"], "outcome": outcome, "gross": gross, "net": net})

        all_trades.extend(day_trades)
        d_gross = sum(x["gross"] for x in day_trades)
        d_net = sum(x["net"] for x in day_trades)
        daily_stats.append({"date": d, "trades": len(day_trades), "gross": d_gross, "net": d_net})

    print("\n=== ULTRA-CONVICTION (1 TRADE/DAY MAX, NO FORCED TRADES) ===")
    for t in all_trades:
        print(f"{t['date']} | {t['sym']:<14} | {t['side']} | Outcome: {t['outcome']:<12} | Gross: {t['gross']:+,.2f} | Net: {t['net']:+,.2f}")
    print("\n--- Daily Session Results ---")
    tot_net = 0
    for s in daily_stats:
        tot_net += s["net"]
        print(f"{s['date']} | Trades: {s['trades']} | Gross: ₹{s['gross']:+,.2f} | Net: ₹{s['net']:+,.2f}")
    print(f"Total 1-Week Net: ₹{tot_net:+,.2f} | Avg Daily: ₹{tot_net/5:+,.2f}/day")

if __name__ == "__main__":
    evaluate_ultra_selective()
