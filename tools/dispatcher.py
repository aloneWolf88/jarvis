# -*- coding: utf-8 -*-
"""
dispatcher.py — AI 작업 오케스트레이터 (museum / 범용)
  프롬프트 조립 → Ollama 호출 → diff 게이트(화이트리스트) → 적용(백업) → 검증 → 상태 전이
  표준 라이브러리만 사용. ROOT는 이 파일 위치(tools/) 기준 자동 계산.

사용법:
  python tools/dispatcher.py run M-0001 --dry   # diff 확인만 (첫 실행 권장)
  python tools/dispatcher.py run M-0001         # 게이트 통과 시 적용+검증
  python tools/dispatcher.py status M-0001

상태 머신: pending → generated → gated → applied → verified / blocked / failed
"""
import argparse
import datetime
import fnmatch
import json
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AI_DIR = ROOT / ".ai"
OLLAMA_URL = "http://localhost:11434/api/generate"
AUTO_RUN_RE = re.compile(r"^(pytest|python|sqlite3|javac|java|ant)\b")

# ---------------------------------------------------------------- util

def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def die(msg, code=1):
    print("[dispatcher] ERROR: " + msg)
    sys.exit(code)


def load_task(task_id):
    p = AI_DIR / "tasks" / (task_id + ".json")
    if not p.exists():
        die("task 파일 없음: " + str(p))
    return json.loads(p.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- state machine

STATES = ["pending", "generated", "gated", "applied", "verified", "blocked", "failed"]


def state_path(task_id):
    return AI_DIR / "state" / (task_id + ".state.json")


def load_state(task_id):
    p = state_path(task_id)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"id": task_id, "status": "pending", "attempts": 0, "history": []}


def transition(state, new_status, note=""):
    state["history"].append({"time": now(), "from": state["status"],
                             "to": new_status, "note": note})
    state["status"] = new_status
    p = state_path(state["id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[state] %s -> %s  %s" % (state["history"][-1]["from"], new_status, note))
    return state


# ---------------------------------------------------------------- prompt

def parse_frontmatter(md_text):
    """coder.md 상단 YAML frontmatter 파싱 (key: value 단순형)."""
    meta, body = {}, md_text
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", md_text, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        body = m.group(2)
    return meta, body


def build_prompt(task, feedback=None):
    conduct = (AI_DIR / "conduct.md").read_text(encoding="utf-8")
    meta, coder_body = parse_frontmatter(
        (AI_DIR / "agents" / "coder.md").read_text(encoding="utf-8"))

    design_ref = ROOT / task.get("design_ref", "")
    design = design_ref.read_text(encoding="utf-8") if design_ref.exists() else "(design.md 없음)"

    parts = [
        "### COMMON CONDUCT (must follow)\n" + conduct,
        "### CODER RULES\n" + coder_body,
        "### TASK CONTRACT (task.json)\n" + json.dumps(task, ensure_ascii=False, indent=2),
        "### DESIGN DOCUMENT\n" + design,
    ]
    for rel in task.get("context_files", []):
        f = ROOT / rel
        if f.exists():
            parts.append("### CONTEXT FILE: %s\n```\n%s\n```" % (rel, f.read_text(encoding="utf-8", errors="replace")))
        else:
            parts.append("### CONTEXT FILE: %s (NOT FOUND)" % rel)
    if feedback:
        parts.append("### PREVIOUS ATTEMPT FEEDBACK (fix this)\n" + feedback)
    parts.append("### OUTPUT\nOutput ONLY the unified diff now (or BLOCKED: <reason>).")
    return meta, "\n\n".join(parts)


# ---------------------------------------------------------------- ollama

def call_ollama(meta, prompt):
    payload = {
        "model": meta.get("model", "qwen2.5-coder:14b"),
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": float(meta.get("temperature", 0.2)),
            "num_ctx": int(meta.get("num_ctx", 16384)),
        },
    }
    req = urllib.request.Request(
        OLLAMA_URL, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            out = json.loads(r.read().decode("utf-8"))["response"]
    except Exception as e:
        die("Ollama 호출 실패 (기동 확인: http://localhost:11434): %s" % e)
    out = re.sub(r"<think>.*?</think>", "", out, flags=re.S)  # 추론 태그 제거
    return out.strip()


def extract_diff(text):
    """마크다운 펜스 제거 후 diff 본문만 추출."""
    m = re.search(r"```(?:diff)?\s*\n(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if l.startswith("--- ")), None)
    if start is None:
        return None
    return "\n".join(lines[start:]).strip() + "\n"


# ---------------------------------------------------------------- gate (fail-close)

def norm(p):
    p = re.sub(r"^[ab]/", "", p.strip())
    return p.replace("\\", "/")


def diff_target_files(diff_text):
    return sorted({norm(l[4:]) for l in diff_text.splitlines()
                   if l.startswith("+++ ") and norm(l[4:]) != "/dev/null"})


def gate_check(diff_text, task):
    """화이트리스트 게이트. 오류 시에도 거부(fail-close). (거부사유목록, 대상파일) 반환."""
    reasons = []
    try:
        targets = diff_target_files(diff_text)
        if not targets:
            return ["diff에서 대상 파일을 찾지 못함"], []
        allowed = [norm(a) for a in task.get("allowed_files", [])]
        forbidden = [norm(f) for f in task.get("forbidden", [])]
        for t in targets:
            if not any(t == a or fnmatch.fnmatch(t, a) for a in allowed):
                reasons.append("allowed_files 밖: " + t)
            if any(fnmatch.fnmatch(t, f) for f in forbidden):
                reasons.append("forbidden 패턴 위반: " + t)
        return reasons, targets
    except Exception as e:
        return ["게이트 내부 오류(fail-close): %s" % e], []


# ---------------------------------------------------------------- apply (pure python)

def parse_hunks(diff_text):
    """{file: [(old_start, [hunk lines]), ...]} 반환."""
    files, cur, hunk = {}, None, None
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            cur = norm(line[4:])
            files[cur] = []
        elif line.startswith("--- "):
            continue
        elif line.startswith("@@"):
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@", line)
            if not (m and cur):
                raise ValueError("hunk 헤더 파싱 실패: " + line)
            hunk = (int(m.group(1)), [])
            files[cur].append(hunk)
        elif hunk is not None and line[:1] in (" ", "+", "-"):
            hunk[1].append(line)
    return files


def _indent(s):  # [추가] 선행 공백 길이
    return len(s) - len(s.lstrip())


def apply_unified_diff(diff_text, task_id):
    """전 파일 메모리에서 적용 성공 시에만 디스크 기록. 컨텍스트 불일치 → 전체 미적용.
    [추가] 로컬 LLM이 들여쓰기를 바꿔 출력하는 경우: 공백 무시 매칭 후 원본 들여쓰기로 보정."""
    files = parse_hunks(diff_text)
    new_contents = {}
    for rel, hunks in files.items():
        path = ROOT / rel
        if not path.exists():
            raise ValueError("대상 파일 없음: " + rel)
        original = path.read_text(encoding="utf-8", errors="replace").splitlines()
        lines, offset = list(original), 0
        for old_start, body in hunks:
            idx = old_start - 1 + offset
            out = []
            indent_delta = None  # [추가] diff↔원본 들여쓰기 차이 (hunk 단위 보정값)
            for b in body:
                tag, content = b[0], b[1:]
                if tag == " ":
                    if idx < len(lines) and lines[idx] == content:
                        out.append(content); idx += 1
                    # [추가] 공백 무시 재매칭 — 내용 일치 시 원본 라인 유지 + 보정값 기록
                    elif idx < len(lines) and content.strip() and lines[idx].strip() == content.strip():
                        if indent_delta is None:
                            indent_delta = _indent(lines[idx]) - _indent(content)
                        out.append(lines[idx]); idx += 1
                    else:
                        raise ValueError("컨텍스트 불일치 %s:%d\n  기대: %r\n  실제: %r"
                                         % (rel, idx + 1, content,
                                            lines[idx] if idx < len(lines) else "<EOF>"))
                elif tag == "-":
                    # [추가] 삭제 라인도 공백 무시 매칭 허용
                    if idx >= len(lines) or lines[idx].strip() != content.strip():
                        raise ValueError("삭제 라인 불일치 %s:%d" % (rel, idx + 1))
                    idx += 1
                elif tag == "+":
                    # [추가] 추가 라인에 들여쓰기 보정 적용
                    if indent_delta and content.strip():
                        content = " " * max(0, _indent(content) + indent_delta) + content.lstrip()
                    out.append(content)
            start = old_start - 1 + offset
            removed = idx - start
            lines[start:idx] = out
            offset += len(out) - removed
        new_contents[rel] = "\n".join(lines) + "\n"

    # 전부 성공 → 백업 후 기록
    backup_dir = AI_DIR / "backup" / ("%s-%s" % (task_id, datetime.datetime.now().strftime("%Y%m%d-%H%M%S")))
    backup_dir.mkdir(parents=True, exist_ok=True)
    for rel in new_contents:
        src = ROOT / rel
        dst = backup_dir / rel.replace("/", "__")
        shutil.copy2(str(src), str(dst))
    for rel, content in new_contents.items():
        (ROOT / rel).write_text(content, encoding="utf-8")
    print("[apply] %d개 파일 적용, 백업: %s" % (len(new_contents), backup_dir))
    return list(new_contents)


# ---------------------------------------------------------------- verify

def run_verify(task):
    results, all_pass = [], True
    for crit in task.get("acceptance_criteria", []):
        if AUTO_RUN_RE.match(crit):
            try:
                cp = subprocess.run(crit, shell=True, cwd=str(ROOT),
                                    capture_output=True, text=True, timeout=300)
                ok = cp.returncode == 0
                results.append((crit, "PASS" if ok else "FAIL",
                                (cp.stdout or "") + (cp.stderr or "")))
                all_pass &= ok
            except Exception as e:
                results.append((crit, "FAIL", str(e))); all_pass = False
        else:
            results.append((crit, "MANUAL", "사람이 직접 확인 필요"))
    # result.md 기록
    design_dir = AI_DIR / "designs" / task["id"]
    design_dir.mkdir(parents=True, exist_ok=True)
    md = ["# result.md — %s (%s)\n" % (task["id"], now())]
    for crit, status, log in results:
        md.append("## [%s] %s\n\n```\n%s\n```\n" % (status, crit, log.strip()[:3000]))
    (design_dir / "result.md").write_text("\n".join(md), encoding="utf-8")
    print("[verify] result.md 기록: %s" % (design_dir / "result.md"))
    for crit, status, _ in results:
        print("  [%s] %s" % (status, crit[:80]))
    return all_pass, results


# ---------------------------------------------------------------- main flow

def cmd_run(task_id, dry):
    task = load_task(task_id)
    # [추가] 셸 실행형 task(output_format=shell_commands 또는 commands 보유)만 수동 안내
    #        task_type 이름은 자유 기입(java_fix 등)이므로 판별 기준으로 쓰지 않음
    if task.get("output_format") == "shell_commands" or "commands" in task:
        print("[dispatcher] task_type=%s — coder 대상 아님. 아래 명령을 직접 실행:" % task.get("task_type", "?"))
        for c in task.get("commands", []):
            print("  " + c)
        return
    state = load_state(task_id)
    max_attempts = int(task.get("max_attempts", 3))
    feedback = None

    while state["attempts"] < max_attempts:
        state["attempts"] += 1
        print("\n=== 시도 %d/%d ===" % (state["attempts"], max_attempts))

        meta, prompt = build_prompt(task, feedback)
        raw = call_ollama(meta, prompt)

        if raw.upper().startswith("BLOCKED:"):
            transition(state, "blocked", raw[:200])
            print("[dispatcher] coder가 BLOCKED 반환 — 재설계 필요:\n" + raw)
            return

        diff_text = extract_diff(raw)
        if not diff_text:
            feedback = "Your output was not a unified diff. Output ONLY a unified diff starting with '--- a/'."
            print("[gate] diff 형식 아님 → 재시도")
            continue
        transition(state, "generated", "diff %d줄" % len(diff_text.splitlines()))

        reasons, targets = gate_check(diff_text, task)
        if reasons:
            feedback = "GATE REJECTED:\n- " + "\n- ".join(reasons) + \
                       "\nModify ONLY files in allowed_files: %s" % task.get("allowed_files")
            print("[gate] 거부: " + "; ".join(reasons))
            continue
        transition(state, "gated", "대상: " + ", ".join(targets))

        if dry:
            print("\n----- DRY RUN: 생성된 diff (미적용) -----\n" + diff_text)
            return

        try:
            apply_unified_diff(diff_text, task_id)
        except ValueError as e:
            feedback = "PATCH APPLY FAILED: %s\nRegenerate the diff with exact context lines from the original file." % e
            print("[apply] 실패 → 재시도: %s" % e)
            continue
        transition(state, "applied")

        ok, _ = run_verify(task)
        if ok:
            transition(state, "verified", "모든 자동 criteria 통과 (MANUAL 항목은 별도 확인)")
        else:
            transition(state, "failed", "검증 실패 — result.md를 Claude(architect)에 회신")
        return

    transition(state, "failed", "max_attempts(%d) 초과" % max_attempts)


def cmd_status(task_id):
    state = load_state(task_id)
    print(json.dumps(state, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="AI task dispatcher")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run"); p_run.add_argument("task_id"); p_run.add_argument("--dry", action="store_true")
    p_st = sub.add_parser("status"); p_st.add_argument("task_id")
    p_rs = sub.add_parser("reset"); p_rs.add_argument("task_id")  # [추가] 상태 초기화 (재도전용)
    args = ap.parse_args()
    if args.cmd == "run":
        cmd_run(args.task_id, args.dry)
    elif args.cmd == "reset":  # [추가]
        p = state_path(args.task_id)
        if p.exists():
            p.unlink()
        print("[state] %s 초기화 완료 — 다시 run 하세요" % args.task_id)
    else:
        cmd_status(args.task_id)
# v1.2 — indent-tolerant apply + reset 커맨드
