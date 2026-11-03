from evaluate_regimes_and_rules import load_cached_bars, find_candidate_for_day, GROWW_UNIVERSE

nifty_df = load_cached_bars('^NSEI')
symbol_dfs = {sym: load_cached_bars(sym) for sym in GROWW_UNIVERSE}

test_dates = [
    '2026-10-12', '2026-10-13', '2026-10-14', '2026-10-15', '2026-10-16',
    '2026-10-19', '2026-10-21', '2026-10-22', '2026-10-23',
    '2026-10-26', '2026-10-27', '2026-10-28', '2026-10-29', '2026-10-30'
]

for d in test_dates:
    c = find_candidate_for_day(d, nifty_df, symbol_dfs)
    if c:
        t_str = c['time'].strftime("%H:%M")
        print(f"{d} | Entry Time: {t_str} | {c['sym']:<14} | {c['side']:<4} | Entry: ₹{c['entry']:.2f}")
