from pykrx import stock

# 투자자별 거래실적 (기관/외국인/개인)
df = stock.get_market_trading_value_by_date("20260701", "20260714", "KOSPI")
print(df)

# 프로그램 매매 관련도 지원
df2 = stock.get_market_trading_volume_by_investor("20260714", "20260714", "KOSPI")
print(df2)