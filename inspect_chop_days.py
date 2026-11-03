import pandas as pd
from evaluate_regimes_and_rules import load_cached_bars, GROWW_UNIVERSE

nifty_df = load_cached_bars('^NSEI')

days = ['2026-10-19', '2026-10-21', '2026-10-22', '2026-10-23']
for d in days:
    ns = nifty_df[nifty_df['d'] == d]
    o, c, h, l = ns['Open'].iloc[0], ns['Close'].iloc[-1], ns['High'].max(), ns['Low'].min()
    rng_pct = (h - l) / o * 100
    or_bars = ns.between_time('09:15', '09:40')
    or_h, or_l = or_bars['High'].max(), or_bars['Low'].min()
    or_pct = (or_h - or_l) / o * 100
    print(f"{d} | Nifty Open: {o:.1f} | Close: {c:.1f} | Day Range: {rng_pct:.2f}% | 30m OR Range: {or_pct:.2f}%")
