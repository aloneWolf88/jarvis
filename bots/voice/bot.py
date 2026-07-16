# bots/voice/bot.py
import io
import os
import re                              # 추가: 파일명 정제·날짜 추출
import datetime                        # 추가: 날짜 fallback
import yaml

# telegram_bot.py가 bots/research/ 를 sys.path 에 넣으므로 modules 직접 import 가능
from bots.research.modules.summarizer import llm_answer
from bots.voice.stt import transcribe

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
with open(os.path.join(BASE_DIR, "config.yaml"), encoding="utf-8") as f:
    config = yaml.safe_load(f)

MAX_CHARS = config["voice"]["max_summary_chars"]

# 추가: 요약본 저장 설정 로드
VOICE_CFG = config["voice"]
SUMMARY_SAVE_DIR = VOICE_CFG.get("summary_save_dir")           # 없으면 저장 스킵
SUMMARY_DEFAULT_FOLDER = VOICE_CFG.get("summary_default_folder", "90.기타")
CATEGORY_MAP = VOICE_CFG.get("summary_category_map", {})       # 태그 → 하위폴더

# 추가: 카테고리 후보를 프롬프트에 주입 (LLM이 이 중에서만 태그 선택 → 폴더 난립 방지)
_CATEGORY_KEYS = list(CATEGORY_MAP.keys())
_CATEGORY_HINT = " / ".join(_CATEGORY_KEYS) if _CATEGORY_KEYS else "역사 / 우리소리"

# 수정: 단순 요약 → 회의록 구조 요약 + 첫 줄에 제목([카테고리]) 출력
SUMMARY_PROMPT = f"""다음은 회의 녹취(또는 문서) 본문입니다. 아래 정해진 구조로 상세 요약하세요.

규칙:
- 본문에 실제로 언급된 내용만 사용하고 추측·창작 금지
- 해당 항목의 내용이 본문에 없으면 그 소제목은 생략
- 결정사항은 표(마크다운)로 정리
- 각 bullet은 한 줄로 간결하게
- 맨 첫 줄에 반드시 "제목: [카테고리]핵심제목" 형식으로 한 줄 출력
  · 카테고리는 다음 중 가장 알맞은 것 하나만: {_CATEGORY_HINT}
  · 해당 없으면 [기타]
  · 핵심제목은 25자 이내

출력 형식:
제목: [카테고리]핵심제목
■ 회의 목적
	(한두 줄)
■ 핵심 결론
	1. ...
	2. ...
■ 주요 논의 사항
	[주제1]
		- ...
	[주제2]
		- ...
■ 결정사항
	| 항목 | 내용 |
	|---|---|
	| ... | ... |
■ 종합 의견
	(마무리 정리)

본문:
{{text}}"""


# 추가: 분할 요약(map 단계)용 프롬프트 — 조각별 핵심만 뽑음
CHUNK_PROMPT = """다음은 긴 회의 녹취의 일부(조각)입니다. 이 조각에서 논의된 핵심 내용을
불릿으로만 간결히 추출하세요. 형식·서론·결론 없이 사실만 나열하세요.

조각 본문:
{text}"""


# 추가: 본문을 MAX_CHARS 이하 조각으로 분할 (문단 경계 우선, 없으면 강제 분할)
def _split_chunks(text: str, size: int):
    chunks, buf = [], ""
    for para in text.split("\n"):
        if len(buf) + len(para) + 1 > size:
            if buf:
                chunks.append(buf)
            # 한 문단이 size보다 길면 강제로 잘라 넣음
            while len(para) > size:
                chunks.append(para[:size])
                para = para[size:]
            buf = para
        else:
            buf = f"{buf}\n{para}" if buf else para
    if buf:
        chunks.append(buf)
    return chunks


def summarize_text(text: str) -> str:
    """txt 내용 → LLM 요약. m4a/txt 두 경로 모두 이 함수로 수렴.
    수정: 12,000자 초과 시 잘라내던 방식 → 분할 요약(map-reduce)로 전체 요약"""
    if not text or not text.strip():
        return "요약할 내용이 없습니다."

    # 짧으면 기존과 동일하게 1회 요약
    if len(text) <= MAX_CHARS:
        result = llm_answer(SUMMARY_PROMPT.format(text=text), temperature=0.3)
        return result or "❌ 요약 실패 (LLM 응답 없음)"

    # 긴 본문: map(조각별 부분요약) → reduce(부분요약 합쳐 최종 회의록 형식)
    chunks = _split_chunks(text, MAX_CHARS)
    partials = []
    for i, chunk in enumerate(chunks, 1):
        part = llm_answer(CHUNK_PROMPT.format(text=chunk), temperature=0.3)
        if part:
            partials.append(f"[조각 {i}]\n{part}")

    if not partials:
        return "❌ 요약 실패 (LLM 응답 없음)"

    merged = "\n\n".join(partials)
    # 부분요약 합본이 또 길면 재귀적으로 한 번 더 축약
    if len(merged) > MAX_CHARS:
        merged = merged[:MAX_CHARS]

    result = llm_answer(SUMMARY_PROMPT.format(text=merged), temperature=0.3)
    if not result:
        return "❌ 요약 실패 (LLM 응답 없음)"

    result += f"\n\nℹ️ 본문이 길어 {len(chunks)}개 조각으로 나눠 전체를 요약했습니다."  # 수정: '앞 N자만' 경고 → 전체 요약 안내
    return result


# 추가: 요약본을 카테고리별 폴더에 저장하는 로직 ─────────────────────────
def _extract_date(source_path: str) -> str:
    """원본 파일명에서 YYYYMMDD 추출, 없으면 오늘 날짜. 예: '음성 260710_...' → 20260710 보정 안 함,
    8자리 우선, 없으면 6자리(YYMMDD)를 20YYMMDD로."""
    name = os.path.basename(source_path or "")
    m = re.search(r"(20\d{6})", name)          # 8자리 YYYYMMDD 우선
    if m:
        return m.group(1)
    m = re.search(r"(?<!\d)(\d{6})(?!\d)", name)  # 6자리 YYMMDD → 20YYMMDD
    if m:
        return "20" + m.group(1)
    return datetime.date.today().strftime("%Y%m%d")


def _sanitize(name: str) -> str:
    """윈도우 파일명 금지문자 제거·정리."""
    name = re.sub(r'[\\/:*?"<>|]', "", name).strip()
    name = re.sub(r"\s+", " ", name)
    return name[:80] or "무제"  # 과도한 길이 방지


def _parse_title(summary: str):
    """요약 첫 줄 '제목: [카테고리]제목' 파싱 → (category, title). 실패 시 ('기타', 첫줄/무제)."""
    first = (summary or "").strip().splitlines()[0] if summary.strip() else ""
    first = re.sub(r"^\s*제목\s*[:：]\s*", "", first)  # '제목:' 접두 제거
    m = re.match(r"\[(.+?)\]\s*(.*)", first)
    if m:
        return m.group(1).strip(), (m.group(2).strip() or "무제")
    return "기타", (first.strip() or "무제")


def save_summary(summary: str, source_path: str) -> str | None:
    """요약본을 카테고리 폴더에 'YYYYMMDD_[태그]제목.txt'로 저장. 경로 반환(미설정 시 None)."""
    if not SUMMARY_SAVE_DIR:
        return None
    category, title = _parse_title(summary)
    folder = CATEGORY_MAP.get(category, SUMMARY_DEFAULT_FOLDER)  # 매핑 없으면 기본 폴더
    date = _extract_date(source_path)
    filename = _sanitize(f"{date}_[{category}]{title}") + "_요약.txt"  # 수정: 요약본 파일명 끝에 '_요약' 추가
    dest_dir = os.path.join(SUMMARY_SAVE_DIR, folder)
    os.makedirs(dest_dir, exist_ok=True)          # 폴더 없으면 생성
    dest = os.path.join(dest_dir, filename)
    # 동일 파일명 존재 시 (1),(2)... 붙여 덮어쓰기 방지
    base, ext = os.path.splitext(dest)
    i = 1
    while os.path.exists(dest):
        dest = f"{base}({i}){ext}"
        i += 1
    with open(dest, "w", encoding="utf-8") as f:
        f.write(summary)
    return dest
# ───────────────────────────────────────────────────────────────────────


def process_m4a(audio_path: str) -> dict:
    """m4a 파이프라인: STT → txt 저장 → 요약 → 요약본+원본 저장
    반환: {"summary": str, "txt_path": str, "saved_path": str|None, "saved_original": str|None}"""
    txt_path = os.path.splitext(audio_path)[0] + ".txt"
    text = transcribe(audio_path, txt_path)
    summary = summarize_text(text)
    saved_path = save_summary(summary, audio_path)  # 카테고리 폴더에 요약본 저장
    # 추가: STT 원본도 같은 폴더에 '..._원본.txt' 로 저장 (요약본 저장 성공 시에만)
    saved_original = None
    if saved_path:
        root = saved_path[:-4]           # '.txt' 제거
        if root.endswith("_요약"):        # 수정: '_요약' 접미 제거 후 '_원본' 부여 → 'xxx_원본.txt'
            root = root[:-3]             # '_요약'(3자) 제거
        saved_original = root + "_원본.txt"
        with open(saved_original, "w", encoding="utf-8") as f:
            f.write(text)
    return {"summary": summary, "txt_path": txt_path,
            "saved_path": saved_path, "saved_original": saved_original}


def _read_text_any(txt_path: str) -> str:
    """여러 인코딩 순차 시도. BOM 있는 UTF-8/UTF-16 및 CP949(윈도우 메모장) 대응.
    추가: utf-8-sig, utf-16 (BOM 0xFE/0xFF), utf-16-le/be fallback"""
    # utf-8-sig: BOM 있으면 제거, 없으면 일반 utf-8과 동일
    # 주의: utf-16-le/be(BOM無)는 아무 바이트나 억지 디코딩→CP949 깨짐. 제외.
    # utf-16(BOM有만 성공)을 cp949보다 먼저 두어 BOM파일 우선 처리
    for enc in ("utf-8-sig", "utf-16", "cp949"):  # 수정: utf-8/cp949 → utf-8-sig/utf-16/cp949
        try:
            with open(txt_path, encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
    # 최후: 손실 허용 디코딩 (깨진 문자만 대체)
    with open(txt_path, encoding="utf-8", errors="replace") as f:
        return f.read()


def process_txt(txt_path: str) -> dict:
    """txt 파이프라인: 읽기 → 요약 (STT 생략) → 요약본 저장"""
    text = _read_text_any(txt_path)  # 수정: try/except 인라인 → _read_text_any 로 위임
    summary = summarize_text(text)
    saved_path = save_summary(summary, txt_path)  # 추가: 카테고리 폴더에 저장
    return {"summary": summary, "txt_path": None, "saved_path": saved_path}
