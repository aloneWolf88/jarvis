# jarvis 프로젝트 규칙

## 환경
- Python 3.x, python-telegram-bot v22.8 (polling)
- SQLite, httpx < 0.28, Ollama (qwen3-coder:30b, localhost:11434)
- 파이프라인: batch1(Naver Finance 크롤링) → batch2(요약) → Telegram 발송

## Claude 작업 범위
- 코드 전체 작성 금지. design.md 설계서만 작성
- 소스 전체 읽기 금지. 제시된 부분만 참조
- design.md 저장: docs/design/design_{기능명}.md

## 코딩 컨벤션
- logging 모듈 사용, print 금지
- async/await 기반 (python-telegram-bot v22 스타일)