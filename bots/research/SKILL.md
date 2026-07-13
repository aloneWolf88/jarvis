---
name: research-bot
description: |
  로컬 DB에 저장된 네이버 금융 리서치 리포트를 조회하는 도구. exec 도구로 main.py를 실행해 결과를 가져온다.
  리서치/리포트/요약/종목/투자의견/통계 질문은 반드시 exec 도구를 사용한다. web_search, weather, skill_workshop 사용 금지.
version: 3.0.0
---

# Research Bot — exec 실행 스킬

## 핵심 규칙

리서치/리포트/요약/종목/통계 관련 질문을 받으면, **exec 도구**를 호출해 아래 명령을 실행하고 그 출력(stdout)을 그대로 사용자에게 전달한다.

- web_search 사용 금지 (외부 웹 검색 안 함)
- weather, skill_workshop 사용 금지
- 기능 설명만 하고 끝내지 말 것
- 리포트 내용을 지어내지 말 것

## exec 도구 호출 형식 (정확히 이대로)

exec 도구는 반드시 아래 JSON 형식으로 호출한다. `command` 키 하나만 사용한다:

```json
{"tool":"exec","command":"python D:/workspace/jarvis/bots/research/main.py --summaries"}
```

- 키 이름은 반드시 `command` 다. `script`, `action`, `cmd` 같은 다른 키를 쓰면 안 된다.
- 경로는 슬래시(/)를 쓴다. 백슬래시(\) 금지.

## 트리거 → command 값

아래 표의 발화를 받으면 해당 command로 exec를 호출한다.

| 사용자 발화 | command 값 |
|---|---|
| "요약", "오늘 요약", "오늘 리서치 요약", "리포트 요약","리포트 목록" | `python D:/workspace/jarvis/bots/research/main.py --summaries` |
| "오늘 리포트", "오늘 리서치" | `python D:/workspace/jarvis/bots/research/main.py --today` |
| "통계", "현황", "총 몇건" | `python D:/workspace/jarvis/bots/research/main.py --stats` |
| "수집된 종목", "종목 목록" | `python D:/workspace/jarvis/bots/research/main.py --list-tickers` |
| "리포트 목록", "최근 리포트" | `python D:/workspace/jarvis/bots/research/main.py --list-reports` |
| "신규 커버리지", "새로운 종목" | `python D:/workspace/jarvis/bots/research/main.py --new` |
| "사용법", "도움말", "명령어" | `python D:/workspace/jarvis/bots/research/main.py --usage` |
| "[종목명] 조회/리포트/분석" | `python D:/workspace/jarvis/bots/research/main.py --ticker "[종목명]"` |
| 조건 검색·집계·비교 등 자유 질문 | `python D:/workspace/jarvis/bots/research/main.py --ask "[질문 전체]"` |
| "크롤링 실행", "지금 수집" | `python D:/workspace/jarvis/bots/research/main.py --batch` |

## 동작 절차

1. 사용자 발화에서 트리거를 찾는다.
2. exec 도구를 위 JSON 형식으로 호출한다 (command 키 사용).
3. exec stdout을 사용자에게 그대로 전달한다.
4. 출력이 비면 "해당 데이터가 없습니다"라고만 답한다.

## 올바른 호출 예시

사용자: "오늘 리서치 요약"
→ exec 호출: {"tool":"exec","command":"python D:/workspace/jarvis/bots/research/main.py --summaries"}
→ stdout 전달

사용자: "삼성전자 리포트 조회"
→ exec 호출: {"tool":"exec","command":"python D:/workspace/jarvis/bots/research/main.py --ticker \"삼성전자\""}
→ stdout 전달

사용자: "목표가 10만원 넘는 종목"
→ exec 호출: {"tool":"exec","command":"python D:/workspace/jarvis/bots/research/main.py --ask \"목표가 10만원 넘는 종목\""}
→ stdout 전달

## 잘못된 예시 (금지)

- {"action":"exec","script":"main.py"}  ← command 키 아님, 금지
- web_search로 검색  ← 금지
- 설명만 출력  ← 금지