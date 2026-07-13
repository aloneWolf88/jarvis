import logging
import os                                                       # 추가
import subprocess                                               # 추가: main.py --batch 호출
import sys                                                      # 추가
import time                                                     # 추가: 스케줄 루프용
from datetime import datetime                                   # 추가

BASE_DIR = os.path.dirname(os.path.abspath(__file__))            # 추가
RESEARCH_BOT_DIR = os.path.join(BASE_DIR, "bots", "research")    # 수정: research_bot → bots/research
LOG_DIR = os.path.join(BASE_DIR, "logs")                         # 추가
os.makedirs(LOG_DIR, exist_ok=True)                              # 추가

# 추가: 로깅 설정 (없으면 INFO 로그가 출력되지 않음)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),                        # 콘솔 출력
        logging.FileHandler(os.path.join(LOG_DIR, "scheduler.log"), encoding="utf-8"),  # 파일 기록
    ],
)

logger = logging.getLogger(__name__)


def batch_job():
    """main.py --batch 기준으로 크롤링 → 요약 → 신규 알림 실행"""
    logger.info("=" * 50)
    logger.info("🚀 스케줄러 배치 시작 (main.py --batch 호출)")

    # 추가: 결과값 추출용 기본값
    saved = done = sent = None

    try:
        result = subprocess.run(
            [sys.executable, "main.py", "--batch"],
            cwd=RESEARCH_BOT_DIR,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1800,
            check=False,
        )

        stdout = result.stdout or ""
        stderr = result.stderr or ""

        # 수정: stdout 전체를 로그로 남기되, 완료 요약 라인을 별도 INFO로 명시 기록 (수동 실행과 결과 비교)
        if stdout:
            logger.info(stdout.rstrip())

        # 추가: "🏁 통합 배치 완료: 신규 N / 요약 M / 전송 K ..." 라인 추출하여 별도 기록
        for line in stdout.splitlines():
            if "통합 배치 완료" in line:
                logger.info(f"[결과 요약] {line.strip()}")
                # 추가: saved / done / sent 파싱 (정규식 미사용, 단순 분할)
                try:
                    saved = int(line.split("신규")[1].split("/")[0].strip())
                    done = int(line.split("요약")[1].split("/")[0].strip())
                    sent = int(line.split("전송")[1].split("/")[0].strip())
                except (IndexError, ValueError):
                    logger.warning("결과 요약 라인 파싱 실패 (무시)")
                break

        # 수정: stderr와 stdout 내 ERROR/ok=False 표시를 WARNING으로 승격 기록
        has_failure_marker = "ok=False" in stdout or "실패" in stdout
        if stderr:
            logger.warning(f"[stderr] {stderr.rstrip()}")
        if has_failure_marker:
            logger.warning("[실패 표시 감지] orchestrator ok=False 또는 실패 마커 존재 (returncode와 무관)")

        ok = result.returncode == 0 and not has_failure_marker
        logger.info(
            f"🏁 스케줄러 배치 완료: {'성공' if ok else f'실패(returncode={result.returncode})'} "
            f"— 신규 {saved} / 요약 {done} / 전송 {sent}"
        )
        return {"ok": ok, "returncode": result.returncode,
                "saved": saved, "done": done, "sent": sent}
    except subprocess.TimeoutExpired as e:
        logger.error(f"배치 실행 시간 초과: {e}")
        return {"ok": False, "returncode": None,
                "saved": saved, "done": done, "sent": sent}
    except Exception as e:
        logger.error(f"배치 실행 중 예외 발생: {e}")
        return {"ok": False, "returncode": None,
                "saved": saved, "done": done, "sent": sent}


# 추가: 시간대별 실행 간격(분) 반환
def get_interval():
    now = datetime.now()
    hm = now.hour * 60 + now.minute
    if 0 <= now.weekday() <= 4:            # 평일
        if 7*60+30 <= hm < 9*60:  return 5
        if 9*60    <= hm < 16*60: return 10
        if 16*60   <= hm < 18*60: return 15
    return 45


# 추가: 실행부 (스케줄 루프)
def main():
    logger.info("스케줄러 시작 — 즉시 1회 실행")
    batch_job()
    last_interval = get_interval()                                 # 추가: 즉시 실행 후 다음 간격 기준 설정
    next_run = time.time() + (last_interval * 60)                  # 수정: 시작 직후 중복 실행 방지
    logger.info(f"실행 간격 변경: {last_interval}분")              # 추가
    logger.info(f"다음 실행까지 {last_interval}분 대기")           # 추가
    while True:
        try:
            interval = get_interval()
            if interval != last_interval:
                last_interval = interval
                logger.info(f"실행 간격 변경: {interval}분")
            if time.time() >= next_run:
                batch_job()
                next_run = time.time() + (interval * 60)
                logger.info(f"다음 실행까지 {interval}분 대기")
            time.sleep(30)
        except KeyboardInterrupt:
            logger.info("스케줄러 종료(사용자 중단)")
            break
        except Exception as e:
            logger.error(f"메인 루프 오류: {e}")
            time.sleep(30)


if __name__ == "__main__":
    main()
