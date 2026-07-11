# D:\workspace\jarvis\tools\gen_code.py (신규)
import httpx, sys, pathlib
import subprocess
import difflib   # 추가: diff 생성용

design = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")

# 수정: 기존 코드 여러 파일 지원 (argv[3] 이후 전부)
existing = ""
if len(sys.argv) > 3:                                              # 추가
    for p in sys.argv[3:]:                                         # 추가: argv[3]~ 전부 순회
        existing += f"\n[기존 코드: {p}]\n" + pathlib.Path(p).read_text(encoding="utf-8")  # 추가
# 삭제(기존): existing = "\n[기존 코드]\n" + pathlib.Path(sys.argv[3]).read_text(encoding="utf-8")

prompt = f"""당신은 Python 코드 생성기입니다.
규칙:
- 필요한 코드는 아래 전부 제공됨. 파일 탐색·도구 호출 금지
- Python 3.x, 코드만 출력, 설명 금지
- 출력은 원본을 그대로 대체할 수 있는 완전한 전체 파일. 생략·축약(... 등) 금지   # 수정: 전체 파일 출력
- 변경 지점만 삭제=주석, 추가=주석 표기 (변경 안 된 부분도 모두 포함)             # 수정
# 삭제(기존): - '수정'은 수정 함수만, 삭제=주석, 추가=주석 표기
- 명세에 없는 기능 추가 금지
- 기존 코드의 네이밍·스타일을 따를 것
- logging 사용, print 금지. httpx<0.28 호환
- python-telegram-bot v22.8 async 스타일 준수

{design}
{existing}
"""

resp = httpx.post("http://localhost:11434/api/generate",
    json={"model": "qwen2.5-coder:14b", "prompt": prompt, "stream": False},
    timeout=900)

def save_code(text, path):
    if "```" in text:
        text = text.split("```python")[-1].split("```")[0]
    pathlib.Path(path).write_text(text.strip(), encoding="utf-8")

# 수정: 기존 저장부 교체
# code = resp.json()["response"]
# if "```" in code: ...
save_code(resp.json()["response"], sys.argv[2])
print(f"생성 완료: {sys.argv[2]}")
for attempt in range(3):
    r = subprocess.run(["python", "-m", "py_compile", sys.argv[2]],
                       capture_output=True, text=True)
    if r.returncode == 0:
        print("문법 검증 통과")
        break
    prompt += f"\n[문법 에러 — 수정할 것]\n{r.stderr[:1000]}"
    resp = httpx.post("http://localhost:11434/api/generate",
        json={"model": "qwen2.5-coder:14b", "prompt": prompt, "stream": False},
        timeout=900)
    code = resp.json()["response"]
    if "```" in code:
        code = code.split("```python")[-1].split("```")[0]
    pathlib.Path(sys.argv[2]).write_text(code.strip(), encoding="utf-8")

# 추가: 생성물(out\fix.py)을 기존 파일들과 비교해 diff 생성 (가독성 개선)
if len(sys.argv) > 3:
    new_lines = pathlib.Path(sys.argv[2]).read_text(encoding="utf-8").splitlines(keepends=True)
    # 추가: 기존 파일 중 변경량이 가장 적은(=수정 대상일 가능성 높은) 파일 자동 매칭
    best = None
    for p in sys.argv[3:]:
        old_lines = pathlib.Path(p).read_text(encoding="utf-8").splitlines(keepends=True)
        diff = list(difflib.unified_diff(old_lines, new_lines, fromfile=p, tofile=p))
        changed = sum(1 for l in diff if l.startswith(("+", "-")) and not l.startswith(("+++", "---")))
        if best is None or changed < best[0]:
            best = (changed, p, diff)
    diff_path = sys.argv[2] + ".diff"
    pathlib.Path(diff_path).write_text("".join(best[2]), encoding="utf-8")
    print(f"변경 대상 추정: {best[1]} (변경 {best[0]}줄)")
    print(f"diff 저장: {diff_path}")