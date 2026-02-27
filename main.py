"""
Telegram Support Bot — Groq AI + Manual Handoff
================================================
✅ Ready for Render.com deployment
✅ Built-in web server (required by Render to stay alive)
✅ Groq AI — 100% free, no credit card

Deploy to Render:
1. Push this file to a GitHub repo
2. Go to render.com → New → Web Service → connect your repo
3. Set environment variables in Render dashboard (see CONFIG below)
4. Start command: python support_bot.py
5. Done!

Get FREE Groq key:    https://console.groq.com → API Keys
Get bot token:        @BotFather on Telegram
Get your Telegram ID: @userinfobot on Telegram
"""

import httpx
import asyncio
import logging
import json
import os
from aiohttp import web

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# ⚙️  CONFIG
# Read from environment variables (set in Render dashboard)
# Or hardcode them here for local testing
# ─────────────────────────────────────────────
BOT_TOKEN    = os.environ.get("BOT_TOKEN",    "YOUR_BOT_TOKEN")
ADMIN_ID     = int(os.environ.get("ADMIN_ID", "123456789"))
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "YOUR_GROQ_API_KEY")
PORT         = int(os.environ.get("PORT", "8080"))  # Render sets PORT automatically

# Free Groq models:
#   llama-3.3-70b-versatile  ← best quality
#   llama3-8b-8192           ← fastest
#   mixtral-8x7b-32768       ← long conversations
AI_MODEL = "llama-3.3-70b-versatile"

SYSTEM_PROMPT = """You are a friendly customer support assistant.
Answer questions clearly and briefly.
If you cannot help, or the user asks for a human agent, reply with exactly: HANDOFF_TO_HUMAN
"""
# ─────────────────────────────────────────────

TG_BASE  = f"https://api.telegram.org/bot{BOT_TOKEN}"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

sessions: dict[int, dict] = {}
admin_reply_target: dict[int, int] = {}


def get_session(uid: int, username: str = "") -> dict:
    if uid not in sessions:
        sessions[uid] = {"mode": "ai", "history": [], "username": username or str(uid)}
    return sessions[uid]


def wants_human(text: str) -> bool:
    kw = ["human", "agent", "person", "real person", "operator", "staff", "manual", "talk to someone"]
    return any(k in text.lower() for k in kw)


# ── Telegram helpers ──────────────────────────

async def tg(client: httpx.AsyncClient, method: str, **kwargs) -> dict:
    r = await client.post(f"{TG_BASE}/{method}", json=kwargs, timeout=10)
    return r.json()


async def send(client, chat_id: int, text: str, reply_markup=None):
    kwargs = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        kwargs["reply_markup"] = json.dumps(reply_markup)
    await tg(client, "sendMessage", **kwargs)


async def send_action(client, chat_id: int):
    await tg(client, "sendChatAction", chat_id=chat_id, action="typing")


async def answer_callback(client, callback_id: str):
    await tg(client, "answerCallbackQuery", callback_query_id=callback_id)


# ── Groq AI ───────────────────────────────────

async def ask_ai(client: httpx.AsyncClient, history: list) -> str:
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": AI_MODEL,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + history,
        "max_tokens": 512,
        "temperature": 0.7,
    }
    try:
        r = await client.post(GROQ_URL, headers=headers, json=payload, timeout=30)
        data = r.json()
        log.info(f"Groq status: {r.status_code}")

        if "error" in data:
            log.error(f"Groq error: {data['error']}")
            return "HANDOFF_TO_HUMAN"

        reply = data["choices"][0]["message"]["content"].strip()
        log.info(f"AI reply: {reply[:80]}")
        return reply

    except Exception as e:
        log.error(f"Groq exception: {type(e).__name__}: {e}")
        return "HANDOFF_TO_HUMAN"


# ── Admin notification ────────────────────────

async def notify_admin(client, uid: int, username: str, text: str, reason: str = ""):
    header = (
        f"🚨 *Human requested!*\n👤 @{username} (ID: `{uid}`)\n📌 _{reason}_"
        if reason else
        f"📩 *Message from @{username}* (ID: `{uid}`)"
    )
    markup = {
        "inline_keyboard": [[
            {"text": "💬 Reply",        "callback_data": f"reply:{uid}"},
            {"text": "🤖 Re-enable AI", "callback_data": f"aion:{uid}"},
        ]]
    }
    await send(client, ADMIN_ID, f"{header}\n\n💬 {text}", reply_markup=markup)


# ── Message handler ───────────────────────────

async def handle_message(client: httpx.AsyncClient, msg: dict):
    uid      = msg["from"]["id"]
    text     = msg.get("text", "").strip()
    username = msg["from"].get("username") or msg["from"].get("first_name", str(uid))

    if not text:
        return

    if text == "/start":
        get_session(uid, username)
        await send(client, uid,
            "👋 *Welcome to Support!*\n\n"
            "I'm your AI assistant — ask me anything! 🤖\n"
            "Type *human* anytime to talk to a real person. 🙋"
        )
        return

    if uid == ADMIN_ID and text.startswith("/reply"):
        parts = text.split(None, 2)
        if len(parts) < 3:
            await send(client, ADMIN_ID, "Usage: `/reply <user_id> your message`")
            return
        try:
            target = int(parts[1])
            await send(client, target, f"👤 *Support Team:*\n{parts[2]}")
            await send(client, ADMIN_ID, f"✅ Sent to `{target}`!")
        except Exception as e:
            await send(client, ADMIN_ID, f"❌ Failed: {e}")
        return

    if uid == ADMIN_ID:
        if uid in admin_reply_target:
            target = admin_reply_target.pop(uid)
            try:
                await send(client, target, f"👤 *Support Team:*\n{text}")
                await send(client, ADMIN_ID, f"✅ Sent to `{target}`!")
            except Exception as e:
                await send(client, ADMIN_ID, f"❌ Failed: {e}")
        else:
            await send(client, ADMIN_ID,
                "ℹ️ *Admin Commands:*\n"
                "• Click *Reply* on a forwarded message, then type\n"
                "• Or: `/reply <user_id> message`"
            )
        return

    session = get_session(uid, username)

    if wants_human(text):
        session["mode"] = "human"
        await send(client, uid,
            "👤 *Connecting you to a real person...*\n"
            "Please wait — someone will reply shortly! ⏳"
        )
        await notify_admin(client, uid, username, text, reason="User requested human")
        return

    if session["mode"] == "human":
        await notify_admin(client, uid, username, text)
        await send(client, uid, "✅ Message forwarded! Please hold on...")
        return

    session["history"].append({"role": "user", "content": text})
    await send_action(client, uid)
    reply = await ask_ai(client, session["history"])

    if "HANDOFF_TO_HUMAN" in reply:
        session["mode"] = "human"
        await send(client, uid, "🤝 Forwarding you to our support team.\nSomeone will reply shortly! 💬")
        await notify_admin(client, uid, username, text, reason="AI could not answer — auto handoff")
        return

    session["history"].append({"role": "assistant", "content": reply})
    if len(session["history"]) > 20:
        session["history"] = session["history"][-20:]

    await send(client, uid, reply)


# ── Callback handler ──────────────────────────

async def handle_callback(client: httpx.AsyncClient, cb: dict):
    await answer_callback(client, cb["id"])
    uid  = cb["from"]["id"]
    data = cb["data"]

    if uid != ADMIN_ID:
        return

    action, target_str = data.split(":", 1)
    target = int(target_str)

    if action == "reply":
        admin_reply_target[uid] = target
        await send(client, ADMIN_ID, f"✏️ Type your reply for user `{target}` and send it:")

    elif action == "aion":
        if target in sessions:
            sessions[target]["mode"] = "ai"
            sessions[target]["history"] = []
        await send(client, target, "✅ Back with AI assistant! How can I help? 🤖")
        await send(client, ADMIN_ID, f"🤖 AI re-enabled for `{target}`.")


# ── Telegram polling loop ─────────────────────

async def poll():
    offset = 0
    log.info("🤖 Telegram polling started...")

    async with httpx.AsyncClient() as client:
        while True:
            try:
                r = await client.get(
                    f"{TG_BASE}/getUpdates",
                    params={"offset": offset, "timeout": 30, "allowed_updates": ["message", "callback_query"]},
                    timeout=40,
                )
                updates = r.json().get("result", [])
                for upd in updates:
                    offset = upd["update_id"] + 1
                    try:
                        if "message" in upd:
                            await handle_message(client, upd["message"])
                        elif "callback_query" in upd:
                            await handle_callback(client, upd["callback_query"])
                    except Exception as e:
                        log.error(f"Handler error: {e}")
            except Exception as e:
                log.error(f"Polling error: {e}")
                await asyncio.sleep(3)


# ── Web server (required by Render) ──────────
# Render needs an HTTP server listening on PORT
# This also acts as a health check endpoint

async def health(request):
    return web.Response(text="✅ Bot is running!", status=200)


async def main():
    # Start web server
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info(f"🌐 Web server running on port {PORT}")

    # Start bot polling
    await poll()


if __name__ == "__main__":
    asyncio.run(main())
