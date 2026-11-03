import warnings, yfinance as yf, pandas as pd
warnings.filterwarnings('ignore')
df = yf.download('^NSEI', period='5d', interval='5m', progress=False)
if isinstance(df.columns, pd.MultiIndex): df = df.xs('^NSEI', axis=1, level=1)
df.index = df.index.tz_convert('Asia/Kolkata') if df.index.tz else df.index.tz_localize('UTC').tz_convert('Asia/Kolkata')
df['date'] = df.index.date
latest_date = df['date'].iloc[-1]
sess = df[df['date'] == latest_date]
or_bars = sess.between_time('09:15', '09:40')
or_high = round(float(or_bars['High'].max()), 2)
or_low = round(float(or_bars['Low'].min()), 2)
or_mid = round((or_high + or_low) / 2.0, 2)
print(f"NIFTY 50 30-Min Opening Range ({latest_date} 09:15-09:45 IST)")
print(f"OR High: {or_high} | OR Low: {or_low}")
print(f"OR Midpoint: {or_mid}")
