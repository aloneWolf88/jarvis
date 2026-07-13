"""
SQLite DB 초기화 및 CRUD
"""
import os
import sqlite3
from datetime import datetime

import yaml
import re  # 추가: 날짜 정규화용

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(BASE_DIR, "config.yaml"), encoding="utf-8") as f:
    config = yaml.safe_load(f)

DB_PATH = os.path.join(BASE_DIR, config["database"]["path"])


def _normalize_date(s: str) -> str:
    """published_date를 YYYY-MM-DD로 표준화. 실패 시 원본 반환."""
    if not s:
        return s
    s = s.strip().replace(".", "-").replace("/", "-")   # 구분자 통일
    m = re.match(r"^(\d{2}|\d{4})-(\d{1,2})-(\d{1,2})$", s)
    if not m:
        return s                                         # 미인식 포맷은 원본 유지(로깅 권장)
    y, mo, d = m.groups()
    if len(y) == 2:
        y = "20" + y                                     # 2자리 → 20YY 가정
    return f"{y}-{int(mo):02d}-{int(d):02d}"             # 월·일 zero-pad

def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30)   # 락 대기 30초 (동시 쓰기 대비)
    conn.execute("PRAGMA journal_mode=WAL")       # 읽기-쓰기 동시성(WAL)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS research_report (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT,
            ticker_name TEXT,
            ticker_code TEXT,
            title TEXT,
            security_firm TEXT,
            published_date TEXT,
            pdf_url TEXT,
            investment_opinion TEXT,
            target_price INTEGER,
            prev_target_price INTEGER,
            summary TEXT,
            keywords TEXT,
            status TEXT DEFAULT 'collected',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # 기존 DB에 status 컬럼 없으면 추가
    try:
        conn.execute("ALTER TABLE research_report ADD COLUMN status TEXT DEFAULT 'collected'")
    except Exception:
        pass
    # is_duplicate 기준과 일치하는 UNIQUE 인덱스 → OR IGNORE 작동
    # 주의: 기존 데이터에 중복이 있으면 생성 실패(except로 통과). 사전 중복 정리 필요
    try:
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_report_unique
            ON research_report(title, security_firm, published_date)
        """)
    except Exception:
        pass
    # 추가: pdf_url 고유 인덱스 (리포트별 고유 URL → 가장 확실한 중복 방지)
    try:
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_report_url
            ON research_report(pdf_url)
        """)
    except Exception:
        pass
    conn.commit()
    conn.close()


def is_duplicate(title, security_firm, published_date, pdf_url=None):
    published_date = _normalize_date(published_date)  # 추가: 저장값과 포맷 통일
    conn = get_conn()
    c = conn.cursor()
    if pdf_url:  # 추가: URL이 리포트별 고유 → 가장 확실한 키
        c.execute("SELECT COUNT(*) FROM research_report WHERE pdf_url = ?", (pdf_url,))
    else:
        c.execute("""
            SELECT COUNT(*) FROM research_report
            WHERE title = ? AND security_firm = ? AND published_date = ?
        """, (title, security_firm, published_date))
    result = c.fetchone()[0] > 0
    conn.close()
    return result


def save_report(data: dict):
    conn = get_conn()
    try:
        conn.execute("""
            INSERT OR IGNORE INTO research_report
            (category, ticker_name, ticker_code, title, security_firm,
             published_date, pdf_url, investment_opinion, target_price,
             prev_target_price, summary, keywords)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            data.get("category"), data.get("ticker_name"), data.get("ticker_code"),
            data.get("title"), data.get("security_firm"), data.get("published_date"),
            data.get("pdf_url"), data.get("investment_opinion"),
            data.get("target_price"), data.get("prev_target_price"),
            data.get("summary"), data.get("keywords"),
        ))
        conn.commit()
    finally:
        conn.close()


def get_today_reports():
    return get_reports_by_date(datetime.now().strftime("%Y-%m-%d"))


def get_reports_by_date(date_str):
    conn = get_conn()
    c = conn.cursor()
    # 변경: published_date → created_at 기준(수집일). 산업분석 Weekly 등 과거 발행분도 노출
    # 유지: collected 제외(미요약 제거)
    c.execute("""
        SELECT * FROM research_report
        WHERE DATE(created_at) = DATE(?)
          AND status != 'collected'
        ORDER BY category, created_at DESC
    """, (date_str,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_today_summary_by_category():
    return get_summary_by_category_for_date(datetime.now().strftime("%Y-%m-%d"))


def get_summary_by_category_for_date(date_str):
    conn = get_conn()
    c = conn.cursor()
    # 변경: published_date → created_at 기준(수집일) 통일, collected 제외
    c.execute("""
        SELECT category, COUNT(*) as cnt FROM research_report
        WHERE DATE(created_at) = DATE(?)
          AND status != 'collected'
        GROUP BY category
    """, (date_str,))
    result = {row["category"]: row["cnt"] for row in c.fetchall()}
    conn.close()
    return result


def aggregate_by_ticker(ticker_name, days=30):
    conn = get_conn()
    c = conn.cursor()
    # 목표가 변동(up/down/same) → 투자의견(buy/hold/sell) 기준 집계
    c.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN UPPER(investment_opinion) IN ('BUY', 'OUTPERFORM', 'STRONGBUY')
                      OR investment_opinion LIKE '%매수%' THEN 1 ELSE 0 END) as buy,
            SUM(CASE WHEN UPPER(investment_opinion) IN ('HOLD', 'NEUTRAL', 'MARKETPERFORM')
                      OR investment_opinion LIKE '%중립%'
                      OR investment_opinion LIKE '%보유%' THEN 1 ELSE 0 END) as hold,
            SUM(CASE WHEN UPPER(investment_opinion) IN ('SELL', 'UNDERPERFORM')
                      OR investment_opinion LIKE '%매도%' THEN 1 ELSE 0 END) as sell,
            SUM(CASE WHEN investment_opinion IS NULL
                      OR investment_opinion = ''
                      OR UPPER(investment_opinion) = 'NONE'
                      OR investment_opinion = '없음' THEN 1 ELSE 0 END) as no_opinion,
            SUM(CASE WHEN prev_target_price IS NULL THEN 1 ELSE 0 END) as new_coverage
        FROM research_report
        WHERE ticker_name LIKE ?
          AND category = 'COMPANY'
          AND DATE(published_date) >= DATE('now','localtime', ?)
    """, (f"%{ticker_name}%", f"-{days} days"))
    row = dict(c.fetchone())
    conn.close()
    return row


def search_reports(ticker_name=None, category=None, days=30, limit=10):
    conn = get_conn()
    query = "SELECT * FROM research_report WHERE 1=1"
    params = []
    if ticker_name:
        query += " AND UPPER(ticker_name) LIKE UPPER(?)"
        params.append(f"%{ticker_name}%")
    if category:
        query += " AND category = ?"
        params.append(category)
    query += " AND DATE(created_at) >= DATE('now','localtime', ?)"
    params.append(f"-{days} days")
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    c = conn.cursor()
    c.execute(query, params)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_new_coverage(days=30):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT ticker_name, MIN(published_date) as first_date, COUNT(*) as cnt
        FROM research_report
        WHERE category = 'COMPANY'
        GROUP BY ticker_name
        HAVING MIN(DATE(published_date)) >= DATE('now','localtime', ?)
    """, (f"-{days} days",))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def save_report_batch1(data: dict):
    conn = get_conn()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data = {**data, "published_date": _normalize_date(data.get("published_date"))}  # 추가: 저장 전 정규화
    conn.execute("""
        INSERT OR IGNORE INTO research_report
            (category, ticker_name, title, security_firm, published_date, pdf_url, status, created_at)
        VALUES
            (:category, :ticker_name, :title, :security_firm, :published_date, :pdf_url, 'collected', :created_at)
    """, {**data, "created_at": now})
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
