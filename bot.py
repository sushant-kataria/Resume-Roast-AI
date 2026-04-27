import os
import asyncio
import logging
from datetime import date
from dotenv import load_dotenv
from google import genai
from google.genai import types
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    PreCheckoutQueryHandler,
    ContextTypes,
    filters,
)
from prompts import FREE_ROAST_PROMPT, FULL_ROAST_PROMPT, REWRITE_PROMPT

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
BOT_USERNAME = os.getenv("BOT_USERNAME", "YourBotUsername")

gemini = genai.Client(api_key=GEMINI_API_KEY)
# Fallback chain: try each model in order until one succeeds
MODELS = [
    "gemini-3.1-flash-lite-preview",  # 500 RPD — primary
    "gemini-2.5-flash-lite",          # 20 RPD  — fallback 1
    "gemini-2.5-flash",               # 20 RPD  — fallback 2
]

# In-memory state (resets on restart; swap for Redis/DB in production)
free_usage: dict[str, bool] = {}        # "user_id:YYYY-MM-DD" -> True
pending_resumes: dict[int, str] = {}    # user_id -> resume text
referrals: dict[int, dict] = {}         # user_id -> {referral_count, bonus_roasts}
user_stats: dict[int, dict] = {}        # user_id -> {total_roasts, stars_spent}

STARS_FULL_ROAST = 50
STARS_REWRITE = 150


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _free_key(user_id: int) -> str:
    return f"{user_id}:{date.today().isoformat()}"


def _has_free_roast(user_id: int) -> bool:
    bonus = referrals.get(user_id, {}).get("bonus_roasts", 0)
    if bonus > 0:
        return True
    return _free_key(user_id) not in free_usage


def _consume_free_roast(user_id: int) -> None:
    bonus = referrals.get(user_id, {}).get("bonus_roasts", 0)
    if bonus > 0:
        referrals[user_id]["bonus_roasts"] -= 1
        return
    free_usage[_free_key(user_id)] = True


def _record_stat(user_id: int, stars: int = 0) -> None:
    stats = user_stats.setdefault(user_id, {"total_roasts": 0, "stars_spent": 0})
    stats["total_roasts"] += 1
    stats["stars_spent"] += stars


def _share_keyboard() -> InlineKeyboardMarkup:
    share_url = (
        f"https://t.me/share/url?url=https://t.me/{BOT_USERNAME}"
        "&text=Get%20your%20resume%20roasted%20by%20AI%20%F0%9F%94%A5"
    )
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⭐ Full Roast — 50 Stars", callback_data="buy_full"),
                InlineKeyboardButton("⭐ Roast + Rewrite — 150 Stars", callback_data="buy_rewrite"),
            ],
            [InlineKeyboardButton("📤 Share Bot", url=share_url)],
        ]
    )


def _paid_share_keyboard() -> InlineKeyboardMarkup:
    share_url = (
        f"https://t.me/share/url?url=https://t.me/{BOT_USERNAME}"
        "&text=Get%20your%20resume%20roasted%20by%20AI%20%F0%9F%94%A5"
    )
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("📤 Share Bot", url=share_url)]]
    )


async def _send_long(update: Update, text: str, reply_markup=None, parse_mode: str = "Markdown") -> None:
    """Split messages exceeding 3800 characters and send in chunks."""
    chunks = [text[i : i + 3800] for i in range(0, len(text), 3800)]
    for idx, chunk in enumerate(chunks):
        km = reply_markup if idx == len(chunks) - 1 else None
        await update.effective_message.reply_text(chunk, parse_mode=parse_mode, reply_markup=km)


async def _gemini_with_retry(contents) -> str:
    last_error = None
    for model in MODELS:
        for attempt in range(3):
            try:
                response = await gemini.aio.models.generate_content(
                    model=model,
                    contents=contents,
                )
                logger.info("Gemini success with model: %s", model)
                return response.text
            except Exception as e:
                last_error = e
                code = getattr(e, "status_code", None) or getattr(e, "code", None)
                err_str = str(e)
                is_503 = "503" in err_str or code == 503
                is_429 = "429" in err_str or code == 429
                is_404 = "404" in err_str or code == 404
                if is_404:
                    logger.warning("Model %s not found, trying next", model)
                    break  # skip remaining retries for this model
                if (is_503 or is_429) and attempt < 2:
                    wait = 5 * (2 ** attempt)
                    logger.warning("Model %s returned %s, retrying in %.0fs", model, code or "error", wait)
                    await asyncio.sleep(wait)
                else:
                    logger.warning("Model %s failed: %s — trying next model", model, code or err_str[:80])
                    break  # try next model
    raise last_error


async def _call_gemini(prompt: str) -> str:
    return await _gemini_with_retry(prompt)


async def _extract_pdf_text(pdf_bytes: bytes) -> str:
    contents = [
        types.Part(
            inline_data=types.Blob(mime_type="application/pdf", data=pdf_bytes)
        ),
        "Extract all resume text. Return plain text only.",
    ]
    return await _gemini_with_retry(contents)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    first_name = user.first_name or "friend"

    # Handle referral parameter: /start ref_<referrer_id>
    if context.args:
        arg = context.args[0]
        if arg.startswith("ref_"):
            try:
                referrer_id = int(arg[4:])
                if referrer_id != user.id:
                    ref_data = referrals.setdefault(referrer_id, {"referral_count": 0, "bonus_roasts": 0})
                    ref_data["referral_count"] += 1
                    ref_data["bonus_roasts"] += 1
                    try:
                        await context.bot.send_message(
                            referrer_id,
                            "🎉 Someone joined via your referral link! You earned 1 extra free roast.",
                        )
                    except Exception:
                        pass
            except ValueError:
                pass

    welcome = (
        f"🔥 *Welcome to ResumeRoast AI, {first_name}!*\n\n"
        "Your resume deserves brutal honesty — not your mom's opinion.\n\n"
        "Here's how it works:\n\n"
        f"🆓 *FREE* — 3-point roast (once per day)\n"
        f"⭐ *50 Stars* — Full 10-point roast + actionable fixes\n"
        f"⭐ *150 Stars* — Full roast + AI rewrites every weak section\n\n"
        "*How to start:*\n"
        "Just paste your resume as text below.\n"
        "Or send it as a PDF file.\n\n"
        "That's it. No signup. No email. Just fire. 🔥"
    )
    await update.message.reply_text(welcome, parse_mode="Markdown")


async def roast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📄 Send your resume as *text* or a *PDF file* and I'll tear it apart. 🔥",
        parse_mode="Markdown",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    help_text = (
        "*How to use ResumeRoast AI:*\n\n"
        "1️⃣ Use /roast and paste your resume as text, or send a PDF.\n"
        "2️⃣ Get your free 3-point roast (once per day).\n"
        "3️⃣ Upgrade with Telegram Stars for deeper roasts or full rewrites.\n\n"
        "*Commands:*\n"
        "/start — Welcome & pricing\n"
        "/roast — Start a roast\n"
        "/mystats — Your usage stats\n"
        "/refer — Get your referral link\n"
        "/help — This message"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")


async def mystats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    stats = user_stats.get(user_id, {"total_roasts": 0, "stars_spent": 0})
    ref_data = referrals.get(user_id, {"referral_count": 0, "bonus_roasts": 0})
    text = (
        "📊 *Your Stats:*\n\n"
        f"🔥 Total roasts: {stats['total_roasts']}\n"
        f"⭐ Stars spent: {stats['stars_spent']}\n"
        f"👥 Referrals: {ref_data['referral_count']}\n"
        f"🎁 Bonus roasts remaining: {ref_data['bonus_roasts']}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def refer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    link = f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"
    text = (
        f"🔗 *Your referral link:*\n{link}\n\n"
        "Share it with friends. Every person who joins gives you *1 extra free roast*! 🎉"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


# ---------------------------------------------------------------------------
# Resume processing
# ---------------------------------------------------------------------------

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    resume_text = update.message.text.strip()

    if len(resume_text) < 150:
        await update.message.reply_text(
            "That's too short to be a resume! Send the full thing 😏"
        )
        return

    pending_resumes[user_id] = resume_text
    await _run_free_roast(update, user_id, resume_text)


async def handle_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    doc = update.message.document

    await update.message.reply_text("📄 Reading your PDF... give me a sec 🔍")

    try:
        file = await context.bot.get_file(doc.file_id)
        pdf_bytes = await file.download_as_bytearray()
        resume_text = await _extract_pdf_text(bytes(pdf_bytes))
    except Exception as e:
        logger.error("PDF extraction failed: %s", e)
        await update.message.reply_text(
            "Couldn't read that PDF. Try pasting your resume as text instead."
        )
        return

    if len(resume_text.strip()) < 150:
        await update.message.reply_text(
            "That's too short to be a resume! Send the full thing 😏"
        )
        return

    pending_resumes[user_id] = resume_text
    await _run_free_roast(update, user_id, resume_text)


async def _run_free_roast(update: Update, user_id: int, resume_text: str) -> None:
    if not _has_free_roast(user_id):
        keyboard = _share_keyboard()
        await update.message.reply_text(
            "🚫 You've used your free roast for today!\n\n"
            "Upgrade with Stars for the *full experience*, or come back tomorrow. 🔥",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
        return

    await update.message.reply_text("🔥 Firing up the roast engine...")

    try:
        prompt = FREE_ROAST_PROMPT.format(resume_text=resume_text)
        result = await _call_gemini(prompt)
    except Exception as e:
        logger.error("Gemini error (free roast): %s", e, exc_info=True)
        await update.message.reply_text("⚠️ AI is busy, try again in 30 seconds")
        return

    _consume_free_roast(user_id)
    _record_stat(user_id)

    await _send_long(update, result, reply_markup=_share_keyboard())


# ---------------------------------------------------------------------------
# Callback query — upgrade buttons
# ---------------------------------------------------------------------------

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data == "buy_full":
        await context.bot.send_invoice(
            chat_id=user_id,
            title="Full Roast — 10-Point Breakdown",
            description="A savage 10-point roast of your resume with exact quotes and concrete fixes.",
            payload="full_roast",
            currency="XTR",
            prices=[LabeledPrice(label="Full Roast", amount=STARS_FULL_ROAST)],
        )

    elif data == "buy_rewrite":
        await context.bot.send_invoice(
            chat_id=user_id,
            title="Roast + Rewrite Package",
            description="10-point roast PLUS AI rewrites of your Summary, Skills, and top Experience bullets.",
            payload="rewrite",
            currency="XTR",
            prices=[LabeledPrice(label="Roast + Rewrite", amount=STARS_REWRITE)],
        )


# ---------------------------------------------------------------------------
# Payment handlers
# ---------------------------------------------------------------------------

async def pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.pre_checkout_query.answer(ok=True)


async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    payment = update.message.successful_payment
    payload = payment.invoice_payload
    stars_paid = payment.total_amount

    resume_text = pending_resumes.get(user_id)
    if not resume_text:
        await update.message.reply_text(
            "Session expired — please send your resume again! /roast"
        )
        return

    await update.message.reply_text("🔥 Payment received! Generating your premium roast...")

    try:
        if payload == "full_roast":
            prompt = FULL_ROAST_PROMPT.format(resume_text=resume_text)
        else:
            prompt = REWRITE_PROMPT.format(resume_text=resume_text)

        result = await _call_gemini(prompt)
    except Exception as e:
        logger.error("Gemini error: %s", e)
        await update.message.reply_text("⚠️ AI is busy, try again in 30 seconds")
        return

    _record_stat(user_id, stars=stars_paid)
    await _send_long(update, result, reply_markup=_paid_share_keyboard())


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    from telegram.error import Conflict
    if isinstance(context.error, Conflict):
        logger.debug("Conflict on startup (old instance still shutting down) — ignored")
        return
    logger.error("Unhandled error: %s", context.error, exc_info=context.error)


def _log_available_models() -> None:
    try:
        for m in gemini.models.list():
            actions = getattr(m, "supported_actions", []) or []
            if "generateContent" in actions:
                logger.info("Available model: %s", m.name)
    except Exception as e:
        logger.warning("Could not list models: %s", e)


def main() -> None:
    _log_available_models()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("roast", roast_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("mystats", mystats))
    app.add_handler(CommandHandler("refer", refer))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(
        MessageHandler(filters.Document.MimeType("application/pdf"), handle_pdf)
    )

    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(PreCheckoutQueryHandler(pre_checkout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    app.add_error_handler(error_handler)

    logger.info("ResumeRoast AI bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
