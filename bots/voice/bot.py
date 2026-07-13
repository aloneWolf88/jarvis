# bots/voice/bot.py
import io
import os
import yaml

# telegram_bot.py가 bots/research/ 를 sys.path 에 넣으므로 modules 직접 import 가능
# 수정: design_voice_summary.md §5 코드 그대로 (research_bot → bots/research 이동에 맞춰 주석만 동기화)
from modules.summarizer import llm_answer
from bots.voice.stt import transcribe

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
with open(os.path.join(BASE_DIR, "config.yaml"), encoding="utf-8") as f:
    config = yaml.safe_load(f)

MAX_CHARS = config["voice"]["max_summary_chars"]

SUMMARY_PROMPT = """다음 문서를 요약하세요.

형식:
📌 핵심 요약 (3~5줄)
- 주요 내용 bullet
🔑 키워드: 키워드 3~5개

본문:
{text}"""


def summarize_text(text: str) -> str:
    """txt 내용 → LLM 요약. m4a/txt 두 경로 모두 이 함수로 수렴."""
    if not text or not text.strip():
        return "요약할 내용이 없습니다."

    truncated = False
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS]
        truncated = True

    result = llm_answer(SUMMARY_PROMPT.format(text=text), temperature=0.3)
    if not result:
        return "❌ 요약 실패 (LLM 응답 없음)"

    if truncated:
        result += f"\n\n⚠️ 본문이 길어 앞 {MAX_CHARS:,}자만 요약했습니다."
    return result


def process_m4a(audio_path: str) -> dict:
    """m4a 파이프라인: STT → txt 저장 → 요약
    반환: {"summary": str, "txt_path": str}"""
    txt_path = os.path.splitext(audio_path)[0] + ".txt"
    text = transcribe(audio_path, txt_path)
    return {"summary": summarize_text(text), "txt_path": txt_path}


def process_txt(txt_path: str) -> dict:
    """txt 파이프라인: 읽기 → 요약 (STT 생략)
    인코딩: UTF-8 기본, 실패 시 CP949 fallback (윈도우 메모장 대비)"""
    try:
        with open(txt_path, encoding="utf-8") as f:
            text = f.read()
    except UnicodeDecodeError:
        with open(txt_path, encoding="cp949") as f:
            text = f.read()
    return {"summary": summarize_text(text), "txt_path": None}
