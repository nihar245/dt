import os
import pandas as pd
from evaluate_regimes_and_rules import load_cached_bars, find_candidate_for_day, GROWW_UNIVERSE

nifty_df = load_cached_bars('^NSEI')
symbol_dfs = {sym: load_cached_bars(sym) for sym in GROWW_UNIVERSE}

test_dates = [
    '2026-10-12', '2026-10-13', '2026-10-14', '2026-10-15', '2026-10-16',
    '2026-10-19', '2026-10-21', '2026-10-22', '2026-10-23',
    '2026-10-26', '2026-10-27', '2026-10-28', '2026-10-29', '2026-10-30'
]

print(f"{'Date':<11} | {'Symbol':<14} | {'Side':<4} | {'Entry':<8} | {'Risk':<6} | {'Max Fav (R)':<12} | {'Max Adv (R)':<12} | {'EOD (R)':<8}")
print("-" * 88)

for d in test_dates:
    c = find_candidate_for_day(d, nifty_df, symbol_dfs)
    if not c:
        continue
    sub = c['sess'][c['sess'].index > c['time']].between_time('09:45', '15:15')
    entry, risk, long = c['entry'], c['risk'], c['side'] == 'BUY'
    if long:
        max_fav = (sub['High'].max() - entry) / risk
        max_adv = (entry - sub['Low'].min()) / risk
        eod_r = (sub['Close'].iloc[-1] - entry) / risk
    else:
        max_fav = (entry - sub['Low'].min()) / risk
        max_adv = (sub['High'].max() - entry) / risk
        eod_r = (entry - sub['Close'].iloc[-1]) / risk
    print(f"{d:<11} | {c['sym']:<14} | {c['side']:<4} | {entry:<8.2f} | {risk:<6.2f} | {max_fav:+6.2f}R      | {max_adv:6.2f}R      | {eod_r:+6.2f}R")
