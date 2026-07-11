"""
orchestrator.py — 배치 오케스트레이터 (N-2)
batch1(크롤링) → batch2(요약) → 신규 텔레그램 알림 순차 실행
스케줄러(08:00, 18:00) 및 main.py --batch 가 호출
(구 modules/batch.py 의 통합 로직을 대체 — batch.py 는 삭제됨)
"""
import logging

from modules.batch1 import batch1_job
from modules.batch2 import batch2_job
from modules.notifier import notify_new_reports

logger = logging.getLogger(__name__)


def batch_job():
    """크롤링 → 요약 → 신규 알림 (순차)"""
    logger.info("=" * 50)
    logger.info("🚀 통합 배치 시작 (batch1 → batch2 → 알림)")
    ok =True
    try:
        # 1. 크롤링 (신규만 collected로 저장)
        r1 = batch1_job()
        if r1["saved"] == 0:
            logger.info("신규 리포트 없음 — 요약·알림 생략")
            return {"saved": 0, "done": 0}

        # 2. 신규 요약 (collected → analyzed), done_ids 반환
        try:
           r2 = batch2_job()
        except Exception as e:
           logger.error(f"요약 단계에서 예외 발생: {str(e)}")
           ok = False

        # 3. 이번에 요약된 신규를 텔레그램으로 push (신규 0이면 내부에서 조용히 종료)
       
        try:
           notify_new_reports(r2.get("done_ids", []))
        except Exception as e:
           logger.error(f"알림 단계에서 예외 발생: {str(e)}")
           ok = False
        logger.info(f"🏁 통합 배치 완료: 신규 {r1['saved']} / 요약 {r2.get('done', 0)} / 전송 {'성공' if ok else '실패'}")
    except Exception as e:
        logger.error(f"배치 작업 중 예외 발생: {str(e)}")
        ok = False

