from pykrx import stock

ticker = "005930"
df = stock.get_market_trading_value_by_date("20260520", "20260605", ticker)
print(df)
print("컬럼:", list(df.columns))