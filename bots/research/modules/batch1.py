"""
배치1: 목록 크롤링 → DB 저장 (status=collected)
COMPANY + INDUSTRY만 처리
"""
import logging
import time

from modules.crawlers import crawl_category
from modules.db import is_duplicate, save_report_batch1

logger = logging.getLogger(__name__)

TARGET_CATEGORIES = ["COMPANY", "INDUSTRY"]  # 추가: 대상 카테고리

def batch1_job():
    logger.info("=" * 50)
    logger.info("🚀 배치1 시작 (크롤링)")

    saved = skipped = 0

    for category in TARGET_CATEGORIES:  # 추가: 카테고리 순회
        reports = crawl_category(category)
        logger.info(f"[{category}] 크롤링 {len(reports)}건")

        for r in reports:
            title = r.get("title", "")
            firm = r.get("security_firm", "")
            date = r.get("published_date", "")
            pdf_url = r.get("pdf_url", "")

            if not title:
                skipped += 1
                continue

            if is_duplicate(title, firm, date, pdf_url):  # 수정: pdf_url 기준 중복검사
                logger.debug(f"  ⏭️  중복: {title}")
                skipped += 1
                continue

            save_report_batch1({
                "category": category,
                "ticker_name": r.get("ticker_name"),
                "title": title,
                "security_firm": firm,
                "published_date": date,
                "pdf_url": pdf_url,
            })
            saved += 1
            time.sleep(0.1)  # 추가: 목록만이라 짧게

    logger.info(f"🏁 배치1 완료: 저장 {saved} / 스킵 {skipped}")
    return {"saved": saved, "skipped": skipped}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    batch1_job()