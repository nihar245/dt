import os
import json
import pandas as pd
from evaluate_regimes_and_rules import load_cached_bars, compute_vwap, get_nifty_regime, simulate_trade, GROWW_UNIVERSE

nifty_df = load_cached_bars('^NSEI')
symbol_dfs = {sym: load_cached_bars(sym) for sym in GROWW_UNIVERSE}

test_dates = [
    '2026-10-12', '2026-10-13', '2026-10-14', '2026-10-15', '2026-10-16',
    '2026-10-19', '2026-10-21', '2026-10-22', '2026-10-23',
    '2026-10-26', '2026-10-27', '2026-10-28', '2026-10-29', '2026-10-30'
]

def find_candidate_window(d: str, max_time_str: str):
    nifty_info = get_nifty_regime(nifty_df, d)
    if not nifty_info:
        return None
    candidates = []
    for sym, df in symbol_dfs.items():
        sess = df[df['d'] == d]
        if len(sess) < 20: continue
        sess = sess.copy()
        sess['vwap'] = compute_vwap(sess)
        or_bars = sess.between_time('09:15', '09:40')
        if len(or_bars) < 6: continue
        or_h = float(or_bars['High'].max())
        or_l = float(or_bars['Low'].min())
        or_mid = (or_h + or_l) / 2.0
        if (or_h - or_l) / or_mid < 0.005: continue
        avg_vol = float(or_bars['Volume'].mean())

        for b_time, b in sess.between_time('09:45', max_time_str).iterrows():
            c, o, h, l, v, vwap = float(b['Close']), float(b['Open']), float(b['High']), float(b['Low']), float(b['Volume']), float(b['vwap'])
            n_bars = nifty_info['sess'][nifty_info['sess'].index <= b_time]
            if n_bars.empty: continue
            n_close = float(n_bars['Close'].iloc[-1])

            if min(o, c) > or_h and c > vwap and n_close > nifty_info['mid'] and n_close > nifty_info['open'] and v >= 1.0 * avg_vol:
                risk = c - or_mid
                if risk > 0:
                    qty = max(1, int(850.0 / risk))
                    candidates.append({'date': d, 'sym': sym, 'side': 'BUY', 'entry': c, 'stop': or_mid, 'risk': risk, 'qty': qty, 'time': b_time, 'sess': sess, 'score': (c - vwap)/vwap * 100})
                    break
            elif max(o, c) < or_l and c < vwap and n_close < nifty_info['mid'] and n_close < nifty_info['open'] and v >= 1.0 * avg_vol:
                risk = or_mid - c
                if risk > 0:
                    qty = max(1, int(850.0 / risk))
                    candidates.append({'date': d, 'sym': sym, 'side': 'SELL', 'entry': c, 'stop': or_mid, 'risk': risk, 'qty': qty, 'time': b_time, 'sess': sess, 'score': (vwap - c)/vwap * 100})
                    break
    if not candidates: return None
    candidates.sort(key=lambda x: -x['score'])
    return candidates[0]

policy_b = {"target_r": 1.5, "be_trigger_r": 0.7, "lock_trigger_r": None, "lock_profit_r": None}
# Also test policy with profit lock at 1.0R (e.g. at 1.0R lock +0.6R)
policy_lock = {"target_r": 1.5, "be_trigger_r": 0.7, "lock_trigger_r": 1.0, "lock_profit_r": 0.6}

windows = ["14:00", "13:00", "12:30", "12:00"]
for max_t in windows:
    print(f"\n=================== ENTRY CUTOFF: {max_t} ===================")
    for p_name, pol in [("Policy B (BE@0.7R, Tgt 1.5R)", policy_b), ("Policy Lock (BE@0.7R, Lock@1.0R->0.6R)", policy_lock)]:
        trades = []
        for d in test_dates:
            cand = find_candidate_window(d, max_t)
            if cand:
                res = simulate_trade(cand, pol)
                trades.append(res)
        n = len(trades)
        wins = sum(1 for t in trades if t['net'] > 0)
        wr = (wins / n * 100) if n else 0
        net = sum(t['net'] for t in trades)
        gross = sum(t['gross'] for t in trades)
        target_days = sum(1 for t in trades if t['net'] >= 1000)
        print(f"  {p_name:<42} | Trades: {n:2d} | Win%: {wr:5.1f}% | Net: ₹{net:+9.2f} | Target Days: {target_days}")
