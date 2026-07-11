"""
배치2: status=collected 건 → 상세 페이지 파싱 → LLM 요약 → DB 업데이트

[수정 이력]
- ticker_code, prev_target_price 보강 + done_ids 반환(N-2 알림용) + 예외처리
- ticker_name 은 batch1 크롤링값 보존 (qwen "종목명" placeholder 방지) → UPDATE 제외
- _to_int 상한 검증 (목표주가 1조 초과 = 영업이익 등 오추출로 간주 → None)
- published_date 폴백 (빈 값이면 당일 날짜로 채움)
"""
import logging
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from modules.db import get_conn, _normalize_date  # 수정: _normalize_date 추가
from modules.summarizer import summarize_report

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36"
}


def _fetch_detail(pdf_url: str) -> str | None:
    """상세 페이지 HTML 반환"""
    try:
        resp = requests.get(pdf_url, headers=HEADERS, timeout=10)
        resp.encoding = "euc-kr"
        return resp.text
    except Exception as e:
        logger.debug(f"HTML 요청 실패: {pdf_url} - {e}")
        return None


def _parse_detail(html: str, category: str) -> dict:
    """상세 페이지 HTML 파싱"""
    soup = BeautifulSoup(html, "html.parser")
    result = {}

    # 공통: 날짜/증권사 (배치1에서 이미 저장되지만 검증용)
    source = soup.select_one("th.view_sbj p.source")
    if source:
        parts = source.get_text("|", strip=True).split("|")
        result["published_date"] = parts[1].strip() if len(parts) > 1 else None

    # 공통: 본문
    body = soup.select_one("td.view_cnt")
    result["body_text"] = body.get_text(strip=True)[:3000] if body else None

    if category == "COMPANY":
        # 목표가
        price = soup.select_one("div.view_info_1 em.money strong")
        if price:
            result["target_price"] = price.text.strip().replace(",", "")

        # 투자의견
        opinion = soup.select_one("div.view_info_1 em.coment")
        if opinion:
            result["investment_opinion"] = opinion.text.strip()

    return result


# INTEGER 컬럼용 숫자 변환 (LLM이 "100,000원" 등 문자열 반환 + 오추출 대비)
def _to_int(value):
    if value is None:
        return None
    if isinstance(value, int):
        result = value
    else:
        try:
            cleaned = str(value).replace(",", "").replace("원", "").strip()
            if not cleaned or cleaned.lower() == "null":
                return None
            result = int(float(cleaned))
        except (ValueError, TypeError):
            return None
    # 추가: 목표주가 상한 (1조 초과 = 영업이익 등 오추출로 간주)
    if result > 1_000_000_000_000:
        return None
    return result


def _update_report(report_id: int, data: dict):
    """DB 업데이트 (ticker_name 은 batch1 값 보존을 위해 갱신 대상에서 제외)"""
    try:      
        conn = get_conn()
        conn.execute("""
            UPDATE research_report SET
                ticker_code = COALESCE(:ticker_code, ticker_code),
                published_date = COALESCE(NULLIF(published_date, ''), :published_date),
                target_price = COALESCE(:target_price, target_price),
                prev_target_price = COALESCE(:prev_target_price, prev_target_price),
                investment_opinion = COALESCE(:investment_opinion, investment_opinion),
                summary = :summary,
                keywords = :keywords,
                status = 'analyzed'
            WHERE id = :id
        """, {
            "id": report_id,
            "ticker_code": data.get("ticker_code"),
            "published_date": data.get("published_date"),
            "target_price": data.get("target_price"),
            "prev_target_price": data.get("prev_target_price"),
            "investment_opinion": data.get("investment_opinion"),
            "summary": data.get("summary"),
            "keywords": data.get("keywords"),
        })
        conn.commit()
        conn.close()
    except Exception as e:            # 추가: except
        logger.warning(f"DB 업데이트 실패 (ID {report_id}): {e}")

def batch2_job(limit: int = 100):
    """배치2 메인"""
    logger.info("🚀 배치2 시작 (파싱+요약)")

    conn = get_conn()
    rows = conn.execute("""
        SELECT id, category, title, pdf_url
        FROM research_report
        WHERE status = 'collected'
        AND pdf_url IS NOT NULL
        ORDER BY id ASC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()

    total = len(rows)
    logger.info(f"대상: {total}건")

    done = failed = skipped = 0
    done_ids = []   # N-2 알림용 — 이번에 요약 완료된 report_id

    for row in rows:
        report_id = row["id"]
        category = row["category"]
        title = row["title"]
        pdf_url = row["pdf_url"]

        logger.info(f"📄 [{category}] {title[:40]}")

        # HTML 요청
        html = _fetch_detail(pdf_url)
        if not html:
            logger.info("  ⏭️  HTML 요청 실패")
            skipped += 1
            continue

        # 파싱
        parsed = _parse_detail(html, category)
        if not parsed.get("body_text"):
            logger.info("  ⏭️  본문 추출 실패")
            skipped += 1
            continue

        # LLM 요약 (_parse_json 이 raise 할 수 있어 예외 처리)
        try:
            summary_data = summarize_report(parsed["body_text"], category)
        except Exception as e:
            logger.info(f"  ⏭️  LLM 요약/파싱 실패: {e}")
            failed += 1
            continue

        if not summary_data:
            logger.info("  ⏭️  LLM 요약 결과 없음")
            failed += 1
            continue

        # 추가: published_date 폴백 — 상세 파싱값 → 없으면 당일 날짜
        # pub_date = parsed.get("published_date") or datetime.now().strftime("%Y.%m.%d")  # 삭제: 점 포맷 혼재 원인
        pub_date = _normalize_date(parsed.get("published_date")) or datetime.now().strftime("%Y-%m-%d")  # 추가: 하이픈 포맷 통일

         # ① 먼저 정의 (반드시 _update_report 호출 위에)
        summary_val = summary_data.get("summary", "")
        if isinstance(summary_val, list):
            summary_val = "\n".join(str(s) for s in summary_val)

        keywords_val = summary_data.get("keywords", "")
        if isinstance(keywords_val, list):
            keywords_val = ", ".join(str(k) for k in keywords_val)


        # DB 업데이트 (ticker_name 제외 — batch1 크롤링값 보존)
        _update_report(report_id, {
            "published_date": pub_date,
            "ticker_code": summary_data.get("tickerCode"),
            "target_price": _to_int(parsed.get("target_price") or summary_data.get("targetPrice")),
            "prev_target_price": _to_int(summary_data.get("prevTargetPrice")),
            "investment_opinion": parsed.get("investment_opinion") or summary_data.get("investmentOpinion"),           
            "summary": summary_val,                            # 추가: 정규화값
            "keywords": keywords_val,                          # 추가: 콤마 결합
        })
        done += 1
        done_ids.append(report_id)
        logger.info("  ✅ 완료")
        time.sleep(1)

    logger.info(f"🏁 배치2 완료: 성공 {done} / 스킵 {skipped} / 실패 {failed}")
    return {"done": done, "skipped": skipped, "failed": failed,
            "done_ids": done_ids}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    batch2_job()