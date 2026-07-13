"""
orchestrator.py — 배치 오케스트레이터 (N-2)
batch1(크롤링) → batch2(요약) → 신규 텔레그램 알림 순차 실행
스케줄러(08:00, 18:00) 및 main.py --batch 가 호출
(구 modules/batch.py 의 통합 로직을 대체 — batch.py 는 삭제됨)
"""
import logging

from modules.batch1 import batch1_job
from modules.batch2 import batch2_job
from modules.notifier import notify_new_reports, send_admin_alert  # 수정: send_admin_alert 추가

logger = logging.getLogger(__name__)


def batch_job():
    """크롤링 → 요약 → 신규 알림 (순차)"""
    logger.info("=" * 50)
    logger.info("🚀 통합 배치 시작 (batch1 → batch2 → 알림)")
    ok = True

    # 저장·요약 기본값 (예외 시에도 안전하게 dict 구성)
    saved = 0
    done = 0
    sent = 0

    # 1. 크롤링 (신규만 collected로 저장) — 개별 try (크롤링 실패가 알림 단계까지 죽이지 않도록)
    # 삭제: if r1["saved"] == 0: 조기 반환 — saved==0이어도 batch2/notify 항상 실행 (잔량 analyzed 발송·재시도 보장)
    r1 = None
    try:
        r1 = batch1_job()
        saved = r1.get("saved", 0)
    except Exception as e:
        logger.error(f"크롤링(batch1) 단계에서 예외 발생: {str(e)}")
        ok = False

    # 2. 신규 요약 (collected → analyzed), done_ids 반환
    r2 = None
    try:
        r2 = batch2_job()
        done = r2.get("done", 0) if r2 else 0
    except Exception as e:
        logger.error(f"요약(batch2) 단계에서 예외 발생: {str(e)}")
        ok = False

    # 3. 텔레그램 push — analyzed 잔량까지 일괄 전송 (신규 0이면 내부에서 조용히 종료)
    try:
        sent = notify_new_reports(r2.get("done_ids", []) if r2 else [])
    except Exception as e:
        logger.error(f"알림(notify) 단계에서 예외 발생: {str(e)}")
        ok = False

    # 추가: ok 판정 보강 — 예외뿐 아니라 실질 실패도 실패로 처리
    fail_reason = None
    if not ok:
        fail_reason = "배치 단계 예외 발생"
    elif r2 and r2.get("llm_down"):                         # ① LLM 다운
        ok = False
        fail_reason = f"LLM 서버 다운 (요약 대상 {r2.get('failed', 0)}건 미처리)"
    elif r2 and r2.get("failed", 0) > 0:                    # ② 요약 실패 존재
        ok = False
        fail_reason = f"요약 실패 {r2['failed']}건"
    elif saved > 0 and done == 0:                           # ③ 신규>0인데 요약 0
        ok = False
        fail_reason = f"신규 {saved}건 수집됐으나 요약 0건"

    # 추가: 실패 확정 시 관리자 텔레그램 알림 (알림 실패가 배치를 죽이지 않도록 try)
    if not ok and fail_reason:
        try:
            send_admin_alert(fail_reason)
        except Exception as e:
            logger.error(f"관리자 알림 발송 실패: {str(e)}")

    logger.info(
        f"🏁 통합 배치 완료: 신규 {saved} / 요약 {done} / 전송 {sent} / {'성공' if ok else '실패'}"
    )
    return {"saved": saved, "done": done, "sent": sent, "ok": ok}  # 추가: sent 키

