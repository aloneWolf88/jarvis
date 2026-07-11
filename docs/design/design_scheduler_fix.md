# design_scheduler_fix.md — scheduler ↔ main --batch 결과 불일치(텔레그램 미수신) 수정 설계

- 작성일: 2026-07-09
- 요구사항: `main.py --batch` 실행 결과와 `scheduler.py` 실행 결과가 동일할 것 (텔레그램 알림 포함)
- 제약: 기존 핸들러(main.py의 cmd_* / argparse 분기) 변경 금지, 코드 구현 없음(설계만)

---

## 1. 문제정의

| 항목 | 내용 |
|---|---|
| 증상 | `main.py --batch` 수동 실행 시 텔레그램 수신 정상. `scheduler.py` 실행 시 오류 없이 "성공" 로그가 남지만 텔레그램이 오지 않음 |
| 기대 동작 | 실행 경로(수동/스케줄러)와 무관하게 동일 파이프라인(크롤링→요약→알림)이 동일 결과 산출 |
| 영향 범위 | 스케줄러 상주 운영 시 신규 리포트 알림 누락, 미전송(analyzed) 잔량 영구 미발송 |

## 2. 원인분석

두 실행 경로 모두 `modules/orchestrator.batch_job()`을 타므로 코드 경로 자체는 동일하다.
차이는 **실행 빈도와 예외 가시성**에서 발생한다.

| # | 원인 | 위치 | 설명 | 심각도 |
|---|---|---|---|---|
| 1 | **saved==0 조기 반환** | orchestrator.py `batch_job()` | `batch1` 신규 저장이 0건이면 `batch2`(요약)와 `notify_new_reports`(알림)를 모두 건너뜀. 스케줄러는 5~45분 간격 고빈도 실행이라 대부분의 주기에서 `saved=0` → 알림 단계 도달 자체가 드묾. 이전 주기에서 `collected`/`analyzed`로 남은 잔량도 신규 0건이면 영원히 요약·발송되지 않음. 수동 `--batch`는 마지막 실행 이후 신규가 쌓인 뒤 돌리므로 `saved>0` → 알림 정상 | 높음(주원인) |
| 2 | **재시도 설계 무력화** | orchestrator.py + notifier.py | notifier는 전송 실패 시 `analyzed` 상태를 유지해 "다음 배치에서 재시도"하는 설계(B안)인데, 원인 1의 조기 반환 때문에 다음 배치에서 notify가 호출되지 않아 재시도가 실제로 동작하지 않음 | 높음 |
| 3 | **예외 삼킴 → exit 0** | orchestrator.py `batch_job()` except 절 | 크롤링/요약/알림 중 어떤 예외가 나도 dict를 정상 반환 → main.py가 exit 0 종료 → scheduler.py는 return code 0만 보고 "배치 작업이 성공적으로 완료되었습니다" 기록. 실패가 성공으로 보임("오류는 발생하지 않으나"의 정체) | 중간 |
| 4 | **성공 시 stdout 미기록** | scheduler.py `run_batch()` | return code 0이면 캡처한 stdout을 버림 → 스케줄러 실행분의 "결과값"(신규/요약/전송 건수)을 확인·비교할 수단이 없음 | 중간 |
| 5 | (부수) **요일 판정 오류** | scheduler.py `get_interval()` | `weekday()`는 월=0~일=6인데 `1 <= weekday() <= 5` 조건은 화~토를 평일로 취급. 월요일이 45분 간격으로 동작 | 낮음 |

참고: DB 경로(`db.py DB_PATH`)와 config 경로(notifier `_ROOT`)는 모두 `__file__` 기준 절대경로라 cwd 차이로 인한 불일치는 없음(확인 완료).

## 3. 대상파일

| 파일 | 변경 여부 | 비고 |
|---|---|---|
| `research_bot/modules/orchestrator.py` | 수정 | 원인 1, 2, 3 해소 |
| `scheduler.py` | 수정 | 원인 4, 5 해소 |
| `research_bot/main.py` | **변경 없음** | 기존 핸들러 변경 금지 제약 준수 |
| `research_bot/modules/notifier.py` | 변경 없음 | 미전송(analyzed) 일괄 push + 실패 시 유지 로직 그대로 활용 |
| `research_bot/modules/batch1.py`, `batch2.py` | 변경 없음 | — |

## 4. 함수명세

### 4.1 orchestrator.py — `batch_job()` (수정)

| 항목 | 내용 |
|---|---|
| 시그니처 | `batch_job() -> dict` (변경 없음) |
| 반환 | `{"saved": int, "done": int, "ok": bool}` — 기존 키 유지, `ok`(성공 여부) 추가 |
| 변경점 1 | `saved == 0` 조기 반환 **제거**. batch1 결과와 무관하게 batch2·notify를 항상 실행 (batch2는 `collected` 잔량, notifier는 `analyzed` 잔량을 스스로 조회하므로 대상 없으면 조용히 종료 — 추가 비용 미미) |
| 변경점 2 | 예외 발생 시 `ok=False`로 반환하고 각 단계(batch1/batch2/notify)를 개별 try로 분리 — 크롤링 실패가 알림 단계까지 죽이지 않도록 |
| 변경점 3 | 완료 로그에 전송 건수 포함: `신규 N / 요약 M / 전송 K` |
| 호출자 영향 | main.py `--batch`, `--scheduler`(cmd_scheduler), scheduler.py 모두 시그니처 동일 → 무수정 호환 |

### 4.2 scheduler.py — `run_batch()` (수정)

| 항목 | 내용 |
|---|---|
| 시그니처 | `run_batch() -> None` (변경 없음) |
| 변경점 1 | return code 0이어도 stdout 마지막 요약 라인(통합 배치 완료 로그)을 INFO로 기록 → 수동 실행과 결과값 비교 가능 |
| 변경점 2 | stdout에 실패 지표(예: `ok=False`, ERROR 로그) 포함 시 WARNING으로 승격 기록 |

### 4.3 scheduler.py — `get_interval()` (수정)

| 항목 | 내용 |
|---|---|
| 변경점 | 평일 조건을 `0 <= weekday() <= 4` (월~금)로 정정 |

## 5. 해결책

| 단계 | 내용 | 해소 원인 |
|---|---|---|
| 1 | `batch_job()`에서 조기 반환 제거 → 매 실행마다 "collected 요약 → analyzed 발송"이 보장되어 수동/스케줄러 결과 수렴. 전송 실패분도 다음 주기에 자동 재발송(재시도 설계 복원) | 1, 2 |
| 2 | 단계별 예외 분리 + `ok` 플래그 반환 + 로그 강화 → 실패가 "성공"으로 위장되지 않음 | 3 |
| 3 | scheduler.py 성공 시에도 결과 요약 로그 기록 | 4 |
| 4 | 요일 조건 정정 | 5 |

설계 원칙: 알림 대상 판정은 호출 경로가 아니라 **DB status 상태 기계(collected → analyzed → notified)** 단일 기준으로 통일. 실행 주체(수동/스케줄러)는 트리거일 뿐 결과에 영향을 주지 않는다.

## 6. 제약조건

| # | 제약 |
|---|---|
| 1 | main.py의 기존 핸들러(cmd_*, argparse 분기) 변경 금지 |
| 2 | `batch_job()` 시그니처·기존 반환 키(`saved`, `done`) 유지 — 호출부 무수정 호환 |
| 3 | notifier의 상태 전이 규칙(전송 성공 시에만 `notified`) 변경 금지 |
| 4 | DB 스키마 변경 없음 |
| 5 | 스케줄러 고빈도 실행을 고려해 batch2/notify는 대상 0건 시 즉시 반환(불필요 LLM 호출·API 호출 없음)이어야 함 |

## 7. 테스트방법

| # | 시나리오 | 절차 | 기대 결과 |
|---|---|---|---|
| 1 | 잔량 발송 (주원인 검증) | DB에서 임의 1건을 `status='analyzed'`로 변경 → 신규 크롤링이 없는 시점에 scheduler `run_batch()` 1회 실행 | 텔레그램 수신, 해당 건 `status='notified'` 전이 |
| 2 | 수동/스케줄러 동등성 | 동일 DB 상태에서 `main.py --batch`와 scheduler 실행 로그의 `신규/요약/전송` 건수 비교 | 동일 값 |
| 3 | 전송 실패 재시도 | config의 telegram token을 임시 오기입 → 배치 실행 → token 복원 → 다음 배치 실행 | 1차: `analyzed` 유지·경고 로그 / 2차: 정상 발송·`notified` 전이 |
| 4 | 실패 가시성 | batch1에서 강제 예외 발생시킨 뒤 scheduler 실행 | scheduler 로그에 WARNING/ERROR 기록 (성공으로 위장되지 않음) |
| 5 | 상태 정합성 | 테스트 전후 `SELECT status, COUNT(*) FROM research_report GROUP BY status` | `analyzed` 잔량이 배치 후 0건 (전송 실패 제외) |
| 6 | 요일 간격 | 월요일 시간대별 `get_interval()` 단위 확인 | 07:30~09:00=5분 등 평일 간격 적용 |
