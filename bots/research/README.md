# 네이버 리서치 봇 (OpenClaw + qwen3:8b)

네이버 금융 리서치 자동 크롤링 + Ollama 요약 + OpenClaw 연동

## 아키텍처

```
Windows Task Scheduler (매일 08:00)
    ↓
[main.py --batch] → 크롤링 → PDF 추출 → qwen3:8b 요약 → SQLite 저장
    
OpenClaw Gateway (포트 18789) ← 텔레그램/Slack/Discord 등
    ↓
[main.py --query/--today/--ticker] → SQLite 조회 → qwen3:8b 응답
    ↓
OpenClaw → 텔레그램 채널로 전달
```

## 사전 준비

```bash
# 1. Ollama 모델 준비
ollama pull qwen3:8b
ollama serve

# 2. OpenClaw Gateway 실행 확인
openclaw gateway start
```

## 설치

```bash
cd D:\workspace\jarvis\bots\research
pip install -r requirements.txt
python main.py --init
```

## CLI 사용법

```bash
python main.py --init                          # DB 초기화
python main.py --batch                         # 크롤링+요약 1회 실행
python main.py --today                         # 오늘 리포트 요약
python main.py --ticker 삼성전자                # 종목별 조회
python main.py --query "삼성전자 상향 몇건?"    # 자연어 질문
python main.py --new                           # 신규 커버리지 종목
python main.py --scheduler                     # 스케줄러 상주 모드
python main.py --usage                         #사용법
```

## OpenClaw 스킬 등록

```bash
# openclaw_skill.yaml을 OpenClaw 스킬 디렉토리에 복사
cp openclaw_skill.yaml D:\workspace\jarvis\skills\
```

또는 OpenClaw에서 직접 shell 명령어로 호출:
- "오늘 리서치 요약" → `python main.py --today`
- "삼성전자 조회" → `python main.py --ticker 삼성전자`
- "삼성전자 상향 몇건?" → `python main.py --query "삼성전자 상향 몇건?"`

## Windows 스케줄러 등록

```powershell
schtasks /create /tn "ResearchBatch" ^
  /tr "python D:\workspace\jarvis\bots\research\main.py --batch" ^
  /sc daily /st 08:00 /f
```

또는 run_batch.bat / run_scheduler.bat 사용

## 텔레그램 사용 (OpenClaw 경유)

OpenClaw 텔레그램 채널에서:
- "오늘 리서치 요약 보여줘"
- "삼성전자 최근 한달 투자의견 상향 몇건?"
- "신규 커버리지 종목 알려줘"
- "리서치 크롤링 실행해줘"
