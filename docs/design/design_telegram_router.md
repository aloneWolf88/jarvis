# 설계서: Telegram 메시지 라우터 (jarvis ↔ OpenClaw)

- 문서: `docs/design/design_telegram_router.md`
- 작성일: 2026-07-04
- 대상 버전: python-telegram-bot v22.8 (async), httpx<0.28

---

## 1. 문제정의

| 항목 | 내용 |
|---|---|
| 증상 | jarvis(`telegram_bot.py`)와 OpenClaw가 동일 봇 토큰으로 동시에 `getUpdates` polling → Telegram API `409 Conflict` 발생 |
| 영향 | 봇 응답 유실·중복, polling 루프 반복 실패 |
| 요구 | ① polling은 jarvis 단독 ② 명령어(`/research`, `/stock` 등)는 jarvis가 직접 처리 ③ 비명령어 메시지는 OpenClaw HTTP API로 위임 후 응답 회신 ④ OpenClaw 60초 타임아웃/오류 시 "처리 실패" 안내 |

## 2. 원인분석

| 원인 | 설명 |
|---|---|
| getUpdates 단일 소비자 제약 | Telegram Bot API는 봇 토큰당 polling 소비자를 1개만 허용. 두 프로세스가 같은 토큰으로 polling하면 나중 요청이 409 반환 |
| 이중 진입점 | OpenClaw telegram 채널과 jarvis 봇이 각각 독립적으로 Telegram에 직결되어 있음 |
| 라우팅 부재 | 현재 `telegram_bot.py`는 CommandHandler만 등록. 비명령어 텍스트를 받을 MessageHandler가 없어 OpenClaw로 위임할 경로 자체가 없음 |

## 3. 대상파일

| 파일 | 변경 유형 | 내용 |
|---|---|---|
| `telegram_bot.py` | 수정 | MessageHandler 추가(비명령어 → OpenClaw 위임). 기존 핸들러 변경 금지 |
| `.env` | 추가 | `OPENCLAW_TOKEN=<Bearer 토큰>` (신규 키) |
| `config.yaml` | 선택 | `openclaw.url` 등 엔드포인트 설정 외부화 시 사용 (기본값 하드코딩 가능) |
| OpenClaw 설정 (`openclaw.json` 또는 채널 설정) | 수정 | telegram 채널 비활성화 (§5.1 절차) |

## 4. 함수명세

### 4.1 신규 함수

| 함수 | 시그니처 | 역할 |
|---|---|---|
| `handle_message` | `async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None` | 비명령어 텍스트 진입점. `is_allowed()` 권한 체크 → `ask_openclaw()` 호출 → 응답을 `_split()`으로 분할 회신. 실패 시 "❌ 처리 실패" 안내 |
| `ask_openclaw` | `async def ask_openclaw(text: str, session_key: str) -> str` | `POST http://127.0.0.1:18789/v1/responses` 호출. 헤더 `Authorization: Bearer {OPENCLAW_TOKEN}`. `httpx.AsyncClient(timeout=60)` 사용. 응답 JSON에서 텍스트 추출 후 반환. 타임아웃/HTTP 오류/연결 거부 시 예외 전파 |

### 4.2 기존 함수 (변경 없음 — 재사용만)

| 함수 | 재사용 방식 |
|---|---|
| `is_allowed(update)` | `handle_message` 진입 시 동일하게 권한 체크 |
| `_split(text)` | OpenClaw 응답 4096자 분할 회신 |
| `cmd_start`, `cmd_research`, `cmd_callback` | 그대로 유지. 등록 순서·인자 변경 금지 |
| `main()` | 핸들러 등록부에 MessageHandler **1줄 추가**만 허용 |

### 4.3 핸들러 등록 규칙

| 항목 | 규칙 |
|---|---|
| 필터 | `filters.TEXT & ~filters.COMMAND` (명령어는 기존 CommandHandler가 선점) |
| 등록 위치 | 기존 `add_handler` 뒤, `run_polling` 앞 |
| 우선순위 | CommandHandler가 group 0에서 먼저 매칭되므로 명령어/비명령어 충돌 없음 |

## 5. 해결책

### 5.1 OpenClaw telegram 채널 비활성화 절차 (getUpdates Conflict 해소)

| 순서 | 작업 | 확인 방법 |
|---|---|---|
| 1 | `openclaw gateway stop` 으로 게이트웨이 중지 | 프로세스 종료 확인 |
| 2 | OpenClaw 설정에서 telegram 채널 `enabled: false` (또는 채널 설정 블록 제거) | 설정 파일 diff |
| 3 | `openclaw gateway start` 로 게이트웨이 재기동 (HTTP API만 활성) | `http://127.0.0.1:18789` 응답 확인 |
| 4 | OpenClaw 로그에 telegram polling 로그가 없는지 확인 | 로그 grep `getUpdates` |
| 5 | `python telegram_bot.py` 기동 → 409 미발생 확인 | jarvis 로그 |

핵심 원칙: **Telegram 직결(polling)은 jarvis 단독, OpenClaw는 localhost HTTP API 백엔드로만 사용.**

### 5.2 메시지 라우팅 흐름

```
텔레그램 수신
 ├─ /research, /stock 등 명령어 → 기존 CommandHandler (변경 없음)
 └─ 비명령어 텍스트 → handle_message
      → ask_openclaw (POST /v1/responses, Bearer OPENCLAW_TOKEN, timeout=60s)
      ├─ 성공 → 응답 텍스트 _split() 분할 회신
      └─ 타임아웃/오류 → "❌ 처리 실패: OpenClaw 응답 없음" 회신 + logger.exception
```

### 5.3 오류 처리 방침

| 상황 | 처리 |
|---|---|
| `httpx.TimeoutException` (60초 초과) | "❌ 처리 실패: 응답 시간 초과(60초)" 회신, `logger.warning` |
| `httpx.HTTPStatusError` (401/5xx 등) | "❌ 처리 실패" 회신, 상태코드 `logger.error` (토큰 노출 금지) |
| `httpx.ConnectError` (OpenClaw 미기동) | "❌ 처리 실패: OpenClaw 연결 불가" 회신, `logger.error` |
| 응답 JSON 파싱 실패 | "❌ 처리 실패" 회신, `logger.exception` |

### 5.4 인증

| 항목 | 내용 |
|---|---|
| 토큰 위치 | `.env` 의 `OPENCLAW_TOKEN` (`python-dotenv` 또는 `os.environ` 로드) |
| 전송 방식 | `Authorization: Bearer {OPENCLAW_TOKEN}` 헤더 |
| 기동 검증 | 시작 시 토큰 미설정이면 `logger.warning` 후 비명령어 라우팅 비활성 (명령어 기능은 정상 동작) |

## 6. 제약조건

| # | 제약 |
|---|---|
| 1 | python-telegram-bot v22.8 async API 준수 (`Application.builder()`, async 핸들러) |
| 2 | `httpx<0.28` — `AsyncClient` timeout 파라미터 방식 이 버전 기준 |
| 3 | 기존 핸들러(`cmd_start`, `cmd_research`, `cmd_callback`) 및 등록부 변경 금지, MessageHandler 추가만 허용 |
| 4 | `logging` 모듈 사용 (`print` 디버그 신규 추가 금지), 토큰 값 로그 출력 금지 |
| 5 | OpenClaw 호출은 `127.0.0.1` localhost 한정 (외부 노출 금지) |
| 6 | 긴 응답은 기존 `_split()` 재사용 (4096자 제한) |

## 7. 테스트방법

| # | 시나리오 | 절차 | 기대 결과 |
|---|---|---|---|
| T1 | Conflict 해소 | OpenClaw 재기동 + jarvis 기동 후 5분 관찰 | 양쪽 로그에 409 Conflict 없음 |
| T2 | 명령어 라우팅 | `/research 테스트 질의` 전송 | 기존과 동일하게 `cmd_research` 처리 (회귀 없음) |
| T3 | 비명령어 라우팅 | 일반 텍스트 전송 | OpenClaw 응답이 텔레그램으로 회신 |
| T4 | 타임아웃 | OpenClaw를 60초 이상 지연되는 상태로 두고 텍스트 전송 | "❌ 처리 실패: 응답 시간 초과" 회신, 봇은 계속 동작 |
| T5 | OpenClaw 미기동 | 게이트웨이 중지 후 텍스트 전송 | "❌ 처리 실패: OpenClaw 연결 불가" 회신 |
| T6 | 인증 오류 | `.env` 토큰을 잘못된 값으로 변경 | "❌ 처리 실패" 회신, 로그에 401 기록 |
| T7 | 긴 응답 분할 | 4096자 초과 응답 유도 | 여러 메시지로 분할 회신 |
| T8 | 권한 체크 | `allow_from` 외 사용자로 텍스트 전송 | 무응답 (기존 정책 동일) |

## 부록: 관련 코드 (현행 `telegram_bot.py` 핸들러 등록부)

```python
# ── 메인 ──
def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("research", cmd_research))
    app.add_handler(CallbackQueryHandler(cmd_callback))   # 추가: 버튼 클릭 처리

    logger.info("✅ Jarvis Bot 시작 (폴링)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
```

※ 현행 코드에 `/stock` CommandHandler는 미등록 상태 — 추가 시에도 본 설계의 라우팅 규칙(명령어=CommandHandler, 비명령어=MessageHandler)에 영향 없음.
※ 현행 설정은 `config.yaml` 기반 — `OPENCLAW_TOKEN`만 `.env`에서 별도 로드 (요구사항 준수).
