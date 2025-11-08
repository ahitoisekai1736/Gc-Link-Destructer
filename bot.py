"""
Fast Link-Blocker + /yo Pyrogram Bot
- Deletes link messages instantly (except OWNER, admins, whitelisted)
- /op <user>    : OWNER-only, adds whitelist and attempts promote
- /kill <user>  : OWNER/admins only, bans single user
- /whitelist    : OWNER/admins only, shows whitelist
- /yo           : OWNER-only, bans all seen non-admin members in chat
"""

import os
import re
import json
import logging
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional, Set, Dict

from pyrogram import Client, filters
from pyrogram.types import Message

# ---------- CONFIG ----------
BOT_TOKEN = os.getenv("BOT_TOKEN", "8314686515:AAE0A_li3hDGFJ7XfKh-i21aS7Tw9YvUIY4")
API_ID = int(os.getenv("API_ID", "29750116"))
API_HASH = os.getenv("API_HASH", "1cf3d0feaced3b517e82195240b5f2d0")
OWNER_ID = int(os.getenv("OWNER_ID", "7020353938"))  # your Telegram user id (int)

WHITELIST_FILE = "whitelist.json"  # persistent whitelist across restarts

# ---------- Logging ----------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- Link detection ----------
LINK_REGEX = re.compile(
    r"(https?://\S+)|(\bwww\.\S+\b)|(\bt\.me/[\w\-/]+)|(\btelegram\.me/[\w\-/]+)|invite\.link",
    flags=re.IGNORECASE,
)

# ---------- Whitelist persistence ----------
def load_whitelist() -> dict:
    try:
        with open(WHITELIST_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
            return {int(k): set(map(int, v)) for k, v in raw.items()}
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.exception("Failed to load whitelist file: %s", e)
        return {}

def save_whitelist(wl: dict):
    try:
        serial = {str(k): list(v) for k, v in wl.items()}
        with open(WHITELIST_FILE, "w", encoding="utf-8") as f:
            json.dump(serial, f, indent=2)
    except Exception as e:
        logger.exception("Failed to save whitelist: %s", e)

whitelist = load_whitelist()  # {chat_id: set(user_ids)}

def is_whitelisted(chat_id: int, user_id: int) -> bool:
    return user_id == OWNER_ID or (chat_id in whitelist and user_id in whitelist[chat_id])

def add_whitelist(chat_id: int, user_id: int):
    if chat_id not in whitelist:
        whitelist[chat_id] = set()
    whitelist[chat_id].add(user_id)
    save_whitelist(whitelist)

def remove_whitelist(chat_id: int, user_id: int):
    if chat_id in whitelist and user_id in whitelist[chat_id]:
        whitelist[chat_id].remove(user_id)
        save_whitelist(whitelist)

# ---------- App ----------
app = Client("fast_link_guard", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ---------- Helpers ----------
async def is_chat_admin(chat_id: int, user_id: int) -> bool:
    try:
        member = await app.get_chat_member(chat_id, user_id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False

async def bot_has_permissions(chat_id: int, required: dict) -> bool:
    try:
        me = await app.get_me()
        memb = await app.get_chat_member(chat_id, me.id)
        if memb.status not in ("administrator", "creator"):
            return False
        for k, v in required.items():
            if not getattr(memb, k, False) and v:
                return False
        return True
    except Exception:
        return False

async def resolve_target_user(message: Message, param: Optional[str]) -> Optional[int]:
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user.id
    if not param:
        return None
    p = param.strip()
    if re.fullmatch(r"-?\d+", p):
        return int(p)
    if p.startswith("@"):
        try:
            u = await app.get_users(p)
            return u.id
        except Exception:
            return None
    m = re.search(r"(?:t\.me|telegram\.me)/(@?\w+)", p)
    if m:
        uname = m.group(1)
        if not uname.startswith("@"):
            uname = "@" + uname
        try:
            u = await app.get_users(uname)
            return u.id
        except Exception:
            return None
    try:
        u = await app.get_users(p)
        return u.id
    except Exception:
        return None

def message_contains_link(message: Message) -> bool:
    entities = message.entities or []
    for ent in entities:
        if ent.type in ("url", "text_link"):
            return True
    text = message.text or message.caption or ""
    if text and LINK_REGEX.search(text):
        return True
    return False

# ---------- Seen members for /yo ----------
_seen_members: Dict[int, Set[int]] = {}

@app.on_message(filters.group & ~filters.me)
async def track_seen_members(_, message: Message):
    if message.from_user:
        chat_id = message.chat.id
        uid = message.from_user.id
        if chat_id not in _seen_members:
            _seen_members[chat_id] = set()
        _seen_members[chat_id].add(uid)

# ---------- Core link delete ----------
@app.on_message(filters.group & ~filters.me)
async def on_group_message(_, message: Message):
    if not message.from_user:
        return
    chat_id = message.chat.id
    sender_id = message.from_user.id
    if sender_id == OWNER_ID:
        return
    if await is_chat_admin(chat_id, sender_id):
        return
    if is_whitelisted(chat_id, sender_id):
        return
    if not message_contains_link(message):
        return
    if not await bot_has_permissions(chat_id, {"can_delete_messages": True}):
        return
    try:
        await message.delete()
    except Exception:
        pass

# ---------- OWNER-only decorator ----------
def owner_only(func):
    async def wrapper(client, message: Message):
        if message.from_user.id != OWNER_ID:
            await message.reply_text("Tera baap hi ye command use kar sakta hai chutiye")
            return
        await func(client, message)
    return wrapper

# ---------- /op ----------
@app.on_message(filters.command("op", prefixes="/") & filters.group)
@owner_only
async def cmd_op(client: Client, message: Message):
    param = " ".join(message.command[1:]) if len(message.command) > 1 else None
    target_id = await resolve_target_user(message, param)
    if not target_id:
        await message.reply_text("Usage: /op <user_id|@username> or reply to user's message")
        return
    add_whitelist(message.chat.id, target_id)
    try:
        me = await client.get_me()
        await client.promote_chat_member(
            message.chat.id,
            target_id,
            can_delete_messages=True,
            can_restrict_members=True,
            can_invite_users=True,
        )
    except Exception:
        pass
    await message.reply_text(f"User `{target_id}` added to whitelist and promoted (if bot has rights).")

# ---------- /kill ----------
@app.on_message(filters.command("kill", prefixes="/") & filters.group)
@owner_only
async def cmd_kill(client: Client, message: Message):
    param = " ".join(message.command[1:]) if len(message.command) > 1 else None
    target_id = await resolve_target_user(message, param)
    if not target_id or target_id == OWNER_ID:
        await message.reply_text("Invalid target.")
        return
    if not await bot_has_permissions(message.chat.id, {"can_restrict_members": True}):
        await message.reply_text("Bot lacks ban permissions.")
        return
    try:
        await client.kick_chat_member(message.chat.id, target_id)
        await message.reply_text(f"User `{target_id}` banned.")
    except Exception as e:
        await message.reply_text(f"Failed: {e}")

# ---------- /whitelist ----------
@app.on_message(filters.command("whitelist", prefixes="/") & filters.group)
@owner_only
async def cmd_whitelist(client: Client, message: Message):
    chat_id = message.chat.id
    users = whitelist.get(chat_id, set())
    if not users:
        await message.reply_text("No whitelisted users.")
        return
    lines = []
    for uid in users:
        try:
            u = await client.get_users(uid)
            lines.append(f"- {u.first_name or u.username or uid} (`{uid}`)")
        except Exception:
            lines.append(f"- `{uid}`")
    await message.reply_text("Whitelisted users:\n" + "\n".join(lines))

# ---------- /yo command ----------
@app.on_message(filters.command("yo", prefixes="/") & filters.group)
@owner_only
async def cmd_yo(client: Client, message: Message):
    chat_id = message.chat.id
    seen = _seen_members.get(chat_id, set())
    if not seen:
        await message.reply_text("No seen members to ban.")
        return

    # fetch admins
    admins = await client.get_chat_members(chat_id, filter="administrators")
    admin_ids = {m.user.id for m in admins}

    me = await client.get_me()
    candidates = [uid for uid in seen if uid not in admin_ids and uid != OWNER_ID and uid != me.id]

    if not candidates:
        await message.reply_text("No non-admin seen members to ban.")
        return

    banned = 0
    failed = []
    for uid in candidates:
        try:
            await client.kick_chat_member(chat_id, uid)
            banned += 1
        except Exception as e:
            failed.append((uid, str(e)))

    for uid in candidates:
        _seen_members[chat_id].discard(uid)

    res_msg = f"YO complete. Banned: {banned}."
    if failed:
        res_msg += f" Failed for {len(failed)} users."
    await message.reply_text(res_msg)

# ---------- Run ----------
if __name__ == "__main__":
    print("Starting Fast Link Guard + YO Bot...")
    if "REPLACE_WITH_BOT_TOKEN" in BOT_TOKEN:
        print("Please set BOT_TOKEN, API_ID, API_HASH, OWNER_ID properly.")
    app.run()
