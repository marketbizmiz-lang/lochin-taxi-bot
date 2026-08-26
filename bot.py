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
SUPPORT_PHONE = os.getenv("SUPPORT_PHONE", "+998913773200").strip()
SUPPORT_PHONE_DISPLAY = os.getenv("SUPPORT_PHONE_DISPLAY", "+998 91 377 32 00").strip()
DRIVER_GROUP_LINK = os.getenv("DRIVER_GROUP_LINK", "https://t.me/+vLyCiiXNvB5kMTUy").strip()

# Yandex Fleet API sozlamalari (Header registri qat'iy standartda)
YANDEX_API_KEY = os.getenv("YANDEX_API_KEY", "").strip()
YANDEX_CLIENT_ID = os.getenv("YANDEX_CLIENT_ID", "").strip()
YANDEX_PARK_ID = os.getenv("YANDEX_PARK_ID", "").strip()
YANDEX_FLEET_URL = "https://fleet-api.yandex.ru/v1"

BRB_API_URL = os.getenv("BRB_API_URL", "https://api.brb.uz/v1").strip()
BRB_API_KEY = os.getenv("BRB_API_KEY", "").strip()
BRB_MERCHANT_ID = os.getenv("BRB_MERCHANT_ID", "").strip()

MIN_WITHDRAWAL = int(os.getenv("MIN_WITHDRAWAL", "30000"))
COMMISSION_PERCENT = float(os.getenv("COMMISSION_PERCENT", "2.0"))
REFERRAL_BONUS = int(os.getenv("REFERRAL_BONUS", "30000"))


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
# DATABASE LAYER
# ============================================================

db_pool: Optional[asyncpg.Pool] = None

async def init_database():
    global db_pool
    if DATABASE_URL:
        try:
            clean_url = DATABASE_URL.replace("?sslmode=require", "")
            db_pool = await asyncpg.create_pool(clean_url, ssl="require", min_size=1, max_size=10)
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
            logger.info("PostgreSQL bazasi tayyor!")
        except Exception as e:
            logger.error(f"PostgreSQL ga ulanishda xato: {e}")
            db_pool = None

    if not db_pool:
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


async def db_get_user(telegram_id: int) -> Optional[dict]:
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


async def db_upsert_start(telegram_id: int, username: str, referrer_id: Optional[int] = None):
    now = utc_now_iso()
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


async def db_set_language(telegram_id: int, language: str):
    now = utc_now_iso()
    if db_pool:
        async with db_pool.acquire() as conn:
            await conn.execute("UPDATE users SET language = $1, updated_at = $2 WHERE telegram_id = $3", language, now, telegram_id)
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE users SET language = ?, updated_at = ? WHERE telegram_id = ?", (language, now, telegram_id))
        conn.commit()
        conn.close()


async def db_generate_unique_position() -> str:
    import random
    for _ in range(100):
        pos = f"LCH-{random.randint(1000, 9999)}"
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
    return f"LCH-{random.randint(10000, 99999)}"


async def db_finish_registration(telegram_id: int, full_name: str, phone: str, card_number: str,
                                 car_model: str, car_number: str, yandex_driver_id: Optional[str]) -> str:
    position = await db_generate_unique_position()
    now = utc_now_iso()
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
    return position


async def db_update_balance(telegram_id: int, balance: float):
    now = utc_now_iso()
    if db_pool:
        async with db_pool.acquire() as conn:
            await conn.execute("UPDATE users SET balance = $1, updated_at = $2 WHERE telegram_id = $3", balance, now, telegram_id)
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE users SET balance = ?, updated_at = ? WHERE telegram_id = ?", (balance, now, telegram_id))
        conn.commit()
        conn.close()


async def db_create_withdrawal(user_id: int, amount: float, commission: float, net_amount: float,
                              card_number: str, status: str, payout_method: str, ext_tx_id: str = "") -> int:
    now = utc_now_iso()
    if db_pool:
        async with db_pool.acquire() as conn:
            await conn.execute("UPDATE users SET balance = balance - $1, updated_at = $2 WHERE id = $3", amount, now, user_id)
            row = await conn.fetchrow("""
                INSERT INTO withdrawals (user_id, amount, commission, net_amount, card_number, status, payout_method, ext_tx_id, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                RETURNING id
            """, user_id, amount, commission, net_amount, card_number, status, payout_method, ext_tx_id, now, now)
            return row["id"] if row else 0
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE users SET balance = balance - ?, updated_at = ? WHERE id = ?", (amount, now, user_id))
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO withdrawals (user_id, amount, commission, net_amount, card_number, status, payout_method, ext_tx_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, amount, commission, net_amount, card_number, status, payout_method, ext_tx_id, now, now))
        w_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return w_id or 0


async def db_get_all_registered_drivers() -> List[dict]:
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


async def db_get_all_users() -> List[dict]:
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


async def db_get_stats() -> dict:
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


# ============================================================
# YANDEX FLEET & BRB API
# ============================================================

class YandexFleetAPI:
    def __init__(self, api_key: str, client_id: str, park_id: str):
        self.api_key = api_key.strip()
        self.client_id = client_id.strip()
        self.park_id = park_id.strip()
        self.base_url = YANDEX_FLEET_URL.rstrip("/")

    @property
    def headers(self) -> dict:
        return {
            "X-Client-ID": self.client_id,
            "X-API-Key": self.api_key,
            "X-Park-ID": self.park_id,
            "Content-Type": "application/json",
            "Accept-Language": "ru",
        }

    def _is_configured(self) -> bool:
        return bool(self.api_key and self.park_id and self.client_id)

    async def get_driver_by_phone(self, phone: str) -> Optional[dict]:
        if not self._is_configured():
            return None

        clean_digits = re.sub(r"\D", "", phone)
        short_phone = clean_digits[3:] if clean_digits.startswith("998") and len(clean_digits) == 12 else clean_digits
        full_phone_plus = f"+{clean_digits}" if not phone.startswith("+") else phone

        url = f"{self.base_url}/parks/driver-profiles/list"
        payload = {
            "query": {
                "park": {"id": self.park_id},
                "driver": {
                    "phones": [full_phone_plus, clean_digits, short_phone, f"+{short_phone}"]
                }
            },
            "limit": 10
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=self.headers, json=payload, timeout=12) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        drivers = data.get("driver_profiles", [])
                        if drivers:
                            return self._normalize_driver_data(drivers[0])
        except Exception as e:
            logger.error(f"Yandex get_driver_by_phone xatosi: {e}")

        all_drivers = await self.get_all_drivers(limit=1000)
        for drv in all_drivers:
            prof = drv.get("driver_profile", {})
            phones = prof.get("phones", [])
            for p in phones:
                p_digits = re.sub(r"\D", "", str(p))
                if short_phone and short_phone in p_digits:
                    return self._normalize_driver_data(drv)
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
        car_number = car.get("number", "").strip() or car.get("normalized_number", "").strip() or "Noma'lum"

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

    async def get_all_drivers(self, limit: int = 1000) -> List[dict]:
        if not self._is_configured():
            return []
        url = f"{self.base_url}/parks/driver-profiles/list"
        payload = {"query": {"park": {"id": self.park_id}}, "limit": limit}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=self.headers, json=payload, timeout=25) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("driver_profiles", [])
        except Exception as e:
            logger.error(f"Yandex get_all_drivers xatosi: {e}")
        return []

    async def get_driver_balance(self, yandex_driver_id: str) -> Optional[float]:
        if not self._is_configured() or not yandex_driver_id:
            return None
        url = f"{self.base_url}/parks/driver-profiles/list"
        payload = {"query": {"park": {"id": self.park_id}, "driver": {"id": [yandex_driver_id]}}, "limit": 1}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=self.headers, json=payload, timeout=12) as resp:
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
        payload = {
            "park_id": self.park_id,
            "driver_profile_id": yandex_driver_id,
            "amount": str(-abs(amount)),
            "category_id": "other",
            "description": description
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=self.headers, json=payload, timeout=12) as resp:
                    return resp.status in [200, 201]
        except Exception as e:
            logger.error(f"Yandex tranzaksiya xatosi: {e}")
            return False


class BRBPaymentAPI:
    def __init__(self, api_url: str, api_key: str, merchant_id: str):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key.strip()
        self.merchant_id = merchant_id.strip()

    def _is_configured(self) -> bool:
        return bool(self.api_key and self.merchant_id)

    async def send_payout(self, card_number: str, amount: float, order_id: int) -> dict:
        if not self._is_configured():
            return {"success": False, "message": "Manual rejim"}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "X-Merchant-ID": self.merchant_id,
            "Content-Type": "application/json"
        }
        payload = {
            "card_number": card_number.replace(" ", ""),
            "amount": int(amount),
            "order_id": f"LCH-WD-{order_id}",
            "description": f"Lochin Taxi to'lovi #{order_id}"
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{self.api_url}/payout", headers=headers, json=payload, timeout=15) as resp:
                    data = await resp.json()
                    if resp.status == 200 and data.get("status") == "success":
                        return {"success": True, "tx_id": data.get("tx_id", "")}
                    else:
                        return {"success": False, "message": data.get("message", "Xatolik")}
        except Exception as e:
            logger.error(f"BRB API xatosi: {e}")
            return {"success": False, "message": str(e)}

yandex_api = YandexFleetAPI(YANDEX_API_KEY, YANDEX_CLIENT_ID, YANDEX_PARK_ID)
brb_api = BRBPaymentAPI(BRB_API_URL, BRB_API_KEY, BRB_MERCHANT_ID)


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
        "Jami Buyurtmalar", "Jami Daromad (so'm)", "Ushlab qolingan Komissiya (so'm)",
        "Joriy Balans (so'm)", "Yandex Driver ID"
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

        row_data = [
            idx, drv.get("position", "N/A"), drv.get("full_name", "Noma'lum"),
            drv.get("phone", ""), drv.get("car_model", ""), drv.get("car_number", ""),
            drv.get("card_number", ""), orders, int(earnings), int(comm), int(bal),
            drv.get("yandex_driver_id", "Yo'q")
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
        "welcome": f"🕌 <b>Assalomu alaykum!</b>\n\n🚕 <b>{BOT_NAME}</b> taksoparkiga xush kelibsiz! Biz bilan daromadingizni oshiring! 🤝\n\nTizimdan to'liq foydalanish uchun ro'yxatdan o'ting:",
        "register_btn": "📝 Ro'yxatdan o'tish",
        "reg_name": "👤 <b>Ism va familiyangizni kiriting:</b>\n\n<i>Misol: Alisher Qodirov</i>",
        "reg_phone": "📱 <b>Telefon raqamingizni yuboring:</b>\n\nQuyidagi <b>[📱 Telefon raqamni yuborish]</b> tugmasini bosing yoki raqamingizni yozing (Format: <i>+998901234567</i>):",
        "reg_card": "💳 <b>Plastik karta raqamingizni kiriting (16 ta raqam):</b>\n\n<i>Misol: 8600 1234 5678 9012 yoki 9860...</i>",
        "reg_car_model": "🚗 <b>Avtomobilingiz rusumini kiriting:</b>\n\n<i>Misol: Chevrolet Cobalt</i>",
        "reg_car_number": "🔢 <b>Avtomobil davlat raqamini kiriting:</b>\n\n<i>Misol: 01 A 123 AA</i>",
        "reg_success": f"✅ <b>Tabriklaymiz! Siz muvaffaqiyatli ro'yxatdan o'tdingiz.</b>\n\n🆔 Sizning POSITION ID: <code>{{position}}</code>\n🔑 Bu kod sizning taksoparkdagi shaxsiy kodingiz.",
        "already_reg": "✅ <b>Siz tizimda ro'yxatdan o'tgansiz!</b>\n\n🆔 POSITION: <code>{position}</code>\n👤 Haydovchi: <b>{name}</b>",
        "menu_balance": "💰 Balans",
        "menu_orders": "📊 Bugungi buyurtmalar",
        "menu_withdraw": "💸 Pul yechish (24/7)",
        "menu_profile": "👤 Profil",
        "menu_referral": "👥 Do'stni taklif qilish (Bonus)",
        "menu_top": "🏆 TOP Haydovchilar",
        "menu_group": "📢 Yangiliklar / Guruh",
        "menu_sos": "🆘 Yordam / SOS",
        "menu_admin": "🛠 Admin Panel",
        "cancel": "❌ Bekor qilish",
        "send_phone_btn": "📱 Telefon raqamni yuborish",
        "action_cancelled": "❌ Amaliyot bekor qilindi.",
        "balance_detail": f"💰 <b>{{bot_name}} da Balans va Daromad:</b>\n\n💵 <b>Naqd tushum:</b> 0 so'm\n💳 <b>Karta tushum (Yandex):</b> <b>{{balance}} so'm</b>\n🔒 <b>Muzlatilgan:</b> {{blocked}} so'm\n➖➖➖➖➖➖➖➖➖➖\n✅ <b>Kartaga yechish mumkin:</b> <b>{{avail}} so'm</b>\n\n🚕 Yandex Pro holati: <b>{{y_status}}</b>",
        "withdraw_min_err": "❌ Minimal yechish summasi: {min_w} so'm",
        "withdraw_no_money": "❌ Balansingizda yechish uchun yetarli mablag' mavjud emas!",
        "withdraw_ask": f"💸 <b>Pul yechish (24/7 Avtomat):</b>\n\n🔹 Yechish mumkin: <b>{{avail}} so'm</b>\n🔹 Minimal summa: <b>{{min_w}} so'm</b>\n🔹 Komissiya: <b>{{comm}}%</b>\n\nYechmoqchi bo'lgan summani kiriting (Masalan: <i>50000</i>):",
        "withdraw_confirm": "💳 <b>Pul yechishni tasdiqlaysizmi?</b>\n\n💰 Yechilayotgan summa: <b>{amount} so'm</b>\n📊 Komissiya ({comm}%): <b>{comm_amount} so'm</b>\n💵 Kartaga tushadi: <b>{net_amount} so'm</b>\n💳 Karta: <code>{card}</code>",
        "ref_text": f"👥 <b>Do'stlarni taklif qiling va daromad oling!</b>\n\nHar bir taklif qilgan faol haydovchingiz uchun: <b>{REFERRAL_BONUS:,} so'm</b> bonus olasiz!\n\n🔗 Sizning taklif havolangiz:\n<code>{{link}}</code>",
        "sos_title": f"🆘 <b>Tezkor Yordam va Aloqa Markazi</b>\n\n📞 <b>Menejer telefoni:</b> {SUPPORT_PHONE_DISPLAY}\n\nKerakli bo'limni tanlang:",
        "sos_btn_loc": "📍 Lokatsiya yuborish (DTP / Yo'lda qoldim)",
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
        "reg_name": "👤 <b>Введите ваше имя и фамилию:</b>\n\n<i>Пример: Алишер Кадыров</i>",
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
        "menu_referral": "👥 Пригласить друга (Бонус)",
        "menu_top": "🏆 ТОП Водителей",
        "menu_group": "📢 Новости / Группа",
        "menu_sos": "🆘 Помощь / SOS",
        "menu_admin": "🛠 Админ Панель",
        "cancel": "❌ Отмена",
        "send_phone_btn": "📱 Отправить номер телефона",
        "action_cancelled": "❌ Действие отменено.",
        "balance_detail": f"💰 <b>Баланс и Доход в {{bot_name}}:</b>\n\n💵 <b>Наличные:</b> 0 сум\n💳 <b>Безналичные (Яндекс):</b> <b>{{balance}} сум</b>\n🔒 <b>Заблокировано:</b> {{blocked}} сум\n➖➖➖➖➖➖➖➖➖➖\n✅ <b>Доступно к выводу:</b> <b>{{avail}} сум</b>\n\n🚕 Статус Яндекс Про: <b>{{y_status}}</b>",
        "withdraw_min_err": "❌ Минимальная сумма вывода: {min_w} сум",
        "withdraw_no_money": "❌ Недостаточно средств для вывода!",
        "withdraw_ask": f"💸 <b>Вывод средств (24/7 Авто):</b>\n\n🔹 Доступно: <b>{{avail}} сум</b>\n🔹 Мин. сумма: <b>{{min_w}} сум</b>\n🔹 Комиссия: <b>{{comm}}%</b>\n\nВведите сумму для вывода (Пример: <i>50000</i>):",
        "withdraw_confirm": "💳 <b>Подтверждаете вывод средств?</b>\n\n💰 Сумма: <b>{amount} сум</b>\n📊 Комиссия ({comm}%): <b>{comm_amount} сум</b>\n💵 К зачислению: <b>{net_amount} сум</b>\n💳 Карта: <code>{card}</code>",
        "ref_text": f"👥 <b>Приглашайте друзей и получайте бонусы!</b>\n\nЗа каждого активного водителя: <b>{REFERRAL_BONUS:,} сум</b> бонуса!\n\n🔗 Ваша реферальная ссылка:\n<code>{{link}}</code>",
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
        [KeyboardButton(text=t(lang, "menu_referral")), KeyboardButton(text=t(lang, "menu_top"))],
        [KeyboardButton(text=t(lang, "menu_group")), KeyboardButton(text=t(lang, "menu_sos"))],
    ]
    if uid in ADMIN_IDS:
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
            [KeyboardButton(text="👥 Haydovchilar" if is_uz else "👥 Водители"), KeyboardButton(text="🚫 Nofaollar" if is_uz else "🚫 Неактивные")],
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
    await state.clear()
    uid = message.from_user.id
    lang = await get_lang(uid)
    user = await db_get_user(uid)
    if user and user.get("is_registered") == 1:
        await message.answer(t(lang, "action_cancelled"), reply_markup=user_main_kb(lang, uid))
    else:
        await message.answer(t(lang, "action_cancelled"), reply_markup=register_reply_kb(lang))


@router.message(CommandStart(), StateFilter("*"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    uid = message.from_user.id
    referrer_id = None
    args = message.text.split()
    if len(args) > 1 and args[1].startswith("ref_"):
        ref_raw = args[1].replace("ref_", "")
        if ref_raw.isdigit() and int(ref_raw) != uid:
            referrer_id = int(ref_raw)

    await db_upsert_start(uid, message.from_user.username or "", referrer_id)
    user = await db_get_user(uid)

    if user and user.get("is_registered") == 1:
        lang = user.get("language", "uz")
        await message.answer(
            t(lang, "already_reg", position=user.get("position", "N/A"), name=user.get("full_name", "Haydovchi")),
            reply_markup=user_main_kb(lang, uid)
        )
        return

    await message.answer(
        "🌐 <b>Iltimos, tilni tanlang / Пожалуйста, выберите язык:</b>",
        reply_markup=language_inline_kb()
    )


@router.callback_query(F.data.startswith("lang:"))
async def lang_callback(callback: CallbackQuery, state: FSMContext) -> None:
    lang = callback.data.split(":")[1]
    uid = callback.from_user.id
    await db_set_language(uid, lang)

    try:
        await callback.message.delete()
    except Exception:
        pass

    user = await db_get_user(uid)
    if user and user.get("is_registered") == 1:
        await callback.message.answer(
            t(lang, "already_reg", position=user.get("position", "N/A"), name=user.get("full_name", "Haydovchi")),
            reply_markup=user_main_kb(lang, uid)
        )
    else:
        await callback.message.answer(t(lang, "welcome"), reply_markup=register_reply_kb(lang))
    await callback.answer()


# --- RO'YXATDAN O'TISH ---

@router.message(F.text.in_(["📝 Ro'yxatdan o'tish", "📝 Регистрация"]), StateFilter("*"))
async def reg_start_flow(message: Message, state: FSMContext) -> None:
    await state.clear()
    uid = message.from_user.id
    user = await db_get_user(uid)
    lang = user.get("language", "uz") if user else "uz"

    if user and user.get("is_registered") == 1:
        await message.answer(
            t(lang, "already_reg", position=user.get("position", "N/A"), name=user.get("full_name", "Haydovchi")),
            reply_markup=user_main_kb(lang, uid)
        )
        return

    await state.set_state(RegStates.phone)
    await message.answer(t(lang, "reg_phone"), reply_markup=phone_request_kb(lang))


@router.message(RegStates.phone)
async def reg_step_phone(message: Message, state: FSMContext) -> None:
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
        full_name = y_driver.get("full_name", "Haydovchi")
        car_model = y_driver.get("car_model", "Chevrolet Cobalt")
        car_number = y_driver.get("car_number", "Noma'lum")
        y_id = y_driver.get("id")

        await state.update_data(
            full_name=full_name,
            car_model=car_model,
            car_number=car_number,
            yandex_driver_id=y_id
        )

        found_msg = (
            f"✅ <b>Siz Yandex Pro taksoparkimizda topildingiz!</b>\n\n"
            f"👤 Haydovchi: <b>{full_name}</b>\n"
            f"🚗 Avtomobil: <b>{car_model} ({car_number})</b>\n\n"
            f"{t(lang, 'reg_card')}"
        ) if lang == "uz" else (
            f"✅ <b>Вы найдены в базе Яндекс Про таксопарка!</b>\n\n"
            f"👤 Водитель: <b>{full_name}</b>\n"
            f"🚗 Автомобиль: <b>{car_model} ({car_number})</b>\n\n"
            f"{t(lang, 'reg_card')}"
        )
        await message.answer(found_msg, reply_markup=cancel_kb(lang))
        await state.set_state(RegStates.card)
    else:
        await state.set_state(RegStates.name)
        await message.answer(t(lang, "reg_name"), reply_markup=cancel_kb(lang))


@router.message(RegStates.name)
async def reg_step_name(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    lang = await get_lang(uid)
    name = (message.text or "").strip()
    if len(name) < 3:
        await message.answer("⚠️ Iltimos, ism va familiyangizni to'liq kiriting:")
        return

    await state.update_data(full_name=name)
    await state.set_state(RegStates.card)
    await message.answer(t(lang, "reg_card"), reply_markup=cancel_kb(lang))


@router.message(RegStates.card)
async def reg_step_card(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    lang = await get_lang(uid)
    card = (message.text or "").strip().replace(" ", "").replace("-", "")
    if not (card.isdigit() and len(card) == 16):
        await message.answer("⚠️ Plastik karta 16 ta raqamdan iborat bo'lishi kerak. Qaytadan kiriting:")
        return

    await state.update_data(card_number=card)
    data = await state.get_data()
    if data.get("yandex_driver_id"):
        await finish_registration_process(message, state, data)
    else:
        await state.set_state(RegStates.car_model)
        await message.answer(t(lang, "reg_car_model"), reply_markup=cancel_kb(lang))


@router.message(RegStates.car_model)
async def reg_step_car_model(message: Message, state: FSMContext) -> None:
    car_model = (message.text or "").strip()
    await state.update_data(car_model=car_model)
    lang = await get_lang(message.from_user.id)
    await state.set_state(RegStates.car_number)
    await message.answer(t(lang, "reg_car_number"), reply_markup=cancel_kb(lang))


@router.message(RegStates.car_number)
async def reg_step_car_number(message: Message, state: FSMContext) -> None:
    car_number = (message.text or "").strip().upper()
    await state.update_data(car_number=car_number)
    data = await state.get_data()
    await finish_registration_process(message, state, data)


async def finish_registration_process(message: Message, state: FSMContext, data: dict):
    uid = message.from_user.id
    lang = await get_lang(uid)
    await state.clear()

    full_name = data.get("full_name", "Haydovchi")
    phone = data.get("phone", "")
    card = data.get("card_number", "")
    car_model = data.get("car_model", "Chevrolet")
    car_number = data.get("car_number", "")
    y_id = data.get("yandex_driver_id")
    yandex_status_text = "Ulangan" if y_id else "Ulanmagan"

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
                f"🆕 <b>Yangi haydovchi ro'yxatdan o'tdi!</b>\n\n"
                f"👤 Ism: <b>{full_name}</b>\n"
                f"📱 Telefon: <code>{phone}</code>\n"
                f"🚗 Mashina: <b>{car_model} ({car_number})</b>\n"
                f"💳 Karta: <code>{card}</code>\n"
                f"🆔 POSITION: <code>{position}</code>\n"
                f"🚕 Yandex: <b>{yandex_status_text}</b>"
            )
        except Exception:
            pass


# --- BALANS VA BUYURTMALAR ---

@router.message(F.text.in_(["💰 Balans", "💰 Баланс"]))
async def balance_handler(message: Message) -> None:
    uid = message.from_user.id
    user = await db_get_user(uid)
    if not user or user.get("is_registered") != 1:
        await message.answer("Iltimos, avval ro'yxatdan o'ting: /start", reply_markup=register_reply_kb("uz"))
        return

    lang = user.get("language", "uz")
    cur_bal = float(user.get("balance", 0.0) or 0.0)
    y_status = "✅ Ulangan" if user.get("yandex_driver_id") else "⚠️ Ulanmagan"

    if user.get("yandex_driver_id"):
        live_bal = await yandex_api.get_driver_balance(user["yandex_driver_id"])
        if live_bal is not None:
            cur_bal = live_bal
            await db_update_balance(uid, live_bal)

    avail = max(0.0, cur_bal - float(user.get("blocked_balance", 0.0) or 0.0))
    await message.answer(
        t(lang, "balance_detail",
          bot_name=BOT_NAME,
          balance=fmt_sum(cur_bal),
          blocked=fmt_sum(user.get("blocked_balance", 0)),
          avail=fmt_sum(avail),
          y_status=y_status),
        reply_markup=user_main_kb(lang, uid)
    )


@router.message(F.text.in_(["📊 Bugungi buyurtmalar", "📊 Сегодняшние заказы"]))
async def orders_handler(message: Message) -> None:
    lang = await get_lang(message.from_user.id)
    text = (
        "📊 <b>Bugungi buyurtmalar:</b>\n\n"
        "Barcha safarlaringiz va daromadlaringiz <b>Yandex Pro</b> ilovasida real vaqt rejimida hisoblanadi "
        "va avtomatik tarzda ushbu bot balansingizda aks etadi."
    ) if lang == "uz" else (
        "📊 <b>Сегодняшние заказы:</b>\n\n"
        "Все ваши поездки и доходы рассчитываются в приложении <b>Яндекс Про</b> в режиме реального времени "
        "и автоматически отображаются на балансе в боте."
    )
    await message.answer(text, reply_markup=user_main_kb(lang, message.from_user.id))


@router.message(F.text.in_(["🏆 TOP Haydovchilar", "🏆 ТОП Водителей"]))
async def top_drivers_handler(message: Message) -> None:
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
                text += f"{medal} <b>{drv.get('full_name', 'Haydovchi')}</b> (<code>{drv.get('position', 'N/A')}</code>) — <b>{drv.get('total_orders', 0)} ta zakaz</b>\n"
        else:
            text += "<i>Hozircha haftalik reyting shakllanmoqda...</i>\n"
        text += "\n🔥 <i>Ko'proq buyurtma bajaring va haftalik maxsus bonuslarga ega bo'ling!</i>"
    else:
        text = f"🏆 <b>{BOT_NAME} — ТОП Водителей Недели:</b>\n\n"
        if drivers_sorted and any(int(d.get("total_orders", 0) or 0) > 0 for d in drivers_sorted):
            for idx, drv in enumerate(drivers_sorted):
                medal = medals[idx] if idx < len(medals) else f"{idx+1}."
                text += f"{medal} <b>{drv.get('full_name', 'Водитель')}</b> (<code>{drv.get('position', 'N/A')}</code>) — <b>{drv.get('total_orders', 0)} заказов</b>\n"
        else:
            text += "<i>Рейтинг недели формируется...</i>\n"
        text += "\n🔥 <i>Выполняйте больше поездок и получайте еженедельные бонусы!</i>"

    await message.answer(text, reply_markup=user_main_kb(lang, uid))


@router.message(F.text.in_(["👤 Profil", "👤 Профиль"]))
async def profile_handler(message: Message) -> None:
    uid = message.from_user.id
    user = await db_get_user(uid)
    if not user or user.get("is_registered") != 1:
        await message.answer("Iltimos, avval ro'yxatdan o'ting: /start", reply_markup=register_reply_kb("uz"))
        return

    lang = user.get("language", "uz")
    if lang == "uz":
        text = (
            f"👤 <b>Haydovchi Profili:</b>\n\n"
            f"🆔 POSITION: <code>{user.get('position', 'N/A')}</code>\n"
            f"👤 Ism: <b>{user.get('full_name')}</b>\n"
            f"📱 Telefon: <b>{user.get('phone')}</b>\n"
            f"🚗 Avtomobil: <b>{user.get('car_model')} ({user.get('car_number')})</b>\n"
            f"💳 Karta: <code>{user.get('card_number')}</code>\n"
            f"🚕 Yandex: <b>{'✅ Ulangan' if user.get('yandex_driver_id') else '⚠️ Ulanmagan'}</b>\n"
            f"🌐 Til: <b>O'zbekcha 🇺🇿</b>"
        )
        change_lang_btn = "🌐 Tilni o'zgartirish"
    else:
        text = (
            f"👤 <b>Профиль Водителя:</b>\n\n"
            f"🆔 POSITION: <code>{user.get('position', 'N/A')}</code>\n"
            f"👤 Имя: <b>{user.get('full_name')}</b>\n"
            f"📱 Телефон: <b>{user.get('phone')}</b>\n"
            f"🚗 Автомобиль: <b>{user.get('car_model')} ({user.get('car_number')})</b>\n"
            f"💳 Карта: <code>{user.get('card_number')}</code>\n"
            f"🚕 Яндекс: <b>{'✅ Привязан' if user.get('yandex_driver_id') else '⚠️ Не привязан'}</b>\n"
            f"🌐 Язык: <b>Русский 🇷🇺</b>"
        )
        change_lang_btn = "🌐 Сменить язык"

    inline_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=change_lang_btn, callback_data="change_lang_menu")]]
    )
    await message.answer(text, reply_markup=inline_kb)


@router.callback_query(F.data == "change_lang_menu")
async def change_lang_menu_cb(callback: CallbackQuery) -> None:
    await callback.message.edit_text("🌐 Tilni tanlang / Выберите язык:", reply_markup=language_inline_kb())
    await callback.answer()


@router.message(F.text.in_(["👥 Do'stni taklif qilish (Bonus)", "👥 Пригласить друга (Бонус)"]))
async def referral_handler(message: Message) -> None:
    uid = message.from_user.id
    user = await db_get_user(uid)
    if not user:
        return
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{uid}"
    lang = user.get("language", "uz")
    await message.answer(t(lang, "ref_text", link=ref_link), reply_markup=user_main_kb(lang, uid))


@router.message(F.text.in_(["📢 Yangiliklar / Guruh", "📢 Новости / Группа"]))
async def group_handler(message: Message) -> None:
    lang = await get_lang(message.from_user.id)
    btn_text = "💬 Haydovchilar guruhiga qo'shilish" if lang == "uz" else "💬 Вступить в группу водителей"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=btn_text, url=DRIVER_GROUP_LINK)]])
    await message.answer(f"📢 <b>{BOT_NAME} Rasmiy Guruhimiz:</b>", reply_markup=kb)


# --- PUL YECHISH ---

@router.message(F.text.in_(["💸 Pul yechish (24/7)", "💸 Вывод средств (24/7)"]), StateFilter("*"))
async def withdraw_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    uid = message.from_user.id
    user = await db_get_user(uid)
    if not user or user.get("is_registered") != 1:
        await message.answer("Iltimos, avval ro'yxatdan o'ting: /start", reply_markup=register_reply_kb("uz"))
        return

    lang = user.get("language", "uz")
    if user.get("yandex_driver_id"):
        live_bal = await yandex_api.get_driver_balance(user["yandex_driver_id"])
        if live_bal is not None:
            await db_update_balance(uid, live_bal)
            user["balance"] = live_bal

    cur_bal = float(user.get("balance", 0.0) or 0.0)
    blocked = float(user.get("blocked_balance", 0.0) or 0.0)
    avail = max(0.0, cur_bal - blocked)

    if avail < MIN_WITHDRAWAL:
        await message.answer(
            f"{t(lang, 'withdraw_no_money')}\n\n"
            f"🔹 Yechish mumkin: <b>{fmt_sum(avail)} so'm</b>\n"
            f"🔹 Minimal: <b>{fmt_sum(MIN_WITHDRAWAL)} so'm</b>",
            reply_markup=user_main_kb(lang, uid)
        )
        return

    await state.set_state(WithdrawStates.amount)
    await message.answer(
        t(lang, "withdraw_ask",
          avail=fmt_sum(avail),
          min_w=fmt_sum(MIN_WITHDRAWAL),
          comm=COMMISSION_PERCENT),
        reply_markup=cancel_kb(lang)
    )


@router.message(WithdrawStates.amount)
async def withdraw_amount_step(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    lang = await get_lang(uid)
    raw = (message.text or "").replace(" ", "").replace("so'm", "").replace("сум", "").strip()

    if not raw.isdigit():
        await message.answer("⚠️ Iltimos, summani faqat musbat raqamlarda kiriting (Masalan: <i>50000</i>):")
        return

    amount = float(raw)
    user = await db_get_user(uid)
    cur_bal = float(user.get("balance", 0.0) or 0.0)
    blocked = float(user.get("blocked_balance", 0.0) or 0.0)
    avail = max(0.0, cur_bal - blocked)

    if amount < MIN_WITHDRAWAL:
        await message.answer(t(lang, "withdraw_min_err", min_w=fmt_sum(MIN_WITHDRAWAL)))
        return

    if amount > avail:
        await message.answer(f"❌ Mablag' yetarli emas! Siz ko'pi bilan <b>{fmt_sum(avail)} so'm</b> yecha olasiz.")
        return

    comm = amount * (COMMISSION_PERCENT / 100.0)
    net = amount - comm

    await state.update_data(amount=amount, commission=comm, net_amount=net, card=user.get("card_number", ""))
    await state.set_state(WithdrawStates.confirm)

    btn_text = "⚡️ Avtomat Yechish (24/7)" if lang == "uz" else "⚡️ Авто Вывод (24/7)"
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
          card=user.get("card_number", "")),
        reply_markup=kb
    )


@router.callback_query(F.data.startswith("wd_go:"), WithdrawStates.confirm)
async def withdraw_process_callback(callback: CallbackQuery, state: FSMContext) -> None:
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

    if user.get("yandex_driver_id"):
        await yandex_api.create_transaction(user["yandex_driver_id"], amount, f"Lochin Taxi Bot Yechish {card}")

    brb_res = await brb_api.send_payout(card, net_amount, user["id"])
    status = "completed" if brb_res.get("success") else "manual_pending"

    w_id = await db_create_withdrawal(
        user_id=user["id"],
        amount=amount,
        commission=commission,
        net_amount=net_amount,
        card_number=card,
        status=status,
        payout_method="brb_auto" if brb_res.get("success") else "manual",
        ext_tx_id=brb_res.get("tx_id", "")
    )

    if brb_res.get("success"):
        msg = (
            f"✅ <b>Mablag' kartangizga muvaffaqiyatli o'tkazildi!</b>\n\n"
            f"💰 Yechilgan: <b>{fmt_sum(amount)} so'm</b>\n"
            f"💵 Kartaga tushdi: <b>{fmt_sum(net_amount)} so'm</b>\n"
            f"💳 Karta: <code>{card}</code>"
        )
    else:
        msg = (
            f"✅ <b>Pul yechish arizangiz qabul qilindi!</b>\n\n"
            f"💰 Summa: <b>{fmt_sum(net_amount)} so'm</b>\n"
            f"💳 Karta: <code>{card}</code>\n\n"
            f"<i>Mablag' tez orada kartangizga o'tkaziladi.</i>"
        )

    await callback.message.edit_text(msg)
    await callback.answer()

    for adm in ADMIN_IDS:
        try:
            await bot.send_message(
                adm,
                f"💸 <b>Yangi pul yechish:</b>\n\n"
                f"👤 Haydovchi: <b>{user.get('full_name')}</b> (<code>{user.get('position')}</code>)\n"
                f"📱 Telefon: <code>{user.get('phone')}</code>\n"
                f"💰 Summa: <b>{fmt_sum(amount)} so'm</b>\n"
                f"💳 Karta: <code>{card}</code>\n"
                f"📊 Holat: <b>{status}</b>"
            )
        except Exception:
            pass


# --- SOS / YORDAM ---

@router.message(F.text.in_(["🆘 Yordam / SOS", "🆘 Помощь / SOS"]), StateFilter("*"))
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
    user = await db_get_user(uid)

    lat = message.location.latitude
    lon = message.location.longitude
    maps_url = f"https://maps.google.com/?q={lat},{lon}"

    alert = (
        f"🚨 <b>DIQQAT: HAYDOVCHIDAN SOS / LOKATSIYA!</b>\n\n"
        f"👤 Haydovchi: <b>{user.get('full_name', 'Haydovchi')}</b> (<code>{user.get('position', 'N/A')}</code>)\n"
        f"📱 Telefon: <code>{user.get('phone', 'Noma\'lum')}</code>\n"
        f"🚗 Mashina: <b>{user.get('car_model', '')} ({user.get('car_number', '')})</b>\n\n"
        f"📍 <a href='{maps_url}'>Google Xaritada ochish</a>"
    )

    adm_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="💬 Haydovchi bilan chat", url=f"tg://user?id={uid}")]]
    )

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

    if message.text in ["❌ Bekor qilish", "❌ Отмена"]:
        await state.clear()
        await message.answer(t(lang, "action_cancelled"), reply_markup=user_main_kb(lang, uid))
        return

    await state.clear()
    user = await db_get_user(uid)
    address_text = message.text.strip()

    alert = (
        f"🚨 <b>DIQQAT: HAYDOVCHIDAN SOS / MANZIL (DESKTOP):</b>\n\n"
        f"👤 Haydovchi: <b>{user.get('full_name', 'Haydovchi')}</b> (<code>{user.get('position', 'N/A')}</code>)\n"
        f"📱 Telefon: <code>{user.get('phone', 'Noma\'lum')}</code>\n"
        f"🚗 Mashina: <b>{user.get('car_model', '')} ({user.get('car_number', '')})</b>\n\n"
        f"📍 <b>Manzil / Holat:</b>\n{address_text}"
    )

    adm_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="💬 Haydovchi bilan chat", url=f"tg://user?id={uid}")]]
    )

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

    if message.text in ["❌ Bekor qilish", "❌ Отмена"]:
        await state.clear()
        await message.answer(t(lang, "action_cancelled"), reply_markup=user_main_kb(lang, uid))
        return

    await state.clear()
    user = await db_get_user(uid)
    msg_body = message.text or "[Xabar]"

    alert = (
        f"📩 <b>HAYDOVCHIDAN MUROJAAT / XABAR:</b>\n\n"
        f"👤 Haydovchi: <b>{user.get('full_name', 'Haydovchi')}</b> (<code>{user.get('position', 'N/A')}</code>)\n"
        f"📱 Telefon: <code>{user.get('phone', 'Noma\'lum')}</code>\n"
        f"🚗 Mashina: <b>{user.get('car_model', '')} ({user.get('car_number', '')})</b>\n\n"
        f"✍️ <b>Xabar matni:</b>\n{msg_body}"
    )

    adm_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="💬 Javob yozish", url=f"tg://user?id={uid}")]]
    )

    for adm in ADMIN_IDS:
        try:
            await bot.send_message(adm, alert, reply_markup=adm_kb)
        except Exception:
            pass

    await message.answer(t(lang, "sos_sent"), reply_markup=user_main_kb(lang, uid))


# --- ADMIN PANEL ---

@admin_router.message(F.text.in_(["🛠 Admin Panel", "🛠 Админ Панель"]), StateFilter("*"))
async def admin_open(message: Message, state: FSMContext) -> None:
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    lang = await get_lang(message.from_user.id)
    await message.answer("🛠 <b>Admin Boshqaruv Paneli:</b>" if lang == "uz" else "🛠 <b>Панель Администратора:</b>", reply_markup=admin_main_kb(lang))


@admin_router.message(F.text.in_(["📊 Statistika", "📊 Статистика"]))
async def admin_stats_handler(message: Message) -> None:
    if message.from_user.id not in ADMIN_IDS:
        return
    stats = await db_get_stats()
    text = (
        f"📊 <b>{BOT_NAME} — Umumiy Tizim Statistikasi:</b>\n\n"
        f"👥 Botga kirgan jami foydalanuvchilar: <b>{stats['total_users']} ta</b>\n"
        f"🚕 Ro'yxatdan o'tgan haydovchilar: <b>{stats['registered_drivers']} ta</b>\n"
        f"🔗 Yandex Pro ulangan haydovchilar: <b>{stats['yandex_linked']} ta</b>\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"💸 Jami yechilgan mablag': <b>{fmt_sum(stats['total_withdrawn'])} so'm</b>\n"
        f"📈 Taksopark komissiyasi: <b>{fmt_sum(stats['total_comm'])} so'm</b>"
    )
    await message.answer(text)


@admin_router.message(F.text.in_(["📥 Excel Hisobot", "📥 Excel Отчет"]))
async def admin_export_excel(message: Message) -> None:
    if message.from_user.id not in ADMIN_IDS:
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
    if message.from_user.id not in ADMIN_IDS:
        return
    status_msg = await message.answer("⏳ <i>Yandex kabinetdagi barcha haydovchilar yuklanmoqda...</i>")
    drivers = await yandex_api.get_all_drivers(limit=1000)

    if not drivers:
        await status_msg.edit_text("❌ Yandex API dan ma'lumot olib bo'lmadi. API kalitlarini tekshiring.")
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


@admin_router.message(F.text.in_(["📢 Xabar tarqatish", "📢 Рассылка"]))
async def admin_broadcast_prompt(message: Message, state: FSMContext) -> None:
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminBroadcastStates.waiting_for_message)
    await message.answer("📢 <b>Barcha haydovchilarga yubormoqchi bo'lgan xabaringizni yozing:</b>\n\n<i>Bekor qilish: '❌ Bekor qilish'</i>", reply_markup=cancel_kb("uz"))


@admin_router.message(AdminBroadcastStates.waiting_for_message)
async def admin_broadcast_send(message: Message, state: FSMContext) -> None:
    if message.from_user.id not in ADMIN_IDS:
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
    if message.from_user.id not in ADMIN_IDS:
        return
    drivers = await db_get_all_registered_drivers()
    if not drivers:
        await message.answer("Hozircha ro'yxatdan o'tgan haydovchilar yo'q.")
        return

    text = f"👥 <b>Ro'yxatdan o'tgan so'nggi haydovchilar (Jami: {len(drivers)} ta):</b>\n\n"
    for drv in drivers[-10:]:
        text += (
            f"🆔 <code>{drv.get('position', 'N/A')}</code> — <b>{drv.get('full_name')}</b>\n"
            f"📱 {drv.get('phone')} | 🚗 {drv.get('car_model')} ({drv.get('car_number')})\n"
            f"💰 Balans: <b>{fmt_sum(drv.get('balance', 0))} so'm</b>\n---------------------------\n"
        )
    await message.answer(text)


@admin_router.message(F.text.in_(["🚫 Nofaollar", "🚫 Неактивные"]))
async def admin_inactive_drivers(message: Message) -> None:
    if message.from_user.id not in ADMIN_IDS:
        return
    ten_days_ago = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    if db_pool:
        async with db_pool.acquire() as conn:
            inactive = await conn.fetch("""
                SELECT position, full_name, phone, car_model, car_number, last_activity 
                FROM users 
                WHERE is_registered = 1 AND (last_activity < $1 OR is_blocked = 1)
                ORDER BY id DESC LIMIT 15
            """, ten_days_ago)
            inactive = [dict(r) for r in inactive]
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        inactive = conn.execute("""
            SELECT position, full_name, phone, car_model, car_number, last_activity 
            FROM users 
            WHERE is_registered = 1 AND (last_activity < ? OR is_blocked = 1)
            ORDER BY id DESC LIMIT 15
        """, (ten_days_ago,)).fetchall()
        conn.close()
        inactive = [dict(r) for r in inactive]

    if not inactive:
        await message.answer("✅ Barcha haydovchilar faol!")
        return

    text = f"🚫 <b>10+ kundan beri faol bo'lmagan haydovchilar ({len(inactive)} ta):</b>\n\n"
    for drv in inactive:
        text += (
            f"👤 <b>{drv.get('full_name')}</b> | 📱 Tel: {drv.get('phone')}\n"
            f"🚗 {drv.get('car_model')} ({drv.get('car_number')})\n"
            f"📅 Faollik: {str(drv.get('last_activity', 'Noma\'lum'))[:10]}\n---------------------------\n"
        )
    await message.answer(text)


@admin_router.message(F.text.in_(["⬅️ Asosiy menyu", "⬅️ Главное меню"]), StateFilter("*"))
async def back_to_user_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    lang = await get_lang(message.from_user.id)
    await message.answer("Asosiy menyu:" if lang == "uz" else "Главное меню:", reply_markup=user_main_kb(lang, message.from_user.id))


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
                    except Exception:
                        pass
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
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
