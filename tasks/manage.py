"""
JARVIS 작업 스케줄러 관리 도구.
 
tasks.yaml 정의를 읽어서 Windows 작업 스케줄러에 등록/해제/상태 조회.
 
사용법:
    python manage.py list                     # 모든 작업 상태
    python manage.py sync                     # yaml과 시스템 동기화
    python manage.py status <task-id>         # 특정 작업 상세
    python manage.py enable <task-id>         # yaml에서 enable + 동기화
    python manage.py disable <task-id>        # yaml에서 disable + 동기화
    python manage.py run <task-id>            # 즉시 한 번 실행 (테스트)
    python manage.py logs <task-id>           # 마지막 실행 로그 보기
 
작업 스케줄러 이름은 모두 'JARVIS-' 접두어로 시작 (충돌 방지).
관리자 권한 PowerShell에서 실행 필요 (sync, enable, disable 시).
"""
 
import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path
 
try:
    import yaml
except ImportError:
    print("[ERROR] PyYAML이 필요합니다. 설치: pip install pyyaml")
    sys.exit(1)
 
# ─────────────────────────────────────────────────────────────
# 경로 설정
# ─────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent
TASKS_FILE = ROOT / "tasks.yaml"
LOGS_DIR = ROOT.parent / "logs"
PYTHON_EXE = sys.executable  # 현재 사용 중인 python.exe
 
 
# ─────────────────────────────────────────────────────────────
# YAML 입출력
# ─────────────────────────────────────────────────────────────
 
def load_tasks() -> list[dict]:
    if not TASKS_FILE.exists():
        print(f"[ERROR] {TASKS_FILE} 없음.")
        return []
    data = yaml.safe_load(TASKS_FILE.read_text(encoding="utf-8")) or {}
    return data.get("tasks", [])
 
 
def save_tasks(tasks: list[dict]) -> None:
    data = {"tasks": tasks}
    TASKS_FILE.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8"
    )
 
 
# ─────────────────────────────────────────────────────────────
# Windows schtasks 래퍼
# ─────────────────────────────────────────────────────────────
 
def run_schtasks(args: list[str]) -> tuple[int, str, str]:
    """schtasks 명령 실행. returns (rc, stdout, stderr)."""
    result = subprocess.run(
        ["schtasks"] + args,
        capture_output=True,
        text=True,
        encoding="cp949",  # Windows 한글 출력 인코딩
        errors="replace",
    )
    return result.returncode, result.stdout, result.stderr
 
 
def task_exists(name: str) -> bool:
    rc, _, _ = run_schtasks(["/Query", "/TN", name])
    return rc == 0
 
 
def register_task(task: dict) -> bool:
    """작업 스케줄러에 등록."""
    name = task["name"]
    script_path = ROOT / task["script"]
    if not script_path.exists():
        print(f"[ERROR] 스크립트 없음: {script_path}")
        return False
 
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOGS_DIR / f"{task['id']}.log"
 
    # cmd.exe로 감싸서 로그 리다이렉트
    command = (
        f'cmd.exe /c "cd /d {script_path.parent} && '
        f'{PYTHON_EXE} -X utf8 {script_path} >> {log_file} 2>&1"'
    )
 
    schtasks_args = [
        "/Create", "/F",
        "/TN", name,
        "/TR", command,
        "/RL", "LIMITED",
    ]
 
    # 스케줄 방식 결정
    if "interval_minutes" in task:
        interval = task["interval_minutes"]
        schtasks_args += [
            "/SC", "MINUTE",
            "/MO", str(interval),
        ]
    elif "schedule" in task:
        schtasks_args += [
            "/SC", "DAILY",
            "/ST", task["schedule"],
        ]
    else:
        print(f"[ERROR] {name}: interval_minutes 또는 schedule 필요")
        return False
 
    rc, stdout, stderr = run_schtasks(schtasks_args)
    if rc == 0:
        print(f"  ✓ 등록: {name}")
        return True
    print(f"  ✗ 등록 실패: {name}")
    print(f"    {stderr or stdout}")
    return False
 
 
def unregister_task(name: str) -> bool:
    """작업 스케줄러에서 제거."""
    if not task_exists(name):
        return True
    rc, _, stderr = run_schtasks(["/Delete", "/TN", name, "/F"])
    if rc == 0:
        print(f"  ✓ 제거: {name}")
        return True
    print(f"  ✗ 제거 실패: {name}: {stderr}")
    return False
 
 
# ─────────────────────────────────────────────────────────────
# 명령어 핸들러
# ─────────────────────────────────────────────────────────────
 
def cmd_list(args):
    tasks = load_tasks()
    if not tasks:
        print("등록된 작업 없음. tasks.yaml 확인.")
        return
 
    print(f"{'ID':<20} {'이름':<25} {'YAML':<8} {'시스템':<10} {'주기':<15}")
    print("─" * 80)
 
    for task in tasks:
        tid = task["id"]
        name = task["name"]
        yaml_enabled = "ON" if task.get("enabled") else "OFF"
        sys_status = "등록됨" if task_exists(name) else "없음"
 
        if "interval_minutes" in task:
            sched = f"{task['interval_minutes']}분마다"
        elif "schedule" in task:
            sched = f"매일 {task['schedule']}"
        else:
            sched = "?"
 
        print(f"{tid:<20} {name:<25} {yaml_enabled:<8} {sys_status:<10} {sched:<15}")
 
    print()
    print("불일치 발견 시 `python manage.py sync` 실행.")
 
 
def cmd_sync(args):
    """YAML과 시스템 작업을 동기화."""
    tasks = load_tasks()
    if not tasks:
        print("[중단] tasks.yaml에 작업 없음.")
        return
 
    print("[동기화 시작]")
 
    for task in tasks:
        name = task["name"]
        should_be_enabled = task.get("enabled", False)
        is_registered = task_exists(name)
 
        if should_be_enabled and not is_registered:
            register_task(task)
        elif not should_be_enabled and is_registered:
            unregister_task(name)
        elif should_be_enabled and is_registered:
            # 이미 등록되어 있어도 정의가 바뀌었을 수 있으니 재등록
            unregister_task(name)
            register_task(task)
        else:
            print(f"  - 변동 없음: {name} (비활성)")
 
    print("[동기화 완료]")
    print()
    cmd_list(args)
 
 
def cmd_enable(args):
    tasks = load_tasks()
    found = False
    for task in tasks:
        if task["id"] == args.task_id:
            task["enabled"] = True
            found = True
            break
    if not found:
        print(f"[ERROR] 작업 ID 없음: {args.task_id}")
        return
    save_tasks(tasks)
    print(f"YAML 업데이트: {args.task_id} = enabled")
    cmd_sync(args)
 
 
def cmd_disable(args):
    tasks = load_tasks()
    found = False
    for task in tasks:
        if task["id"] == args.task_id:
            task["enabled"] = False
            found = True
            break
    if not found:
        print(f"[ERROR] 작업 ID 없음: {args.task_id}")
        return
    save_tasks(tasks)
    print(f"YAML 업데이트: {args.task_id} = disabled")
    cmd_sync(args)
 
 
def cmd_status(args):
    tasks = load_tasks()
    task = next((t for t in tasks if t["id"] == args.task_id), None)
    if not task:
        print(f"[ERROR] 작업 ID 없음: {args.task_id}")
        return
 
    name = task["name"]
    print(f"ID:           {task['id']}")
    print(f"이름:         {name}")
    print(f"설명:         {task.get('description', '-')}")
    print(f"스크립트:     {task['script']}")
    print(f"YAML 상태:    {'enabled' if task.get('enabled') else 'disabled'}")
    print(f"시스템 등록:  {'예' if task_exists(name) else '아니오'}")
 
    if task_exists(name):
        print("\n[작업 스케줄러 상세]")
        rc, stdout, _ = run_schtasks(["/Query", "/TN", name, "/V", "/FO", "LIST"])
        if rc == 0:
            for line in stdout.splitlines():
                if any(k in line for k in ["다음 실행 시간", "마지막 실행 시간", "마지막 결과", "상태", "Next Run Time", "Last Run Time", "Last Result", "Status"]):
                    print(f"  {line.strip()}")
 
    log_file = LOGS_DIR / f"{task['id']}.log"
    if log_file.exists():
        print(f"\n[로그 파일] {log_file}")
        size_kb = log_file.stat().st_size / 1024
        mtime = datetime.fromtimestamp(log_file.stat().st_mtime)
        print(f"  크기: {size_kb:.2f} KB")
        print(f"  마지막 수정: {mtime:%Y-%m-%d %H:%M:%S}")
 
 
def cmd_run(args):
    """즉시 한 번 실행 (테스트용)."""
    tasks = load_tasks()
    task = next((t for t in tasks if t["id"] == args.task_id), None)
    if not task:
        print(f"[ERROR] 작업 ID 없음: {args.task_id}")
        return
 
    script_path = ROOT / task["script"]
    if not script_path.exists():
        print(f"[ERROR] 스크립트 없음: {script_path}")
        return
 
    print(f"[실행] {PYTHON_EXE} {script_path}")
    print("─" * 60)
 
    result = subprocess.run([PYTHON_EXE, str(script_path)])
    print("─" * 60)
    print(f"종료 코드: {result.returncode}")
 
 
def cmd_logs(args):
    """마지막 실행 로그 보기."""
    tasks = load_tasks()
    task = next((t for t in tasks if t["id"] == args.task_id), None)
    if not task:
        print(f"[ERROR] 작업 ID 없음: {args.task_id}")
        return
 
    log_file = LOGS_DIR / f"{task['id']}.log"
    if not log_file.exists():
        print(f"로그 파일 없음: {log_file}")
        print("작업이 한 번도 실행되지 않았거나, 로그 경로가 다를 수 있습니다.")
        return
 
    print(f"[{log_file}] 마지막 50줄")
    print("─" * 60)
    lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in lines[-50:]:
        print(line)
 
 
# ─────────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────────
 
def main():
    parser = argparse.ArgumentParser(
        description="JARVIS 작업 스케줄러 관리",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
 
    sub.add_parser("list", help="모든 작업 상태")
    sub.add_parser("sync", help="YAML과 시스템 동기화")
 
    p = sub.add_parser("status", help="특정 작업 상세")
    p.add_argument("task_id", help="작업 ID (예: site-monitor)")
 
    p = sub.add_parser("enable", help="작업 활성화 + 동기화")
    p.add_argument("task_id")
 
    p = sub.add_parser("disable", help="작업 비활성화 + 동기화")
    p.add_argument("task_id")
 
    p = sub.add_parser("run", help="즉시 한 번 실행 (테스트)")
    p.add_argument("task_id")
 
    p = sub.add_parser("logs", help="마지막 실행 로그")
    p.add_argument("task_id")
 
    args = parser.parse_args()
    handlers = {
        "list": cmd_list,
        "sync": cmd_sync,
        "status": cmd_status,
        "enable": cmd_enable,
        "disable": cmd_disable,
        "run": cmd_run,
        "logs": cmd_logs,
    }
    handlers[args.cmd](args)
 
 
if __name__ == "__main__":
    main()