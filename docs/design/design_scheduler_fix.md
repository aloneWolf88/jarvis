# design_scheduler_fix.md — scheduler ↔ main --batch 결과 불일치(텔레그램 미수신) 수정 설계

- 작성일: 2026-07-11 (재작성)
- 요구사항: `main.py --batch` 실행 결과와 `scheduler.py` 실행 결과가 동일할 것 (텔레그램 알림 포함)
- 제약: 기존 핸들러(main.py의 cmd_* / argparse 분기) 변경 금지, 코드 구현 없음(설계만)
- 템플릿: 문제정의 / 원인분석 / 대상파일 / 함수명세 / 해결책 / 제약조건 / 테스트방법

---

## 1. 문제정의

| 항목 | 내용 |
|---|---|
| 증상 | `main.py --batch` 수동 실행 시 텔레그램 수신 정상. `scheduler.py` 실행 시 오류 없이 "성공"/"완료" 로그가 남지만 **텔레그램이 오지 않음** |
| 기대 동작 | 실행 경로(수동/스케줄러)와 무관하게 동일 파이프라인(크롤링→요약→알림)이 동일 결과 산출 |
| 영향 범위 | 스케줄러 상주 운영 시 신규 리포트 알림 누락, 미전송(analyzed) 잔량 영구 미발송 |
| 로그 증거 | `logs/scheduler.log` 11:21 이후(subprocess 전환 후) 매 주기 "신규 리포트 없음 — 요약·알림 생략" 반복, `텔레그램 알림` 로그 부재 / 11:21 이전(구 직접 import)은 `텔레그램 알림: N/N건` 정상 |

## 2. 원인분석

### 2.1 실행 경로 차이 (핵심)

| 모드 | 호출 방식 | batch_job 호출부 | saved==0 동작 |
|---|---|---|---|
| 수동 `main.py --batch` | 직접 실행 | main.py:499 `batch_job()` (orchestrator) | **early return** (batch2·notify 생략) |
| `scheduler.py` (현재) | subprocess `main.py --batch` | scheduler.py:33 → main.py:499 | **early return** (batch2·notify 생략) |
| 구 `out/scheduler.py` (참고) | 직접 import orchestrator | out/scheduler.py:17 `batch_job()` | early return **없음** → batch2·notify 항상 실행 |

> 현재 scheduler.py는 subprocess로 main.py를 호출하므로 main.py --batch와 동일한 orchestrator 경로를 탄다. 둘 다 `saved==0`이면 notify_new_reports에 도달하지 못한다. 그러나 수동 실행은 "신규가 쌓인 후" 돌리므로 saved>0이기 쉽고, 스케줄러는 고빈도(5~45분)라 대부분 주기에서 saved==0이다. **결과적으로 수동은 알림이 오고 스케줄러는 안 온다.**

### 2.2 근본 원인 목록

| # | 원인 | 위치 | 설명 | 심각도 |
|---|---|---|---|---|
| 1 | **saved==0 조기 반환** | orchestrator.py `batch_job()` L24-26 | `batch1` 신규 저장이 0건이면 `batch2`·`notify_new_reports` 모두 건너뜀. notifier는 `WHERE status='analyzed'`(잔량 재시도 포함)로 스스로 대상을 판단하도록 설계되었으나, 조기 반환 때문에 notify 자체가 호출되지 않아 잔량 발송·재시도 불가 | **높음 (주원인)** |
| 2 | **return문 누락** | orchestrator.py `batch_job()` 끝(L46) | 정상 종료 경로에 `return` 문이 없어 함수가 `None` 반환. out/scheduler.py(참고)는 L39 `return {...}` 존재. 호출자가 결과값(saved/done/ok)을 받지 못해 결과 비교 불가 | 높음 |
| 3 | **재시도 설계 무력화** | orchestrator.py + notifier.py | notifier는 전송 실패 시 `analyzed` 유지("다음 배치 재시도") 설계(B안). 원인1 조기 반환 때문에 재시도 동작 안 함 | 높음 |
| 4 | **예외 삼킴 → exit 0** | orchestrator.py except 절 + main.py | 크롤링/요약/알림 예외 dict 정상 반환 → main.py exit 0 → scheduler.py returncode 0만 보고 "성공" 기록. "오류는 발생하지 않으나"의 정체 | 중간 |
| 5 | **성공 시 stdout 미기록** | scheduler.py `batch_job()` L43-49 | returncode 0이면 stdout의 결과 요약(신규/요약/전송 건수)을 logger.info로만 rstrip 출력, 체계적 기록 부족 → 수동/스케줄러 결과 비교 수단 미흡 | 중간 |
| 6 | (부수) **요일 판정** | scheduler.py `get_interval()` L63 | `0 <= weekday() <= 4`는 월(0)~금(4)로 **정상** (기존 설계서의 "1 <= weekday() <= 5" 오류는 현 코드에 없음) | 정상 (정정) |

> 참고: DB 경로(`db.py DB_PATH`), config 경로(notifier `_ROOT`=jarvis 루트, telegram 섹션 있음) 모두 `__file__` 기준 절대경로라 cwd 차이로 인한 불일치 없음(확인 완료).

## 3. 대상파일

| 파일 | 변경 여부 | 비고 |
|---|---|---|
| `research_bot/modules/orchestrator.py` | 수정 | 원인 1, 2, 3 해소 |
| `scheduler.py` | 수정 | 원인 4, 5 해소 (함수명 `batch_job()`) |
| `research_bot/main.py` | **변경 없음** | 기존 핸들러 변경 금지 제약 준수 |
| `research_bot/modules/notifier.py` | 변경 없음 | 미전송(analyzed) 일괄 push + 실패 시 유지 로직 그대로 활용 |
| `research_bot/modules/batch1.py`, `batch2.py` | 변경 없음 | — |

## 4. 함수명세

### 4.1 orchestrator.py — `batch_job()` (수정)

| 항목 | 내용 |
|---|---|
| 시그니처 | `batch_job() -> dict` (변경 없음) |
| 반환 | `{"saved": int, "done": int, "ok": bool}` — 기존 키(`saved`, `done`) 유지, `ok`(성공 여부) 추가 |
| 변경점 1 | `saved == 0` 조기 반환 **제거**. batch1 결과와 무관하게 batch2·notify를 항상 실행 (batch2는 `status='collected'` 잔량, notifier는 `status='analyzed'` 잔량을 스스로 조회하므로 대상 0건 시 조용히 종료 — 추가 비용 미미). 이를 통해 수동/스케줄러 결과 수렴 + 잔량 재발송(재시도 설계 복원) | 
| 변경점 2 | **끝에 `return {"saved": r1["saved"], "done": done_count, "ok": ok}` 추가** (out/scheduler.py L39 참고). `r2` 예외 시 `done_count=0`으로 안전하게 처리 |
| 변경점 3 | 단계별(batch1/batch2/notify) 개별 try 분리 + `ok` 플래그 — 크롤링 실패가 알림 단계까지 죽이지 않도록 |
| 변경점 4 | 완료 로그에 전송 건수 포함: `신규 N / 요약 M / 전송 K` |
| 호출자 영향 | main.py `--batch`, `--scheduler`(cmd_scheduler), scheduler.py 모두 시그니처 동일 → 무수정 호환 |

### 4.2 scheduler.py — `batch_job()` (수정) ※함수명 정정: `run_batch()`가 아님

| 항목 | 내용 |
|---|---|
| 시그니처 | `batch_job() -> dict` (변경 없음, L26) |
| 변경점 1 | returncode 0이어도 stdout의 "🏁 통합 배치 완료: 신규 N / 요약 M / 전송 K" 라인을 INFO로 명시적 기록 → 수동 실행과 결과값 비교 가능 |
| 변경점 2 | stdout/stderr에 실패 지표(`ok=False`, ERROR 로그) 포함 시 WARNING으로 승격 기록 |
| 변경점 3 | (선택) subprocess 결과 dict에 main.py 결과값(saved/done/ok) 반영 가능하면 반환값에 포함 |

### 4.3 scheduler.py — `get_interval()` (검토)

| 항목 | 내용 |
|---|---|
| 현재 상태 | `0 <= now.weekday() <= 4` (월~금) — **정상** (정정) |
| 비고 | 기존 설계서의 "1 <= weekday() <= 5 (화~토)" 오류는 현 코드에 없음. 변경 불필요 |

## 5. 해결책

| 단계 | 내용 | 해소 원인 |
|---|---|---|
| 1 | `batch_job()`에서 `saved==0` 조기 반환 제거 → 매 실행마다 "collected 요약 → analyzed 발송" 보장 → 수동/스케줄러 결과 수렴 + 잔량·실패분 다음 주기 자동 재발송(재시도 설계 복원) | 1, 3 |
| 2 | `batch_job()` 끝에 `return {"saved","done","ok"}` 추가 | 2 |
| 3 | 단계별 예외 분리 + `ok` 플래그 + 로그 강화 → 실패가 "성공"으로 위장되지 않음 | 4 |
| 4 | scheduler.py 성공 시에도 결과 요약 라인 INFO 기록 | 5 |
| 5 | get_interval() 요일 조건 — 현행 유지(정상 확인) | 6 |

> 설계 원칙: 알림 대상 판정은 호출 경로가 아닌 **DB status 상태 기계(collected → analyzed → notified)** 단일 기준으로 통일. 실행 주체(수동/스케줄러)는 트리거일 뿐 결과에 영향을 주지 않는다.

## 6. 제약조건

| # | 제약 |
|---|---|
| 1 | main.py의 기존 핸들러(cmd_*, argparse 분기) 변경 금지 |
| 2 | `batch_job()` 시그니처·기존 반환 키(`saved`, `done`) 유지 — 호출부 무수정 호환 |
| 3 | notifier의 상태 전이 규칙(전송 성공 시에만 `notified`) 변경 금지 |
| 4 | DB 스키마 변경 없음 |
| 5 | 스케줄러 고빈도 실행을 고려해 batch2/notify는 대상 0건 시 즉시 반환(불필요 LLM 호출·API 호출 없음)이어야 함 |
| 6 | `out/scheduler.py`는 참고 구현(직접 import 방식)으로만 활용, 메인 경로는 subprocess(main.py --batch) 유지 |

## 7. 테스트방법

| # | 시나리오 | 절차 | 기대 결과 |
|---|---|---|---|
| 1 | 잔량 발송 (주원인 검증) | DB에서 임의 1건을 `status='analyzed'`로 변경 → 신규 크롤링이 없는(saved=0) 시점에 scheduler `batch_job()` 1회 실행 | 텔레그램 수신, 해당 건 `status='notified'` 전이 |
| 2 | 수동/스케줄러 동등성 | 동일 DB 상태에서 `main.py --batch`와 scheduler 실행 로그의 `신규/요약/전송` 건수 비교 | 동일 값 |
| 3 | return 값 검증 | orchestrator `batch_job()` 반환값 `{"saved","done","ok"}` 확인 | None이 아닌 dict 반환 |
| 4 | 전송 실패 재시도 | config의 telegram token 임시 오기입 → 배치 실행 → token 복원 → 다음 배치 실행 | 1차: `analyzed` 유지·경고 / 2차: 정상 발송·`notified` 전이 |
| 5 | 실패 가시성 | batch1에서 강제 예외 발생시킨 뒤 scheduler 실행 | scheduler 로그에 WARNING/ERROR 기록 (returncode 0이어도 실패 노출) |
| 6 | 상태 정합성 | 테스트 전후 `SELECT status, COUNT(*) FROM research_report GROUP BY status` | `analyzed` 잔량이 배치 후 0건 (전송 실패 제외) |
| 7 | 요일 간격 | 월요일 시간대별 `get_interval()` 단위 확인 | 07:30~09:00=5분 등 평일 간격 정상 적용 |
