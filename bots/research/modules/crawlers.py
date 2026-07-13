"""
네이버 금융 리서치 크롤러
"""
import logging
import os
import time

import re  # 추가: URL 정규화용

import requests
import yaml
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(BASE_DIR, "config.yaml"), encoding="utf-8") as f:
    config = yaml.safe_load(f)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36"
}

CATEGORIES = {
    "COMPANY":   {
        "url": "https://finance.naver.com/research/company_list.naver",
             "label": "종목분석"
             },
    "INDUSTRY":  {
        "url": "https://finance.naver.com/research/industry_list.naver",
        "label": "산업분석"
        },
    
}

DELAY = config["crawl"]["delay_seconds"]
MAX_PAGES = config["crawl"]["max_pages"]


def crawl_category(category_key, pages=None):
    cat = CATEGORIES.get(category_key)
    if not cat:
        return []
    if pages is None:
        pages = MAX_PAGES

    reports = []
    for page in range(1, pages + 1):
        url = f"{cat['url']}?&page={page}"
        logger.info(f"[{cat['label']}] 페이지 {page}")

        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            resp.encoding = "euc-kr"
            soup = BeautifulSoup(resp.text, "html.parser")
            rows = soup.select("table.type_1 tr")

            for row in rows:
                cells = row.select("td")
                if len(cells) < 5:
                    continue
                try:
                    if category_key == "COMPANY":
                        r = _parse_company(cells)
                    else:
                       r = _parse_general(cells, category_key) 
                    if r and r.get("title"):
                        r["category"] = category_key
                        reports.append(r)
                except Exception:
                    continue

            time.sleep(DELAY)
        except Exception as e:
            logger.error(f"크롤링 오류: {url} - {e}")

    logger.info(f"[{cat['label']}] {len(reports)}건")
    return reports


def _normalize_url(url):
    """추가: &page=N 제거 — 리포트가 목록에서 밀리면 page가 바뀌어 중복 저장되는 원인"""
    if not url:
        return url
    return re.sub(r"[?&]page=\d+", "", url)


def _parse_company(cells):
    ticker_name = cells[0].text.strip() if len(cells) > 0 else None
    a_tag = cells[1].select_one("a") if len(cells) > 1 else None
    title = a_tag.text.strip() if a_tag else None
    
    # 수정: 상대 URL → 절대 URL 변환
    detail_url = a_tag["href"] if a_tag and a_tag.get("href") else None
    if detail_url and not detail_url.startswith("http"):
        detail_url = "https://finance.naver.com/research/" + detail_url
    
    firm = cells[2].text.strip() if len(cells) > 2 else None
    date = cells[3].text.strip() if len(cells) > 3 else None
    if date:
        date = date.replace(".", "-").strip("-")
    return {"ticker_name": ticker_name, "title": title,
            "security_firm": firm, "published_date": date,
            "pdf_url": _normalize_url(detail_url)}  # 수정: page 파라미터 제거


def _extract_html_content(detail_url):
    """상세 페이지에서 본문 HTML 추출"""
    try:
        # 수정: 이미 절대 URL이므로 변환 불필요
        resp = requests.get(detail_url, headers=HEADERS, timeout=10)
        resp.encoding = "euc-kr"
        soup = BeautifulSoup(resp.text, "html.parser")
        
        # 본문 내용 추출
        content = soup.select_one(".research-detail-content, .report-content, .description")
        if content:
            text = content.get_text(strip=True)
            return text[:3000]  # 3000자 제한
        
        # fallback: 전체 본문에서 추출
        body = soup.select_one("body")
        if body:
            # 불필요한 태그 제거
            for tag in body.select("script, style, nav, footer"):
                tag.decompose()
            text = body.get_text(separator=" ", strip=True)
            return text[:3000]
        
        return None
    except Exception as e:
        logger.debug(f"HTML 추출 실패: {detail_url} - {e}")
        return None


def _parse_general(cells, category_key=None):  
    if category_key == "INDUSTRY":
        # 6칸: [업종, title, firm, 빈칸, date, 조회수]
        a_tag = cells[1].select_one("a") if len(cells) > 1 else None
        firm = cells[2].text.strip() if len(cells) > 2 else None
        date = cells[4].text.strip() if len(cells) > 4 else None
    else:
        # 5칸: MARKET/ECONOMY/DEBENTURE [title, firm, 빈칸, date, 조회수]
        a_tag = cells[0].select_one("a") if len(cells) > 0 else None
        firm = cells[1].text.strip() if len(cells) > 1 else None
        date = cells[3].text.strip() if len(cells) > 3 else None
    
    if date:
        date = date.replace(".", "-").strip("-")
    title = a_tag.text.strip() if a_tag else None
    pdf_url = a_tag["href"] if a_tag and a_tag.get("href") else None
    if pdf_url and not pdf_url.startswith("http"):
        pdf_url = "https://finance.naver.com/research/" + pdf_url
    return {"title": title, "security_firm": firm, "published_date": date,
            "pdf_url": _normalize_url(pdf_url)}  # 수정: page 파라미터 제거




def crawl_all():
    all_reports = []
    for key in CATEGORIES:
        all_reports.extend(crawl_category(key))
    return all_reports


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    reports = crawl_category("COMPANY", pages=1)
    for r in reports[:5]:
        print(r)