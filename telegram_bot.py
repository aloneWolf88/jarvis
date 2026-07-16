import asyncio
import io
import logging
import sys
import os
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
from bots.voice import bot as voice_bot

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(BASE_DIR, "config.yaml"), encoding="utf-8") as f:
    config = yaml.safe_load(f)

TOKEN = config["telegram"]["token"]
ALLOWED = set(str(x) for x in config["telegram"].get("allow_from", []))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

def load_dotenv(path=os.path.join(BASE_DIR, ".env")):
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

def is_allowed(update: Update) -> bool:
    if not ALLOWED:
        return True
    return str(update.effective_user.id) in ALLOWED

def _split(text: str, size: int = 4000):
    if len(text) <= size:
        return [text]
    return [text[i:i + size] for i in range(0, len(text), size)]

async def _handle(update: Update, text: str, domain: str):
    if not text:
        await update.message.reply_text("질문/인자를 함께 입력하세요.")
        return

    logger.info(f"[{domain}] 입력: {text}")
    await update.message.reply_text("⏳ 처리 중...")

    try:
        result = await asyncio.to_thread(process, text, domain)
    except Exception as e:
        logger.exception(f"[{domain}] 오류")
        await update.message.reply_text(f"❌ 처리 중 오류: {e}")
        return

    if not result:
        result = "처리 결과가 없습니다."

    print(f"[DEBUG] result type: {type(result)}")
    print(f"[DEBUG] result: {result}")

    if isinstance(result, dict) and "buttons" in result:
        text_msg = result["text"]
        buttons_data = result["buttons"]

        keyboard_buttons = []
        for btn in buttons_data:
            keyboard_buttons.append(
                InlineKeyboardButton(btn["label"], callback_data=btn["command"])
            )

        button_rows = [
            keyboard_buttons[i:i+2] for i in range(0, len(keyboard_buttons), 2)
        ]
        keyboard = InlineKeyboardMarkup(button_rows)

        for chunk in _split(text_msg):
            await update.message.reply_text(chunk, reply_markup=keyboard)
    else:
        for chunk in _split(result):
            await update.message.reply_text(chunk)

async def cmd_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    logger.info(f"[callback] {data}")

    if data.startswith("/research "):
        text = data.replace("/research ", "", 1).strip()
        await query.message.reply_text("⏳ 처리 중...")
        try:
            result = await asyncio.to_thread(process, text, "research")
        except Exception as e:
            logger.exception("[callback] 오류")
            await query.message.reply_text(f"❌ 오류: {e}")
            return

        if not result:
            result = "처리 결과가 없습니다."

        if isinstance(result, dict):
            result = result.get("text", "처리 결과가 없습니다.")

        for chunk in _split(result):
            await query.message.reply_text(chunk)

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    await update.message.reply_text(
        "🤖 Jarvis Bot\n\n"
        "[Research]\n"
        "/research 요약 — 리포트 요약\n"
        "/research 원전 요약 — 키워드 필터 요약\n"
        # ... (나머지 메시지 생략)
    )

async def cmd_research(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    text = " ".join(context.args).strip()
    await _handle(update, text, domain="research")

async def cmd_voice_local(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    path = " ".join(context.args).strip().strip('"')
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
    if result.get("saved_path"):
        note = f"💾 저장됨: {result['saved_path']}"
        if result.get("saved_original"):
            note += f"\n📄 원본: {result['saved_original']}"
        await update.message.reply_text(note)
    if result.get("txt_path"):
        with open(result["txt_path"], "rb") as f:
            buf = io.BytesIO(f.read())
        buf.name = os.path.basename(result["txt_path"])
        await update.message.reply_document(buf, filename=buf.name)

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

VOICE_DATA_DIR = os.path.join(BASE_DIR, config["voice"]["data_dir"])
os.makedirs(VOICE_DATA_DIR, exist_ok=True)
MAX_FILE_MB = config["voice"]["max_file_mb"]

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return

    msg = update.message
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
        tg_file = await file_obj.get_file()
        save_path = os.path.join(VOICE_DATA_DIR, file_name)
        await tg_file.download_to_drive(save_path)

        if ext == ".m4a":
            result = await asyncio.to_thread(voice_bot.process_m4a, save_path)
        else:
            result = await asyncio.to_thread(voice_bot.process_txt, save_path)
    except Exception as e:
        logger.exception("[voice] 파일 처리 오류")
        await msg.reply_text(f"❌ 처리 중 오류: {e}")
        return

    for chunk in _split(result["summary"]):
        await msg.reply_text(chunk)

    if result.get("saved_path"):
        note = f"💾 저장됨: {result['saved_path']}"
        if result.get("saved_original"):
            note += f"\n📄 원본: {result['saved_original']}"
        await update.message.reply_text(note)

    if result.get("txt_path"):
        with open(result["txt_path"], "rb") as f:
            buf = io.BytesIO(f.read())
        buf.name = os.path.basename(result["txt_path"])
        await msg.reply_document(buf, filename=buf.name)

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("research", cmd_research))
    app.add_handler(CommandHandler("voice", cmd_voice_local))
    app.add_handler(CallbackQueryHandler(cmd_callback))

    app.add_handler(MessageHandler(filters.Document.ALL | filters.AUDIO, handle_file))

    if OPENCLAW_TOKEN:
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("✅ Jarvis Bot 시작 (폴링)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()