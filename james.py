import os
import sqlite3
import re
import asyncio
import time
import logging
import aiohttp
from aiohttp import web
import csv
import zipfile
import shutil
import html
import json
from datetime import datetime
from urllib.parse import quote, urlparse

from telethon import TelegramClient, events, Button as TelegramButton
from telethon.errors import (
    SessionPasswordNeededError, 
    MessageNotModifiedError,
    FloodWaitError,
    UserNotParticipantError,
    ChatAdminRequiredError
)
from telethon.tl.types import ReplyKeyboardMarkup, KeyboardButtonRow, KeyboardButton, InputPhoto
from telethon.tl.functions.channels import GetParticipantRequest
from telethon.tl.functions.account import GetPasswordRequest

# ================= COLORFUL BUTTON SYSTEM =================
# Telegram does not expose button background colors to bots. We mirror the
# reference bot's visual logic with clear color-coded emoji prefixes:
# blue = navigation/info, green = actions/confirmation, red = cancel/destructive.
BUTTON_COLOR_PREFIXES = {
    "primary": "🔵",
    "success": "🟢",
    "danger": "🔴",
}
BUTTON_DANGER_WORDS = (
    "cancel", "reject", "delete", "remove", "stop", "disable", "logout",
    "close", "ban", "clear", "no", "back",
)
BUTTON_SUCCESS_WORDS = (
    "accept", "confirm", "approve", "buy", "add", "deposit", "join",
    "verify", "submit", "pay", "enable", "next", "save", "start", "retry",
)

def colorize_button_text(text, style=None):
    """Add a consistent visual color marker without changing button actions."""
    value = str(text)
    if value.startswith(tuple(BUTTON_COLOR_PREFIXES.values())):
        return value
    lowered = value.lower()
    if style is None:
        if any(re.search(rf"\b{re.escape(word)}\b", lowered) for word in BUTTON_DANGER_WORDS):
            style = "danger"
        elif any(re.search(rf"\b{re.escape(word)}\b", lowered) for word in BUTTON_SUCCESS_WORDS):
            style = "success"
        else:
            style = "primary"
    return f"{BUTTON_COLOR_PREFIXES.get(style, BUTTON_COLOR_PREFIXES['primary'])} {value}"

class ColorButtonFactory:
    """Drop-in Button facade so every inline/URL button gets a color marker."""

    @staticmethod
    def inline(text, data, *args, **kwargs):
        return TelegramButton.inline(colorize_button_text(text), data, *args, **kwargs)

    @staticmethod
    def url(text, url, *args, **kwargs):
        return TelegramButton.url(colorize_button_text(text), url, *args, **kwargs)

Button = ColorButtonFactory

# ================= CONFIGURATION =================
def load_env_file(path=".env"):
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as env_file:
            for raw_line in env_file:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                os.environ.setdefault(key, value)
    except Exception as ex:
        print(f"Failed to load {path}: {ex}")

load_env_file()

def env_int(name, default=0):
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        return default

def env_list(name, default_csv=""):
    raw = os.getenv(name, default_csv)
    return [item.strip() for item in raw.split(",") if item.strip()]

API_ID = env_int("API_ID")
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# ========== BOTH ADMINS ==========
ADMIN_ID = env_int("ADMIN_ID")
ADMIN_IDS = [ADMIN_ID] if ADMIN_ID else []

# ========== CHANNELS ==========
LOG_CHANNEL_ID = env_int("LOG_CHANNEL_ID", -1004359100536)
REQUIRED_CHANNELS = env_list("REQUIRED_CHANNELS", os.getenv("CHECK_CHANNELS", "-1004359100536"))
JOIN_URLS = env_list("JOIN_URLS", "https://t.me/moviesmasterupdates")

# ========== LINKS & MEDIA ==========
DEFAULT_TERMS_URL = "https://dhruvkumarray3-eng.github.io/Terms-And-Conditions/Dhruv.html"
TERMS_URL = os.getenv("TERMS_URL", DEFAULT_TERMS_URL)

# ========== UPI DETAILS ==========
UPI_ID = os.getenv("UPI_ID", "bobbyahirwar@fam")
UPI_QR = os.getenv("UPI_QR", "https://files.catbox.moe/m5c01u.jpg")

# ========== CWALLET DETAILS ==========
CWALLET_QR = os.getenv("CWALLET_QR", "https://files.catbox.moe/m5c01u.jpg")
CWALLET_ID = os.getenv("CWALLET_ID", "your_cwallet_id_here")

# ========== SUPPORT CONTACTS ==========
SUPPORT_USERNAME_1 = os.getenv("SUPPORT_USERNAME_1", "Your_cuteexd").strip().lstrip("@")
SUPPORT_USERNAME_2 = os.getenv("SUPPORT_USERNAME_2", "Know_Your_Papa").strip().lstrip("@")

OTP_REGEX = os.getenv("OTP_REGEX", r"\b\d{4,8}\b")
AUTO_CANCEL_SECONDS = env_int("AUTO_CANCEL_SECONDS", 600)
DEFAULT_USDT_RATE = os.getenv("DEFAULT_USDT_RATE", "94.0")
DEFAULT_SUPPORT_URL = os.getenv("DEFAULT_SUPPORT_URL", "https://t.me/tgtelehelpbot")

# ================= PREMIUM EMOJIS =================
# Premium emoji rendering is on by default. It remains configurable for
# deployments whose bot account cannot render the configured custom IDs.
USE_PREMIUM_EMOJIS = os.getenv("USE_PREMIUM_EMOJIS", "1").strip().lower() not in {"0", "false", "no", "off"}
PREMIUM_EMOJIS = {
    "heart_fire": os.getenv("PREMIUM_EMOJI_HEART_FIRE", "5042225965518816316"),
    "lightning": os.getenv("PREMIUM_EMOJI_LIGHTNING", "5042334757040423886"),
    "location": os.getenv("PREMIUM_EMOJI_LOCATION", "5039775669496579510"),
    "flower": os.getenv("PREMIUM_EMOJI_FLOWER", "6073117703965511893"),
    "check": os.getenv("PREMIUM_EMOJI_CHECK", "6147460667281511517"),
    "crown": os.getenv("PREMIUM_EMOJI_CROWN", "6235252066554484059"),
    "kiss": os.getenv("PREMIUM_EMOJI_KISS", "6116282026506065674"),
    "skull": os.getenv("PREMIUM_EMOJI_SKULL", "6089128873893563936"),
    "xmas": os.getenv("PREMIUM_EMOJI_XMAS", "6267071898702583835"),
    "monkey": os.getenv("PREMIUM_EMOJI_MONKEY", "6273627839862411998"),
    "gift": os.getenv("PREMIUM_EMOJI_GIFT", "5893175870096414393"),
    "angel": os.getenv("PREMIUM_EMOJI_ANGEL", "5893411041030707544"),
    "devil": os.getenv("PREMIUM_EMOJI_DEVIL", "5893079628469246474"),
}

def tg_emoji(name, fallback):
    emoji_id = PREMIUM_EMOJIS.get(name)
    if USE_PREMIUM_EMOJIS and emoji_id:
        return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'
    return fallback

PE_HEART = tg_emoji("heart_fire", "❤️‍🔥")
PE_LIGHTNING = tg_emoji("lightning", "⚡")
PE_LOCATION = tg_emoji("location", "📍")
PE_FLOWER = tg_emoji("flower", "🌸")
PE_CHECK = tg_emoji("check", "✅")
PE_CROWN = tg_emoji("crown", "👑")
PE_KISS = tg_emoji("kiss", "😘")
PE_SKULL = tg_emoji("skull", "💀")
PE_XMAS = tg_emoji("xmas", "🎄")
PE_MONKEY = tg_emoji("monkey", "🐵")
PE_GIFT = tg_emoji("gift", "🎁")
PE_ANGEL = tg_emoji("angel", "😇")
PE_DEVIL = tg_emoji("devil", "😈")

# ================= UI ICONS =================
P_YES = PE_CHECK
P_NO = '❌'
P_PKG = '📦'
P_MONEY = '💰'
P_USDT = '💲'
P_INR = '₹'
P_TG = '✈️'
P_GIFT = PE_GIFT
P_STATS = '📊'
P_CARD = '💳'
P_USERS = '👥'
P_CAL = '📅'
P_PC = '💻'
P_EYE = '👁️'
P_UPI = '🏦'
P_CW = '👛'
P_ON = '🟢'
P_OFF = '🔴'
P_ID = '🆔'
P_KEY = '⌨️'
P_GLOBE = PE_LOCATION
P_CART = '🛒'
P_STORE = '🏬'
P_OTP = '🔢'
P_2FA = '🔐'
P_FLAG = '🏳️'
P_PHONE = '📱'
P_WAIT = '⏳'
P_TIME = '⏰'
P_WARN = '⚠️'
P_DOC = '📃'
P_SOS = '🆘'
P_ASST = '🤖'
P_ACC = '👤'
P_SCREEN = '🖼️'
P_UTR = '🧾'

# ========== URL HELPER ==========
def fix_url(url, fallback=None):
    """Return a Telegram-safe HTTP(S) URL or a known-good fallback."""
    fallback = DEFAULT_SUPPORT_URL if fallback is None else fallback
    value = str(url or "").strip()
    if not value:
        return fallback
    if value.startswith("@"):
        value = "https://t.me/" + value[1:]
    elif value.startswith("t.me/"):
        value = "https://" + value
    elif not value.startswith(("http://", "https://")):
        value = "https://" + value
    parsed = urlparse(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or not parsed.hostname
        or "." not in parsed.hostname
        or any(char.isspace() for char in value)
    ):
        return fallback
    return value

# ========== VALIDATE CONFIG ==========
def validate_config():
    missing = []
    # Telegram credentials and the primary administrator are required to start.
    if API_ID <= 0:
        missing.append("API_ID")
    if not API_HASH:
        missing.append("API_HASH")
    if not BOT_TOKEN or ":" not in BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if not ADMIN_IDS:
        missing.append("ADMIN_ID")

    # Premium emoji IDs are only needed when premium emoji rendering is enabled.
    if USE_PREMIUM_EMOJIS:
        for name, value in PREMIUM_EMOJIS.items():
            if not value:
                missing.append(f"PREMIUM_EMOJI_{name.upper()}")
    if missing:
        raise RuntimeError("Missing/invalid environment variables: " + ", ".join(missing))
    if not REQUIRED_CHANNELS:
        logger.warning("REQUIRED_CHANNELS is empty; join verification will be ineffective.")
    if not JOIN_URLS:
        logger.warning("JOIN_URLS is empty; users will not see join buttons.")
    if REQUIRED_CHANNELS and JOIN_URLS and len(REQUIRED_CHANNELS) != len(JOIN_URLS):
        logger.warning(
            "REQUIRED_CHANNELS (%s) and JOIN_URLS (%s) lengths differ.",
            len(REQUIRED_CHANNELS), len(JOIN_URLS)
        )

# ================= INITIALIZATION =================
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

validate_config()

os.makedirs("sessions", exist_ok=True)
os.makedirs("screenshots", exist_ok=True)

session_name = f"bot_session_{BOT_TOKEN.split(':')[0]}"
bot = TelegramClient(session_name, API_ID, API_HASH)
bot.parse_mode = 'html'

db = sqlite3.connect("otp_bot_final.db", check_same_thread=False, timeout=20)
db.execute("PRAGMA journal_mode=WAL;")
cur = db.cursor()

active_orders = {}      
waiting_proof = {}      
deposit_input = {} 
admin_dep_state = {}    
admin_content_state = {}
admin_user_state = {}
user_spam_cooldown = {} 
session_buy_state = {}  
account_product_state = {}
custom_dep_amt = {}     
pending_utr = {}        
broadcast_drafts = {}
broadcast_jobs = {}

user_locks = {}

def get_user_lock(uid):
    if uid not in user_locks:
        user_locks[uid] = asyncio.Lock()
    return user_locks[uid]

# ================= DATABASE SCHEMA =================
def setup_db():
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        balance INTEGER DEFAULT 0,
        referred_by INTEGER,
        total_deposited INTEGER DEFAULT 0,
        joined_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        banned INTEGER DEFAULT 0,
        discount INTEGER DEFAULT 0,
        terms_accepted INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE IF NOT EXISTS stock (
        phone TEXT PRIMARY KEY,
        session_file TEXT,
        country_name TEXT,
        country_icon TEXT DEFAULT '🌍',
        account_year INTEGER,
        category TEXT DEFAULT 'Good',
        price INTEGER,
        available INTEGER DEFAULT 1,
        twofa TEXT DEFAULT 'None',
        added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS auto_prices (
        country TEXT,
        year TEXT,
        price INTEGER,
        PRIMARY KEY (country, year)
    );
    CREATE TABLE IF NOT EXISTS deposits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        amount INTEGER,
        method_name TEXT,
        status TEXT, 
        date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        screenshot TEXT,
        utr TEXT
    );
    CREATE TABLE IF NOT EXISTS upi_orders (
        order_id TEXT PRIMARY KEY,
        user_id INTEGER,
        amount INTEGER,
        status TEXT,
        date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        country TEXT,
        year INTEGER,
        price INTEGER,
        phone TEXT,
        otp TEXT,
        date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS custom_payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        caption TEXT,
        qr_file_id TEXT
    );
    CREATE TABLE IF NOT EXISTS admins (
        user_id INTEGER PRIMARY KEY,
        p_add_stock INTEGER DEFAULT 0,
        p_manage_stock INTEGER DEFAULT 0,
        p_stats INTEGER DEFAULT 0,
        p_bal INTEGER DEFAULT 0,
        p_settings INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS custom_countries (
        code TEXT PRIMARY KEY,
        name TEXT,
        flag TEXT
    );
    """)
    db.commit()

# ========== FIX: Update existing database ==========
def update_database_schema():
    """Add missing columns to deposits table"""
    try:
        cur.execute("ALTER TABLE deposits ADD COLUMN screenshot TEXT")
        db.commit()
        logger.info("✅ Added screenshot column to deposits")
    except sqlite3.OperationalError:
        pass  # Column already exists
    
    try:
        cur.execute("ALTER TABLE deposits ADD COLUMN utr TEXT")
        db.commit()
        logger.info("✅ Added utr column to deposits")
    except sqlite3.OperationalError:
        pass  # Column already exists

setup_db()
update_database_schema()

# ================= HELPER FUNCTIONS =================
def is_bot_online():
    res = cur.execute("SELECT value FROM settings WHERE key='bot_status'").fetchone()
    return res[0] == 'on' if res else True

def is_maintenance_mode():
    return get_setting("maintenance_enabled", "off") == "on"

def get_maintenance_message():
    return get_setting(
        "maintenance_message",
        "🛠 <b>Maintenance Mode</b>\n\nPlease try again later."
    )

def is_admin(uid):
    if uid in ADMIN_IDS:
        return True
    row = cur.execute("SELECT user_id FROM admins WHERE user_id=?", (uid,)).fetchone()
    return bool(row)

def has_perm(uid, perm):
    if uid in ADMIN_IDS:
        return True
    row = cur.execute(f"SELECT {perm} FROM admins WHERE user_id=?", (uid,)).fetchone()
    return bool(row and row[0] == 1)

def ensure_user(uid):
    cur.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (uid,))
    db.commit()

def get_usdt_rate():
    res = cur.execute("SELECT value FROM settings WHERE key='usdt_rate'").fetchone()
    try: return float(res[0]) if res else float(DEFAULT_USDT_RATE)
    except: return float(DEFAULT_USDT_RATE)

def get_auto_cancel_seconds():
    try:
        value = int(get_setting("auto_cancel_seconds", str(AUTO_CANCEL_SECONDS)))
        return value if value >= 1 else AUTO_CANCEL_SECONDS
    except (TypeError, ValueError):
        return AUTO_CANCEL_SECONDS

def get_terms_url():
    return fix_url(get_setting("terms_url", TERMS_URL), DEFAULT_TERMS_URL)

def get_support_url():
    res = cur.execute("SELECT value FROM settings WHERE key='support_url'").fetchone()
    url = res[0] if res and res[0] else DEFAULT_SUPPORT_URL
    return fix_url(url, DEFAULT_SUPPORT_URL)

def get_channel_links():
    """Return the current public channel links, with an admin-editable override."""
    raw = get_setting("channel_links")
    values = JOIN_URLS if raw is None else raw.split(",")
    links = []
    for item in values:
        link = fix_url(item, fallback="")
        if link:
            links.append(link)
    return links

def get_setting(key, default=None):
    row = cur.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default

def set_setting(key, value):
    cur.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    db.commit()

def delete_setting(key):
    cur.execute("DELETE FROM settings WHERE key=?", (key,))
    db.commit()

async def send_broadcast_content(recipient_id, draft):
    message = draft["message"]
    kwargs = {"parse_mode": "html"}
    if message.media:
        kwargs["file"] = message.media
        return await bot.send_message(recipient_id, draft["caption"], buttons=draft["buttons"], **kwargs)
    return await bot.send_message(recipient_id, draft["text"], buttons=draft["buttons"], **kwargs)

async def send_broadcast_preview(admin_id, draft):
    message = draft["message"]
    buttons = [
        [Button.inline("✅ Confirm Send", f"adm_bcast_confirm|{admin_id}"), Button.inline("❌ Cancel", f"adm_bcast_cancel|{admin_id}")]
    ]
    if message.media:
        return await bot.send_message(admin_id, draft["caption"], file=message.media, buttons=buttons, parse_mode="html")
    return await bot.send_message(admin_id, draft["text"], buttons=buttons, parse_mode="html")

async def run_broadcast(admin_id, chat_id, draft):
    users = cur.execute("SELECT user_id FROM users").fetchall()
    total = len(users)
    sent = failed = blocked = 0
    progress_message = await bot.send_message(
        chat_id,
        f"{P_TG} <b>Broadcast in progress...</b>\n\n👥 Total: {total}\n✅ Sent: 0\n❌ Failed: 0\n🚫 Blocked/Deactivated: 0\n⏳ Remaining: {total}",
        buttons=[[Button.inline("🛑 Cancel Broadcast", f"adm_bcast_cancel|{admin_id}")]]
    )
    job = {"cancelled": False, "progress_message": progress_message}
    broadcast_jobs[admin_id] = job
    last_update = 0.0
    try:
        for index, (user_id,) in enumerate(users, start=1):
            if job["cancelled"]:
                break
            try:
                await send_broadcast_content(int(user_id), draft)
                sent += 1
            except FloodWaitError as error:
                await asyncio.sleep(error.seconds)
                try:
                    await send_broadcast_content(int(user_id), draft)
                    sent += 1
                except Exception as retry_error:
                    failed += 1
                    if retry_error.__class__.__name__ in {"UserBlockedError", "PeerIdInvalidError", "ChatWriteForbiddenError"}:
                        blocked += 1
            except Exception as error:
                failed += 1
                if error.__class__.__name__ in {"UserBlockedError", "PeerIdInvalidError", "ChatWriteForbiddenError"}:
                    blocked += 1

            now = time.monotonic()
            if now - last_update >= 2 or index == total:
                last_update = now
                try:
                    await progress_message.edit(
                        f"{P_TG} <b>Broadcast in progress...</b>\n\n👥 Total: {total}\n✅ Sent: {sent}\n❌ Failed: {failed}\n🚫 Blocked/Deactivated: {blocked}\n⏳ Remaining: {total - index}",
                        buttons=[[Button.inline("🛑 Cancel Broadcast", f"adm_bcast_cancel|{admin_id}")]]
                    )
                except Exception:
                    pass
            await asyncio.sleep(0.1)
    finally:
        broadcast_jobs.pop(admin_id, None)
        broadcast_drafts.pop(admin_id, None)
        cancelled = job["cancelled"]
        title = "Broadcast Cancelled" if cancelled else "Broadcast Complete"
        await bot.send_message(
            chat_id,
            f"{P_TG} <b>{title}</b>\n\n👥 Total: {total}\n✅ Sent: {sent}\n❌ Failed: {failed}\n🚫 Blocked/Deactivated: {blocked}",
            buttons=[[Button.inline("◀️ Back", "adm_adminmain")]]
        )

def get_default_welcome(uid, pct, bot_username):
    ref_line = (
        f"{P_GLOBE} <code>https://t.me/{bot_username}?start=ref_{uid}</code>"
        if bot_username else
        f"{P_GLOBE} <i>Set a public bot username to enable referral links.</i>"
    )
    balance_row = cur.execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
    balance = balance_row[0] if balance_row else 0
    return (f"{PE_HEART} <b>Welcome to hiddenleafXvillage!</b>\n\n"
            f"{PE_GIFT} <b>Premium services:</b> Buy accounts, sessions, and top up instantly.\n"
            f"{P_GIFT} <b>Refer & Earn:</b>\nInvite friends and earn {pct}% of their deposits!\n"
            f"{ref_line}\n\n"
            f"{P_MONEY} <b>Balance:</b> {P_INR}{balance}\n\n"
            f"👨‍💻 <b>Developer:</b>\n@{SUPPORT_USERNAME_1} & @{SUPPORT_USERNAME_2}")

def get_welcome_message(uid, pct, bot_username):
    saved = get_setting("welcome_message")
    return saved if saved is not None else get_default_welcome(uid, pct, bot_username)

def get_banner_media():
    raw = get_setting("banner_photo")
    return get_banner_reference(raw)

def get_banner_reference(raw):
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return InputPhoto(
            id=int(data["id"]),
            access_hash=int(data["access_hash"]),
            file_reference=bytes.fromhex(data["file_reference"])
        )
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None

STORE_DEFAULT_MESSAGES = {
    "single": (
        "🛒 <b>ACCOUNT STORE</b>\n━━━━━━━━━━━━━━━━━━\n"
        "⚡ Select an account below to view details.\n\n"
        "💱 Rate: 1 USDT = ₹{rate}\n📦 Available: {available} accounts\n\n"
        "━━━━━━━━━━━━━━━━━━\n{products}{page}"
    ),
    "bulk": (
        "🔐 <b>SESSIONS STORE</b>\n━━━━━━━━━━━━━━━━━━\n"
        "⚡ Select a session package below.\n\n"
        "💱 Rate: 1 USDT = ₹{rate}\n📦 Available: {available} sessions\n\n"
        "━━━━━━━━━━━━━━━━━━\n{products}{page}"
    )
}

STORE_DEFAULT_BUTTONS = {
    "single": {"product": "{icon} {country}", "buy": "🛒 Buy Now", "back": "⬅️ Back to Products", "previous": "⬅️ Previous", "next": "Next ➡️", "cancel": "❌ Cancel", "page": "Page {page}/{total_pages}"},
    "bulk": {"product": "{icon} {country}", "quantity": "Quantity", "confirm": "✅ Confirm", "change_quantity": "✏️ Change Quantity", "back": "⬅️ Back to Products", "previous": "⬅️ Previous", "next": "Next ➡️", "cancel": "❌ Cancel", "page": "Page {page}/{total_pages}"}
}

def get_store_message(flow):
    return get_setting(f"{'account' if flow == 'single' else 'sessions'}_store_message", STORE_DEFAULT_MESSAGES[flow])

def get_store_buttons(flow):
    key = "account" if flow == "single" else "sessions"
    raw = get_setting(f"{key}_button_labels")
    try:
        labels = json.loads(raw) if raw else {}
        return {**STORE_DEFAULT_BUTTONS[flow], **labels}
    except (TypeError, ValueError, json.JSONDecodeError):
        return STORE_DEFAULT_BUTTONS[flow].copy()

def store_banner_key(flow):
    return "account_store_banner" if flow == "single" else "sessions_store_banner"

def to_usd(inr):
    return round(inr / get_usdt_rate(), 2)

def is_user_banned(uid):
    res = cur.execute("SELECT banned FROM users WHERE user_id=?", (uid,)).fetchone()
    return res and res[0] == 1

def update_balance(uid, amount):
    cur.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, uid))
    db.commit()

def delete_session_files(session_path):
    base = session_path if not session_path.endswith('.session') else session_path[:-8]
    for ext in ['.session', '.session-wal', '.session-shm', '.session-journal']:
        try:
            if os.path.exists(base + ext): os.remove(base + ext)
        except: pass

async def check_channel_joined(uid):
    if is_admin(uid): return True
    for ch in REQUIRED_CHANNELS:
        try:
            ch_id = int(ch.strip()) if str(ch).strip().lstrip('-').isdigit() else ch.strip()
            try:
                await bot(GetParticipantRequest(channel=ch_id, participant=uid))
            except ValueError:
                entity = await bot.get_entity(ch_id)
                await bot(GetParticipantRequest(channel=entity, participant=uid))
        except UserNotParticipantError:
            return False
        except ChatAdminRequiredError:
            logger.error(f"Bot is not admin in channel: {ch}")
            return False
        except Exception as e:
            logger.error(f"Channel Check Error for {ch}: {e}")
            return False
    return True

COUNTRY_CODES = {
    '1': ('USA/Canada', '🇺🇸'), '7': ('Russia', '🇷🇺'), '20': ('Egypt', '🇪🇬'),
    '27': ('South Africa', '🇿🇦'), '31': ('Netherlands', '🇳🇱'), '32': ('Belgium', '🇧🇪'),
    '33': ('France', '🇫🇷'), '34': ('Spain', '🇪🇸'), '39': ('Italy', '🇮🇹'), 
    '44': ('UK', '🇬🇧'), '46': ('Sweden', '🇸🇪'), '48': ('Poland', '🇵🇱'),
    '49': ('Germany', '🇩🇪'), '51': ('Peru', '🇵🇪'), '52': ('Mexico', '🇲🇽'),
    '54': ('Argentina', '🇦🇷'), '55': ('Brazil', '🇧🇷'), '56': ('Chile', '🇨🇱'),
    '57': ('Colombia', '🇨🇴'), '58': ('Venezuela', '🇻🇪'), '60': ('Malaysia', '🇲🇾'),
    '61': ('Australia', '🇦🇺'), '62': ('Indonesia', '🇮🇩'), '63': ('Philippines', '🇵🇭'), 
    '66': ('Thailand', '🇹🇭'), '84': ('Vietnam', '🇻🇳'), '86': ('China', '🇨🇳'), 
    '90': ('Turkey', '🇹🇷'), '91': ('India', '🇮🇳'), '92': ('Pakistan', '🇵🇰'), 
    '93': ('Afghanistan', '🇦🇫'), '94': ('Sri Lanka', '🇱🇰'), '95': ('Myanmar', '🇲🇲'),
    '98': ('Iran', '🇮🇷'), '212': ('Morocco', '🇲🇦'), '213': ('Algeria', '🇩🇿'),
    '234': ('Nigeria', '🇳🇬'), '254': ('Kenya', '🇰🇪'), '255': ('Tanzania', '🇹🇿'),
    '380': ('Ukraine', '🇺🇦'), '880': ('Bangladesh', '🇧🇩'), '964': ('Iraq', '🇮🇶'),
    '966': ('Saudi Arabia', '🇸🇦'), '971': ('UAE', '🇦🇪'), '998': ('Uzbekistan', '🇺🇿')
}

def get_flag_by_country_name(name):
    for code, (c_name, c_flag) in COUNTRY_CODES.items():
        if c_name == name: return c_flag
    try:
        row = cur.execute("SELECT flag FROM custom_countries WHERE name=?", (name,)).fetchone()
        if row: return row[0]
    except: pass
    return "🌍"

def get_country_info(phone):
    phone = str(phone).replace(' ', '').replace('+', '')
    if not phone: return "Unknown", "🌍"
    
    try:
        customs = cur.execute("SELECT code, name, flag FROM custom_countries").fetchall()
        customs.sort(key=lambda x: len(x[0]), reverse=True)
        for code, name, flag in customs:
            if phone.startswith(code): return name, flag
    except: pass

    for length in (3, 2, 1):
        prefix = phone[:length]
        if prefix in COUNTRY_CODES: return COUNTRY_CODES[prefix]
    return "Unknown", "🌍"

async def detect_account_year(client):
    year = 2024
    try:
        try: await client.delete_dialog('TGDNAbot')
        except: pass
        await client.send_message('TGDNAbot', '/start')
        me = await client.get_me()
        await asyncio.sleep(1)
        await client.send_message('TGDNAbot', str(me.id)) 
        for _ in range(8):
            await asyncio.sleep(1.5)
            msgs = await client.get_messages('TGDNAbot', limit=3)
            for m in msgs:
                if m.text and ('Created:' in m.text or 'Age:' in m.text or 'Registration' in m.text):
                    match = re.search(r'(?:Created|Age|Registration)[^\d]*(\d{4})', m.text, re.IGNORECASE)
                    if match: return int(match.group(1))
    except Exception: pass
    return year

# ================= LOGGING LOGIC =================
async def process_referral_bonus(uid, amount):
    row = cur.execute("SELECT referred_by FROM users WHERE user_id=?", (uid,)).fetchone()
    ref = row[0] if row else None
    if ref:
        pct_row = cur.execute("SELECT value FROM settings WHERE key='ref_percent'").fetchone()
        pct = float(pct_row[0]) if pct_row else 3.0
        if pct > 0:
            bonus = int(amount * (pct / 100))
            if bonus > 0:
                update_balance(ref, bonus)
                try:
                    await bot.send_message(
                        ref,
                        f"{PE_GIFT} <b>Referral Bonus Unlocked!</b>\n"
                        f"{PE_HEART} Your referral <code>{uid}</code> deposited {P_INR}{amount}.\n"
                        f"{PE_CHECK} You earned <b>{P_INR}{bonus}</b>!"
                    )
                except:
                    pass

async def log_primary_deposit(uid, amt, method):
    try:
        try:
            user = await bot.get_entity(int(uid))
            username = html.escape(user.username) if user.username else "NoUsername"
        except:
            username = "NoUsername"
        t = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        msg = (f"{PE_GIFT} <b>NEW DEPOSIT SUCCESSFUL</b>\n\n"
               f"{P_ACC} Uꜱᴇʀ ID: <code>{uid}</code>\n"
               f"👤 Uꜱᴇʀɴᴀᴍᴇ: @{username}\n"
               f"{P_MONEY} Aᴍᴏᴜɴᴛ: {P_INR}{amt}\n"
               f"{P_CARD} Mᴇᴛʜᴏᴅ: {method}\n"
               f"{P_TIME} Tɪᴍᴇ: {t}\n\n"
               f"<i>{PE_HEART} Thanks for depositing in Fresh Tg!</i>")
        try: await bot.send_message(LOG_CHANNEL_ID, msg)
        except Exception as e: logger.error(f"Failed Log: {e}")
    except Exception as e: logger.error(f"Global Dep Log Err: {e}")

async def log_primary_purchase(uid, country, price, amount, year, qty):
    try:
        t = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        msg = (f"{PE_LIGHTNING} <b>NEW PURCHASE SUCCESSFUL</b>\n\n"
               f"{P_ID} Uꜱᴇʀ Iᴅ: <code>{uid}</code>\n"
               f"{P_GLOBE} Cᴏᴜɴᴛʀʏ: {country}\n"
               f"{P_MONEY} Pʀɪᴄᴇ: {P_INR}{price}\n"
               f"{P_CARD} Tᴏᴛᴀʟ Pᴀɪᴅ: {P_INR}{amount}\n"
               f"{P_CAL} Yᴇᴀʀ: {year}\n"
               f"{P_PKG} Qᴜᴀɴᴛɪᴛʏ: {qty}\n"
               f"{P_TIME} Tɪᴍᴇ: {t}")
        try: await bot.send_message(LOG_CHANNEL_ID, msg)
        except: pass
    except Exception as e: logger.error(f"Pur Log Err: {e}")

# ================= MENU HELPERS =================
def get_persistent_menu(uid):
    # Match the supplied seller-bot layout while keeping labels mapped to
    # real actions or an explicit not-configured response.
    rows = [
        [KeyboardButton(colorize_button_text("🛒 Buy Account", "success")),
         KeyboardButton(colorize_button_text("💳 Deposit", "primary"))],
        [KeyboardButton(colorize_button_text("📁 Bot Repos", "primary")),
         KeyboardButton(colorize_button_text("📣 SMM Services", "success"))],
        [KeyboardButton(colorize_button_text("✅ Profile", "primary")),
         KeyboardButton(colorize_button_text("📦 My Orders", "primary"))],
        [KeyboardButton(colorize_button_text("💰 Balance", "success")),
         KeyboardButton(colorize_button_text("🏆 Stock", "primary"))],
        [KeyboardButton(colorize_button_text("👾 Refer", "success")),
         KeyboardButton(colorize_button_text("💲 Support", "primary"))],
        [KeyboardButton(colorize_button_text("🏠 Start", "success"))]
    ]
    if is_admin(uid):
        rows.append([KeyboardButton(colorize_button_text("🔐 Admin Panel", "danger"))])
    return ReplyKeyboardMarkup([KeyboardButtonRow(r) for r in rows], resize=True)

def get_terms_buttons():
    return [
        [Button.url("📜 Read Terms & Conditions", get_terms_url())],
        [Button.inline("✅ Accept & Continue", "tc_accept"), Button.inline("❌ Reject", "tc_reject")]
    ]

def get_support_buttons():
    buttons = [
        [Button.url(f"📩 @{SUPPORT_USERNAME_1}", f"https://t.me/{SUPPORT_USERNAME_1}")],
        [Button.url(f"📩 @{SUPPORT_USERNAME_2}", f"https://t.me/{SUPPORT_USERNAME_2}")],
        [Button.url("📜 Terms & Conditions", get_terms_url())]
    ]
    channel_links = get_channel_links()
    if channel_links and channel_links[0]:
        try:
            buttons.append([Button.url("📢 Official Channel", channel_links[0])])
        except Exception:
            pass
    return buttons

def get_join_buttons():
    buttons = []
    for i, link in enumerate(get_channel_links()):
        if link:
            try:
                buttons.append([Button.url(f"📢 Join Channel {i+1}", link)])
            except Exception:
                pass
    buttons.append([Button.inline("✅ I've Joined — Verify", "verify_join")])
    return buttons

async def send_main_menu(event, uid):
    me = await bot.get_me()
    pct_row = cur.execute("SELECT value FROM settings WHERE key='ref_percent'").fetchone()
    pct = pct_row[0] if pct_row else "3"
    bot_username = me.username or ""
    msg = get_welcome_message(uid, pct, bot_username)
    banner = get_banner_media() if get_setting("images_enabled", "off") == "on" else None
    menu = get_persistent_menu(uid)
    
    if isinstance(event, events.CallbackQuery.Event):
        try: await event.delete()
        except: pass
        if banner:
            try:
                await bot.send_file(uid, banner, caption=msg, buttons=menu)
                return
            except Exception:
                pass
        await bot.send_message(uid, msg, buttons=menu)
    else:
        if banner:
            try:
                await bot.send_file(uid, banner, caption=msg, buttons=menu)
                return
            except Exception:
                pass
        await event.respond(msg, buttons=menu)

# ================= DEPOSIT HANDLERS =================
def format_payment_buttons(buttons):
    n = len(buttons)
    res = []
    for i in range(0, n, 2): res.append(buttons[i:i+2])
    return res

async def deposit_menu(event):
    msg = (f"{P_CARD} <b>Select Payment Method:</b>\n\n"
           f"{PE_LIGHTNING} Choose Automatic for instant credit.\n"
           f"{P_WAIT} Choose Manual for other methods.\n"
           f"{PE_GIFT} Cwallet gives <b>+5% bonus</b>!")
    
    flat_buttons = [
        Button.inline("⚡ UPI Automatic", "dep_upi"),
        Button.inline(f"👛 Cwallet (+5%)", "depm_Cwallet")
    ]
    
    customs = cur.execute("SELECT name FROM custom_payments").fetchall()
    for c in customs:
        flat_buttons.append(Button.inline(f"💳 {c[0]}", f"depm_{c[0]}"))
    
    btns = format_payment_buttons(flat_buttons)
    await bot.send_message(event.chat_id, msg, buttons=btns)

def get_keypad():
    return [
        [Button.inline("1", "kp_1"), Button.inline("2", "kp_2"), Button.inline("3", "kp_3")],
        [Button.inline("4", "kp_4"), Button.inline("5", "kp_5"), Button.inline("6", "kp_6")],
        [Button.inline("7", "kp_7"), Button.inline("8", "kp_8"), Button.inline("9", "kp_9")],
        [Button.inline("🔙 Del", "kp_del"), Button.inline("0", "kp_0"), Button.inline("✅ Confirm", "kp_done")],
        [Button.inline("❌ Cancel", "cancel_action")]
    ]

def get_admin_custom_keypad(dep_id):
    return [
        [Button.inline("1", f"dkp|{dep_id}|1"), Button.inline("2", f"dkp|{dep_id}|2"), Button.inline("3", f"dkp|{dep_id}|3")],
        [Button.inline("4", f"dkp|{dep_id}|4"), Button.inline("5", f"dkp|{dep_id}|5"), Button.inline("6", f"dkp|{dep_id}|6")],
        [Button.inline("7", f"dkp|{dep_id}|7"), Button.inline("8", f"dkp|{dep_id}|8"), Button.inline("9", f"dkp|{dep_id}|9")],
        [Button.inline("🔙 Del", f"dkp|{dep_id}|del"), Button.inline("0", f"dkp|{dep_id}|0"), Button.inline("✅ Confirm", f"dkp|{dep_id}|conf")],
        [Button.inline("❌ Cancel", f"dkp|{dep_id}|cancel")]
    ]

async def manual_deposit_init(event, method):
    uid = event.sender_id
    deposit_input[uid] = {'step': 'wait_amt', 'method': method}
    
    if method == "Cwallet":
        caption = (f"{P_CARD} <b>Cwallet Deposit</b>\n\n"
                   f"👇 <b>Scan QR to pay via Cwallet</b>\n\n"
                   f"💳 <b>Cwallet ID:</b> <code>{CWALLET_ID}</code>\n\n"
                   f"💰 <b>Enter the AMOUNT</b> in ₹ (INR) you want to deposit.\n\n"
                   f"{PE_GIFT} <b>Bonus:</b> You will get <b>+5% extra</b> on Cwallet deposits!\n\n"
                   f"<i>After payment, send the screenshot/proof here.</i>")
        
        await event.delete()
        
        try:
            await bot.send_file(uid, CWALLET_QR, caption=caption, buttons=[[Button.inline("❌ Cancel", "cancel_action")]])
        except Exception as e:
            await bot.send_message(uid, caption, buttons=[[Button.inline("❌ Cancel", "cancel_action")]])
    else:
        await event.edit(
            f"{P_CARD} <b>{method} Deposit</b>\n\n"
            f"👇 Reply to this message with the <b>AMOUNT</b> in ₹ (INR) you want to deposit.",
            buttons=[[Button.inline("❌ Cancel", "cancel_action")]]
        )

# ========== UPI FUNCTIONS WITH SCREENSHOT ==========

async def init_upi_keypad(event):
    """Start UPI payment with QR"""
    uid = event.sender_id
    deposit_input[uid] = {'step': 'upi_keypad', 'val': '0'}
    
    caption = (f"{PE_LIGHTNING} <b>UPI PAYMENT</b>\n\n"
               f"👇 <b>Scan QR to pay</b>\n"
               f"💳 <b>UPI ID:</b> <code>{UPI_ID}</code>\n\n"
               f"💰 <b>Enter the AMOUNT</b> in INR using the keypad below.\n"
               f"<i>(Min: ₹1)</i>")
    
    await event.delete()
    
    try:
        # Generate QR
        upi_url = f"upi://pay?pa={UPI_ID}&pn=FreshTgStore&am=1&cu=INR"
        encoded_upi = quote(upi_url)
        qr_url = f"https://quickchart.io/qr?text={encoded_upi}&size=400"
        
        try:
            await bot.send_file(uid, qr_url, caption=caption, buttons=get_keypad())
        except Exception as e:
            logger.error(f"QR Init Error: {e}")
            await bot.send_message(uid, caption, buttons=get_keypad())
            
    except Exception as e:
        logger.error(f"init_upi_keypad Error: {e}")
        await bot.send_message(uid, caption, buttons=get_keypad())

async def keypad_logic(event):
    """Handle UPI keypad input"""
    uid = event.sender_id
    action = event.data.decode().replace("kp_", "")
    curr = deposit_input.get(uid, {}).get('val', "0")

    if action.isdigit():
        if curr == "0": 
            curr = action
        else: 
            curr += action
        if len(curr) > 5: 
            curr = curr[:5]
    elif action == "del": 
        curr = curr[:-1] or "0"
    elif action == "done":
        try:
            amt = int(curr)
            if amt < 1: 
                return await event.answer("⚠️ Minimum Deposit is ₹1", alert=True)
            return await show_upi_qr(event, amt)
        except ValueError:
            return await event.answer("⚠️ Invalid amount", alert=True)
    
    deposit_input[uid] = {'step': 'upi_keypad', 'val': curr}
    
    try:
        await event.edit(f"{P_KEY} <b>ENTER AMOUNT IN INR</b>\n\n"
                        f"💳 UPI ID: <code>{UPI_ID}</code>\n\n"
                        f"{P_MONEY} <code>₹{curr}</code>", 
                        buttons=get_keypad())
    except MessageNotModifiedError:
        pass

async def show_upi_qr(event, amount):
    """Show UPI QR with amount and UTR + Screenshot option"""
    uid = event.sender_id
    order_id = f"ORDER_{uid}_{int(time.time())}"
    
    try:
        # Create UPI URL
        upi_url = f"upi://pay?pa={UPI_ID}&pn=FreshTgStore&am={amount}&cu=INR"
        encoded_upi = quote(upi_url)
        
        # Generate QR
        generated_qr = f"https://quickchart.io/qr?text={encoded_upi}&size=400"
        
        # Save order
        cur.execute("INSERT INTO upi_orders (order_id, user_id, amount, status) VALUES (?,?,?,?)", 
                    (order_id, uid, amount, "pending"))
        db.commit()
        
        msg = (f"{PE_LIGHTNING} <b>AUTOMATIC UPI PAYMENT</b>\n\n"
               f"{P_MONEY} Amount: <code>₹{amount}</code>\n"
               f"{P_ID} Order ID: <code>{order_id}</code>\n\n"
               f"👇 <b>Scan QR below or Pay to:</b>\n<code>{UPI_ID}</code>\n\n"
               f"📌 <b>After Payment:</b>\n"
               f"1️⃣ Send your <b>12-Digit UTR</b> number\n"
               f"2️⃣ Send <b>Screenshot</b> of payment confirmation\n\n"
               f"⚠️ <b>Both UTR and Screenshot are required!</b>")
        
        await event.delete()
        
        # Store pending UPI order
        pending_utr[uid] = {
            'order_id': order_id,
            'amount': amount,
            'step': 'wait_utr'
        }
        
        # Send generated QR
        try:
            await bot.send_file(uid, generated_qr, caption=msg, buttons=[
                [Button.inline("📝 Submit UTR", f"submit_utr_{order_id}")],
                [Button.inline("❌ Cancel", "cancel_action")]
            ])
        except Exception as e:
            logger.error(f"QR Send Error: {e}")
            # Fallback: Send UPI ID only
            fallback_msg = (f"{PE_LIGHTNING} <b>AUTOMATIC UPI PAYMENT</b>\n\n"
                           f"{P_MONEY} Amount: <code>₹{amount}</code>\n"
                           f"{P_ID} Order ID: <code>{order_id}</code>\n\n"
                           f"👇 <b>Pay to this UPI ID:</b>\n<code>{UPI_ID}</code>\n\n"
                           f"📌 <b>After Payment:</b>\n"
                           f"1️⃣ Send your <b>12-Digit UTR</b> number\n"
                           f"2️⃣ Send <b>Screenshot</b> of payment confirmation\n\n"
                           f"⚠️ <b>Both UTR and Screenshot are required!</b>")
            
            await bot.send_message(uid, fallback_msg, buttons=[
                [Button.inline("📝 Submit UTR", f"submit_utr_{order_id}")],
                [Button.inline("❌ Cancel", "cancel_action")]
            ])
            
    except Exception as e:
        logger.error(f"show_upi_qr Error: {e}")
        # Ultimate fallback
        fallback_msg = (f"{PE_LIGHTNING} <b>AUTOMATIC UPI PAYMENT</b>\n\n"
                       f"{P_MONEY} Amount: <code>₹{amount}</code>\n"
                       f"{P_ID} Order ID: <code>{order_id}</code>\n\n"
                       f"👇 <b>Pay to this UPI ID:</b>\n<code>{UPI_ID}</code>\n\n"
                       f"📌 <b>After Payment:</b>\n"
                       f"1️⃣ Send your <b>12-Digit UTR</b> number\n"
                       f"2️⃣ Send <b>Screenshot</b> of payment confirmation\n\n"
                       f"⚠️ <b>Both UTR and Screenshot are required!</b>")
        
        pending_utr[uid] = {
            'order_id': order_id,
            'amount': amount,
            'step': 'wait_utr'
        }
        
        await bot.send_message(uid, fallback_msg, buttons=[
            [Button.inline("📝 Submit UTR", f"submit_utr_{order_id}")],
            [Button.inline("❌ Cancel", "cancel_action")]
        ])

async def submit_utr_handler(event, order_id):
    """Handle UTR submission with screenshot"""
    uid = event.sender_id
    
    # Check if order exists
    row = cur.execute("SELECT amount, status FROM upi_orders WHERE order_id=?", (order_id,)).fetchone()
    if not row:
        return await event.answer("❌ Order not found.", alert=True)
    
    if row[1] == 'success':
        return await event.answer("✅ Already credited!", alert=True)
    
    await event.delete()
    
    chat = event.chat_id
    
    async with bot.conversation(chat, timeout=180) as conv:
        try:
            # Step 1: Ask for UTR
            await conv.send_message(f"{P_UTR} <b>Step 1/2: Enter UTR Number</b>\n\n"
                                   f"Please enter the <b>12-Digit UTR</b> / Reference Number of your payment:\n\n"
                                   f"<i>Type /cancel to abort</i>")
            
            resp = await conv.get_response()
            utr_number = resp.text.strip()
            
            if utr_number.lower() == "/cancel":
                return await conv.send_message("❌ Cancelled.")
            
            if len(utr_number) < 8:
                return await conv.send_message("❌ Invalid UTR. Please try again with a valid 12-digit UTR.")
            
            # Step 2: Ask for Screenshot
            await conv.send_message(f"{P_SCREEN} <b>Step 2/2: Send Payment Screenshot</b>\n\n"
                                   f"Please send the <b>payment confirmation screenshot</b>.\n\n"
                                   f"<i>Make sure the UTR is visible in the screenshot.</i>\n\n"
                                   f"<i>Type /skip if you don't have screenshot</i>")
            
            # Wait for photo
            photo_msg = await conv.get_response()
            
            screenshot_path = None
            if photo_msg.photo:
                screenshot_path = f"screenshots/utr_{uid}_{int(time.time())}.jpg"
                os.makedirs("screenshots", exist_ok=True)
                await bot.download_media(photo_msg, screenshot_path)
                await conv.send_message("✅ Screenshot received! Thank you.")
            elif photo_msg.text and photo_msg.text.lower() == "/skip":
                await conv.send_message("⚠️ Screenshot skipped (not recommended)")
            else:
                await conv.send_message("⚠️ No screenshot received. Continuing without it.")
            
            # Save deposit
            amount = int(row[0])
            method = f"UPI (UTR: {utr_number})"
            
            cur.execute("""
                INSERT INTO deposits (user_id, amount, method_name, status, screenshot, utr) 
                VALUES (?,?,?,?,?,?)
            """, (uid, amount, method, "pending", screenshot_path, utr_number))
            db.commit()
            dep_id = cur.lastrowid
            
            # Notify admin with UTR and screenshot
            cap = (f"{PE_LIGHTNING} <b>NEW UPI DEPOSIT (Needs Approval)</b>\n"
                   f"{P_ACC} User: <code>{uid}</code>\n"
                   f"{P_MONEY} Amount: <b>₹{amount}</b>\n"
                   f"{P_UTR} UTR Submitted: <code>{utr_number}</code>\n")
            
            if screenshot_path:
                cap += f"{P_SCREEN} Screenshot: ✅ Received\n"
            else:
                cap += f"{P_SCREEN} Screenshot: ❌ Not Provided\n"
            
            cap += f"\nPlease verify this UTR in your app."
            
            btns = [
                [Button.inline(f"✅ Accept (₹{amount})", f"dep_acc|{dep_id}|{uid}|UPI|exact|{amount}"), 
                 Button.inline("❌ Reject", f"dep_rej|{dep_id}|{uid}")]
            ]
            
            # Send to admin with screenshot
            try:
                if screenshot_path and os.path.exists(screenshot_path):
                    await bot.send_file(LOG_CHANNEL_ID, screenshot_path, caption=cap, buttons=btns)
                else:
                    await bot.send_message(LOG_CHANNEL_ID, cap, buttons=btns)
            except Exception as e:
                logger.error(f"Admin log error: {e}")
                await bot.send_message(LOG_CHANNEL_ID, cap, buttons=btns)
            
            await conv.send_message("✅ <b>UTR Submitted successfully!</b>\n\n"
                                   f"{P_UTR} UTR: <code>{utr_number}</code>\n"
                                   f"{P_SCREEN} Screenshot: {'✅ Received' if screenshot_path else '❌ Not Provided'}\n\n"
                                   f"Amount will be added to your balance as soon as our admin verifies the payment.\n"
                                   f"Thank you for your patience! 🙏")
            
            # Cleanup
            if uid in pending_utr:
                del pending_utr[uid]
            
        except asyncio.TimeoutError:
            await conv.send_message("❌ Time out. Please try again.")
        except Exception as e:
            logger.error(f"UTR Error: {e}")
            await conv.send_message("❌ Error processing your request. Please try again.")

# ================= BUYING FLOW =================
def get_available_account_products():
    columns = {row[1] for row in cur.execute("PRAGMA table_info(stock)").fetchall()}
    dc_expression = "data_center" if "data_center" in columns else "NULL"
    query = f"""
        SELECT country_icon, country_name, category, account_year, price,
               COUNT(*), MIN(phone), {dc_expression}
        FROM stock
        WHERE available=1
        GROUP BY country_icon, country_name, category, account_year, price, {dc_expression}
        ORDER BY country_name ASC, account_year DESC, price ASC
    """
    rows = cur.execute(query).fetchall()
    return [
        {
            "icon": row[0] or "🌍",
            "country": row[1] or "Unknown",
            "category": row[2] or "Standard",
            "year": row[3],
            "price": int(row[4] or 0),
            "stock": int(row[5] or 0),
            "phone": row[6],
            "dc": row[7]
        }
        for row in rows
    ]

def get_product_stock(product):
    row = cur.execute(
        "SELECT COUNT(*) FROM stock WHERE available=1 AND country_name=? AND category=? AND account_year=? AND price=?",
        (product["country"], product["category"], product["year"], product["price"])
    ).fetchone()
    return row[0] if row else 0

def account_store_caption(products, page, total_pages, page_products, flow):
    labels = get_store_buttons(flow)
    product_lines = []
    for product in page_products:
        year_line = f" • {html.escape(str(product['year']))}" if product["year"] else ""
        info_line = (f"🖥 DC {html.escape(str(product['dc']))}" if product["dc"] else "") + year_line
        product_lines.extend([
            "",
            f"{product['icon']} <b>{html.escape(product['country'])}</b> — {html.escape(product['category'])}",
            info_line,
            f"💵 ${to_usd(product['price']):.2f} • {P_INR}{product['price']}",
            f"{P_PKG} Stock: {product['stock']}"
        ])
    page_text = ""
    if total_pages > 1:
        page_text = f"\n\n📄 {html.escape(labels['page'].format(page=page, total_pages=total_pages))}"
    values = {
        "rate": f"{get_usdt_rate():g}",
        "available": sum(product["stock"] for product in products),
        "products": "\n".join(product_lines) if product_lines else f"\n\n{P_PKG} No accounts are currently available.\n\nPlease check again later.",
        "page": page_text,
        "total_pages": total_pages
    }
    try:
        return get_store_message(flow).format(**values)
    except (KeyError, ValueError):
        return STORE_DEFAULT_MESSAGES[flow].format(**values)

async def render_account_store(event, flow, page=1, send_banner=False):
    limit = 10
    products = get_available_account_products()
    total_pages = max(1, (len(products) + limit - 1) // limit)
    page = min(max(page, 1), total_pages)
    page_products = products[(page - 1) * limit:page * limit]

    uid = event.sender_id
    account_product_state[uid] = {str(index): product for index, product in enumerate(page_products)}
    product_buttons = []
    for index, product in enumerate(page_products):
        try:
            label = get_store_buttons(flow)["product"].format(
                icon=product["icon"], country=product["country"], category=product["category"],
                year=product["year"], price=product["price"], stock=product["stock"]
            )
        except (KeyError, ValueError):
            label = f"{product['icon']} {product['country']}"
        if len(label) > 32:
            label = label[:31] + "…"
        product_buttons.append(Button.inline(label, f"prod|{flow}|{index}"))
    buttons = [product_buttons[index:index + 2] for index in range(0, len(product_buttons), 2)]

    if total_pages > 1:
        navigation = []
        if page > 1:
            navigation.append(Button.inline(get_store_buttons(flow)["previous"], f"shop|{flow}|{page - 1}"))
        navigation.append(Button.inline(get_store_buttons(flow)["page"].format(page=page, total_pages=total_pages), "shop_noop"))
        if page < total_pages:
            navigation.append(Button.inline(get_store_buttons(flow)["next"], f"shop|{flow}|{page + 1}"))
        buttons.append(navigation)
    buttons.append([Button.inline(get_store_buttons(flow)["back"], "shop_back")])

    caption = account_store_caption(products, page, total_pages, page_products, flow)
    banner = get_banner_reference(get_setting(store_banner_key(flow)))
    if send_banner and banner:
        try:
            await bot.send_file(event.chat_id, banner, caption=caption, buttons=buttons)
            return
        except Exception:
            pass
    if isinstance(event, events.CallbackQuery.Event):
        await event.edit(caption, buttons=buttons)
    else:
        await event.respond(caption, buttons=buttons)

async def show_product_details(event, flow, token):
    product = account_product_state.get(event.sender_id, {}).get(token)
    if not product:
        return await event.answer("⚠️ Product list expired. Please reopen the shop.", alert=True)
    stock = get_product_stock(product)
    if stock == 0:
        await event.edit(f"{P_NO} <b>Out of Stock</b>\n\nThis product is no longer available.", buttons=[[Button.inline(get_store_buttons(flow)["back"], f"shop|{flow}|1")]])
        return

    lines = [
        f"{P_CART} <b>PRODUCT DETAILS</b>",
        "━━━━━━━━━━━━━━━━━━",
        "",
        f"🌍 <b>Country:</b> {product['icon']} {html.escape(product['country'])}",
        f"📌 <b>Type:</b> {html.escape(product['category'])}"
    ]
    if product["dc"]:
        lines.append(f"🖥 <b>DC:</b> {html.escape(str(product['dc']))}")
    if product["year"]:
        lines.append(f"📅 <b>Year:</b> {html.escape(str(product['year']))}")
    lines.extend([
        "",
        f"💵 <b>Price:</b> ${to_usd(product['price']):.2f}",
        f"🇮🇳 <b>Price:</b> {P_INR}{product['price']}",
        f"{P_PKG} <b>Available:</b> {stock}"
    ])
    buttons = [
        [Button.inline(get_store_buttons(flow)["buy"], f"pbuy|{flow}|{token}")],
        [Button.inline(get_store_buttons(flow)["back"], f"shop|{flow}|1")]
    ]
    await event.edit("\n".join(lines), buttons=buttons)

async def show_countries(event, flow, page=1):
    return await render_account_store(event, flow, page, send_banner=not isinstance(event, events.CallbackQuery.Event))

async def show_years(event, flow, country):
    rows = cur.execute("SELECT account_year, price, COUNT(*) FROM stock WHERE available=1 AND country_name LIKE ? GROUP BY account_year, price ORDER BY account_year DESC", (f"{country}%",)).fetchall()
    if not rows: return await event.answer("❌ Out of stock for this country.", alert=True)

    uid = event.sender_id
    disc_row = cur.execute("SELECT discount FROM users WHERE user_id=?", (uid,)).fetchone()
    discount = disc_row[0] if disc_row else 0

    msg = f"{PE_FLOWER} <b>Select Account Year</b>\n{P_GLOBE} Country: <b>{country}</b>\n\n"
    btns = []
    
    for (y, p, c) in rows:
        disp_p = p if discount == 0 else int(p * (100 - discount) / 100)
        disc_text = f" (-{discount}%)" if discount > 0 else ""
        btns.append([Button.inline(f"{y} | ₹{disp_p}{disc_text} | {c}", f"by|{flow}|{country}|{y}|{p}")])

    btns.append([Button.inline("Back to Countries", f"pg_c|{flow}|1")])
    await event.edit(msg, buttons=btns)

async def confirm_purchase(event, country, year, price_str):
    uid = event.sender_id
    base_price = int(price_str)
    
    bal_row = cur.execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
    bal = bal_row[0] if bal_row else 0
    disc_row = cur.execute("SELECT discount FROM users WHERE user_id=?", (uid,)).fetchone()
    discount = disc_row[0] if disc_row else 0
    final_price = base_price if discount == 0 else int(base_price * (100 - discount) / 100)

    msg = (f"{PE_CHECK} <b>Confirm Your Purchase</b>\n\n"
           f"{P_FLAG} <b>Country:</b> {country}\n"
           f"{P_CAL} <b>Year:</b> {year}\n"
           f"{P_MONEY} <b>Final Price:</b> {P_INR}{final_price}\n\n"
           f"{P_CARD} <b>Your Balance:</b> {P_INR}{bal}\n\n"
           f"❓ Do you want to proceed with this purchase?")
    
    btns = [
        [Button.inline(get_store_buttons("single")["buy"], f"buy_cf|{country}|{year}|{base_price}")],
        [Button.inline(get_store_buttons("single")["cancel"], "cancel_action")]
    ]
    await event.edit(msg, buttons=btns)

async def process_purchase(event, country, year_str, price_str):
    uid, base_price = event.sender_id, int(price_str)

    disc_row = cur.execute("SELECT discount FROM users WHERE user_id=?", (uid,)).fetchone()
    discount = disc_row[0] if disc_row else 0
    final_price = base_price if discount == 0 else int(base_price * (100 - discount) / 100)

    async with get_user_lock(uid):
        row = cur.execute("SELECT phone, session_file, country_icon, account_year, twofa FROM stock WHERE country_name LIKE ? AND account_year=? AND price=? AND available=1 LIMIT 1", (f"{country}%", int(year_str), base_price)).fetchone()

        if not row:
            return await event.answer("❌ Sold out! Another user just bought this account.", alert=True)
        
        phone, sess, c_icon, actual_year, twofa_pass = row

        cur.execute("UPDATE users SET balance = balance - ? WHERE user_id=? AND balance >= ?", (final_price, uid, final_price))
        if cur.rowcount == 0:
            return await event.answer(f"❌ Insufficient Balance! Need ₹{final_price}", alert=True)

        cur.execute("UPDATE stock SET available=0 WHERE phone=?", (phone,))
        db.commit()

    await event.edit(f"{PE_LIGHTNING} <b>Fetching Number (+{phone})...</b>")
    clean_sess = sess if not sess.endswith(".session") else sess[:-8]
    client = TelegramClient(clean_sess, API_ID, API_HASH)
    
    try:
        await client.connect()
        if not await client.is_user_authorized(): raise Exception("Session dead")
    except Exception:
        async with get_user_lock(uid):
            cur.execute("DELETE FROM stock WHERE phone=?", (phone,))
            cur.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (final_price, uid))
            db.commit()
        await client.disconnect()
        delete_session_files(sess)
        return await event.edit(f"{P_NO} <b>Account Invalid.</b> Money refunded. Try buying another.")

    msg = (f"{PE_LIGHTNING} <b>Order Active!</b>\n\n"
           f"{P_PHONE} <b>Phone:</b> <code>{phone}</code>\n"
           f"{P_FLAG} <b>Country:</b> {c_icon} {country}\n\n"
           f"🔻 <b>INSTRUCTIONS:</b>\n"
           f"1. Open Telegram & Add Account\n"
           f"2. Enter the number above.\n"
           f"3. {P_WAIT} <b>Please wait!</b> The bot is actively listening for your OTP and will send it automatically once Telegram delivers it.\n\n"
           f"<i>Note: If no OTP is received within 10 minutes, the bot will auto-cancel and refund your balance automatically.</i>")
    
    sent_msg = await event.edit(msg)
    
    active_orders[phone] = {
        'uid': uid,
        'client': client, 'sess': sess, 'start_time': time.time(), 
        'paid': False, 'price': final_price, 'country': country, 'year': actual_year, 
        'c_icon': c_icon, 'twofa': twofa_pass, 'msg_id': sent_msg.id
    }
    asyncio.create_task(auto_otp_task(phone))

async def auto_otp_task(phone):
    if phone not in active_orders: return
    
    order = active_orders[phone]
    client = order['client']
    start_time = order['start_time']
    uid = order['uid']
    msg_id = order['msg_id']
    
    while time.time() - start_time < get_auto_cancel_seconds():
        if phone not in active_orders: return 
        try:
            msgs = await client.get_messages(777000, limit=5)
            code = None
            for m in msgs:
                if m.date.timestamp() > start_time - 10: 
                    if m.message and re.search(OTP_REGEX, m.message) and "Login detected" not in m.message:
                        code = re.search(OTP_REGEX, m.message).group()
                        break
            
            if code:
                if not order['paid']:
                    order['paid'] = True
                    async with get_user_lock(uid):
                        cur.execute("INSERT INTO orders (user_id, country, year, price, phone, otp) VALUES (?,?,?,?,?,?)", (uid, order['country'], order['year'], order['price'], phone, code))
                        cur.execute("DELETE FROM stock WHERE phone=?", (phone,))
                        db.commit()
                    
                    await log_primary_purchase(uid, order['country'], order['price'], order['price'], order['year'], 1)
                
                twofa_text = f"{P_2FA} <b>2FA:</b> <code>{order['twofa']}</code>" if order['twofa'] != "None" else f"🔓 <b>2FA:</b> <code>Disabled (No Password)</code>"
                msg_text = (f"{PE_CHECK} <b>Latest OTP Fetched!</b>\n\n"
                            f"{P_PHONE} <b>Phone:</b> <code>{phone}</code>\n"
                            f"{P_FLAG} <b>Country:</b> {order['c_icon']} {order['country']}\n"
                            f"{P_OTP} <b>OTP:</b> <code>{code}</code>\n"
                            f"{twofa_text}")
                
                try: 
                    await bot.edit_message(uid, msg_id, msg_text, buttons=[[Button.inline("🔄 Get OTP Again", f"get_otp_again|{phone}")], [Button.inline("🚪 Finish & Logout", f"logout_bot|{phone}")]])
                except MessageNotModifiedError: pass
                except Exception: 
                    await bot.send_message(uid, msg_text, buttons=[[Button.inline("🔄 Get OTP Again", f"get_otp_again|{phone}")], [Button.inline("🚪 Finish & Logout", f"logout_bot|{phone}")]])
                return 
        except Exception: pass
        await asyncio.sleep(6) 
        
    if phone in active_orders and not active_orders[phone]['paid']:
        order = active_orders.pop(phone)
        try: await order['client'].disconnect()
        except: pass
        
        async with get_user_lock(uid):
            cur.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (order['price'], uid))
            cur.execute("UPDATE stock SET available=1 WHERE phone=?", (phone,))
            db.commit()
            
        try: await bot.edit_message(uid, msg_id, f"{P_TIME} <b>Order Expired!</b>\nThe 10-minute limit for <code>{phone}</code> ran out. Your money ({P_INR}{order['price']}) has been automatically refunded.")
        except: pass

async def init_session_purchase(event, country, year, price_str):
    uid, price = event.sender_id, int(price_str)
    stock_row = cur.execute("SELECT COUNT(*) FROM stock WHERE country_name LIKE ? AND account_year=? AND price=? AND available=1", (f"{country}%", int(year), price)).fetchone()
    stock = stock_row[0] if stock_row else 0
    if stock == 0: return await event.answer("❌ Out of stock!", alert=True)
    
    session_buy_state[uid] = {'country': country, 'year': year, 'price': price, 'stock': stock}
    disc_row = cur.execute("SELECT discount FROM users WHERE user_id=?", (uid,)).fetchone()
    discount = disc_row[0] if disc_row else 0
    p_disp = price if discount == 0 else int(price * (100 - discount) / 100)
    
    msg = (f"{PE_GIFT} <b>Buy {country} ({year}) Sessions</b>\n\n"
           f"{P_MONEY} <b>Price per session:</b> {P_INR}{p_disp}\n"
           f"{P_PKG} <b>Available Stock:</b> {stock}\n\n"
           f"👇 <b>Reply to this message</b> with the <b>Number of Sessions</b> you want to buy.")
    await event.edit(msg, buttons=[[Button.inline(get_store_buttons("bulk")["cancel"], "cancel_action")]])

async def process_bulk_sessions(event, uid, qty, state, final_cost):
    country, year, price = state['country'], int(state['year']), int(state['price'])
    await event.respond(f"{PE_LIGHTNING} <b>Processing your sessions...</b>")

    async with get_user_lock(uid):
        rows = cur.execute("SELECT phone, session_file, twofa, account_year FROM stock WHERE country_name LIKE ? AND account_year=? AND price=? AND available=1 LIMIT ?", (f"{country}%", year, price, qty)).fetchall()
        if len(rows) < qty:
            return await event.respond(f"{P_NO} Stock changed during processing. Purchase Cancelled.")
        
        cur.execute("UPDATE users SET balance = balance - ? WHERE user_id=? AND balance >= ?", (final_cost, uid, final_cost))
        if cur.rowcount == 0:
            return await event.respond(f"{P_NO} Insufficient Balance! Purchase Cancelled.")

        phones = [r[0] for r in rows]
        placeholders = ",".join("?" for _ in phones)
        cur.execute(f"UPDATE stock SET available=0 WHERE phone IN ({placeholders})", phones)
        
        price_per_acc = final_cost // qty
        for p in phones:
            cur.execute("INSERT INTO orders (user_id, country, price, phone, otp) VALUES (?,?,?,?,?)", (uid, country, price_per_acc, p, "SESSION_FILES"))
        db.commit()

    zip_name = f"sessions_{uid}_{int(time.time())}.zip"
    numbers_txt = ""

    try:
        with zipfile.ZipFile(zip_name, 'w') as zf:
            for phone, sess_file, twofa_pass, y in rows:
                base_s = sess_file if not sess_file.endswith(".session") else sess_file[:-8]
                for ext in ['.session', '.session-wal', '.session-shm', '.session-journal']:
                    src = base_s + ext
                    if os.path.exists(src): zf.write(src, os.path.basename(src))
                
                pass_text = twofa_pass if twofa_pass != "None" else "No_Password"
                numbers_txt += f"+{phone} | pass:{pass_text}\n"
            
            numbers_txt += "\n\nPurchased from @Freshtgsales\n"
            zf.writestr("numbers.txt", numbers_txt)
            
        caption = f"{PE_GIFT} <b>Bulk Purchase Successful!</b>\n\n{P_FLAG} Country: {country}\n{P_PKG} Quantity: {qty}\n{P_CARD} Total Paid: {P_INR}{final_cost}\n\n<i>(Note: Sessions are safely provided, the bot does not keep them active)</i>"
        await bot.send_file(uid, zip_name, caption=caption)
        await log_primary_purchase(uid, country, price, final_cost, year, qty)
    except Exception as e: await event.respond(f"{P_WARN} Error creating zip: {e}")
    finally:
        if os.path.exists(zip_name): os.remove(zip_name)

# ================= STATS & PROFILE FUNCTIONS =================
async def profile_handler(event):
    uid = event.sender_id
    row = cur.execute("SELECT balance, total_deposited, joined_date, discount FROM users WHERE user_id=?", (uid,)).fetchone()
    if not row: return await bot.send_message(event.chat_id, "⚠️ Error: Please type /start to initialize your account.")
    
    bal, dep, date, discount = row
    ref_count_row = cur.execute("SELECT COUNT(*) FROM users WHERE referred_by=?", (uid,)).fetchone()
    ref_count = ref_count_row[0] if ref_count_row else 0
    me = await bot.get_me()
    bot_username = me.username or ""
    ref_link = f"https://t.me/{bot_username}?start=ref_{uid}" if bot_username else None
    disc_msg = f"\n{P_GIFT} Active Discount: <b>{discount}% OFF</b>" if discount > 0 else ""
    ref_block = (f"{P_USERS} <b>Your Referral Link:</b>\n<code>{ref_link}</code>\n\n"
                 if ref_link else
                 f"{P_USERS} <b>Referral Link:</b>\n<i>Set a public bot username to enable referrals.</i>\n\n")
    
    msg = (f"{PE_KISS} <b>USER PROFILE</b>\n\n"
           f"{P_ID} User ID: <code>{uid}</code>\n"
           f"{P_MONEY} Balance: <code>${to_usd(bal):.2f} (₹{bal})</code>\n"
           f"{P_CARD} Deposited: <code>${to_usd(dep):.2f} (₹{dep})</code>{disc_msg}\n"
           f"{P_USERS} Referred Users: <b>{ref_count}</b>\n"
           f"{P_CAL} Joined: {date[:10]}\n\n"
           f"{ref_block}"
           f"<i>(Share this link with your friends to earn bonuses!)</i>")
    await bot.send_message(event.chat_id, msg)

async def stats_handler(event, is_callback=False):
    uid = event.sender_id
    row = cur.execute("SELECT total_deposited FROM users WHERE user_id=?", (uid,)).fetchone()
    if not row: return
    dep = row[0]
    o_row = cur.execute("SELECT COUNT(*), SUM(price) FROM orders WHERE user_id=?", (uid,)).fetchone()
    total_orders = o_row[0] if o_row else 0
    spent = o_row[1] if o_row and o_row[1] else 0
    ref_row = cur.execute("SELECT COUNT(*) FROM users WHERE referred_by=?", (uid,)).fetchone()
    ref_count = ref_row[0] if ref_row else 0
    
    msg = (f"{PE_CROWN} <b>My Statistics</b>\n\n"
           f"{P_CART} <b>Accounts Bought:</b> {total_orders}\n"
           f"{P_USERS} <b>Referrals:</b> {ref_count}\n"
           f"{P_MONEY} <b>Total Spent:</b>\n${to_usd(spent):.2f}\n"
           f"{P_CARD} <b>Total Deposited:</b>\n${to_usd(dep):.2f}")
    
    btns = [[Button.inline("View Purchase Logs", "page_purchases_1")], [Button.inline("Referral Logs", "view_referrals")]]
    if is_callback:
        try: await event.edit(msg, buttons=btns)
        except MessageNotModifiedError: pass
    else: await bot.send_message(event.chat_id, msg, buttons=btns)

async def send_purchase_page(event, uid, page):
    limit = 5
    offset = (page - 1) * limit
    t_row = cur.execute("SELECT COUNT(*) FROM orders WHERE user_id=?", (uid,)).fetchone()
    total = t_row[0] if t_row else 0
    rows = cur.execute("SELECT phone, date FROM orders WHERE user_id=? ORDER BY id DESC LIMIT ? OFFSET ?", (uid, limit, offset)).fetchall()
    
    msg = f"{PE_FLOWER} <b>Purchase History</b>\nPage {page}\n\n"
    if not rows: msg += "No purchases found."
    else:
        for ph, d in rows:
            try:
                dt = datetime.strptime(d, "%Y-%m-%d %H:%M:%S")
                d_str = dt.strftime("%a %b %d %H:%M:%S %Y")
            except:
                d_str = d
            msg += f"{P_PHONE} {ph}\n{P_CAL} {d_str}\n────────────────\n"
            
    nav = []
    if page > 1: nav.append(Button.inline("Prev", f"page_purchases_{page-1}"))
    nav.append(Button.inline("Back", "back_to_stats"))
    if offset + limit < total: nav.append(Button.inline("Next", f"page_purchases_{page+1}"))
    await event.edit(msg, buttons=[nav])

async def view_referrals(event):
    refs = cur.execute("SELECT user_id FROM users WHERE referred_by=?", (event.sender_id,)).fetchall()
    await event.answer(f"👥 You have referred {len(refs)} user(s).", alert=True)

# ================= ADMIN ACTIONS =================
async def admin_panel_handler(event):
    uid = event.sender_id
    if not is_admin(uid): return
    
    status_text = "🟢 Bot is ON" if is_bot_online() else "🔴 Bot is OFF"
    total_users = cur.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    total_stock = cur.execute("SELECT COUNT(*) FROM stock WHERE available=1").fetchone()[0]
    pending_deposits = cur.execute("SELECT COUNT(*) FROM deposits WHERE status='pending'").fetchone()[0]
    btns = []
    
    if uid in ADMIN_IDS or has_perm(uid, 'p_settings'):
        btns.append([Button.inline(f"Status: {status_text}", "adm_togglebot")])
        
    r1 = []
    if uid in ADMIN_IDS or has_perm(uid, 'p_add_stock'):
        r1.extend([Button.inline("Add Single Acc", "adm_addstock"), Button.inline("Add ZIP", "adm_addzip")])
        btns.append([Button.inline("🛠 Maintenance Mode", "adm_maintenance"), Button.inline("⚙️ General Settings", "adm_general")])
    if r1: btns.append(r1)

    r2 = []
    if uid in ADMIN_IDS or has_perm(uid, 'p_manage_stock'):
        r2.extend([Button.inline("Manage Stock", "adm_managestock"), Button.inline("Auto Price", "adm_autoprice")])
    if r2: btns.append(r2)

    r3 = []
    if uid in ADMIN_IDS or has_perm(uid, 'p_stats'):
        r3.extend([Button.inline("Statistics", "adm_stats"), Button.inline("Broadcast", "adm_bcast")])
        r3.append(Button.inline("User Info", "adm_userinfo"))
    if r3: btns.append(r3)

    r4 = []
    if uid in ADMIN_IDS or has_perm(uid, 'p_bal'):
        r4.extend([Button.inline("Change Balance", "adm_bal"), Button.inline("Ban User", "adm_ban")])
    if r4: btns.append(r4)

    r5 = []
    if uid in ADMIN_IDS or has_perm(uid, 'p_settings'):
        r5.extend([Button.inline("Discount", "adm_discount"), Button.inline("Ref %", "adm_refpct")])
        btns.append(r5)
        btns.append([Button.inline("📝 Set Welcome Msg", "adm_welcome"), Button.inline("🖼️ Banner Images", "adm_banner")])
        btns.append([Button.inline("📝 Store Messages", "adm_store_messages"), Button.inline("⚙️ Store Buttons", "adm_store_buttons")])
        btns.append([Button.inline("Support URL", "adm_supporturl"), Button.inline("Payments", "adm_payments")])
        btns.append([Button.inline("Set USDT Rate", "adm_usdtrate")])
        btns.append([Button.inline("Backup Users", "adm_backupusr"), Button.inline("Restore Users", "adm_restoreusr")])

    if uid in ADMIN_IDS:
        btns.append([Button.inline("Manage Admins", "adm_manageadmins")])

    header = (f"{PE_CROWN} <b>ADVANCED ADMIN DASHBOARD</b>\n\n"
              f"{P_USERS} Users: <b>{total_users}</b>\n"
              f"{P_PKG} Available Stock: <b>{total_stock}</b>\n"
              f"{P_WAIT} Pending Deposits: <b>{pending_deposits}</b>")
    await bot.send_message(event.chat_id, header, buttons=btns)

async def maintenance_menu(event):
    enabled = is_maintenance_mode()
    status = "🟢 Enabled" if enabled else "🔴 Disabled"
    buttons = [
        [Button.inline("🟢 Enable", "adm_maintenance_set|on"), Button.inline("🔴 Disable", "adm_maintenance_set|off")],
        [Button.inline("✏️ Change Message", "adm_maintenance_message")],
        [Button.inline("📊 Status", "adm_maintenance_status")],
        [Button.inline("◀️ Back", "adm_adminmain")]
    ]
    await event.edit(
        f"🛠 <b>Maintenance Mode</b>\n\nStatus: <b>{status}</b>\n\n"
        f"Current message:\n{html.escape(get_maintenance_message())}",
        buttons=buttons
    )

async def general_settings_menu(event):
    msg = (f"⚙️ <b>General Settings</b>\n\n"
           f"🔗 Support URL: <code>{html.escape(get_support_url())}</code>\n"
           f"📜 Terms URL: <code>{html.escape(get_terms_url())}</code>\n"
           f"📢 Channel links: <b>{len(get_channel_links())}</b> configured\n"
           f"💱 USDT rate: <b>{get_usdt_rate()}</b> INR\n"
           f"⏱ Auto-cancel: <b>{get_auto_cancel_seconds()}</b> seconds")
    buttons = [
        [Button.inline("🔗 Support URL", "adm_setting_edit|support_url")],
        [Button.inline("📜 Terms URL", "adm_setting_edit|terms_url")],
        [Button.inline("📢 Update Channel Links", "adm_setting_edit|channel_links")],
        [Button.inline("💱 USDT Rate", "adm_setting_edit|usdt_rate")],
        [Button.inline("⏱ Auto-cancel Seconds", "adm_setting_edit|auto_cancel_seconds")],
        [Button.inline("◀️ Back", "adm_adminmain")]
    ]
    await event.edit(msg, buttons=buttons)

def get_stats_period(period):
    periods = {
        "today": ("Today", "date('now', 'start of day')"),
        "week": ("This Week", "date('now', '-' || ((cast(strftime('%w', 'now') as integer) + 6) % 7) || ' days')"),
        "month": ("This Month", "date('now', 'start of month')"),
        "all": ("All Time", "NULL")
    }
    return periods.get(period, periods["all"])

async def render_admin_stats(event, period="all"):
    if not has_perm(event.sender_id, 'p_stats'):
        return await event.answer("Not authorized.", alert=True)
    label, since = get_stats_period(period)
    date_filter = "" if period == "all" else " AND date >= " + since
    user_filter = "" if period == "all" else " WHERE joined_date >= " + since

    total_users = cur.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    period_users = cur.execute("SELECT COUNT(*) FROM users" + user_filter).fetchone()[0]
    banned_users = cur.execute("SELECT COUNT(*) FROM users WHERE banned=1").fetchone()[0]
    total_balance = cur.execute("SELECT COALESCE(SUM(balance), 0) FROM users").fetchone()[0]
    total_upi_revenue = get_setting("upi_revenue", "0")

    deposit_row = cur.execute(
        "SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM deposits WHERE status='approved'" + date_filter
    ).fetchone()
    total_deposits = cur.execute(
        "SELECT COALESCE(SUM(total_deposited), 0) FROM users"
    ).fetchone()[0]
    period_deposit_count, period_deposits = deposit_row

    order_row = cur.execute(
        "SELECT COUNT(*), COALESCE(SUM(price), 0) FROM orders WHERE 1=1" + date_filter
    ).fetchone()
    period_orders, period_sales = order_row
    total_orders, total_sales = cur.execute(
        "SELECT COUNT(*), COALESCE(SUM(price), 0) FROM orders"
    ).fetchone()

    referral_filter = "" if period == "all" else " AND joined_date >= " + since
    total_referrals = cur.execute("SELECT COUNT(*) FROM users WHERE referred_by IS NOT NULL").fetchone()[0]
    period_referrals = cur.execute("SELECT COUNT(*) FROM users WHERE referred_by IS NOT NULL" + referral_filter).fetchone()[0]
    top_referrers = cur.execute(
        "SELECT referred_by, COUNT(*) AS referrals FROM users "
        "WHERE referred_by IS NOT NULL GROUP BY referred_by ORDER BY referrals DESC LIMIT 3"
    ).fetchall()

    available_stock = cur.execute("SELECT COUNT(*) FROM stock WHERE available=1").fetchone()[0]
    used_stock = cur.execute("SELECT COUNT(*) FROM stock WHERE available=0").fetchone()[0]
    pending_deposits = cur.execute("SELECT COUNT(*) FROM deposits WHERE status='pending'").fetchone()[0]
    top_text = "Unavailable"
    if top_referrers:
        top_text = "\n".join(f"<code>{referrer}</code>: {count}" for referrer, count in top_referrers)

    msg = (f"{P_STATS} <b>ADVANCED STATISTICS</b>\n"
           f"📅 Period: <b>{label}</b>\n\n"
           f"{P_USERS} <b>USERS</b>\n"
           f"Total: <b>{total_users}</b> | In period: <b>{period_users}</b>\n"
           f"Banned: <b>{banned_users}</b>\n\n"
           f"{P_MONEY} <b>FINANCIAL</b>\n"
           f"Total deposits: <b>{P_INR}{total_deposits}</b>\n"
           f"In period: <b>{P_INR}{period_deposits}</b> ({period_deposit_count} approved)\n"
           f"Total UPI revenue: <b>{P_INR}{html.escape(str(total_upi_revenue))}</b>\n"
           f"Total sales/revenue: <b>{P_INR}{total_sales}</b>\n"
           f"Period sales/revenue: <b>{P_INR}{period_sales}</b>\n\n"
           f"{P_CART} <b>ORDERS / STOCK</b>\n"
           f"Total orders: <b>{total_orders}</b> | In period: <b>{period_orders}</b>\n"
           f"Pending deposits: <b>{pending_deposits}</b>\n"
           f"Pending/completed/cancelled orders: <i>Unavailable (not stored)</i>\n"
           f"Available stock: <b>{available_stock}</b>\n"
           f"Used stock records: <b>{used_stock}</b>\n"
           f"Overall user balance: <b>{P_INR}{total_balance}</b>\n\n"
           f"{P_GIFT} <b>REFERRALS</b>\n"
           f"Total referrals: <b>{total_referrals}</b> | In period: <b>{period_referrals}</b>\n"
           f"Referral rewards issued: <i>Unavailable (not stored)</i>\n"
           f"Top referrers:\n{top_text}")
    buttons = [
        [Button.inline("Today", "adm_statsp|today"), Button.inline("This Week", "adm_statsp|week")],
        [Button.inline("This Month", "adm_statsp|month"), Button.inline("All Time", "adm_statsp|all")],
        [Button.inline("🔄 Refresh", f"adm_statsp|{period}")],
        [Button.inline("◀️ Back", "adm_adminmain")]
    ]
    await event.edit(msg, buttons=buttons)

async def manage_admins_menu(event):
    rows = cur.execute("SELECT user_id FROM admins").fetchall()
    msg = f"{PE_CROWN} <b>Manage Sub-Admins</b>\n\n"
    for r in rows: msg += f"{P_ACC} <code>{r[0]}</code>\n"
    btns = [[Button.inline("Add Admin", "adm_addadmin"), Button.inline("Edit Admin", "adm_editadminreq")],
            [Button.inline("Back", "adm_adminmain")]]
    await event.edit(msg, buttons=btns)

async def render_user_management(event, target_id):
    row = cur.execute(
        "SELECT user_id, balance, referred_by, total_deposited, joined_date, banned, discount "
        "FROM users WHERE user_id=?", (target_id,)
    ).fetchone()
    if not row:
        return await event.edit(f"{P_NO} <b>User not found.</b>", buttons=[[Button.inline("Back", "adm_adminmain")]])

    user_id, balance, referred_by, deposited, joined, banned, discount = row
    username = "Not available"
    name = "Not available"
    try:
        telegram_user = await bot.get_entity(int(user_id))
        username = f"@{html.escape(telegram_user.username)}" if telegram_user.username else "No username"
        name = html.escape(" ".join(filter(None, [telegram_user.first_name, telegram_user.last_name]))) or "No name"
    except Exception:
        pass

    order_row = cur.execute("SELECT COUNT(*), COALESCE(SUM(price), 0) FROM orders WHERE user_id=?", (user_id,)).fetchone()
    deposit_row = cur.execute(
        "SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM deposits WHERE user_id=? AND status='approved'", (user_id,)
    ).fetchone()
    referral_count = cur.execute("SELECT COUNT(*) FROM users WHERE referred_by=?", (user_id,)).fetchone()[0]
    order_count, spent = order_row
    approved_count, approved_total = deposit_row

    msg = (f"{P_ACC} <b>USER MANAGEMENT</b>\n\n"
           f"{P_ID} Telegram ID: <code>{user_id}</code>\n"
           f"👤 Username: <b>{username}</b>\n"
           f"🪪 Name: <b>{name}</b>\n"
           f"{P_MONEY} Balance: <b>{P_INR}{balance}</b>\n"
           f"{P_CARD} Deposited: <b>{P_INR}{deposited}</b> ({approved_count} approved, {P_INR}{approved_total})\n"
           f"{P_CART} Orders: <b>{order_count}</b> ({P_INR}{spent} spent)\n"
           f"{P_USERS} Referred by: <code>{referred_by if referred_by else 'None'}</code>\n"
           f"{P_USERS} Referrals: <b>{referral_count}</b>\n"
           f"{P_CAL} Joined: <b>{joined}</b>\n"
           f"{P_GIFT} Discount: <b>{discount}%</b>\n"
           f"{P_OFF} Status: <b>{'Banned' if banned else 'Active'}</b>")

    buttons = []
    if has_perm(event.sender_id, 'p_bal'):
        buttons.append([Button.inline("💰 Change Balance", f"adm_um_bal|{user_id}")])
        if banned:
            buttons.append([Button.inline("✅ Unban User", f"adm_um_ban|{user_id}|0")])
        else:
            buttons.append([Button.inline("🚫 Ban User", f"adm_um_ban|{user_id}|1")])
    buttons.append([Button.inline("Back to Admin", "adm_adminmain")])
    await event.edit(msg, buttons=buttons)

async def start_user_search(event, action):
    if not (has_perm(event.sender_id, 'p_stats') or has_perm(event.sender_id, 'p_bal')):
        return await event.answer("Not authorized.", alert=True)
    async with bot.conversation(event.chat_id, timeout=180) as conv:
        try:
            await conv.send_message(
                f"{P_ACC} <b>Enter Telegram numeric user ID:</b>\n\n"
                f"Search by username is unavailable because usernames are not stored in the database.\n"
                f"<i>Type /cancel to abort.</i>"
            )
            response = await conv.get_response()
            value = response.text.strip()
            if value.lower() == "/cancel":
                return await conv.send_message("✅ Cancelled.")
            if not value.isdigit() or int(value) <= 0:
                return await conv.send_message(f"{P_NO} Enter a valid numeric Telegram user ID.")
            target_id = int(value)
            if not cur.execute("SELECT 1 FROM users WHERE user_id=?", (target_id,)).fetchone():
                return await conv.send_message(f"{P_NO} <b>User not found.</b>")
            if action == "balance":
                await conv.send_message(
                    f"{P_MONEY} <b>Enter balance change for <code>{target_id}</code>:</b>\n"
                    f"Use a positive number to increase or a negative number to decrease.\n"
                    f"<i>Type /cancel to abort.</i>"
                )
                amount_text = (await conv.get_response()).text.strip()
                if amount_text.lower() == "/cancel":
                    return await conv.send_message("✅ Cancelled.")
                if not re.fullmatch(r"[+-]?\d+", amount_text) or int(amount_text) == 0:
                    return await conv.send_message(f"{P_NO} Enter a non-zero whole number, such as <code>500</code> or <code>-100</code>.")
                amount = int(amount_text)
            else:
                amount = None
            class ConversationEvent:
                sender_id = event.sender_id
                async def edit(self, text, buttons):
                    await conv.send_message(text, buttons=buttons)
            if amount is None:
                await render_user_management(ConversationEvent(), target_id)
            else:
                await confirm_balance_change(ConversationEvent(), target_id, amount)
        except asyncio.TimeoutError:
            await conv.send_message(f"{P_NO} Timed out. Please try again.")

async def confirm_balance_change(event, target_id, amount):
    row = cur.execute("SELECT balance FROM users WHERE user_id=?", (target_id,)).fetchone()
    if not row:
        return await event.answer("User not found.", alert=True)
    new_balance = row[0] + amount
    if new_balance < 0:
        return await event.answer("This change would make the balance negative.", alert=True)
    action = "increase" if amount > 0 else "decrease"
    admin_user_state[event.sender_id] = {"target_id": target_id, "amount": amount}
    await event.edit(
        f"{P_WARN} <b>Confirm balance change</b>\n\nUser: <code>{target_id}</code>\n"
        f"Current: <b>{P_INR}{row[0]}</b>\nChange: <b>{action} {P_INR}{abs(amount)}</b>\n"
        f"New balance: <b>{P_INR}{new_balance}</b>",
        buttons=[
            [Button.inline("✅ Confirm", f"adm_um_balcf|{target_id}"), Button.inline("❌ Cancel", f"adm_um|{target_id}")]
        ]
    )

async def start_balance_for_user(event, target_id):
    if not cur.execute("SELECT 1 FROM users WHERE user_id=?", (target_id,)).fetchone():
        return await event.answer("User not found.", alert=True)
    async with bot.conversation(event.chat_id, timeout=180) as conv:
        try:
            await conv.send_message(
                f"{P_MONEY} <b>Enter balance change for <code>{target_id}</code>:</b>\n"
                f"Use a positive number to increase or a negative number to decrease.\n"
                f"<i>Type /cancel to abort.</i>"
            )
            amount_text = (await conv.get_response()).text.strip()
            if amount_text.lower() == "/cancel":
                return await conv.send_message("✅ Cancelled.")
            if not re.fullmatch(r"[+-]?\d+", amount_text) or int(amount_text) == 0:
                return await conv.send_message(f"{P_NO} Enter a non-zero whole number, such as <code>500</code> or <code>-100</code>.")
            class ConversationEvent:
                sender_id = event.sender_id
                async def edit(self, text, buttons):
                    await conv.send_message(text, buttons=buttons)
            await confirm_balance_change(ConversationEvent(), target_id, int(amount_text))
        except asyncio.TimeoutError:
            await conv.send_message(f"{P_NO} Timed out. Please try again.")

async def edit_admin_menu(event, target_id):
    row = cur.execute("SELECT p_add_stock, p_manage_stock, p_stats, p_bal, p_settings FROM admins WHERE user_id=?", (target_id,)).fetchone()
    if not row: return await event.answer("Admin not found", alert=True)
    p = ["✅" if x==1 else "❌" for x in row]
    
    btns = [
        [Button.inline(f"Add Stock: {p[0]}", f"adm_tglperm|{target_id}|p_add_stock")],
        [Button.inline(f"Manage Stock: {p[1]}", f"adm_tglperm|{target_id}|p_manage_stock")],
        [Button.inline(f"Stats & Bcast: {p[2]}", f"adm_tglperm|{target_id}|p_stats")],
        [Button.inline(f"Bal & Users: {p[3]}", f"adm_tglperm|{target_id}|p_bal")],
        [Button.inline(f"Settings: {p[4]}", f"adm_tglperm|{target_id}|p_settings")],
        [Button.inline("Remove Admin", f"adm_deladmin|{target_id}")],
        [Button.inline("Back", "adm_manageadmins")]
    ]
    await event.edit(f"✏️ <b>Editing Admin:</b> <code>{target_id}</code>", buttons=btns)

async def send_manage_stock_page(event, page):
    limit = 10
    offset = (page - 1) * limit
    rows = cur.execute("SELECT DISTINCT country_name FROM stock ORDER BY country_name").fetchall()
    total = len(rows)
    countries = rows[offset:offset+limit]
    
    btns = []
    for (c,) in countries: 
        flag = get_flag_by_country_name(c)
        btns.append([Button.inline(f"{flag} {c}", f"adm_msc|{c}")])
    
    nav = []
    if page > 1: nav.append(Button.inline("Prev", f"adm_mspg|{page-1}"))
    if offset + limit < total: nav.append(Button.inline("Next", f"adm_mspg|{page+1}"))
    if nav: btns.append(nav)
    btns.append([Button.inline("Back", "adm_adminmain")])
    await event.edit(f"{PE_LOCATION} <b>Manage Stock</b> (Page {page})\nSelect a country to edit its properties:", buttons=btns)

async def send_manage_stock_country(event, c_name):
    years = cur.execute("SELECT DISTINCT account_year FROM stock WHERE country_name=? ORDER BY account_year DESC", (c_name,)).fetchall()
    flag = get_flag_by_country_name(c_name)
    btns = [
        [Button.inline("Edit Country Name", f"adm_msedit|name|{c_name}"), Button.inline("Edit Flag", f"adm_msedit|flag|{c_name}")],
        [Button.inline("Edit Common Price (All Years)", f"adm_msedit|cprice|{c_name}")]
    ]
    y_btns = []
    for (y,) in years: y_btns.append(Button.inline(f"{y}", f"adm_msedit|yprice|{c_name}|{y}"))
    
    for i in range(0, len(y_btns), 3): btns.append(y_btns[i:i+3])
    btns.append([Button.inline("Back", "adm_mspg|1")])
    await event.edit(f"{flag} <b>Managing: {c_name}</b>\nSelect an option to edit:", buttons=btns)

async def send_autoprice_page(event, page):
    limit = 10
    offset = (page - 1) * limit
    c_list = set([c[0] for c in COUNTRY_CODES.values()])
    db_countries = cur.execute("SELECT DISTINCT country_name FROM stock").fetchall()
    for (c,) in db_countries: c_list.add(c)
    
    custom_countries = cur.execute("SELECT DISTINCT name FROM custom_countries").fetchall()
    for (c,) in custom_countries: c_list.add(c)

    c_list = sorted(list(c_list))
    total = len(c_list)
    countries = c_list[offset:offset+limit]
    
    btns = []
    for c in countries: 
        flag = get_flag_by_country_name(c)
        btns.append([Button.inline(f"{flag} {c}", f"adm_apc|{c}")])
        
    nav = []
    if page > 1: nav.append(Button.inline("Prev", f"adm_appg|{page-1}"))
    if offset + limit < total: nav.append(Button.inline("Next", f"adm_appg|{page+1}"))
    if nav: btns.append(nav)
    btns.append([Button.inline("Add Custom Country", "adm_ap_add_country")])
    btns.append([Button.inline("Back", "adm_adminmain")])
    await event.edit(f"{PE_LIGHTNING} <b>Auto Price Setup</b> (Page {page})\nSelect a country to set fixed prices:", buttons=btns)

async def send_autoprice_country(event, c_name):
    flag = get_flag_by_country_name(c_name)
    btns = [[Button.inline("Set Common Price", f"adm_apset|{c_name}|Common")]]
    y_btns = []
    for y in range(2024, 1999, -1): y_btns.append(Button.inline(f"{y}", f"adm_apset|{c_name}|{y}"))
    for i in range(0, len(y_btns), 4): btns.append(y_btns[i:i+4])
    btns.append([Button.inline("Back", "adm_appg|1")])
    await event.edit(f"{flag} <b>Auto Price: {c_name}</b>\nSelect 'Common' for default price, or specific years:", buttons=btns)

async def welcome_manager_menu(event):
    uid = event.sender_id
    me = await bot.get_me()
    current = get_welcome_message(uid, get_setting("ref_percent", "3"), me.username or "")
    btns = [
        [Button.inline("✏️ Edit", "adm_welcome_edit"), Button.inline("👁 Preview", "adm_welcome_preview")],
        [Button.inline("🔄 Reset", "adm_welcome_reset")],
        [Button.inline("◀️ Back", "adm_adminmain")]
    ]
    await event.edit(f"📝 <b>Welcome Message</b>\n\n{current}", buttons=btns)

async def banner_manager_menu(event):
    enabled = get_setting("images_enabled", "off") == "on"
    status = "🟢 ON" if enabled else "🔴 OFF"
    banner_status = "configured" if get_setting("banner_photo") else "not configured"
    btns = [
        [Button.inline("➕ Add/Replace Banner", "adm_banner_add")],
        [Button.inline("🗑️ Delete Banner", "adm_banner_delete"), Button.inline("👁️ Preview Banner", "adm_banner_preview")],
        [Button.inline(f"Images {'ON' if enabled else 'OFF'}", "adm_banner_toggle")],
        [Button.inline("◀️ Back", "adm_adminmain")]
    ]
    await event.edit(f"🖼️ <b>Banner Images</b>\n\nStatus: {status}\nBanner: {banner_status}", buttons=btns)

async def store_settings_menu(event, flow):
    name = "Account" if flow == "single" else "Sessions"
    key = "account" if flow == "single" else "sessions"
    preview_label = "👁 Preview Account Store" if flow == "single" else "👁 Preview Sessions Store"
    buttons = [
        [Button.inline("✏️ Edit Message", f"adm_store_msg|{flow}"), Button.inline(preview_label, f"adm_store_preview|{flow}")],
        [Button.inline("🖼 Set Banner", f"adm_store_banner|{flow}"), Button.inline("👁 Preview Banner", f"adm_store_banner_preview|{flow}")],
        [Button.inline("🗑 Remove Banner", f"adm_store_banner_delete|{flow}")],
        [Button.inline("↩️ Back", "adm_store_messages")]
    ]
    return await event.edit(
        f"🛒 <b>{name} Store</b>\n\n"
        f"Message: {'customized' if get_setting(f'{key}_store_message') else 'default'}\n"
        f"Banner: {'configured' if get_setting(store_banner_key(flow)) else 'not configured'}",
        buttons=buttons
    )

async def store_messages_menu(event):
    return await event.edit(
        "📝 <b>Store Messages</b>\n\nChoose the store to configure.",
        buttons=[[Button.inline("🛒 Buy Account Message", "adm_store_config|single")],
                 [Button.inline("🔐 Buy Sessions Message", "adm_store_config|bulk")],
                 [Button.inline("↩️ Back", "adm_adminmain")]]
    )

async def store_buttons_menu(event):
    return await event.edit(
        "⚙️ <b>Store Buttons</b>\n\nChoose the store whose labels you want to edit.",
        buttons=[[Button.inline("🛒 Account Buttons", "adm_store_btns|single")],
                 [Button.inline("🔐 Sessions Buttons", "adm_store_btns|bulk")],
                 [Button.inline("↩️ Back", "adm_adminmain")]]
    )

async def store_button_editor(event, flow):
    name = "Account" if flow == "single" else "Sessions"
    labels = get_store_buttons(flow)
    rows = [[Button.inline(f"✏️ {key}: {label[:18]}", f"adm_store_btn|{flow}|{key}")] for key, label in labels.items()]
    rows.append([Button.inline("↩️ Back", "adm_store_buttons")])
    return await event.edit(f"⚙️ <b>{name} Store Buttons</b>\n\nSelect a label to edit.", buttons=rows)

async def preview_store(event, flow):
    class PreviewEvent:
        sender_id = event.sender_id
        chat_id = event.chat_id
    await render_account_store(PreviewEvent(), flow, 1, send_banner=True)
    return await event.answer("Preview sent.", alert=True)

async def admin_actions(event):
    data_full = event.data.decode()
    if not data_full.startswith("adm_"): return
    uid = event.sender_id
    action_data = data_full[4:]
    chat = event.chat_id
    
    if action_data == "adminmain":
        await event.delete()
        class FakeEvent: chat_id = chat; sender_id = uid
        return await admin_panel_handler(FakeEvent())

    if action_data.startswith("bcast_confirm|"):
        if not has_perm(uid, 'p_stats'):
            return await event.answer("Not authorized.", alert=True)
        owner_id = int(action_data.split("|", 1)[1])
        if owner_id != uid or owner_id not in broadcast_drafts:
            return await event.answer("Broadcast draft not found or expired.", alert=True)
        if owner_id in broadcast_jobs:
            return await event.answer("Broadcast is already running.", alert=True)
        draft = broadcast_drafts[owner_id]
        await event.answer("Broadcast started.", alert=True)
        asyncio.create_task(run_broadcast(owner_id, chat, draft))
        return

    if action_data.startswith("bcast_cancel|"):
        if not has_perm(uid, 'p_stats'):
            return await event.answer("Not authorized.", alert=True)
        owner_id = int(action_data.split("|", 1)[1])
        if owner_id != uid:
            return await event.answer("Not authorized.", alert=True)
        job = broadcast_jobs.get(owner_id)
        if job:
            job["cancelled"] = True
            return await event.answer("Cancellation requested.", alert=True)
        broadcast_drafts.pop(owner_id, None)
        await event.answer("Broadcast cancelled.", alert=True)
        return await event.edit("❌ <b>Broadcast cancelled.</b>", buttons=[[Button.inline("◀️ Back", "adm_adminmain")]])

    if action_data in {"maintenance", "general"}:
        if not has_perm(uid, 'p_settings'):
            return await event.answer("Not authorized.", alert=True)
        return await maintenance_menu(event) if action_data == "maintenance" else await general_settings_menu(event)

    if action_data == "maintenance_status":
        if not has_perm(uid, 'p_settings'):
            return await event.answer("Not authorized.", alert=True)
        status = "enabled" if is_maintenance_mode() else "disabled"
        return await event.answer(f"Maintenance mode is {status}.", alert=True)

    if action_data.startswith("maintenance_set|"):
        if not has_perm(uid, 'p_settings'):
            return await event.answer("Not authorized.", alert=True)
        desired = action_data.split("|", 1)[1]
        if desired not in {"on", "off"}:
            return await event.answer("Invalid maintenance state.", alert=True)
        admin_content_state[uid] = {"type": "maintenance_confirm", "value": desired}
        verb = "enable" if desired == "on" else "disable"
        return await event.edit(
            f"⚠️ <b>Confirm {verb} maintenance mode?</b>",
            buttons=[
                [Button.inline("✅ Confirm", f"adm_maintenance_confirm|{desired}"), Button.inline("❌ Cancel", "adm_maintenance")]
            ]
        )

    if action_data.startswith("maintenance_confirm|"):
        if not has_perm(uid, 'p_settings'):
            return await event.answer("Not authorized.", alert=True)
        desired = action_data.split("|", 1)[1]
        pending = admin_content_state.get(uid)
        if pending != {"type": "maintenance_confirm", "value": desired}:
            return await event.answer("This confirmation has expired.", alert=True)
        set_setting("maintenance_enabled", desired)
        admin_content_state.pop(uid, None)
        await event.answer("Maintenance mode updated.", alert=True)
        return await maintenance_menu(event)

    if action_data == "maintenance_message":
        if not has_perm(uid, 'p_settings'):
            return await event.answer("Not authorized.", alert=True)
        admin_content_state[uid] = "maintenance_message"
        return await event.edit(
            "✏️ <b>Send the new maintenance message.</b>\nHTML formatting is supported.",
            buttons=[[Button.inline("◀️ Cancel", "adm_maintenance")]]
        )

    if action_data.startswith("setting_edit|"):
        if not has_perm(uid, 'p_settings'):
            return await event.answer("Not authorized.", alert=True)
        setting_name = action_data.split("|", 1)[1]
        if setting_name not in {"support_url", "terms_url", "channel_links", "usdt_rate", "auto_cancel_seconds"}:
            return await event.answer("Invalid setting.", alert=True)
        admin_content_state[uid] = {"type": "general_setting", "name": setting_name}
        labels = {
            "support_url": "Support URL (http:// or https://)",
            "terms_url": "Terms URL (http:// or https://)",
            "channel_links": "channel URLs, comma-separated (http:// or https://)",
            "usdt_rate": "USDT rate in INR (positive number)",
            "auto_cancel_seconds": "Auto-cancel seconds (at least 1)"
        }
        return await event.edit(
            f"⚙️ <b>Enter {labels[setting_name]}:</b>\n\n"
            f"Current: <code>{html.escape(str(get_setting(setting_name, get_support_url() if setting_name == 'support_url' else get_terms_url() if setting_name == 'terms_url' else get_usdt_rate() if setting_name == 'usdt_rate' else get_auto_cancel_seconds())))}</code>",
            buttons=[[Button.inline("◀️ Cancel", "adm_general")]]
        )

    if action_data in {"userinfo", "bal", "ban"}:
        required_perm = 'p_stats' if action_data == "userinfo" else 'p_bal'
        if not has_perm(uid, required_perm):
            return await event.answer("Not authorized.", alert=True)
        return await start_user_search(event, "balance" if action_data == "bal" else "info")

    if action_data.startswith("um|"):
        if not (has_perm(uid, 'p_stats') or has_perm(uid, 'p_bal')):
            return await event.answer("Not authorized.", alert=True)
        return await render_user_management(event, int(action_data.split("|", 1)[1]))

    if action_data.startswith("um_bal|"):
        if not has_perm(uid, 'p_bal'):
            return await event.answer("Not authorized.", alert=True)
        target_id = int(action_data.split("|", 1)[1])
        return await start_balance_for_user(event, target_id)

    if action_data.startswith("um_balcf|"):
        if not has_perm(uid, 'p_bal'):
            return await event.answer("Not authorized.", alert=True)
        target_id = int(action_data.split("|", 1)[1])
        pending = admin_user_state.pop(uid, None)
        if not pending or pending.get("target_id") != target_id:
            return await event.answer("This confirmation has expired.", alert=True)
        amount = pending["amount"]
        cur.execute("UPDATE users SET balance=balance+? WHERE user_id=? AND balance+? >= 0", (amount, target_id, amount))
        if cur.rowcount != 1:
            db.rollback()
            return await event.answer("Balance change was not applied.", alert=True)
        db.commit()
        await event.answer("Balance updated.", alert=True)
        return await render_user_management(event, target_id)

    if action_data.startswith("um_ban|"):
        if not has_perm(uid, 'p_bal'):
            return await event.answer("Not authorized.", alert=True)
        _, target_text, desired_text = action_data.split("|")
        target_id, desired = int(target_text), int(desired_text)
        row = cur.execute("SELECT banned FROM users WHERE user_id=?", (target_id,)).fetchone()
        if not row:
            return await event.answer("User not found.", alert=True)
        if row[0] != desired:
            return await render_user_management(event, target_id)
        admin_user_state[uid] = {"ban_target": target_id, "ban_value": desired}
        verb = "ban" if desired else "unban"
        return await event.edit(
            f"{P_WARN} <b>Confirm {verb} for user <code>{target_id}</code>?</b>",
            buttons=[[Button.inline("✅ Confirm", f"adm_um_bancf|{target_id}|{desired}"), Button.inline("❌ Cancel", f"adm_um|{target_id}")]]
        )

    if action_data.startswith("um_bancf|"):
        if not has_perm(uid, 'p_bal'):
            return await event.answer("Not authorized.", alert=True)
        _, target_text, desired_text = action_data.split("|")
        target_id, desired = int(target_text), int(desired_text)
        pending = admin_user_state.pop(uid, None)
        if not pending or pending.get("ban_target") != target_id or pending.get("ban_value") != desired:
            return await event.answer("This confirmation has expired.", alert=True)
        cur.execute("UPDATE users SET banned=? WHERE user_id=?", (desired, target_id))
        db.commit()
        await event.answer("User status updated.", alert=True)
        return await render_user_management(event, target_id)

    if action_data in {"welcome", "welcome_edit", "welcome_cancel", "welcome_preview", "welcome_reset", "banner", "banner_add", "banner_cancel", "banner_delete", "banner_preview", "banner_toggle", "store_messages", "store_buttons"} or action_data.startswith(("store_config|", "store_msg|", "store_preview|", "store_banner", "store_btns|", "store_btn|")):
        if not (uid in ADMIN_IDS or has_perm(uid, 'p_settings')):
            return await event.answer("Not authorized.", alert=True)

    if action_data == "store_messages":
        return await store_messages_menu(event)
    if action_data == "store_buttons":
        return await store_buttons_menu(event)
    if action_data.startswith("store_config|"):
        flow = action_data.split("|", 1)[1]
        if flow not in {"single", "bulk"}: return await event.answer("Invalid store.", alert=True)
        return await store_settings_menu(event, flow)
    if action_data.startswith("store_msg|"):
        flow = action_data.split("|", 1)[1]
        admin_content_state[uid] = {"type": "store_message", "flow": flow}
        return await event.edit(
            "📝 <b>Send the complete store message.</b>\n"
            "HTML formatting is supported. Use {rate}, {available}, {products}, and {page} for dynamic values.",
            buttons=[[Button.inline("↩️ Cancel", f"adm_store_config|{flow}")]]
        )
    if action_data.startswith("store_preview|"):
        flow = action_data.split("|", 1)[1]
        return await preview_store(event, flow)
    if action_data.startswith("store_banner|"):
        flow = action_data.split("|", 1)[1]
        admin_content_state[uid] = {"type": "store_banner", "flow": flow}
        return await event.edit("🖼 <b>Send the store banner image/photo.</b>", buttons=[[Button.inline("↩️ Cancel", f"adm_store_config|{flow}")]])
    if action_data.startswith("store_banner_preview|"):
        flow = action_data.split("|", 1)[1]
        banner = get_banner_reference(get_setting(store_banner_key(flow)))
        if not banner: return await event.answer("No banner configured.", alert=True)
        try:
            await bot.send_file(uid, banner)
            return await event.answer("Preview sent.", alert=True)
        except Exception:
            return await event.answer("Banner reference expired. Upload it again.", alert=True)
    if action_data.startswith("store_banner_delete|"):
        flow = action_data.split("|", 1)[1]
        delete_setting(store_banner_key(flow))
        await event.answer("Banner removed.", alert=True)
        return await store_settings_menu(event, flow)
    if action_data.startswith("store_btns|"):
        flow = action_data.split("|", 1)[1]
        return await store_button_editor(event, flow)
    if action_data.startswith("store_btn|"):
        parts = action_data.split("|")
        if len(parts) != 3 or parts[1] not in {"single", "bulk"} or parts[2] not in get_store_buttons(parts[1]):
            return await event.answer("Invalid store button.", alert=True)
        admin_content_state[uid] = {"type": "store_button", "flow": parts[1], "key": parts[2]}
        return await event.edit(f"✏️ <b>Send the new label for {parts[2]}.</b>", buttons=[[Button.inline("↩️ Cancel", f"adm_store_btns|{parts[1]}")]])

    if action_data in {"welcome", "welcome_edit", "welcome_cancel", "welcome_preview", "welcome_reset", "banner", "banner_add", "banner_cancel", "banner_delete", "banner_preview", "banner_toggle"} and not (uid in ADMIN_IDS or has_perm(uid, 'p_settings')):
        return await event.answer("Not authorized.", alert=True)

    if action_data == "welcome":
        return await welcome_manager_menu(event)
    if action_data == "welcome_edit":
        admin_content_state[uid] = "welcome"
        return await event.edit("📝 <b>Send the new welcome message.</b>\nHTML formatting is supported.", buttons=[[Button.inline("◀️ Cancel", "adm_welcome_cancel")]])
    if action_data == "welcome_cancel":
        admin_content_state.pop(uid, None)
        return await welcome_manager_menu(event)
    if action_data == "welcome_preview":
        me = await bot.get_me()
        await bot.send_message(uid, get_welcome_message(uid, get_setting("ref_percent", "3"), me.username or ""))
        return await event.answer("Preview sent.", alert=True)
    if action_data == "welcome_reset":
        delete_setting("welcome_message")
        await event.answer("Welcome message reset.", alert=True)
        return await welcome_manager_menu(event)
    if action_data == "banner":
        return await banner_manager_menu(event)
    if action_data == "banner_add":
        admin_content_state[uid] = "banner"
        return await event.edit("🖼️ <b>Send the banner image/photo.</b>", buttons=[[Button.inline("◀️ Cancel", "adm_banner_cancel")]])
    if action_data == "banner_cancel":
        admin_content_state.pop(uid, None)
        return await banner_manager_menu(event)
    if action_data == "banner_toggle":
        set_setting("images_enabled", "off" if get_setting("images_enabled", "off") == "on" else "on")
        return await banner_manager_menu(event)
    if action_data == "banner_delete":
        delete_setting("banner_photo")
        await event.answer("Banner deleted.", alert=True)
        return await banner_manager_menu(event)
    if action_data == "banner_preview":
        banner = get_banner_media()
        if not banner:
            return await event.answer("No banner configured.", alert=True)
        try:
            await bot.send_file(uid, banner)
            return await event.answer("Preview sent.", alert=True)
        except Exception:
            return await event.answer("Banner reference expired. Upload it again.", alert=True)

    if action_data == "togglebot" and (uid in ADMIN_IDS or has_perm(uid, 'p_settings')):
        new_status = 'off' if is_bot_online() else 'on'
        cur.execute("UPDATE settings SET value=? WHERE key='bot_status'", (new_status,))
        db.commit()
        await event.answer(f"Bot turned {new_status.upper()}", alert=True)
        class FakeEvent: chat_id = chat; sender_id = uid
        await admin_panel_handler(FakeEvent())
        await event.delete()
        return

    elif action_data == "stats" and (uid in ADMIN_IDS or has_perm(uid, 'p_stats')):
        return await render_admin_stats(event, "all")

    elif action_data.startswith("statsp|") and (uid in ADMIN_IDS or has_perm(uid, 'p_stats')):
        return await render_admin_stats(event, action_data.split("|", 1)[1])

    elif action_data == "payments" and (uid in ADMIN_IDS or has_perm(uid, 'p_settings')):
        btns = [
            [Button.inline("Add Payment Method", "adm_addpay")],
            [Button.inline("Remove Payment Method", "adm_delpay")],
            [Button.inline("Back to Admin", "adm_adminmain")]
        ]
        return await event.edit(f"{P_CARD} <b>Manage Payment Methods</b>", buttons=btns)

    elif action_data == "manageadmins" and uid in ADMIN_IDS:
        return await manage_admins_menu(event)

    elif action_data.startswith("tglperm|") and uid in ADMIN_IDS:
        _, t_id, p_name = action_data.split("|")
        cur.execute(f"UPDATE admins SET {p_name} = CASE WHEN {p_name}=1 THEN 0 ELSE 1 END WHERE user_id=?", (t_id,))
        db.commit()
        return await edit_admin_menu(event, t_id)
        
    elif action_data.startswith("deladmin|") and uid in ADMIN_IDS:
        t_id = action_data.split("|")[1]
        cur.execute("DELETE FROM admins WHERE user_id=?", (t_id,))
        db.commit()
        await event.answer("✅ Admin Removed", alert=True)
        return await manage_admins_menu(event)

    elif action_data == "managestock" and (uid in ADMIN_IDS or has_perm(uid, 'p_manage_stock')):
        return await send_manage_stock_page(event, 1)
    elif action_data.startswith("mspg|") and (uid in ADMIN_IDS or has_perm(uid, 'p_manage_stock')):
        return await send_manage_stock_page(event, int(action_data.split("|")[1]))
    elif action_data.startswith("msc|") and (uid in ADMIN_IDS or has_perm(uid, 'p_manage_stock')):
        return await send_manage_stock_country(event, action_data.split("|")[1])
    elif action_data == "autoprice" and (uid in ADMIN_IDS or has_perm(uid, 'p_manage_stock')):
        return await send_autoprice_page(event, 1)
    elif action_data.startswith("appg|") and (uid in ADMIN_IDS or has_perm(uid, 'p_manage_stock')):
        return await send_autoprice_page(event, int(action_data.split("|")[1]))
    elif action_data.startswith("apc|") and (uid in ADMIN_IDS or has_perm(uid, 'p_manage_stock')):
        return await send_autoprice_country(event, action_data.split("|")[1])
        
    elif action_data == "backupusr" and (uid in ADMIN_IDS or has_perm(uid, 'p_settings')):
        cur.execute("SELECT * FROM users")
        with open("users_backup.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f); w.writerow([i[0] for i in cur.description]); w.writerows(cur.fetchall())
        await bot.send_file(chat, "users_backup.csv", caption=f"{P_USERS} <b>Users Backup CSV</b>")
        os.remove("users_backup.csv")
        return await event.answer("✅ Backup Generated!", alert=True)

    async with bot.conversation(chat, timeout=600) as conv:
        async def get_reply(txt):
            await conv.send_message(txt + "\n\n<i>(Type /cancel to abort)</i>")
            resp = await conv.get_response()
            if resp.text == "/cancel": raise ValueError("Cancelled")
            return resp

        try:
            if action_data == "ap_add_country" and (uid in ADMIN_IDS or has_perm(uid, 'p_manage_stock')):
                code = (await get_reply(f"{P_PHONE} <b>Enter Country Calling Code (without +):</b>\n<i>Example: 91</i>")).text.replace("+", "").strip()
                flag = html.escape((await get_reply(f"{P_FLAG} <b>Enter Country Flag Emoji:</b>\n<i>Example: 🇮🇳</i>")).text.strip())
                name = html.escape((await get_reply(f"{P_GLOBE} <b>Enter Country Name:</b>\n<i>Example: India</i>")).text.strip())
                
                cur.execute("INSERT OR REPLACE INTO custom_countries (code, name, flag) VALUES (?,?,?)", (code, name, flag))
                db.commit()
                await conv.send_message(f"{P_YES} <b>Custom Country Added Successfully!</b>\n{flag} {name} (+{code})\n\n<i>It will now automatically be recognized when adding stock!</i>")

            elif action_data == "addadmin" and uid in ADMIN_IDS:
                new_ad = int((await get_reply(f"{P_ACC} <b>Enter User ID for new Admin:</b>")).text)
                cur.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (new_ad,))
                db.commit()
                await conv.send_message(f"{P_YES} Admin added!")
                class FakeEvent: 
                    async def edit(self, text, buttons): await bot.send_message(chat, text, buttons=buttons)
                    async def answer(self, txt, alert): pass
                await edit_admin_menu(FakeEvent(), new_ad)
                
            elif action_data == "editadminreq" and uid in ADMIN_IDS:
                t_id = int((await get_reply(f"{P_ACC} <b>Enter User ID to edit:</b>")).text)
                class FakeEvent: 
                    async def edit(self, text, buttons): await bot.send_message(chat, text, buttons=buttons)
                    async def answer(self, txt, alert): pass
                await edit_admin_menu(FakeEvent(), t_id)

            elif action_data.startswith("msedit|") and (uid in ADMIN_IDS or has_perm(uid, 'p_manage_stock')):
                parts = action_data.split("|")
                action, c_name = parts[1], parts[2]
                
                if action == "name":
                    new_name = html.escape((await get_reply(f"{P_DOC} <b>Enter NEW Name for {c_name}:</b>")).text)
                    cur.execute("UPDATE stock SET country_name=? WHERE country_name=?", (new_name, c_name))
                    cur.execute("UPDATE auto_prices SET country=? WHERE country=?", (new_name, c_name))
                    db.commit()
                    await conv.send_message(f"{P_YES} Country '{c_name}' successfully renamed to '{new_name}'!")
                    
                elif action == "flag":
                    new_flag = html.escape((await get_reply(f"{P_FLAG} <b>Enter NEW Flag Emoji for {c_name}:</b>")).text)
                    cur.execute("UPDATE stock SET country_icon=? WHERE country_name=?", (new_flag, c_name))
                    db.commit()
                    await conv.send_message(f"{P_YES} Flag updated to {new_flag} for '{c_name}'!")
                    
                elif action == "cprice":
                    new_p = int((await get_reply(f"{P_MONEY} <b>Enter NEW Common Price for all {c_name} accounts:</b>")).text)
                    cur.execute("UPDATE stock SET price=? WHERE country_name=?", (new_p, c_name))
                    db.commit()
                    await conv.send_message(f"{P_YES} All existing '{c_name}' accounts updated to {P_INR}{new_p}!")
                    
                elif action == "yprice":
                    year = parts[3]
                    new_p = int((await get_reply(f"{P_MONEY} <b>Enter NEW Price for {c_name} ({year}):</b>")).text)
                    cur.execute("UPDATE stock SET price=? WHERE country_name=? AND account_year=?", (new_p, c_name, year))
                    db.commit()
                    await conv.send_message(f"{P_YES} All existing '{c_name}' ({year}) accounts updated to {P_INR}{new_p}!")
                    
            elif action_data.startswith("apset|") and (uid in ADMIN_IDS or has_perm(uid, 'p_manage_stock')):
                parts = action_data.split("|")
                c_name, year = parts[1], parts[2]
                new_p = int((await get_reply(f"{P_ASST} <b>Enter Auto-Price for {c_name} ({year}):</b>\n<i>(Enter 0 to remove this auto-price)</i>")).text)
                if new_p == 0:
                    cur.execute("DELETE FROM auto_prices WHERE country=? AND year=?", (c_name, year))
                    await conv.send_message(f"{P_YES} Auto-Price for {c_name} ({year}) removed!")
                else:
                    cur.execute("INSERT OR REPLACE INTO auto_prices (country, year, price) VALUES (?,?,?)", (c_name, year, new_p))
                    await conv.send_message(f"{P_YES} Auto-Price for {c_name} ({year}) set to {P_INR}{new_p}! Incoming accounts will use this price automatically.")
                db.commit()

            elif action_data == "addpay" and (uid in ADMIN_IDS or has_perm(uid, 'p_settings')):
                name = html.escape((await get_reply(f"{P_CARD} <b>Enter Payment Method Name:</b>\n<i>(e.g., Binance Pay, TRX)</i>")).text)
                qr_msg = await get_reply(f"📸 <b>Send QR Code Image:</b>\n<i>(Or type <code>skip</code> if no QR needed)</i>")
                qr_path = ""
                if qr_msg.photo:
                    qr_path = f"qr_{int(time.time())}.jpg"
                    await bot.download_media(qr_msg, qr_path)
                
                cap_msg = (await get_reply(f"{P_DOC} <b>Enter Payment Caption:</b>\n<i>(Use <code>text</code> to make wallet IDs or UPI copyable)</i>")).text
                cap_msg = html.escape(cap_msg).replace("&lt;code&gt;", "<code>").replace("&lt;/code&gt;", "</code>")
                cur.execute("INSERT INTO custom_payments (name, caption, qr_file_id) VALUES (?,?,?)", (name, cap_msg, qr_path))
                db.commit()
                await conv.send_message(f"{P_YES} Payment Method '{name}' added successfully!")

            elif action_data == "delpay" and (uid in ADMIN_IDS or has_perm(uid, 'p_settings')):
                rows = cur.execute("SELECT id, name FROM custom_payments").fetchall()
                if not rows: return await conv.send_message(f"{P_NO} No custom payment methods.")
                msg = f"{P_DOC} <b>Reply with the ID of the method to delete:</b>\n\n"
                for r in rows: msg += f"ID: {r[0]} - {r[1]}\n"
                del_id = (await get_reply(msg)).text
                try:
                    del_id = int(del_id)
                    file_path = cur.execute("SELECT qr_file_id FROM custom_payments WHERE id=?", (del_id,)).fetchone()
                    if file_path and file_path[0] and os.path.exists(file_path[0]): os.remove(file_path[0])
                    cur.execute("DELETE FROM custom_payments WHERE id=?", (del_id,))
                    db.commit()
                    await conv.send_message(f"{P_YES} Deleted!")
                except: await conv.send_message(f"{P_NO} Invalid ID.")

            elif action_data == "addzip" and (uid in ADMIN_IDS or has_perm(uid, 'p_add_stock')):
                resp = await get_reply(f"{P_PKG} <b>Send the ZIP file containing <code>.session</code> files:</b>")
                if not resp.file or not resp.file.name.endswith('.zip'): return await conv.send_message(f"{P_NO} Invalid file.")
                
                await conv.send_message(f"{P_WAIT} <b>Extracting & Scanning Accounts...</b>")
                zip_path = await bot.download_media(resp, "temp_sessions.zip")
                extracted_dir = f"temp_extracted_{int(time.time())}"
                os.makedirs(extracted_dir, exist_ok=True)
                with zipfile.ZipFile(zip_path, 'r') as zip_ref: zip_ref.extractall(extracted_dir)

                groups = {}
                for file in os.listdir(extracted_dir):
                    if not file.endswith(".session"): continue
                    sess_path = os.path.join(extracted_dir, file)
                    clean_path = sess_path[:-8]
                    try:
                        client = TelegramClient(clean_path, API_ID, API_HASH)
                        await client.connect()
                        if not await client.is_user_authorized(): await client.disconnect(); continue
                        me = await client.get_me()
                        phone = getattr(me, 'phone', None)
                        if not phone: await client.disconnect(); continue
                        
                        c_name, c_icon = get_country_info(phone)
                        pwd = await client(GetPasswordRequest())
                        has_2fa = pwd.has_password
                        year = await detect_account_year(client)
                        await client.disconnect()

                        key = (c_name, year, has_2fa)
                        if key not in groups: groups[key] = []
                        groups[key].append({"phone": phone, "path": clean_path, "c_icon": c_icon})
                    except Exception as e: logger.error(f"Scan error: {e}")

                for key in list(groups.keys()):
                    if key[0] == "Unknown":
                        sample_phone = groups[key][0]["phone"]
                        await conv.send_message(f"{P_WARN} <b>Country not recognized for +{sample_phone}!</b>")
                        new_icon = html.escape((await get_reply(f"{P_FLAG} <b>Enter Country Flag Emoji:</b>\n<i>Example: 🇮🇳</i>")).text)
                        new_name = html.escape((await get_reply(f"{P_GLOBE} <b>Enter Country Name:</b>\n<i>Example: India</i>")).text)
                        new_key = (new_name, key[1], key[2])
                        groups[new_key] = groups.pop(key)
                        for acc in groups[new_key]: acc["c_icon"] = new_icon

                success = 0
                for (c_name, year, has_2fa), accs in groups.items():
                    c_icon = accs[0]["c_icon"]
                    twofa_pass = "None"
                    if has_2fa: twofa_pass = html.escape((await get_reply(f"{P_2FA} <b>Enter 2FA Password for {len(accs)}x {c_name} accounts:</b>")).text)

                    auto_row = cur.execute("SELECT price FROM auto_prices WHERE country=? AND year=?", (c_name, str(year))).fetchone()
                    if not auto_row: auto_row = cur.execute("SELECT price FROM auto_prices WHERE country=? AND year='Common'", (c_name,)).fetchone()

                    if auto_row:
                        price = auto_row[0]
                        await conv.send_message(f"⚡ <b>Auto-Price Applied:</b> {len(accs)}x {c_name} ({year}) at {P_INR}{price}.")
                    else:
                        existing_price = cur.execute("SELECT price FROM stock WHERE country_name=? LIMIT 1", (c_name,)).fetchone()
                        if existing_price:
                            price = existing_price[0]
                            await conv.send_message(f"⚡ <b>Auto-Added:</b> {len(accs)}x {c_name} at {P_INR}{price} (Copied from DB).")
                        else:
                            price = int((await get_reply(f"📌 Found {len(accs)}x {c_name} ({year}).\n{P_MONEY} Enter Price (₹):")).text)

                    for acc in accs:
                        perm_base = f"sessions/{acc['phone']}"
                        for ext in ['.session', '.session-wal', '.session-shm', '.session-journal']:
                            if os.path.exists(acc['path'] + ext): shutil.move(acc['path'] + ext, perm_base + ext)
                        cur.execute("INSERT OR REPLACE INTO stock (phone, session_file, country_name, country_icon, account_year, category, price, available, twofa) VALUES (?,?,?,?,?,?,?,?,?)", 
                                    (acc['phone'], perm_base + ".session", c_name, c_icon, year, 'Good', price, 1, twofa_pass))
                        success += 1
                db.commit()
                os.remove(zip_path); shutil.rmtree(extracted_dir)
                await conv.send_message(f"{P_YES} <b>Bulk Interactive Upload Complete!</b>\n{P_ON} Added: {success}")

            elif action_data == "addstock" and (uid in ADMIN_IDS or has_perm(uid, 'p_add_stock')):
                phone = (await get_reply(f"{P_PHONE} Enter Phone (+919999...):")).text.replace(" ", "").replace("+", "")
                sp = f"sessions/{phone}"
                client = TelegramClient(sp, API_ID, API_HASH)
                await client.connect()
                sreq = await client.send_code_request(phone)
                
                twofa_pass = "None"
                try: 
                    await client.sign_in(phone, (await get_reply(f"{P_OTP} OTP:")).text, phone_code_hash=sreq.phone_code_hash)
                except SessionPasswordNeededError: 
                    twofa_pass = html.escape((await get_reply(f"{P_2FA} 2FA Pass required. Enter it now:")).text)
                    await client.sign_in(password=twofa_pass)
                
                c_name, c_icon = get_country_info(phone)
                
                if c_name == "Unknown":
                    await conv.send_message(f"{P_WARN} <b>Country not recognized for +{phone}!</b>")
                    c_icon = html.escape((await get_reply(f"{P_FLAG} <b>Enter Country Flag Emoji:</b>\n<i>Example: 🇮🇳</i>")).text)
                    c_name = html.escape((await get_reply(f"{P_GLOBE} <b>Enter Country Name:</b>\n<i>Example: India</i>")).text)
                
                auto_year = await detect_account_year(client)
                await client.disconnect()
                
                year = int((await get_reply(f"{P_CAL} Detected Year: <b>{auto_year}</b>\nReply with Year to confirm or change:")).text)
                auto_row = cur.execute("SELECT price FROM auto_prices WHERE country=? AND year=?", (c_name, str(year))).fetchone()
                if not auto_row: auto_row = cur.execute("SELECT price FROM auto_prices WHERE country=? AND year='Common'", (c_name,)).fetchone()

                if auto_row:
                    price = auto_row[0]
                    await conv.send_message(f"⚡ <b>Auto-Price Applied:</b> {P_INR}{price} for {c_name} ({year})")
                else:
                    existing_price = cur.execute("SELECT price FROM stock WHERE country_name=? LIMIT 1", (c_name,)).fetchone()
                    if existing_price:
                        price = existing_price[0]
                        await conv.send_message(f"⚡ <b>Auto-detected Price:</b> {P_INR}{price} for {c_name}")
                    else: price = int((await get_reply(f"{P_MONEY} Price (₹):")).text)
                
                cur.execute("INSERT OR REPLACE INTO stock (phone, session_file, country_name, country_icon, account_year, category, price, available, twofa) VALUES (?,?,?,?,?,?,?,?,?)", 
                            (phone, sp + ".session", c_name, c_icon, year, 'Good', price, 1, twofa_pass))
                db.commit()
                await conv.send_message(f"{P_YES} Added!")

            elif action_data == "supporturl" and (uid in ADMIN_IDS or has_perm(uid, 'p_settings')):
                url = (await get_reply("🔗 Enter new Support URL (must start with http:// or https://):")).text
                if not url.startswith("http"): url = "https://" + url.replace("@", "t.me/")
                cur.execute("UPDATE settings SET value=? WHERE key='support_url'", (url,))
                db.commit()
                await conv.send_message(f"{P_YES} Support URL updated.")

            elif action_data == "bcast" and (uid in ADMIN_IDS or has_perm(uid, 'p_stats')):
                message = await get_reply(f"{P_DOC} <b>Send the message or media to broadcast.</b>\nText, photo, video, and document captions are preserved.\nSupports HTML & tg-emoji tags.")
                if not message.text and not message.media:
                    return await conv.send_message(f"{P_NO} Empty messages cannot be broadcast.")
                btn_name = (await get_reply(f"🔘 <b>Button Name (or 'skip'):</b>")).text
                url = (await get_reply("🔗 <b>URL:</b>")).text if btn_name.lower() != 'skip' else None
                btns = [[Button.url(btn_name, url)]] if url else None
                broadcast_drafts[uid] = {
                    "message": message,
                    "text": message.text or "",
                    "caption": message.message or message.text or "",
                    "buttons": btns
                }
                await send_broadcast_preview(uid, broadcast_drafts[uid])
                await conv.send_message(f"{P_EYE} Preview sent. Confirm or cancel it using the buttons below.")

            elif action_data == "discount" and (uid in ADMIN_IDS or has_perm(uid, 'p_settings')):
                t_uid = int((await get_reply(f"{P_ACC} <b>User ID:</b>")).text)
                pct = int((await get_reply(f"{P_GIFT} <b>Discount % (0 to remove):</b>")).text)
                cur.execute("UPDATE users SET discount=? WHERE user_id=?", (pct, t_uid))
                db.commit()
                await conv.send_message(f"{P_YES} User {t_uid} has {pct}% discount.")
                
            elif action_data == "refpct" and (uid in ADMIN_IDS or has_perm(uid, 'p_settings')):
                pct = int((await get_reply(f"{P_USERS} <b>New Referral %:</b>")).text)
                cur.execute("UPDATE settings SET value=? WHERE key='ref_percent'", (str(pct),))
                db.commit()
                await conv.send_message(f"{P_YES} Ref revenue set to {pct}%.")

            elif action_data == "usdtrate" and (uid in ADMIN_IDS or has_perm(uid, 'p_settings')):
                r = float((await get_reply(f"{P_USDT} <b>New USDT Rate (INR):</b>")).text)
                cur.execute("UPDATE settings SET value=? WHERE key='usdt_rate'", (str(r),))
                db.commit()
                await conv.send_message(f"{P_YES} Rate set to {r}.")

            elif action_data == "restoreusr" and (uid in ADMIN_IDS or has_perm(uid, 'p_settings')):
                resp = await get_reply(f"📤 <b>Send the <code>users_backup.csv</code> file:</b>")
                if not resp.file or not resp.file.name.endswith('.csv'): return await conv.send_message(f"{P_NO} Invalid file.")
                await bot.download_media(resp, "temp_restore.csv")
                with open("temp_restore.csv", "r", encoding="utf-8") as f:
                    reader = csv.reader(f); next(reader); count = 0
                    for row in reader:
                        try:
                            cur.execute("INSERT OR REPLACE INTO users (user_id, balance, referred_by, total_deposited, joined_date, banned, discount, terms_accepted) VALUES (?,?,?,?,?,?,?,?)", 
                                        (int(row[0]), int(row[1]), row[2] if row[2] else None, int(row[3]), row[4], int(row[5]), int(row[6]), int(row[7])))
                            count += 1
                        except: pass
                db.commit()
                os.remove("temp_restore.csv")
                await conv.send_message(f"{P_YES} Restored {count} users.")

        except ValueError: await conv.send_message(f"{P_NO} Cancelled.")
        except Exception as e: await conv.send_message(f"{P_NO} Error: {e}")

# ================= CORE EVENT ROUTERS =================
@bot.on(events.NewMessage(pattern=r"(?i)^/start"))
async def handle_start(e):
    try:
        uid = e.sender_id
        if not uid: return
        
        ensure_user(uid)
        if is_user_banned(uid): return

        if (not is_bot_online() or is_maintenance_mode()) and not is_admin(uid):
            return await e.respond(get_maintenance_message() if is_maintenance_mode() else f"{P_OFF} <b>Bot is currently under maintenance.</b> Please try again later.")
        
        session_buy_state.pop(uid, None)
        deposit_input.pop(uid, None)

        text = e.text or ''
        if len(text.split()) > 1:
            start_param = text.split()[1]
            if start_param.startswith("ref_"):
                ref = start_param.replace("ref_", "")
                if ref.isdigit() and int(ref) != uid:
                    cur.execute("UPDATE users SET referred_by=? WHERE user_id=? AND referred_by IS NULL", (int(ref), uid))
                    db.commit()

        row = cur.execute("SELECT terms_accepted FROM users WHERE user_id=?", (uid,)).fetchone()
        terms_acc = row[0] if row else 0
        if not terms_acc:
            msg = (f"{PE_FLOWER} <b>STEP 1/2 — TERMS & CONDITIONS</b>\n\n"
                   f"Please read and accept our Terms & Conditions before using the bot.")
            return await e.respond(msg, buttons=get_terms_buttons())

        is_joined = await check_channel_joined(uid)
        if not is_joined:
            msg = ("🔒 <b>STEP 2/2 — JOIN REQUIRED</b>\n\n"
                   "Join our official channel(s) below, then tap Verify to continue.\n\n"
                   "Thank you for supporting us ❤️")
            return await e.respond(msg, buttons=get_join_buttons())

        await send_main_menu(e, uid)
    except Exception as ex: 
        print(f"Start Error: {ex}")

@bot.on(events.NewMessage())
async def handle_all_messages(e):
    try:
        uid = e.sender_id
        if not uid: return
        if getattr(e, 'text', None) and e.text.startswith('/') and not (e.text.strip().lower() == '/cancel' and uid in admin_content_state): return
        if (not is_bot_online() or is_maintenance_mode()) and not is_admin(uid):
            return await e.respond(get_maintenance_message() if is_maintenance_mode() else f"{P_OFF} <b>Bot is currently under maintenance.</b> Please try again later.")
        
        ensure_user(uid)
        if is_user_banned(uid): return

        if uid in waiting_proof and (e.photo or (e.text and "http" in e.text)):
            info = waiting_proof.pop(uid)
            final_amt = info['amount']
            if info['method'] == "Cwallet": final_amt = int(final_amt * 1.05)
            
            screenshot_path = None
            if e.photo:
                screenshot_path = f"screenshots/dep_{uid}_{int(time.time())}.jpg"
                os.makedirs("screenshots", exist_ok=True)
                await bot.download_media(e.photo, screenshot_path)
            
            cur.execute("INSERT INTO deposits (user_id, amount, method_name, status, screenshot) VALUES (?,?,?,?,?)", 
                       (uid, final_amt, info['method'], "pending", screenshot_path))
            db.commit()
            dep_id = cur.lastrowid
            await e.reply(f"{PE_GIFT} Deposit request submitted! Please wait for admin approval.")
            
            cap = f"{PE_LIGHTNING} <b>NEW DEPOSIT REQUEST</b>\n{P_ACC} User: <code>{uid}</code>\n{P_MONEY} Request: <b>{P_INR}{info['amount']}</b>\n{P_CARD} Method: {info['method']}\n{P_ID} Ref: <code>{dep_id}</code>\n"
            if screenshot_path:
                cap += f"{P_SCREEN} Screenshot: ✅ Received\n"
            else:
                cap += f"{P_SCREEN} Screenshot: ❌ Not Provided\n"
            
            btns = [[Button.inline(f"✅ Accept (₹{final_amt})", f"dep_acc|{dep_id}|{uid}|{info['method']}|exact|{final_amt}"), Button.inline("❌ Reject", f"dep_rej|{dep_id}|{uid}")],
                    [Button.inline("📝 Custom Amount", f"dep_acc|{dep_id}|{uid}|{info['method']}|custom|0")]]
            
            try:
                if e.photo:
                    await bot.send_message(LOG_CHANNEL_ID, cap, file=e.media, buttons=btns)
                else:
                    await bot.send_message(LOG_CHANNEL_ID, cap + f"\n🔗 Hash: {html.escape(e.text)}", buttons=btns)
            except Exception as log_err:
                logger.error(f"Failed to log deposit: {log_err}")
            return

        text = e.text or ""
        if is_admin(uid) and uid in admin_content_state:
            content_type = admin_content_state[uid]
            if text.strip().lower() == "/cancel":
                admin_content_state.pop(uid, None)
                return await e.reply("✅ Cancelled.")
            if isinstance(content_type, dict) and content_type.get("type") == "store_message":
                if not text.strip():
                    return await e.reply("❌ Store message cannot be empty.")
                set_setting(f"{'account' if content_type['flow'] == 'single' else 'sessions'}_store_message", text)
                flow = content_type["flow"]
                admin_content_state.pop(uid, None)
                return await e.reply("✅ Store message saved.", buttons=[[Button.inline("↩️ Back", f"adm_store_config|{flow}")]])
            if isinstance(content_type, dict) and content_type.get("type") == "store_button":
                label = text.strip()
                if not label or len(label.encode("utf-8")) > 60:
                    return await e.reply("❌ Enter a non-empty label up to 60 bytes.")
                flow, button_key = content_type["flow"], content_type["key"]
                labels = get_store_buttons(flow)
                labels[button_key] = label
                set_setting(f"{'account' if flow == 'single' else 'sessions'}_button_labels", json.dumps(labels, ensure_ascii=False))
                admin_content_state.pop(uid, None)
                return await e.reply("✅ Button label saved.", buttons=[[Button.inline("↩️ Back", f"adm_store_btns|{flow}")]])
            if isinstance(content_type, dict) and content_type.get("type") == "store_banner":
                if not e.photo:
                    return await e.reply("❌ Please send a Telegram photo.")
                photo = e.photo
                reference = {"id": photo.id, "access_hash": photo.access_hash, "file_reference": photo.file_reference.hex()}
                set_setting(store_banner_key(content_type["flow"]), json.dumps(reference))
                flow = content_type["flow"]
                admin_content_state.pop(uid, None)
                return await e.reply("✅ Store banner saved.", buttons=[[Button.inline("↩️ Back", f"adm_store_config|{flow}")]])
            if isinstance(content_type, dict) and content_type.get("type") == "general_setting":
                name = content_type["name"]
                value = text.strip()
                try:
                    if name in {"support_url", "terms_url"}:
                        if not re.match(r"^https?://[^\s]+$", value, re.IGNORECASE):
                            raise ValueError
                    elif name == "channel_links":
                        links = [item.strip() for item in value.split(",") if item.strip()]
                        if not links or any(not re.match(r"^https?://[^\s]+$", item, re.IGNORECASE) for item in links):
                            raise ValueError
                        value = ",".join(links)
                    elif name == "usdt_rate":
                        if float(value) <= 0:
                            raise ValueError
                        value = str(float(value))
                    elif name == "auto_cancel_seconds":
                        if not value.isdigit() or int(value) < 1:
                            raise ValueError
                    set_setting(name, value)
                    admin_content_state.pop(uid, None)
                    return await e.reply(f"✅ <b>{name}</b> saved.", buttons=[[Button.inline("Back", "adm_general")]])
                except ValueError:
                    return await e.reply("❌ Invalid value. Please try again or type /cancel.")
            if content_type == "maintenance_message":
                if not text.strip():
                    return await e.reply("❌ Maintenance message cannot be empty.")
                set_setting("maintenance_message", text.strip())
                admin_content_state.pop(uid, None)
                return await e.reply("✅ Maintenance message saved.", buttons=[[Button.inline("Back", "adm_maintenance")]])
            if content_type == "welcome":
                if not text:
                    return await e.reply("❌ Welcome message cannot be empty.")
                set_setting("welcome_message", text)
                admin_content_state.pop(uid, None)
                return await e.reply("✅ Welcome message saved.")
            if content_type == "banner":
                if not e.photo:
                    return await e.reply("❌ Please send a Telegram photo.")
                photo = e.photo
                reference = {
                    "id": photo.id,
                    "access_hash": photo.access_hash,
                    "file_reference": photo.file_reference.hex()
                }
                set_setting("banner_photo", json.dumps(reference))
                admin_content_state.pop(uid, None)
                return await e.reply("✅ Banner added/replaced successfully.")
        if not text: return

        if any(label in text for label in ("Buy Account", "Buy Sessions", "Deposit", "Bot Repos", "SMM Services", "Profile", "My Orders", "Balance", "Stock", "Refer", "Support", "Start", "Admin Panel")):
            session_buy_state.pop(uid, None)
            deposit_input.pop(uid, None)
            admin_dep_state.pop(uid, None)

        if is_admin(uid) and uid in admin_dep_state:
            st = admin_dep_state[uid]
            if st['step'] == 'wait_reason':
                t_uid, dep_id, msg_id = st['target_uid'], st['dep_id'], st['msg_id']
                cur.execute("UPDATE deposits SET status='rejected' WHERE id=?", (dep_id,))
                db.commit()
                
                try: await bot.edit_message(LOG_CHANNEL_ID, msg_id, f"{P_NO} <b>REJECTED USER {t_uid}</b>\nReason: {html.escape(text)}")
                except: pass
                
                await bot.send_message(int(t_uid), f"{P_NO} <b>Deposit Rejected!</b>\n📋 Reason: {html.escape(text)}")
                await e.reply(f"{P_YES} Rejection reason sent.")
                admin_dep_state.pop(uid)
                return

        if uid in session_buy_state:
            state = session_buy_state[uid]
            try:
                qty = int(re.sub(r'[^\d]', '', text))
                if qty < 1: raise ValueError
                if qty > state['stock']: return await e.respond(f"{P_WARN} <b>Not enough stock!</b> Max is {state['stock']}.")
                
                disc_row = cur.execute("SELECT discount FROM users WHERE user_id=?", (uid,)).fetchone()
                discount = disc_row[0] if disc_row else 0
                total_cost = qty * state['price']
                if discount > 0: total_cost = int(total_cost * (100 - discount) / 100)
                    
                bal_row = cur.execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
                user_bal = bal_row[0] if bal_row else 0
                if user_bal < total_cost: return await e.respond(f"{P_NO} <b>Insufficient Balance!</b>\nYou need {P_INR}{total_cost} to buy {qty} sessions.")

                session_buy_state.pop(uid)
                await process_bulk_sessions(e, uid, qty, state, total_cost)
                return
            except ValueError: return await e.respond(f"{P_NO} Please enter a valid number.")

        if uid in deposit_input and deposit_input[uid]['step'] == 'wait_amt':
            try:
                amt = int(re.sub(r'[^\d]', '', text))
                if amt < 10: return await e.reply(f"{P_WARN} Minimum Deposit is ₹10.")
                method = deposit_input[uid]['method']
                
                if method == "Cwallet":
                    final_amt = int(amt * 1.05)
                    waiting_proof[uid] = {
                        'amount': amt,
                        'method': method,
                        'final_amount': final_amt
                    }
                    deposit_input.pop(uid)
                    
                    caption = (f"{P_CARD} <b>Cwallet Deposit</b>\n\n"
                               f"{P_MONEY} <b>Amount:</b> ₹{amt}\n"
                               f"{PE_GIFT} <b>Bonus (5%):</b> ₹{final_amt - amt}\n"
                               f"{P_MONEY} <b>Total Credit:</b> ₹{final_amt}\n\n"
                               f"👇 <b>Scan QR to pay via Cwallet</b>\n"
                               f"💳 <b>Cwallet ID:</b> <code>{CWALLET_ID}</code>\n\n"
                               f"<i>After payment, send the screenshot/proof here.</i>")
                    
                    try:
                        await bot.send_file(uid, CWALLET_QR, caption=caption, 
                                           buttons=[[Button.inline("❌ Cancel", "cancel_action")]])
                    except Exception as err:
                        await bot.send_message(uid, caption, 
                                              buttons=[[Button.inline("❌ Cancel", "cancel_action")]])
                    return
                else:
                    waiting_proof[uid] = {'amount': amt, 'method': method}
                    deposit_input.pop(uid)
                    
                    rate = get_usdt_rate()
                    usdt_amt = round(amt / rate, 2)
                    rate_text = f"\n\n{P_MONEY} <b>Amount to Pay:</b> {P_INR}{amt} (~{P_USDT}{usdt_amt} USDT)\n💱 <i>Exchange Rate: {P_INR}{rate} = $1</i>"
                    
                    if method == "UPI":
                        return await show_upi_qr(event, amt)
                    else:
                        row = cur.execute("SELECT caption, qr_file_id FROM custom_payments WHERE name=?", (method,)).fetchone()
                        if row:
                            cap = row[0] + f"{rate_text}\n\n👇 <b>After paying, send a clear Screenshot here:</b>"
                            btns = [[Button.inline("❌ Cancel", "cancel_action")]]
                            if row[1] and os.path.exists(row[1]): 
                                try: await bot.send_file(e.chat_id, row[1], caption=cap, buttons=btns)
                                except: await e.reply(cap, buttons=btns)
                            else: await e.reply(cap, buttons=btns)
                        else: 
                            await e.reply(f"{P_CARD} <b>{method} Deposit</b>{rate_text}\n\n👇 Send Screenshot here:", 
                                         buttons=[[Button.inline("❌ Cancel", "cancel_action")]])
            except ValueError: 
                await e.respond(f"{P_NO} Please enter a valid number in {P_INR} (INR).")
            return

        if "Buy Account" in text: await show_countries(e, 'single', 1)
        elif "Deposit" in text: await deposit_menu(e)
        elif "Bot Repos" in text:
            await e.reply(f"{P_PKG} <b>Bot Repos</b>\n\nThis section is not configured yet. Use <b>Buy Account</b> or <b>Stock</b> to browse the available OTP inventory.")
        elif "SMM Services" in text:
            await e.reply(f"{P_WARN} <b>SMM Services</b>\n\nSMM services are not configured in this bot yet. Contact support if you need help.", buttons=get_support_buttons())
        elif "Profile" in text: await profile_handler(e)
        elif "My Orders" in text or "My Stats" in text: await stats_handler(e)
        elif "Balance" in text: await profile_handler(e)
        elif "Stock" in text: await show_countries(e, 'single', 1)
        elif "Refer" in text:
            me = await bot.get_me()
            ref_link = f"https://t.me/{me.username}?start=ref_{uid}" if me.username else "Referral link is unavailable until the bot has a public username."
            count = cur.execute("SELECT COUNT(*) FROM users WHERE referred_by=?", (uid,)).fetchone()[0]
            await e.reply(f"{PE_GIFT} <b>Refer & Earn</b>\n\nInvite friends and earn from their deposits.\n\n{P_USERS} Referrals: <b>{count}</b>\n{P_GLOBE} <code>{ref_link}</code>")
        elif "Start" in text: await send_main_menu(e, uid)
        elif "Support" in text: 
            await e.reply(f"{PE_ANGEL} <b>Fresh Tg Support & Relevant Information</b>\n\n{P_WARN} For support contact our developers:", buttons=get_support_buttons())
        elif "Admin Panel" in text: 
            if is_admin(uid): await admin_panel_handler(e)

    except Exception as ex: print(f"Message Error: {ex}")

@bot.on(events.CallbackQuery)
async def handle_callback_query(e):
    try:
        uid = e.sender_id
        if (not is_bot_online() or is_maintenance_mode()) and not is_admin(uid):
            return await e.answer(
                get_maintenance_message() if is_maintenance_mode() else "⚙️ Bot is under maintenance.",
                alert=True
            )
            
        ensure_user(uid)
        now = time.time()
        if uid in user_spam_cooldown and now - user_spam_cooldown[uid] < 0.5:
            return await e.answer("⚠️ Please slow down! Don't spam buttons.", alert=True)
        user_spam_cooldown[uid] = now

        if is_user_banned(uid): return await e.answer("🚫 BANNED", alert=True)
        data = e.data.decode()

        if data == "verify_join":
            row = cur.execute("SELECT terms_accepted FROM users WHERE user_id=?", (uid,)).fetchone()
            terms = row[0] if row else 0
            if not terms:
                msg = (f"{PE_FLOWER} <b>STEP 1/2 — TERMS & CONDITIONS</b>\n\n"
                       "Please read and accept our Terms & Conditions first.")
                try: await e.edit(msg, buttons=get_terms_buttons())
                except MessageNotModifiedError: pass
                return
            if not await check_channel_joined(uid): return await e.answer("⚠️ You must join the channels first!", alert=True)
            await send_main_menu(e, uid)

        elif data == "tc_accept":
            cur.execute("UPDATE users SET terms_accepted=1 WHERE user_id=?", (uid,))
            db.commit()
            await e.answer("✅ Terms Accepted!", alert=True)
            await send_main_menu(e, uid)
            
        elif data == "tc_reject":
            try: await e.edit(f"{P_NO} You cannot use the bot without accepting the terms.")
            except MessageNotModifiedError: pass
            
        elif data == "cancel_action":
            deposit_input.pop(uid, None); waiting_proof.pop(uid, None); session_buy_state.pop(uid, None)
            if uid in pending_utr:
                del pending_utr[uid]
            try: await e.edit(f"{P_NO} <b>Cancelled.</b>")
            except MessageNotModifiedError: pass

        elif data.startswith("shop|"):
            parts = data.split("|")
            if len(parts) != 3 or parts[1] not in {"single", "bulk"} or not parts[2].isdigit():
                return await e.answer("Invalid shop page.", alert=True)
            await show_countries(e, parts[1], int(parts[2]))

        elif data == "shop_noop":
            await e.answer("You are viewing this page.")

        elif data == "shop_back":
            account_product_state.pop(uid, None)
            await send_main_menu(e, uid)

        elif data.startswith("prod|"):
            parts = data.split("|")
            if len(parts) != 3 or parts[1] not in {"single", "bulk"} or not parts[2].isdigit():
                return await e.answer("Invalid product.", alert=True)
            await show_product_details(e, parts[1], parts[2])

        elif data.startswith("pbuy|"):
            parts = data.split("|")
            if len(parts) != 3 or parts[1] not in {"single", "bulk"} or not parts[2].isdigit():
                return await e.answer("Invalid product.", alert=True)
            flow, token = parts[1], parts[2]
            product = account_product_state.get(uid, {}).get(token)
            if not product:
                return await e.answer("⚠️ Product list expired. Please reopen the shop.", alert=True)
            if get_product_stock(product) == 0:
                return await e.edit(
                    f"{P_NO} <b>Out of Stock</b>\n\nThis product is no longer available.",
                    buttons=[[Button.inline(get_store_buttons(flow)["back"], f"shop|{flow}|1")]]
                )
            if flow == "single":
                await confirm_purchase(e, product["country"], product["year"], str(product["price"]))
            else:
                await init_session_purchase(e, product["country"], product["year"], str(product["price"]))

        elif data.startswith("pg_c|"): 
            p = data.split("|")
            await show_countries(e, p[1], int(p[2]))

        elif data.startswith("bc|"):
            p = data.split("|")
            await show_years(e, p[1], p[2])

        elif data.startswith("by|"):
            p = data.split("|")
            if p[1] == 'single': await confirm_purchase(e, p[2], p[3], p[4])
            else: await init_session_purchase(e, p[2], p[3], p[4])
            
        elif data.startswith("buy_cf|"):
            p = data.split("|")
            await process_purchase(e, p[1], p[2], p[3])

        elif data.startswith("get_otp_again|"):
            phone = data.split("|")[1]
            if phone not in active_orders:
                return await e.answer("⚠️ Session already logged out or expired.", alert=True)
            
            order = active_orders[phone]
            client = order['client']
            start_time = order['start_time']
            
            await e.answer("🔄 Fetching latest OTP...", alert=False)
            try:
                msgs = await client.get_messages(777000, limit=5)
                latest_code = None
                for m in msgs:
                    if m.date.timestamp() > start_time - 10:
                        if m.message and re.search(OTP_REGEX, m.message) and "Login detected" not in m.message:
                            latest_code = re.search(OTP_REGEX, m.message).group()
                            break
                
                if latest_code:
                    twofa_text = f"{P_2FA} <b>2FA:</b> <code>{order['twofa']}</code>" if order['twofa'] != "None" else f"🔓 <b>2FA:</b> <code>Disabled (No Password)</code>"
                    msg = (f"{P_YES} <b>Latest OTP Fetched!</b>\n\n"
                           f"{P_PHONE} <b>Phone:</b> <code>{phone}</code>\n"
                           f"{P_FLAG} <b>Country:</b> {order['c_icon']} {order['country']}\n"
                           f"{P_OTP} <b>OTP:</b> <code>{latest_code}</code>\n"
                           f"{twofa_text}")
                    try: await e.edit(msg, buttons=[[Button.inline("🔄 Get OTP Again", f"get_otp_again|{phone}")], [Button.inline("🚪 Finish & Logout", f"logout_bot|{phone}")]])
                    except MessageNotModifiedError: pass
                else:
                    await e.answer("⏳ No new OTP found yet. Try again in a few seconds.", alert=True)
            except Exception as ex:
                await e.answer(f"❌ Error fetching OTP.", alert=True)

        elif data.startswith("logout_bot|"):
            phone = data.split("|")[1]
            if phone in active_orders:
                order = active_orders.pop(phone)
                try: await order['client'].log_out()
                except: pass
                try: await order['client'].disconnect()
                except: pass
                delete_session_files(order['sess'])
                await e.edit(f"{P_YES} <b>Session Finished & Logged out successfully.</b>")
            else:
                await e.answer("⚠️ No active order found or already logged out.", alert=True)
        
        elif data.startswith("page_purchases_"): await send_purchase_page(e, uid, int(data.split("_")[2]))
        elif data == "back_to_stats": await stats_handler(e, is_callback=True)
        elif data == "view_referrals": await view_referrals(e)
            
        elif data.startswith("depm_"): await manual_deposit_init(e, data.replace("depm_", ""))
        elif data == "dep_upi": await init_upi_keypad(e)
        elif data.startswith("kp_"): await keypad_logic(e)
        elif data.startswith("submit_utr_"): await submit_utr_handler(e, data.replace("submit_utr_", ""))
        
        elif data.startswith("adm_") and is_admin(uid): await admin_actions(e)
        
        elif data.startswith("dkp|") and has_perm(uid, 'p_bal'):
            _, dep_id, action = data.split("|")
            dep_id = int(dep_id)
            row = cur.execute("SELECT user_id, method_name, status, amount FROM deposits WHERE id=?", (dep_id,)).fetchone()
            if not row or row[2] != 'pending': return await e.edit(f"{P_WARN} Already processed.")
            t_uid, method, orig_amt = row[0], row[1], row[3]
            
            curr = custom_dep_amt.get(dep_id, "0")
            
            if action.isdigit():
                if curr == "0": curr = action
                else: curr += action
                if len(curr) > 7: curr = curr[:7]
            elif action == "del": curr = curr[:-1] or "0"
            elif action == "cancel":
                btns = [[Button.inline(f"✅ Accept (₹{orig_amt})", f"dep_acc|{dep_id}|{t_uid}|{method}|exact|{orig_amt}"), Button.inline("❌ Reject", f"dep_rej|{dep_id}|{t_uid}")],
                        [Button.inline("📝 Custom Amount", f"dep_acc|{dep_id}|{t_uid}|{method}|custom|0")]]
                return await e.edit(f"{PE_LIGHTNING} <b>NEW DEPOSIT REQUEST</b>\n{P_ACC} User: <code>{t_uid}</code>\n{P_MONEY} Request: <b>{P_INR}{orig_amt}</b>\n{P_CARD} Method: {method}\n{P_ID} Ref: <code>{dep_id}</code>", buttons=btns)
            elif action == "conf":
                amt = int(curr)
                if amt <= 0: return await e.answer("Amount must be > 0", alert=True)
                
                async with get_user_lock(t_uid):
                    prev_row = cur.execute("SELECT balance FROM users WHERE user_id=?", (t_uid,)).fetchone()
                    prev_bal = prev_row[0] if prev_row else 0
                    update_balance(t_uid, amt)
                    cur.execute("UPDATE deposits SET status='approved', amount=? WHERE id=?", (amt, dep_id))
                    cur.execute("UPDATE users SET total_deposited = total_deposited + ? WHERE user_id=?", (amt, t_uid))
                    db.commit()
                    
                await process_referral_bonus(t_uid, amt)
                await e.edit(f"{PE_CHECK} <b>APPROVED {P_INR}{amt} TO {t_uid} (Custom Amount)</b>")
                await bot.send_message(int(t_uid), f"{PE_CHECK} <b>Deposit Approved!</b>\n{P_MONEY} Amount Added: {P_INR}{amt}\n📉 Old: {P_INR}{prev_bal} | 📈 New: {P_INR}{prev_bal+amt}")
                return

            custom_dep_amt[dep_id] = curr
            await e.edit(f"{P_KEY} <b>Enter Custom Amount for User {t_uid}:</b>\n\n{P_MONEY} {curr}", buttons=get_admin_custom_keypad(dep_id))

        elif data.startswith("dep_acc|") and has_perm(uid, 'p_bal'):
            p = data.split("|")
            dep_id, t_uid, method, a_type = p[1], int(p[2]), p[3], p[4]
            row = cur.execute("SELECT status FROM deposits WHERE id=?", (dep_id,)).fetchone()
            if not row or row[0] != 'pending': return await e.edit(f"{P_WARN} Already processed.")
            
            if a_type == "exact":
                amt = int(p[5]) 
                async with get_user_lock(t_uid):
                    prev_row = cur.execute("SELECT balance FROM users WHERE user_id=?", (t_uid,)).fetchone()
                    prev_bal = prev_row[0] if prev_row else 0
                    update_balance(t_uid, amt)
                    
                    cur.execute("UPDATE deposits SET status='approved', amount=? WHERE id=?", (amt, dep_id))
                    cur.execute("UPDATE users SET total_deposited = total_deposited + ? WHERE user_id=?", (amt, t_uid))
                    db.commit()
                
                await process_referral_bonus(t_uid, amt)
                
                user_msg = (f"{PE_CHECK} <b>Deposit Approved!</b>\n\n{P_MONEY} <b>Amount Added:</b> ${to_usd(amt):.2f} ({P_INR}{amt})\n"
                            f"📉 <b>Previous Balance:</b> ${to_usd(prev_bal):.2f} ({P_INR}{prev_bal})\n📈 <b>New Balance:</b> ${to_usd(prev_bal+amt):.2f} ({P_INR}{prev_bal+amt})")
                await bot.send_message(int(t_uid), user_msg)
                try: await e.edit(f"{PE_CHECK} <b>INSTANT CREDITED {P_INR}{amt} TO {t_uid}</b>")
                except MessageNotModifiedError: pass
                
            elif a_type == "custom":
                custom_dep_amt[int(dep_id)] = "0"
                await e.edit(f"{P_KEY} <b>Enter Custom Amount for User {t_uid}:</b>\n\n{P_MONEY} 0", buttons=get_admin_custom_keypad(int(dep_id)))
                
        elif data.startswith("dep_rej|") and has_perm(uid, 'p_bal'):
            p = data.split("|")
            dep_id, t_uid = p[1], int(p[2])
            row = cur.execute("SELECT status FROM deposits WHERE id=?", (dep_id,)).fetchone()
            if not row or row[0] != 'pending': return await e.edit(f"{P_WARN} Already processed.")
            admin_dep_state[uid] = {'target_uid': t_uid, 'dep_id': dep_id, 'step': 'wait_reason', 'msg_id': e.message.id}
            await bot.send_message(uid, f"{P_WARN} Reply to this message with the REASON for rejecting user <code>{t_uid}</code>:")
            try: await e.answer("Check your bot PMs to enter the reason.", alert=True)
            except: pass

    except Exception as ex: print(f"Callback Error: {ex}")

async def health_handler(request):
    """Small liveness/readiness endpoint for Render and external monitors."""
    connected = bot.is_connected()
    return web.json_response(
        {
            "status": "ok" if connected else "degraded",
            "bot_connected": connected,
            "timestamp": int(time.time()),
        },
        status=200 if connected else 503,
    )

async def ping_handler(request):
    """Always-on liveness endpoint used to keep free web hosts warm."""
    return web.Response(text="OK", content_type="text/plain")

async def keep_alive_loop(port):
    """Keep the web service warm and refresh the Telegram connection periodically."""
    await asyncio.sleep(30)
    while True:
        try:
            await ensure_bot_connected()
            await bot.get_me()
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"http://127.0.0.1:{port}/ping") as response:
                    if response.status != 200:
                        logger.warning("Keep-alive ping returned HTTP %s.", response.status)
            logger.info("Heartbeat: bot alive.")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Heartbeat check failed; reconnect loop will retry.")
        await asyncio.sleep(180)


async def ensure_bot_connected():
    """Reconnect after a transient Telegram/network disconnect."""
    if bot.is_connected():
        return
    start_result = bot.start(bot_token=BOT_TOKEN)
    if asyncio.iscoroutine(start_result):
        await start_result


async def main():
    port = int(os.getenv("PORT", "10000"))
    app = web.Application()
    app.router.add_get("/", lambda request: web.Response(text="OK"))
    app.router.add_get("/ping", ping_handler)
    app.router.add_get("/health", health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"✅ Health server listening on 0.0.0.0:{port}")
    heartbeat_task = asyncio.create_task(keep_alive_loop(port))

    print("=" * 50)
    print("✅ ULTIMATE ADVANCED HTML BOT STARTED SUCCESSFULLY")
    print("=" * 50)
    print(f"✅ Admins: {ADMIN_IDS}")
    print(f"✅ Support: @{SUPPORT_USERNAME_1} & @{SUPPORT_USERNAME_2}")
    print("=" * 50)
    reconnect_delay = 5
    try:
        while True:
            try:
                await ensure_bot_connected()
                logger.info("Telegram bot connected.")
                await bot.run_until_disconnected()
                logger.warning("Telegram bot disconnected; reconnecting in %ss.", reconnect_delay)
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                logger.exception("Telegram connection dropped; reconnecting in %ss.", reconnect_delay)
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, 60)
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass
        await runner.cleanup()
        if bot.is_connected():
            await bot.disconnect()

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
