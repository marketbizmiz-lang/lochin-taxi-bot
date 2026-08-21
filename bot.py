import os
import re
import json
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
from aiogram.types import (
    Message,
    CallbackQuery,
    KeyboardButton,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

# ============================================================
# CONFIG
# ============================================================
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "lochin_taxi.db"

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger("lochin_taxi_bot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
PORT = int(os.getenv("PORT", "8080"))
BOT_NAME = os.getenv("BOT_NAME", "LOCHIN TAXI").strip() or "LOCHIN TAXI"
DEFAULT_LANGUAGE = os.getenv("DEFAULT_LANGUAGE", "uz").strip() or "uz"

ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "").strip()
ADMIN_IDS = {int(x.strip()) for x in ADMIN_IDS_RAW.split(",") if x.strip().lstrip("-").isdigit()}

# Яндекс Такси Созламалари
YANDEX_API_KEY = os.getenv("YANDEX_API_KEY", "").strip()
YANDEX_PARK_ID = os.getenv("YANDEX_PARK_ID", "").strip()

# Линклар
DRIVER_GROUP_LINK = os.getenv("DRIVER_GROUP_LINK", "https://t.me/+LyCliXNvB5kMTUy").strip()
SUPPORT_PHONE = os.getenv("SUPPORT_PHONE", "+998913773200").strip()
SUPPORT_TG = os.getenv("SUPPORT_TG", "@lochin_support").strip()

MIN_WITHDRAWAL = int(os.getenv("MIN_WITHDRAWAL", "50000"))
COMMISSION_PERCENT = float(os.getenv("COMMISSION_PERCENT", "2.5"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")


# ============================================================
# BOT & ROUTERS
# ============================================================
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
driver_router = Router()
admin_router = Router()
web_router = web.RouteTableDef()


# ============================================================
# DATABASE (База)
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
            language TEXT NOT NULL DEFAULT 'uz',
            role TEXT NOT NULL DEFAULT 'driver',
            balance REAL DEFAULT 0,
            blocked_balance REAL DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            is_blocked INTEGER DEFAULT 0,
            is_registered INTEGER DEFAULT 0,
            total_orders INTEGER DEFAULT 0,
            total_earnings REAL DEFAULT 0,
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
            payment_type TEXT NOT NULL,
            card_number TEXT,
            phone_number TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            completed_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            driver_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            payment_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'completed',
            created_at TEXT NOT NULL,
            FOREIGN KEY (driver_id) REFERENCES users(id)
        )
    """)
    
    conn.commit()
    conn.close()
    logger.info("Database initialized")


# ============================================================
# YANDEX FLEET API INTEGRATION (Яндекс билан ишлаш)
# ============================================================
async def yandex_api_request(endpoint: str, method: str = "GET", payload: dict = None) -> dict:
    """Яндекс Такси API га сўров юборадиган функция."""
    if not YANDEX_API_KEY or not YANDEX_PARK_ID:
        return {"error": "Yandex API not configured"}
    
    url = f"https://fleet-api.taxi.yandex.net/v1/parks/{endpoint}"
    headers = {
        "X-API-Key": YANDEX_API_KEY,
        "Content-Type": "application/json",
        "Accept-Language": "ru"
    }
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.request(method, url, headers=headers, json=payload) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    text = await resp.text()
                    logger.error(f"Yandex API Error {resp.status}: {text}")
                    return {"error": f"Yandex API Error {resp.status}"}
        except Exception as e:
            logger.error(f"Yandex API Exception: {e}")
            return {"error": str(e)}


# ============================================================
# HELPER FUNCTIONS (Ёрдамчи функциялар)
# ============================================================
def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except:
        return default

def get_user_lang(telegram_id: int) -> str:
    conn = get_db()
    row = conn.execute("SELECT language FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
    conn.close()
    return row["language"] if row else DEFAULT_LANGUAGE

def get_user_by_telegram(telegram_id: int) -> Optional[dict]:
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def get_driver_by_id(driver_id: int) -> Optional[dict]:
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (driver_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def upsert_user(telegram_id: int, username: str, full_name: str) -> dict:
    now = utc_now_iso()
    conn = get_db()
    existing = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
    if existing:
        conn.execute("UPDATE users SET username=?, full_name=?, updated_at=? WHERE telegram_id=?", (username, full_name, now, telegram_id))
        conn.commit()
        user = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
        conn.close()
        return dict(user)
    
    conn.execute("INSERT INTO users (telegram_id, username, full_name, language, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                 (telegram_id, username, full_name, DEFAULT_LANGUAGE, now, now))
    conn.commit()
    user = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
    conn.close()
    return dict(user)

def complete_registration(telegram_id: int, full_name: str, phone: str, card_number: str, car_model: str, car_number: str) -> Optional[str]:
    import random, string
    conn = get_db()
    existing = conn.execute("SELECT is_registered FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
    if existing and existing["is_registered"] == 1:
        conn.close()
        return None
    
    # Уникал POSITION яратиш
    chars = string.ascii_uppercase + string.digits
    code = ''.join(random.choices(chars, k=5))
    position = f"LCH-{code}"
    while conn.execute("SELECT id FROM users WHERE position = ?", (position,)).fetchone():
        code = ''.join(random.choices(chars, k=5))
        position = f"LCH-{code}"
    
    now = utc_now_iso()
    conn.execute("""
        UPDATE users SET full_name=?, phone=?, card_number=?, car_model=?, car_number=?, position=?, is_registered=1, updated_at=?, 
        WHERE telegram_id=?
    """, (full_name, phone, card_number, car_model, car_number, position, now, telegram_id))
    conn.commit()
    conn.close()
    return position

def create_withdrawal(telegram_id: int, amount: float, payment_type: str, card_number: str = "", phone_number: str = "") -> Optional[int]:
    conn = get_db()
    cur = conn.cursor()
    user_row = cur.execute("SELECT id, balance FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
    if not user_row or user_row["balance"] < amount:
        conn.close()
        return None
    
    user_id = user_row["id"]
    commission = amount * (COMMISSION_PERCENT / 100)
    net_amount = amount - commission
    now = utc_now_iso()
    
    cur.execute("""
        INSERT INTO withdrawals (user_id, amount, commission, net_amount, payment_type, card_number, phone_number, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
    """, (user_id, amount, commission, net_amount, payment_type, card_number, phone_number, now))
    withdrawal_id = cur.lastrowid
    
    cur.execute("UPDATE users SET balance = balance - ?, blocked_balance = blocked_balance + ?, updated_at = ? WHERE id = ?",
                (amount, amount, now, user_id))
    conn.commit()
    conn.close()
    return withdrawal_id

def get_all_drivers() -> List[dict]:
    conn = get_db()
    rows = conn.execute("SELECT * FROM users WHERE role='driver' AND is_registered=1 ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(row) for row in rows]


# ============================================================
# FSM STATES
# ============================================================
class RegisterStates(StatesGroup):
    name = State()
    phone = State()
    card = State()
    car_model = State()
    car_number = State()

class WithdrawStates(StatesGroup):
    amount = State()
    payment_type = State()
    card_number = State()
    phone_number = State()

class AdminAddDriverState(StatesGroup):
    telegram_id = State()
    yandex_id = State()


# ============================================================
# KEYBOARDS (Клавиатуралар)
# ============================================================
def user_main_menu(telegram_id: int) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="💰 Balans")],
        [KeyboardButton(text="📊 Bugungi buyurtmalar")],
        [KeyboardButton(text="💸 Pul yechish")],
        [KeyboardButton(text="👤 Profil")],
        [KeyboardButton(text="💬 Haydovchilar guruhi")],
        [KeyboardButton(text="🆘 Yordam")],
    ]
    if telegram_id in ADMIN_IDS:
        rows.append([KeyboardButton(text="🛠 Admin")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)

def admin_main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👥 Haydovchilar")],
            [KeyboardButton(text="💸 Pul yechishlar")],
            [KeyboardButton(text="➕ Haydovchi qo'shish")],
            [KeyboardButton(text="⬅️ Orqaga")],
        ],
        resize_keyboard=True
    )

def cancel_keyboard(telegram_id: int) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Bekor qilish")]], resize_keyboard=True)


# ============================================================
# HANDLERS (Асосий логика)
# ============================================================

@driver_router.message(CommandStart())
async def cmd_start(message: Message):
    user = upsert_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    if user.get("is_registered", 0) == 1:
        await message.answer(f"✅ Xush kelibsiz, {user['full_name']}!\n\n🆔 POSITION: <code>{user['position']}</code>",
                             reply_markup=user_main_menu(message.from_user.id))
        return
    
    await message.answer(f"🚕 <b>{BOT_NAME}</b> ga xush kelibsiz!\n\nIshni boshlash uchun ro'yxatdan o'ting.",
                         reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="📝 Ro'yxatdan o'tish")]], resize_keyboard=True))

@driver_router.message(F.text == "📝 Ro'yxatdan o'tish")
async def start_registration(message: Message, state: FSMContext):
    await state.set_state(RegisterStates.name)
    await message.answer("👤 <b>Ism va familiyangizni kiriting:</b>\n\nMisol: <i>Aliyev Alisher</i>",
                         reply_markup=cancel_keyboard(message.from_user.id))

@driver_router.message(F.text == "❌ Bekor qilish")
async def cancel_action(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Amal bekor qilindi.", reply_markup=user_main_menu(message.from_user.id))

@driver_router.message(RegisterStates.name)
async def register_name(message: Message, state: FSMContext):
    name = message.text.strip()
    if len(name.split()) < 2:
        await message.answer("❌ Ism familiya noto'g'ri. Qaytadan kiriting.")
        return
    await state.update_data(full_name=name)
    await state.set_state(RegisterStates.phone)
    await message.answer("📱 <b>Telefon raqamingizni kiriting:</b>\n\nMisol: <i>+998901234567</i>")

@driver_router.message(RegisterStates.phone)
async def register_phone(message: Message, state: FSMContext):
    phone = re.sub(r"[\s\-\(\)]", "", message.text)
    if not re.fullmatch(r"\+998\d{9}", phone):
        await message.answer("❌ Telefon raqam noto'g'ri. Format: +998901234567")
        return
    await state.update_data(phone=phone)
    await state.set_state(RegisterStates.card)
    await message.answer("💳 <b>Karta raqamingizni kiriting:</b>\n\nMisol: <i>8600 1234 5678 9012</i>")

@driver_router.message(RegisterStates.card)
async def register_card(message: Message, state: FSMContext):
    card = re.sub(r"\s+", "", message.text)
    if not re.fullmatch(r"\d{16}", card):
        await message.answer("❌ Karta raqam noto'g'ri. 16 ta raqam bo'lishi kerak.")
        return
    await state.update_data(card_number=card)
    await state.set_state(RegisterStates.car_model)
    await message.answer("🚗 <b>Avtomobilingiz markasini kiriting:</b>\n\nMisol: <i>Chevrolet Lacetti</i>")

@driver_router.message(RegisterStates.car_model)
async def register_car_model(message: Message, state: FSMContext):
    await state.update_data(car_model=message.text.strip())
    await state.set_state(RegisterStates.car_number)
    await message.answer("🔢 <b>Avtomobilingiz davlat raqamini kiriting:</b>\n\nMisol: <i>01 A 123 AA</i>")

@driver_router.message(RegisterStates.car_number)
async def register_car_number(message: Message, state: FSMContext):
    car_number = message.text.strip().upper()
    await state.update_data(car_number=car_number)
    data = await state.get_data()
    
    position = complete_registration(
        message.from_user.id,
        data.get("full_name", ""),
        data.get("phone", ""),
        data.get("card_number", ""),
        data.get("car_model", ""),
        car_number
    )
    
    await state.clear()
    if position:
        await message.answer(f"✅ <b>Tabriklaymiz!</b>\n\nSiz {BOT_NAME} tizimida muvaffaqiyatli ro'yxatdan o'tdingiz!\n\n🆔 Sizning POSITIONingiz: <code>{position}</code>\n\n🔑 Bu kod shaxsiy identifikatoringiz.", reply_markup=user_main_menu(message.from_user.id))
        
        # Админга хабар юбориш
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(admin_id, f"✅ Yangi haydovchi ro'yxatdan o'tdi!\n👤 {data.get('full_name')}\n📱 {data.get('phone')}\n🚗 {data.get('car_model')} - {car_number}")
            except: pass
    else:
        await message.answer("❌ Xatolik yuz berdi. /start bosing.")


# ============================================================
# АСОСИЙ МЕНЮ ФУНКЦИЯЛАРИ (Ҳар бир хайдовчи фақат ўзиникини кўради!)
# ============================================================

@driver_router.message(F.text == "💰 Balans")
async def show_balance(message: Message):
    user = get_user_by_telegram(message.from_user.id)
    if not user or user["is_registered"] == 0:
        await message.answer("Avval ro'yxatdan o'ting. /start bosing.")
        return
    await message.answer(f"💰 <b>Balans</b>\n\nJoriy balans: <b>{user['balance']:,}</b> so'm\nBloklangan: <b>{user['blocked_balance']:,}</b> so'm", reply_markup=user_main_menu(message.from_user.id))

@driver_router.message(F.text == "📊 Bugungi buyurtmalar")
async def show_today_orders(message: Message):
    user = get_user_by_telegram(message.from_user.id)
    if not user or user["is_registered"] == 0:
        await message.answer("Avval ro'yxatdan o'ting. /start bosing.")
        return
    
    # Яндекс API дан хайдовчи заказларини олиш (Умумий статистика)
    stats_text = ""
    if user.get("yandex_driver_id"):
        # Яндексга сўров юборамиз
        data = await yandex_api_request("driver-metrics", method="POST", payload={
            "park_id": YANDEX_PARK_ID,
            "driver_id": user["yandex_driver_id"],
            "date_from": datetime.now().date().isoformat() + "T00:00:00Z",
            "date_to": datetime.now().isoformat()
        })
        # (Бу ерда маълумотни таҳлил қилиш керак, оддий кўриниш учун)
        stats_text = "📊 Яндекс дан маълумот олинмоқда..."
    else:
        stats_text = "📭 Siz hali Yandex bilan bog'lanmagansiz. Admin bilan bog'laning."
    
    await message.answer(stats_text)

@driver_router.message(F.text == "💸 Pul yechish")
async def start_withdraw(message: Message, state: FSMContext):
    user = get_user_by_telegram(message.from_user.id)
    if not user or user["is_registered"] == 0:
        await message.answer("Avval ro'yxatdan o'ting. /start bosing.")
        return
    await state.set_state(WithdrawStates.amount)
    await message.answer(f"💸 <b>Pul yechish</b>\n\nMavjud balans: <b>{user['balance']:,}</b> so'm\nMinimal: {MIN_WITHDRAWAL:,} so'm.\n\nSummani kiriting:", reply_markup=cancel_keyboard(message.from_user.id))

@driver_router.message(WithdrawStates.amount)
async def process_withdraw_amount(message: Message, state: FSMContext):
    try:
        amount = float(message.text.replace(" ", "").replace("so'm", ""))
    except:
        await message.answer("❌ Noto'g'ri summa.")
        return
    
    user = get_user_by_telegram(message.from_user.id)
    if amount < MIN_WITHDRAWAL:
        await message.answer(f"❌ Minimal yechish summasi {MIN_WITHDRAWAL:,} so'm.")
        return
    if amount > user["balance"]:
        await message.answer("❌ Balansingizda yetarli mablag' yo'q.")
        return
    
    await state.update_data(amount=amount)
    await state.set_state(WithdrawStates.payment_type)
    await message.answer("Qanday usulda yechmoqchisiz?", reply_markup=ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="💳 Kartaga"), KeyboardButton(text="💵 Naqd")], [KeyboardButton(text="❌ Bekor qilish")]], resize_keyboard=True))

@driver_router.message(F.text.in_(["💳 Kartaga", "💵 Naqd"]))
async def process_withdraw_type(message: Message, state: FSMContext):
    data = await state.get_data()
    payment_type = "card" if "Kartaga" in message.text else "cash"
    await state.update_data(payment_type=payment_type)
    
    if payment_type == "card":
        await state.set_state(WithdrawStates.card_number)
        await message.answer("💳 Karta raqamini kiriting:")
    else:
        await state.set_state(WithdrawStates.phone_number)
        await message.answer("📱 Telefon raqamini kiriting (+998...):")

@driver_router.message(WithdrawStates.card_number)
async def process_withdraw_card(message: Message, state: FSMContext):
    card = re.sub(r"\s+", "", message.text)
    if not re.fullmatch(r"\d{16}", card):
        await message.answer("❌ Karta raqam noto'g'ri.")
        return
    await state.update_data(card_number=card)
    
    data = await state.get_data()
    w_id = create_withdrawal(message.from_user.id, data["amount"], data["payment_type"], card_number=card)
    await state.clear()
    if w_id:
        # Админга хабар
        for admin_id in ADMIN_IDS:
            await bot.send_message(admin_id, f"💸 <b>Yangi pul yechish so'rovi!</b>\nID: #{w_id}\nHaydovchi: {message.from_user.full_name}\nSumma: {data['amount']:,} so'm\nKarta: {card}")
        await message.answer("✅ Arizangiz qabul qilindi! Admin tasdiqlagach pul tushadi.", reply_markup=user_main_menu(message.from_user.id))
    else:
        await message.answer("❌ Xatolik yuz berdi.", reply_markup=user_main_menu(message.from_user.id))

@driver_router.message(WithdrawStates.phone_number)
async def process_withdraw_phone(message: Message, state: FSMContext):
    phone = re.sub(r"[\s\-\(\)]", "", message.text)
    if not re.fullmatch(r"\+998\d{9}", phone):
        await message.answer("❌ Telefon raqam noto'g'ri.")
        return
    await state.update_data(phone_number=phone)
    
    data = await state.get_data()
    w_id = create_withdrawal(message.from_user.id, data["amount"], data["payment_type"], phone_number=phone)
    await state.clear()
    if w_id:
        for admin_id in ADMIN_IDS:
            await bot.send_message(admin_id, f"💸 <b>Yangi pul yechish so'rovi!</b>\nID: #{w_id}\nHaydovchi: {message.from_user.full_name}\nSumma: {data['amount']:,} so'm\nTel: {phone}")
        await message.answer("✅ Arizangiz qabul qilindi! Admin tasdiqlagach pul tushadi.", reply_markup=user_main_menu(message.from_user.id))
    else:
        await message.answer("❌ Xatolik yuz berdi.", reply_markup=user_main_menu(message.from_user.id))

@driver_router.message(F.text == "👤 Profil")
async def show_profile(message: Message):
    user = get_user_by_telegram(message.from_user.id)
    if not user or user["is_registered"] == 0:
        await message.answer("Avval ro'yxatdan o'ting. /start bosing.")
        return
    await message.answer(f"👤 <b>Profil</b>\n\nIsm: {user['full_name']}\nTelefon: {user['phone']}\nMashina: {user['car_model']} | {user['car_number']}\nPOSITION: <code>{user['position']}</code>\nYandex ID: <code>{user.get('yandex_driver_id') or 'Yo\\'q'}</code>", reply_markup=user_main_menu(message.from_user.id))

@driver_router.message(F.text == "💬 Haydovchilar guruhi")
async def show_group(message: Message):
    await message.answer(f"💬 <b>Haydovchilar guruhi</b>\n\n<a href='{DRIVER_GROUP_LINK}'>Guruhga o'tish</a>")

@driver_router.message(F.text == "🆘 Yordam")
async def show_support(message: Message):
    await message.answer(f"🆘 <b>Yordam</b>\n\nTelefon: <b>{SUPPORT_PHONE}</b>\nTelegram: {SUPPORT_TG}", reply_markup=user_main_menu(message.from_user.id))


# ============================================================
# АДМИН ПАНЕЛ (Фақат Админ ишлатади)
# ============================================================

@driver_router.message(F.text == "🛠 Admin")
async def admin_panel(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("🚫 Bu bo'lim faqat adminlar uchun.")
        return
    await message.answer("🛠 <b>Admin panel</b>", reply_markup=admin_main_menu())

@driver_router.message(F.text == "👥 Haydovchilar")
async def admin_list_drivers(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    drivers = get_all_drivers()
    text = f"👥 <b>Haydovchilar</b> ({len(drivers)})\n\n"
    for d in drivers:
        text += f"🆔 {d['position']} | {d['full_name']} | {d['car_number']}\n"
    await message.answer(text, reply_markup=admin_main_menu())

@driver_router.message(F.text == "💸 Pul yechishlar")
async def admin_withdrawals(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    conn = get_db()
    rows = conn.execute("SELECT * FROM withdrawals WHERE status='pending' ORDER BY id DESC").fetchall()
    conn.close()
    text = f"💸 <b>Pul yechish so'rovlari</b>\n\n"
    if not rows:
        text += "Hozircha so'rovlar yo'q."
    else:
        for w in rows:
            driver = get_driver_by_id(w["user_id"])
            text += f"#{w['id']} | {driver['full_name']} | {w['amount']:,} so'm | {w['payment_type']}\n"
    await message.answer(text, reply_markup=admin_main_menu())

@driver_router.message(F.text == "➕ Haydovchi qo'shish")
async def admin_add_driver(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminAddDriverState.telegram_id)
    await message.answer("➕ <b>Yangi haydovchi qo'shish</b>\n\nHaydovchining Telegram ID sini kiriting:", reply_markup=cancel_keyboard(message.from_user.id))

@driver_router.message(AdminAddDriverState.telegram_id)
async def admin_add_driver_tg_id(message: Message, state: FSMContext):
    try:
        tg_id = int(message.text)
    except:
        await message.answer("❌ Noto'g'ri ID.")
        return
    
    # Текшириш: бу одам базада борми?
    user = get_user_by_telegram(tg_id)
    if not user:
        await message.answer("❌ Bu Telegram ID tizimda topilmadi. Haydovchi avval botga kirishi kerak.")
        return
    
    await state.update_data(telegram_id=tg_id)
    await state.set_state(AdminAddDriverState.yandex_id)
    await message.answer("🚕 Endi haydovchining <b>Yandex Taxi ID</b> sini kiriting:")

@driver_router.message(AdminAddDriverState.yandex_id)
async def admin_add_driver_yandex_id(message: Message, state: FSMContext):
    yandex_id = message.text.strip()
    data = await state.get_data()
    tg_id = data["telegram_id"]
    
    conn = get_db()
    conn.execute("UPDATE users SET yandex_driver_id=? WHERE telegram_id=?", (yandex_id, tg_id))
    conn.commit()
    conn.close()
    
    await state.clear()
    await message.answer(f"✅ Haydovchi muvaffaqiyatli Yandex bilan bog'landi!\n\nTelegram ID: {tg_id}\nYandex ID: {yandex_id}", reply_markup=admin_main_menu())

@driver_router.message(F.text == "⬅️ Orqaga")
async def admin_back(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    await message.answer("Asosiy menyu", reply_markup=user_main_menu(message.from_user.id))


# ============================================================
# WEB ROUTES (Render учун)
# ============================================================

@web_router.get("/")
async def index_page(request: web.Request) -> web.Response:
    return web.Response(text=f"<h1>{BOT_NAME} is running</h1>", content_type="text/html")

@web_router.get("/health")
async def health_route(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


# ============================================================
# MAIN
# ============================================================

def register_routers() -> None:
    dp.include_router(driver_router)
    dp.include_router(admin_router)

async def main() -> None:
    init_db()
    register_routers()
    
    app = web.Application()
    app.add_routes(web_router)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    
    logger.info(f"🚕 {BOT_NAME} started!")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
