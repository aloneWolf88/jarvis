# design_folder_fix.md — main --batch / telegram-bot 텔레그램 미발송 버그 수정

작성일: 2026-07-13

---

## 1. 문제정의

| 항목 | 내용 |
|---|---|
| 증상 | `main.py --batch` 실행 및 `scheduler.py` 자동 실행 시 **오류 없이 "성공"으로 끝나지만 텔레그램 알림이 오지 않음** |
| 발생 시점 | 2026-07-13 배치부터 (08:17, 10:52 실행분) |
| 로그 근거 | `logs/bot_20260713.log`: `🏁 통합 배치 완료: 신규 6 / 요약 0 / 전송 0 / 성공`, `신규 64 / 요약 0 / 전송 0 / 성공` — **요약 0·전송 0인데 "성공"** |
| DB 근거 | `research.db`: 2026-07-13 수집분 32건이 `status='collected'`로 적체, `analyzed` 0건 |
| 영향 | 신규 리포트 알림 전면 중단 + 실패 사실 인지 불가(무증상 장애) |

---

## 2. 원인분석

### 2-1. 장애 체인

```
Ollama(localhost:11434) 미기동/연결 불가
  → summarizer.llm_answer(): 예외를 catch 후 "" 반환 (예외 전파 안 됨)
  → batch2_job(): 전 건 "LLM 요약 결과 없음" → failed 처리, status='collected' 유지
  → notifier.notify_new_reports(): status='analyzed'만 조회 → 대상 0건 → 발송 없이 0 반환
  → orchestrator.batch_job(): batch2가 예외 없이 반환 → ok=True → "성공" 로그
  → scheduler.batch_job(): returncode=0 + 실패 마커 없음 → "성공" 기록
```

### 2-2. 원인 분류

| # | 구분 | 위치 | 내용 |
|---|---|---|---|
| R1 | **근본 원인** | 실행 환경 | Ollama 서버 미기동 → LLM 요약 전 건 `Connection error` (당일 로그 95건) |
| R2 | **은폐 버그** | `orchestrator.batch_job()` | `ok` 판정이 예외 발생 여부만 반영. `failed>0` 또는 "대상>0인데 done==0"이어도 성공 처리 |
| R3 | 은폐 버그 | `summarizer.llm_answer()` | 연결 실패·요약 실패를 구분 없이 `""` 반환 → 호출자가 LLM 다운을 감지 불가 |
| R4 | 비효율 | `batch2_job()` | LLM 다운 상태에서도 건당 재시도(~15초)를 전 건 반복 → 64건 × 15초 ≈ 17분 낭비 (10:52→11:09 실행 소요와 일치) |
| R5 | 관측성 부족 | `notifier.notify_new_reports()` | 대상 0건이면 로그 없이 조용히 `return 0` → "발송할 게 없어서 안 보낸 것"과 "실패로 못 보낸 것" 구분 불가 |
| R6 | 관측성 부족 | 시스템 전체 | 배치 실패를 텔레그램으로 알릴 경로 없음 → 사용자는 "알림이 안 온다"로만 인지 |

※ 참고: `notifier._ROOT` 경로(4단계 상승 → jarvis/config.yaml), 토큰, `allow_from` 설정은 모두 정상 확인됨. 발송 로직 자체 문제 아님.

---

## 3. 대상 파일

| 파일 | 변경 여부 | 역할 |
|---|---|---|
| `bots/research/modules/summarizer.py` | 수정 | LLM 헬스체크 함수 추가 |
| `bots/research/modules/batch2.py` | 수정 | 배치 시작 전 헬스체크, 실패 시 조기 중단 |
| `bots/research/modules/orchestrator.py` | 수정 | ok 판정 보강 + 실패 시 관리자 알림 호출 |
| `bots/research/modules/notifier.py` | 수정 | 대상 0건 로그 + 관리자 알림 함수 추가 |
| `bots/research/main.py` | 변경 없음 | `--batch` → `orchestrator.batch_job()` 위임 구조 유지 |
| `scheduler.py` | 변경 없음 | stdout의 "통합 배치 완료" 파싱은 기존 포맷 유지로 호환 |
| `telegram_bot.py` | **변경 금지** | 기존 핸들러 유지 (제약조건) |

---

## 4. 함수 명세

### 4-1. `summarizer.check_llm_health()` — 신규

| 항목 | 내용 |
|---|---|
| 시그니처 | `check_llm_health(timeout: int = 5) -> bool` |
| 동작 | `config["ollama"]["api_base"]` 루트(`/models` 등 경량 엔드포인트)에 짧은 timeout으로 1회 요청. 응답 수신 시 True, 연결 실패/타임아웃 시 False |
| 로그 | 실패 시 `ERROR "LLM 서버 연결 불가: {api_base}"` |
| 주의 | 기존 `llm_answer()` 시그니처·반환 규약(`""` 반환)은 변경하지 않음 |

### 4-2. `batch2.batch2_job()` — 수정

| 항목 | 내용 |
|---|---|
| 추가 동작 | 대상 조회 후 대상>0이면 루프 진입 **전** `check_llm_health()` 1회 호출 |
| 헬스체크 실패 시 | 루프 스킵, 즉시 반환. 반환 dict에 `"llm_down": True` 키 추가 |
| 반환 | `{"done", "skipped", "failed", "done_ids", "llm_down"}` — 기존 키 유지(하위 호환), `llm_down` 기본 False |
| 효과 | R4 해소: LLM 다운 시 64건 × 15초 재시도 → 5초 내 종료 |

### 4-3. `orchestrator.batch_job()` — 수정

| 항목 | 내용 |
|---|---|
| ok 판정 보강 | 기존 "예외 발생 시 False"에 추가: ① `r2["llm_down"]` True → False, ② `r2["failed"] > 0` → False, ③ 크롤링 신규>0인데 `done==0` → False |
| 실패 시 알림 | `ok=False`가 확정되면 `notifier.send_admin_alert(사유 문자열)` 호출 (try로 감싸 알림 실패가 배치를 죽이지 않도록) |
| 완료 로그 | 기존 포맷 `🏁 통합 배치 완료: 신규 N / 요약 M / 전송 K / 성공|실패` **유지** — scheduler.py의 stdout 파싱 호환 |
| 반환 | 기존 dict에 `"sent"` 키 추가 (현재 누락) |

### 4-4. `notifier.send_admin_alert()` — 신규

| 항목 | 내용 |
|---|---|
| 시그니처 | `send_admin_alert(reason: str) -> bool` |
| 동작 | 기존 `_send()` 재사용. `"⚠️ 배치 실패: {reason} ({시각 KST})"` 형식으로 CHAT_IDS에 발송 |
| 효과 | R6 해소: 무증상 장애 → 실패 즉시 텔레그램 인지 |

### 4-5. `notifier.notify_new_reports()` — 수정

| 항목 | 내용 |
|---|---|
| 추가 동작 | 대상 0건일 때 `INFO "📤 텔레그램 알림: 발송 대상(analyzed) 0건 — 생략"` 로그 후 0 반환 |
| 유지 | 발송 성공 시에만 `analyzed → notified` 전이하는 기존 재시도 보장 로직 그대로 |

---

## 5. 해결책 (요약)

| 단계 | 조치 | 해소 대상 |
|---|---|---|
| 1 | (운영) Ollama 기동 확인 — `stock_day.bat`에 ollama 서비스 기동/확인 선행 단계 추가 검토 | R1 |
| 2 | batch2 시작 전 LLM 헬스체크 → 다운이면 조기 중단(`llm_down` 플래그) | R3, R4 |
| 3 | orchestrator ok 판정 보강 (failed>0, done==0, llm_down 반영) | R2 |
| 4 | 실패 시 텔레그램 관리자 알림 발송 | R6 |
| 5 | notifier 대상 0건 로그 명시 | R5 |

적체 데이터 처리: 수정 배포 후 첫 배치에서 `collected` 32건이 자동으로 batch2 → notify로 흘러가므로 **별도 마이그레이션 불필요** (기존 잔량 재시도 설계 활용).

---

## 6. 제약조건

| # | 제약 |
|---|---|
| C1 | **`telegram_bot.py` 기존 핸들러 변경 금지** (cmd_start, cmd_research, cmd_callback, handle_message, handle_file 등) |
| C2 | `🏁 통합 배치 완료: 신규 N / 요약 M / 전송 K` stdout 포맷 변경 금지 — `scheduler.py`가 문자열 분할로 파싱 |
| C3 | 기존 함수 반환 dict의 기존 키 삭제·타입 변경 금지 (키 추가만 허용) |
| C4 | 발송 성공 시에만 `notified` 전이하는 규약 유지 (실패 시 `analyzed` 잔존 → 다음 배치 재시도) |
| C5 | 코드 수정은 변경분 위주로, 삭제는 주석 처리 + 추가분 주석 표기 |

---

## 7. 테스트 방법

| # | 시나리오 | 절차 | 기대 결과 |
|---|---|---|---|
| T1 | 정상 경로 | Ollama 기동 확인 → `python main.py --batch` | `collected` 32건 요약 → 텔레그램 수신 → `status='notified'` 전이 |
| T2 | LLM 다운 | Ollama 중지 → `python main.py --batch` | 5초 내 배치2 조기 종료, 로그 `실패`, `⚠️ 배치 실패` 텔레그램 수신, status는 `collected` 유지 |
| T3 | 다운 후 복구 | T2 직후 Ollama 기동 → 재실행 | 적체분 정상 요약·발송 (재시도 보장 확인) |
| T4 | 신규 0건 | 연속 2회 실행 | 2회차: `대상 0건 — 생략` 로그, 텔레그램 미발송(정상), "성공" |
| T5 | 스케줄러 경로 | `python scheduler.py` 1사이클 | scheduler.log에 신규/요약/전송 수치 정상 파싱 (C2 호환 확인) |
| T6 | 핸들러 무영향 | `python telegram_bot.py` 기동 → `/research 오늘 리포트` | 기존 응답 동일 (C1 검증) |
| T7 | 부분 실패 | 요약 중 일부만 실패하도록 유도(임의 1건 pdf_url 오염) | `failed>0` → "실패" 로그 + 관리자 알림, 성공분은 정상 발송 |

DB 검증 쿼리: `SELECT status, COUNT(*) FROM research_report GROUP BY status` — T1 후 `collected` 0건 확인.
