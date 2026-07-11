# 설계: 음성/문서 요약 봇 (voice_summary)

> claudeQ 설계문서 — Claude 작성, Qwen 구현용 (2026-07-07)
> Qwen 사용법: Continue(Ctrl+L)에서 `@docs/design/design_voice_summary.md` 참조 후 단계별 구현 요청

## 1. 개요

텔레그램으로 파일 전송 시 자동 처리:

```
텔레그램 파일 전송
 ├─ .m4a → 다운로드 → STT(faster-whisper) → txt 저장 → LLM 요약 → 응답(요약 + txt파일)
 └─ .txt → 다운로드 → (STT 생략) → LLM 요약 → 응답(요약)
 └─ 기타 → "지원하지 않는 형식" 안내
```

- STT: `test_flow.py`의 faster-whisper 로직을 모듈화 (large-v3, CUDA, float16)
- 요약: 기존 `research_bot/modules/summarizer.py`의 `llm_answer()` 재사용 (Ollama qwen3:8b)
- router.py 미경유 — 파일 메시지는 telegram_bot.py에서 직접 voice bot 호출

## 2. 파일 구조

| 파일 | 구분 | 내용 |
|---|---|---|
| `bots/voice/__init__.py` | 신규 | 빈 파일 |
| `bots/voice/stt.py` | 신규 | faster-whisper STT 모듈 (test_flow.py 모듈화) |
| `bots/voice/bot.py` | 신규 | 파이프라인: m4a→txt→요약 / txt→요약 |
| `telegram_bot.py` | 수정 | 파일 핸들러 추가 (Document/Audio/Voice) |
| `config.yaml` | 수정 | whisper 설정 추가 |
| `data/voice/` | 신규 폴더 | 원본 + 결과 txt 저장 |

## 3. config.yaml 추가

```yaml
# 추가: 음성/문서 요약 설정
voice:
  model: "large-v3"          # faster-whisper 모델
  device: "cuda"
  compute_type: "float16"
  language: "ko"
  data_dir: "data/voice"     # 다운로드/결과 저장 폴더
  max_file_mb: 20            # 텔레그램 봇 다운로드 한도
  max_summary_chars: 12000   # 요약 입력 최대 글자수
```

## 4. bots/voice/stt.py (신규)

test_flow.py를 모듈화. 핵심: **모델은 최초 1회만 로딩**(지연 초기화, 로딩 수십초 소요).

```python
# bots/voice/stt.py
import os
import yaml
from faster_whisper import WhisperModel

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
with open(os.path.join(BASE_DIR, "config.yaml"), encoding="utf-8") as f:
    config = yaml.safe_load(f)

VOICE_CFG = config["voice"]

_model = None  # 모듈 전역 — 1회 로딩 후 재사용


def _get_model():
    """지연 초기화: 최초 호출 시에만 모델 로딩 (summarizer._get_client 패턴과 동일)"""
    global _model
    if _model is None:
        _model = WhisperModel(
            VOICE_CFG["model"],
            device=VOICE_CFG["device"],
            compute_type=VOICE_CFG["compute_type"],
        )
    return _model


def transcribe(audio_path: str, txt_path: str) -> str:
    """m4a → 텍스트 변환 후 txt 저장. 전체 텍스트 반환."""
    model = _get_model()
    segments, info = model.transcribe(audio_path, language=VOICE_CFG["language"])

    lines = [seg.text for seg in segments]
    text = "\n".join(lines)

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(text)

    return text
```

## 5. bots/voice/bot.py (신규)

```python
# bots/voice/bot.py
import os
import yaml

# telegram_bot.py가 research_bot을 sys.path에 넣으므로 modules 직접 import 가능
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
```

## 6. telegram_bot.py (수정)

### 6-1. import 추가 (상단)

```python
# 추가: 음성/문서 요약 봇
from bots.voice import bot as voice_bot
```

### 6-2. 파일 핸들러 함수 추가 (`handle_message` 아래에)

```python
# 추가: 파일(m4a/txt) 수신 처리
VOICE_DATA_DIR = os.path.join(BASE_DIR, config["voice"]["data_dir"])
os.makedirs(VOICE_DATA_DIR, exist_ok=True)
MAX_FILE_MB = config["voice"]["max_file_mb"]


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return

    msg = update.message
    # Document(파일 첨부) 또는 Audio(음악파일로 인식된 m4a) 모두 지원
    file_obj = msg.document or msg.audio
    if not file_obj:
        return

    file_name = file_obj.file_name or "unknown"
    ext = os.path.splitext(file_name)[1].lower()

    if ext not in (".m4a", ".txt"):
        await msg.reply_text(f"지원하지 않는 형식입니다: {ext}\n(.m4a 또는 .txt만 가능)")
        return

    if file_obj.file_size > MAX_FILE_MB * 1024 * 1024:
        await msg.reply_text(f"❌ 파일이 너무 큽니다 (한도 {MAX_FILE_MB}MB)")
        return

    await msg.reply_text("⏳ 파일 처리 중... (음성은 수 분 소요될 수 있습니다)")

    try:
        # 다운로드
        tg_file = await file_obj.get_file()
        save_path = os.path.join(VOICE_DATA_DIR, file_name)
        await tg_file.download_to_drive(save_path)

        # 처리 (동기·장시간 → 스레드 실행, 기존 _handle 패턴 동일)
        if ext == ".m4a":
            result = await asyncio.to_thread(voice_bot.process_m4a, save_path)
        else:  # .txt
            result = await asyncio.to_thread(voice_bot.process_txt, save_path)
    except Exception as e:
        logger.exception("[voice] 파일 처리 오류")
        await msg.reply_text(f"❌ 처리 중 오류: {e}")
        return

    # 요약 응답
    for chunk in _split(result["summary"]):
        await msg.reply_text(chunk)

    # m4a인 경우 변환 txt 파일도 전송
    if result.get("txt_path"):
        with open(result["txt_path"], "rb") as f:
            await msg.reply_document(f, filename=os.path.basename(result["txt_path"]))
```

### 6-3. main()에 핸들러 등록

```python
def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("research", cmd_research))
    app.add_handler(CallbackQueryHandler(cmd_callback))
    # 추가: 파일 수신 핸들러 (m4a/txt)
    app.add_handler(MessageHandler(filters.Document.ALL | filters.AUDIO, handle_file))

    if OPENCLAW_TOKEN:
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    ...
```

### 6-4. cmd_start 안내문 추가 (선택)

```python
        # 추가: 파일 요약 안내
        "\n\n[파일 요약]\n"
        "m4a 파일 전송 — 음성→텍스트 변환 + 요약\n"
        "txt 파일 전송 — 문서 요약"
```

## 7. 제약/주의사항

| 항목 | 내용 |
|---|---|
| 모델 로딩 | large-v3 최초 로딩 수십초 + VRAM 점유. 첫 m4a 처리만 느림 |
| VRAM | Ollama(qwen3:8b)와 faster-whisper 동시 사용 → VRAM 부족 시 config에서 model을 `medium`으로 변경 |
| 텔레그램 한도 | Bot API 다운로드 20MB. 초과 파일은 안내 후 종료 |
| 음성 메시지(Voice) | 텔레그램 음성녹음(ogg)은 현재 범위 외. 필요 시 `filters.VOICE` 추가 + ogg 지원 확장 |
| test_flow.py | 삭제하지 않고 유지 (단독 테스트용). 신규 로직은 bots/voice/stt.py |

## 8. 구현 순서 (Qwen 작업 지시)

| 순서 | 작업 | 검증 |
|---|---|---|
| 1 | `config.yaml`에 voice 섹션 추가 | yaml 로드 확인 |
| 2 | `bots/voice/__init__.py`, `bots/voice/stt.py` 생성 | `python -c "from bots.voice.stt import transcribe"` |
| 3 | `bots/voice/bot.py` 생성 | txt 파일로 `process_txt()` 단독 테스트 |
| 4 | `telegram_bot.py` 수정 (6-1~6-4) | 봇 재시작 → txt 전송 → 요약 수신 |
| 5 | m4a 전송 테스트 | 요약 + txt 파일 수신 확인 |

## 9. 테스트 시나리오

| # | 입력 | 기대 결과 |
|---|---|---|
| 1 | txt 파일 전송 | 요약 메시지 수신 |
| 2 | m4a 파일 전송 | 요약 메시지 + 변환 txt 파일 수신 |
| 3 | pdf 등 기타 파일 | "지원하지 않는 형식" 안내 |
| 4 | 20MB 초과 파일 | 한도 안내 |
| 5 | CP949 인코딩 txt | 정상 요약 (fallback 동작) |
| 6 | 빈 txt | "요약할 내용이 없습니다" |
