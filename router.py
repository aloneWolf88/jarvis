# router.py

import re

YOUTUBE_PATTERNS = [
    "youtube.com",
    "youtu.be"
]


def route(text: str):
    text = text.strip()
    print("[ROUTE] Processing text:", text)

    # 1. YouTube
    if any(x in text for x in YOUTUBE_PATTERNS):
        return {
            "domain": "youtube",        # 수정: "bot" → "domain"
            "command": "summarize",
            "args": {"url": text}       # 수정: "text" → "args": {"url"}
        }

    if text in ("--help", "?", "help", "도움말", "사용법"):
        return {
            "domain": "research",
            "command": "help",
        }


    # 2. Research 정적 명령
    RESEARCH_KEYWORDS = {               # 수정: "--summaries" → "summaries" 등
        "산업분석": "summaries",
        "통계": "stats",
        "오늘 리포트": "today",
        "오늘 요약": "today",
        "어제 리포트": "yesterday",
        "어제 요약": "yesterday",
        "종목 목록": "list_tickers",
        "리포트 목록": "list_reports",
        "신규 종목": "new",
        "크롤링": "batch",      # 추가: 배치 실행 (크롤링+요약)
        "수집": "batch",        # 추가: 동의어      

        "오늘 리포트": "today",          # 유지
        "오늘 산업분석": "summaries",   # 추가
        "크롤링": "batch",
        "수집": "batch",

        # 구체적인 "오늘 요약" 등을 먼저 검사한 뒤 일반 키워드 요약 처리
        "요약": "summaries",

    }

    for keyword, command in RESEARCH_KEYWORDS.items():
        if keyword in text:
            #print("[ROUTE] Detected research command:", keyword)
            # 추가: 키워드/"리포트" 제외한 나머지를 name으로 추출
            name = text.replace(keyword, "").replace("리포트", "").strip()
            return {
                "domain": "research",
                "command": command,
                "args": {"name": name} if name else {},   # 추가: name 전달
            }

    # 3. 종목 리포트 (기존 "조회" 표현도 하위 호환)
    m = re.match(r"(.+?)\s*(?:리포트|조회)$", text)
    if m:
        #print("[ROUTE] Detected stock lookup. Ticker:", m.group(1).strip())
        query_term = m.group(1).strip()      
        print(f"[ROUTE] Detected lookup: {query_term}")
        return {
           # "domain": "research",       # 수정: "bot" → "domain"
           # "command": "ticker",        # 수정: "--ticker" → "ticker"
           # "args": {"ticker": m.group(1).strip()}  # 수정: "value" → "args"
           "domain": "research",
           "command": "lookup",
           "args": {"query": query_term}
        }

    # 4. Research 자연어
    research_words = ["목표가", "투자의견", "증권사", "상향", "하향", "리포트", "종목", "주가", "실적"]
    if any(word in text for word in research_words):
        return {
            "domain": "research",       # 수정: "bot" → "domain"
            "command": "query",         # 수정: "--query" → "query"
            "args": {"query": text}     # 수정: "value" → "args"
        }

    # 5. 일반 대화
    return {
        "domain": "chat",               # 수정: "bot" → "domain"
        "command": "chat",
        "args": {"text": text}          # 수정: "value" → "args"
    }
