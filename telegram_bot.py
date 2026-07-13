import asyncio
import io                            # 추가: voice bot reply_document BytesIO (P1 보완)
import logging
import sys
import os

# 추가: bots/research 폴더를 path에 추가 (modules 찾기 위해) — 수정: research_bot → bots/research
RESEARCH_BOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bots", "research")
sys.path.insert(0, RESEARCH_BOT_DIR)

import yaml
import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler, 
    ContextTypes,
    MessageHandler,
    filters,
)

# from research_bot.main import cmd_today   # 삭제: telegram_bot은 직접 안 씀 (program이 호출)
from program import process
# 추가: 음성/문서 요약 봇 — design_voice_summary.md §6-1
from bots.voice import bot as voice_bot

# ── 설정 로드 ──
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(BASE_DIR, "config.yaml"), encoding="utf-8") as f:
    config = yaml.safe_load(f)

TOKEN = config["telegram"]["token"]
ALLOWED = set(str(x) for x in config["telegram"].get("allow_from", []))

# ── 로깅 ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ── 환경변수 로드 ──
# 수정: dotenv 패키지 의존 제거 (배치 실행 환경에 미설치 → ModuleNotFoundError)
# from dotenv import load_dotenv   # 삭제
# load_dotenv()                    # 삭제
def load_dotenv(path=os.path.join(BASE_DIR, ".env")):  # 추가: 표준 라이브러리로 .env 파싱
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))

load_dotenv()

OPENCLAW_TOKEN = os.getenv("OPENCLAW_TOKEN")
OPENCLAW_URL = "http://127.0.0.1:18789/v1/responses"

# ── 권한 체크 (allow_from 비어있으면 전체 허용) ──
def is_allowed(update: Update) -> bool:
    if not ALLOWED:
        return True
    return str(update.effective_user.id) in ALLOWED


# ── 긴 메시지 분할 (텔레그램 4096자 제한 대응) ──
def _split(text: str, size: int = 4000):
    if len(text) <= size:
        return [text]
    return [text[i:i + size] for i in range(0, len(text), size)]


# ── 공통 처리 (봇별 핸들러가 domain만 다르게 호출) ──
async def _handle(update: Update, text: str, domain: str):
    if not text:
        await update.message.reply_text("질문/인자를 함께 입력하세요.")
        return

    logger.info(f"[{domain}] 입력: {text}")
    await update.message.reply_text("⏳ 처리 중...")

    try:
        # process()는 동기 함수 → 별도 스레드 실행 (봇 블로킹 방지)
        result = await asyncio.to_thread(process, text, domain)
    except Exception as e:
        logger.exception(f"[{domain}] 오류")
        await update.message.reply_text(f"❌ 처리 중 오류: {e}")
        return

    if not result:
        result = "처리 결과가 없습니다."

    print(f"[DEBUG] result type: {type(result)}")
    print(f"[DEBUG] result: {result}")
    # 추가: dict인지 확인 (버튼 있는지)
    if isinstance(result, dict) and "buttons" in result:
        print(f"[DEBUG] buttons count: {len(result['buttons'])}")
        text_msg = result["text"]
        buttons_data = result["buttons"]
        
        # 추가: InlineKeyboardButton 생성 (한 줄에 2개씩)
        keyboard_buttons = []
        for btn in buttons_data:
            keyboard_buttons.append(
                InlineKeyboardButton(btn["label"], callback_data=btn["command"])
            )
        
        # 추가: 버튼을 2개씩 배치
        button_rows = [
            keyboard_buttons[i:i+2] for i in range(0, len(keyboard_buttons), 2)
        ]
        keyboard = InlineKeyboardMarkup(button_rows)
        
        for chunk in _split(text_msg):
            await update.message.reply_text(chunk, reply_markup=keyboard)
    else:
        # 기존: 일반 텍스트 (버튼 없음)
        for chunk in _split(result):
            await update.message.reply_text(chunk)

# 추가: 버튼 클릭 처리
async def cmd_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()   # 로딩 표시 제거 (필수)

    # callback_data = "/research 삼성전자 조회"
    data = query.data
    logger.info(f"[callback] {data}")

    # "/research " 접두사 제거 → "삼성전자 조회"
    if data.startswith("/research "):
        text = data.replace("/research ", "", 1).strip()
        # 추가: 결과 처리 (버튼 클릭 → 조회 실행)
        await query.message.reply_text("⏳ 처리 중...")
        try:
            result = await asyncio.to_thread(process, text, "research")
        except Exception as e:
            logger.exception("[callback] 오류")
            await query.message.reply_text(f"❌ 오류: {e}")
            return

        if not result:
            result = "처리 결과가 없습니다."

        # dict면 텍스트만, 아니면 그대로
        if isinstance(result, dict):
            result = result.get("text", "처리 결과가 없습니다.")

        for chunk in _split(result):
            await query.message.reply_text(chunk)

# ── /start ──
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    await update.message.reply_text(
        "🤖 Jarvis Bot\n\n"
        "[Research]\n"
        "/research 요약 — 리포트 요약\n"
        "/research 원전 요약 — 키워드 필터 요약\n"
        "/research 통계 — 전체 통계\n"
        "/research 삼성전자 리포트 — 종목 분석\n"
        "/research 오늘 리포트 — 오늘 수집분\n"
        "/research 어제 리포트 — 어제 수집분\n"
        "/research 한달간 삼성전자 추세 — 자연어 질문\n"
        "/research 수집 — 크롤링+요약 수동 실행\n"
        "/research 도움말 — 전체 명령어"
        # 향후 봇 안내 (구현 후 활성화)
        # "\n\n[YouTube]\n/youtube <URL> — 영상 요약"
        # "\n\n[DocSort]\n/doc_sort <경로> <방식> — 파일 정리"
        # 추가: 파일 요약 안내 — design_voice_summary.md §6-4
        "\n\n[파일 요약]\n"
        "m4a 파일 전송 — 음성→텍스트 변환 + 요약\n"
        "txt 파일 전송 — 문서 요약"
    )


# ── /research_bot ──
async def cmd_research(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    text = " ".join(context.args).strip()
    await _handle(update, text, domain="research")


# 추가: /voice <로컬경로> — 큰 m4a/txt를 업로드 없이 로컬 파일로 직접 요약
#  (텔레그램 봇 다운로드 한도 20MB 우회. 봇과 같은 PC의 파일만 접근 가능)
async def cmd_voice_local(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    path = " ".join(context.args).strip().strip('"')  # 따옴표로 감싼 경로도 허용
    if not path:
        await update.message.reply_text(
            "사용법: /voice <파일 전체경로>\n예: /voice D:\\90.temp\\20260703공공데이터 등록.m4a"
        )
        return
    if not os.path.exists(path):
        await update.message.reply_text(f"❌ 파일을 찾을 수 없습니다:\n{path}")
        return
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".m4a", ".txt"):
        await update.message.reply_text(f"지원하지 않는 형식입니다: {ext}\n(.m4a 또는 .txt만 가능)")
        return

    await update.message.reply_text("⏳ 로컬 파일 처리 중... (음성은 수 분 소요될 수 있습니다)")
    try:
        if ext == ".m4a":
            result = await asyncio.to_thread(voice_bot.process_m4a, path)
        else:
            result = await asyncio.to_thread(voice_bot.process_txt, path)
    except Exception as e:
        logger.exception("[voice] 로컬 파일 처리 오류")
        await update.message.reply_text(f"❌ 처리 중 오류: {e}")
        return

    for chunk in _split(result["summary"]):
        await update.message.reply_text(chunk)
    if result.get("saved_path"):  # 추가: 로컬 저장 경로 안내
        note = f"💾 저장됨: {result['saved_path']}"
        if result.get("saved_original"):  # 추가: 원본 txt 경로도 함께 안내
            note += f"\n📄 원본: {result['saved_original']}"
        await update.message.reply_text(note)
    if result.get("txt_path"):
        with open(result["txt_path"], "rb") as f:
            buf = io.BytesIO(f.read())
        buf.name = os.path.basename(result["txt_path"])
        await update.message.reply_document(buf, filename=buf.name)


# 추가: /youtube (나중에 youtube_bot 구현 후 활성화)
# async def cmd_youtube(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     if not is_allowed(update):
#         return
#     text = " ".join(context.args).strip()
#     await _handle(update, text, domain="youtube")


# 추가: /doc_sort (나중에 doc_sort 구현 후 활성화)
# async def cmd_docsort(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     if not is_allowed(update):
#         return
#     text = " ".join(context.args).strip()
#     await _handle(update, text, domain="docsort")


# ── OpenClaw API 호출 ──
async def ask_openclaw(text: str, session_key: str) -> str:
    if not OPENCLAW_TOKEN:
        raise ValueError("OPENCLAW_TOKEN이 설정되지 않았습니다.")

    headers = {"Authorization": f"Bearer {OPENCLAW_TOKEN}"}
    data = {"text": text, "session_key": session_key}

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            response = await client.post(OPENCLAW_URL, headers=headers, json=data)
            response.raise_for_status()
            result = response.json()
            return str(result.get("text", ""))
        except httpx.TimeoutException:
            logger.warning("OpenClaw 응답 시간 초과")
            raise
        except httpx.HTTPStatusError as e:
            logger.error(f"OpenClaw HTTP 오류: {e.response.status_code}")
            raise
        except httpx.ConnectError:
            logger.error("OpenClaw 연결 불가")
            raise
        except Exception:
            logger.exception("OpenClaw 응답 파싱 실패")
            raise


# ── 비명령어 메시지 처리 ──
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return

    text = update.message.text.strip()
    session_key = str(update.effective_user.id)

    try:
        response_text = await ask_openclaw(text, session_key)
        for chunk in _split(response_text):
            await update.message.reply_text(chunk)
    except Exception as e:
        logger.exception("OpenClaw 호출 실패")
        await update.message.reply_text("❌ 처리 실패: OpenClaw 응답 없음")


# 추가: 파일(m4a/txt) 수신 처리 — design_voice_summary.md §6-2
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

    if result.get("saved_path"):  # 추가: 로컬 저장 경로 안내
        note = f"💾 저장됨: {result['saved_path']}"
        if result.get("saved_original"):  # 추가: 원본 txt 경로도 함께 안내
            note += f"\n📄 원본: {result['saved_original']}"
        await msg.reply_text(note)

    # m4a인 경우 변환 txt 파일도 전송 (P1 보완: BytesIO 로 복사본 전달 — async 중 파일 닫힘 위험 회피)
    if result.get("txt_path"):
        with open(result["txt_path"], "rb") as f:
            buf = io.BytesIO(f.read())
        buf.name = os.path.basename(result["txt_path"])
        await msg.reply_document(buf, filename=buf.name)


# ── 메인 ──
def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("research", cmd_research))
    app.add_handler(CommandHandler("voice", cmd_voice_local))  # 추가: 로컬 m4a/txt 경로 요약
    app.add_handler(CallbackQueryHandler(cmd_callback))   # 추가: 버튼 클릭 처리

    # 추가: 파일 수신 핸들러 (m4a/txt) — 설계문서 §6-3 + P2 보정
    #  PTB 는 등록 순서대로 매칭 → Document/Audio 가 TEXT 핸들러보다 먼저 등록되어야
    #  Document 메시지가 TEXT 핸들러에 먼저 매칭되는 것을 방지
    app.add_handler(MessageHandler(filters.Document.ALL | filters.AUDIO, handle_file))

    if OPENCLAW_TOKEN:
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("✅ Jarvis Bot 시작 (폴링)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()