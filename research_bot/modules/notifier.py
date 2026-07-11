"""
notifier.py — 미전송(analyzed) 요약을 텔레그램으로 일괄 push (N-2 / B안)
"""
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone, timedelta

import httpx
import yaml

from modules.db import get_conn

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
with open(os.path.join(_ROOT, "config.yaml"), encoding="utf-8") as f:
    _cfg = yaml.safe_load(f)

TOKEN = _cfg["telegram"]["token"]
CHAT_IDS = [str(x) for x in _cfg["telegram"].get("allow_from", [])]


def _send(text: str) -> bool:
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    ok = True
    for cid in CHAT_IDS:
        for i in range(0, len(text), 4000):
            chunk = text[i:i + 4000]
            try:
                resp = httpx.post(url, json={"chat_id": cid, "text": chunk}, timeout=10)
                resp.raise_for_status()
            except Exception as e:
                logger.warning(f"텔레그램 전송 실패: {e}")
                ok = False
    return ok


def opinion_emoji(opinion: str) -> str:  # 수정: 함수 위치를 모듈 레벨로 이동
    if not opinion:
        return "⚪없음"
    op = opinion.strip()
    if any(k in op for k in ["매수", "Buy", "BUY", "강력매수", "적극매수"]):
        return f"🟢{op}"
    if any(k in op for k in ["매도", "Sell", "SELL"]):
        return f"🔴{op}"
    return f"⚪{op}"


CATEGORY_LABEL = {
    "COMPANY": "🏢 기업 리포트",
    "INDUSTRY": "🏭 산업 리포트",
    "ETC": "📄 기타",
}


def notify_new_reports(done_ids: list = None):
    conn = get_conn()

    rows = [dict(r) for r in conn.execute("""  
        SELECT id, category, ticker_name, security_firm, published_date,
               investment_opinion, target_price, summary,keywords,title
        FROM research_report
        WHERE status = 'analyzed'
        ORDER BY category, ticker_name
    """).fetchall()]  # 수정: dict 변환

    if not rows:
        conn.close()
        return

    now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

    grouped = defaultdict(list)
    for r in rows:
        cat = (r.get("category") or "ETC").upper()
        grouped[cat].append(r)

    lines = [f"📊 신규 리서치 {len(rows)}건 ({now_str})"]

    for cat, rlist in grouped.items():
        label = CATEGORY_LABEL.get(cat, f"📄 {cat}")
        lines.append(f"\n{'─'*20}\n{label} ({len(rlist)}건)") 

        for r in rlist:
            
            firm = r.get("security_firm") or "정보없음"
            price = r.get("target_price")
            price_str = f"{int(price):,}원" if price else "-"
            summary = (r.get("summary") or "").strip()[:150]
            pub = r.get("published_date") or "-"
            keywords =r.get("keywords") or "-"
            op_str = opinion_emoji(r.get("investment_opinion"))

            
            if cat == "COMPANY":
                ticker = r.get("ticker_name") or "미상"
                lines.append(f"\n🔹 [{ticker}] {firm}")
                lines.append(f"  {pub} | {op_str} | 🎯{price_str}")
            else:
                title = r.get("title") or ticker
                short_title = title[:30] + "..." if len(title) > 30 else title
                lines.append(f"\n🔶 [{short_title}]  {firm} ({pub})")
               
            if summary:
                lines.append(f"  💬 {summary}") 

    if _send("\n".join(lines)):
        ids = [r["id"] for r in rows]
        placeholders = ",".join("?" * len(ids))
        conn.execute(
            f"UPDATE research_report SET status='notified' "
            f"WHERE id IN ({placeholders}) AND status='analyzed'",
            ids,
        )
        conn.commit()
        sent = len(ids)
    else:
        sent = 0
        logger.warning("일괄 전송 실패 → 전체 analyzed 유지 (다음 배치 재시도)")

    conn.close()
    logger.info(f"📤 텔레그램 알림: {sent}/{len(rows)}건 전송·전이")


if __name__ == "__main__":  # 추가: 직접 실행 지원
    logging.basicConfig(level=logging.INFO)
    notify_new_reports()