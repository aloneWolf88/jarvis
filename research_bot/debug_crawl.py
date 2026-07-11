from modules.db import get_conn
c = get_conn()
print("\n[investment_opinion 분포]")
for r in c.execute("SELECT investment_opinion, COUNT(*) as cnt FROM research_report WHERE category='COMPANY' GROUP BY investment_opinion").fetchall():
    print(dict(r))
c.close()