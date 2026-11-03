#!/usr/bin/env python3
"""Indian Tax & Brokerage Friction Engine for Intraday Day Trading (NSE/BSE).

Simulates standard statutory Indian market trading frictions:
  - Flat Brokerage: ₹20 per executed leg (₹40 round-trip)
  - STT (Securities Transaction Tax): 0.025% on sell-side turnover (intraday equity)
  - Exchange Transaction Charges: ~0.00297% (NSE)
  - GST: 18% on (Brokerage + Exchange Charges)
  - SEBI Turnover Charges: ₹10 per crore (0.0001%)
  - Stamp Duty: 0.003% on buy-side turnover
  - Flat Standard Friction Benchmark: ₹60 flat per round-trip trade

Automatically subtracts this fee from Gross Paper PnL to reflect realistic Net PnL.
"""

import argparse
import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DEFAULT_FLAT_FRICTION = 60.0  # INR per round-trip trade

# Statutory rate constants for Intraday Equity (NSE)
BROKERAGE_PER_LEG = 20.0        # ₹20 flat discount broker fee
STT_SELL_RATE = 0.00025         # 0.025% on sell turnover
EXCHANGE_TXN_RATE = 0.0000297   # 0.00297% on both sides
GST_RATE = 0.18                 # 18% on (brokerage + exchange charges + sebi fees)
SEBI_RATE = 0.000001            # 0.0001%
STAMP_DUTY_BUY_RATE = 0.00003   # 0.003% on buy turnover


def calculate_flat_friction(num_trades: int, flat_fee: float = DEFAULT_FLAT_FRICTION) -> float:
    """Calculate total friction using flat round-trip benchmark."""
    return round(float(num_trades) * float(flat_fee), 2)


def calculate_net_pnl(gross_pnl: float, num_trades: int, flat_fee: float = DEFAULT_FLAT_FRICTION) -> dict:
    """Compute Net PnL from Gross PnL after subtracting round-trip friction."""
    friction = calculate_flat_friction(num_trades, flat_fee)
    net_pnl = round(float(gross_pnl) - friction, 2)
    return {
        "currency": "INR",
        "gross_pnl": round(float(gross_pnl), 2),
        "num_trades": int(num_trades),
        "flat_fee_per_trade": round(float(flat_fee), 2),
        "total_friction": friction,
        "net_pnl": net_pnl,
        "is_profitable": net_pnl > 0.0
    }


def calculate_itemized_friction(buy_turnover: float, sell_turnover: float, brokerage_per_leg: float = 20.0) -> dict:
    """Detailed SEBI & Exchange statutory friction breakdown for intraday equity."""
    total_turnover = buy_turnover + sell_turnover
    brokerage = brokerage_per_leg * 2.0
    stt = round(sell_turnover * STT_SELL_RATE, 2)
    exchange_charges = round(total_turnover * EXCHANGE_TXN_RATE, 2)
    sebi_charges = round(total_turnover * SEBI_RATE, 2)
    stamp_duty = round(buy_turnover * STAMP_DUTY_BUY_RATE, 2)
    taxable_services = brokerage + exchange_charges + sebi_charges
    gst = round(taxable_services * GST_RATE, 2)
    total_statutory = round(brokerage + stt + exchange_charges + gst + sebi_charges + stamp_duty, 2)

    return {
        "buy_turnover": round(buy_turnover, 2),
        "sell_turnover": round(sell_turnover, 2),
        "total_turnover": round(total_turnover, 2),
        "brokerage": brokerage,
        "stt": stt,
        "exchange_charges": exchange_charges,
        "gst": gst,
        "sebi_charges": sebi_charges,
        "stamp_duty": stamp_duty,
        "total_statutory_friction": total_statutory,
        "benchmark_flat_friction": DEFAULT_FLAT_FRICTION
    }


def audit_trade_session(trades: list, flat_fee: float = DEFAULT_FLAT_FRICTION) -> dict:
    """Audit a list of session trades, computing gross and net pnl after ₹60 friction."""
    total_gross = 0.0
    num_trades = len(trades)
    trade_details = []

    for idx, tr in enumerate(trades, 1):
        gross = float(tr.get("gross_pnl", 0.0))
        net = round(gross - flat_fee, 2)
        total_gross += gross
        trade_details.append({
            "trade_id": idx,
            "symbol": tr.get("symbol", "UNKNOWN.NS"),
            "qty": tr.get("qty", 0),
            "entry": tr.get("entry", 0.0),
            "exit": tr.get("exit", 0.0),
            "gross_pnl": gross,
            "friction": flat_fee,
            "net_pnl": net
        })

    summary = calculate_net_pnl(total_gross, num_trades, flat_fee)
    summary["trades"] = trade_details
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description="Indian Tax & Brokerage Friction Engine (NSE/BSE).")
    ap.add_argument("--gross-pnl", type=float, help="Gross paper trading PnL in INR")
    ap.add_argument("--trades", type=int, default=1, help="Number of completed round-trip trades (default: 1)")
    ap.add_argument("--flat-fee", type=float, default=DEFAULT_FLAT_FRICTION, help="Flat fee per trade in INR (default: ₹60)")
    ap.add_argument("--log", help="Path to trade execution log JSON file")
    ap.add_argument("--out", help="Optional path to save net PnL report JSON")
    ap.add_argument("--itemized", action="store_true", help="Show detailed statutory tax breakdown")
    ap.add_argument("--buy-turnover", type=float, default=50000.0, help="Buy turnover for itemized breakdown")
    ap.add_argument("--sell-turnover", type=float, default=50500.0, help="Sell turnover for itemized breakdown")
    ap.add_argument("--json", action="store_true", help="Print structured JSON output")
    a = ap.parse_args(argv)

    if a.itemized:
        res = calculate_itemized_friction(a.buy_turnover, a.sell_turnover)
        if a.json:
            print(json.dumps(res, indent=2))
        else:
            print("==================================================")
            print(" Indian Market Intraday Statutory Friction Model ")
            print("==================================================")
            print("Buy Turnover:          ₹{:,.2f}".format(res["buy_turnover"]))
            print("Sell Turnover:         ₹{:,.2f}".format(res["sell_turnover"]))
            print("Total Turnover:        ₹{:,.2f}".format(res["total_turnover"]))
            print("--------------------------------------------------")
            print("Brokerage (Flat ₹20x2): ₹{:,.2f}".format(res["brokerage"]))
            print("STT (0.025% on Sell):  ₹{:,.2f}".format(res["stt"]))
            print("Exchange Txn (NSE):    ₹{:,.2f}".format(res["exchange_charges"]))
            print("GST (18% on Services): ₹{:,.2f}".format(res["gst"]))
            print("SEBI Charges:          ₹{:,.2f}".format(res["sebi_charges"]))
            print("Stamp Duty (0.003%):   ₹{:,.2f}".format(res["stamp_duty"]))
            print("--------------------------------------------------")
            print("Total Statutory Fee:   ₹{:,.2f}".format(res["total_statutory_friction"]))
            print("Standard Benchmark:    ₹{:,.2f} per round-trip".format(res["benchmark_flat_friction"]))
            print("==================================================")
        return 0

    if a.log and os.path.exists(a.log):
        with open(a.log, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        trades = data if isinstance(data, list) else data.get("trades", [])
        res = audit_trade_session(trades, a.flat_fee)
    elif a.gross_pnl is not None:
        res = calculate_net_pnl(a.gross_pnl, a.trades, a.flat_fee)
    else:
        ap.print_help()
        return 1

    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(res, fh, indent=2)

    if a.json:
        print(json.dumps(res, indent=2))
    else:
        print("==================================================")
        print("  Indian Market Net PnL Friction Report (NSE/BSE) ")
        print("==================================================")
        print("Completed Trades:      %d" % res["num_trades"])
        print("Gross Paper PnL:       ₹{:,.2f}".format(res["gross_pnl"]))
        print("Friction Deducted:     -₹{:,.2f} (flat ₹{:,.2f}/trade)".format(res["total_friction"], res["flat_fee_per_trade"]))
        print("--------------------------------------------------")
        net_str = "₹{:,.2f}".format(res["net_pnl"])
        print("Actual Net PnL:        %s%s" % ("+" if res["net_pnl"] > 0 else "", net_str))
        print("Performance Verdict:   %s" % ("PROFITABLE" if res["is_profitable"] else "LOSS / BREAKEVEN"))
        print("==================================================")

    return 0


if __name__ == "__main__":
    sys.exit(main())
