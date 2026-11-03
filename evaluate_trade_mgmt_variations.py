import os
import json
import pandas as pd
from test_entry_cutoff import find_candidate_window, test_dates, nifty_df, symbol_dfs

candidates_1300 = {d: find_candidate_window(d, '13:00') for d in test_dates}

def simulate_advanced_mgmt(c, target_r=1.5, be_r=0.7, lock_trigger=1.0, lock_profit=0.5, exit_stale_1430=True):
    if not c:
        return None
    entry, stop, risk, qty = c['entry'], c['stop'], c['risk'], c['qty']
    long = c['side'] == 'BUY'
    target = round(entry + target_r * risk if long else entry - target_r * risk, 2)
    fee = 60.0
    sub = c['sess'][c['sess'].index > c['time']].between_time('09:45', '15:15')
    if sub.empty:
        return {"outcome": "NO_BARS", "net": -fee, "gross": 0.0}

    curr_stop = stop
    be_active = False
    lock_active = False
    exit_price = float(sub['Close'].iloc[-1])
    outcome = "EOD_FLAT"

    for b_time, b in sub.iterrows():
        bh, bl, bc = float(b['High']), float(b['Low']), float(b['Close'])
        time_str = b_time.strftime('%H:%M')

        # 1. Profit Locking
        if lock_trigger and not lock_active:
            fav_r = (bh - entry)/risk if long else (entry - bl)/risk
            if fav_r >= lock_trigger:
                curr_stop = entry + lock_profit * risk if long else entry - lock_profit * risk
                lock_active = True

        # 2. Breakeven Trail
        if be_r and not be_active and not lock_active:
            fav_r = (bh - entry)/risk if long else (entry - bl)/risk
            if fav_r >= be_r:
                curr_stop = entry + (fee / qty) if long else entry - (fee / qty)
                be_active = True

        # 3. Check Stop
        if (long and bl <= curr_stop) or (not long and bh >= curr_stop):
            outcome = "LOCK_PROFIT" if lock_active else ("BREAKEVEN" if be_active else "STOP_LOSS")
            exit_price = curr_stop
            break

        # 4. Check Target
        if (long and bh >= target) or (not long and bl <= target):
            outcome = "TARGET"
            exit_price = target
            break

        # 5. Stale Trade Rule at 14:30 IST:
        # If past 14:30 and trade is either slightly in profit or stagnant, protect capital
        if exit_stale_1430 and time_str >= "14:30":
            fav_r = (bc - entry)/risk if long else (entry - bc)/risk
            if fav_r > 0.4:
                # Lock in whatever profit is available at 14:30
                outcome = "1430_PROFIT_CLOSE"
                exit_price = bc
                break
            elif fav_r < -0.3:
                # Cut stagnant loser early instead of waiting for full EOD slide
                outcome = "1430_CUT_EARLY"
                exit_price = bc
                break

    gross = round(qty * (exit_price - entry if long else entry - exit_price), 2)
    net = round(gross - fee, 2)
    return {"date": c['date'], "sym": c['sym'], "side": c['side'], "outcome": outcome, "gross": gross, "net": net}

print(f"{'Variation Description':<55} | {'Win%':<6} | {'Gross PnL':<11} | {'Net PnL':<11} | {'Target Days':<10}")
print("-" * 105)

test_configs = [
    ("Config 1: Target 1.5R, BE@0.7R (Standard)", 1.5, 0.7, None, None, False),
    ("Config 2: Target 1.4R, BE@0.7R (Faster Target)", 1.4, 0.7, None, None, False),
    ("Config 3: Target 1.5R, BE@0.65R (Tighter BE)", 1.5, 0.65, None, None, False),
    ("Config 4: Target 1.5R, BE@0.7R + Lock@1.0R->0.6R", 1.5, 0.7, 1.0, 0.6, False),
    ("Config 5: Target 1.4R, BE@0.7R + 14:30 Stale Rule", 1.4, 0.7, None, None, True),
    ("Config 6: Target 1.5R, BE@0.7R + 14:30 Stale Rule", 1.5, 0.7, None, None, True),
    ("Config 7: Target 1.3R, BE@0.6R + 14:30 Stale Rule", 1.3, 0.6, None, None, True),
]

best_res = None
best_net = -999999

for name, tgt, be, lock_t, lock_p, stale in test_configs:
    trades = []
    for d in test_dates:
        cand = candidates_1300[d]
        if cand:
            res = simulate_advanced_mgmt(cand, target_r=tgt, be_r=be, lock_trigger=lock_t, lock_profit=lock_p, exit_stale_1430=stale)
            trades.append(res)
    n = len(trades)
    wins = sum(1 for t in trades if t['net'] > 0)
    wr = (wins/n*100) if n else 0
    gross = sum(t['gross'] for t in trades)
    net = sum(t['net'] for t in trades)
    tdays = sum(1 for t in trades if t['net'] >= 1000)
    print(f"{name:<55} | {wr:5.1f}% | ₹{gross:+10.2f} | ₹{net:+10.2f} | {tdays:<10d}")
    if net > best_net:
        best_net = net
        best_res = (name, trades)

print("\n" + "=" * 80)
print(f"WINNING TRADE MANAGEMENT VARIATION: {best_res[0]}")
print("=" * 80)
for t in best_res[1]:
    print(f"  {t['date']} | {t['sym']:<14} | {t['side']:<4} | {t['outcome']:<18} | Gross: ₹{t['gross']:+9.2f} | Net: ₹{t['net']:+9.2f}")
