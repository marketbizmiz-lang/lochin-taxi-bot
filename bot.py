import os
import re
import math
import html
import io
import asyncio
import logging
import sqlite3
from pathlib import Path
from typing import Any, Optional, Dict, List, Set
from datetime import datetime, timezone, timedelta

import aiohttp
import asyncpg
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    KeyboardButton,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    BufferedInputFile,
)

# ============================================================
# KONFIGURATSIYA
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "lochin_taxi.db"
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("lochin_taxi_bot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
PORT = int(os.getenv("PORT", "8080"))
BOT_NAME = os.getenv("BOT_NAME", "LOCHIN TAXI").strip() or "LOCHIN TAXI"

ADMIN_IDS: Set[int] = {8934129079, 8956429378}
env_admins = os.getenv("ADMIN_IDS", "")
if env_admins:
    for adm in env_admins.split(","):
        adm_clean = adm.strip()
        if adm_clean.isdigit():
            ADMIN_IDS.add(int(adm_clean))

MANAGER_TG_ID = int(os.getenv("MANAGER_TG_ID", "8934129079"))
ADMIN_IDS.add(MANAGER_TG_ID)

SUPPORT_PHONE = os.getenv("SUPPORT_PHONE", "+998913773200").strip()
SUPPORT_PHONE_DISPLAY = os.getenv("SUPPORT_PHONE_DISPLAY", "+998 91 377 32 00").strip()
DRIVER_GROUP_LINK = os.getenv("DRIVER_GROUP_LINK", "https://t.me/+vLyCiiXNvB5kMTUy").strip()

# Yandex Fleet API sozlamalari
YANDEX_API_KEY = os.getenv("YANDEX_API_KEY", "").strip()
YANDEX_CLIENT_ID = os.getenv("YANDEX_CLIENT_ID", "").strip()
YANDEX_PARK_ID = os.getenv("YANDEX_PARK_ID", "").strip()
YANDEX_FLEET_URL = "https://fleet-api.yandex.ru/v1"

# Pul yechish qoidalari (20 000 so'm depozit ushlab qolinadi)
MIN_DEPOSIT_BALANCE = int(os.getenv("MIN_DEPOSIT_BALANCE", "20000"))
MIN_WITHDRAWAL = int(os.getenv("MIN_WITHDRAWAL", "10000"))
COMMISSION_PERCENT = float(os.getenv("COMMISSION_PERCENT", "2.0"))


def fmt_sum(val: Any) -> str:
    try:
        if val is None:
            return "0"
        return f"{int(float(val)):,}".replace(",", " ")
    except Exception:
        return "0"


def clean_phone_number(raw_phone: str) -> str:
    digits = re.sub(r"\D", "", str(raw_phone or ""))
    if digits.startswith("8") and len(digits) == 11:
        digits = "998" + digits[1:]
    elif not digits.startswith("998") and len(digits) == 9:
        digits = "998" + digits
    return f"+{digits}"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ============================================================
# DATABASE LAYER (PostgreSQL & SQLite)
# ============================================================

db_pool: Optional[asyncpg.Pool] = None

async def init_database():
    global db_pool
    if DATABASE_URL:
        try:
            clean_url = DATABASE_URL.replace("postgres://", "postgresql://").replace("?sslmode=require", "")
            logger.info("PostgreSQL ga ulanish boshlanmoqda...")
            db_pool = await asyncio.wait_for(
                asyncpg.create_pool(clean_url, ssl="require", min_size=1, max_size=10, command_timeout=10),
                timeout=5.0
            )
            async with db_pool.acquire() as conn:
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id SERIAL PRIMARY KEY,
                        telegram_id BIGINT UNIQUE NOT NULL,
                        username TEXT,
                        full_name TEXT,
                        phone TEXT,
                        card_number TEXT,
                        car_model TEXT,
                        car_number TEXT,
                        position TEXT UNIQUE,
                        language TEXT NOT NULL DEFAULT 'uz',
                        balance NUMERIC DEFAULT 0,
                        blocked_balance NUMERIC DEFAULT 0,
                        is_registered INT DEFAULT 0,
                        is_blocked INT DEFAULT 0,
                        yandex_driver_id TEXT,
                        referrer_id BIGINT,
                        total_orders INT DEFAULT 0,
                        total_earnings NUMERIC DEFAULT 0,
                        last_activity TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS withdrawals (
                        id SERIAL PRIMARY KEY,
                        user_id INT NOT NULL,
                        amount NUMERIC NOT NULL,
                        commission NUMERIC DEFAULT 0,
                        net_amount NUMERIC NOT NULL,
                        card_number TEXT,
                        status TEXT NOT NULL DEFAULT 'pending',
                        payout_method TEXT DEFAULT 'manual',
                        ext_tx_id TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                """)
                try:
                    await conn.execute("ALTER TABLE users ALTER COLUMN telegram_id TYPE BIGINT;")
                    await conn.execute("ALTER TABLE users ALTER COLUMN referrer_id TYPE BIGINT;")
                except Exception:
                    pass
            logger.info("PostgreSQL bazasi muvaffaqiyatli tayyorlandi!")
        except Exception as e:
            logger.warning(f"PostgreSQL ga ulanib bo'lmadi ({e}). SQLite bazasiga o'tilmoqda.")
            db_pool = None

    if not db_pool:
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER UNIQUE NOT NULL,
                    username TEXT,
                    full_name TEXT,
                    phone TEXT,
                    card_number TEXT,
                    car_model TEXT,
                    car_number TEXT,
                    position TEXT UNIQUE,
                    language TEXT NOT NULL DEFAULT 'uz',
                    balance REAL DEFAULT 0,
                    blocked_balance REAL DEFAULT 0,
                    is_registered INTEGER DEFAULT 0,
                    is_blocked INTEGER DEFAULT 0,
                    yandex_driver_id TEXT,
                    referrer_id INTEGER,
                    total_orders INTEGER DEFAULT 0,
                    total_earnings REAL DEFAULT 0,
                    last_activity TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS withdrawals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    amount REAL NOT NULL,
                    commission REAL DEFAULT 0,
                    net_amount REAL NOT NULL,
                    card_number TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    payout_method TEXT DEFAULT 'manual',
                    ext_tx_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.commit()
            conn.close()
            logger.info("SQLite bazasi tayyor!")
        except Exception as e:
            logger.error(f"SQLite yaratishda xatolik: {e}")


async def db_get_user(telegram_id: int) -> Optional[dict]:
    try:
        if db_pool:
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow("SELECT * FROM users WHERE telegram_id = $1", telegram_id)
                return dict(row) if row else None
        else:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
            conn.close()
            return dict(row) if row else None
    except Exception as e:
        logger.error(f"db_get_user xatosi (uid: {telegram_id}): {e}")
        return None


async def db_upsert_start(telegram_id: int, username: str, referrer_id: Optional[int] = None):
    now = utc_now_iso()
    try:
        user = await db_get_user(telegram_id)
        if not user:
            if db_pool:
                async with db_pool.acquire() as conn:
                    await conn.execute("""
                        INSERT INTO users (telegram_id, username, referrer_id, last_activity, created_at, updated_at)
                        VALUES ($1, $2, $3, $4, $5, $6)
                        ON CONFLICT (telegram_id) DO NOTHING
                    """, telegram_id, username or "", referrer_id, now, now, now)
            else:
                conn = sqlite3.connect(DB_PATH)
                conn.execute("""
                    INSERT OR IGNORE INTO users (telegram_id, username, referrer_id, last_activity, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (telegram_id, username or "", referrer_id, now, now, now))
                conn.commit()
                conn.close()
        else:
            if db_pool:
                async with db_pool.acquire() as conn:
                    await conn.execute("UPDATE users SET last_activity = $1, username = $2 WHERE telegram_id = $3", now, username or "", telegram_id)
            else:
                conn = sqlite3.connect(DB_PATH)
                conn.execute("UPDATE users SET last_activity = ?, username = ? WHERE telegram_id = ?", (now, username or "", telegram_id))
                conn.commit()
                conn.close()
    except Exception as e:
        logger.error(f"db_upsert_start xatosi: {e}")


async def db_set_language(telegram_id: int, language: str):
    now = utc_now_iso()
    try:
        if db_pool:
            async with db_pool.acquire() as conn:
                await conn.execute("UPDATE users SET language = $1, updated_at = $2 WHERE telegram_id = $3", language, now, telegram_id)
        else:
            conn = sqlite3.connect(DB_PATH)
            conn.execute("UPDATE users SET language = ?, updated_at = ? WHERE telegram_id = ?", (language, now, telegram_id))
            conn.commit()
            conn.close()
    except Exception as e:
        logger.error(f"db_set_language xatosi: {e}")


async def db_generate_unique_position() -> str:
    import random
    for _ in range(100):
        pos = f"LCH-{random.randint(1000, 9999)}"
        try:
            if db_pool:
                async with db_pool.acquire() as conn:
                    exists = await conn.fetchval("SELECT 1 FROM users WHERE position = $1", pos)
                    if not exists:
                        return pos
            else:
                conn = sqlite3.connect(DB_PATH)
                row = conn.execute("SELECT 1 FROM users WHERE position = ?", (pos,)).fetchone()
                conn.close()
                if not row:
                    return pos
        except Exception:
            return pos
    return f"LCH-{random.randint(10000, 99999)}"


async def db_finish_registration(telegram_id: int, full_name: str, phone: str, card_number: str,
                                 car_model: str, car_number: str, yandex_driver_id: Optional[str]) -> str:
    position = await db_generate_unique_position()
    now = utc_now_iso()
    try:
        if db_pool:
            async with db_pool.acquire() as conn:
                await conn.execute("""
                    UPDATE users SET 
                        full_name = $1, phone = $2, card_number = $3, car_model = $4,
                        car_number = $5, position = $6, yandex_driver_id = $7, is_registered = 1,
                        last_activity = $8, updated_at = $8
                    WHERE telegram_id = $9
                """, full_name, phone, card_number, car_model, car_number, position, yandex_driver_id, now, telegram_id)
        else:
            conn = sqlite3.connect(DB_PATH)
            conn.execute("""
                UPDATE users SET 
                    full_name = ?, phone = ?, card_number = ?, car_model = ?,
                    car_number = ?, position = ?, yandex_driver_id = ?, is_registered = 1,
                    last_activity = ?, updated_at = ?
                WHERE telegram_id = ?
            """, (full_name, phone, card_number, car_model, car_number, position, yandex_driver_id, now, now, telegram_id))
            conn.commit()
            conn.close()
    except Exception as e:
        logger.error(f"db_finish_registration xatosi: {e}")
    return position


async def db_update_balance(telegram_id: int, balance: float):
    now = utc_now_iso()
    try:
        if db_pool:
            async with db_pool.acquire() as conn:
                await conn.execute("UPDATE users SET balance = $1, updated_at = $2 WHERE telegram_id = $3", balance, now, telegram_id)
        else:
            conn = sqlite3.connect(DB_PATH)
            conn.execute("UPDATE users SET balance = ?, updated_at = ? WHERE telegram_id = ?", (balance, now, telegram_id))
            conn.commit()
            conn.close()
    except Exception as e:
        logger.error(f"db_update_balance xatosi: {e}")


async def db_create_withdrawal(user_id: int, amount: float, commission: float, net_amount: float,
                              card_number: str, status: str = "pending", payout_method: str = "manual", ext_tx_id: str = "") -> int:
    now = utc_now_iso()
    try:
        if db_pool:
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow("""
                    INSERT INTO withdrawals (user_id, amount, commission, net_amount, card_number, status, payout_method, ext_tx_id, created_at, updated_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    RETURNING id
                """, user_id, amount, commission, net_amount, card_number, status, payout_method, ext_tx_id, now, now)
                return row["id"] if row else 0
        else:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO withdrawals (user_id, amount, commission, net_amount, card_number, status, payout_method, ext_tx_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (user_id, amount, commission, net_amount, card_number, status, payout_method, ext_tx_id, now, now))
            w_id = cursor.lastrowid
            conn.commit()
            conn.close()
            return w_id or 0
    except Exception as e:
        logger.error(f"db_create_withdrawal xatosi: {e}")
        return 0


async def db_get_withdrawal(w_id: int) -> Optional[dict]:
    try:
        if db_pool:
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT w.*, u.telegram_id, u.full_name, u.phone, u.car_model, u.car_number, u.position, u.yandex_driver_id, u.language, u.balance as user_balance
                    FROM withdrawals w
                    JOIN users u ON w.user_id = u.id
                    WHERE w.id = $1
                """, w_id)
                return dict(row) if row else None
        else:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            row = conn.execute("""
                SELECT w.*, u.telegram_id, u.full_name, u.phone, u.car_model, u.car_number, u.position, u.yandex_driver_id, u.language, u.balance as user_balance
                FROM withdrawals w
                JOIN users u ON w.user_id = u.id
                WHERE w.id = ?
            """, (w_id,)).fetchone()
            conn.close()
            return dict(row) if row else None
    except Exception as e:
        logger.error(f"db_get_withdrawal xatosi: {e}")
        return None


async def db_update_withdrawal_status(w_id: int, status: str):
    now = utc_now_iso()
    try:
        if db_pool:
            async with db_pool.acquire() as conn:
                await conn.execute("UPDATE withdrawals SET status = $1, updated_at = $2 WHERE id = $3", status, now, w_id)
        else:
            conn = sqlite3.connect(DB_PATH)
            conn.execute("UPDATE withdrawals SET status = ?, updated_at = ? WHERE id = ?", (status, now, w_id))
            conn.commit()
            conn.close()
    except Exception as e:
        logger.error(f"db_update_withdrawal_status xatosi: {e}")


async def db_get_all_registered_drivers() -> List[dict]:
    try:
        if db_pool:
            async with db_pool.acquire() as conn:
                rows = await conn.fetch("SELECT * FROM users WHERE is_registered = 1 ORDER BY id ASC")
                return [dict(r) for r in rows]
        else:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM users WHERE is_registered = 1 ORDER BY id ASC").fetchall()
            conn.close()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"db_get_all_registered_drivers xatosi: {e}")
        return []


async def db_get_all_users() -> List[dict]:
    try:
        if db_pool:
            async with db_pool.acquire() as conn:
                rows = await conn.fetch("SELECT * FROM users ORDER BY id ASC")
                return [dict(r) for r in rows]
        else:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM users ORDER BY id ASC").fetchall()
            conn.close()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"db_get_all_users xatosi: {e}")
        return []


async def db_get_stats() -> dict:
    try:
        if db_pool:
            async with db_pool.acquire() as conn:
                total_users = await conn.fetchval("SELECT COUNT(*) FROM users") or 0
                registered_drivers = await conn.fetchval("SELECT COUNT(*) FROM users WHERE is_registered = 1") or 0
                yandex_linked = await conn.fetchval("SELECT COUNT(*) FROM users WHERE yandex_driver_id IS NOT NULL AND yandex_driver_id != ''") or 0
                total_withdrawn = await conn.fetchval("SELECT COALESCE(SUM(amount), 0) FROM withdrawals WHERE status = 'completed'") or 0
                total_comm = await conn.fetchval("SELECT COALESCE(SUM(commission), 0) FROM withdrawals WHERE status = 'completed'") or 0
                return {
                    "total_users": total_users,
                    "registered_drivers": registered_drivers,
                    "yandex_linked": yandex_linked,
                    "total_withdrawn": float(total_withdrawn),
                    "total_comm": float(total_comm)
                }
        else:
            conn = sqlite3.connect(DB_PATH)
            total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            registered_drivers = conn.execute("SELECT COUNT(*) FROM users WHERE is_registered = 1").fetchone()[0]
            yandex_linked = conn.execute("SELECT COUNT(*) FROM users WHERE yandex_driver_id IS NOT NULL AND yandex_driver_id != ''").fetchone()[0]
            total_w = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM withdrawals WHERE status = 'completed'").fetchone()[0]
            total_c = conn.execute("SELECT COALESCE(SUM(commission), 0) FROM withdrawals WHERE status = 'completed'").fetchone()[0]
            conn.close()
            return {
                "total_users": total_users,
                "registered_drivers": registered_drivers,
                "yandex_linked": yandex_linked,
                "total_withdrawn": float(total_w),
                "total_comm": float(total_c)
            }
    except Exception as e:
        logger.error(f"db_get_stats xatosi: {e}")
        return {"total_users": 0, "registered_drivers": 0, "yandex_linked": 0, "total_withdrawn": 0.0, "total_comm": 0.0}


# ============================================================
# YANDEX FLEET API
# ============================================================

class YandexFleetAPI:
    def __init__(self, api_key: str, client_id: str, park_id: str):
        self.api_key = api_key.strip()
        self.client_id = client_id.strip()
        self.park_id = park_id.strip()
        self.base_url = YANDEX_FLEET_URL.rstrip("/")
        self._cached_drivers: List[dict] = []
        self._cache_time: float = 0

    def _get_headers(self, use_default_client_id: bool = False) -> dict:
        cid = f"taxi/park/{self.park_id}" if use_default_client_id or not self.client_id else self.client_id
        return {
            "X-Client-ID": cid,
            "X-API-Key": self.api_key,
            "X-Park-ID": self.park_id,
            "Content-Type": "application/json",
            "Accept-Language": "ru",
        }

    def _is_configured(self) -> bool:
        return bool(self.api_key and self.park_id)

    async def get_all_drivers(self, limit: int = 1000, force_refresh: bool = False) -> tuple[List[dict], str]:
        if not self._is_configured():
            return [], "YANDEX_API_KEY yoki YANDEX_PARK_ID kiritilmagan!"

        import time
        now_t = time.time()
        if not force_refresh and self._cached_drivers and (now_t - self._cache_time < 60):
            return self._cached_drivers, ""

        url = f"{self.base_url}/parks/driver-profiles/list"
        payload = {"query": {"park": {"id": self.park_id}}, "limit": limit}

        # 1-urinish: mavjud client_id bilan
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=self._get_headers(False), json=payload, timeout=25) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        drivers = data.get("driver_profiles", [])
                        self._cached_drivers = drivers
                        self._cache_time = now_t
                        logger.info(f"Yandex Fleet: {len(drivers)} ta haydovchi yuklandi!")
                        return drivers, ""
                    elif resp.status in [400, 401, 403]:
                        # 2-urinish: taxi/park/{park_id} bilan
                        async with session.post(url, headers=self._get_headers(True), json=payload, timeout=25) as resp2:
                            if resp2.status == 200:
                                data = await resp2.json()
                                drivers = data.get("driver_profiles", [])
                                self._cached_drivers = drivers
                                self._cache_time = now_t
                                logger.info(f"Yandex Fleet (fallback): {len(drivers)} ta haydovchi yuklandi!")
                                return drivers, ""
                            err_text = await resp2.text()
                            logger.error(f"Yandex Fleet get_all_drivers status {resp2.status}: {err_text}")
                            return [], f"HTTP {resp2.status}: {err_text[:180]}"
                    else:
                        err_text = await resp.text()
                        logger.error(f"Yandex Fleet get_all_drivers status {resp.status}: {err_text}")
                        return [], f"HTTP {resp.status}: {err_text[:180]}"
        except Exception as e:
            logger.error(f"Yandex get_all_drivers xatosi: {e}")
            return [], str(e)

    async def get_driver_by_phone(self, phone: str) -> Optional[dict]:
        if not self._is_configured():
            return None

        clean_digits = re.sub(r"\D", "", str(phone or ""))
        if len(clean_digits) < 7:
            return None
        core_phone = clean_digits[-9:]

        all_drivers, _ = await self.get_all_drivers()
        logger.info(f"Yandex haydovchi qidiruv (tel): {core_phone}, bazada {len(all_drivers)} ta")

        for drv in all_drivers:
            prof = drv.get("driver_profile", {})
            phones = prof.get("phones", [])
            for p in phones:
                p_digits = re.sub(r"\D", "", str(p))
                if len(p_digits) >= 7 and p_digits[-9:] == core_phone:
                    norm = self._normalize_driver_data(drv)
                    logger.info(f"Yandex haydovchi topildi: {norm['full_name']} ({norm['phone']})")
                    return norm
        return None

    async def get_driver_by_name(self, name: str) -> Optional[dict]:
        if not self._is_configured() or not name:
            return None
        clean_search = name.strip().lower()
        parts = [p for p in re.split(r"[\s\-_,]+", clean_search) if len(p) >= 3]
        if not parts:
            return None

        all_drivers, _ = await self.get_all_drivers()
        logger.info(f"Yandex haydovchi qidiruv (ism): {parts}, bazada {len(all_drivers)} ta")

        for drv in all_drivers:
            norm = self._normalize_driver_data(drv)
            full_n = norm["full_name"].lower()
            if all(p in full_n for p in parts) or any(len(p) >= 4 and p in full_n for p in parts):
                logger.info(f"Yandex haydovchi topildi (ism bo'yicha): {norm['full_name']}")
                return norm
        return None

    def _normalize_driver_data(self, raw_driver: dict) -> dict:
        prof = raw_driver.get("driver_profile", {})
        car = raw_driver.get("car", {})
        accounts = raw_driver.get("accounts", [])

        last_name = prof.get("last_name", "").strip()
        first_name = prof.get("first_name", "").strip()
        middle_name = prof.get("middle_name", "").strip()
        full_name = f"{last_name} {first_name} {middle_name}".strip() or "Haydovchi"

        car_brand = car.get("brand", "").strip()
        car_model = car.get("model", "").strip()
        brand_and_model = car.get("brand_and_model", "").strip()
        car_title = brand_and_model or f"{car_brand} {car_model}".strip() or "Chevrolet Cobalt"
        car_number = car.get("number", "").strip() or car.get("normalized_number", "").strip() or "Nomalum"

        balance = 0.0
        if accounts:
            try:
                balance = float(accounts[0].get("balance", 0.0))
            except Exception:
                balance = 0.0

        phones = prof.get("phones", [])
        primary_phone = phones[0] if phones else ""

        return {
            "id": prof.get("id", ""),
            "full_name": full_name,
            "phone": primary_phone,
            "car_model": car_title,
            "car_number": car_number,
            "balance": balance,
            "raw": raw_driver
        }

    async def get_driver_balance(self, yandex_driver_id: str) -> Optional[float]:
        if not self._is_configured() or not yandex_driver_id:
            return None
        url = f"{self.base_url}/parks/driver-profiles/list"
        payload = {"query": {"park": {"id": self.park_id}, "driver": {"id": [yandex_driver_id]}}, "limit": 1}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=self._get_headers(), json=payload, timeout=12) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        drivers = data.get("driver_profiles", [])
                        if drivers:
                            norm = self._normalize_driver_data(drivers[0])
                            return norm["balance"]
        except Exception as e:
            logger.error(f"Yandex get_driver_balance xatosi: {e}")
        return None

    async def create_transaction(self, yandex_driver_id: str, amount: float, description: str) -> bool:
        if not self._is_configured() or not yandex_driver_id:
            return False
        url = f"{self.base_url}/parks/driver-profiles/transactions"
        category = os.getenv("YANDEX_TX_CATEGORY", "didox out").strip() or "didox out"
        payload = {
            "park_id": self.park_id,
            "driver_profile_id": yandex_driver_id,
            "amount": str(-abs(amount)),
            "category_id": category,
            "description": description
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=self._get_headers(), json=payload, timeout=12) as resp:
                    if resp.status in [200, 201]:
                        return True
                    else:
                        payload["category_id"] = "other"
                        async with session.post(url, headers=self._get_headers(), json=payload, timeout=12) as resp2:
                            return resp2.status in [200, 201]
        except Exception as e:
            logger.error(f"Yandex tranzaksiya xatosi: {e}")
            return False

yandex_api = YandexFleetAPI(YANDEX_API_KEY, YANDEX_CLIENT_ID, YANDEX_PARK_ID)


# ============================================================
# EXCEL HISOBOT GENERATORI (.XLSX)
# ============================================================

async def generate_monthly_excel_report() -> bytes:
    drivers = await db_get_all_registered_drivers()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Lochin Taxi Hisoboti"

    headers = [
        "№", "POSITION", "F.I.O (Haydovchi)", "Telefon Raqam",
        "Avtomobil Rusumi", "Davlat Raqami", "Plastik Karta",
        "Jami Buyurtmalar", "Jami Daromad (som)", "Ushlab qolingan Komissiya (som)",
        "Joriy Balans (som)", "Yandex Driver ID"
    ]

    header_fill = PatternFill(start_color="1F497D", end_color="1F497D", fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    align_center = Alignment(horizontal="center", vertical="center")
    align_left = Alignment(horizontal="left", vertical="center")
    align_right = Alignment(horizontal="right", vertical="center")
    thin_border = Border(
        left=Side(style='thin', color='D9D9D9'), right=Side(style='thin', color='D9D9D9'),
        top=Side(style='thin', color='D9D9D9'), bottom=Side(style='thin', color='D9D9D9')
    )

    ws.append(headers)
    ws.row_dimensions[1].height = 28
    for col_num, _ in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_num)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = align_center

    total_orders_sum = 0
    total_earnings_sum = 0
    total_comm_sum = 0
    total_balance_sum = 0

    for idx, drv in enumerate(drivers, 1):
        orders = int(drv.get("total_orders", 0) or 0)
        bal = float(drv.get("balance", 0.0) or 0.0)
        earnings = float(drv.get("total_earnings", 0.0) or 0.0)
        if earnings <= 0 and bal > 0:
            earnings = bal * 1.15
        comm = earnings * (COMMISSION_PERCENT / 100.0)

        total_orders_sum += orders
        total_earnings_sum += earnings
        total_comm_sum += comm
        total_balance_sum += bal

        name_val = drv.get("full_name") or "Nomalum"
        pos_val = drv.get("position") or "N/A"
        y_val = drv.get("yandex_driver_id") or "Yoq"

        row_data = [
            idx, pos_val, name_val,
            drv.get("phone", ""), drv.get("car_model", ""), drv.get("car_number", ""),
            drv.get("card_number", ""), orders, int(earnings), int(comm), int(bal),
            y_val
        ]
        ws.append(row_data)

        row_idx = idx + 1
        ws.row_dimensions[row_idx].height = 20
        for col_num in range(1, len(headers) + 1):
            cell = ws.cell(row=row_idx, column=col_num)
            cell.border = thin_border
            if col_num in [1, 2, 6]:
                cell.alignment = align_center
            elif col_num in [8, 9, 10, 11]:
                cell.alignment = align_right
                cell.number_format = '#,##0'
            else:
                cell.alignment = align_left

    last_row = len(drivers) + 2
    total_row = [
        "JAMI", "", f"{len(drivers)} ta haydovchi", "", "", "", "",
        total_orders_sum, int(total_earnings_sum), int(total_comm_sum), int(total_balance_sum), ""
    ]
    ws.append(total_row)
    ws.row_dimensions[last_row].height = 24

    for col_num in range(1, len(headers) + 1):
        cell = ws.cell(row=last_row, column=col_num)
        cell.font = Font(name="Calibri", size=11, bold=True)
        cell.fill = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
        cell.border = thin_border
        if col_num in [8, 9, 10, 11]:
            cell.alignment = align_right
            cell.number_format = '#,##0'
        else:
            cell.alignment = align_center

    for col in ws.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        col_letter = openpyxl.utils.get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = max(max_len + 4, 12)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output.getvalue()


# ============================================================
# MATNLAR VA KLAVIATURALAR
# ============================================================

TEXTS = {
    "uz": {
        "welcome": f"🕌 <b>Assalomu alaykum!</b>\n\n🚕 <b>{BOT_NAME}</b> taksoparkiga xush kelibsiz! Biz bilan daromadingizni oshiring! 🤝\n\nTizimdan toliq foydalanish uchun royxatdan oting:",
        "register_btn": "📝 Ro'yxatdan o'tish",
        "reg_name": "👤 <b>Ism va familiyangizni kiriting:</b>\n\n<i>Misol: Abdullaev Ziyovuddin</i>",
        "reg_phone": "📱 <b>Telefon raqamingizni yuboring:</b>\n\nQuyidagi <b>[📱 Telefon raqamni yuborish]</b> tugmasini bosing yoki raqamingizni yozing (Format: <i>+998901234567</i>):",
        "reg_card": "💳 <b>Plastik karta raqamingizni kiriting (16 ta raqam):</b>\n\n<i>Misol: 8600 1234 5678 9012 yoki 9860...</i>",
        "reg_car_model": "🚗 <b>Avtomobilingiz rusumini kiriting:</b>\n\n<i>Misol: Chevrolet Cobalt</i>",
        "reg_car_number": "🔢 <b>Avtomobil davlat raqamini kiriting:</b>\n\n<i>Misol: 01 A 123 AA</i>",
        "reg_success": f"✅ <b>Tabriklaymiz! Siz muvaffaqiyatli royxatdan otdingiz.</b>\n\n🆔 Sizning POSITION ID: <code>{{position}}</code>\n🔑 Bu kod sizning taksoparkdagi shaxsiy kodingiz.",
        "already_reg": "✅ <b>Siz tizimda royxatdan otgansiz!</b>\n\n🆔 POSITION: <code>{position}</code>\n👤 Haydovchi: <b>{name}</b>",
        "menu_balance": "💰 Balans",
        "menu_orders": "📊 Bugungi buyurtmalar",
        "menu_withdraw": "💸 Pul yechish (24/7)",
        "menu_profile": "👤 Profil",
        "menu_top": "🏆 TOP Haydovchilar",
        "menu_group": "📢 Yangiliklar / Guruh",
        "menu_sos": "🆘 Yordam / SOS",
        "menu_admin": "🛠 Admin Panel",
        "cancel": "❌ Bekor qilish",
        "send_phone_btn": "📱 Telefon raqamni yuborish",
        "action_cancelled": "❌ Amaliyot bekor qilindi.",
        "balance_detail": f"💰 <b>{{bot_name}} da Balans va Daromad:</b>\n\n💵 <b>Naqd tushum:</b> 0 som\n💳 <b>Karta tushum (Yandex):</b> <b>{{balance}} som</b>\n🔒 <b>Depozit (Muzlatilgan):</b> <b>{{blocked}} som</b>\n➖➖➖➖➖➖➖➖➖➖\n✅ <b>Kartaga yechish mumkin:</b> <b>{{avail}} som</b>\n\n🚕 Yandex Pro holati: <b>{{y_status}}</b>",
        "withdraw_min_err": f"❌ Minimal yechish summasi: {MIN_WITHDRAWAL:,} som (Balansda 20 000 som depozit qolishi shart)",
        "withdraw_no_money": f"❌ Balansingizda yechish uchun yetarli mablag mavjud emas!\n(Hisobingizda minimal 20 000 som depozit saqlanadi)",
        "withdraw_ask": f"💸 <b>Pul yechish (24/7):</b>\n\n🔹 Yechish mumkin: <b>{{avail}} som</b>\n🔹 Depozitda qoladi: <b>{MIN_DEPOSIT_BALANCE:,} som</b>\n🔹 Komissiya: <b>{{comm}}%</b>\n\nYechmoqchi bolgan summani kiriting (Masalan: <i>50000</i>):",
        "withdraw_confirm": "💳 <b>Pul yechishni tasdiqlaysizmi?</b>\n\n💰 Yechilayotgan summa: <b>{amount} som</b>\n📊 Komissiya ({comm}%): <b>{comm_amount} som</b>\n💵 Kartaga tushadi: <b>{net_amount} som</b>\n💳 Karta: <code>{card}</code>",
        "sos_title": f"🆘 <b>Tezkor Yordam va Aloqa Markazi</b>\n\n📞 <b>Menejer telefoni:</b> {SUPPORT_PHONE_DISPLAY}\n\nKerakli bolimni tanlang:",
        "sos_btn_loc": "📍 Lokatsiya yuborish (DTP / Yolda qoldim)",
        "sos_btn_msg": "✍️ Menejerga xabar / Shikoyat yozish",
        "sos_btn_chat": "💬 Menejer bilan shaxsiy chat",
        "sos_ask_loc": "📍 <b>Lokatsiya yoki joylashuvingizni yuboring:</b>\n\n📱 <b>Mobil telefonda:</b> Pastdagi tugmani bosing.\n💻 <b>Kompyuterda (Desktop):</b> Manzilingizni shu yerga yozib yuboring.\n\n<i>Bekor qilish uchun '❌ Bekor qilish' tugmasini bosing.</i>",
        "sos_loc_btn": "📍 Hozirgi joylashuvimni yuborish",
        "sos_ask_msg": "✍️ <b>Muammo yoki savolingizni yozib yuboring:</b>\n\n<i>Bekor qilish uchun '❌ Bekor qilish' tugmasini bosing.</i>",
        "sos_sent": "🚨 <b>Xabaringiz Bosh Menejerga yetkazildi!</b>",
    },
    "ru": {
        "welcome": f"🕌 <b>Ассаламу алейкум!</b>\n\n🚕 Добро пожаловать в таксопарк <b>{BOT_NAME}</b>! Увеличьте свой доход вместе с нами! 🤝\n\nДля начала работы пройдите регистрацию:",
        "register_btn": "📝 Регистрация",
        "reg_name": "👤 <b>Введите ваше имя и фамилию:</b>\n\n<i>Пример: Абдуллаев Зиёвуддин</i>",
        "reg_phone": "📱 <b>Отправьте ваш номер телефона:</b>\n\nНажмите кнопку <b>[📱 Отправить номер]</b> ниже или введите вручную (Формат: <i>+998901234567</i>):",
        "reg_card": "💳 <b>Введите 16-значный номер карты:</b>\n\n<i>Пример: 8600 1234 5678 9012</i>",
        "reg_car_model": "🚗 <b>Введите марку автомобиля:</b>\n\n<i>Пример: Chevrolet Cobalt</i>",
        "reg_car_number": "🔢 <b>Введите госномер автомобиля:</b>\n\n<i>Пример: 01 A 123 AA</i>",
        "reg_success": f"✅ <b>Поздравляем! Вы успешно зарегистрированы.</b>\n\n🆔 Ваш POSITION ID: <code>{{position}}</code>\n🔑 Это ваш личный идентификатор в таксопарке.",
        "already_reg": "✅ <b>Вы уже зарегистрированы!</b>\n\n🆔 POSITION: <code>{position}</code>\n👤 Водитель: <b>{name}</b>",
        "menu_balance": "💰 Баланс",
        "menu_orders": "📊 Сегодняшние заказы",
        "menu_withdraw": "💸 Вывод средств (24/7)",
        "menu_profile": "👤 Профиль",
        "menu_top": "🏆 ТОП Водителей",
        "menu_group": "📢 Новости / Группа",
        "menu_sos": "🆘 Помощь / SOS",
        "menu_admin": "🛠 Админ Панель",
        "cancel": "❌ Отмена",
        "send_phone_btn": "📱 Отправить номер телефона",
        "action_cancelled": "❌ Действие отменено.",
        "balance_detail": f"💰 <b>Баланс и Доход в {{bot_name}}:</b>\n\n💵 <b>Наличные:</b> 0 сум\n💳 <b>Безналичные (Яндекс):</b> <b>{{balance}} сум</b>\n🔒 <b>Депозит (Неснижаемый):</b> <b>{{blocked}} сум</b>\n➖➖➖➖➖➖➖➖➖➖\n✅ <b>Доступно к выводу:</b> <b>{{avail}} сум</b>\n\n🚕 Статус Яндекс Про: <b>{{y_status}}</b>",
        "withdraw_min_err": f"❌ Минимальная сумма вывода: {MIN_WITHDRAWAL:,} сум (Неснижаемый депозит 20 000 сум)",
        "withdraw_no_money": f"❌ Недостаточно средств для вывода!\n(Неснижаемый остаток депозита: 20 000 сум)",
        "withdraw_ask": f"💸 <b>Вывод средств (24/7):</b>\n\n🔹 Доступно: <b>{{avail}} сум</b>\n🔹 Неснижаемый депозит: <b>{MIN_DEPOSIT_BALANCE:,} сум</b>\n🔹 Комиссия: <b>{{comm}}%</b>\n\nВведите сумму для вывода (Пример: <i>50000</i>):",
        "withdraw_confirm": "💳 <b>Подтверждаете вывод средств?</b>\n\n💰 Сумма: <b>{amount} сум</b>\n📊 Комиссия ({comm}%): <b>{comm_amount} сум</b>\n💵 К зачислению: <b>{net_amount} сум</b>\n💳 Карта: <code>{card}</code>",
        "sos_title": f"🆘 <b>Центр Экстренной Помощи</b>\n\n📞 <b>Телефон менеджера:</b> {SUPPORT_PHONE_DISPLAY}\n\nВыберите нужный раздел:",
        "sos_btn_loc": "📍 Отправить локацию (ДТП / В пути)",
        "sos_btn_msg": "✍️ Написать менеджеру / Жалоба",
        "sos_btn_chat": "💬 Личный чат с менеджером",
        "sos_ask_loc": "📍 <b>Отправьте геопозицию или адрес:</b>\n\n📱 <b>С телефона:</b> Нажмите кнопку ниже.\n💻 <b>С компьютера (Desktop):</b> Напишите адрес в чат.\n\n<i>Для отмены нажмите '❌ Отмена'.</i>",
        "sos_loc_btn": "📍 Отправить мою локацию",
        "sos_ask_msg": "✍️ <b>Опишите вашу проблему или вопрос:</b>\n\n<i>Для отмены нажмите '❌ Отмена'.</i>",
        "sos_sent": "🚨 <b>Сообщение отправлено Главному Менеджеру!</b>",
    }
}

def t(lang_code: str, key: str, **kwargs) -> str:
    text = TEXTS.get(lang_code, TEXTS["uz"]).get(key, key)
    return text.format(**kwargs) if kwargs else text

def user_main_kb(lang: str, uid: int) -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton(text=t(lang, "menu_balance")), KeyboardButton(text=t(lang, "menu_withdraw"))],
        [KeyboardButton(text=t(lang, "menu_orders")), KeyboardButton(text=t(lang, "menu_profile"))],
        [KeyboardButton(text=t(lang, "menu_top")), KeyboardButton(text=t(lang, "menu_group"))],
        [KeyboardButton(text=t(lang, "menu_sos"))],
    ]
    if uid in ADMIN_IDS or uid == MANAGER_TG_ID:
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
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🇺🇿 O'zbekcha", callback_data="lang:uz"),
                InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang:ru")
            ]
        ]
    )

def register_reply_kb(lang: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=t(lang, "register_btn"))]], resize_keyboard=True)

def sos_menu_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t(lang, "sos_btn_loc"), callback_data="sos:loc")],
            [InlineKeyboardButton(text=t(lang, "sos_btn_msg"), callback_data="sos:msg")],
            [InlineKeyboardButton(text=t(lang, "sos_btn_chat"), url=f"tg://user?id={MANAGER_TG_ID}")],
        ]
    )

def admin_main_kb(lang: str) -> ReplyKeyboardMarkup:
    is_uz = lang == "uz"
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Statistika" if is_uz else "📊 Статистика"), KeyboardButton(text="📥 Excel Hisobot" if is_uz else "📥 Excel Отчет")],
            [KeyboardButton(text="🔄 Yandex Sinxronlash" if is_uz else "🔄 Синхронизация Яндекс"), KeyboardButton(text="📢 Xabar tarqatish" if is_uz else "📢 Рассылка")],
            [KeyboardButton(text="👥 Haydovchilar" if is_uz else "👥 Водители")],
            [KeyboardButton(text="⬅️ Asosiy menyu" if is_uz else "⬅️ Главное меню")],
        ],
        resize_keyboard=True
    )


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
    confirm = State()

class SOSStates(StatesGroup):
    waiting_for_location = State()
    waiting_for_message = State()

class AdminBroadcastStates(StatesGroup):
    waiting_for_message = State()


# ============================================================
# ROUTERLAR VA HANDLERLAR
# ============================================================

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
router = Router()
admin_router = Router()

async def get_lang(uid: int) -> str:
    user = await db_get_user(uid)
    return user.get("language", "uz") if user else "uz"


# --- GLOBAL CANCEL & START ---

@router.message(Command("cancel"), StateFilter("*"))
@router.message(F.text.in_(["❌ Bekor qilish", "❌ Отмена", "bekor", "отмена", "/bekor"]), StateFilter("*"))
async def global_cancel_handler(message: Message, state: FSMContext) -> None:
    try:
        await state.clear()
        uid = message.from_user.id
        lang = await get_lang(uid)
        user = await db_get_user(uid)
        if user and user.get("is_registered") == 1:
            await message.answer(t(lang, "action_cancelled"), reply_markup=user_main_kb(lang, uid))
        else:
            await message.answer(t(lang, "action_cancelled"), reply_markup=register_reply_kb(lang))
    except Exception as e:
        logger.error(f"global_cancel_handler xatosi: {e}")


@router.message(CommandStart(), StateFilter("*"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    try:
        await state.clear()
        uid = message.from_user.id
        logger.info(f"Start buyrug'i qabul qilindi: user_id={uid}, username={message.from_user.username}")

        referrer_id = None
        args = (message.text or "").split()
        if len(args) > 1 and args[1].startswith("ref_"):
            ref_raw = args[1].replace("ref_", "")
            if ref_raw.isdigit() and int(ref_raw) != uid:
                referrer_id = int(ref_raw)

        await db_upsert_start(uid, message.from_user.username or "", referrer_id)
        user = await db_get_user(uid)

        if user and user.get("is_registered") == 1:
            lang = user.get("language", "uz")
            drv_name = user.get("full_name") or "Haydovchi"
            pos_id = user.get("position") or "N/A"
            await message.answer(
                t(lang, "already_reg", position=pos_id, name=drv_name),
                reply_markup=user_main_kb(lang, uid)
            )
            return

        await message.answer(
            "🌐 <b>Iltimos, tilni tanlang / Пожалуйста, выберите язык:</b>",
            reply_markup=language_inline_kb()
        )
    except Exception as e:
        logger.error(f"cmd_start xatosi: {e}", exc_info=True)
        try:
            await message.answer(
                "🌐 <b>Iltimos, tilni tanlang / Пожалуйста, выберите язык:</b>",
                reply_markup=language_inline_kb()
            )
        except Exception:
            pass


@router.callback_query(F.data.startswith("lang:"))
async def lang_callback(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        lang = callback.data.split(":")[1]
        uid = callback.from_user.id
        await db_set_language(uid, lang)

        try:
            await callback.message.delete()
        except Exception:
            pass

        user = await db_get_user(uid)
        if user and user.get("is_registered") == 1:
            drv_name = user.get("full_name") or "Haydovchi"
            pos_id = user.get("position") or "N/A"
            await callback.message.answer(
                t(lang, "already_reg", position=pos_id, name=drv_name),
                reply_markup=user_main_kb(lang, uid)
            )
        else:
            await callback.message.answer(t(lang, "welcome"), reply_markup=register_reply_kb(lang))
        await callback.answer()
    except Exception as e:
        logger.error(f"lang_callback xatosi: {e}")


# --- RO'YXATDAN O'TISH ---

@router.message(F.text.in_(["📝 Ro'yxatdan o'tish", "📝 Регистрация"]), StateFilter("*"))
async def reg_start_flow(message: Message, state: FSMContext) -> None:
    try:
        await state.clear()
        uid = message.from_user.id
        user = await db_get_user(uid)
        lang = user.get("language", "uz") if user else "uz"

        if user and user.get("is_registered") == 1:
            drv_name = user.get("full_name") or "Haydovchi"
            pos_id = user.get("position") or "N/A"
            await message.answer(
                t(lang, "already_reg", position=pos_id, name=drv_name),
                reply_markup=user_main_kb(lang, uid)
            )
            return

        await state.set_state(RegStates.phone)
        await message.answer(t(lang, "reg_phone"), reply_markup=phone_request_kb(lang))
    except Exception as e:
        logger.error(f"reg_start_flow xatosi: {e}")


@router.message(RegStates.phone)
async def reg_step_phone(message: Message, state: FSMContext) -> None:
    try:
        uid = message.from_user.id
        lang = await get_lang(uid)
        raw_phone = message.contact.phone_number if message.contact else (message.text or "").strip()
        phone = clean_phone_number(raw_phone)

        if not re.fullmatch(r"\+998\d{9}", phone):
            await message.answer(
                "⚠️ Telefon raqam formati noto'g'ri. Iltimos, qaytadan yozing (Format: <i>+998901234567</i>):",
                reply_markup=phone_request_kb(lang)
            )
            return

        await state.update_data(phone=phone)
        search_msg = await message.answer("⏳ <i>Yandex bazasidan haydovchi tekshirilmoqda...</i>")
        y_driver = await yandex_api.get_driver_by_phone(phone)
        try:
            await search_msg.delete()
        except Exception:
            pass

        if y_driver:
            full_name = y_driver.get("full_name") or "Haydovchi"
            car_model = y_driver.get("car_model") or "Chevrolet Cobalt"
            car_number = y_driver.get("car_number") or "Nomalum"
            y_id = y_driver.get("id")
            y_bal = fmt_sum(y_driver.get("balance", 0))

            await state.update_data(
                full_name=full_name,
                car_model=car_model,
                car_number=car_number,
                yandex_driver_id=y_id
            )

            found_msg = (
                f"✅ <b>Siz Yandex Pro taksoparkimizda topildingiz!</b>\n\n"
                f"👤 F.I.O: <b>{full_name}</b>\n"
                f"🚗 Avtomobil: <b>{car_model} ({car_number})</b>\n"
                f"💰 Yandex Balans: <b>{y_bal} som</b>\n\n"
                f"💳 Daromadingizni yechib olish uchun <b>plastik karta raqamingizni kiriting</b> (16 ta raqam):"
            ) if lang == "uz" else (
                f"✅ <b>Вы найдены в базе Яндекс Про таксопарка!</b>\n\n"
                f"👤 Ф.И.О: <b>{full_name}</b>\n"
                f"🚗 Автомобиль: <b>{car_model} ({car_number})</b>\n"
                f"💰 Баланс Яндекс: <b>{y_bal} сум</b>\n\n"
                f"💳 Для вывода дохода введите <b>номер вашей карты</b> (16 цифр):"
            )
            await message.answer(found_msg, reply_markup=cancel_kb(lang))
            await state.set_state(RegStates.card)
        else:
            await state.set_state(RegStates.name)
            await message.answer(t(lang, "reg_name"), reply_markup=cancel_kb(lang))
    except Exception as e:
        logger.error(f"reg_step_phone xatosi: {e}")


@router.message(RegStates.name)
async def reg_step_name(message: Message, state: FSMContext) -> None:
    try:
        uid = message.from_user.id
        lang = await get_lang(uid)
        name = (message.text or "").strip()
        if len(name) < 3:
            await message.answer("⚠️ Iltimos, ism va familiyangizni toliq kiriting:")
            return

        await state.update_data(full_name=name)
        search_msg = await message.answer("⏳ <i>Yandex bazasidan ism tekshirilmoqda...</i>")
        y_driver = await yandex_api.get_driver_by_name(name)
        try:
            await search_msg.delete()
        except Exception:
            pass

        if y_driver:
            full_name = y_driver.get("full_name") or name
            car_model = y_driver.get("car_model") or "Chevrolet Cobalt"
            car_number = y_driver.get("car_number") or "Nomalum"
            y_id = y_driver.get("id")
            y_bal = fmt_sum(y_driver.get("balance", 0))

            await state.update_data(
                full_name=full_name,
                car_model=car_model,
                car_number=car_number,
                yandex_driver_id=y_id
            )

            found_msg = (
                f"✅ <b>Siz Yandex Pro taksoparkimizda topildingiz!</b>\n\n"
                f"👤 F.I.O: <b>{full_name}</b>\n"
                f"🚗 Avtomobil: <b>{car_model} ({car_number})</b>\n"
                f"💰 Yandex Balans: <b>{y_bal} som</b>\n\n"
                f"💳 Daromadingizni yechib olish uchun <b>plastik karta raqamingizni kiriting</b> (16 ta raqam):"
            ) if lang == "uz" else (
                f"✅ <b>Вы найдены в базе Яндекс Про таксопарка!</b>\n\n"
                f"👤 Ф.И.О: <b>{full_name}</b>\n"
                f"🚗 Автомобиль: <b>{car_model} ({car_number})</b>\n"
                f"💰 Баланс Яндекс: <b>{y_bal} сум</b>\n\n"
                f"💳 Для вывода дохода введите <b>номер вашей карты</b> (16 цифр):"
            )
            await message.answer(found_msg, reply_markup=cancel_kb(lang))
            await state.set_state(RegStates.card)
        else:
            await state.set_state(RegStates.card)
            await message.answer(t(lang, "reg_card"), reply_markup=cancel_kb(lang))
    except Exception as e:
        logger.error(f"reg_step_name xatosi: {e}")


@router.message(RegStates.card)
async def reg_step_card(message: Message, state: FSMContext) -> None:
    try:
        uid = message.from_user.id
        lang = await get_lang(uid)
        card = (message.text or "").strip().replace(" ", "").replace("-", "")
        if not (card.isdigit() and len(card) == 16):
            await message.answer("⚠️ Plastik karta 16 ta raqamdan iborat bolishi kerak. Qaytadan kiriting:")
            return

        await state.update_data(card_number=card)
        data = await state.get_data()
        if data.get("yandex_driver_id"):
            await finish_registration_process(message, state, data)
        else:
            await state.set_state(RegStates.car_model)
            await message.answer(t(lang, "reg_car_model"), reply_markup=cancel_kb(lang))
    except Exception as e:
        logger.error(f"reg_step_card xatosi: {e}")


@router.message(RegStates.car_model)
async def reg_step_car_model(message: Message, state: FSMContext) -> None:
    try:
        car_model = (message.text or "").strip()
        await state.update_data(car_model=car_model)
        lang = await get_lang(message.from_user.id)
        await state.set_state(RegStates.car_number)
        await message.answer(t(lang, "reg_car_number"), reply_markup=cancel_kb(lang))
    except Exception as e:
        logger.error(f"reg_step_car_model xatosi: {e}")


@router.message(RegStates.car_number)
async def reg_step_car_number(message: Message, state: FSMContext) -> None:
    try:
        car_number = (message.text or "").strip().upper()
        await state.update_data(car_number=car_number)
        data = await state.get_data()
        await finish_registration_process(message, state, data)
    except Exception as e:
        logger.error(f"reg_step_car_number xatosi: {e}")


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
    yandex_status_text = "Ulangan ✅" if y_id else "Ulanmagan ❌"

    position = await db_finish_registration(
        telegram_id=uid,
        full_name=full_name,
        phone=phone,
        card_number=card,
        car_model=car_model,
        car_number=car_number,
        yandex_driver_id=y_id
    )

    await message.answer(t(lang, "reg_success", position=position), reply_markup=user_main_kb(lang, uid))

    for adm in ADMIN_IDS:
        try:
            await bot.send_message(
                adm,
                f"🆕 <b>Yangi haydovchi royxatdan otdi!</b>\n\n"
                f"👤 Ism: <b>{full_name}</b>\n"
                f"📱 Telefon: <code>{phone}</code>\n"
                f"🚗 Mashina: <b>{car_model} ({car_number})</b>\n"
                f"💳 Karta: <code>{card}</code>\n"
                f"🆔 POSITION: <code>{position}</code>\n"
                f"🚕 Yandex: <b>{yandex_status_text}</b>"
            )
        except Exception as e:
            logger.error(f"Admin {adm} ga yangi haydovchi xabarini yuborishda xato: {e}")


# --- BALANS VA BUYURTMALAR ---

@router.message(F.text.in_(["💰 Balans", "💰 Баланс"]))
async def balance_handler(message: Message) -> None:
    try:
        uid = message.from_user.id
        user = await db_get_user(uid)
        if not user or user.get("is_registered") != 1:
            await message.answer("Iltimos, avval royxatdan oting: /start", reply_markup=register_reply_kb("uz"))
            return

        lang = user.get("language", "uz")
        cur_bal = float(user.get("balance", 0.0) or 0.0)
        y_status = "Ulangan ✅" if user.get("yandex_driver_id") else "Ulanmagan ❌"

        if user.get("yandex_driver_id"):
            live_bal = await yandex_api.get_driver_balance(user["yandex_driver_id"])
            if live_bal is not None:
                cur_bal = live_bal
                await db_update_balance(uid, live_bal)

        # 20 000 so'm depozit ushlab qolinadi
        avail = max(0.0, cur_bal - MIN_DEPOSIT_BALANCE)
        await message.answer(
            t(lang, "balance_detail",
              bot_name=BOT_NAME,
              balance=fmt_sum(cur_bal),
              blocked=fmt_sum(MIN_DEPOSIT_BALANCE),
              avail=fmt_sum(avail),
              y_status=y_status),
            reply_markup=user_main_kb(lang, uid)
        )
    except Exception as e:
        logger.error(f"balance_handler xatosi: {e}")


@router.message(F.text.in_(["📊 Bugungi buyurtmalar", "📊 Сегодняшние заказы"]))
async def orders_handler(message: Message) -> None:
    try:
        lang = await get_lang(message.from_user.id)
        text = (
            "📊 <b>Bugungi buyurtmalar va daromad:</b>\n\n"
            "🚕 Barcha naqd va karta orqali bajargan safarlaringiz <b>Yandex Pro</b> ilovasida hisoblanadi.\n"
            "💳 Kartadan to'langan daromadlar va komissiyalar to'g'ridan-to'g'ri bot balansingizda aks etadi va ularni 20 000 so'm depozit qoldig'i saqlangan holda istalgan payt kartangizga yechib olishingiz mumkin."
        ) if lang == "uz" else (
            "📊 <b>Сегодняшние заказы и доход:</b>\n\n"
            "🚕 Все поездки за наличные и безналичные рассчитываются в приложении <b>Яндекс Про</b>.\n"
            "💳 Безналичный доход отображается на балансе бота, и вы можете вывести его на свою карту в любое время (с сохранением 20 000 сум депозита)."
        )
        await message.answer(text, reply_markup=user_main_kb(lang, message.from_user.id))
    except Exception as e:
        logger.error(f"orders_handler xatosi: {e}")


@router.message(F.text.in_(["🏆 TOP Haydovchilar", "🏆 ТОП Водителей"]))
async def top_drivers_handler(message: Message) -> None:
    try:
        uid = message.from_user.id
        lang = await get_lang(uid)
        drivers = await db_get_all_registered_drivers()
        drivers_sorted = sorted(drivers, key=lambda x: int(x.get("total_orders", 0) or 0), reverse=True)[:10]

        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        if lang == "uz":
            text = f"🏆 <b>{BOT_NAME} — Haftaning Eng Faol Haydovchilari:</b>\n\n"
            if drivers_sorted and any(int(d.get("total_orders", 0) or 0) > 0 for d in drivers_sorted):
                for idx, drv in enumerate(drivers_sorted):
                    medal = medals[idx] if idx < len(medals) else f"{idx+1}."
                    h_name = drv.get("full_name") or "Haydovchi"
                    h_pos = drv.get("position") or "N/A"
                    h_orders = drv.get("total_orders", 0)
                    text += f"{medal} <b>{h_name}</b> (<code>{h_pos}</code>) — <b>{h_orders} ta zakaz</b>\n"
            else:
                text += "<i>Hozircha haftalik reyting shakllanmoqda...</i>\n"
            text += "\n🔥 <i>Koproq buyurtma bajaring va haftalik maxsus bonuslarga ega boling!</i>"
        else:
            text = f"🏆 <b>{BOT_NAME} — ТОП Водителей Недели:</b>\n\n"
            if drivers_sorted and any(int(d.get("total_orders", 0) or 0) > 0 for d in drivers_sorted):
                for idx, drv in enumerate(drivers_sorted):
                    medal = medals[idx] if idx < len(medals) else f"{idx+1}."
                    h_name = drv.get("full_name") or "Водитель"
                    h_pos = drv.get("position") or "N/A"
                    h_orders = drv.get("total_orders", 0)
                    text += f"{medal} <b>{h_name}</b> (<code>{h_pos}</code>) — <b>{h_orders} заказов</b>\n"
            else:
                text += "<i>Рейтинг недели формируется...</i>\n"
            text += "\n🔥 <i>Выполняйте больше поездок и получайте еженедельные бонусы!</i>"

        await message.answer(text, reply_markup=user_main_kb(lang, uid))
    except Exception as e:
        logger.error(f"top_drivers_handler xatosi: {e}")


@router.message(F.text.in_(["👤 Profil", "👤 Профиль"]))
async def profile_handler(message: Message) -> None:
    try:
        uid = message.from_user.id
        user = await db_get_user(uid)
        if not user or user.get("is_registered") != 1:
            await message.answer("Iltimos, avval royxatdan oting: /start", reply_markup=register_reply_kb("uz"))
            return

        lang = user.get("language", "uz")
        pos_val = user.get("position") or "N/A"
        name_val = user.get("full_name") or "Haydovchi"
        phone_val = user.get("phone") or ""
        car_m_val = user.get("car_model") or ""
        car_n_val = user.get("car_number") or ""
        card_val = user.get("card_number") or ""
        y_val = "Ulangan ✅" if user.get("yandex_driver_id") else "Ulanmagan ❌"

        if lang == "uz":
            text = (
                f"👤 <b>Haydovchi Profili:</b>\n\n"
                f"🆔 POSITION: <code>{pos_val}</code>\n"
                f"👤 Ism: <b>{name_val}</b>\n"
                f"📱 Telefon: <b>{phone_val}</b>\n"
                f"🚗 Avtomobil: <b>{car_m_val} ({car_n_val})</b>\n"
                f"💳 Karta: <code>{card_val}</code>\n"
                f"🚕 Yandex: <b>{y_val}</b>\n"
                f"🌐 Til: <b>Ozbekcha</b>"
            )
            change_lang_btn = "🌐 Tilni ozgartirish"
        else:
            text = (
                f"👤 <b>Профиль Водителя:</b>\n\n"
                f"🆔 POSITION: <code>{pos_val}</code>\n"
                f"👤 Имя: <b>{name_val}</b>\n"
                f"📱 Телефон: <b>{phone_val}</b>\n"
                f"🚗 Автомобиль: <b>{car_m_val} ({car_n_val})</b>\n"
                f"💳 Карта: <code>{card_val}</code>\n"
                f"🚕 Яндекс: <b>{y_val}</b>\n"
                f"🌐 Язык: <b>Русский</b>"
            )
            change_lang_btn = "🌐 Сменить язык"

        inline_kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=change_lang_btn, callback_data="change_lang_menu")]]
        )
        await message.answer(text, reply_markup=inline_kb)
    except Exception as e:
        logger.error(f"profile_handler xatosi: {e}")


@router.callback_query(F.data == "change_lang_menu")
async def change_lang_menu_cb(callback: CallbackQuery) -> None:
    try:
        await callback.message.edit_text("🌐 Tilni tanlang / Выберите язык:", reply_markup=language_inline_kb())
        await callback.answer()
    except Exception as e:
        logger.error(f"change_lang_menu_cb xatosi: {e}")


@router.message(F.text.in_(["📢 Yangiliklar / Guruh", "📢 Новости / Группа"]))
async def group_handler(message: Message) -> None:
    try:
        lang = await get_lang(message.from_user.id)
        btn_text = "💬 Haydovchilar guruhiga qoshilish" if lang == "uz" else "💬 Вступить в группу водителей"
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=btn_text, url=DRIVER_GROUP_LINK)]])
        await message.answer(f"📢 <b>{BOT_NAME} Rasmiy Guruhimiz:</b>", reply_markup=kb)
    except Exception as e:
        logger.error(f"group_handler xatosi: {e}")


# --- PUL YECHISH (20 000 SO'M DEPOZIT BILAN) ---

@router.message(F.text.in_(["💸 Pul yechish (24/7)", "💸 Вывод средств (24/7)"]), StateFilter("*"))
async def withdraw_start(message: Message, state: FSMContext) -> None:
    try:
        await state.clear()
        uid = message.from_user.id
        user = await db_get_user(uid)
        if not user or user.get("is_registered") != 1:
            await message.answer("Iltimos, avval royxatdan oting: /start", reply_markup=register_reply_kb("uz"))
            return

        lang = user.get("language", "uz")
        cur_bal = float(user.get("balance", 0.0) or 0.0)

        if user.get("yandex_driver_id"):
            live_bal = await yandex_api.get_driver_balance(user["yandex_driver_id"])
            if live_bal is not None:
                await db_update_balance(uid, live_bal)
                cur_bal = live_bal
                user["balance"] = live_bal

        avail = max(0.0, cur_bal - MIN_DEPOSIT_BALANCE)

        if avail < MIN_WITHDRAWAL:
            msg = (
                f"❌ <b>Balansingizda yechish uchun yetarli mablag' mavjud emas!</b>\n\n"
                f"💰 Umumiy balans: <b>{fmt_sum(cur_bal)} som</b>\n"
                f"🔒 Depozit (ushlab qolinadi): <b>{fmt_sum(MIN_DEPOSIT_BALANCE)} som</b>\n"
                f"🔹 Yechish mumkin: <b>{fmt_sum(avail)} som</b>\n"
                f"🔹 Minimal yechish: <b>{fmt_sum(MIN_WITHDRAWAL)} som</b>"
            ) if lang == "uz" else (
                f"❌ <b>Недостаточно средств для вывода!</b>\n\n"
                f"💰 Общий баланс: <b>{fmt_sum(cur_bal)} сум</b>\n"
                f"🔒 Депозит (неснижаемый): <b>{fmt_sum(MIN_DEPOSIT_BALANCE)} сум</b>\n"
                f"🔹 Доступно к выводу: <b>{fmt_sum(avail)} сум</b>\n"
                f"🔹 Мин. сумма: <b>{fmt_sum(MIN_WITHDRAWAL)} сум</b>"
            )
            await message.answer(msg, reply_markup=user_main_kb(lang, uid))
            return

        await state.set_state(WithdrawStates.amount)
        await message.answer(
            t(lang, "withdraw_ask",
              avail=fmt_sum(avail),
              min_w=fmt_sum(MIN_WITHDRAWAL),
              comm=COMMISSION_PERCENT),
            reply_markup=cancel_kb(lang)
        )
    except Exception as e:
        logger.error(f"withdraw_start xatosi: {e}")


@router.message(WithdrawStates.amount)
async def withdraw_amount_step(message: Message, state: FSMContext) -> None:
    try:
        uid = message.from_user.id
        lang = await get_lang(uid)
        raw = (message.text or "").replace(" ", "").replace("so'm", "").replace("som", "").replace("сум", "").strip()

        if not raw.isdigit():
            await message.answer("⚠️ Iltimos, summani faqat musbat raqamlarda kiriting (Masalan: <i>50000</i>):")
            return

        amount = float(raw)
        user = await db_get_user(uid)
        cur_bal = float(user.get("balance", 0.0) or 0.0)
        avail = max(0.0, cur_bal - MIN_DEPOSIT_BALANCE)

        if amount < MIN_WITHDRAWAL:
            await message.answer(t(lang, "withdraw_min_err", min_w=fmt_sum(MIN_WITHDRAWAL)))
            return

        if amount > avail:
            await message.answer(
                f"❌ Mablag yetarli emas!\n\n"
                f"Depozitdan (20 000 som) tashqari siz eng kopi bilan <b>{fmt_sum(avail)} som</b> yecha olasiz."
            )
            return

        comm = amount * (COMMISSION_PERCENT / 100.0)
        net = amount - comm
        card_val = user.get("card_number") or ""

        await state.update_data(amount=amount, commission=comm, net_amount=net, card=card_val)
        await state.set_state(WithdrawStates.confirm)

        btn_text = "⚡️ Pul yechishni tasdiqlash" if lang == "uz" else "⚡️ Подтвердить вывод"
        btn_cancel = "❌ Bekor qilish" if lang == "uz" else "❌ Отмена"

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text=btn_text, callback_data="wd_go:auto"),
                    InlineKeyboardButton(text=btn_cancel, callback_data="wd_go:no")
                ]
            ]
        )

        await message.answer(
            t(lang, "withdraw_confirm",
              amount=fmt_sum(amount),
              comm=COMMISSION_PERCENT,
              comm_amount=fmt_sum(comm),
              net_amount=fmt_sum(net),
              card=card_val),
            reply_markup=kb
        )
    except Exception as e:
        logger.error(f"withdraw_amount_step xatosi: {e}")


@router.callback_query(F.data.startswith("wd_go:"), WithdrawStates.confirm)
async def withdraw_process_callback(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        uid = callback.from_user.id
        action = callback.data.split(":")[1]
        lang = await get_lang(uid)

        if action == "no":
            await state.clear()
            await callback.message.edit_text("❌ Pul yechish bekor qilindi." if lang == "uz" else "❌ Вывод средств отменен.")
            await callback.answer()
            return

        data = await state.get_data()
        amount = data["amount"]
        commission = data["commission"]
        net_amount = data["net_amount"]
        card = data["card"]
        await state.clear()

        user = await db_get_user(uid)
        cur_bal = float(user.get("balance", 0.0) or 0.0)
        remaining = cur_bal - amount

        w_id = await db_create_withdrawal(
            user_id=user["id"],
            amount=amount,
            commission=commission,
            net_amount=net_amount,
            card_number=card,
            status="pending",
            payout_method="manual",
            ext_tx_id=""
        )

        msg = (
            f"✅ <b>Pul yechish arizangiz ma'muriyatga yuborildi!</b>\n\n"
            f"💰 Yechilayotgan summa: <b>{fmt_sum(amount)} som</b>\n"
            f"💵 Kartaga tushadi: <b>{fmt_sum(net_amount)} som</b>\n"
            f"💳 Karta: <code>{card}</code>\n"
            f"🔒 Depozitda qoladigan: <b>{fmt_sum(remaining)} som</b>\n\n"
            f"<i>Mablag' tez orada kartangizga o'tkazib beriladi.</i>"
        ) if lang == "uz" else (
            f"✅ <b>Заявка на вывод средств отправлена администрации!</b>\n\n"
            f"💰 Сумма вывода: <b>{fmt_sum(amount)} сум</b>\n"
            f"💵 К зачислению: <b>{fmt_sum(net_amount)} сум</b>\n"
            f"💳 Карта: <code>{card}</code>\n"
            f"🔒 Остаток депозита: <b>{fmt_sum(remaining)} сум</b>\n\n"
            f"<i>Средства скоро поступят на вашу карту.</i>"
        )

        await callback.message.edit_text(msg)
        await callback.answer()

        drv_name = user.get("full_name") or "Haydovchi"
        drv_pos = user.get("position") or "N/A"
        drv_phone = user.get("phone") or ""
        drv_car_m = user.get("car_model") or ""
        drv_car_n = user.get("car_number") or ""
        drv_yandex = "Ulangan ✅" if user.get("yandex_driver_id") else "Ulanmagan ❌"

        # Admin uchun qulay boshqaruv tugmalari
        adm_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ To'landi (Yandexdan yechish)", callback_data=f"adm_wd:pay:{w_id}"),
                    InlineKeyboardButton(text="❌ Rad etish", callback_data=f"adm_wd:rej:{w_id}")
                ],
                [
                    InlineKeyboardButton(text="💬 Haydovchi bilan chat", url=f"tg://user?id={uid}")
                ]
            ]
        )

        admin_alert = (
            f"💸 <b>YANGI PUL YECHISH ARIZASI!</b> (Ariza #{w_id})\n\n"
            f"🆔 <b>POSITION:</b> <code>{drv_pos}</code>\n"
            f"👤 <b>Haydovchi:</b> <b>{drv_name}</b>\n"
            f"📱 <b>Telefon:</b> <code>{drv_phone}</code>\n"
            f"🚗 <b>Avtomobil:</b> <b>{drv_car_m} ({drv_car_n})</b>\n"
            f"💳 <b>Karta:</b> <code>{card}</code> (Nusxa olish uchun bosing)\n"
            f"➖➖➖➖➖➖➖➖➖➖\n"
            f"💰 <b>Yechilayotgan summa:</b> <b>{fmt_sum(amount)} som</b>\n"
            f"📊 <b>Komissiya ({COMMISSION_PERCENT}%):</b> <b>{fmt_sum(commission)} som</b>\n"
            f"💵 <b>Kartaga to'lanadigan sof summa:</b> <b>{fmt_sum(net_amount)} som</b>\n"
            f"🔒 <b>Depozitda qoladigan:</b> <b>{fmt_sum(remaining)} som</b> (Min. depozit: 20 000 som)\n"
            f"🚕 <b>Yandex Pro:</b> {drv_yandex}"
        )

        for adm in ADMIN_IDS:
            try:
                await bot.send_message(adm, admin_alert, reply_markup=adm_kb)
            except Exception as e:
                logger.error(f"Admin {adm} ga pul yechish arizasini yuborishda xato: {e}")
    except Exception as e:
        logger.error(f"withdraw_process_callback xatosi: {e}")


# --- ADMIN PUL YECHISH TASDIQLASH / RAD ETISH ---

@admin_router.callback_query(F.data.startswith("adm_wd:"))
async def admin_handle_withdrawal_decision(callback: CallbackQuery) -> None:
    try:
        _, action, w_id_str = callback.data.split(":")
        w_id = int(w_id_str)
        w_data = await db_get_withdrawal(w_id)

        if not w_data:
            await callback.answer("❌ Ariza topilmadi!", show_alert=True)
            return

        if w_data.get("status") in ["completed", "rejected"]:
            await callback.answer(f"Ushbu ariza allaqachon '{w_data.get('status')}' holatida!", show_alert=True)
            return

        drv_tg_id = w_data["telegram_id"]
        drv_name = w_data["full_name"]
        amount = float(w_data["amount"])
        net_amount = float(w_data["net_amount"])
        card = w_data["card_number"]
        y_id = w_data.get("yandex_driver_id")
        drv_lang = w_data.get("language", "uz")

        if action == "pay":
            y_done = False
            if y_id:
                y_done = await yandex_api.create_transaction(y_id, amount, f"Lochin Taxi tolov #{w_id}")

            await db_update_withdrawal_status(w_id, "completed")
            y_note = " (Yandex hisobidan ham yechildi ✅)" if y_done else ""

            await callback.message.edit_text(
                f"{callback.message.text}\n\n➖➖➖➖➖➖➖➖➖➖\n"
                f"✅ <b>TO'LANDI:</b> Admin tomonidan kartaga to'lab berildi va yakunlandi!{y_note}"
            )
            await callback.answer("✅ To'lov tasdiqlandi!")

            # Haydovchiga xushxabar
            try:
                msg_for_drv = (
                    f"✅ <b>Mablag' kartangizga o'tkazib berildi!</b>\n\n"
                    f"💰 Yechilgan: <b>{fmt_sum(amount)} som</b>\n"
                    f"💵 Kartangizga tushdi: <b>{fmt_sum(net_amount)} som</b>\n"
                    f"💳 Karta: <code>{card}</code>\n\n"
                    f"<i>{BOT_NAME} bilan ishlaganingiz uchun rahmat! 🤝</i>"
                ) if drv_lang == "uz" else (
                    f"✅ <b>Средства переведены на вашу карту!</b>\n\n"
                    f"💰 Сумма: <b>{fmt_sum(amount)} сум</b>\n"
                    f"💵 Зачислено: <b>{fmt_sum(net_amount)} сум</b>\n"
                    f"💳 Карта: <code>{card}</code>\n\n"
                    f"<i>Спасибо за сотрудничество с {BOT_NAME}! 🤝</i>"
                )
                await bot.send_message(drv_tg_id, msg_for_drv)
            except Exception:
                pass

        elif action == "rej":
            await db_update_withdrawal_status(w_id, "rejected")
            await callback.message.edit_text(
                f"{callback.message.text}\n\n➖➖➖➖➖➖➖➖➖➖\n"
                f"❌ <b>RAD ETILDI:</b> Ariza ma'muriyat tomonidan bekor qilindi."
            )
            await callback.answer("❌ Ariza rad etildi!")

            # Haydovchiga xabar
            try:
                msg_for_drv = (
                    f"❌ <b>Pul yechish arizangiz ma'muriyat tomonidan rad etildi.</b>\n\n"
                    f"Savollar bo'lsa, dispetcher bilan bog'laning: {SUPPORT_PHONE_DISPLAY}"
                ) if drv_lang == "uz" else (
                    f"❌ <b>Ваша заявка на вывод средств отклонена администрацией.</b>\n\n"
                    f"По вопросам обращайтесь к диспетчеру: {SUPPORT_PHONE_DISPLAY}"
                )
                await bot.send_message(drv_tg_id, msg_for_drv)
            except Exception:
                pass
    except Exception as e:
        logger.error(f"admin_handle_withdrawal_decision xatosi: {e}")


# --- SOS / YORDAM ---

@router.message(F.text.in_(["🆘 Yordam / SOS", "🆘 Помощь / SOS"]), StateFilter("*"))
async def sos_handler(message: Message, state: FSMContext) -> None:
    try:
        await state.clear()
        lang = await get_lang(message.from_user.id)
        await message.answer(t(lang, "sos_title"), reply_markup=sos_menu_kb(lang))
    except Exception as e:
        logger.error(f"sos_handler xatosi: {e}")


@router.callback_query(F.data == "sos:loc")
async def sos_location_flow(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        lang = await get_lang(callback.from_user.id)
        await state.set_state(SOSStates.waiting_for_location)
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer(t(lang, "sos_ask_loc"), reply_markup=location_request_kb(lang))
        await callback.answer()
    except Exception as e:
        logger.error(f"sos_location_flow xatosi: {e}")


@router.message(SOSStates.waiting_for_location, F.location)
async def sos_receive_location_geo(message: Message, state: FSMContext) -> None:
    try:
        uid = message.from_user.id
        lang = await get_lang(uid)
        await state.clear()
        user = await db_get_user(uid) or {}

        lat = message.location.latitude
        lon = message.location.longitude
        maps_url = f"https://maps.google.com/?q={lat},{lon}"

        drv_name = user.get("full_name") or message.from_user.full_name or "Haydovchi"
        drv_pos = user.get("position") or "Ro'yxatdan o'tmagan"
        drv_phone = user.get("phone") or "Kiritilmagan"
        
        car_m = user.get("car_model") or ""
        car_n = user.get("car_number") or ""
        drv_car = f"{car_m} ({car_n})".strip() if (car_m or car_n) else "Kiritilmagan"
        
        drv_card = user.get("card_number") or "Yo'q"
        drv_yandex = "Ulangan ✅" if user.get("yandex_driver_id") else "Ulanmagan ❌"
        
        tg_username = f"@{message.from_user.username}" if message.from_user.username else (f"@{user.get('username')}" if user.get("username") else "Mavjud emas")

        alert = (
            f"🚨 <b>DIQQAT: HAYDOVCHIDAN SOS / LOKATSIYA!</b>\n\n"
            f"🆔 <b>POSITION (Pozivnoy):</b> <code>{drv_pos}</code>\n"
            f"👤 <b>F.I.O / Ism:</b> <b>{drv_name}</b>\n"
            f"📱 <b>Telefon:</b> <code>{drv_phone}</code>\n"
            f"🚗 <b>Avtomobil:</b> <b>{drv_car}</b>\n"
            f"💳 <b>Karta:</b> <code>{drv_card}</code>\n"
            f"🚕 <b>Yandex Pro:</b> {drv_yandex}\n"
            f"➖➖➖➖➖➖➖➖➖➖\n"
            f"🌐 <b>Telegram Username:</b> {tg_username}\n"
            f"🆔 <b>Telegram ID:</b> <code>{uid}</code>\n\n"
            f"📍 <a href='{maps_url}'>🗺 Google Xaritada ochish</a>"
        )

        chat_url = f"https://t.me/{message.from_user.username}" if message.from_user.username else f"tg://user?id={uid}"
        adm_kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="💬 Haydovchi bilan chat", url=chat_url)]]
        )

        for adm in ADMIN_IDS:
            try:
                await bot.send_message(adm, alert, reply_markup=adm_kb)
                await bot.send_location(adm, latitude=lat, longitude=lon)
            except Exception as e:
                logger.error(f"Admin {adm} ga SOS lokatsiyasini yuborishda xato: {e}")

        await message.answer(t(lang, "sos_sent"), reply_markup=user_main_kb(lang, uid))
    except Exception as e:
        logger.error(f"sos_receive_location_geo xatosi: {e}")


@router.message(SOSStates.waiting_for_location, F.text)
async def sos_receive_location_text(message: Message, state: FSMContext) -> None:
    try:
        uid = message.from_user.id
        lang = await get_lang(uid)

        if message.text in ["❌ Bekor qilish", "❌ Отмена"]:
            await state.clear()
            await message.answer(t(lang, "action_cancelled"), reply_markup=user_main_kb(lang, uid))
            return

        await state.clear()
        user = await db_get_user(uid) or {}
        address_text = message.text.strip()

        drv_name = user.get("full_name") or message.from_user.full_name or "Haydovchi"
        drv_pos = user.get("position") or "Ro'yxatdan o'tmagan"
        drv_phone = user.get("phone") or "Kiritilmagan"
        
        car_m = user.get("car_model") or ""
        car_n = user.get("car_number") or ""
        drv_car = f"{car_m} ({car_n})".strip() if (car_m or car_n) else "Kiritilmagan"
        
        drv_card = user.get("card_number") or "Yo'q"
        drv_yandex = "Ulangan ✅" if user.get("yandex_driver_id") else "Ulanmagan ❌"
        
        tg_username = f"@{message.from_user.username}" if message.from_user.username else (f"@{user.get('username')}" if user.get("username") else "Mavjud emas")

        alert = (
            f"🚨 <b>DIQQAT: HAYDOVCHIDAN SOS / MANZIL (DESKTOP):</b>\n\n"
            f"🆔 <b>POSITION (Pozivnoy):</b> <code>{drv_pos}</code>\n"
            f"👤 <b>F.I.O / Ism:</b> <b>{drv_name}</b>\n"
            f"📱 <b>Telefon:</b> <code>{drv_phone}</code>\n"
            f"🚗 <b>Avtomobil:</b> <b>{drv_car}</b>\n"
            f"💳 <b>Karta:</b> <code>{drv_card}</code>\n"
            f"🚕 <b>Yandex Pro:</b> {drv_yandex}\n"
            f"➖➖➖➖➖➖➖➖➖➖\n"
            f"🌐 <b>Telegram Username:</b> {tg_username}\n"
            f"🆔 <b>Telegram ID:</b> <code>{uid}</code>\n\n"
            f"📍 <b>Manzil / Holat:</b>\n{address_text}"
        )

        chat_url = f"https://t.me/{message.from_user.username}" if message.from_user.username else f"tg://user?id={uid}"
        adm_kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="💬 Haydovchi bilan chat", url=chat_url)]]
        )

        for adm in ADMIN_IDS:
            try:
                await bot.send_message(adm, alert, reply_markup=adm_kb)
            except Exception as e:
                logger.error(f"Admin {adm} ga SOS matnini yuborishda xato: {e}")

        await message.answer(t(lang, "sos_sent"), reply_markup=user_main_kb(lang, uid))
    except Exception as e:
        logger.error(f"sos_receive_location_text xatosi: {e}")


@router.callback_query(F.data == "sos:msg")
async def sos_message_flow(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        lang = await get_lang(callback.from_user.id)
        await state.set_state(SOSStates.waiting_for_message)
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer(t(lang, "sos_ask_msg"), reply_markup=cancel_kb(lang))
        await callback.answer()
    except Exception as e:
        logger.error(f"sos_message_flow xatosi: {e}")


@router.message(SOSStates.waiting_for_message)
async def sos_receive_text_message(message: Message, state: FSMContext) -> None:
    try:
        uid = message.from_user.id
        lang = await get_lang(uid)

        if message.text in ["❌ Bekor qilish", "❌ Отмена"]:
            await state.clear()
            await message.answer(t(lang, "action_cancelled"), reply_markup=user_main_kb(lang, uid))
            return

        await state.clear()
        user = await db_get_user(uid) or {}
        msg_body = message.text or "[Xabar]"

        drv_name = user.get("full_name") or message.from_user.full_name or "Haydovchi"
        drv_pos = user.get("position") or "Ro'yxatdan o'tmagan"
        drv_phone = user.get("phone") or "Kiritilmagan"
        
        car_m = user.get("car_model") or ""
        car_n = user.get("car_number") or ""
        drv_car = f"{car_m} ({car_n})".strip() if (car_m or car_n) else "Kiritilmagan"
        
        drv_card = user.get("card_number") or "Yo'q"
        drv_yandex = "Ulangan ✅" if user.get("yandex_driver_id") else "Ulanmagan ❌"
        
        tg_username = f"@{message.from_user.username}" if message.from_user.username else (f"@{user.get('username')}" if user.get("username") else "Mavjud emas")

        alert = (
            f"📩 <b>HAYDOVCHIDAN MUROJAAT / XABAR:</b>\n\n"
            f"🆔 <b>POSITION (Pozivnoy):</b> <code>{drv_pos}</code>\n"
            f"👤 <b>F.I.O / Ism:</b> <b>{drv_name}</b>\n"
            f"📱 <b>Telefon:</b> <code>{drv_phone}</code>\n"
            f"🚗 <b>Avtomobil:</b> <b>{drv_car}</b>\n"
            f"💳 <b>Karta:</b> <code>{drv_card}</code>\n"
            f"🚕 <b>Yandex Pro:</b> {drv_yandex}\n"
            f"➖➖➖➖➖➖➖➖➖➖\n"
            f"🌐 <b>Telegram Username:</b> {tg_username}\n"
            f"🆔 <b>Telegram ID:</b> <code>{uid}</code>\n\n"
            f"✍️ <b>Xabar matni:</b>\n{msg_body}"
        )

        chat_url = f"https://t.me/{message.from_user.username}" if message.from_user.username else f"tg://user?id={uid}"
        adm_kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="💬 Javob yozish", url=chat_url)]]
        )

        for adm in ADMIN_IDS:
            try:
                await bot.send_message(adm, alert, reply_markup=adm_kb)
            except Exception as e:
                logger.error(f"Admin {adm} ga SOS xabarini yuborishda xato: {e}")

        await message.answer(t(lang, "sos_sent"), reply_markup=user_main_kb(lang, uid))
    except Exception as e:
        logger.error(f"sos_receive_text_message xatosi: {e}")


# --- ADMIN PANEL ---

@admin_router.message(F.text.in_(["🛠 Admin Panel", "🛠 Админ Панель"]), StateFilter("*"))
async def admin_open(message: Message, state: FSMContext) -> None:
    if message.from_user.id not in ADMIN_IDS and message.from_user.id != MANAGER_TG_ID:
        return
    await state.clear()
    lang = await get_lang(message.from_user.id)
    await message.answer("🛠 <b>Admin Boshqaruv Paneli:</b>" if lang == "uz" else "🛠 <b>Панель Администратора:</b>", reply_markup=admin_main_kb(lang))


@admin_router.message(F.text.in_(["📊 Statistika", "📊 Статистика"]))
async def admin_stats_handler(message: Message) -> None:
    if message.from_user.id not in ADMIN_IDS and message.from_user.id != MANAGER_TG_ID:
        return
    stats = await db_get_stats()
    text = (
        f"📊 <b>{BOT_NAME} — Umumiy Tizim Statistikasi:</b>\n\n"
        f"👥 Botga kirgan jami foydalanuvchilar: <b>{stats['total_users']} ta</b>\n"
        f"🚕 Royxatdan otgan haydovchilar: <b>{stats['registered_drivers']} ta</b>\n"
        f"🔗 Yandex Pro ulangan haydovchilar: <b>{stats['yandex_linked']} ta</b>\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"💸 Jami yechilgan mablag: <b>{fmt_sum(stats['total_withdrawn'])} som</b>\n"
        f"📈 Taksopark komissiyasi: <b>{fmt_sum(stats['total_comm'])} som</b>"
    )
    await message.answer(text)


@admin_router.message(F.text.in_(["📥 Excel Hisobot", "📥 Excel Отчет"]))
async def admin_export_excel(message: Message) -> None:
    if message.from_user.id not in ADMIN_IDS and message.from_user.id != MANAGER_TG_ID:
        return
    status_msg = await message.answer("⏳ <i>Excel hisoboti tayyorlanmoqda...</i>")
    try:
        excel_bytes = await generate_monthly_excel_report()
        now_str = datetime.now().strftime("%Y_%m_%d_%H%M")
        filename = f"Lochin_Taxi_Hisobot_{now_str}.xlsx"
        file = BufferedInputFile(excel_bytes, filename=filename)
        await message.answer_document(
            document=file,
            caption=f"📊 <b>Lochin Taxi Hisoboti ({now_str})</b>\n\nBarcha haydovchilar va hisob-kitoblar."
        )
        try:
            await status_msg.delete()
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Excel xatosi: {e}")
        await status_msg.edit_text("❌ Excel yaratishda xatolik yuz berdi.")


@admin_router.message(F.text.in_(["🔄 Yandex Sinxronlash", "🔄 Синхронизация Яндекс"]))
async def admin_sync_all_drivers(message: Message) -> None:
    if message.from_user.id not in ADMIN_IDS and message.from_user.id != MANAGER_TG_ID:
        return
    status_msg = await message.answer("⏳ <i>Yandex kabinetdagi barcha haydovchilar yuklanmoqda...</i>")
    try:
        drivers, err = await yandex_api.get_all_drivers(limit=1000, force_refresh=True)

        if not drivers:
            await status_msg.edit_text(
                f"❌ <b>Yandex API dan ma'lumot olib bo'lmadi!</b>\n\n"
                f"📌 <b>Sabab / Xatolik:</b>\n<code>{err}</code>\n\n"
                f"💡 <i>Render Environment bo'limida YANDEX_API_KEY va YANDEX_PARK_ID to'g'ri kiritilganini tekshiring.</i>"
            )
            return

        count = 0
        now = utc_now_iso()

        for raw_drv in drivers:
            norm = yandex_api._normalize_driver_data(raw_drv)
            phone = clean_phone_number(norm.get("phone", ""))
            if not phone or len(phone) < 9:
                continue

            full_name = norm["full_name"]
            car_model = norm["car_model"]
            car_number = norm["car_number"]
            y_id = norm["id"]
            balance = norm["balance"]
            count += 1

            if db_pool:
                async with db_pool.acquire() as conn:
                    await conn.execute("""
                        INSERT INTO users (telegram_id, full_name, phone, car_model, car_number, yandex_driver_id, balance, is_registered, last_activity, created_at, updated_at)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, 1, $8, $9, $10)
                        ON CONFLICT (telegram_id) DO UPDATE SET 
                            full_name = EXCLUDED.full_name,
                            car_model = EXCLUDED.car_model,
                            car_number = EXCLUDED.car_number,
                            yandex_driver_id = EXCLUDED.yandex_driver_id,
                            balance = EXCLUDED.balance,
                            updated_at = EXCLUDED.updated_at
                    """, -count, full_name, phone, car_model, car_number, y_id, balance, now, now, now)
            else:
                conn = sqlite3.connect(DB_PATH)
                conn.execute("""
                    INSERT INTO users (telegram_id, full_name, phone, car_model, car_number, yandex_driver_id, balance, is_registered, last_activity, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                    ON CONFLICT(telegram_id) DO UPDATE SET
                        full_name=excluded.full_name,
                        car_model=excluded.car_model,
                        car_number=excluded.car_number,
                        yandex_driver_id=excluded.yandex_driver_id,
                        balance=excluded.balance,
                        updated_at=excluded.updated_at
                """, (-count, full_name, phone, car_model, car_number, y_id, balance, now, now, now))
                conn.commit()
                conn.close()

        await status_msg.edit_text(
            f"✅ <b>Muvaffaqiyatli sinxronlandi!</b>\n\n"
            f"🚕 Jami yuklangan haydovchilar: <b>{count} ta</b>\n"
            f"Endi ushbu haydovchilar botga kirishi bilan tizim ularni bir zumda taniydi!"
        )
    except Exception as e:
        logger.error(f"admin_sync_all_drivers xatosi: {e}")
        await status_msg.edit_text(f"❌ Xatolik yuz berdi: {e}")


@admin_router.message(F.text.in_(["📢 Xabar tarqatish", "📢 Рассылка"]))
async def admin_broadcast_prompt(message: Message, state: FSMContext) -> None:
    if message.from_user.id not in ADMIN_IDS and message.from_user.id != MANAGER_TG_ID:
        return
    await state.set_state(AdminBroadcastStates.waiting_for_message)
    await message.answer("📢 <b>Barcha haydovchilarga yubormoqchi bolgan xabaringizni yozing:</b>\n\n<i>Bekor qilish: '❌ Bekor qilish'</i>", reply_markup=cancel_kb("uz"))


@admin_router.message(AdminBroadcastStates.waiting_for_message)
async def admin_broadcast_send(message: Message, state: FSMContext) -> None:
    if message.from_user.id not in ADMIN_IDS and message.from_user.id != MANAGER_TG_ID:
        return

    if message.text in ["❌ Bekor qilish", "❌ Отмена"]:
        await state.clear()
        await message.answer("❌ Xabar tarqatish bekor qilindi.", reply_markup=admin_main_kb("uz"))
        return

    await state.clear()
    users = await db_get_all_users()
    status_msg = await message.answer("⏳ <i>Xabar yuborilmoqda...</i>")

    sent_count = 0
    fail_count = 0
    for u in users:
        tg_id = u.get("telegram_id")
        if tg_id and tg_id > 0:
            try:
                await bot.copy_message(chat_id=tg_id, from_chat_id=message.chat.id, message_id=message.message_id)
                sent_count += 1
                await asyncio.sleep(0.05)
            except Exception:
                fail_count += 1

    await status_msg.edit_text(f"📢 <b>Xabar tarqatish yakunlandi!</b>\n\n✅ Yetkazildi: <b>{sent_count} ta</b>\n❌ Yetib bormadi: <b>{fail_count} ta</b>")


@admin_router.message(F.text.in_(["👥 Haydovchilar", "👥 Водители"]))
async def admin_list_drivers(message: Message) -> None:
    if message.from_user.id not in ADMIN_IDS and message.from_user.id != MANAGER_TG_ID:
        return
    drivers = await db_get_all_registered_drivers()
    if not drivers:
        await message.answer("Hozircha royxatdan otgan haydovchilar yoq.")
        return

    text = f"👥 <b>Royxatdan otgan songgi haydovchilar (Jami: {len(drivers)} ta):</b>\n\n"
    for drv in drivers[-10:]:
        drv_pos = drv.get("position") or "N/A"
        drv_name = drv.get("full_name") or "Haydovchi"
        drv_phone = drv.get("phone") or ""
        drv_car_m = drv.get("car_model") or ""
        drv_car_n = drv.get("car_number") or ""
        drv_bal = fmt_sum(drv.get("balance", 0))

        text += (
            f"🆔 <code>{drv_pos}</code> — <b>{drv_name}</b>\n"
            f"📱 {drv_phone} | 🚗 {drv_car_m} ({drv_car_n})\n"
            f"💰 Balans: <b>{drv_bal} som</b>\n---------------------------\n"
        )
    await message.answer(text)


@admin_router.message(F.text.in_(["⬅️ Asosiy menyu", "⬅️ Главное меню"]), StateFilter("*"))
async def back_to_user_menu(message: Message, state: FSMContext) -> None:
    try:
        await state.clear()
        lang = await get_lang(message.from_user.id)
        await message.answer("Asosiy menyu:" if lang == "uz" else "Главное меню:", reply_markup=user_main_kb(lang, message.from_user.id))
    except Exception as e:
        logger.error(f"back_to_user_menu xatosi: {e}")


# --- OYLIK SCHEDULER & WEB RUNNER ---

async def monthly_report_scheduler():
    while True:
        try:
            now = datetime.now()
            if now.day == 1 and now.hour == 9 and now.minute == 0:
                excel_bytes = await generate_monthly_excel_report()
                filename = f"Lochin_Taxi_Oylik_Hisobot_{now.strftime('%Y_%m')}.xlsx"
                file = BufferedInputFile(excel_bytes, filename=filename)
                for adm in ADMIN_IDS:
                    try:
                        await bot.send_document(
                            chat_id=adm,
                            document=file,
                            caption=f"🗓 <b>{now.strftime('%B %Y')} Oylik Hisoboti!</b>\n\nTaksoparkdagi barcha haydovchilar va umumiy tushumlar."
                        )
                    except Exception as e:
                        logger.error(f"Admin {adm} ga oylik hisobot yuborishda xato: {e}")
                await asyncio.sleep(70)
        except Exception as e:
            logger.error(f"Oylik avtomatik hisobot xatosi: {e}")
        await asyncio.sleep(40)


routes = web.RouteTableDef()
@routes.get("/")
@routes.get("/health")
async def health_check(request: web.Request) -> web.Response:
    return web.Response(text="LOCHIN TAXI ENTERPRISE 24/7 IS RUNNING PERFECTLY", status=200)

async def start_web_server():
    app = web.Application()
    app.add_routes(routes)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"Web server 0.0.0.0:{PORT} portida ishga tushdi.")

async def main() -> None:
    logger.info("Lochin Taxi Bot ishga tushmoqda...")
    await init_database()

    dp.include_router(admin_router)
    dp.include_router(router)

    await start_web_server()
    asyncio.create_task(monthly_report_scheduler())

    await bot.delete_webhook(drop_pending_updates=True)
    logger.info(f"{BOT_NAME} muvaffaqiyatli ishga tushdi va xabarlarni qabul qilmoqda!")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())
