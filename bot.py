import os
import re
import io
import time
import json
import random
import asyncio
import logging
import sqlite3
import base64
import hashlib
import html
from pathlib import Path
from typing import Any, Optional, List, Set, Tuple, Dict, Callable, Awaitable
from datetime import datetime, timezone, timedelta

import aiohttp
import asyncpg
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from aiohttp import web

from cryptography.fernet import Fernet

from aiogram import Bot, Dispatcher, F, Router, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, KeyboardButton, ReplyKeyboardMarkup,
    InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile, TelegramObject,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger("lochin_taxi_bot")

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "lochin_taxi.db"
AUDIT_LOG_PATH = BASE_DIR / "audit.log"
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN .env da topilmadi!")

BOT_NAME = os.getenv("BOT_NAME", "LOCHIN TAXI").strip() or "LOCHIN TAXI"
PORT = int(os.getenv("PORT", "8080"))

ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", "").strip()
if not ENCRYPTION_KEY or len(ENCRYPTION_KEY) < 32:
    ENCRYPTION_KEY = "LochinTaxiSecretEncryptionKey2026_SecureKey32!"
derived_key = base64.urlsafe_b64encode(hashlib.sha256(ENCRYPTION_KEY.encode()).digest())
_cipher_suite = Fernet(derived_key)

SUPPORT_PHONE = os.getenv("SUPPORT_PHONE", "+998913773200").strip()
SUPPORT_PHONE_DISPLAY = os.getenv("SUPPORT_PHONE_DISPLAY", "+998 91 377 32 00").strip()
DRIVER_GROUP_LINK = os.getenv("DRIVER_GROUP_LINK", "https://t.me/+vLyCiiXNvB5kMTUy").strip()


def clean_phone_number(raw_phone: str) -> str:
    if not raw_phone:
        return ""
    digits = re.sub(r"\D", "", str(raw_phone))
    if not digits:
        return ""
    if digits.startswith("8") and len(digits) == 11:
        digits = "998" + digits[1:]
    elif not digits.startswith("998") and len(digits) == 9:
        digits = "998" + digits
    elif digits.startswith("998") and len(digits) == 12:
        pass
    else:
        if len(digits) >= 9:
            digits = "998" + digits[-9:]
    return f"+{digits}"


OWNER_PHONE = clean_phone_number(SUPPORT_PHONE)

ADMIN_IDS: Set[int] = set()
_raw_admins = str(os.getenv("ADMIN_IDS", "")) + " " + str(os.getenv("ADMIN_ID", ""))
for _adm_str in re.findall(r"\d+", _raw_admins):
    ADMIN_IDS.add(int(_adm_str))
MANAGER_TG_ID = int(os.getenv("MANAGER_TG_ID", "0") if os.getenv("MANAGER_TG_ID", "0").isdigit() else 0)
if MANAGER_TG_ID > 0:
    ADMIN_IDS.add(MANAGER_TG_ID)

YANDEX_API_KEY = os.getenv("YANDEX_API_KEY", "").strip()
YANDEX_CLIENT_ID = os.getenv("YANDEX_CLIENT_ID", "").strip()
YANDEX_PARK_ID = os.getenv("YANDEX_PARK_ID", "").strip()

KAPITAL_API_URL = os.getenv("KAPITAL_API_URL", "https://m.bank24.uz:2713").strip()
KAPITAL_LOGIN = os.getenv("KAPITAL_LOGIN", "KUDRAT198SK").strip()
KAPITAL_PASSWORD = os.getenv("KAPITAL_PASSWORD", "$e09hZzU").strip()
KAPITAL_ACCOUNT = os.getenv("KAPITAL_ACCOUNT", "20208000607311468002").strip()
KAPITAL_MFO = os.getenv("KAPITAL_MFO", "01158").strip()
KAPITAL_INN = os.getenv("KAPITAL_INN", "312433744").strip()
KAPITAL_COMPANY_NAME = os.getenv("KAPITAL_COMPANY_NAME", '"LOCHIN TAKSI"MCHJ').strip()

MIN_WITHDRAWAL = int(os.getenv("MIN_WITHDRAWAL", "20000"))
MIN_DEPOSIT = int(os.getenv("MIN_DEPOSIT", "20000"))
COMMISSION_PERCENT = float(os.getenv("COMMISSION_PERCENT", "0.0"))

TASHKENT_TZ = timezone(timedelta(hours=5))
UZ_MONTHS = {
    1: "Yanvar", 2: "Fevral", 3: "Mart", 4: "Aprel", 5: "May", 6: "Iyun",
    7: "Iyul", 8: "Avgust", 9: "Sentabr", 10: "Oktabr", 11: "Noyabr", 12: "Dekabr"
}


def is_admin(user_id: int) -> bool:
    return int(user_id) in ADMIN_IDS


def esc(text: Any) -> str:
    return html.escape(str(text or ""))


def encrypt_card(card_number: str) -> str:
    clean = re.sub(r"\D", "", str(card_number))
    try:
        return _cipher_suite.encrypt(clean.encode()).decode()
    except Exception:
        return clean


def decrypt_card(encrypted_card: str) -> str:
    try:
        return _cipher_suite.decrypt(encrypted_card.encode()).decode()
    except Exception:
        return encrypted_card


def mask_card(card_number: str) -> str:
    clean = re.sub(r"\D", "", str(card_number))
    if len(clean) == 16:
        return f"{clean[:4]} **** **** {clean[-4:]}"
    elif len(clean) >= 8:
        return f"{clean[:4]} **** {clean[-4:]}"
    return "Noma'lum karta"


def log_admin_view_card(admin_id: int, withdrawal_id: int):
    logger.info(f"AUDIT | ADMIN {admin_id} viewed full card for withdrawal_id={withdrawal_id}")
    try:
        with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{tashkent_now_iso()} | ADMIN_ID:{admin_id} | WITHDRAWAL_ID:{withdrawal_id}\n")
    except Exception:
        pass


def fmt_sum(val: Any) -> str:
    try:
        return f"{int(val):,}".replace(",", " ")
    except Exception:
        return "0"


def tashkent_now_iso() -> str:
    return datetime.now(TASHKENT_TZ).replace(microsecond=0).isoformat()


# ============================================================
# DATABASE LAYER
# ============================================================

db_pool: Optional[asyncpg.Pool] = None


async def init_database():
    global db_pool
    if DATABASE_URL:
        try:
            clean_url = DATABASE_URL.replace("?sslmode=require", "")
            db_pool = await asyncpg.create_pool(clean_url, ssl="require", min_size=5, max_size=30, timeout=15)
            async with db_pool.acquire() as conn:
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id SERIAL PRIMARY KEY, telegram_id BIGINT UNIQUE, username TEXT, full_name TEXT,
                        phone TEXT, card_number TEXT, car_model TEXT, car_number TEXT, position TEXT UNIQUE,
                        language TEXT NOT NULL DEFAULT 'uz', balance BIGINT DEFAULT 0, blocked_balance BIGINT DEFAULT 0,
                        is_registered INT DEFAULT 0, is_blocked INT DEFAULT 0, yandex_driver_id TEXT,
                        referrer_id BIGINT, total_orders INT DEFAULT 0, total_earnings BIGINT DEFAULT 0,
                        last_activity TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_users_phone ON users(phone);
                    CREATE INDEX IF NOT EXISTS idx_users_tg_id ON users(telegram_id);
                    CREATE INDEX IF NOT EXISTS idx_users_pos ON users(position);
                    CREATE INDEX IF NOT EXISTS idx_users_y_id ON users(yandex_driver_id);
                    CREATE TABLE IF NOT EXISTS withdrawals (
                        id SERIAL PRIMARY KEY, user_id INT NOT NULL, amount BIGINT NOT NULL, commission BIGINT DEFAULT 0,
                        net_amount BIGINT NOT NULL, card_number TEXT, status TEXT NOT NULL DEFAULT 'pending',
                        payout_method TEXT DEFAULT 'manual', ext_tx_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_wd_user_id ON withdrawals(user_id);
                    CREATE INDEX IF NOT EXISTS idx_wd_status ON withdrawals(status);
                """)
                owner_tg = await conn.fetchval("SELECT telegram_id FROM users WHERE phone = $1", OWNER_PHONE)
                if owner_tg:
                    ADMIN_IDS.add(int(owner_tg))
            logger.info("PostgreSQL tayyor!")
        except Exception as e:
            logger.error(f"PostgreSQL xatosi: {e}")
            db_pool = None

    if not db_pool:
        conn = sqlite3.connect(DB_PATH, timeout=20)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_id INTEGER UNIQUE, username TEXT, full_name TEXT,
                phone TEXT, card_number TEXT, car_model TEXT, car_number TEXT, position TEXT UNIQUE,
                language TEXT NOT NULL DEFAULT 'uz', balance INTEGER DEFAULT 0, blocked_balance INTEGER DEFAULT 0,
                is_registered INTEGER DEFAULT 0, is_blocked INTEGER DEFAULT 0, yandex_driver_id TEXT,
                referrer_id INTEGER, total_orders INTEGER DEFAULT 0, total_earnings INTEGER DEFAULT 0,
                last_activity TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, amount INTEGER NOT NULL,
                commission INTEGER DEFAULT 0, net_amount INTEGER NOT NULL, card_number TEXT,
                status TEXT NOT NULL DEFAULT 'pending', payout_method TEXT DEFAULT 'manual',
                ext_tx_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
        """)
        conn.commit()
        row = conn.execute("SELECT telegram_id FROM users WHERE phone = ?", (OWNER_PHONE,)).fetchone()
        if row and row[0]:
            ADMIN_IDS.add(int(row[0]))
        conn.close()
        logger.info("SQLite tayyor!")


def _process_user_dict(d: Optional[dict]) -> Optional[dict]:
    if not d:
        return None
    res = dict(d)
    if res.get("card_number"):
        res["card_number"] = decrypt_card(res["card_number"])
    if "balance" in res:
        res["balance"] = int(res["balance"] or 0)
    if res.get("phone") and clean_phone_number(res["phone"]) == OWNER_PHONE and res.get("telegram_id"):
        ADMIN_IDS.add(int(res["telegram_id"]))
    return res


def _process_wd_dict(d: Optional[dict]) -> Optional[dict]:
    if not d:
        return None
    res = dict(d)
    if res.get("card_number"):
        res["card_number"] = decrypt_card(res["card_number"])
    for k in ("amount", "net_amount", "commission"):
        if k in res:
            res[k] = int(res[k] or 0)
    return res


async def db_get_user(telegram_id: int) -> Optional[dict]:
    if db_pool:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM users WHERE telegram_id = $1", telegram_id)
            return _process_user_dict(dict(row)) if row else None
    else:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
        conn.close()
        return _process_user_dict(dict(row)) if row else None


async def db_get_user_by_id(user_id: int) -> Optional[dict]:
    if db_pool:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
            return _process_user_dict(dict(row)) if row else None
    else:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        conn.close()
        return _process_user_dict(dict(row)) if row else None


async def db_get_user_by_phone(phone: str) -> Optional[dict]:
    clean_p = clean_phone_number(phone)
    if not clean_p:
        return None
    short9 = clean_p[-9:]
    if db_pool:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM users WHERE phone LIKE $1 OR phone = $2 LIMIT 1", f"%{short9}", clean_p)
            return _process_user_dict(dict(row)) if row else None
    else:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM users WHERE phone LIKE ? OR phone = ? LIMIT 1", (f"%{short9}", clean_p)).fetchone()
        conn.close()
        return _process_user_dict(dict(row)) if row else None


async def db_find_driver_by_query(query: str) -> Optional[dict]:
    clean_q = query.strip()
    phone_clean = clean_phone_number(clean_q)
    short9 = phone_clean[-9:] if phone_clean else clean_q
    if db_pool:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM users WHERE position ILIKE $1 OR phone LIKE $2 OR car_number ILIKE $1 LIMIT 1",
                f"%{clean_q}%", f"%{short9}%"
            )
            return _process_user_dict(dict(row)) if row else None
    else:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM users WHERE position LIKE ? OR phone LIKE ? OR car_number LIKE ? LIMIT 1",
            (f"%{clean_q}%", f"%{short9}%", f"%{clean_q}%")
        ).fetchone()
        conn.close()
        return _process_user_dict(dict(row)) if row else None


async def db_delete_user_by_id(user_id: int) -> bool:
    try:
        if db_pool:
            async with db_pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute("DELETE FROM withdrawals WHERE user_id = $1", user_id)
                    await conn.execute("DELETE FROM users WHERE id = $1", user_id)
        else:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            with conn:
                conn.execute("DELETE FROM withdrawals WHERE user_id = ?", (user_id,))
                conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            conn.close()
        return True
    except Exception as e:
        logger.error(f"delete error: {e}")
        return False


async def db_upsert_start(telegram_id: int, username: str):
    now = tashkent_now_iso()
    if db_pool:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO users (telegram_id, username, last_activity, created_at, updated_at) "
                "VALUES ($1,$2,$3,$4,$5) ON CONFLICT (telegram_id) DO UPDATE SET last_activity=$3, updated_at=$5",
                telegram_id, username or "", now, now, now
            )
    else:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        with conn:
            conn.execute(
                "INSERT INTO users (telegram_id, username, last_activity, created_at, updated_at) "
                "VALUES (?,?,?,?,?) ON CONFLICT(telegram_id) DO UPDATE SET last_activity=excluded.last_activity, updated_at=excluded.updated_at",
                (telegram_id, username or "", now, now, now)
            )
        conn.close()


async def db_set_language(telegram_id: int, language: str):
    now = tashkent_now_iso()
    if db_pool:
        async with db_pool.acquire() as conn:
            await conn.execute("UPDATE users SET language=$1, updated_at=$2 WHERE telegram_id=$3", language, now, telegram_id)
    else:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        with conn:
            conn.execute("UPDATE users SET language=?, updated_at=? WHERE telegram_id=?", (language, now, telegram_id))
        conn.close()


async def db_generate_unique_position() -> str:
    for _ in range(300):
        pos = f"LCH-{random.randint(1000, 9999)}"
        if db_pool:
            async with db_pool.acquire() as conn:
                exists = await conn.fetchval("SELECT 1 FROM users WHERE position=$1", pos)
                if not exists:
                    return pos
        else:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            row = conn.execute("SELECT 1 FROM users WHERE position=?", (pos,)).fetchone()
            conn.close()
            if not row:
                return pos
    return f"LCH-{random.randint(10000, 99999)}"


async def db_finish_registration(
    telegram_id: int, full_name: str, phone: str, card_number: str,
    car_model: str, car_number: str, yandex_driver_id: Optional[str]
) -> str:
    now = tashkent_now_iso()
    enc_card = encrypt_card(card_number)
    phone_clean = clean_phone_number(phone)
    if phone_clean == OWNER_PHONE:
        ADMIN_IDS.add(telegram_id)
    existing = await db_get_user(telegram_id)
    position = existing["position"] if existing and existing.get("position") else await db_generate_unique_position()
    if db_pool:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET full_name=$1, phone=$2, card_number=$3, car_model=$4, car_number=$5, "
                "position=COALESCE(position,$6), yandex_driver_id=$7, is_registered=1, last_activity=$8, updated_at=$8 "
                "WHERE telegram_id=$9",
                full_name, phone_clean, enc_card, car_model, car_number, position, yandex_driver_id, now, telegram_id
            )
    else:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        with conn:
            conn.execute(
                "UPDATE users SET full_name=?, phone=?, card_number=?, car_model=?, car_number=?, "
                "position=COALESCE(position,?), yandex_driver_id=?, is_registered=1, last_activity=?, updated_at=? "
                "WHERE telegram_id=?",
                (full_name, phone_clean, enc_card, car_model, car_number, position, yandex_driver_id, now, now, telegram_id)
            )
        conn.close()
    return position


async def db_update_balance(telegram_id: int, balance: int):
    now = tashkent_now_iso()
    if db_pool:
        async with db_pool.acquire() as conn:
            await conn.execute("UPDATE users SET balance=$1, updated_at=$2 WHERE telegram_id=$3", int(balance), now, telegram_id)
    else:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        with conn:
            conn.execute("UPDATE users SET balance=?, updated_at=? WHERE telegram_id=?", (int(balance), now, telegram_id))
        conn.close()


async def db_has_pending_withdrawal(user_id: int) -> bool:
    if db_pool:
        async with db_pool.acquire() as conn:
            return bool(await conn.fetchval("SELECT 1 FROM withdrawals WHERE user_id=$1 AND status='pending'", user_id))
    else:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        row = conn.execute("SELECT 1 FROM withdrawals WHERE user_id=? AND status='pending'", (user_id,)).fetchone()
        conn.close()
        return bool(row)


async def db_get_pending_yandex_ids() -> Set[str]:
    if db_pool:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT DISTINCT u.yandex_driver_id FROM users u "
                "INNER JOIN withdrawals w ON u.id=w.user_id "
                "WHERE w.status='pending' AND u.yandex_driver_id IS NOT NULL AND u.yandex_driver_id != ''"
            )
            return {r["yandex_driver_id"] for r in rows if r["yandex_driver_id"]}
    else:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        rows = conn.execute(
            "SELECT DISTINCT u.yandex_driver_id FROM users u "
            "INNER JOIN withdrawals w ON u.id=w.user_id "
            "WHERE w.status='pending' AND u.yandex_driver_id IS NOT NULL AND u.yandex_driver_id != ''"
        ).fetchall()
        conn.close()
        return {r[0] for r in rows if r[0]}


async def db_create_withdrawal(
    user_id: int, telegram_id: int, amount: int, commission: int,
    net_amount: int, card_number: str, status: str, payout_method: str, ext_tx_id: str = ""
) -> int:
    now = tashkent_now_iso()
    enc_card = encrypt_card(card_number)
    if db_pool:
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                if await conn.fetchval("SELECT 1 FROM withdrawals WHERE user_id=$1 AND status='pending' FOR UPDATE", user_id):
                    raise ValueError("Faol ariza mavjud!")
                cur_bal = await conn.fetchval("SELECT balance FROM users WHERE id=$1 FOR UPDATE", user_id)
                if (cur_bal or 0) - MIN_DEPOSIT < amount:
                    raise ValueError("Balans yetarli emas!")
                await conn.execute("UPDATE users SET balance=balance-$1, updated_at=$2 WHERE id=$3", amount, now, user_id)
                row = await conn.fetchrow(
                    "INSERT INTO withdrawals (user_id, amount, commission, net_amount, card_number, status, payout_method, ext_tx_id, created_at, updated_at) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) RETURNING id",
                    user_id, amount, commission, net_amount, enc_card, status, payout_method, ext_tx_id, now, now
                )
                return row["id"] if row else 0
    else:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        with conn:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM withdrawals WHERE user_id=? AND status='pending'", (user_id,))
            if cur.fetchone():
                raise ValueError("Faol ariza mavjud!")
            cur.execute("SELECT balance FROM users WHERE id=?", (user_id,))
            r = cur.fetchone()
            if not r or (r[0] or 0) - MIN_DEPOSIT < amount:
                raise ValueError("Balans yetarli emas!")
            cur.execute("UPDATE users SET balance=balance-?, updated_at=? WHERE id=?", (amount, now, user_id))
            cur.execute(
                "INSERT INTO withdrawals (user_id, amount, commission, net_amount, card_number, status, payout_method, ext_tx_id, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (user_id, amount, commission, net_amount, enc_card, status, payout_method, ext_tx_id, now, now)
            )
            w_id = cur.lastrowid
        conn.close()
        return w_id or 0


async def db_get_withdrawal(w_id: int) -> Optional[dict]:
    if db_pool:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM withdrawals WHERE id=$1", w_id)
            return _process_wd_dict(dict(row)) if row else None
    else:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM withdrawals WHERE id=?", (w_id,)).fetchone()
        conn.close()
        return _process_wd_dict(dict(row)) if row else None


async def db_update_withdrawal_status(w_id: int, status: str, ext_tx_id: str = ""):
    now = tashkent_now_iso()
    if db_pool:
        async with db_pool.acquire() as conn:
            await conn.execute("UPDATE withdrawals SET status=$1, ext_tx_id=$2, updated_at=$3 WHERE id=$4", status, ext_tx_id, now, w_id)
    else:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        with conn:
            conn.execute("UPDATE withdrawals SET status=?, ext_tx_id=?, updated_at=? WHERE id=?", (status, ext_tx_id, now, w_id))
        conn.close()


async def db_refund_withdrawal(w_id: int):
    wd = await db_get_withdrawal(w_id)
    if not wd or wd.get("status") in ("rejected", "completed"):
        return
    now = tashkent_now_iso()
    if db_pool:
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("UPDATE users SET balance=balance+$1, updated_at=$2 WHERE id=$3", int(wd["amount"]), now, wd["user_id"])
                await conn.execute("UPDATE withdrawals SET status='rejected', updated_at=$1 WHERE id=$2", now, w_id)
    else:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        with conn:
            conn.execute("UPDATE users SET balance=balance+?, updated_at=? WHERE id=?", (int(wd["amount"]), now, wd["user_id"]))
            conn.execute("UPDATE withdrawals SET status='rejected', updated_at=? WHERE id=?", (now, w_id))
        conn.close()


async def db_get_driver_today_withdrawn(user_id: int) -> int:
    today_start = datetime.now(TASHKENT_TZ).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    if db_pool:
        async with db_pool.acquire() as conn:
            val = await conn.fetchval(
                "SELECT COALESCE(SUM(amount),0) FROM withdrawals WHERE user_id=$1 AND status='completed' AND updated_at>=$2",
                user_id, today_start
            )
            return int(val or 0)
    else:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        row = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM withdrawals WHERE user_id=? AND status='completed' AND updated_at>=?",
            (user_id, today_start)
        ).fetchone()
        conn.close()
        return int(row[0] if row else 0)


async def db_get_all_bot_drivers() -> List[dict]:
    """
    BOTGA ULANGAN BARCHA FOYDALANUVCHILAR (TELEGRAM ID MAVJUD BO'LGAN BARCHASI).
    Hech kim tashlab ketilmaydi.
    """
    if db_pool:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM users WHERE telegram_id IS NOT NULL AND telegram_id != 0 ORDER BY id DESC"
            )
            return [_process_user_dict(dict(r)) for r in rows]
    else:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM users WHERE telegram_id IS NOT NULL AND telegram_id != 0 ORDER BY id DESC"
        ).fetchall()
        conn.close()
        return [_process_user_dict(dict(r)) for r in rows]


async def db_get_bot_users_for_broadcast() -> List[dict]:
    if db_pool:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM users WHERE telegram_id IS NOT NULL AND telegram_id != 0 ORDER BY id ASC")
            return [_process_user_dict(dict(r)) for r in rows]
    else:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM users WHERE telegram_id IS NOT NULL AND telegram_id != 0 ORDER BY id ASC").fetchall()
        conn.close()
        return [_process_user_dict(dict(r)) for r in rows]


async def db_get_stats() -> dict:
    today_start = datetime.now(TASHKENT_TZ).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    month_start = datetime.now(TASHKENT_TZ).replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    if db_pool:
        async with db_pool.acquire() as conn:
            total_users = await conn.fetchval("SELECT COUNT(*) FROM users") or 0
            bot_users = await conn.fetchval("SELECT COUNT(*) FROM users WHERE telegram_id IS NOT NULL AND telegram_id != 0") or 0
            registered = await conn.fetchval("SELECT COUNT(*) FROM users WHERE telegram_id IS NOT NULL AND telegram_id != 0 AND is_registered=1") or 0
            yandex_linked = await conn.fetchval("SELECT COUNT(*) FROM users WHERE yandex_driver_id IS NOT NULL AND yandex_driver_id!=''") or 0
            today_withdrawn = await conn.fetchval("SELECT COALESCE(SUM(amount),0) FROM withdrawals WHERE status='completed' AND updated_at >= $1", today_start) or 0
            month_withdrawn = await conn.fetchval("SELECT COALESCE(SUM(amount),0) FROM withdrawals WHERE status='completed' AND updated_at >= $1", month_start) or 0
            total_withdrawn = await conn.fetchval("SELECT COALESCE(SUM(amount),0) FROM withdrawals WHERE status='completed'") or 0
            pending_count = await conn.fetchval("SELECT COUNT(*) FROM withdrawals WHERE status='pending'") or 0
            pending_sum = await conn.fetchval("SELECT COALESCE(SUM(amount),0) FROM withdrawals WHERE status='pending'") or 0
    else:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        bot_users = conn.execute("SELECT COUNT(*) FROM users WHERE telegram_id IS NOT NULL AND telegram_id != 0").fetchone()[0]
        registered = conn.execute("SELECT COUNT(*) FROM users WHERE telegram_id IS NOT NULL AND telegram_id != 0 AND is_registered=1").fetchone()[0]
        yandex_linked = conn.execute("SELECT COUNT(*) FROM users WHERE yandex_driver_id IS NOT NULL AND yandex_driver_id!=''").fetchone()[0]
        today_withdrawn = conn.execute("SELECT COALESCE(SUM(amount),0) FROM withdrawals WHERE status='completed' AND updated_at >= ?", (today_start,)).fetchone()[0]
        month_withdrawn = conn.execute("SELECT COALESCE(SUM(amount),0) FROM withdrawals WHERE status='completed' AND updated_at >= ?", (month_start,)).fetchone()[0]
        total_withdrawn = conn.execute("SELECT COALESCE(SUM(amount),0) FROM withdrawals WHERE status='completed'").fetchone()[0]
        pending_count = conn.execute("SELECT COUNT(*) FROM withdrawals WHERE status='pending'").fetchone()[0]
        pending_sum = conn.execute("SELECT COALESCE(SUM(amount),0) FROM withdrawals WHERE status='pending'").fetchone()[0] or 0
        conn.close()
    return {
        "total_users": total_users,
        "bot_users": bot_users,
        "registered_drivers": registered,
        "yandex_linked": yandex_linked,
        "today_withdrawn": int(today_withdrawn),
        "month_withdrawn": int(month_withdrawn),
        "total_withdrawn": int(total_withdrawn),
        "pending_count": pending_count,
        "pending_sum": int(pending_sum)
    }


async def db_get_all_pending_withdrawals() -> List[dict]:
    if db_pool:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT w.*, u.full_name, u.phone, u.position FROM withdrawals w "
                "JOIN users u ON w.user_id=u.id WHERE w.status='pending' ORDER BY w.id DESC"
            )
            return [_process_wd_dict(dict(r)) for r in rows]
    else:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT w.*, u.full_name, u.phone, u.position FROM withdrawals w "
            "JOIN users u ON w.user_id=u.id WHERE w.status='pending' ORDER BY w.id DESC"
        ).fetchall()
        conn.close()
        return [_process_wd_dict(dict(r)) for r in rows]


async def db_force_complete_pending(user_id: Optional[int] = None) -> int:
    now = tashkent_now_iso()
    if db_pool:
        async with db_pool.acquire() as conn:
            if user_id:
                res = await conn.execute("UPDATE withdrawals SET status='completed', updated_at=$1 WHERE user_id=$2 AND status='pending'", now, user_id)
            else:
                res = await conn.execute("UPDATE withdrawals SET status='completed', updated_at=$1 WHERE status='pending'", now)
            try:
                return int(res.split()[-1])
            except Exception:
                return 1
    else:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        with conn:
            if user_id:
                cur = conn.execute("UPDATE withdrawals SET status='completed', updated_at=? WHERE user_id=? AND status='pending'", (now, user_id))
            else:
                cur = conn.execute("UPDATE withdrawals SET status='completed', updated_at=? WHERE status='pending'", (now,))
            count = cur.rowcount
        conn.close()
        return count


# ============================================================
# YANDEX FLEET API (TO'LIQ REAL INTEGRATSIYA)
# ============================================================

class YandexFleetAPI:
    FLEET_BASE = "https://fleet-api.taxi.yandex.net"

    def __init__(self, api_key: str, client_id: str, park_id: str):
        self.api_key = api_key.strip()
        self.client_id = client_id.strip()
        self.park_id = park_id.strip()
        self._session: Optional[aiohttp.ClientSession] = None
        self._drivers_cache: List[dict] = []
        self._cache_ts: Optional[datetime] = None
        self._cache_ttl = 25
        self._stats_cache: Optional[dict] = None
        self._stats_cache_ts: Optional[datetime] = None
        self._stats_cache_ttl = 25

    def _is_configured(self) -> bool:
        return bool(self.api_key and self.park_id and self.client_id)

    @property
    def _headers(self) -> dict:
        return {
            "X-Client-ID": self.client_id,
            "X-API-Key": self.api_key,
            "X-Park-ID": self.park_id,
            "Content-Type": "application/json",
            "Accept-Language": "ru"
        }

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(limit=100, limit_per_host=20, enable_cleanup_closed=True)
            timeout = aiohttp.ClientTimeout(total=45, connect=10)
            self._session = aiohttp.ClientSession(connector=connector, timeout=timeout, headers=self._headers)
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    def _extract_balance(self, raw_driver: dict) -> int:
        accounts = raw_driver.get("accounts", [])
        if accounts:
            for acc in accounts:
                acc_type = str(acc.get("type", "")).lower()
                if acc_type in ("current", "personal_wallet", "wallet", "main", "balance"):
                    try:
                        return int(float(acc.get("balance", 0)))
                    except Exception:
                        pass
            try:
                return int(float(accounts[0].get("balance", 0)))
            except Exception:
                pass
        for field in ("balance", "wallet_balance"):
            val = raw_driver.get(field)
            if val is not None:
                try:
                    return int(float(val))
                except Exception:
                    pass
        prof = raw_driver.get("driver_profile", {})
        if "balance" in prof and prof["balance"] is not None:
            try:
                return int(float(prof["balance"]))
            except Exception:
                pass
        return 0

    def _normalize(self, raw_driver: dict) -> dict:
        prof = raw_driver.get("driver_profile", {})
        car = raw_driver.get("car", {})
        last = prof.get("last_name", "").strip()
        first = prof.get("first_name", "").strip()
        middle = prof.get("middle_name", "").strip()
        full_name = f"{last} {first} {middle}".strip() or "Haydovchi"
        brand_model = car.get("brand_and_model", "").strip() or f"{car.get('brand','').strip()} {car.get('model','').strip()}".strip() or "Avtomobil"
        car_number = car.get("number", "").strip() or car.get("normalized_number", "").strip() or "Noma'lum"
        phones = prof.get("phones", [])
        if not phones and prof.get("phone"):
            phones = [prof.get("phone")]
        phone = clean_phone_number(phones[0]) if phones else ""

        work_status = str(prof.get("work_status", "")).lower()
        st_raw = str(raw_driver.get("status", "")).lower()
        cur_st = str(raw_driver.get("current_status", {}).get("status", "")).lower()
        is_on_order = bool("order" in st_raw or "order" in cur_st or raw_driver.get("order") or "busy" in cur_st)

        if is_on_order:
            status_text = "🔴 Band (Zakazda)"
        elif work_status == "working" or "online" in cur_st or "free" in cur_st or "ready" in cur_st:
            status_text = "🟢 Liniyada"
        else:
            status_text = "⚪ Offline"

        return {
            "id": prof.get("id", ""),
            "full_name": full_name,
            "phone": phone,
            "car_model": brand_model,
            "car_number": car_number,
            "balance": self._extract_balance(raw_driver),
            "work_status": work_status,
            "status_text": status_text,
            "is_on_order": is_on_order,
            "raw": raw_driver
        }

    async def get_all_drivers(self, force_refresh: bool = False) -> Tuple[List[dict], str]:
        """
        Barcha haydovchilarni cheklovsiz olish.
        Yandex Fleet API v1 cursor formatiga to'liq moslangan:
        Cursor 'cursor' parametrida hamda 'query.cursor' da uzatiladi.
        """
        if not self._is_configured():
            return [], "Yandex API sozlamalari to'liq emas!"
        now = datetime.now()
        if (not force_refresh and self._drivers_cache and self._cache_ts and (now - self._cache_ts).total_seconds() < self._cache_ttl):
            return self._drivers_cache, ""

        url = f"{self.FLEET_BASE}/v1/parks/driver-profiles/list"
        all_drivers: List[dict] = []
        last_error = ""
        cursor = None
        seen_ids = set()

        try:
            session = await self._get_session()
            for _ in range(100):  # 100 * 500 = 50 000 tagacha haydovchi
                payload: Dict[str, Any] = {
                    "query": {
                        "park": {"id": self.park_id}
                    },
                    "limit": 500
                }
                if cursor:
                    payload["cursor"] = cursor
                    payload["query"]["cursor"] = cursor

                async with session.post(url, json=payload) as resp:
                    text = await resp.text()
                    if resp.status == 429:
                        last_error = "429 Rate Limit"
                        break
                    elif resp.status != 200:
                        last_error = f"HTTP {resp.status}"
                        break

                    data = json.loads(text)
                    batch = data.get("driver_profiles", [])
                    if not batch:
                        break

                    added_count = 0
                    for drv in batch:
                        d_id = drv.get("driver_profile", {}).get("id")
                        if d_id and d_id not in seen_ids:
                            seen_ids.add(d_id)
                            all_drivers.append(drv)
                            added_count += 1

                    if added_count == 0:
                        break

                    # Yandex cursor har xil versiyalarda har joyda keladi
                    new_cursor = data.get("cursor") or data.get("next_cursor") or data.get("pagination", {}).get("next_cursor")
                    if not new_cursor or new_cursor == cursor:
                        break
                    cursor = new_cursor
                    await asyncio.sleep(0.08)
        except Exception as e:
            last_error = str(e)

        if all_drivers:
            self._drivers_cache = all_drivers
            self._cache_ts = now
            return all_drivers, ""

        if self._drivers_cache:
            return self._drivers_cache, ""

        return [], last_error

    async def get_driver_by_phone(self, phone: str) -> Optional[dict]:
        if not self._is_configured():
            return None
        clean_target = clean_phone_number(phone)
        digits_target = re.sub(r"\D", "", clean_target)
        short9 = digits_target[-9:] if len(digits_target) >= 9 else digits_target

        drivers, _ = await self.get_all_drivers(force_refresh=False)
        for raw in drivers:
            prof = raw.get("driver_profile", {})
            phones = prof.get("phones", [])
            if not phones and prof.get("phone"):
                phones = [prof.get("phone")]
            for p in phones:
                p_digits = re.sub(r"\D", "", str(p))
                if short9 and p_digits.endswith(short9):
                    return self._normalize(raw)
        return None

    async def get_driver_balance(self, yandex_driver_id: Optional[str] = None, phone: Optional[str] = None) -> Optional[int]:
        if not self._is_configured():
            return None
        if yandex_driver_id:
            url = f"{self.FLEET_BASE}/v1/parks/driver-profiles/list"
            payload = {"query": {"park": {"id": self.park_id, "driver_profile": {"id": [yandex_driver_id]}}}, "limit": 1}
            try:
                session = await self._get_session()
                async with session.post(url, json=payload) as resp:
                    if resp.status == 200:
                        data = json.loads(await resp.text())
                        drivers = data.get("driver_profiles", [])
                        if drivers:
                            return self._extract_balance(drivers[0])
            except Exception:
                pass

        drivers, _ = await self.get_all_drivers(force_refresh=False)
        clean_p = clean_phone_number(phone) if phone else ""
        digits_target = re.sub(r"\D", "", clean_p)[-9:] if clean_p else ""
        for raw in drivers:
            norm = self._normalize(raw)
            if yandex_driver_id and norm.get("id") == yandex_driver_id:
                return norm["balance"]
            if digits_target and norm.get("phone"):
                p_dig = re.sub(r"\D", "", norm["phone"])
                if p_dig.endswith(digits_target):
                    return norm["balance"]
        return None

    async def get_today_orders_stats(self, yandex_driver_id: Optional[str] = None) -> dict:
        now = datetime.now()
        if (not yandex_driver_id and self._stats_cache and self._stats_cache_ts and (now - self._stats_cache_ts).total_seconds() < self._stats_cache_ttl):
            return self._stats_cache

        default_res = {
            "total_orders": 0, "completed_orders": 0, "cancelled_orders": 0,
            "in_progress_orders": 0, "total_earnings": 0, "cash_earnings": 0,
            "card_earnings": 0, "park_comm": 0, "api_error": ""
        }
        if not self._is_configured():
            default_res["api_error"] = "Yandex API sozlanmagan"
            return default_res

        now_tashkent = datetime.now(TASHKENT_TZ)
        today_start_tashkent = now_tashkent.replace(hour=0, minute=0, second=0, microsecond=0)
        from_utc = today_start_tashkent.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        to_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        active_on_order = 0
        drivers_list, _ = await self.get_all_drivers(force_refresh=False)
        for d_raw in drivers_list:
            norm_d = self._normalize(d_raw)
            if norm_d.get("is_on_order"):
                if not yandex_driver_id or norm_d.get("id") == yandex_driver_id:
                    active_on_order += 1

        session = await self._get_session()
        tx_url = f"{self.FLEET_BASE}/v1/parks/driver-profiles/transactions/list"
        orders_dict = {}
        total_fare_sum = 0
        card_fare_sum = 0
        cash_fare_sum = 0
        tx_error = ""

        try:
            tx_payload = {
                "query": {
                    "park": {
                        "id": self.park_id,
                        "transaction": {"event_at": {"from": from_utc, "to": to_utc}}
                    }
                },
                "limit": 1000
            }
            async with session.post(tx_url, json=tx_payload) as resp:
                if resp.status == 200:
                    data = json.loads(await resp.text())
                    transactions = data.get("transactions", [])
                    for tx in transactions:
                        drv_id = tx.get("driver_profile_id")
                        if yandex_driver_id and drv_id != yandex_driver_id:
                            continue
                        ord_id = tx.get("order_id")
                        cat = str(tx.get("category_id", "")).lower()
                        desc = str(tx.get("description", "")).lower()
                        try:
                            amt = abs(int(float(tx.get("amount", 0))))
                        except Exception:
                            amt = 0
                        if ord_id:
                            if ord_id not in orders_dict:
                                orders_dict[ord_id] = {"cost": 0, "is_card": False}
                            if "trip" in cat or "order" in cat or amt > orders_dict[ord_id]["cost"]:
                                orders_dict[ord_id]["cost"] = max(orders_dict[ord_id]["cost"], amt)
                            if "card" in cat or "cashless" in cat or "card" in desc or "безнал" in desc:
                                orders_dict[ord_id]["is_card"] = True
                else:
                    tx_error = f"HTTP {resp.status}"
        except Exception as e:
            tx_error = str(e)

        for o_info in orders_dict.values():
            c = o_info["cost"]
            total_fare_sum += c
            if o_info["is_card"]:
                card_fare_sum += c
            else:
                cash_fare_sum += c

        completed_count = len(orders_dict)
        total_orders = completed_count + active_on_order
        comm = int(total_fare_sum * (COMMISSION_PERCENT / 100.0))
        result = {
            "total_orders": total_orders,
            "completed_orders": completed_count,
            "cancelled_orders": 0,
            "in_progress_orders": active_on_order,
            "total_earnings": total_fare_sum,
            "cash_earnings": cash_fare_sum,
            "card_earnings": card_fare_sum,
            "park_comm": comm,
            "api_error": tx_error if (total_orders == 0 and tx_error) else ""
        }
        if not yandex_driver_id:
            self._stats_cache = result
            self._stats_cache_ts = now
        return result

    async def create_transaction(self, yandex_driver_id: str, amount: int, description: str) -> bool:
        if not self._is_configured() or not yandex_driver_id:
            return False
        url = f"{self.FLEET_BASE}/v1/parks/driver-profiles/transactions"
        payload = {
            "park_id": self.park_id,
            "driver_profile_id": yandex_driver_id,
            "amount": str(-abs(int(amount))),
            "category_id": "other",
            "description": description
        }
        try:
            session = await self._get_session()
            async with session.post(url, json=payload) as resp:
                text = await resp.text()
                if resp.status in (200, 201):
                    return True
                logger.error(f"Yandex tranzaksiya xatosi: HTTP {resp.status} | {text[:200]}")
        except Exception as e:
            logger.error(f"Yandex tranzaksiya exception: {e}")
        return False


yandex_api = YandexFleetAPI(YANDEX_API_KEY, YANDEX_CLIENT_ID, YANDEX_PARK_ID)


# ============================================================
# KAPITALBANK OPENAPI
# ============================================================

class KapitalBankAPI:
    def __init__(self):
        self.base_url = KAPITAL_API_URL.rstrip("/")
        self.login = KAPITAL_LOGIN
        self.password = KAPITAL_PASSWORD
        self.account = KAPITAL_ACCOUNT
        self.mfo = KAPITAL_MFO
        self.inn = KAPITAL_INN
        self.company_name = KAPITAL_COMPANY_NAME
        self._session: Optional[aiohttp.ClientSession] = None

    def is_configured(self) -> bool:
        return bool(self.login and self.password and self.account and self.mfo and self.account != "0")

    @property
    def auth_header(self) -> str:
        raw_cred = f"{self.login}:{self.password}"
        enc = base64.b64encode(raw_cred.encode("utf-8")).decode("ascii")
        return f"Basic {enc}"

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=25),
                headers={"Content-Type": "application/json", "Authorization": self.auth_header}
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def send_card_payout(self, target_card: str, amount_sum: int, doc_id: int) -> Tuple[bool, str, str]:
        if not self.is_configured():
            return False, "Kapitalbank sozlanmagan", ""
        clean_card = re.sub(r"\D", "", str(target_card))
        if len(clean_card) != 16:
            return False, "Karta 16 ta raqam bo'lishi kerak", ""

        amount_tiyin = int(amount_sum) * 100
        now_tashkent = datetime.now(TASHKENT_TZ)
        date_str = now_tashkent.strftime("%d.%m.%Y")
        now_micro = now_tashkent.strftime("%Y%m%d%H%M%S%f")[:17]
        uniq_id = f"{now_micro}{doc_id}{self.login}"[:35]
        purpose_text = f"Lochin Taxi tulovi #{doc_id}"

        payload = {
            "client_id": 0,
            "sid": "",
            "payment": {
                "document": {
                    "num": str(doc_id)[:10],
                    "branch": self.mfo,
                    "general_id": None,
                    "uniq": uniq_id,
                    "ddate": date_str,
                    "mfo_dt": self.mfo,
                    "acc_dt": self.account,
                    "name_dt": self.company_name,
                    "inn_dt": self.inn,
                    "mfo_ct": self.mfo,
                    "acc_ct": self.account,
                    "name_ct": "Karta egasi",
                    "inn_ct": "",
                    "purpose": purpose_text,
                    "purp_code": "00699",
                    "amount": amount_tiyin,
                    "dtype": "97",
                    "state": 52,
                    "dir": 1,
                    "err": "",
                    "err_msg": ""
                },
                "signs": []
            }
        }
        url = f"{self.base_url}/Mobile.svc/SendPayment"
        try:
            session = await self._get_session()
            async with session.post(url, json=payload) as resp:
                text = await resp.text()
                try:
                    data = json.loads(text)
                except Exception:
                    data = {}
                if resp.status == 200:
                    err = data.get("error")
                    if err:
                        msg = err.get("message") or str(err)
                        return False, f"Bank xatolik: {msg}", ""
                    return True, "To'lov qabul qilindi!", str(data.get("result") or uniq_id)
                else:
                    return False, f"HTTP {resp.status}: {text[:200]}", ""
        except Exception as e:
            logger.error(f"Kapitalbank xatosi: {e}")
            return False, f"Bank ulanish xatosi: {e}", ""


kapital_bank_api = KapitalBankAPI()


# ============================================================
# EXCEL GENERATOR
# ============================================================

async def generate_monthly_excel_report() -> bytes:
    y_drivers, _ = await yandex_api.get_all_drivers(force_refresh=False)
    y_map_by_id = {d.get("driver_profile", {}).get("id"): yandex_api._normalize(d) for d in y_drivers}
    drivers = await db_get_all_bot_drivers()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Lochin Taxi Hisoboti"

    headers = ["№", "POSITION", "F.I.O", "Telefon", "Avtomobil", "Davlat Raqami", "Karta", "Jami Buyurtma", "Daromad", "Komissiya", "Balans", "Yandex ID", "Status"]
    header_fill = PatternFill(start_color="1F497D", end_color="1F497D", fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    align_center = Alignment(horizontal="center", vertical="center")
    thin_border = Border(
        left=Side(style="thin", color="D9D9D9"), right=Side(style="thin", color="D9D9D9"),
        top=Side(style="thin", color="D9D9D9"), bottom=Side(style="thin", color="D9D9D9")
    )

    ws.append(headers)
    for col_num in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_num)
        cell.fill, cell.font, cell.alignment = header_fill, header_font, align_center

    for idx, drv in enumerate(drivers, 1):
        y_id = drv.get("yandex_driver_id")
        if y_id and y_id in y_map_by_id:
            bal = y_map_by_id[y_id]["balance"]
            status = y_map_by_id[y_id]["status_text"]
        else:
            bal = int(drv.get("balance", 0) or 0)
            status = "Noma'lum"

        ws.append([
            idx,
            drv.get("position") or "N/A",
            drv.get("full_name") or "Noma'lum",
            drv.get("phone", ""),
            drv.get("car_model", ""),
            drv.get("car_number", ""),
            mask_card(drv.get("card_number", "")),
            int(drv.get("total_orders", 0) or 0),
            int(drv.get("total_earnings", 0) or 0),
            0,
            bal,
            y_id or "Yo'q",
            status
        ])

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output.getvalue()


# ============================================================
# MATNLAR VA KLAVIATURALAR
# ============================================================

TEXTS = {
    "uz": {
        "welcome": f"🕌 <b>Assalomu alaykum!</b>\n\n🚕 <b>{BOT_NAME}</b> taksoparkiga xush kelibsiz!",
        "register_btn": "📝 Ro'yxatdan o'tish",
        "reg_phone": "📱 <b>Telefon raqamingizni tasdiqlang:</b>\n\n<b>[📱 Telefon raqamni yuborish]</b> tugmasini bosing:",
        "reg_card": "💳 <b>Plastik karta raqamingizni kiriting (16 ta raqam):</b>",
        "reg_name": "👤 <b>Ism va familiyangizni kiriting:</b>",
        "reg_car_model": "🚗 <b>Avtomobilingiz rusumini kiriting:</b>",
        "reg_car_number": "🔢 <b>Avtomobil davlat raqamini kiriting:</b>",
        "reg_success": "✅ <b>Tabriklaymiz! Ro'yxatdan o'tdingiz.</b>\n\n🆔 POSITION ID: <code>{position}</code>",
        "already_reg": "✅ <b>Siz ro'yxatdan o'tgansiz!</b>\n\n🆔 POSITION: <code>{position}</code>\n👤 Haydovchi: <b>{name}</b>",
        "menu_balance": "💰 Balans",
        "menu_orders": "📊 Bugungi buyurtmalar",
        "menu_withdraw": "💸 Pul yechish (24/7)",
        "menu_profile": "👤 Profil",
        "menu_group": "📢 Yangiliklar / Guruh",
        "menu_sos": "🆘 Yordam / SOS",
        "menu_admin": "🛠 Admin Panel",
        "cancel": "❌ Bekor qilish",
        "send_phone_btn": "📱 Telefon raqamni yuborish",
        "action_cancelled": "❌ Amaliyot bekor qilindi.",
        "withdraw_no_money": f"❌ Balans yetarli emas!\nMinimal depozit: <b>{fmt_sum(MIN_DEPOSIT)} so'm</b>",
        "withdraw_min_err": f"❌ Minimal yechish: {fmt_sum(MIN_WITHDRAWAL)} so'm",
        "withdraw_ask": f"💸 <b>Pul yechish:</b>\n\n🔹 Yechish mumkin: <b>{{avail}} so'm</b>\nSummani kiriting:",
        "sos_title": f"🆘 <b>Yordam Markazi</b>\n\n📞 <b>Menejer:</b> {SUPPORT_PHONE_DISPLAY}",
        "sos_btn_loc": "📍 Lokatsiya yuborish",
        "sos_btn_msg": "✍ Xabar yozish",
        "sos_btn_chat": "💬 Menejer bilan chat",
        "sos_ask_loc": "📍 <b>Lokatsiyangizni yuboring:</b>",
        "sos_loc_btn": "📍 Hozirgi joylashuvimni yuborish",
        "sos_ask_msg": "✍ <b>Muammoingizni yozing:</b>",
        "sos_sent": "🚨 <b>Xabaringiz yetkazildi!</b>",
    },
    "ru": {
        "welcome": f"🕌 <b>Ассаламу алейкум!</b>\n\n🚕 Добро пожаловать в <b>{BOT_NAME}</b>!",
        "register_btn": "📝 Регистрация",
        "reg_phone": "📱 <b>Подтвердите номер:</b>",
        "reg_card": "💳 <b>Введите карту 16 цифр:</b>",
        "reg_name": "👤 <b>Введите имя:</b>",
        "reg_car_model": "🚗 <b>Марка авто:</b>",
        "reg_car_number": "🔢 <b>Госномер:</b>",
        "reg_success": "✅ <b>Вы зарегистрированы.</b>\n\n🆔 POSITION ID: <code>{position}</code>",
        "already_reg": "✅ <b>Вы уже зарегистрированы!</b>\n\n🆔 POSITION: <code>{position}</code>\n👤 Водитель: <b>{name}</b>",
        "menu_balance": "💰 Баланс",
        "menu_orders": "📊 Заказы",
        "menu_withdraw": "💸 Вывод",
        "menu_profile": "👤 Профиль",
        "menu_group": "📢 Новости",
        "menu_sos": "🆘 Помощь",
        "menu_admin": "🛠 Админ",
        "cancel": "❌ Отмена",
        "send_phone_btn": "📱 Отправить номер",
        "action_cancelled": "❌ Отменено.",
        "withdraw_no_money": "❌ Недостаточно средств!",
        "withdraw_min_err": f"❌ Мин. сумма: {fmt_sum(MIN_WITHDRAWAL)}",
        "withdraw_ask": "💸 <b>Вывод:</b>\n\n🔹 Доступно: <b>{avail}</b>\nВведите сумму:",
        "sos_title": "🆘 <b>Центр Помощи</b>",
        "sos_btn_loc": "📍 Локация",
        "sos_btn_msg": "✍ Сообщение",
        "sos_btn_chat": "💬 Чат",
        "sos_ask_loc": "📍 <b>Отправьте локацию:</b>",
        "sos_loc_btn": "📍 Отправить",
        "sos_ask_msg": "✍ <b>Опишите проблему:</b>",
        "sos_sent": "🚨 <b>Сообщение отправлено!</b>",
    }
}


def t(lang_code: str, key: str, **kwargs) -> str:
    text = TEXTS.get(lang_code, TEXTS["uz"]).get(key, key)
    return text.format(**kwargs) if kwargs else text


def user_main_kb(lang: str, uid: int) -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton(text=t(lang, "menu_balance")), KeyboardButton(text=t(lang, "menu_withdraw"))],
        [KeyboardButton(text=t(lang, "menu_orders")), KeyboardButton(text=t(lang, "menu_profile"))],
        [KeyboardButton(text=t(lang, "menu_group")), KeyboardButton(text=t(lang, "menu_sos"))]
    ]
    if is_admin(uid):
        buttons.append([KeyboardButton(text=t(lang, "menu_admin"))])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def cancel_kb(lang: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=t(lang, "cancel"))]], resize_keyboard=True)


def phone_request_kb(lang: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t(lang, "send_phone_btn"), request_contact=True)],
            [KeyboardButton(text=t(lang, "cancel"))]
        ],
        resize_keyboard=True
    )


def location_request_kb(lang: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t(lang, "sos_loc_btn"), request_location=True)],
            [KeyboardButton(text=t(lang, "cancel"))]
        ],
        resize_keyboard=True
    )


def language_inline_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🇺🇿 O'zbekcha", callback_data="lang:uz"),
        InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang:ru")
    ]])


def register_reply_kb(lang: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=t(lang, "register_btn"))]], resize_keyboard=True)


def sos_menu_kb(lang: str) -> InlineKeyboardMarkup:
    kb_rows = [
        [InlineKeyboardButton(text=t(lang, "sos_btn_loc"), callback_data="sos:loc")],
        [InlineKeyboardButton(text=t(lang, "sos_btn_msg"), callback_data="sos:msg")]
    ]
    if MANAGER_TG_ID > 0:
        kb_rows.append([InlineKeyboardButton(text=t(lang, "sos_btn_chat"), url=f"tg://user?id={MANAGER_TG_ID}")])
    return InlineKeyboardMarkup(inline_keyboard=kb_rows)


def admin_main_kb(lang: str) -> ReplyKeyboardMarkup:
    is_uz = lang == "uz"
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📊 Statistika" if is_uz else "📊 Статистика"), KeyboardButton(text="🚖 Bugungi Park Zakazlari" if is_uz else "🚖 Заказы парка")],
        [KeyboardButton(text="⏳ Kutilayotgan arizalar" if is_uz else "⏳ Заявки"), KeyboardButton(text="📥 Excel Hisobot" if is_uz else "📥 Excel Отчет")],
        [KeyboardButton(text="🔄 Yandex Sinxronlash" if is_uz else "🔄 Синхронизация"), KeyboardButton(text="📢 Xabar tarqatish" if is_uz else "📢 Рассылка")],
        [KeyboardButton(text="👥 Haydovchilar" if is_uz else "👥 Водители"), KeyboardButton(text="🗑 Haydovchini o'chirish" if is_uz else "🗑 Удалить")],
        [KeyboardButton(text="🚫 Nofaollar" if is_uz else "🚫 Неактивные")],
        [KeyboardButton(text="⬅ Asosiy menyu" if is_uz else "⬅ Главное меню")],
    ], resize_keyboard=True)


# ============================================================
# THROTTLING MIDDLEWARE
# ============================================================

class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, limit: float = 0.4):
        self.limit = limit
        self.user_timestamps: Dict[int, float] = {}
        self.last_cleanup = time.time()

    async def __call__(self, handler, event, data):
        if isinstance(event, Message) and event.from_user:
            user_id = event.from_user.id
            now = time.time()
            if now - self.last_cleanup > 3600:
                self.user_timestamps = {uid: ts for uid, ts in self.user_timestamps.items() if ts > now - 60}
                self.last_cleanup = now
            last_time = self.user_timestamps.get(user_id, 0.0)
            if now - last_time < self.limit:
                return
            self.user_timestamps[user_id] = now
        return await handler(event, data)


# ============================================================
# FSM STATES
# ============================================================

class RegStates(StatesGroup):
    phone = State()
    name = State()
    card = State()
    car_model = State()
    car_number = State()


class WithdrawStates(StatesGroup):
    amount = State()


class SOSStates(StatesGroup):
    waiting_for_location = State()
    waiting_for_message = State()


class AdminBroadcastStates(StatesGroup):
    waiting_for_message = State()


class AdminDeleteDriverStates(StatesGroup):
    waiting_for_query = State()


bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
dp.message.middleware(ThrottlingMiddleware(limit=0.4))
router = Router()
admin_router = Router()

CANCEL_TEXTS = {"❌ Bekor qilish", "❌ Отмена", "bekor", "отмена", "/bekor", "/cancel"}


async def get_lang(uid: int) -> str:
    user = await db_get_user(uid)
    return user.get("language", "uz") if user else "uz"


@router.message(Command("id"))
async def cmd_my_id(message: Message):
    uid = message.from_user.id
    status_str = "✅ Admin" if is_admin(uid) else "❌ Oddiy foydalanuvchi"
    await message.answer(f"🆔 <b>ID:</b> <code>{uid}</code>\n👑 <b>Status:</b> {status_str}")


@router.message(Command("admin"))
async def cmd_direct_admin(message: Message, state: FSMContext):
    uid = message.from_user.id
    if not is_admin(uid):
        await message.answer(f"❌ Siz admin emassiz! ID: <code>{uid}</code>")
        return
    await state.clear()
    lang = await get_lang(uid)
    await message.answer("🛠 <b>Admin Panel:</b>", reply_markup=admin_main_kb(lang))


@router.message(Command("cancel"), StateFilter("*"))
@router.message(F.text.in_(CANCEL_TEXTS), StateFilter("*"))
async def global_cancel_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    uid = message.from_user.id
    user = await db_get_user(uid)
    lang = user.get("language", "uz") if user else "uz"
    kb = user_main_kb(lang, uid) if (user and user.get("is_registered") == 1) else register_reply_kb(lang)
    if is_admin(uid):
        kb = user_main_kb(lang, uid)
    await message.answer(t(lang, "action_cancelled"), reply_markup=kb)


@router.message(CommandStart(), StateFilter("*"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    uid = message.from_user.id
    await db_upsert_start(uid, message.from_user.username or "")
    if is_admin(uid):
        lang = await get_lang(uid)
        await message.answer("👑 <b>Xush kelibsiz, Admin!</b>", reply_markup=user_main_kb(lang, uid))
        return
    user = await db_get_user(uid)
    if user and user.get("is_registered") == 1:
        lang = user.get("language", "uz")
        drv_name = esc(user.get("full_name") or "Haydovchi")
        pos_id = user.get("position") or "N/A"
        await message.answer(
            t(lang, "already_reg", position=pos_id, name=drv_name),
            reply_markup=user_main_kb(lang, uid)
        )
        return
    await message.answer("🌐 <b>Tilni tanlang / Выберите язык:</b>", reply_markup=language_inline_kb())


@router.callback_query(F.data.startswith("lang:"))
async def lang_callback(callback: CallbackQuery) -> None:
    lang = callback.data.split(":")[1]
    uid = callback.from_user.id
    await db_set_language(uid, lang)
    try:
        await callback.message.delete()
    except Exception:
        pass
    if is_admin(uid):
        await callback.message.answer("👑 <b>Admin menyusi:</b>", reply_markup=user_main_kb(lang, uid))
        await callback.answer()
        return
    user = await db_get_user(uid)
    if user and user.get("is_registered") == 1:
        drv_name = esc(user.get("full_name") or "Haydovchi")
        pos_id = user.get("position") or "N/A"
        await callback.message.answer(
            t(lang, "already_reg", position=pos_id, name=drv_name),
            reply_markup=user_main_kb(lang, uid)
        )
    else:
        await callback.message.answer(t(lang, "welcome"), reply_markup=register_reply_kb(lang))
    await callback.answer()


# ============================================================
# RO'YXATDAN O'TISH FLOW
# ============================================================

@router.message(F.text.in_(["📝 Ro'yxatdan o'tish", "📝 Регистрация"]), StateFilter("*"))
async def reg_start_flow(message: Message, state: FSMContext) -> None:
    await state.clear()
    uid = message.from_user.id
    user = await db_get_user(uid)
    lang = user.get("language", "uz") if user else "uz"
    if user and user.get("is_registered") == 1:
        drv_name = esc(user.get("full_name") or "Haydovchi")
        pos_id = user.get("position") or "N/A"
        await message.answer(
            t(lang, "already_reg", position=pos_id, name=drv_name),
            reply_markup=user_main_kb(lang, uid)
        )
        return
    await state.set_state(RegStates.phone)
    await message.answer(t(lang, "reg_phone"), reply_markup=phone_request_kb(lang))


@router.message(RegStates.phone)
async def reg_step_phone(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    lang = await get_lang(uid)
    if not message.contact:
        await message.answer("⚠ Pastdagi tugmani bosing!", reply_markup=phone_request_kb(lang))
        return
    if message.contact.user_id != uid:
        await message.answer("❌ Faqat o'zingizning raqamingiz!", reply_markup=phone_request_kb(lang))
        return
    phone = clean_phone_number(message.contact.phone_number)
    existing_phone_user = await db_get_user_by_phone(phone)
    if existing_phone_user and existing_phone_user.get("telegram_id") and existing_phone_user.get("telegram_id") != uid and existing_phone_user.get("is_registered") == 1:
        await message.answer("❌ Bu raqam boshqa profilga biriktirilgan!", reply_markup=user_main_kb(lang, uid))
        await state.clear()
        return

    await state.update_data(phone=phone)
    search_msg = await message.answer("⏳ <i>Yandex tekshirilmoqda...</i>")
    y_driver = await yandex_api.get_driver_by_phone(phone)
    try:
        await search_msg.delete()
    except Exception:
        pass

    if y_driver:
        await state.update_data(
            full_name=y_driver.get("full_name") or "Haydovchi",
            car_model=y_driver.get("car_model") or "Cobalt",
            car_number=y_driver.get("car_number") or "Noma'lum",
            yandex_driver_id=y_driver.get("id")
        )
        await state.set_state(RegStates.card)
        drv_nm = esc(y_driver.get("full_name", ""))
        car_md = esc(y_driver.get("car_model", ""))
        car_nb = esc(y_driver.get("car_number", ""))
        card_prompt = t(lang, "reg_card")
        await message.answer(
            f"✅ <b>Yandex da topildingiz!</b>\n👤 {drv_nm}\n"
            f"🚗 {car_md} ({car_nb})\n\n{card_prompt}",
            reply_markup=cancel_kb(lang)
        )
    else:
        await state.set_state(RegStates.name)
        await message.answer(t(lang, "reg_name"), reply_markup=cancel_kb(lang))


@router.message(RegStates.name)
async def reg_step_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if len(name) < 3:
        await message.answer("⚠ To'liq ism kiriting:")
        return
    await state.update_data(full_name=name)
    await state.set_state(RegStates.card)
    lang = await get_lang(message.from_user.id)
    await message.answer(t(lang, "reg_card"), reply_markup=cancel_kb(lang))


@router.message(RegStates.card)
async def reg_step_card(message: Message, state: FSMContext) -> None:
    card = re.sub(r"\D", "", message.text or "")
    if not (card.isdigit() and len(card) == 16):
        await message.answer("⚠ Karta 16 ta raqam bo'lishi kerak:")
        return
    await state.update_data(card_number=card)
    data = await state.get_data()
    if data.get("yandex_driver_id"):
        await finish_registration_process(message, state, data)
    else:
        await state.set_state(RegStates.car_model)
        lang = await get_lang(message.from_user.id)
        await message.answer(t(lang, "reg_car_model"), reply_markup=cancel_kb(lang))


@router.message(RegStates.car_model)
async def reg_step_car_model(message: Message, state: FSMContext) -> None:
    await state.update_data(car_model=(message.text or "").strip())
    await state.set_state(RegStates.car_number)
    lang = await get_lang(message.from_user.id)
    await message.answer(t(lang, "reg_car_number"), reply_markup=cancel_kb(lang))


@router.message(RegStates.car_number)
async def reg_step_car_number(message: Message, state: FSMContext) -> None:
    await state.update_data(car_number=(message.text or "").strip().upper())
    data = await state.get_data()
    await finish_registration_process(message, state, data)


async def finish_registration_process(message: Message, state: FSMContext, data: dict):
    uid = message.from_user.id
    lang = await get_lang(uid)
    await state.clear()
    full_name = data.get("full_name") or "Haydovchi"
    phone = data.get("phone") or ""
    card = data.get("card_number") or ""
    car_model = data.get("car_model") or "Chevrolet"
    car_number = data.get("car_number") or ""
    y_id = data.get("yandex_driver_id")
    try:
        position = await db_finish_registration(
            telegram_id=uid, full_name=full_name, phone=phone, card_number=card,
            car_model=car_model, car_number=car_number, yandex_driver_id=y_id
        )
    except Exception:
        position = "LCH-AUTO"

    await message.answer(t(lang, "reg_success", position=position), reply_markup=user_main_kb(lang, uid))
    init_bal = 0
    if y_id:
        try:
            live_b = await yandex_api.get_driver_balance(y_id, phone=phone)
            if live_b is not None:
                init_bal = live_b
                await db_update_balance(uid, live_b)
        except Exception:
            pass

    yandex_txt = "Ulangan ✅" if y_id else "Ulanmagan ❌"
    fn_esc = esc(full_name)
    ph_esc = esc(phone)
    cm_esc = esc(car_model)
    cn_esc = esc(car_number)
    admin_alert = (
        f"🆕 <b>YANGI HAYDOVCHI!</b>\n\n"
        f"🆔 POSITION: <code>{position}</code>\n"
        f"👤 <b>{fn_esc}</b>\n"
        f"📱 <code>{ph_esc}</code>\n"
        f"🚗 {cm_esc} ({cn_esc})\n"
        f"💳 <code>{mask_card(card)}</code>\n"
        f"💰 Balans: <b>{fmt_sum(init_bal)} so'm</b>\n"
        f"🚖 Yandex: {yandex_txt}"
    )
    adm_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💬 Chat", url=f"tg://user?id={uid}")]])
    for adm in ADMIN_IDS:
        if adm != uid:
            try:
                await bot.send_message(adm, admin_alert, reply_markup=adm_kb)
            except Exception:
                pass


# ============================================================
# BALANS VA BUYURTMALAR
# ============================================================

@router.message(F.text.in_(["💰 Balans", "💰 Баланс"]))
async def balance_handler(message: Message) -> None:
    uid = message.from_user.id
    user = await db_get_user(uid)
    if not user or user.get("is_registered") != 1:
        await message.answer("Iltimos, /start", reply_markup=register_reply_kb("uz"))
        return
    lang = user.get("language", "uz")
    wait_msg = await message.answer("⏳ <i>Balans olinmoqda...</i>")
    has_pending = await db_has_pending_withdrawal(user["id"])
    live_bal = await yandex_api.get_driver_balance(user.get("yandex_driver_id"), phone=user.get("phone"))
    if live_bal is not None and not has_pending:
        await db_update_balance(uid, live_bal)
        cur_bal = live_bal
    else:
        cur_bal = int(user.get("balance", 0) or 0)
    try:
        await wait_msg.delete()
    except Exception:
        pass
    today_withdrawn = await db_get_driver_today_withdrawn(user["id"])
    avail = max(0, cur_bal - MIN_DEPOSIT)
    fn = esc(user.get("full_name", ""))
    pos = user.get("position", "N/A")
    ph = esc(user.get("phone", ""))
    cm = esc(user.get("car_model", ""))
    cn = esc(user.get("car_number", ""))
    mc = mask_card(user.get("card_number", ""))
    time_str = datetime.now(TASHKENT_TZ).strftime("%H:%M:%S")

    text = (
        f"💰 <b>{BOT_NAME} — Balans (Real vaqt):</b>\n"
        f"🕒 {time_str}\n\n"
        f"👤 {fn}\n"
        f"🆔 <code>{pos}</code>\n"
        f"📱 <code>{ph}</code>\n"
        f"🚗 {cm} ({cn})\n"
        f"💳 <code>{mc}</code>\n"
        f"➖➖➖\n"
        f"💳 Balans: <b>{fmt_sum(cur_bal)} so'm</b>\n"
        f"🔒 Depozit: {fmt_sum(MIN_DEPOSIT)}\n"
        f"💸 Bugun yechilgan: {fmt_sum(today_withdrawn)}\n"
        f"✅ Yechish mumkin: <b>{fmt_sum(avail)} so'm</b>"
    )
    await message.answer(text, reply_markup=user_main_kb(lang, uid))


@router.message(F.text.in_(["📊 Bugungi buyurtmalar", "📊 Сегодняшние заказы", "📊 Заказы"]))
async def orders_handler(message: Message) -> None:
    uid = message.from_user.id
    user = await db_get_user(uid)
    if not user or user.get("is_registered") != 1:
        await message.answer("Iltimos, /start")
        return
    lang = user.get("language", "uz")
    wait_msg = await message.answer("⏳ <i>Buyurtmalar olinmoqda...</i>")
    y_id = user.get("yandex_driver_id")
    stats = await yandex_api.get_today_orders_stats(yandex_driver_id=y_id) if y_id else {
        "total_orders": 0, "completed_orders": 0, "in_progress_orders": 0,
        "total_earnings": 0, "card_earnings": 0, "cash_earnings": 0
    }
    try:
        await wait_msg.delete()
    except Exception:
        pass
    now_tashkent = datetime.now(TASHKENT_TZ)
    in_prog = stats.get("in_progress_orders", 0)
    act = f"  └ 🚖 Jarayonda: <b>{in_prog} ta</b>\n" if in_prog > 0 else ""
    date_str = now_tashkent.strftime("%d.%m.%Y | %H:%M")
    tot_ord = stats.get("total_orders", 0)
    cmp_ord = stats.get("completed_orders", 0)
    tot_earn = fmt_sum(stats.get("total_earnings", 0))
    crd_earn = fmt_sum(stats.get("card_earnings", 0))
    csh_earn = fmt_sum(stats.get("cash_earnings", 0))

    text = (
        f"📊 <b>Bugungi buyurtmalar:</b>\n"
        f"📅 {date_str}\n\n"
        f"🚕 Jami: <b>{tot_ord} ta</b>\n"
        f"  └ ✅ Tugallangan: <b>{cmp_ord} ta</b>\n"
        f"{act}"
        f"💰 Daromad: <b>{tot_earn} so'm</b>\n"
        f"  └ 💳 Karta: <b>{crd_earn}</b>\n"
        f"  └ 💵 Naqd: <b>{csh_earn}</b>"
    )
    await message.answer(text, reply_markup=user_main_kb(lang, uid))


# ============================================================
# PUL YECHISH FLOW
# ============================================================

@router.message(F.text.in_(["💸 Pul yechish (24/7)", "💸 Вывод средств (24/7)", "💸 Вывод"]), StateFilter("*"))
async def withdraw_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    uid = message.from_user.id
    user = await db_get_user(uid)
    if not user or user.get("is_registered") != 1:
        await message.answer("Iltimos, /start")
        return
    lang = user.get("language", "uz")
    if await db_has_pending_withdrawal(user["id"]):
        await message.answer("⏳ <b>Faol arizangiz bor! Admin tasdig'ini kuting.</b>", reply_markup=user_main_kb(lang, uid))
        return
    live_bal = await yandex_api.get_driver_balance(user.get("yandex_driver_id"), phone=user.get("phone"))
    if live_bal is not None:
        await db_update_balance(uid, live_bal)
        cur_bal = live_bal
    else:
        cur_bal = int(user.get("balance", 0) or 0)
    avail = max(0, cur_bal - MIN_DEPOSIT)
    if avail < MIN_WITHDRAWAL:
        err_msg = t(lang, "withdraw_no_money")
        await message.answer(f"{err_msg}\n\nJoriy: <b>{fmt_sum(cur_bal)}</b>\nYechish mumkin: <b>{fmt_sum(avail)}</b>", reply_markup=user_main_kb(lang, uid))
        return
    await state.set_state(WithdrawStates.amount)
    await message.answer(t(lang, "withdraw_ask", avail=fmt_sum(avail)), reply_markup=cancel_kb(lang))


@router.message(WithdrawStates.amount)
async def withdraw_amount_step(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    lang = await get_lang(uid)
    if message.text in CANCEL_TEXTS:
        await state.clear()
        await message.answer(t(lang, "action_cancelled"), reply_markup=user_main_kb(lang, uid))
        return
    raw = (message.text or "").replace(" ", "").replace("so'm", "").replace("som", "").replace("сум", "").strip()
    if not raw.isdigit():
        await message.answer("⚠ Faqat raqam kiriting:")
        return
    amount = int(raw)
    user = await db_get_user(uid)
    cur_bal = int(user.get("balance", 0) or 0)
    avail = max(0, cur_bal - MIN_DEPOSIT)
    if amount < MIN_WITHDRAWAL:
        await message.answer(t(lang, "withdraw_min_err"))
        return
    if amount > avail:
        await message.answer(f"❌ Ko'pi bilan <b>{fmt_sum(avail)}</b> yecha olasiz.")
        return
    comm = int(amount * (COMMISSION_PERCENT / 100.0))
    net = amount - comm
    full_card_val = user.get("card_number") or ""
    rem_deposit = cur_bal - amount
    await state.clear()
    try:
        w_id = await db_create_withdrawal(
            user_id=user["id"], telegram_id=uid, amount=amount, commission=comm,
            net_amount=net, card_number=full_card_val, status="pending", payout_method="manual", ext_tx_id=""
        )
    except ValueError as val_err:
        await message.answer(f"❌ Xatolik: {val_err}", reply_markup=user_main_kb(lang, uid))
        return

    await message.answer(
        f"⏳ <b>Ariza qabul qilindi! (#{w_id})</b>\n\n"
        f"💰 Summa: <b>{fmt_sum(amount)}</b>\n"
        f"💵 Kartaga: <b>{fmt_sum(net)}</b>\n"
        f"💳 Karta: <code>{mask_card(full_card_val)}</code>\n\n"
        f"👨💻 Admin tasdig'ini kuting.",
        reply_markup=user_main_kb(lang, uid)
    )

    pos = user.get("position", "N/A")
    fn = esc(user.get("full_name", ""))
    ph = esc(user.get("phone", ""))
    cm = esc(user.get("car_model", ""))
    cn = esc(user.get("car_number", ""))

    admin_alert = (
        f"💸 <b>YANGI ARIZA! (#{w_id})</b>\n\n"
        f"🆔 <code>{pos}</code>\n"
        f"👤 {fn}\n"
        f"📱 <code>{ph}</code>\n"
        f"🚗 {cm} ({cn})\n"
        f"💳 <code>{full_card_val}</code>\n"
        f"➖➖➖\n"
        f"💰 Summa: {fmt_sum(amount)}\n"
        f"💵 To'lanishi: <b>{fmt_sum(net)}</b>\n"
        f"🔒 Qoladi: {fmt_sum(rem_deposit)}"
    )
    adm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏦 Kapitalbank orqali to'lash", callback_data=f"adm_kapital:{w_id}")],
        [InlineKeyboardButton(text="💳 Click/Payme orqali to'landi (Yandexdan yechish)", callback_data=f"adm_pay:{w_id}")],
        [InlineKeyboardButton(text="❌ Rad etish", callback_data=f"adm_rej:{w_id}"), InlineKeyboardButton(text="💬 Chat", url=f"tg://user?id={uid}")]
    ])
    for adm in ADMIN_IDS:
        try:
            log_admin_view_card(adm, w_id)
            await bot.send_message(adm, admin_alert, reply_markup=adm_kb)
        except Exception:
            pass


@admin_router.callback_query(F.data.startswith("adm_kapital:"))
async def admin_kapitalbank_payout(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return
    w_id = int(callback.data.split(":")[1])
    wd = await db_get_withdrawal(w_id)
    if not wd or wd.get("status") != "pending":
        await callback.answer("Allaachon ko'rib chiqilgan!", show_alert=True)
        return
    user = await db_get_user_by_id(wd["user_id"])
    if not user:
        await callback.answer("Haydovchi topilmadi!", show_alert=True)
        return
    await callback.answer("⏳ Bankka yuborilmoqda...")
    success, bank_msg, tx_id = await kapital_bank_api.send_card_payout(
        target_card=wd.get("card_number") or "",
        amount_sum=int(wd.get("net_amount", 0)),
        doc_id=w_id
    )
    if not success:
        b_msg = esc(bank_msg)
        await callback.message.reply(f"❌ <b>Bank xatoligi!</b>\n<code>{b_msg}</code>\n\nClick/Payme tugmasidan foydalaning.")
        return
    if user.get("yandex_driver_id"):
        await yandex_api.create_transaction(
            user["yandex_driver_id"],
            int(wd["amount"]),
            f"Kapitalbank to'lovi #{w_id} ({mask_card(wd.get('card_number',''))})"
        )
    await db_update_withdrawal_status(w_id, "completed", ext_tx_id=tx_id)
    try:
        adm_name = esc(callback.from_user.full_name)
        await callback.message.edit_text(f"{callback.message.text}\n\n✅ <b>KAPITALBANK ORQALI TO'LANDI!</b>\n🏦 {tx_id}\n👨💻 {adm_name}")
    except Exception:
        pass
    try:
        mc = mask_card(wd.get("card_number", ""))
        net_str = fmt_sum(wd["net_amount"])
        await bot.send_message(
            user["telegram_id"],
            f"✅ <b>Ariza #{w_id} tasdiqlandi!</b>\n\n💵 <b>{net_str} so'm</b> kartangizga o'tkazildi.\n💳 <code>{mc}</code>",
            reply_markup=user_main_kb("uz", user["telegram_id"])
        )
    except Exception:
        pass


@admin_router.callback_query(F.data.startswith("adm_pay:"))
async def admin_approve_payout(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return
    w_id = int(callback.data.split(":")[1])
    wd = await db_get_withdrawal(w_id)
    if not wd or wd.get("status") != "pending":
        await callback.answer("Allaachon ko'rib chiqilgan!", show_alert=True)
        return
    user = await db_get_user_by_id(wd["user_id"])
    if not user:
        await callback.answer("Haydovchi topilmadi!", show_alert=True)
        return
    if user.get("yandex_driver_id"):
        await yandex_api.create_transaction(
            user["yandex_driver_id"],
            int(wd["amount"]),
            f"Click/Payme #{w_id} ({mask_card(wd.get('card_number',''))})"
        )
    await db_update_withdrawal_status(w_id, "completed")
    try:
        adm_name = esc(callback.from_user.full_name)
        await callback.message.edit_text(f"{callback.message.text}\n\n✅ <b>TO'LANDI (Click/Payme) VA YANDEXDAN YECHILDI!</b>\n👨💻 {adm_name}")
    except Exception:
        pass
    await callback.answer("Tasdiqlandi!")
    try:
        net_str = fmt_sum(wd["net_amount"])
        await bot.send_message(
            user["telegram_id"],
            f"✅ <b>Ariza #{w_id} tasdiqlandi!</b>\n\n💵 <b>{net_str} so'm</b> kartangizga o'tkazildi.",
            reply_markup=user_main_kb("uz", user["telegram_id"])
        )
    except Exception:
        pass


@admin_router.callback_query(F.data.startswith("adm_rej:"))
async def admin_reject_payout(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return
    w_id = int(callback.data.split(":")[1])
    wd = await db_get_withdrawal(w_id)
    if not wd or wd.get("status") != "pending":
        await callback.answer("Allaachon ko'rib chiqilgan!", show_alert=True)
        return
    await db_refund_withdrawal(w_id)
    user = await db_get_user_by_id(wd["user_id"])
    try:
        adm_name = esc(callback.from_user.full_name)
        await callback.message.edit_text(f"{callback.message.text}\n\n❌ <b>RAD ETILDI VA QAYTARILDI.</b>\n👨💻 {adm_name}")
    except Exception:
        pass
    await callback.answer("Rad etildi!")
    if user:
        try:
            amt_str = fmt_sum(wd["amount"])
            await bot.send_message(
                user["telegram_id"],
                f"❌ <b>Ariza #{w_id} rad etildi!</b>\n\n💰 {amt_str} so'm balansga qaytarildi.",
                reply_markup=user_main_kb("uz", user["telegram_id"])
            )
        except Exception:
            pass


# ============================================================
# PROFIL VA SOS
# ============================================================

@router.message(F.text.in_(["👤 Profil", "👤 Профиль"]))
async def profile_handler(message: Message) -> None:
    uid = message.from_user.id
    user = await db_get_user(uid)
    if not user or user.get("is_registered") != 1:
        await message.answer("Iltimos, /start")
        return
    lang = user.get("language", "uz")
    y_val = "Ulangan ✅" if user.get("yandex_driver_id") else "Ulanmagan ❌"
    pos = user.get("position", "N/A")
    fn = esc(user.get("full_name", ""))
    ph = esc(user.get("phone", ""))
    cm = esc(user.get("car_model", ""))
    cn = esc(user.get("car_number", ""))
    mc = mask_card(user.get("card_number", ""))

    text = (
        f"👤 <b>Profil:</b>\n\n"
        f"🆔 <code>{pos}</code>\n"
        f"👤 <b>{fn}</b>\n"
        f"📱 <b>{ph}</b>\n"
        f"🚗 <b>{cm} ({cn})</b>\n"
        f"💳 <code>{mc}</code>\n"
        f"🚕 Yandex: <b>{y_val}</b>"
    )
    inline_rows = [[InlineKeyboardButton(text="🌐 Tilni o'zgartirish", callback_data="change_lang_menu")]]
    if is_admin(uid):
        inline_rows.append([InlineKeyboardButton(text="🛠 Admin Panel", callback_data="open_admin_panel_cb")])
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=inline_rows))


@router.callback_query(F.data == "open_admin_panel_cb")
async def open_admin_panel_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return
    await state.clear()
    lang = await get_lang(callback.from_user.id)
    await callback.message.answer("🛠 <b>Admin Panel:</b>", reply_markup=admin_main_kb(lang))
    await callback.answer()


@router.callback_query(F.data == "change_lang_menu")
async def change_lang_menu_cb(callback: CallbackQuery) -> None:
    await callback.message.edit_text("🌐 Tilni tanlang:", reply_markup=language_inline_kb())
    await callback.answer()


@router.message(F.text.in_(["📢 Yangiliklar / Guruh", "📢 Новости / Группа", "📢 Новости"]))
async def group_handler(message: Message) -> None:
    lang = await get_lang(message.from_user.id)
    btn_txt = "💬 Guruhga qo'shilish" if lang == "uz" else "💬 Вступить"
    await message.answer(f"📢 <b>{BOT_NAME} Guruhi:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=btn_txt, url=DRIVER_GROUP_LINK)]]))


@router.message(F.text.in_(["🆘 Yordam / SOS", "🆘 Помощь / SOS", "🆘 Помощь"]), StateFilter("*"))
async def sos_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    lang = await get_lang(message.from_user.id)
    await message.answer(t(lang, "sos_title"), reply_markup=sos_menu_kb(lang))


@router.callback_query(F.data == "sos:loc")
async def sos_location_flow(callback: CallbackQuery, state: FSMContext) -> None:
    lang = await get_lang(callback.from_user.id)
    await state.set_state(SOSStates.waiting_for_location)
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer(t(lang, "sos_ask_loc"), reply_markup=location_request_kb(lang))
    await callback.answer()


@router.message(SOSStates.waiting_for_location, F.location)
async def sos_receive_location_geo(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    lang = await get_lang(uid)
    await state.clear()
    user = await db_get_user(uid) or {}
    lat, lon = message.location.latitude, message.location.longitude
    maps_url = f"https://maps.google.com/?q={lat},{lon}"
    fn = esc(user.get("full_name") or "Noma'lum")
    pos = user.get("position") or "N/A"
    ph = esc(user.get("phone", ""))
    cm = esc(user.get("car_model", ""))
    cn = esc(user.get("car_number", ""))

    alert = (
        f"🚨 <b>SOS / LOKATSIYA!</b>\n\n"
        f"👤 {fn} (<code>{pos}</code>)\n"
        f"📱 <code>{ph}</code>\n"
        f"🚗 {cm} ({cn})\n\n"
        f"📍 <a href='{maps_url}'>Xaritada ochish</a>"
    )
    adm_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💬 Chat", url=f"tg://user?id={uid}")]])
    for adm in ADMIN_IDS:
        try:
            await bot.send_message(adm, alert, reply_markup=adm_kb)
            await bot.send_location(adm, latitude=lat, longitude=lon)
        except Exception:
            pass
    await message.answer(t(lang, "sos_sent"), reply_markup=user_main_kb(lang, uid))


@router.message(SOSStates.waiting_for_location, F.text)
async def sos_receive_location_text(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    lang = await get_lang(uid)
    if message.text in CANCEL_TEXTS:
        await state.clear()
        await message.answer(t(lang, "action_cancelled"), reply_markup=user_main_kb(lang, uid))
        return
    await state.clear()
    user = await db_get_user(uid) or {}
    fn = esc(user.get("full_name") or "Noma'lum")
    pos = user.get("position") or "N/A"
    ph = esc(user.get("phone", ""))
    msg_addr = esc(message.text.strip())

    alert = (
        f"🚨 <b>SOS / MANZIL:</b>\n\n"
        f"👤 {fn} (<code>{pos}</code>)\n"
        f"📱 <code>{ph}</code>\n\n"
        f"📍 {msg_addr}"
    )
    adm_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💬 Chat", url=f"tg://user?id={uid}")]])
    for adm in ADMIN_IDS:
        try:
            await bot.send_message(adm, alert, reply_markup=adm_kb)
        except Exception:
            pass
    await message.answer(t(lang, "sos_sent"), reply_markup=user_main_kb(lang, uid))


@router.callback_query(F.data == "sos:msg")
async def sos_message_flow(callback: CallbackQuery, state: FSMContext) -> None:
    lang = await get_lang(callback.from_user.id)
    await state.set_state(SOSStates.waiting_for_message)
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer(t(lang, "sos_ask_msg"), reply_markup=cancel_kb(lang))
    await callback.answer()


@router.message(SOSStates.waiting_for_message)
async def sos_receive_text_message(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    lang = await get_lang(uid)
    if message.text in CANCEL_TEXTS:
        await state.clear()
        await message.answer(t(lang, "action_cancelled"), reply_markup=user_main_kb(lang, uid))
        return
    await state.clear()
    user = await db_get_user(uid) or {}
    fn = esc(user.get("full_name") or "Noma'lum")
    pos = user.get("position") or "N/A"
    ph = esc(user.get("phone", ""))
    txt_msg = esc(message.text or "")

    alert = (
        f"📩 <b>MUROJAAT:</b>\n\n"
        f"👤 {fn} (<code>{pos}</code>)\n"
        f"📱 <code>{ph}</code>\n\n"
        f"✍ {txt_msg}"
    )
    adm_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💬 Javob", url=f"tg://user?id={uid}")]])
    for adm in ADMIN_IDS:
        try:
            await bot.send_message(adm, alert, reply_markup=adm_kb)
        except Exception:
            pass
    await message.answer(t(lang, "sos_sent"), reply_markup=user_main_kb(lang, uid))


# ============================================================
# ADMIN PANEL HANDLERS
# ============================================================

@admin_router.message(F.text.in_(["🛠 Admin Panel", "🛠 Админ Панель", "🛠 Админ"]), StateFilter("*"))
async def admin_open(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    lang = await get_lang(message.from_user.id)
    await message.answer("🛠 <b>Admin Panel:</b>", reply_markup=admin_main_kb(lang))


@admin_router.message(Command("clear_pending"))
async def cmd_clear_pending(message: Message):
    if not is_admin(message.from_user.id):
        return
    args = message.text.split()
    if len(args) > 1:
        driver = await db_find_driver_by_query(args[1].strip())
        if not driver:
            await message.answer(f"❌ '{args[1]}' topilmadi.")
            return
        count = await db_force_complete_pending(user_id=driver["id"])
        d_name = esc(driver.get("full_name"))
        await message.answer(f"✅ {d_name} ning {count} ta arizasi yopildi!")
    else:
        count = await db_force_complete_pending()
        await message.answer(f"✅ Barcha {count} ta ariza yopildi!")


@admin_router.message(F.text.in_(["⏳ Kutilayotgan arizalar", "⏳ Заявки на вывод", "⏳ Заявки"]))
async def admin_pending_withdrawals_list(message: Message):
    if not is_admin(message.from_user.id):
        return
    pending_list = await db_get_all_pending_withdrawals()
    if not pending_list:
        await message.answer("✅ Kutilayotgan ariza yo'q.")
        return
    await message.answer(f"⏳ <b>Kutilayotgan arizalar (Jami: {len(pending_list)} ta):</b>")
    for wd in pending_list:
        w_id = wd["id"]
        pos = wd.get("position", "N/A")
        fn = esc(wd.get("full_name", "Haydovchi"))
        ph = esc(wd.get("phone", ""))
        cn = wd.get("card_number", "")
        req_sum = fmt_sum(wd.get("amount", 0))
        net_sum = fmt_sum(wd.get("net_amount", 0))

        alert_text = (
            f"💸 <b>Ariza #{w_id}</b>\n"
            f"🆔 <code>{pos}</code>\n"
            f"👤 {fn}\n"
            f"📱 <code>{ph}</code>\n"
            f"💳 <code>{cn}</code>\n"
            f"💰 {req_sum} | To'lanadi: <b>{net_sum}</b>"
        )
        adm_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏦 Kapitalbank", callback_data=f"adm_kapital:{w_id}")],
            [InlineKeyboardButton(text="💳 Click/Payme to'landi", callback_data=f"adm_pay:{w_id}")],
            [InlineKeyboardButton(text="❌ Rad etish", callback_data=f"adm_rej:{w_id}")]
        ])
        await message.answer(alert_text, reply_markup=adm_kb)


@admin_router.message(F.text.in_(["🚖 Bugungi Park Zakazlari", "🚖 Заказы парка за сегодня", "🚕 Bugungi Park Zakazlari", "🚖 Заказы парка"]))
async def admin_park_today_orders(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    status_msg = await message.answer("⏳ <i>Yandex dan olinmoqda...</i>")
    stats = await yandex_api.get_today_orders_stats(yandex_driver_id=None)
    try:
        await status_msg.delete()
    except Exception:
        pass
    now_tashkent = datetime.now(TASHKENT_TZ)
    in_prog = stats.get("in_progress_orders", 0)
    in_prog_str = f"  └ 🚖 Jarayonda: <b>{in_prog} ta</b>\n" if in_prog > 0 else ""
    date_str = now_tashkent.strftime("%d.%m.%Y | %H:%M:%S")
    tot_ord = stats.get("total_orders", 0)
    cmp_ord = stats.get("completed_orders", 0)
    tot_earn = fmt_sum(stats.get("total_earnings", 0))
    crd_earn = fmt_sum(stats.get("card_earnings", 0))
    csh_earn = fmt_sum(stats.get("cash_earnings", 0))

    await message.answer(
        f"🚖 <b>Bugungi Park Buyurtmalari (Real vaqt):</b>\n"
        f"📅 {date_str}\n\n"
        f"🚕 Jami: <b>{tot_ord} ta</b>\n"
        f"  └ ✅ Tugallangan: <b>{cmp_ord} ta</b>\n"
        f"{in_prog_str}\n"
        f"💰 Aylanma: <b>{tot_earn} so'm</b>\n"
        f"  └ 💳 Karta: <b>{crd_earn}</b>\n"
        f"  └ 💵 Naqd: <b>{csh_earn}</b>"
    )


@admin_router.message(F.text.in_(["📊 Statistika", "📊 Статистика"]))
async def admin_stats_handler(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    stats = await db_get_stats()
    tot_u = stats.get("total_users", 0)
    bot_u = stats.get("bot_users", 0)
    reg_d = stats.get("registered_drivers", 0)
    y_lnk = stats.get("yandex_linked", 0)
    td_w = fmt_sum(stats.get("today_withdrawn", 0))
    mn_w = fmt_sum(stats.get("month_withdrawn", 0))
    tt_w = fmt_sum(stats.get("total_withdrawn", 0))
    pn_c = stats.get("pending_count", 0)
    pn_s = fmt_sum(stats.get("pending_sum", 0))

    await message.answer(
        f"📊 <b>{BOT_NAME} — Statistika:</b>\n\n"
        f"👥 Jami ro'yxatdagilar: <b>{tot_u} ta</b>\n"
        f"🤖 Botga ulangan haydovchilar: <b>{bot_u} ta</b>\n"
        f"🚕 Ro'yxatdan o'tganlar: <b>{reg_d} ta</b>\n"
        f"🔗 Yandex ulangan: <b>{y_lnk} ta</b>\n"
        f"➖➖➖\n"
        f"📅 Bugun yechilgan: <b>{td_w}</b>\n"
        f"🗓 Oyda: <b>{mn_w}</b>\n"
        f"💸 Jami: <b>{tt_w}</b>\n\n"
        f"⏳ Kutilayotgan: <b>{pn_c} ta</b> ({pn_s})"
    )


@admin_router.message(F.text.in_(["📥 Excel Hisobot", "📥 Excel Отчет"]))
async def admin_export_excel(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    status_msg = await message.answer("⏳ <i>Excel tayyorlanmoqda...</i>")
    try:
        excel_bytes = await generate_monthly_excel_report()
        now = datetime.now(TASHKENT_TZ)
        time_fn = now.strftime("%Y_%m_%d_%H%M")
        file = BufferedInputFile(excel_bytes, filename=f"Lochin_Taxi_{time_fn}.xlsx")
        m_name = UZ_MONTHS.get(now.month, "")
        await bot.send_document(chat_id=message.chat.id, document=file, caption=f"📊 <b>{now.year}-yil {m_name} hisoboti</b>")
        try:
            await status_msg.delete()
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Excel xatosi: {e}")
        await status_msg.edit_text("❌ Excel xatosi.")


@admin_router.message(F.text.in_(["🔄 Yandex Sinxronlash", "🔄 Синхронизация Яндекс", "🔄 Синхронизация"]))
async def admin_sync_all_drivers(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    status_msg = await message.answer("⏳ <i>Yandex bazasi yuklanmoqda...</i>")
    try:
        drivers, err_msg = await yandex_api.get_all_drivers(force_refresh=True)
        if not drivers:
            err_d = esc(err_msg or "Noma'lum xatolik")
            await status_msg.edit_text(f"❌ <b>Yandex dan ma'lumot olinmadi!</b>\n\n<code>{err_d}</code>")
            return

        pending_yandex_ids = await db_get_pending_yandex_ids()
        inserted_count = 0
        updated_count = 0
        now = tashkent_now_iso()

        for raw in drivers:
            norm = yandex_api._normalize(raw)
            y_id = norm["id"]
            if not y_id:
                continue

            bal = int(norm["balance"])
            phone = norm["phone"]
            p_clean = clean_phone_number(phone) if phone else ""
            short9 = p_clean[-9:] if p_clean else ""
            full_name = norm["full_name"]
            car_model = norm["car_model"]
            car_num = norm["car_number"]

            if db_pool:
                async with db_pool.acquire() as conn:
                    if short9:
                        row = await conn.fetchrow("SELECT id FROM users WHERE yandex_driver_id=$1 OR phone LIKE $2 LIMIT 1", y_id, f"%{short9}")
                    else:
                        row = await conn.fetchrow("SELECT id FROM users WHERE yandex_driver_id=$1 LIMIT 1", y_id)
                    if row:
                        u_id = row["id"]
                        if y_id in pending_yandex_ids:
                            await conn.execute("UPDATE users SET full_name=$1, car_model=$2, car_number=$3, updated_at=$4 WHERE id=$5", full_name, car_model, car_num, now, u_id)
                        else:
                            await conn.execute("UPDATE users SET full_name=$1, car_model=$2, car_number=$3, balance=$4, yandex_driver_id=$5, updated_at=$6 WHERE id=$7", full_name, car_model, car_num, bal, y_id, now, u_id)
                        updated_count += 1
                    else:
                        new_pos = await db_generate_unique_position()
                        await conn.execute(
                            "INSERT INTO users (full_name, phone, car_model, car_number, position, balance, yandex_driver_id, is_registered, created_at, updated_at) "
                            "VALUES ($1,$2,$3,$4,$5,$6,$7,0,$8,$8) ON CONFLICT (position) DO NOTHING",
                            full_name, p_clean, car_model, car_num, new_pos, bal, y_id, now
                        )
                        inserted_count += 1
            else:
                conn = sqlite3.connect(DB_PATH, timeout=10)
                conn.row_factory = sqlite3.Row
                if short9:
                    row = conn.execute("SELECT id FROM users WHERE yandex_driver_id=? OR phone LIKE ? LIMIT 1", (y_id, f"%{short9}")).fetchone()
                else:
                    row = conn.execute("SELECT id FROM users WHERE yandex_driver_id=? LIMIT 1", (y_id,)).fetchone()
                if row:
                    if y_id not in pending_yandex_ids:
                        with conn:
                            conn.execute("UPDATE users SET full_name=?, car_model=?, car_number=?, balance=?, yandex_driver_id=?, updated_at=? WHERE id=?", (full_name, car_model, car_num, bal, y_id, now, row["id"]))
                    else:
                        with conn:
                            conn.execute("UPDATE users SET full_name=?, car_model=?, car_number=?, updated_at=? WHERE id=?", (full_name, car_model, car_num, now, row["id"]))
                    updated_count += 1
                else:
                    new_pos = await db_generate_unique_position()
                    with conn:
                        conn.execute(
                            "INSERT OR IGNORE INTO users (full_name, phone, car_model, car_number, position, balance, yandex_driver_id, is_registered, created_at, updated_at) "
                            "VALUES (?,?,?,?,?,?,?,0,?,?)",
                            (full_name, p_clean, car_model, car_num, new_pos, bal, y_id, now, now)
                        )
                    inserted_count += 1
                conn.close()

        p_len = len(pending_yandex_ids)
        d_len = len(drivers)
        await status_msg.edit_text(
            f"✅ <b>Yandex sinxronlash yakunlandi!</b>\n\n"
            f"🚕 Jami Yandex haydovchilari: <b>{d_len} ta</b>\n"
            f"🆕 Yangi kiritilgan: <b>{inserted_count} ta</b>\n"
            f"🔄 Yangilangan: <b>{updated_count} ta</b>\n"
            f"🔒 Ariza kutilayotgan: <b>{p_len} ta</b>"
        )
    except Exception as e:
        logger.error(f"Sync xatosi: {e}")
        try:
            err_str = esc(str(e))
            await status_msg.edit_text(f"❌ Xatolik: <code>{err_str}</code>")
        except Exception:
            pass


@admin_router.message(F.text.in_(["👥 Haydovchilar", "👥 Водители"]))
async def admin_list_drivers(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    wait_msg = await message.answer("⏳ <i>Botga ulangan haydovchilar ro'yxati va jonli holati olinmoqda...</i>")

    drivers = await db_get_all_bot_drivers()

    y_drivers, _ = await yandex_api.get_all_drivers(force_refresh=False)
    y_map_by_id = {d.get("driver_profile", {}).get("id"): yandex_api._normalize(d) for d in y_drivers}
    y_map_by_phone = {re.sub(r"\D", "", n.get("phone", ""))[-9:]: n for n in y_map_by_id.values() if n.get("phone")}

    try:
        await wait_msg.delete()
    except Exception:
        pass

    if not drivers:
        await message.answer("Hozircha botda haydovchi yo'q.")
        return

    d_total = len(drivers)
    await message.answer(f"👥 <b>Botga Ulangan Haydovchilar (Jami: {d_total} ta) — Real vaqt:</b>")

    chunk_size = 10
    for chunk_start in range(0, d_total, chunk_size):
        chunk = drivers[chunk_start:chunk_start + chunk_size]
        text = ""
        for idx, drv in enumerate(chunk, chunk_start + 1):
            y_id = drv.get("yandex_driver_id")
            y_info = y_map_by_id.get(y_id) if y_id else None
            if not y_info and drv.get("phone"):
                ph9 = re.sub(r"\D", "", drv.get("phone", ""))[-9:]
                y_info = y_map_by_phone.get(ph9)

            if y_info:
                status = y_info.get("status_text", "⚪ Offline")
                bal_str = fmt_sum(y_info.get("balance", 0))
            else:
                status = "⚪ Offline"
                bal_str = fmt_sum(drv.get("balance", 0))

            card_str = mask_card(drv.get("card_number", ""))
            pos = drv.get("position", "N/A")
            fn = esc(drv.get("full_name", "Haydovchi"))
            ph = esc(drv.get("phone", ""))
            cm = esc(drv.get("car_model", ""))
            cn = esc(drv.get("car_number", ""))

            text += (
                f"<b>{idx}.</b> {status} | 🆔 <code>{pos}</code> — <b>{fn}</b>\n"
                f"   📱 <code>{ph}</code> | 🚗 {cm} ({cn})\n"
                f"   💳 <code>{card_str}</code> | 💰 Balans: <b>{bal_str} so'm</b>\n"
                f"---------------------------\n"
            )
        if text:
            await message.answer(text)
            await asyncio.sleep(0.2)


@admin_router.message(F.text.in_(["🗑 Haydovchini o'chirish", "🗑 Удалить водителя", "🗑 Удалить"]), StateFilter("*"))
async def admin_delete_driver_prompt(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    await state.set_state(AdminDeleteDriverStates.waiting_for_query)
    lang = await get_lang(message.from_user.id)
    await message.answer("🗑 <b>Haydovchini o'chirish:</b>\n\nPOSITION ID yoki telefon yuboring:", reply_markup=cancel_kb(lang))


@admin_router.message(AdminDeleteDriverStates.waiting_for_query)
async def admin_delete_driver_find(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    lang = await get_lang(message.from_user.id)
    if message.text in CANCEL_TEXTS:
        await state.clear()
        await message.answer("❌ Bekor qilindi.", reply_markup=admin_main_kb(lang))
        return
    driver = await db_find_driver_by_query((message.text or "").strip())
    if not driver:
        msg_t = esc(message.text)
        await message.answer(f"❌ '{msg_t}' topilmadi!", reply_markup=cancel_kb(lang))
        return
    await state.clear()
    pos = driver.get("position", "N/A")
    fn = esc(driver.get("full_name", ""))
    ph = esc(driver.get("phone", ""))
    cm = esc(driver.get("car_model", ""))
    cn = esc(driver.get("car_number", ""))
    b_val = fmt_sum(driver.get("balance", 0))

    info_txt = (
        f"⚠ <b>O'chirasizmi?</b>\n\n"
        f"🆔 <code>{pos}</code>\n"
        f"👤 <b>{fn}</b>\n"
        f"📱 <code>{ph}</code>\n"
        f"🚗 {cm} ({cn})\n"
        f"💰 {b_val}"
    )
    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🗑 Ha, o'chirish", callback_data=f"del_confirm:{driver['id']}"),
        InlineKeyboardButton(text="❌ Bekor", callback_data="del_cancel")
    ]])
    await message.answer(info_txt, reply_markup=confirm_kb)


@admin_router.callback_query(F.data.startswith("del_confirm:"))
async def admin_delete_driver_confirm(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return
    user_id = int(callback.data.split(":")[1])
    success = await db_delete_user_by_id(user_id)
    if success:
        await callback.message.edit_text("✅ <b>O'chirildi!</b>")
    else:
        await callback.message.edit_text("❌ Xatolik.")
    await callback.answer("Bajarildi!")


@admin_router.callback_query(F.data == "del_cancel")
async def admin_delete_driver_cancel(callback: CallbackQuery):
    await callback.message.edit_text("❌ Bekor qilindi.")
    await callback.answer()


@admin_router.message(F.text.in_(["📢 Xabar tarqatish", "📢 Рассылка"]))
async def admin_broadcast_prompt(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    await state.set_state(AdminBroadcastStates.waiting_for_message)
    lang = await get_lang(message.from_user.id)
    await message.answer("📢 <b>Barcha botdagi haydovchilarga yuboriladigan xabarni yozing:</b>", reply_markup=cancel_kb(lang))


@admin_router.message(AdminBroadcastStates.waiting_for_message)
async def admin_broadcast_send(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    lang = await get_lang(message.from_user.id)
    if message.text in CANCEL_TEXTS:
        await state.clear()
        await message.answer("❌ Bekor qilindi.", reply_markup=admin_main_kb(lang))
        return
    await state.clear()
    users = await db_get_bot_users_for_broadcast()
    u_len = len(users)
    status_msg = await message.answer(f"⏳ <i>{u_len} ta haydovchiga yuborilmoqda...</i>")
    sent = 0
    fail = 0
    for u in users:
        tg_id = u.get("telegram_id")
        if tg_id and tg_id > 0:
            try:
                await bot.copy_message(chat_id=tg_id, from_chat_id=message.chat.id, message_id=message.message_id)
                sent += 1
                await asyncio.sleep(0.05)
            except Exception:
                fail += 1
    await status_msg.edit_text(
        f"📢 <b>Xabar tarqatish yakunlandi!</b>\n\n"
        f"✅ Yetkazildi: <b>{sent} ta</b>\n"
        f"❌ Yetmadi (bloklagan): <b>{fail} ta</b>\n"
        f"📊 Jami bot foydalanuvchisi: <b>{u_len} ta</b>"
    )
    await message.answer("🛠 <b>Admin Panel:</b>", reply_markup=admin_main_kb(lang))


@admin_router.message(F.text.in_(["🚫 Nofaollar", "🚫 Неактивные"]))
async def admin_inactive_drivers(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    ten_days_ago = (datetime.now(TASHKENT_TZ) - timedelta(days=10)).isoformat()
    if db_pool:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT position, full_name, phone, car_model, car_number, last_activity FROM users "
                "WHERE (is_registered=1 OR (yandex_driver_id IS NOT NULL AND yandex_driver_id != '')) "
                "AND (last_activity < $1 OR is_blocked=1) ORDER BY id DESC LIMIT 20",
                ten_days_ago
            )
            inactive = [dict(r) for r in rows]
    else:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        inactive = [dict(r) for r in conn.execute(
            "SELECT position, full_name, phone, car_model, car_number, last_activity FROM users "
            "WHERE (is_registered=1 OR (yandex_driver_id IS NOT NULL AND yandex_driver_id != '')) "
            "AND (last_activity < ? OR is_blocked=1) ORDER BY id DESC LIMIT 20",
            (ten_days_ago,)
        ).fetchall()]
        conn.close()

    if not inactive:
        await message.answer("✅ Barcha faol!")
        return

    in_len = len(inactive)
    text = f"🚫 <b>10+ kundan beri nofaol ({in_len} ta):</b>\n\n"
    for drv in inactive:
        fn = esc(drv.get("full_name", ""))
        ph = esc(drv.get("phone", ""))
        cm = esc(drv.get("car_model", ""))
        cn = esc(drv.get("car_number", ""))
        act_date = str(drv.get("last_activity", ""))[:10]
        text += (
            f"👤 <b>{fn}</b> | 📱 {ph}\n"
            f"🚗 {cm} ({cn})\n"
            f"📅 {act_date}\n---\n"
        )
    await message.answer(text)


@admin_router.message(F.text.in_(["⬅ Asosiy menyu", "⬅ Главное меню"]), StateFilter("*"))
async def back_to_user_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    uid = message.from_user.id
    lang = await get_lang(uid)
    await message.answer("Asosiy menyu:", reply_markup=user_main_kb(lang, uid))


# ============================================================
# AVTOMATIK SCHEDULERLAR
# ============================================================

async def daily_morning_reminder():
    last_sent_day = -1
    while True:
        try:
            now = datetime.now(TASHKENT_TZ)
            if now.hour == 8 and now.minute == 0 and now.day != last_sent_day:
                last_sent_day = now.day
                drivers = await db_get_bot_users_for_broadcast()
                text = (
                    f"🕌 <b>Assalomu alaykum!</b>\n\n"
                    f"🚕 <b>{BOT_NAME}</b> jamoasi eslatadi:\n"
                    f"⚠ Yo'l qoidalariga amal qiling!\n"
                    f"🤝 Xushmuomala bo'ling.\n"
                    f"🧼 Avto tozaligiga e'tibor bering.\n\n"
                    f"✨ <i>Xayrli kun!</i>"
                )
                for d in drivers:
                    tg_id = d.get("telegram_id")
                    if tg_id and tg_id > 0:
                        try:
                            await bot.send_message(tg_id, text)
                            await asyncio.sleep(0.08)
                        except Exception:
                            pass
        except Exception as e:
            logger.error(f"daily reminder xatosi: {e}")
        await asyncio.sleep(30)


async def yandex_auto_sync_scheduler():
    while True:
        try:
            await asyncio.sleep(300)
            drivers, _ = await yandex_api.get_all_drivers(force_refresh=True)
            if drivers:
                pending_yandex_ids = await db_get_pending_yandex_ids()
                now = tashkent_now_iso()
                for raw_drv in drivers:
                    norm = yandex_api._normalize(raw_drv)
                    y_id = norm["id"]
                    bal = int(norm["balance"])
                    if not y_id:
                        continue
                    p_clean = clean_phone_number(norm["phone"]) if norm["phone"] else ""
                    short9 = p_clean[-9:] if p_clean else ""
                    if db_pool:
                        async with db_pool.acquire() as conn:
                            if short9:
                                row = await conn.fetchrow("SELECT id FROM users WHERE yandex_driver_id=$1 OR phone LIKE $2 LIMIT 1", y_id, f"%{short9}")
                            else:
                                row = await conn.fetchrow("SELECT id FROM users WHERE yandex_driver_id=$1 LIMIT 1", y_id)
                            if row:
                                if y_id not in pending_yandex_ids:
                                    await conn.execute(
                                        "UPDATE users SET full_name=$1, car_model=$2, car_number=$3, balance=$4, yandex_driver_id=$5, updated_at=$6 WHERE id=$7",
                                        norm["full_name"], norm["car_model"], norm["car_number"], bal, y_id, now, row["id"]
                                    )
                            else:
                                new_pos = await db_generate_unique_position()
                                await conn.execute(
                                    "INSERT INTO users (full_name, phone, car_model, car_number, position, balance, yandex_driver_id, is_registered, created_at, updated_at) "
                                    "VALUES ($1,$2,$3,$4,$5,$6,$7,0,$8,$8) ON CONFLICT (position) DO NOTHING",
                                    norm["full_name"], p_clean, norm["car_model"], norm["car_number"], new_pos, bal, y_id, now
                                )
                    else:
                        conn = sqlite3.connect(DB_PATH, timeout=10)
                        conn.row_factory = sqlite3.Row
                        if short9:
                            row = conn.execute("SELECT id FROM users WHERE yandex_driver_id=? OR phone LIKE ? LIMIT 1", (y_id, f"%{short9}")).fetchone()
                        else:
                            row = conn.execute("SELECT id FROM users WHERE yandex_driver_id=? LIMIT 1", (y_id,)).fetchone()
                        if row:
                            if y_id not in pending_yandex_ids:
                                with conn:
                                    conn.execute(
                                        "UPDATE users SET full_name=?, car_model=?, car_number=?, balance=?, yandex_driver_id=?, updated_at=? WHERE id=?",
                                        (norm["full_name"], norm["car_model"], norm["car_number"], bal, y_id, now, row["id"])
                                    )
                        else:
                            new_pos = await db_generate_unique_position()
                            with conn:
                                conn.execute(
                                    "INSERT OR IGNORE INTO users (full_name, phone, car_model, car_number, position, balance, yandex_driver_id, is_registered, created_at, updated_at) "
                                    "VALUES (?,?,?,?,?,?,?,0,?,?)",
                                    (norm["full_name"], p_clean, norm["car_model"], norm["car_number"], new_pos, bal, y_id, now, now)
                                )
                        conn.close()
        except Exception as e:
            logger.error(f"Avtomatik sync xatosi: {e}")


async def monthly_report_scheduler():
    last_report_month = -1
    while True:
        try:
            now = datetime.now(TASHKENT_TZ)
            if now.day == 1 and now.hour == 9 and now.month != last_report_month:
                last_report_month = now.month
                excel_bytes = await generate_monthly_excel_report()
                m_name = UZ_MONTHS.get(now.month, "")
                for adm in ADMIN_IDS:
                    try:
                        file = BufferedInputFile(excel_bytes, filename=f"Lochin_Taxi_{now.year}_{m_name}.xlsx")
                        await bot.send_document(chat_id=adm, document=file, caption=f"🗓 <b>{now.year} {m_name} hisoboti</b>")
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"monthly report xatosi: {e}")
        await asyncio.sleep(3600)


# ============================================================
# WEB SERVER VA ASOSIY RUNNER
# ============================================================

routes = web.RouteTableDef()


@routes.get("/")
@routes.get("/health")
async def health_check(request: web.Request) -> web.Response:
    return web.Response(text="LOCHIN TAXI ENTERPRISE FIXED RUNNING ✅", status=200)


async def start_web_server():
    app = web.Application()
    app.add_routes(routes)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"Web server 0.0.0.0:{PORT} portida ishga tushdi")


async def main() -> None:
    logger.info("Lochin Taxi FIXED ishga tushmoqda...")
    await init_database()
    dp.include_router(admin_router)
    dp.include_router(router)
    await start_web_server()
    asyncio.create_task(yandex_auto_sync_scheduler())
    asyncio.create_task(daily_morning_reminder())
    asyncio.create_task(monthly_report_scheduler())
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info(f"{BOT_NAME} tayyor!")
    try:
        await dp.start_polling(bot)
    finally:
        await yandex_api.close()
        await kapital_bank_api.close()
        if db_pool:
            await db_pool.close()


if __name__ == "__main__":
    asyncio.run(main())
