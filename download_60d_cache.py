#!/usr/bin/env python3
"""Download 60-day 5m historical data for NSE Groww universe and cache locally."""
import os
import sys
import time
import warnings
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

TRADER_DIR = os.path.join(os.path.dirname(__file__), "trader")
UNIVERSE_PATH = os.path.join(TRADER_DIR, "universe.txt")
CACHE_DIR = os.path.join(TRADER_DIR, "data_cache_60d")
os.makedirs(CACHE_DIR, exist_ok=True)

def load_universe() -> list:
    symbols = ["^NSEI"]
    if os.path.exists(UNIVERSE_PATH):
        with open(UNIVERSE_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                sym = line.split("|")[0].strip()
                if sym and sym not in symbols:
                    symbols.append(sym)
    return symbols

def main():
    universe = load_universe()
    print(f"Downloading 60d 5m data for {len(universe)} symbols into {CACHE_DIR}...")
    for sym in universe:
        cache_file = os.path.join(CACHE_DIR, f"{sym}_5m.csv")
        t0 = time.time()
        try:
            df = yf.download(sym, period="60d", interval="5m", progress=False)
            if df is None or df.empty:
                print(f"FAILED: {sym} (empty)")
                continue
            if isinstance(df.columns, pd.MultiIndex):
                df = df.xs(sym, axis=1, level=1)
            if df.index.tz:
                df.index = df.index.tz_convert("Asia/Kolkata")
            else:
                df.index = df.index.tz_localize("UTC").tz_convert("Asia/Kolkata")
            df.to_csv(cache_file)
            print(f"Saved {sym:14} | {len(df):5d} bars | {time.time()-t0:.1f}s")
        except Exception as e:
            print(f"ERROR: {sym} -> {e}")
    print("Download complete.")

if __name__ == "__main__":
    main()
