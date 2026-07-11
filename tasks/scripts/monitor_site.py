"""
사이트 가동 모니터링 + 텔레그램 알림.

설계:
- 표준 HTTPS는 requests로 빠르게 처리.
- 한국 정부 사이트 등 OpenSSL이 막히는 레거시 SSL 사이트는
  Windows curl(Schannel)로 우회. Chrome과 같은 호환성.
- Windows 작업 스케줄러로 1분(혹은 N분)마다 실행.
- 상태 변화 시만 텔레그램 알림 (다운/복구), 연속 다운 시 10분마다 재알림.

환경변수:
    TELEGRAM_TOKEN     - 봇 토큰
    TELEGRAM_CHAT_ID   - 알림 받을 chat ID

사이트별 옵션:
    name        : 알림에 표시되는 이름
    url         : 모니터링 URL
    timeout     : 요청 타임아웃 초 (기본 10)
    use_curl    : Windows curl로 호출 (True = 한국 정부 사이트 등 레거시 SSL)
                  미지정 또는 False = Python requests 사용 (빠름, 표준 SSL)
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests

# ─────────────────────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────────────────────

SITES = [
    {
        "name": "역사박물관",
        "url": "https://museum.seoul.go.kr/www/NR_index.do",
        #"url":"http://218.146.11.102:8080/",
        "timeout": 10,
        "use_curl": True,       # 한국 정부 사이트 - curl(Schannel)로 우회
    },
    {
        "name": "보호나라",
        "url": "https://www.boho.or.kr/",   # 실제 URL로 수정
        "timeout": 10,
        # use_curl 미지정 = requests 사용 (표준 SSL)
    },
    {
        "name": "우리소리박물관",
        "url": "https://museum.seoul.go.kr/sekm/front/main.do?locale=KO",   # 실제 URL로 수정
        "timeout": 10,
        # use_curl 미지정 = requests 사용 (표준 SSL)
    },
]

REQUEST_TIMEOUT = 10
REPEAT_ALERT_AFTER = timedelta(seconds=30)
#STATE_FILE = Path(__file__).parent / "data" / "monitor_state.json"
STATE_FILE = Path(__file__).resolve().parent / "data" / "monitor_state.json"

print(f"[INFO] STATE_FILE = {STATE_FILE}")
print(f"[INFO] STATE_FILE exists = {STATE_FILE.exists()}")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
#print("TELEGRAM_TOKEN==",TELEGRAM_TOKEN)

# ─────────────────────────────────────────────────────────────
# 텔레그램
# ─────────────────────────────────────────────────────────────

def send_telegram(message: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[ERROR] TELEGRAM_TOKEN/CHAT_ID 환경변수 없음. 메시지: {message}")
        return False
        
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            print(f"[OK] 텔레그램 전송: {message[:60]}...")
            return True
        print(f"[FAIL] 텔레그램 응답 {r.status_code}: {r.text[:200]}")
        return False
    except requests.exceptions.RequestException as e:
        print(f"[FAIL] 텔레그램 예외: {e}")
        return False


# ─────────────────────────────────────────────────────────────
# 상태 저장/로드
# ─────────────────────────────────────────────────────────────

def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"[WARN] 상태 파일 로드 실패, 새로 시작: {e}")
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


# ─────────────────────────────────────────────────────────────
# 사이트 체크 - curl (한국 정부 사이트 등)
# ─────────────────────────────────────────────────────────────

def check_via_curl(url: str, timeout: int) -> tuple[bool, str]:
    """
    Windows curl로 사이트 체크. Schannel을 써서 Chrome과 같은 호환성.
    -o NUL: 본문 버림 (속도 향상)
    -s: silent (진행 표시 제거)
    -k: SSL 검증 우회 (Schannel 자체가 더 관대하지만 혹시 모를 인증서 문제 대비)
    -L: redirect 따라감
    -w "%{http_code}|%{time_total}": HTTP 상태 코드와 응답 시간 출력
    --max-time: 전체 타임아웃
    """
    try:
        result = subprocess.run(
            [
                "curl",
                "-o", "NUL",
                "-s",
                "-k",
                "-L",
                "-w", "%{http_code}|%{time_total}",
                "--max-time", str(timeout),
                url,
            ],
            capture_output=True,
            text=True,
            timeout=timeout + 5,
        )

        if result.returncode != 0:
            # curl 실패 (네트워크 문제 등)
            stderr_brief = result.stderr.strip()[:100] if result.stderr else "no stderr"
            return False, f"curl 실패 (exit {result.returncode}): {stderr_brief}"

        # 출력 파싱: "200|0.234567"
        output = result.stdout.strip()
        match = re.match(r"(\d+)\|([\d.]+)", output)
        if not match:
            return False, f"curl 응답 파싱 실패: {output[:50]}"

        status = int(match.group(1))
        elapsed = float(match.group(2))

        if status == 200:
            return True, f"HTTP 200 ({elapsed:.2f}s, via curl)"
        return False, f"HTTP {status} (via curl)"

    except subprocess.TimeoutExpired:
        return False, f"curl 타임아웃 ({timeout}s)"
    except FileNotFoundError:
        return False, "curl 명령 없음 (Windows 10/11 기본 탑재이어야 함)"
    except Exception as e:
        return False, f"curl 예외: {type(e).__name__}: {e}"


# ─────────────────────────────────────────────────────────────
# 사이트 체크 - requests (표준 SSL 사이트)
# ─────────────────────────────────────────────────────────────

def check_via_requests(url: str, timeout: int) -> tuple[bool, str]:
    try:
        r = requests.get(url, timeout=timeout)
        if r.status_code == 200:
            return True, f"HTTP 200 ({r.elapsed.total_seconds():.2f}s)"
        return False, f"HTTP {r.status_code}"
    except requests.exceptions.Timeout:
        return False, f"타임아웃 ({timeout}s)"
    except requests.exceptions.SSLError as e:
        return False, f"SSL 오류: {str(e)[:80]}"
    except requests.exceptions.ConnectionError as e:
        return False, f"연결 실패: {type(e).__name__}"
    except requests.exceptions.RequestException as e:
        return False, f"요청 예외: {type(e).__name__}"


# ─────────────────────────────────────────────────────────────
# 사이트 체크 - 라우팅
# ─────────────────────────────────────────────────────────────

def check_site(site: dict) -> tuple[bool, str]:
    """Returns: (is_up, detail_message)"""
    url = site["url"]
    timeout = site.get("timeout", REQUEST_TIMEOUT)

    if site.get("use_curl", False):
        return check_via_curl(url, timeout)
    return check_via_requests(url, timeout)


# ─────────────────────────────────────────────────────────────
# 메인 로직
# ─────────────────────────────────────────────────────────────

def main() -> int:
    now = datetime.now()
    state = load_state()
    print(f"[DEBUG] state file: {STATE_FILE}")
    for site in SITES:
        name = site["name"]
        url = site["url"]
        is_up, detail = check_site(site)

        prev = state.get(name, {})
        prev_up = prev.get("up")
        prev_last_alert = prev.get("last_alert_at")
        prev_down_since = prev.get("down_since")

        if is_up:
            print(f"[UP] {name}: {detail}")

            if prev_up is False:
                duration = ""
                if prev_down_since:
                    delta = now - datetime.fromisoformat(prev_down_since)
                    minutes = int(delta.total_seconds() / 60)
                    duration = f" (다운 지속: {minutes}분)"
                send_telegram(
                    f"✅ <b>복구</b>\n"
                    f"사이트: {name}\n"
                    f"URL: {url}\n"
                    f"상태: {detail}{duration}\n"
                    f"시각: {now:%Y-%m-%d %H:%M:%S}"
                )

            state[name] = {
                "up": True,
                "last_check_at": now.isoformat(),
                "last_detail": detail,
            }
           
        else:
            print(f"[DOWN] {name}: {detail}")

            should_alert = False
            if prev_up is not False:
                should_alert = True
                down_since = now.isoformat()
            else:
                down_since = prev_down_since or now.isoformat()
                if prev_last_alert:
                    last_alert_dt = datetime.fromisoformat(prev_last_alert)
                    if now - last_alert_dt >= REPEAT_ALERT_AFTER:
                        should_alert = True
                else:
                    should_alert = True

            if should_alert:
                delta = now - datetime.fromisoformat(down_since)
                minutes = int(delta.total_seconds() / 60)
                duration_str = f"\n다운 지속: {minutes}분" if minutes > 0 else ""
                send_telegram(
                    f"🚨 <b>다운 감지</b>\n"
                    f"사이트: {name}\n"
                    f"URL: {url}\n"
                    f"원인: {detail}{duration_str}\n"
                    f"시각: {now:%Y-%m-%d %H:%M:%S}"
                )
                last_alert_at = now.isoformat()
            else:
                last_alert_at = prev_last_alert

            state[name] = {
                "up": False,
                "last_check_at": now.isoformat(),
                "last_detail": detail,
                "down_since": down_since,
                "last_alert_at": last_alert_at,
            }

    save_state(state)
    print(f"[DEBUG] saved state: {state}")   # ← 추가
    return 0


if __name__ == "__main__":
    sys.exit(main())