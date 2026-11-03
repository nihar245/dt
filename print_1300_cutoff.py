from test_entry_cutoff import find_candidate_window, simulate_trade, policy_b, test_dates

print(f"{'Date':<11} | {'Symbol':<14} | {'Side':<4} | {'Time':<5} | {'Outcome':<12} | {'Net PnL':<10}")
print("-" * 68)
tot = 0
for d in test_dates:
    c = find_candidate_window(d, '13:00')
    if c:
        res = simulate_trade(c, policy_b)
        tot += res['net']
        t_str = c['time'].strftime('%H:%M')
        print(f"{d:<11} | {c['sym']:<14} | {c['side']:<4} | {t_str:<5} | {res['outcome']:<12} | ₹{res['net']:+8.2f}")
    else:
        print(f"{d:<11} | --- NO QUALIFIED MORNING TRADE (₹0.00) ---")
print("-" * 68)
print(f"Total Net: ₹{tot:+.2f}")
