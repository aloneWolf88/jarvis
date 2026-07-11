"""
Text-to-SQL 모듈
자연어 질문 → SQL 생성 → 실행 → 자연어 응답
SELECT 전용, research_report 테이블만 허용
"""
import logging
import os
import re
import sqlite3

from modules.summarizer import llm_answer

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 테이블 스키마 (LLM에게 알려줄 정보)
SCHEMA = """
테이블: research_report
컬럼:
  - category TEXT: 카테고리 (
        COMPANY=종목분석, 
        INDUSTRY=산업분석, 
        MARKET=시황정보, 
        ECONOMY=경제분석, 
        DEBENTURE=채권분석
    )
  - ticker_name TEXT: 종목명 (예: 삼성전자)
  - ticker_code TEXT: 종목코드
  - title TEXT: 리포트 제목
  - security_firm TEXT: 증권사
  - published_date TEXT: 발행일 (형식: 26.06.11)
  - investment_opinion TEXT: 투자의견 (BUY/HOLD/SELL)
  - target_price INTEGER: 목표주가
  - prev_target_price INTEGER: 직전 목표주가
  - summary TEXT: 요약
  - keywords TEXT: 키워드
  - created_at TIMESTAMP: 수집일시
"""

CATEGORY_MAP = {
    "종목분석": "COMPANY",
    "산업분석": "INDUSTRY",
    "시황정보": "MARKET",
    "경제분석": "ECONOMY",
    "채권분석": "DEBENTURE",
}

def _normalize_question(question: str) -> str:
    for kor, eng in CATEGORY_MAP.items():
        question = question.replace(kor, eng)
    return question


def _is_safe_sql(sql: str) -> bool:
    """SELECT만 허용, 위험 키워드 차단"""
    sql_lower = sql.lower().strip()
    # SELECT로 시작해야 함
    if not sql_lower.startswith("select"):
        return False
    # 위험 키워드 차단
    forbidden = ["insert", "update", "delete", "drop", "alter",
                 "create", "replace", "truncate", "attach", ";--", "pragma"]
    for word in forbidden:
        if re.search(r'\b' + word + r'\b', sql_lower):
            return False
    # research_report 테이블만 허용
    if "research_report" not in sql_lower:
        return False
    return True


def generate_sql(question: str) -> str:
    question = _normalize_question(question)  
    prompt = f"""너는 SQLite 전문가다. 아래 스키마를 보고 사용자 질문에 답하는 SQL을 작성하라.

{SCHEMA}

규칙:
- SELECT 문만 작성한다.
- 반드시 research_report 테이블만 사용한다.
- 종목명 검색은 LIKE '%종목명%' 사용.
- 결과는 최대 20건으로 제한 (LIMIT 20).
- SQL만 출력하라. 설명, 주석, 마크다운(```) 없이 순수 SQL 한 줄만.

사용자 질문: {question}

SQL:"""

    result = llm_answer(prompt, temperature=0.1)

    # 마크다운/설명 제거
    sql = result.strip()
    sql = re.sub(r"```sql|```", "", sql).strip()
    # 첫 SELECT부터 추출
    match = re.search(r"select.*", sql, re.IGNORECASE | re.DOTALL)
    if match:
        sql = match.group(0).strip()
    # 세미콜론 정리
    sql = sql.rstrip(";").strip()

    return sql


def run_sql(sql: str):
    """SQL 실행 (안전 검증 후)"""
    if not _is_safe_sql(sql):
        return None, "안전하지 않은 쿼리입니다 (SELECT만 허용)."

    try:
        conn = sqlite3.connect(os.path.join(BASE_DIR, "research.db"))
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute(sql)
        rows = [dict(r) for r in c.fetchall()]
        conn.close()
        return rows, None
    except Exception as e:
        return None, f"쿼리 실행 오류: {e}"


def ask(question: str) -> str:
    """질문 → SQL 생성 → 실행 → 자연어 응답"""
    sql = generate_sql(question)
    logger.info(f"생성된 SQL: {sql}")

    rows, error = run_sql(sql)

    if error:
        return f"질문을 처리하지 못했습니다. ({error})"

    if not rows:
        return "조건에 맞는 리포트가 없습니다."

    # 결과를 자연어로 변환
    prompt = f"""사용자 질문과 DB 조회 결과를 보고 한국어로 깔끔하게 답하라.
숫자나 목록은 정확히 전달하고, 불필요한 설명은 생략하라.

질문: {question}
조회 결과 ({len(rows)}건): {rows}

답변:"""

    lines = [f"총 {len(rows)}건"]
    for r in rows:
        lines.append(f"[{r.get('published_date','')}] {r.get('ticker_name','')} - {r.get('title','')} ({r.get('security_firm','')})")
    return "\n".join(lines)




if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # 테스트
    print(ask("BUY 의견 종목 몇개야?"))