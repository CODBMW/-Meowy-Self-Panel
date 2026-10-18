import os
import asyncio
import threading
import logging
from datetime import datetime
from functools import wraps
from zoneinfo import ZoneInfo

from flask import Flask, request, redirect, url_for, session, render_template, flash
from telethon import TelegramClient, events
from apscheduler.schedulers.background import BackgroundScheduler
from openai import OpenAI

logging.basicConfig(level=logging.INFO)
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-this-before-deploying")

PANEL_USER = os.environ.get("PANEL_USERNAME", "admin")
PANEL_PASSWORD = os.environ.get("PANEL_PASSWORD", "")
API_ID = os.environ.get("TG_API_ID", "")
API_HASH = os.environ.get("TG_API_HASH", "")
SESSION_STRING = os.environ.get("TG_SESSION_STRING", "")
TIMEZONE = os.environ.get("TIMEZONE", "Asia/Tehran")
AI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

# Use environment variables for deployment. Do not commit secrets to GitHub.
state = {
    "bio_enabled": False,
    "secretary_enabled": False,
    "ai_enabled": False,
    "bio_morning": "",
    "bio_evening": "",
    "bio_morning_time": "08:00",
    "bio_evening_time": "20:00",
    "secretary_reply": "سلام، پیام شما دریافت شد. در اولین فرصت پاسخ می‌دهم.",
    "ai_instructions": "پاسخ کوتاه، مودبانه و فارسی بده.",
    "last_action": "هنوز اقدامی انجام نشده",
    "telegram_status": "در حال راه‌اندازی",
}
reply_rules = []

loop = asyncio.new_event_loop()
client = None
client_thread = None
scheduler = BackgroundScheduler(timezone=TIMEZONE)
_booted = False


def run_async_loop():
    asyncio.set_event_loop(loop)
    loop.run_forever()


def submit(coro, timeout=20):
    if not loop.is_running():
        raise RuntimeError("حلقه تلگرام هنوز اجرا نشده است.")
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=timeout)


async def set_bio(text):
    if not client or not client.is_connected():
        raise RuntimeError("اتصال تلگرام برقرار نیست.")
    from telethon.tl.functions.account import UpdateProfileRequest
    await client(UpdateProfileRequest(about=text[:70]))
    state["last_action"] = f"بیو در {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} تغییر کرد"


def scheduled_bio(which):
    if not state["bio_enabled"]:
        return
    text = state["bio_morning"] if which == "morning" else state["bio_evening"]
    if text.strip():
        try:
            submit(set_bio(text))
            logging.info("Scheduled bio updated (%s)", which)
        except Exception:
            logging.exception("Could not update scheduled bio")


async def ask_ai(message):
    if not AI_API_KEY:
        return None
    try:
        ai = OpenAI(api_key=AI_API_KEY)
        response = await asyncio.to_thread(
            ai.chat.completions.create,
            model=os.environ.get("OPENAI_MODEL", "gpt-4.1-mini"),
            messages=[
                {"role": "system", "content": state["ai_instructions"]},
                {"role": "user", "content": message[:3000]},
            ],
            max_tokens=180,
        )
        return response.choices[0].message.content.strip()
    except Exception:
        logging.exception("AI reply failed")
        return None


async def start_telegram():
    global client
    if not (API_ID and API_HASH and SESSION_STRING):
        state["telegram_status"] = "تنظیمات تلگرام کامل نیست؛ متغیرهای TG_API_ID، TG_API_HASH و TG_SESSION_STRING را تنظیم کنید."
        return
    try:
        client = TelegramClient(
            __import__("io").StringIO(SESSION_STRING) if False else "meowy_user_session",
            int(API_ID), API_HASH
        )
        # Session string is imported as a StringSession.
        from telethon.sessions import StringSession
        client = TelegramClient(StringSession(SESSION_STRING), int(API_ID), API_HASH)
        await client.start()
        state["telegram_status"] = "متصل"
        logging.info("Telegram user session connected")

        @client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
        async def secretary_handler(event):
            if not state["secretary_enabled"]:
                return
            sender = await event.get_sender()
            if getattr(sender, "bot", False):
                return
            incoming = (event.raw_text or "").strip()
            if not incoming:
                return
            # Optional keyword rules take precedence.
            for rule in reply_rules:
                if rule["keyword"].lower() in incoming.lower():
                    await event.reply(rule["reply"])
                    return
            if state["ai_enabled"] and AI_API_KEY:
                answer = await ask_ai(incoming)
                if answer:
                    await event.reply(answer)
                    return
            if state["secretary_reply"].strip():
                await event.reply(state["secretary_reply"])

        await client.run_until_disconnected()
    except Exception as exc:
        state["telegram_status"] = f"خطای اتصال: {type(exc).__name__}: {exc}"
        logging.exception("Telegram client stopped")


def require_login(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapped


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("username") == PANEL_USER and PANEL_PASSWORD and request.form.get("password") == PANEL_PASSWORD:
            session.clear()
            session["logged_in"] = True
            return redirect(url_for("index"))
        flash("نام کاربری یا رمز عبور اشتباه است، یا PANEL_PASSWORD تنظیم نشده.")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/", methods=["GET", "POST"])
@require_login
def index():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "save":
            state["bio_enabled"] = request.form.get("bio_enabled") == "on"
            state["secretary_enabled"] = request.form.get("secretary_enabled") == "on"
            state["ai_enabled"] = request.form.get("ai_enabled") == "on"
            state["bio_morning"] = request.form.get("bio_morning", "")[:70]
            state["bio_evening"] = request.form.get("bio_evening", "")[:70]
            state["bio_morning_time"] = request.form.get("bio_morning_time", "08:00")
            state["bio_evening_time"] = request.form.get("bio_evening_time", "20:00")
            state["secretary_reply"] = request.form.get("secretary_reply", "")[:2000]
            state["ai_instructions"] = request.form.get("ai_instructions", "")[:1000]
            # Rebuild schedule using configured local timezone.
            for job in ("bio_morning", "bio_evening"):
                if scheduler.get_job(job):
                    scheduler.remove_job(job)
            try:
                mh, mm = map(int, state["bio_morning_time"].split(":"))
                eh, em = map(int, state["bio_evening_time"].split(":"))
                scheduler.add_job(scheduled_bio, "cron", id="bio_morning", hour=mh, minute=mm, args=["morning"], replace_existing=True)
                scheduler.add_job(scheduled_bio, "cron", id="bio_evening", hour=eh, minute=em, args=["evening"], replace_existing=True)
                state["last_action"] = "تنظیمات ذخیره شد"
                flash("تنظیمات ذخیره شد.")
            except Exception as exc:
                flash(f"زمان‌بندی معتبر نیست: {exc}")
        elif action == "bio_now":
            text = request.form.get("manual_bio", "")[:70]
            try:
                submit(set_bio(text))
                flash("بیو تغییر کرد.")
            except Exception as exc:
                flash(f"تغییر بیو ناموفق بود: {exc}")
        elif action == "add_rule":
            keyword = request.form.get("keyword", "").strip()[:100]
            reply = request.form.get("rule_reply", "").strip()[:1000]
            if keyword and reply:
                reply_rules.append({"keyword": keyword, "reply": reply})
                flash("قانون منشی اضافه شد.")
        elif action == "clear_rules":
            reply_rules.clear()
            flash("همه قوانین منشی پاک شدند.")
        return redirect(url_for("index"))

    return render_template(
        "index.html",
        state=state,
        rules=reply_rules,
        timezone=TIMEZONE,
        ai_key_set=bool(AI_API_KEY),
    )


def boot():
    global _booted
    if _booted:
        return
    _booted = True
    threading.Thread(target=run_async_loop, daemon=True).start()
    async def launch():
        await start_telegram()
    asyncio.run_coroutine_threadsafe(launch(), loop)
    if not scheduler.running:
        scheduler.start()


# Start background Telegram client and scheduler when imported by Gunicorn or run directly.
boot()

if __name__ == "__main__":
    if not PANEL_PASSWORD:
        logging.warning("Set PANEL_PASSWORD and FLASK_SECRET_KEY before exposing this panel.")
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
