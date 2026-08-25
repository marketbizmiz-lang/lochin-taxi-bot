import os
import re
import math
import html
import asyncio
import logging
import sqlite3
import aiohttp
from pathlib import Path
from typing import Any, Optional, Dict, List
from datetime import datetime, timezone

from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

# ============================================================
# KONFIGURATSIYA VA SOZLAMALAR
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "lochin_taxi.db"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("lochin_taxi_bot")

# Bot va Server
BOT_TOKEN = os.getenv("BOT_TOKEN", "7963339189:AAFD_example").strip()  # O'zingizning bot tokeningiz
PORT = int(os.getenv("PORT", "8080"))  # Render uchun port
BOT_NAME = os.getenv("BOT_NAME", "LOCHIN TAXI").strip()

# Adminlar va Menejer (Skrindan olingan ID lar)
ADMIN_IDS = {8934129079, 8956429378}
env_admins = os.getenv("ADMIN_IDS", "")
if env_admins:
    for adm in env_admins.split(","):
        if adm.strip().isdigit():
            ADMIN_IDS.add(int(adm.strip()))

# Aloqa va Guruh ma'lumotlari (Skrindan olingan)
SUPPORT_PHONE = os.getenv("SUPPORT_PHONE", "+998913773200").strip()
SUPPORT_PHONE_DISPLAY = "+998 91 377 32 00"
SUPPORT_TG = os.getenv("SUPPORT_TG", "@lochin_support").strip()
DRIVER_GROUP_LINK = os.getenv("DRIVER_GROUP_LINK", "https://t.me/+vLyCiiXNvB5kMTUy").strip()

# Yandex Fleet API
YANDEX_API_KEY = os.getenv("YANDEX_API_KEY", "").strip()
YANDEX_CLIENT_ID = os.getenv("YANDEX_CLIENT_ID", "").strip()
YANDEX_PARK_ID = os.getenv("YANDEX_PARK_ID", "").strip()
YANDEX_FLEET_URL = "https://fleet-api.yandex.ru/v1"

# Pul yechish sozlamalari
MIN_WITHDRAWAL = int(os.getenv("MIN_WITHDRAWAL", "30000"))
COMMISSION_PERCENT = float(os.getenv("COMMISSION_PERCENT", "2.0"))


# ============================================================
# YANDEX FLEET API KLIENTI
# ============================================================

class YandexFleetAPI:
    def __init__(self, api_key: str, client_id: str, park_id: str):
        self.api_key = api_key
        self.client_id = client_id
        self.park_id = park_id
        self.base_url = YANDEX_FLEET_URL

    @property
    def headers(self) -> dict:
        return {
            "X-Client-ID": self.client_id,
            "X-API-Key": self.api_key,
            "X-Park-ID": self.park_id,
            "Content-Type": "application/json",
            "Accept-Language": "ru",
        }

    async def get_driver_by_phone(self, phone: str) -> Optional[dict]:
        if not self.api_key or not self.park_id:
            return None
        url = f"{self.base_url}/parks/driver-profiles/list"
        clean_phone = phone.replace("+", "").strip()
        payload = {
            "query": {
                "park": {"id": self.park_id},
                "driver": {"phone": [clean_phone, f"+{clean_phone}"]}
            },
            "limit": 1
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=self.headers, json=payload, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        drivers = data.get("driver_profiles", [])
                        return drivers[0] if drivers else None
        except Exception as e:
            logger.error(f"Yandex API xatolik: {e}")
        return None

    async def get_driver_balance(self, yandex_driver_id: str) -> Optional[float]:
        if not self.api_key or not self.park_id:
            return None
        url = f"{self.base_url}/parks/driver-profiles/list"
        payload = {
            "query": {
                "park": {"id": self.park_id},
                "driver": {"id": [yandex_driver_id]}
            },
            "limit": 1
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=self.headers, json=payload, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        drivers = data.get("driver_profiles", [])
                        if drivers:
                            accounts = drivers[0].get("accounts", [])
                            if accounts:
                                return float(accounts[0].get("balance", 0.0))
        except Exception as e:
            logger.error(f"Yandex balansni olishda xatolik: {e}")
        return None

    async def create_transaction(self, yandex_driver_id: str, amount: float, description: str) -> bool:
        if not self.api_key or not self.park_id:
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
                async with session.post(url, headers=self.headers, json=payload, timeout=10) as resp:
                    return resp.status == 200
        except Exception as e:
            logger.error(f"Yandex tranzaksiya yaratishda xatolik: {e}")
            return False

yandex_api = YandexFleetAPI(YANDEX_API_KEY, YANDEX_CLIENT_ID, YANDEX_PARK_ID)


# ============================================================
# MA'LUMOTLAR BAZASI
# ============================================================

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def init_db() -> None:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
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
            balance REAL DEFAULT 0,
            blocked_balance REAL DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            is_blocked INTEGER DEFAULT 0,
            is_registered INTEGER DEFAULT 0,
            yandex_driver_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            commission REAL DEFAULT 0,
            net_amount REAL NOT NULL,
            card_number TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            admin_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    conn.commit()
    conn.close()
    logger.info("Baza muvaffaqiyatli ishga tushdi!")

init_db()

def fmt_sum(val: float) -> str:
    return f"{int(val):,}".replace(",", " ")


# ============================================================
# TUGMALAR (KEYBOARDS)
# ============================================================

def user_main_menu_kb(telegram_id: int) -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton(text="💰 Balans"), KeyboardButton(text="📊 Bugungi buyurtmalar")],
        [KeyboardButton(text="💸 Pul yechish"), KeyboardButton(text="👤 Profil")],
        [KeyboardButton(text="📢 Yangiliklar / Guruh"), KeyboardButton(text="🆘 Yordam")],
    ]
    if telegram_id in ADMIN_IDS:
        buttons.append([KeyboardButton(text="🛠 Admin Panel")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def cancel_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Bekor qilish")]],
        resize_keyboard=True
    )

def phone_request_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Telefon raqamni yuborish", request_contact=True)],
            [KeyboardButton(text="❌ Bekor qilish")]
        ],
        resize_keyboard=True
    )

def admin_main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Statistika"), KeyboardButton(text="💸 Pul yechish so'rovlari")],
            [KeyboardButton(text="📢 Xabar yuborish (Hammaga)"), KeyboardButton(text="👥 Haydovchilar ro'yxati")],
            [KeyboardButton(text="🔍 Haydovchi qidirish"), KeyboardButton(text="⬅️ Asosiy menyu")],
        ],
        resize_keyboard=True
    )


# ============================================================
# FSM STATES
# ============================================================

class RegStates(StatesGroup):
    name = State()
    phone = State()
    card = State()
    car_model = State()
    car_number = State()

class WithdrawStates(StatesGroup):
    amount = State()
    confirm = State()

class AdminBroadcast(StatesGroup):
    message = State()

class AdminSearch(StatesGroup):
    query = State()


# ============================================================
# BOT HANDLERLARI
# ============================================================

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
router = Router()
admin_router = Router()


# --- START VA RO'YXATDAN O'TISH ---

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    uid = message.from_user.id
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (uid,)).fetchone()

    if not user:
        now = utc_now_iso()
        conn.execute(
            "INSERT INTO users (telegram_id, username, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (uid, message.from_user.username or "", now, now)
        )
        conn.commit()
        user = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (uid,)).fetchone()
    conn.close()

    if user["is_registered"] == 1:
        await message.answer(
            f"✅ <b>Siz tizimda ro'yxatdan o'tgansiz!</b>\n\n"
            f"🆔 POSITION: <code>{user['position']}</code>\n"
            f"👤 Haydovchi: <b>{user['full_name']}</b>",
            reply_markup=user_main_menu_kb(uid)
        )
        return

    # Yangi foydalanuvchi uchun start
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📝 Ro'yxatdan o'tish")]],
        resize_keyboard=True
    )
    await message.answer(
        f"🚕 <b>{BOT_NAME} ga xush kelibsiz!</b>\n\n"
        f"Ishni boshlash uchun quyidagi tugmani bosib ro'yxatdan o'ting:",
        reply_markup=kb
    )


@router.message(F.text == "📝 Ro'yxatdan o'tish")
async def start_reg(message: Message, state: FSMContext) -> None:
    await state.set_state(RegStates.name)
    await message.answer(
        "👤 <b>Ism va familiyangizni kiriting:</b>\n\n<i>Misol: Aliyev Alisher</i>",
        reply_markup=cancel_kb()
    )


@router.message(RegStates.name)
async def reg_name_step(message: Message, state: FSMContext) -> None:
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("❌ Amal bekor qilindi.", reply_markup=user_main_menu_kb(message.from_user.id))
        return

    name = message.text.strip()
    if len(name) < 3:
        await message.answer("⚠️ Iltimos, ism va familiyangizni to'liq kiriting:")
        return

    await state.update_data(full_name=name)
    await state.set_state(RegStates.phone)
    await message.answer(
        "📱 <b>Telefon raqamingizni yuboring:</b>\n\n"
        "Quyidagi <b>[📱 Telefon raqamni yuborish]</b> tugmasini bosing yoki qo'lda yozing (Masalan: <i>+998901234567</i>):",
        reply_markup=phone_request_kb()
    )


@router.message(RegStates.phone)
async def reg_phone_step(message: Message, state: FSMContext) -> None:
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("❌ Amal bekor qilindi.", reply_markup=user_main_menu_kb(message.from_user.id))
        return

    if message.contact:
        phone = message.contact.phone_number
    else:
        phone = message.text.strip().replace(" ", "").replace("-", "")

    if not phone.startswith("+"):
        phone = "+" + phone

    if not re.fullmatch(r"\+998\d{9}", phone):
        await message.answer("⚠️ Telefon raqam formati noto'g'ri. Misol: <i>+998901234567</i>")
        return

    await state.update_data(phone=phone)
    await state.set_state(RegStates.card)
    await message.answer(
        "💳 <b>Karta raqamingizni kiriting:</b>\n\n"
        "<i>Misol: 8600 1234 5678 9012 yoki 9860123456789012</i>",
        reply_markup=cancel_kb()
    )


@router.message(RegStates.card)
async def reg_card_step(message: Message, state: FSMContext) -> None:
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("❌ Amal bekor qilindi.", reply_markup=user_main_menu_kb(message.from_user.id))
        return

    card = message.text.strip().replace(" ", "")
    if not (card.isdigit() and len(card) == 16):
        await message.answer("⚠️ Karta raqam 16 ta raqamdan iborat bo'lishi kerak. Qaytadan kiriting:")
        return

    await state.update_data(card_number=card)
    await state.set_state(RegStates.car_model)
    await message.answer(
        "🚗 <b>Avtomobilingiz markasini kiriting:</b>\n\n<i>Misol: Chevrolet Cobalt</i>",
        reply_markup=cancel_kb()
    )


@router.message(RegStates.car_model)
async def reg_car_model_step(message: Message, state: FSMContext) -> None:
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("❌ Amal bekor qilindi.", reply_markup=user_main_menu_kb(message.from_user.id))
        return

    await state.update_data(car_model=message.text.strip())
    await state.set_state(RegStates.car_number)
    await message.answer(
        "🔢 <b>Avtomobilingiz davlat raqamini kiriting:</b>\n\n<i>Misol: 01 A 123 AA</i>",
        reply_markup=cancel_kb()
    )


@router.message(RegStates.car_number)
async def reg_car_num_step(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("❌ Amal bekor qilindi.", reply_markup=user_main_menu_kb(uid))
        return

    car_number = message.text.strip().upper()
    data = await state.get_data()
    await state.clear()

    # Unique POSITION yaratish
    import random
    position = f"LCH-{random.randint(1000, 9999)}"

    # Yandex Pro da haydovchini tekshirish
    yandex_driver = await yandex_api.get_driver_by_phone(data["phone"])
    yandex_driver_id = yandex_driver.get("driver_profile", {}).get("id") if yandex_driver else None

    now = utc_now_iso()
    conn = get_db()
    conn.execute("""
        UPDATE users SET 
            full_name = ?,
            phone = ?,
            card_number = ?,
            car_model = ?,
            car_number = ?,
            position = ?,
            yandex_driver_id = ?,
            is_registered = 1,
            updated_at = ?
        WHERE telegram_id = ?
    """, (data["full_name"], data["phone"], data["card_number"], data["car_model"], car_number, position, yandex_driver_id, now, uid))
    conn.commit()
    conn.close()

    await message.answer(
        f"✅ <b>Tabriklaymiz!</b>\n\n"
        f"Siz {BOT_NAME} tizimida muvaffaqiyatli ro'yxatdan o'tdingiz!\n\n"
        f"🆔 Sizning POSITIONingiz: <code>{position}</code>\n"
        f"🔑 Bu sizning taksoparkdagi shaxsiy kodingiz.",
        reply_markup=user_main_menu_kb(uid)
    )

    # Adminlarga yangi haydovchi xabarini yuborish
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"🆕 <b>Yangi haydovchi qo'shildi!</b>\n\n"
                f"👤 Ism: <b>{data['full_name']}</b>\n"
                f"📱 Telefon: <code>{data['phone']}</code>\n"
                f"🚗 Mashina: <b>{data['car_model']} ({car_number})</b>\n"
                f"💳 Karta: <code>{data['card_number']}</code>\n"
                f"🆔 POSITION: <code>{position}</code>\n"
                f"🚕 Yandex: <b>{'✅ Ulangan' if yandex_driver_id else '⚠️ Ulunmagan'}</b>"
            )
        except Exception:
            pass


# --- MENYU TUGMALARI ---

@router.message(F.text == "💰 Balans")
async def show_balance(message: Message) -> None:
    uid = message.from_user.id
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (uid,)).fetchone()
    conn.close()

    if not user or user["is_registered"] != 1:
        await message.answer("Avval ro'yxatdan o'ting: /start bosing.")
        return

    # Yandexdagi balansni sinxronlashtirish
    y_bal_text = "Ulanmagan"
    cur_bal = user["balance"]
    if user["yandex_driver_id"]:
        live_bal = await yandex_api.get_driver_balance(user["yandex_driver_id"])
        if live_bal is not None:
            cur_bal = live_bal
            y_bal_text = f"{fmt_sum(live_bal)} so'm"
            conn = get_db()
            conn.execute("UPDATE users SET balance = ? WHERE telegram_id = ?", (live_bal, uid))
            conn.commit()
            conn.close()

    avail = max(0.0, cur_bal - user["blocked_balance"])
    await message.answer(
        f"💰 <b>{BOT_NAME} da balans:</b>\n\n"
        f"💵 Asosiy balans: <b>{fmt_sum(cur_bal)} so'm</b>\n"
        f"🔒 Muzlatilgan (so'rovda): <b>{fmt_sum(user['blocked_balance'])} so'm</b>\n"
        f"✅ Yechish mumkin: <b>{fmt_sum(avail)} so'm</b>\n\n"
        f"🚕 Yandex Pro balansi: <b>{y_bal_text}</b>",
        reply_markup=user_main_menu_kb(uid)
    )


@router.message(F.text == "📊 Bugungi buyurtmalar")
async def show_today_orders(message: Message) -> None:
    uid = message.from_user.id
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (uid,)).fetchone()
    conn.close()

    if not user or user["is_registered"] != 1:
        await message.answer("Avval ro'yxatdan o'ting: /start bosing.")
        return

    await message.answer(
        f"📊 <b>Bugungi buyurtmalar</b>\n\n"
        f"🚕 Bugun bajarilgan safarlar Yandex Pro ilovangiz orqali to'g'ridan-to'g'ri hisoblab boriladi.\n\n"
        f"💰 Umumiy tushum balansingizda avtomatik yangilanadi.",
        reply_markup=user_main_menu_kb(uid)
    )


@router.message(F.text == "👤 Profil")
async def show_profile(message: Message) -> None:
    uid = message.from_user.id
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (uid,)).fetchone()
    conn.close()

    if not user or user["is_registered"] != 1:
        await message.answer("Avval ro'yxatdan o'ting: /start bosing.")
        return

    await message.answer(
        f"👤 <b>Mening profilim</b>\n\n"
        f"🆔 POSITION: <code>{user['position']}</code>\n"
        f"🆔 Telegram ID: <code>{user['telegram_id']}</code>\n"
        f"👤 Ism: <b>{user['full_name']}</b>\n"
        f"📱 Telefon: <b>{user['phone']}</b>\n"
        f"🚗 Mashina: <b>{user['car_model']} ({user['car_number']})</b>\n"
        f"💳 Karta: <code>{user['card_number']}</code>\n"
        f"🚕 Yandex ID: <code>{user['yandex_driver_id'] or 'Ulanmagan'}</code>\n"
        f"📅 Qo'shilgan: <b>{user['created_at'][:10]}</b>",
        reply_markup=user_main_menu_kb(uid)
    )


@router.message(F.text == "📢 Yangiliklar / Guruh")
async def news_and_group(message: Message) -> None:
    inline_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💬 Haydovchilar guruhiga qo'shilish", url=DRIVER_GROUP_LINK)]
        ]
    )
    await message.answer(
        f"📢 <b>{BOT_NAME} Haydovchilar Guruhi</b>\n\n"
        f"Barcha eng so'nggi yangiliklar, e'lonlar va haydovchilar bilan jonli muloqot bizning rasmiy guruhimizda!\n\n"
        f"Guruhga qo'shilish uchun quyidagi tugmani bosing 👇",
        reply_markup=inline_kb
    )


@router.message(F.text == "🆘 Yordam")
async def help_and_sos(message: Message) -> None:
    inline_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💬 Menejerga yozish", url=f"https://t.me/{SUPPORT_TG.replace('@', '')}")],
            [InlineKeyboardButton(text="👥 Haydovchilar Guruhi", url=DRIVER_GROUP_LINK)]
        ]
    )
    await message.answer(
        f"🆘 <b>Yordam va Aloqa Markazi:</b>\n\n"
        f"📞 <b>Menejer telefoni:</b> <a href='tel:{SUPPORT_PHONE}'>{SUPPORT_PHONE_DISPLAY}</a>\n"
        f"💬 <b>Telegram:</b> {SUPPORT_TG}\n"
        f"🚕 <b>Taksopark:</b> {BOT_NAME}\n\n"
        f"Savol yoki muammolar bo'lsa, bemalol bog'lanishingiz mumkin!",
        reply_markup=inline_kb
    )


# --- PUL YECHISH (WITHDRAWAL) ---

@router.message(F.text == "💸 Pul yechish")
async def withdraw_init(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (uid,)).fetchone()
    conn.close()

    if not user or user["is_registered"] != 1:
        await message.answer("Avval ro'yxatdan o'ting: /start bosing.")
        return

    avail = max(0.0, user["balance"] - user["blocked_balance"])
    if avail < MIN_WITHDRAWAL:
        await message.answer(
            f"❌ <b>Balansingizda mablag' yetarli emas!</b>\n\n"
            f"🔹 Minimal yechish miqdori: <b>{fmt_sum(MIN_WITHDRAWAL)} so'm</b>\n"
            f"🔹 Sizning balansingiz: <b>{fmt_sum(avail)} so'm</b>"
        )
        return

    await state.set_state(WithdrawStates.amount)
    await message.answer(
        f"💸 <b>{BOT_NAME} dan pul yechish</b>\n\n"
        f"🔹 Yechish mumkin: <b>{fmt_sum(avail)} so'm</b>\n"
        f"🔹 Minimal summa: <b>{fmt_sum(MIN_WITHDRAWAL)} so'm</b>\n"
        f"🔹 Komissiya: <b>{COMMISSION_PERCENT}%</b>\n\n"
        f"Yechmoqchi bo'lgan summani kiriting (Masalan: <i>50000</i>):",
        reply_markup=cancel_kb()
    )


@router.message(WithdrawStates.amount)
async def withdraw_amount_input(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("❌ Pul yechish bekor qilindi.", reply_markup=user_main_menu_kb(uid))
        return

    raw = message.text.replace(" ", "").replace("so'm", "").strip()
    if not raw.isdigit():
        await message.answer("⚠️ Iltimos, faqat musbat son kiriting (Masalan: 50000):")
        return

    amount = float(raw)
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (uid,)).fetchone()
    conn.close()

    avail = max(0.0, user["balance"] - user["blocked_balance"])

    if amount < MIN_WITHDRAWAL:
        await message.answer(f"⚠️ Minimal yechish summasi: {fmt_sum(MIN_WITHDRAWAL)} so'm")
        return

    if amount > avail:
        await message.answer("❌ Balansingizda buncha mablag' mavjud emas!")
        return

    commission = amount * (COMMISSION_PERCENT / 100.0)
    net_amount = amount - commission

    await state.update_data(amount=amount, commission=commission, net_amount=net_amount, card=user["card_number"])
    await state.set_state(WithdrawStates.confirm)

    confirm_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Tasdiqlash", callback_data="wd_c:yes"),
                InlineKeyboardButton(text="❌ Bekor qilish", callback_data="wd_c:no")
            ]
        ]
    )

    await message.answer(
        f"💸 <b>Pul yechishni tasdiqlaysizmi?</b>\n\n"
        f"💰 Yechilayotgan summa: <b>{fmt_sum(amount)} so'm</b>\n"
        f"📊 Komissiya ({COMMISSION_PERCENT}%): <b>{fmt_sum(commission)} so'm</b>\n"
        f"💳 Kartaga tushadi: <b>{fmt_sum(net_amount)} so'm</b>\n"
        f"💳 Karta raqami: <code>{user['card_number']}</code>",
        reply_markup=confirm_kb
    )


@router.callback_query(F.data.startswith("wd_c:"), WithdrawStates.confirm)
async def withdraw_confirm_cb(callback: CallbackQuery, state: FSMContext) -> None:
    uid = callback.from_user.id
    action = callback.data.split(":")[1]

    if action == "no":
        await state.clear()
        await callback.message.edit_text("❌ Pul yechish bekor qilindi.")
        await callback.answer()
        return

    data = await state.get_data()
    amount = data["amount"]
    commission = data["commission"]
    net_amount = data["net_amount"]
    card = data["card"]
    await state.clear()

    now = utc_now_iso()
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (uid,)).fetchone()

    # Muzlatish
    conn.execute(
        "UPDATE users SET blocked_balance = blocked_balance + ?, updated_at = ? WHERE id = ?",
        (amount, now, user["id"])
    )

    cur = conn.cursor()
    cur.execute("""
        INSERT INTO withdrawals (user_id, amount, commission, net_amount, card_number, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
    """, (user["id"], amount, commission, net_amount, card, now, now))
    w_id = cur.lastrowid
    conn.commit()
    conn.close()

    await callback.message.edit_text("✅ <b>Arizangiz qabul qilindi!</b>\nTez orada admin ko'rib chiqib, pulni kartangizga o'tkazadi.")
    await callback.answer()

    # Adminga Tasdiqlash / Rad etish tugmali ariza yuborish
    adm_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ To'landi (Tasdiqlash)", callback_data=f"adm_app:{w_id}"),
                InlineKeyboardButton(text="❌ Rad etish", callback_data=f"adm_rej:{w_id}")
            ]
        ]
    )

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"🔔 <b>Yangi Pul Yechish Arizasi #{w_id}</b>\n\n"
                f"👤 Haydovchi: <b>{user['full_name']}</b> (<code>{user['position']}</code>)\n"
                f"📱 Telefon: <code>{user['phone']}</code>\n"
                f"💳 Karta: <code>{card}</code>\n"
                f"💰 Summa: <b>{fmt_sum(amount)} so'm</b>\n"
                f"💵 To'lanadigan summa: <b>{fmt_sum(net_amount)} so'm</b>",
                reply_markup=adm_kb
            )
        except Exception:
            pass


# --- ADMIN PANEL ---

@admin_router.message(F.text == "🛠 Admin Panel")
async def admin_open(message: Message) -> None:
    if message.from_user.id not in ADMIN_IDS:
        return
    await message.answer("🛠 <b>Admin Boshqaruv Paneli:</b>", reply_markup=admin_main_menu_kb())


@admin_router.message(F.text == "📊 Statistika")
async def admin_stats(message: Message) -> None:
    if message.from_user.id not in ADMIN_IDS:
        return

    conn = get_db()
    total_drivers = conn.execute("SELECT COUNT(*) FROM users WHERE is_registered = 1").fetchone()[0]
    pending_w = conn.execute("SELECT COUNT(*) FROM withdrawals WHERE status = 'pending'").fetchone()[0]
    total_paid = conn.execute("SELECT SUM(amount) FROM withdrawals WHERE status = 'completed'").fetchone()[0] or 0
    conn.close()

    await message.answer(
        f"📊 <b>{BOT_NAME} Statistikasi:</b>\n\n"
        f"👥 Ro'yxatdan o'tgan haydovchilar: <b>{total_drivers} ta</b>\n"
        f"⏳ Kutilayotgan arizalar: <b>{pending_w} ta</b>\n"
        f"💸 Jami to'lab berilgan mablag': <b>{fmt_sum(total_paid)} so'm</b>"
    )


@admin_router.message(F.text == "👥 Haydovchilar ro'yxati")
async def admin_drivers_list(message: Message) -> None:
    if message.from_user.id not in ADMIN_IDS:
        return

    conn = get_db()
    drivers = conn.execute("SELECT * FROM users WHERE is_registered = 1 ORDER BY id DESC LIMIT 15").fetchall()
    conn.close()

    if not drivers:
        await message.answer("Hozircha ro'yxatdan o'tgan haydovchilar yo'q.")
        return

    text = f"👥 <b>So'nggi 15 ta haydovchi:</b>\n\n"
    for d in drivers:
        text += (
            f"🆔 <code>{d['position']}</code> | <b>{d['full_name']}</b>\n"
            f"📱 Tel: {d['phone']} | 🚗 {d['car_model']} ({d['car_number']})\n"
            f"💰 Balans: {fmt_sum(d['balance'])} so'm\n"
            f"---------------------------\n"
        )
    await message.answer(text)


@admin_router.callback_query(F.data.startswith("adm_app:"))
async def admin_approve_wd(callback: CallbackQuery) -> None:
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return

    w_id = int(callback.data.split(":")[1])
    now = utc_now_iso()

    conn = get_db()
    w = conn.execute("SELECT * FROM withdrawals WHERE id = ?", (w_id,)).fetchone()
    if not w or w["status"] != "pending":
        conn.close()
        await callback.answer("Bu ariza allaqachon bajarilgan!", show_alert=True)
        return

    user = conn.execute("SELECT * FROM users WHERE id = ?", (w["user_id"],)).fetchone()

    # Balansdan yechish
    conn.execute("""
        UPDATE users SET 
            balance = balance - ?, 
            blocked_balance = blocked_balance - ?, 
            updated_at = ? 
        WHERE id = ?
    """, (w["amount"], w["amount"], now, user["id"]))

    conn.execute("UPDATE withdrawals SET status = 'completed', admin_id = ?, updated_at = ? WHERE id = ?",
                 (callback.from_user.id, now, w_id))
    conn.commit()

    if user["yandex_driver_id"]:
        await yandex_api.create_transaction(
            user["yandex_driver_id"],
            w["amount"],
            f"Pul yechish #{w_id} karta {user['card_number']}"
        )
    conn.close()

    await callback.message.edit_text(callback.message.text + f"\n\n✅ <b>ADMIN ({callback.from_user.full_name}) TOMONIDAN TO'LANDI!</b>")
    try:
        await bot.send_message(
            user["telegram_id"],
            f"✅ <b>Pul yechish arizangiz bajarildi!</b>\n\n"
            f"💰 Summa: <b>{fmt_sum(w['net_amount'])} so'm</b>\n"
            f"💳 Karta: <code>{w['card_number']}</code> ga muvaffaqiyatli o'tkazildi."
        )
    except Exception:
        pass
    await callback.answer()


@admin_router.callback_query(F.data.startswith("adm_rej:"))
async def admin_reject_wd(callback: CallbackQuery) -> None:
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Ruxsat yo'q!", show_alert=True)
        return

    w_id = int(callback.data.split(":")[1])
    now = utc_now_iso()

    conn = get_db()
    w = conn.execute("SELECT * FROM withdrawals WHERE id = ?", (w_id,)).fetchone()
    if not w or w["status"] != "pending":
        conn.close()
        await callback.answer("Bu ariza allaqachon ko'rib chiqilgan!", show_alert=True)
        return

    user = conn.execute("SELECT * FROM users WHERE id = ?", (w["user_id"],)).fetchone()

    conn.execute("""
        UPDATE users SET 
            blocked_balance = blocked_balance - ?, 
            updated_at = ? 
        WHERE id = ?
    """, (w["amount"], now, user["id"]))

    conn.execute("UPDATE withdrawals SET status = 'cancelled', admin_id = ?, updated_at = ? WHERE id = ?",
                 (callback.from_user.id, now, w_id))
    conn.commit()
    conn.close()

    await callback.message.edit_text(callback.message.text + f"\n\n❌ <b>ADMIN ({callback.from_user.full_name}) TOMONIDAN RAD ETILDI!</b>")
    try:
        await bot.send_message(
            user["telegram_id"],
            f"❌ <b>Pul yechish so'rovingiz rad etildi!</b>\n\n"
            f"Mablag' ({fmt_sum(w['amount'])} so'm) balansingizga qaytarildi."
        )
    except Exception:
        pass
    await callback.answer()


# --- BROADCAST (HAMMAGA XABAR) ---

@admin_router.message(F.text == "📢 Xabar yuborish (Hammaga)")
async def broadcast_prompt(message: Message, state: FSMContext) -> None:
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminBroadcast.message)
    await message.answer("📢 Barcha haydovchilarga yubormoqchi bo'lgan xabaringizni yozing (rasm, video yoki matn):", reply_markup=cancel_kb())


@admin_router.message(AdminBroadcast.message)
async def broadcast_send(message: Message, state: FSMContext) -> None:
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=admin_main_menu_kb())
        return

    await state.clear()
    conn = get_db()
    users = conn.execute("SELECT telegram_id FROM users WHERE is_registered = 1").fetchall()
    conn.close()

    sent = 0
    await message.answer(f"⏳ {len(users)} ta haydovchiga xabar yuborilmoqda...")
    for u in users:
        try:
            await message.copy_to(chat_id=u["telegram_id"])
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass
    await message.answer(f"✅ Xabar muvaffaqiyatli {sent} ta haydovchiga yetkazildi!", reply_markup=admin_main_menu_kb())


@admin_router.message(F.text == "⬅️ Asosiy menyu")
async def back_to_user_menu(message: Message) -> None:
    await message.answer("Asosiy menyuga qaytdingiz:", reply_markup=user_main_menu_kb(message.from_user.id))


# ============================================================
# RENDER & UPTIMEROBOT UCHUN VEB SERVER
# ============================================================

routes = web.RouteTableDef()

@routes.get("/")
@routes.get("/health")
async def health_check(request: web.Request) -> web.Response:
    return web.Response(text="LOCHIN TAXI BOT IS RUNNING 24/7", status=200)

async def start_web_server():
    app = web.Application()
    app.add_routes(routes)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"🌐 Veb-server port {PORT} da ishga tushdi (Render & UptimeRobot uchun).")


# ============================================================
# ISHGA TUSHIRISH (MAIN)
# ============================================================

async def main() -> None:
    dp.include_router(admin_router)
    dp.include_router(router)

    # Veb serverni orqa fonda yoqish
    await start_web_server()

    logger.info(f"🚕 {BOT_NAME} Polling orqali ishga tushdi!")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
