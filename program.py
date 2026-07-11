from router import route
from bots.research.bot import ResearchBot


from main import cmd_today, cmd_yesterday
research = ResearchBot()


# 수정: domain 파라미터 추가 (기본 None → 기존 호출 하위 호환)
def process(text, domain=None):

    result = route(text)
    #print(f"[PROC-DEBUG] result={result!r}") 
    routed_domain = result["domain"]
    command = result["command"]
    args = result.get("args", {})

    #print(f"[PROC-DEBUG] domain={routed_domain!r} command={command!r} args={args!r}")
    # 추가: domain이 명시되면 router 분류보다 우선 (오분류 방지)
    #  - /research_bot 으로 들어온 입력은 domain="research" 강제
    #  - router가 chat 등으로 잘못 분류해도 research로 처리
    if domain:
        routed_domain = domain

    # Research
    if routed_domain == "research":   # 수정: domain → routed_domain
   
        if command == "summaries":
            print(f"[PROG-DEBUG] name={args.get('name')!r}")
            return research.summaries(args.get("name"))  

        elif command == "stats":
            return research.stats()

        elif command == "today":            
            return cmd_today()  

        elif command == "yesterday":
            return cmd_yesterday()

        elif command == "list_tickers":
            return research.list_tickers()

        elif command == "list_reports":
            return research.list_reports()

        elif command == "new":
            return research.new()

        elif command == "ticker":
            return research.ticker(args["ticker"])

        elif command == "query":
            return research.query(args["query"])
        
        elif command == "batch":
            return research.batch()
        # 추가: 키워드 미매칭으로 chat 분류됐으나 research 명령인 경우 → 자유 질문 처리
        elif command == "chat":
            return research.query(args["text"])
        elif command == "lookup":
            return research.lookup(args.get("query"))            
        elif command == "help":
            return _research_help()    

    # 추가: youtube domain (나중에 youtube_bot 구현 후 활성화)
    # elif routed_domain == "youtube":
    #     return youtube.summarize(args["url"])

    # 추가: docsort domain (나중에 doc_sort 구현 후 활성화)
    # elif routed_domain == "docsort":
    #     return docsort.run(args["path"], args.get("mode"))

    # Chat
    return "처리 불가"


def _research_help():
    return (
        "📖 Research 명령어\n\n"
        "[조회]\n"
        "/research 요약 — 전체 리포트 요약\n"
        "/research [키워드] 요약 — 키워드 필터 요약\n"
        "/research 통계 — 전체 통계\n"
        "/research 오늘 리포트 — 오늘 수집분\n"
        "/research 어제 리포트 — 어제 수집분\n"
        "/research 종목 목록 — 수집 종목\n"
        "/research 리포트 목록 — 최근 리포트\n"
        "/research 신규 종목 — 신규 커버리지\n"
        "/research [주식명] 리포트 — 종목 분석\n\n"
        "[자연어]\n"
        "/research 삼성전자 목표가 상향 몇건? — 자연어 질문\n\n"
        "[관리]\n"
        "/research 수집 — 크롤링+요약 수동 실행\n"
        "/research --help — 이 도움말"
    )
