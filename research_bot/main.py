"""
Research Bot 메인
- OpenClaw에서 shell로 호출
- Windows Task Scheduler에서 배치 실행

사용법:
  python main.py --init                          # DB 초기화
  python main.py --batch                         # 크롤링+요약 1회
  python main.py --today                         # 오늘 요약 출력
  python main.py --ticker 삼성전자                # 종목 조회
  python main.py --query "삼성전자 상향 몇건?"    # 자연어 질문
  python main.py --new                           # 신규 커버리지
  python main.py --scheduler                     # 스케줄러 상주
"""
import argparse
import io
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime

import schedule
import yaml

# 삭제: from modules.batch import batch_job
from modules.orchestrator import batch_job
from modules.batch1 import batch1_job
from modules.batch2 import batch2_job
from modules.crawlers import CATEGORIES
from modules.db import (
    aggregate_by_ticker,
    get_new_coverage,
    get_today_reports,
    get_today_summary_by_category,
    get_reports_by_date,
    get_summary_by_category_for_date,
    init_db,
    search_reports,
)
from modules.query_parser import generate_answer, parse_intent
from modules.text2sql import ask as text2sql_ask
from datetime import datetime, timezone, timedelta   # 추가: 파일 상단에 없으면 추가

# 추가: Windows cp949 환경에서 UTF-8 출력 강제 (이모지/한글 깨짐 방지)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

KST = timezone(timedelta(hours=9))                    # 추가: 상단 상수 (db/notifier와 동일)

# ── 로깅 ──
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)
log_file = os.path.join(LOG_DIR, f"bot_{datetime.now():%Y%m%d}.log")


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

with open(os.path.join(BASE_DIR, "config.yaml"), encoding="utf-8") as f:
    config = yaml.safe_load(f)

LABEL = {k: v["label"] for k, v in CATEGORIES.items()}


# ── 명령어 함수 ──
def cmd_usage():
    """research-bot 사용법"""
    print("""📖 research-bot 사용법

    [조회 명령]
    --summaries      리포트 요약 목록 (증권사/목표가/투자의견/분석)
    --stats          전체 통계 (카테고리별/종목별/투자의견별)
    --today          오늘 수집된 리포트
    --yesterday      어제 수집된 리포트
    --list-tickers   수집된 종목 목록
    --list-reports   최근 리포트 목록
    --new            신규 커버리지 종목
    --ticker 종목명   특정 종목 리포트 (예: --ticker 삼성전자)
    --query "질문"    자연어 질문 (예: --query "삼성전자 상향 몇건?")


    [관리 명령]
    --batch          수동 크롤링 실행
    --init           DB 초기화

    [텔레그램 사용 예시]
    "오늘 리포트"           → 오늘 수집된 전체 리포트
    "어제 리포트"           → 어제 수집된 전체 리포트
    "최근 산업분석"         → 최근 산업 리포트 목록
    "오늘 산업분석"         → 오늘 수집된 산업 리포트
    "[키워드] 산업분석"     → 키워드 관련 산업 리포트 (예: 반도체 산업분석)
    "[종목명] 리포트"       → 종목 리포트 조회 (예: 삼성전자 리포트)
    """)

def cmd_reports_for_date(target_date, label):
    date_str = target_date.strftime("%Y-%m-%d")
    summary_map = get_summary_by_category_for_date(date_str)
    if not summary_map:
        return f"{label}({date_str}) 신규 리포트가 없습니다."
    
    total = sum(summary_map.values())
    output = f"📋 {label} 리서치 요약 ({date_str}, 총 {total}건)\n\n"

    for cat, cnt in summary_map.items():
        output += f"  • {LABEL.get(cat, cat)}: {cnt}건\n"

    reports = get_reports_by_date(date_str)
    buttons_data = []
    identifiers_seen = set()

    if reports:
        output += f"\n📋 {label} 수집 리포트:\n"

        # 삭제: from itertools import groupby
        # 삭제: grouped = groupby(reports, key=lambda r: r.get("category"))
        # 추가: defaultdict 방식
        from collections import defaultdict
        grouped = defaultdict(list)
        for r in reports:
            grouped[r.get("category") or "ETC"].append(r)

        for cat, group in grouped.items():  # 수정: group이 이미 list
            label = LABEL.get(cat, cat)
            output += f"\n[{label}]\n"
            for i, r in enumerate(group, 1):
                if cat == "COMPANY":
                    identifier = r.get("ticker_name") or ""
                else:
                    keywords = r.get("keywords") or ""
                    identifier = keywords.split(",")[0].strip() if keywords else ""

                opinion = r.get("investment_opinion") or ""
                title = (r.get("title") or "")[:35]
                op_emoji = _opinion_emoji(opinion)
                line = f"  {i}. [ {identifier} ] {title}"
                if opinion:
                    line += f" {op_emoji}{opinion}"
                output += line + "\n"

                if identifier and identifier not in identifiers_seen:
                    buttons_data.append({
                        "label": f"[{identifier}]",
                        "command": f"/research {identifier} 리포트"
                    })
                    identifiers_seen.add(identifier)

    return {
        "text": output,
        "buttons": buttons_data
    }  


def cmd_today():
    return cmd_reports_for_date(datetime.now(KST).date(), "오늘")


def cmd_yesterday():
    yesterday = datetime.now(KST).date() - timedelta(days=1)
    return cmd_reports_for_date(yesterday, "어제")


def cmd_ticker(name):
    """특정 종목 조회"""
    agg = aggregate_by_ticker(name, days=30)
    reports = search_reports(
        ticker_name=name, 
        category="COMPANY", 
        days=30, limit=10
    )  # 수정: 5 → 10

    print(f"📊 {name} 최근 30일\n")
    print(
        f"총 {agg['total']}건 | "
        f"🟢 매수 {agg['buy']}건 | "
        f"🟡 중립 {agg['hold']}건 | "
        f"🔴 매도 {agg['sell']}건"
    )
    if agg.get("no_opinion"):
        print(f"  (의견없음 {agg['no_opinion']}건)")
    if agg["new_coverage"]:
        print(f"🆕 신규 커버리지 {agg['new_coverage']}건")

    # 수정: 번호 형식(A안)으로 출력
    if reports:
        print()
        for i, r in enumerate(reports, 1):
            firm = r.get("security_firm") or "정보없음"
            date = r.get("published_date") or ""
            opinion = r.get("investment_opinion") or "정보없음"
            price = r.get("target_price")
            price_str = f"{price:,}원" if price else "정보없음"
            summary = (r.get("summary") or "").strip() or "정보없음"
            op_emoji = _opinion_emoji(opinion) 
            print(f"{i}. {firm} 리포트 ({date}):")
            print(f"  🎯 목표가: {price_str}")
            print(f"  💡 투자의견: {op_emoji} {opinion}")
            print(f"  📝 분석: {summary}")
            print()


def cmd_query(question):
    """자연어 질문 → 의도 파싱 → DB 조회 → 자연어 응답"""
    intent = parse_intent(question)
    db_result = {}

    if intent.get("tickerName"):
        db_result = aggregate_by_ticker(
            intent["tickerName"], days=intent.get("periodDays", 30)
        )
    elif intent.get("metric") == "NEW":
        new_list = get_new_coverage(days=intent.get("periodDays", 30))
        db_result = {"new_coverage_list": [dict(r) for r in new_list]}
    else:
        reports = search_reports(
            category=intent.get("category"),
            days=intent.get("periodDays", 30),
        )
        db_result = {
            "reports_count": len(reports),
            "reports": [{"title": r["title"], "summary": (r.get("summary") or "")[:50]}  # 추가: None 방어
                        for r in reports[:5]],
        }

    answer = generate_answer(question, db_result, intent)
    print(answer)


def cmd_new():
    """신규 커버리지 종목"""
    new_list = get_new_coverage(days=30)
    if not new_list:
        print("최근 30일 내 신규 커버리지 종목이 없습니다.")
        return

    print(" 최근 30일 신규 커버리지\n")
    for item in new_list:
        print(f"  • {item['ticker_name']} (첫 리포트: {item['first_date']}, {item['cnt']}건)")


def cmd_list_tickers():
    """수집된 전체 종목 목록"""
    conn = sqlite3.connect(os.path.join(BASE_DIR, config["database"]["path"]))
    c = conn.cursor()
    c.execute("""
        SELECT DISTINCT ticker_name, COUNT(*) as cnt 
        FROM research_report 
        WHERE ticker_name IS NOT NULL
        GROUP BY ticker_name 
        ORDER BY cnt DESC
        LIMIT 20 
    """)
    rows = c.fetchall()
    conn.close()

    if not rows:
        print("수집된 종목이 없습니다.")
        return

    print(f" 수집된 종목 (총 {len(rows)}개)\n")
    for ticker, cnt in rows:
        print(f"  • {ticker}: {cnt}건")

def cmd_lookup(query):
    """종목/키워드 통합 조회 (ticker 또는 keywords LIKE 검색)"""
    # ticker 먼저 시도
    agg = aggregate_by_ticker(query, days=30)
    if agg['total'] > 0:
        # ticker 존재 → cmd_ticker와 동일 처리
        cmd_ticker(query)
        return
    
    # ticker 없으면 keywords 검색
    reports = search_reports(
        category="COMPANY",
        days=30,
        limit=10
    )
    
    # 추가: keywords에서 query 필터링
    filtered = [r for r in reports 
                if query in (r.get("keywords") or "")]
    
    if not filtered:
        print(f"'{query}'에 대한 결과가 없습니다.")
        return
    
    print(f"📊 '{query}' 검색 결과 ({len(filtered)}건)\n")
    for i, r in enumerate(filtered, 1):
        firm = r.get("security_firm") or "정보없음"
        date = r.get("published_date") or ""
        opinion = r.get("investment_opinion") or "정보없음"
        summary = (r.get("summary") or "").strip() or "정보없음"
        op_emoji = _opinion_emoji(opinion)
        
        print(f"{i}. {firm} 리포트 ({date}):")
        print(f"  🎯 투자의견: {op_emoji} {opinion}")
        print(f"  📝 분석: {summary}")
        print()

def cmd_list_reports(limit=50):
    """전체 리포트 목록"""
    conn = sqlite3.connect(os.path.join(BASE_DIR, config["database"]["path"]))
    c = conn.cursor()
    # 수정: 조회 컬럼 변경 (title, keywords 추가 / opinion, price 제거)
    c.execute("""
        SELECT title, security_firm, published_date, summary, keywords
        FROM research_report
        WHERE summary IS NOT NULL AND summary != ''
        ORDER BY published_date DESC
        LIMIT ?
    """, (limit,))
    rows = c.fetchall()
    conn.close()

    if not rows:
        print("요약된 리포트가 없습니다.")
        return

    print(f"📊 최근 리포트 요약 ({len(rows)}건)\n")
    # 수정: 언패킹 변수 변경 (title, firm, date, summary, keywords)
    for i, (title, firm, date, summary, keywords) in enumerate(rows, 1):
        title_str = title or "제목없음"
        firm_str = firm or "정보없음"
        summary_str = (summary or "").strip() or "정보없음"
        keywords_str = (keywords or "").strip() or "-"

        print(f"▶{i}. {title_str} ===================")                    # 수정: 제목
        print(f"  🏢 {firm_str} ({date})")            # 수정: 증권사 + 날짜
        print(f"  📝 분석: {summary_str}")
        print(f"  🏷️ 키워드: {keywords_str}")          # 추가: 키워드
        print()

def cmd_summaries(name=None, limit=10):
    """리포트 요약 목록 (LLM 없이 즉시 출력)"""
    #print(f"[DEBUG] name={name!r}")
    conn = sqlite3.connect(os.path.join(BASE_DIR, config["database"]["path"]))
    c = conn.cursor()
    
    # 조건절 생성
    conditions = ["summary IS NOT NULL AND summary != ''"]
    params = []

    if name:
        conditions.append("""(
            keywords LIKE ? OR title LIKE ? OR summary LIKE ?
            OR ticker_name LIKE ?
        )""")
        search_term = f"%{name}%"
        params.extend([search_term] * 4)

    # 쿼리 구성
    query = """
        SELECT title, security_firm, published_date, summary, keywords
        FROM research_report
        WHERE {}
        ORDER BY published_date DESC
        LIMIT ?
    """.format(" AND ".join(conditions))

    # 실행
    c.execute(query, params + [limit])
    rows = c.fetchall()
    conn.close()

    if not rows:
        print("요약된 리포트가 없습니다.")
        return

    print(f"📊 최근 리포트 요약 ({len(rows)}건)\n")
    # 수정: 언패킹 변수 변경 (title, firm, date, summary, keywords)
    for i, (title, firm, date, summary, keywords) in enumerate(rows, 1):
        title_str = title or "제목없음"
        firm_str = firm or "정보없음"
        summary_str = (summary or "").strip() or "정보없음"
        keywords_str = (keywords or "").strip() or "-"

        print(f"▶{i}. {title_str}==================================")                    # 수정: 제목
        print(f"  🏢 {firm_str} ({date})")            # 수정: 증권사 + 날짜
        print(f"  📝 분석: {summary_str}")
        print(f"  🏷️ 키워드: {keywords_str}")          # 추가: 키워드
        print()
        
def cmd_stats():
    """통계"""
    conn = sqlite3.connect(os.path.join(BASE_DIR, config["database"]["path"]))
    c = conn.cursor()

    # 총 통계
    c.execute("SELECT COUNT(*) FROM research_report")
    total = c.fetchone()[0]

    # 카테고리별
    c.execute("SELECT category, COUNT(*) FROM research_report GROUP BY category")
    cat_stats = dict(c.fetchall())

    # 종목별
    c.execute("""
        SELECT COUNT(DISTINCT ticker_name) 
        FROM research_report 
        WHERE ticker_name IS NOT NULL
    """)
    ticker_count = c.fetchone()[0]

    # 투자의견별
    c.execute("""
        SELECT investment_opinion, COUNT(*) 
        FROM research_report 
        WHERE investment_opinion IS NOT NULL
        GROUP BY investment_opinion
    """)
    opinion_stats = dict(c.fetchall())

    conn.close()

    print(" 통계\n")
    print(f"  • 총 리포트: {total}건")
    print(f"  • 수집 종목: {ticker_count}개\n")

    print("카테고리별:")
    for cat, cnt in cat_stats.items():
        label = LABEL.get(cat, cat)
        print(f"  • {label}: {cnt}건")

    print("\n투자의견별:")
    for opinion, cnt in opinion_stats.items():
        print(f"  • {opinion}: {cnt}건")


def cmd_scheduler():
    """배치 스케줄러 상주 모드"""
    for t in config["schedule"]["batch_times"]:
        schedule.every().day.at(t).do(batch_job)
        logger.info(f"⏰ 스케줄 등록: 매일 {t}")

    logger.info("스케줄러 시작. Ctrl+C로 종료.")
    while True:
        schedule.run_pending()
        time.sleep(60)

def cmd_ask(question):
    """자연어 → SQL 생성 → 실행 → 응답"""
    answer = text2sql_ask(question)
    print(answer)

def _opinion_emoji(opinion):
    if not opinion:
        return "⚪"
    op = opinion.upper()
    if "BUY" in op or "매수" in opinion or "OUTPERFORM" in op:
        return "🟢"
    if "SELL" in op or "매도" in opinion or "UNDERPERFORM" in op:
        return "🔴"
    if "HOLD" in op or "중립" in opinion or "보유" in opinion or "NEUTRAL" in op:
        return "🟡"
    return "⚪"

# ── 메인 ──

def main():
    parser = argparse.ArgumentParser(description="네이버 리서치 봇 (OpenClaw 연동)")
    parser.add_argument("--init",        action="store_true", help="DB 초기화")
    parser.add_argument("--batch",       action="store_true", help="크롤링+요약 1회")
    parser.add_argument("--today",       action="store_true", help="오늘 요약")
    parser.add_argument("--yesterday",   action="store_true", help="어제 요약")
    parser.add_argument("--ticker",      type=str,            help="종목명 조회")
    parser.add_argument("--query",       type=str,            help="자연어 질문")
    parser.add_argument("--new",         action="store_true", help="신규 커버리지")
    parser.add_argument("--ask", type=str, help="자유 질문 (Text-to-SQL)")  
    # 추가: 3개 명령어
    parser.add_argument("--list-tickers",action="store_true", help="수집된 종목 목록")
    parser.add_argument("--list-reports",action="store_true", help="최근 산업분석")
    parser.add_argument("--stats",       action="store_true", help="통계")
    parser.add_argument("--summaries", nargs="?", const="", default=None,
                        help="(키워드) 산업분석") 
    parser.add_argument("--lookup",      type=str,            help="종목/키워드 조회")                        
    parser.add_argument("--scheduler",   action="store_true", help="스케줄러 상주")
    parser.add_argument("--usage",     action="store_true", help="사용법") 
    parser.add_argument("--batch1", action="store_true", help="크롤링만 (배치1)")
    parser.add_argument("--batch2", action="store_true", help="파싱+요약 (배치2)")
    args = parser.parse_args()

    init_db()

    if args.init:
        return
    elif args.batch:
        batch_job()
    elif args.today:
        result = cmd_today()
        if isinstance(result, dict):
            print(result["text"])
        elif result:
            print(result)
    elif args.yesterday:
        result = cmd_yesterday()
        if isinstance(result, dict):
            print(result["text"])
        elif result:
            print(result)
    elif args.ticker:
        cmd_ticker(args.ticker)
    elif args.lookup:
        cmd_lookup(args.lookup)    
    elif args.query:
        cmd_query(args.query)
    elif args.new:
        cmd_new()
    # 추가: 3개 명령어 처리
    elif args.list_tickers:
        cmd_list_tickers()
    elif args.list_reports:
        cmd_list_reports()
    elif args.stats:
        cmd_stats()
    elif args.summaries is not None:      # 추가: nargs="?" → None 비교
        cmd_summaries(name=args.summaries or None)
    elif args.scheduler:
        cmd_scheduler()
    elif args.usage:       
        cmd_usage()   
    elif args.ask:        ##자연어
        cmd_ask(args.ask)  
    elif args.batch1:
        batch1_job() 
    elif args.batch2:
        batch2_job()         
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
