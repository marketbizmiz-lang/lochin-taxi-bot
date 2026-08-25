import os
import re
import math
import html
import asyncio
import logging
import sqlite3
import aiohttp
import asyncpg
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
    InlineKeyboardMarkup,
    InlineKeyboardButton,
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

# Bosh Adminlar va Menejer (Qudrat aka)
ADMIN_IDS = {8934129079, 8956429378}
env_admins = os.getenv("ADMIN_IDS", "")
if env_admins:
    for adm in env_admins.split(","):
        if adm.strip().isdigit():
            ADMIN_IDS.add(int(adm.strip()))

MANAGER_TG_ID = 8934129079
SUPPORT_PHONE = os.getenv("SUPPORT_PHONE", "+998913773200").strip()
SUPPORT_PHONE_DISPLAY = "+998 91 377 32 00"
DRIVER_GROUP_LINK = os.getenv("DRIVER_GROUP_LINK", "https://t.me/+vLyCiiXNvB5kMTUy").strip()

# Yandex Fleet API
YANDEX_API_KEY = os.getenv("YANDEX_API_KEY", "").strip()
YANDEX_CLIENT_ID = os.getenv("YANDEX_CLIENT_ID", "").strip()
YANDEX_PARK_ID = os.getenv("YANDEX_PARK_ID", "").strip()
YANDEX_FLEET_URL = "https://fleet-api.yandex.ru/v1"

# BRB 24/7 API (Avtomatik to'lovlar uchun)
BRB_API_URL = os.getenv("BRB_API_URL", "https://api.brb.uz/v1").strip()
BRB_API_KEY = os.getenv("BRB_API_KEY", "").strip()
BRB_MERCHANT_ID = os.getenv("BRB_MERCHANT_ID", "").strip()

# Sozlamalar
MIN_WITHDRAWAL = int(os.getenv("MIN_WITHDRAWAL", "30000"))
COMMISSION_PERCENT = float(os.getenv("COMMISSION_PERCENT", "2.0"))
REFERRAL_BONUS = int(os.getenv("REFERRAL_BONUS", "30000"))  # Do'stini taklif qilgani uchun bonus


# ============================================================
# BULUTLI POSTGRESQL / SQLITE DASTURIY QATLAMI
# ============================================================

db_pool: Optional[asyncpg.Pool] = None

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

async def init_database():
    global db_pool
    if DATABASE_URL:
        logger.info("🐘 PostgreSQL bulutli bazasiga ulanmoqda...")
        # sslmode require bo'lsa asyncpg ga to'g'rilash
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
                    yandex_driver_id TEXT,
                    referrer_id BIGINT,
                    total_orders INT DEFAULT 0,
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
        logger.info("✅ PostgreSQL bazasi tayyor va jadvallar yaratildi!")
    else:
        logger.info("📁 Lokal SQLite bazasi ishlatilmoqda...")
        conn = sqlite3.connect(DB_PATH)
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
                balance REAL DEFAULT 0,
                blocked_balance REAL DEFAULT 0,
                is_registered INTEGER DEFAULT 0,
                yandex_driver_id TEXT,
                referrer_id INTEGER,
                total_orders INTEGER DEFAULT 0,
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
                payout_method TEXT DEFAULT 'manual',
                ext_tx_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()


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
                    INSERT INTO users (telegram_id, username, referrer_id, created_at, updated_at)
                    VALUES ($1, $2, $3, $4, $5)
                """, telegram_id, username or "", referrer_id, now, now)
        else:
            conn = sqlite3.connect(DB_PATH)
            conn.execute("""
                INSERT INTO users (telegram_id, username, referrer_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
            """, (telegram_id, username or "", referrer_id, now, now))
            conn.commit()
            conn.close()


# ============================================================
# YANDEX FLEET VA BRB 24/7 API
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
            logger.error(f"Yandex balans xatolik: {e}")
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
            logger.error(f"Yandex tranzaksiya xatolik: {e}")
            return False


class BRBPaymentAPI:
    """BRB 24/7 Avtomatik kartaga to'lov API"""
    def __init__(self, api_url: str, api_key: str, merchant_id: str):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.merchant_id = merchant_id

    async def send_payout(self, card_number: str, amount: float, order_id: int) -> dict:
        """Kartaga 2 soniyada avto-to'lov o'tkazish"""
        if not self.api_key or not self.merchant_id:
            # Agar BRB kaliti hali kiritilmagan bo'lsa
            return {"success": False, "message": "BRB API kalitlari kiritilmagan (Manual rejim)"}

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "X-Merchant-ID": self.merchant_id,
            "Content-Type": "application/json"
        }
        payload = {
            "card_number": card_number,
            "amount": int(amount),
            "order_id": f"LCH-WD-{order_id}",
            "description": f"Lochin Taxi haydovchi to'lovi #{order_id}"
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{self.api_url}/payout", headers=headers, json=payload, timeout=15) as resp:
                    data = await resp.json()
                    if resp.status == 200 and data.get("status") == "success":
                        return {"success": True, "tx_id": data.get("tx_id")}
                    else:
                        return {"success": False, "message": data.get("message", "Bank rad etdi")}
        except Exception as e:
            logger.error(f"BRB API ulanish xatosi: {e}")
            return {"success": False, "message": str(e)}


yandex_api = YandexFleetAPI(YANDEX_API_KEY, YANDEX_CLIENT_ID, YANDEX_PARK_ID)
brb_api = BRBPaymentAPI(BRB_API_URL, BRB_API_KEY, BRB_MERCHANT_ID)


# ============================================================
# MATNLAR VA INTERFEYS (UZ / RU)
# ============================================================

TEXTS = {
    "uz": {
        "choose_lang": "🌐 <b>Iltimos, tilni tanlang / Пожалуйста, выберите язык:</b>",
        "welcome": f"🕌 <b>Assalomu alaykum!</b>\n\n"
                   f"🚕 <b>{BOT_NAME}</b> taksoparkiga xush kelibsiz! Biz bilan daromadingizni oshiring! 🤝\n\n"
                   f"Tizimdan foydalanish uchun ro'yxatdan o'ting:",
        "register_btn": "📝 Ro'yxatdan o'tish",
        "reg_name": "👤 <b>Ism va familiyangizni kiriting:</b>\n\n<i>Misol: Alisher Qodirov</i>",
        "reg_phone": "📱 <b>Telefon raqamingizni yuboring:</b>\n\nQuyidagi tugmani bosing yoki raqamingizni yozing (Format: <i>+998901234567</i>):",
        "reg_card": "💳 <b>Plastik karta raqamingizni kiriting (16 ta raqam):</b>\n\n<i>Misol: 8600 1234 5678 9012 yoki 9860...</i>",
        "reg_car_model": "🚗 <b>Avtomobilingiz rusumini kiriting:</b>\n\n<i>Misol: Chevrolet Cobalt</i>",
        "reg_car_number": "🔢 <b>Avtomobil davlat raqamini kiriting:</b>\n\n<i>Misol: 01 A 123 AA</i>",
        "reg_success": f"✅ <b>Tabriklaymiz! Siz muvaffaqiyatli ro'yxatdan o'tdingiz.</b>\n\n"
                       f"🆔 Sizning POSITION ID: <code>{{position}}</code>\n"
                       f"🔑 Bu kod sizning taksoparkdagi shaxsiy kodingiz.",
        "already_reg": "✅ <b>Siz tizimda ro'yxatdan o'tgansiz!</b>\n\n🆔 POSITION: <code>{position}</code>\n👤 Haydovchi: <b>{name}</b>",
        
        # Tugmalar
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
        
        # Balans
        "balance_text": f"💰 <b>{{bot_name}} Balansi:</b>\n\n"
                        f"💵 Asosiy balans: <b>{{balance}} so'm</b>\n"
                        f"🔒 Muzlatilgan: <b>{{blocked}} so'm</b>\n"
                        f"✅ Yechish mumkin: <b>{{avail}} so'm</b>\n\n"
                        f"🚕 Yandex Pro balansi: <b>{{y_balance}}</b>",
        "withdraw_min_err": "❌ Minimal yechish summasi: {min_w} so'm",
        "withdraw_no_money": "❌ Balansingizda yetarli mablag' mavjud emas!",
        "withdraw_ask": f"💸 <b>Pul yechish (24/7 Avtomat):</b>\n\n"
                        f"🔹 Yechish mumkin: <b>{{avail}} so'm</b>\n"
                        f"🔹 Minimal summa: <b>{{min_w}} so'm</b>\n"
                        f"🔹 Komissiya: <b>{{comm}}%</b>\n\n"
                        f"Yechmoqchi bo'lgan summani kiriting (Masalan: <i>50000</i>):",
        "withdraw_confirm": "💳 <b>Pul yechishni tasdiqlaysizmi?</b>\n\n"
                             "💰 Yechilayotgan summa: <b>{amount} so'm</b>\n"
                             "📊 Komissiya ({comm}%): <b>{comm_amount} so'm</b>\n"
                             "💵 Kartaga tushadi: <b>{net_amount} so'm</b>\n"
                             "💳 Karta: <code>{card}</code>",
        "withdraw_sent": "⚡️ <b>To'lov jarayoni boshlandi!</b>\nPul 2-3 soniya ichida kartangizga o'tkaziladi.",

        # Referal
        "ref_text": f"👥 <b>Do'stlarni taklif qiling va daromad oling!</b>\n\n"
                    f"Har bir taklif qilgan faol haydovchingiz uchun: <b>{REFERRAL_BONUS:,} so'm</b> bonus beriladi!\n\n"
                    f"🔗 Sizning shaxsiy taklif havolangiz:\n<code>{{link}}</code>\n\n"
                    f"Ushbu havolani haydovchi do'stlaringizga yuboring!",

        # SOS
        "sos_title": "🆘 <b>Tezkor Yordam va Aloqa Markazi</b>\n\nKerakli bo'limni tanlang:",
        "sos_btn_loc": "📍 Lokatsiya yuborish (DTP / Yo'lda qoldim)",
        "sos_btn_msg": "✍️ Menejerga xabar / Shikoyat yozish",
        "sos_btn_call": "📞 Menejerga qo'ng'iroq",
        "sos_btn_chat": "💬 Menejer bilan shaxsiy chat",
        "sos_ask_loc": "📍 Pastdagi <b>[📍 Hozirgi joylashuvimni yuborish]</b> tugmasini bosing:",
        "sos_loc_btn": "📍 Hozirgi joylashuvimni yuborish",
        "sos_ask_msg": "✍️ <b>Muammo yoki savolingizni yozing:</b>\n<i>(Mijoz bilan mojaro, to'lov yoki boshqa holat haqida batafsil yozing)</i>",
        "sos_sent": "🚨 <b>Xabaringiz Bosh Menejerga yetkazildi!</b>\nTez orada siz bilan bog'lanishadi.",
    },
    "ru": {
        "choose_lang": "🌐 <b>Пожалуйста, выберите язык / Iltimos, tilni tanlang:</b>",
        "welcome": f"🕌 <b>Ассаламу алейкум!</b>\n\n"
                   f"🚕 Добро пожаловать в таксопарк <b>{BOT_NAME}</b>! Увеличьте свой доход вместе с нами! 🤝\n\n"
                   f"Для начала работы пройдите регистрацию:",
        "register_btn": "📝 Регистрация",
        "reg_name": "👤 <b>Введите ваше имя и фамилию:</b>\n\n<i>Пример: Алишер Кадыров</i>",
        "reg_phone": "📱 <b>Отправьте ваш номер телефона:</b>\n\nНажмите кнопку ниже или введите номер вручную (Формат: <i>+998901234567</i>):",
        "reg_card": "💳 <b>Введите 16-значный номер карты:</b>\n\n<i>Пример: 8600 1234 5678 9012</i>",
        "reg_car_model": "🚗 <b>Введите марку автомобиля:</b>\n\n<i>Пример: Chevrolet Cobalt</i>",
        "reg_car_number": "🔢 <b>Введите госномер автомобиля:</b>\n\n<i>Пример: 01 A 123 AA</i>",
        "reg_success": f"✅ <b>Поздравляем! Вы успешно зарегистрированы.</b>\n\n"
                       f"🆔 Ваш POSITION ID: <code>{{position}}</code>\n"
                       f"🔑 Это ваш личный идентификатор в таксопарке.",
        "already_reg": "✅ <b>Вы уже зарегистрированы!</b>\n\n🆔 POSITION: <code>{position}</code>\n👤 Водитель: <b>{name}</b>",
        
        # Кнопки
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
        
        # Баланс
        "balance_text": f"💰 <b>Баланс в {{bot_name}}:</b>\n\n"
                        f"💵 Основной баланс: <b>{{balance}} сум</b>\n"
                        f"🔒 Заблокировано: <b>{{blocked}} сум</b>\n"
                        f"✅ Доступно к выводу: <b>{{avail}} сум</b>\n\n"
                        f"🚕 Баланс в Яндекс Про: <b>{{y_balance}}</b>",
        "withdraw_min_err": "❌ Минимальная сумма вывода: {min_w} сум",
        "withdraw_no_money": "❌ На вашем балансе недостаточно средств!",
        "withdraw_ask": f"💸 <b>Вывод средств (24/7 Авто):</b>\n\n"
                        f"🔹 Доступно: <b>{{avail}} сум</b>\n"
                        f"🔹 Мин. сумма: <b>{{min_w}} сум</b>\n"
                        f"🔹 Комиссия: <b>{{comm}}%</b>\n\n"
                        f"Введите сумму для вывода (Пример: <i>50000</i>):",
        "withdraw_confirm": "💳 <b>Подтверждаете вывод средств?</b>\n\n"
                             "💰 Сумма: <b>{amount} сум</b>\n"
                             "📊 Комиссия ({comm}%): <b>{comm_amount} сум</b>\n"
                             "💵 К зачислению на карту: <b>{net_amount} сум</b>\n"
                             "💳 Карта: <code>{card}</code>",
        "withdraw_sent": "⚡️ <b>Процесс вывода запущен!</b>\nСредства поступят на карту в течение 2-3 секунд.",

        # Реферал
        "ref_text": f"👥 <b>Приглашайте друзей и получайте бонусы!</b>\n\n"
                    f"За каждого активного водителя: <b>{REFERRAL_BONUS:,} сум</b> бонуса!\n\n"
                    f"🔗 Ваша реферальная ссылка:\n<code>{{link}}</code>",

        # SOS
        "sos_title": "🆘 <b>Центр Экстренной Помощи</b>\n\nВыберите нужный раздел:",
        "sos_btn_loc": "📍 Отправить локацию (ДТП / В пути)",
        "sos_btn_msg": "✍️ Написать менеджеру / Жалоба",
        "sos_btn_call": "📞 Позвонить менеджеру",
        "sos_btn_chat": "💬 Личный чат с менеджером",
        "sos_ask_loc": "📍 Нажмите кнопку <b>[📍 Отправить мою локацию]</b> ниже:",
        "sos_loc_btn": "📍 Отправить мою локацию",
        "sos_ask_msg": "✍️ <b>Опишите проблему:</b>",
        "sos_sent": "🚨 <b>Сообщение отправлено Главному Менеджеру!</b>",
    }
}

async def get_lang(uid: int) -> str:
    user = await db_get_user(uid)
    return user.get("language", "uz") if user else "uz"

def t(lang_code: str, key: str, **kwargs) -> str:
    text = TEXTS.get(lang_code, TEXTS["uz"]).get(key, key)
    return text.format(**kwargs) if kwargs else text

def fmt_sum(val: Any) -> str:
    try:
        return f"{int(float(val)):,}".replace(",", " ")
    except Exception:
        return "0"


# ============================================================
# KEYBOARDS
# ============================================================

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

def sos_menu_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t(lang, "sos_btn_loc"), callback_data="sos:loc")],
            [InlineKeyboardButton(text=t(lang, "sos_btn_msg"), callback_data="sos:msg")],
            [InlineKeyboardButton(text=t(lang, "sos_btn_chat"), url=f"tg://user?id={MANAGER_TG_ID}")],
            [InlineKeyboardButton(text=t(lang, "sos_btn_call"), url=f"tel:{SUPPORT_PHONE}")],
        ]
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

class SOSStates(StatesGroup):
    waiting_for_location = State()
    waiting_for_message = State()

class AdminBroadcast(StatesGroup):
    message = State()


# ============================================================
# HANDLERS
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

    # Referal kodni aniqlash (?start=ref_123456)
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
            t(lang, "already_reg", position=user["position"], name=user["full_name"]),
            reply_markup=user_main_kb(lang, uid)
        )
        return

    # Til tanlash
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🇺🇿 O'zbekcha", callback_data="lang:uz"),
                InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang:ru")
            ]
        ]
    )
    await message.answer("🌐 <b>Iltimos, tilni tanlang / Пожалуйста, выберите язык:</b>", reply_markup=kb)


@router.callback_query(F.data.startswith("lang:"))
async def lang_callback(callback: CallbackQuery) -> None:
    lang = callback.data.split(":")[1]
    uid = callback.from_user.id
    now = utc_now_iso()

    if db_pool:
        async with db_pool.acquire() as conn:
            await conn.execute("UPDATE users SET language = $1, updated_at = $2 WHERE telegram_id = $3", lang, now, uid)
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE users SET language = ?, updated_at = ? WHERE telegram_id = ?", (lang, now, uid))
        conn.commit()
        conn.close()

    await callback.message.delete()
    reg_kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t(lang, "register_btn"))]],
        resize_keyboard=True
    )
    await callback.message.answer(t(lang, "welcome"), reply_markup=reg_kb)
    await callback.answer()


@router.message(F.text.in_(["📝 Ro'yxatdan o'tish", "📝 Регистрация"]))
async def reg_start_flow(message: Message, state: FSMContext) -> None:
    lang = await get_lang(message.from_user.id)
    await state.set_state(RegStates.name)
    await message.answer(t(lang, "reg_name"), reply_markup=cancel_kb(lang))


@router.message(RegStates.name)
async def reg_step_name(message: Message, state: FSMContext) -> None:
    lang = await get_lang(message.from_user.id)
    if message.text == t(lang, "cancel"):
        await state.clear()
        await message.answer(t(lang, "cancel"), reply_markup=user_main_kb(lang, message.from_user.id))
        return

    name = message.text.strip()
    if len(name) < 3:
        await message.answer("⚠️ Iltimos, ism va familiyangizni to'liq yozing:")
        return

    await state.update_data(full_name=name)
    await state.set_state(RegStates.phone)
    await message.answer(t(lang, "reg_phone"), reply_markup=phone_request_kb(lang))


@router.message(RegStates.phone)
async def reg_step_phone(message: Message, state: FSMContext) -> None:
    lang = await get_lang(message.from_user.id)
    if message.text == t(lang, "cancel"):
        await state.clear()
        await message.answer(t(lang, "cancel"), reply_markup=user_main_kb(lang, message.from_user.id))
        return

    phone = message.contact.phone_number if message.contact else message.text.strip().replace(" ", "").replace("-", "")
    if not phone.startswith("+"):
        phone = "+" + phone

    if not re.fullmatch(r"\+998\d{9}", phone):
        await message.answer("⚠️ Telefon raqam formati noto'g'ri. Misol: <i>+998901234567</i>")
        return

    await state.update_data(phone=phone)
    await state.set_state(RegStates.card)
    await message.answer(t(lang, "reg_card"), reply_markup=cancel_kb(lang))


@router.message(RegStates.card)
async def reg_step_card(message: Message, state: FSMContext) -> None:
    lang = await get_lang(message.from_user.id)
    if message.text == t(lang, "cancel"):
        await state.clear()
        await message.answer(t(lang, "cancel"), reply_markup=user_main_kb(lang, message.from_user.id))
        return

    card = message.text.strip().replace(" ", "")
    if not (card.isdigit() and len(card) == 16):
        await message.answer("⚠️ Karta raqami 16 ta sondan iborat bo'lishi kerak:")
        return

    await state.update_data(card_number=card)
    await state.set_state(RegStates.car_model)
    await message.answer(t(lang, "reg_car_model"), reply_markup=cancel_kb(lang))


@router.message(RegStates.car_model)
async def reg_step_car_model(message: Message, state: FSMContext) -> None:
    lang = await get_lang(message.from_user.id)
    if message.text == t(lang, "cancel"):
        await state.clear()
        await message.answer(t(lang, "cancel"), reply_markup=user_main_kb(lang, message.from_user.id))
        return

    await state.update_data(car_model=message.text.strip())
    await state.set_state(RegStates.car_number)
    await message.answer(t(lang, "reg_car_number"), reply_markup=cancel_kb(lang))


@router.message(RegStates.car_number)
async def reg_step_car_number(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    lang = await get_lang(uid)
    if message.text == t(lang, "cancel"):
        await state.clear()
        await message.answer(t(lang, "cancel"), reply_markup=user_main_kb(lang, uid))
        return

    car_number = message.text.strip().upper()
    data = await state.get_data()
    await state.clear()

    import random
    position = f"LCH-{random.randint(1000, 9999)}"

    # Yandex tekshirish
    yandex_driver = await yandex_api.get_driver_by_phone(data["phone"])
    yandex_driver_id = yandex_driver.get("driver_profile", {}).get("id") if yandex_driver else None

    now = utc_now_iso()
    if db_pool:
        async with db_pool.acquire() as conn:
            await conn.execute("""
                UPDATE users SET 
                    full_name = $1, phone = $2, card_number = $3, car_model = $4,
                    car_number = $5, position = $6, yandex_driver_id = $7, is_registered = 1, updated_at = $8
                WHERE telegram_id = $9
            """, data["full_name"], data["phone"], data["card_number"], data["car_model"], car_number, position, yandex_driver_id, now, uid)
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            UPDATE users SET 
                full_name = ?, phone = ?, card_number = ?, car_model = ?,
                car_number = ?, position = ?, yandex_driver_id = ?, is_registered = 1, updated_at = ?
            WHERE telegram_id = ?
        """, (data["full_name"], data["phone"], data["card_number"], data["car_model"], car_number, position, yandex_driver_id, now, uid))
        conn.commit()
        conn.close()

    await message.answer(t(lang, "reg_success", position=position), reply_markup=user_main_kb(lang, uid))

    # Adminga xabar
    for adm in ADMIN_IDS:
        try:
            await bot.send_message(
                adm,
                f"🆕 <b>Yangi haydovchi qo'shildi!</b>\n\n"
                f"👤 Ism: <b>{data['full_name']}</b>\n"
                f"📱 Telefon: <code>{data['phone']}</code>\n"
                f"🚗 Mashina: <b>{data['car_model']} ({car_number})</b>\n"
                f"💳 Karta: <code>{data['card_number']}</code>\n"
                f"🆔 POSITION: <code>{position}</code>\n"
                f"🚕 Yandex: <b>{'✅ Ulangan' if yandex_driver_id else '⚠️ Ulanmagan'}</b>"
            )
        except Exception:
            pass


# --- MENYU TUGMALARI ---

@router.message(F.text.in_(["💰 Balans", "💰 Баланс"]))
async def balance_handler(message: Message) -> None:
    uid = message.from_user.id
    user = await db_get_user(uid)
    if not user or user.get("is_registered") != 1:
        await message.answer("Iltimos, avval ro'yxatdan o'ting: /start")
        return

    lang = user.get("language", "uz")
    cur_bal = float(user.get("balance", 0.0))
    y_bal_str = "Ulanmagan"

    if user.get("yandex_driver_id"):
        live_bal = await yandex_api.get_driver_balance(user["yandex_driver_id"])
        if live_bal is not None:
            cur_bal = live_bal
            y_bal_str = f"{fmt_sum(live_bal)} so'm"
            if db_pool:
                async with db_pool.acquire() as conn:
                    await conn.execute("UPDATE users SET balance = $1 WHERE telegram_id = $2", live_bal, uid)
            else:
                conn = sqlite3.connect(DB_PATH)
                conn.execute("UPDATE users SET balance = ? WHERE telegram_id = ?", (live_bal, uid))
                conn.commit()
                conn.close()

    avail = max(0.0, cur_bal - float(user.get("blocked_balance", 0.0)))
    await message.answer(
        t(lang, "balance_text",
          bot_name=BOT_NAME,
          balance=fmt_sum(cur_bal),
          blocked=fmt_sum(user.get("blocked_balance", 0)),
          avail=fmt_sum(avail),
          y_balance=y_bal_str),
        reply_markup=user_main_kb(lang, uid)
    )


@router.message(F.text.in_(["👥 Do'stni taklif qilish (Bonus)", "👥 Пригласить друга (Бонус)"]))
async def referral_handler(message: Message) -> None:
    uid = message.from_user.id
    user = await db_get_user(uid)
    if not user or user.get("is_registered") != 1:
        await message.answer("Iltimos, avval ro'yxatdan o'ting: /start")
        return

    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{uid}"
    lang = user.get("language", "uz")

    await message.answer(t(lang, "ref_text", link=ref_link), reply_markup=user_main_kb(lang, uid))


@router.message(F.text.in_(["🏆 TOP Haydovchilar", "🏆 ТОП Водителей"]))
async def top_drivers_handler(message: Message) -> None:
    uid = message.from_user.id
    lang = await get_lang(uid)

    text = f"🏆 <b>Lochin Taxi — Haftaning Eng Yaxshi Haydovchilari:</b>\n\n"
    text += f"🥇 1. Alisher Q. (LCH-1044) — <b>84 ta buyurtma</b> (🎁 100 000 so'm)\n"
    text += f"🥈 2. Jamshid B. (LCH-1892) — <b>76 ta buyurtma</b> (🎁 50 000 so'm)\n"
    text += f"🥉 3. Otabek S. (LCH-1120) — <b>68 ta buyurtma</b> (🎁 30 000 so'm)\n"
    text += f"4. Dilshod T. — 61 ta\n"
    text += f"5. Sanjar R. — 58 ta\n\n"
    text += f"🔥 <i>Siz ham ko'proq buyurtma bajaring va haftalik sovrinlarni yutib oling!</i>"
    await message.answer(text, reply_markup=user_main_kb(lang, uid))


# --- PUL YECHISH (BRB 24/7 AVTO-TO'LOV INTEGRATSIYASI) ---

@router.message(F.text.in_(["💸 Pul yechish (24/7)", "💸 Вывод средств (24/7)"]))
async def withdraw_start(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    user = await db_get_user(uid)
    if not user or user.get("is_registered") != 1:
        await message.answer("Iltimos, avval ro'yxatdan o'ting: /start")
        return

    lang = user.get("language", "uz")
    avail = max(0.0, float(user.get("balance", 0)) - float(user.get("blocked_balance", 0)))

    if avail < MIN_WITHDRAWAL:
        await message.answer(t(lang, "withdraw_no_money") + f"\nMinimal: {fmt_sum(MIN_WITHDRAWAL)} so'm")
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

    if message.text == t(lang, "cancel"):
        await state.clear()
        await message.answer(t(lang, "cancel"), reply_markup=user_main_kb(lang, uid))
        return

    raw = message.text.replace(" ", "").replace("so'm", "").strip()
    if not raw.isdigit():
        await message.answer("⚠️ Iltimos, faqat musbat son kiriting:")
        return

    amount = float(raw)
    user = await db_get_user(uid)
    avail = max(0.0, float(user.get("balance", 0)) - float(user.get("blocked_balance", 0)))

    if amount < MIN_WITHDRAWAL:
        await message.answer(t(lang, "withdraw_min_err", min_w=fmt_sum(MIN_WITHDRAWAL)))
        return

    if amount > avail:
        await message.answer(t(lang, "withdraw_no_money"))
        return

    comm = amount * (COMMISSION_PERCENT / 100.0)
    net = amount - comm

    await state.update_data(amount=amount, commission=comm, net_amount=net, card=user["card_number"])
    await state.set_state(WithdrawStates.confirm)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⚡️ Avtomat Yechish (BRB 24/7)", callback_data="wd_go:auto"),
                InlineKeyboardButton(text="❌ Bekor qilish", callback_data="wd_go:no")
            ]
        ]
    )

    await message.answer(
        t(lang, "withdraw_confirm",
          amount=fmt_sum(amount),
          comm=COMMISSION_PERCENT,
          comm_amount=fmt_sum(comm),
          net_amount=fmt_sum(net),
          card=user["card_number"]),
        reply_markup=kb
    )


@router.callback_query(F.data.startswith("wd_go:"), WithdrawStates.confirm)
async def withdraw_process_callback(callback: CallbackQuery, state: FSMContext) -> None:
    uid = callback.from_user.id
    action = callback.data.split(":")[1]
    lang = await get_lang(uid)

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

    user = await db_get_user(uid)
    now = utc_now_iso()

    # 1. Yandex balansdan ayirish
    if user.get("yandex_driver_id"):
        y_ok = await yandex_api.create_transaction(
            user["yandex_driver_id"],
            amount,
            f"BRB 24/7 Avto-yechish karta {card}"
        )
        if not y_ok:
            await callback.message.edit_text("❌ Yandex Pro balansidan mablag' yechishda xatolik yuz berdi!")
            return

    # 2. BRB 24/7 orqali kartaga to'lash
    brb_res = await brb_api.send_payout(card, net_amount, user["id"])

    # 3. Bazaga yozish va balansni yangilash
    if db_pool:
        async with db_pool.acquire() as conn:
            await conn.execute("UPDATE users SET balance = balance - $1, updated_at = $2 WHERE id = $3", amount, now, user["id"])
            await conn.execute("""
                INSERT INTO withdrawals (user_id, amount, commission, net_amount, card_number, status, payout_method, ext_tx_id, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, 'brb_auto', $7, $8, $9)
            """, user["id"], amount, commission, net_amount, card, "completed" if brb_res["success"] else "manual_pending", brb_res.get("tx_id", ""), now, now)
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE users SET balance = balance - ?, updated_at = ? WHERE id = ?", (amount, now, user["id"]))
        conn.execute("""
            INSERT INTO withdrawals (user_id, amount, commission, net_amount, card_number, status, payout_method, ext_tx_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'brb_auto', ?, ?, ?)
        """, (user["id"], amount, commission, net_amount, card, "completed" if brb_res["success"] else "manual_pending", brb_res.get("tx_id", ""), now, now))
        conn.commit()
        conn.close()

    if brb_res["success"]:
        await callback.message.edit_text(
            f"✅ <b>Mablag' kartangizga muvaffaqiyatli o'tkazildi!</b>\n\n"
            f"💰 Summa: <b>{fmt_sum(net_amount)} so'm</b>\n"
            f"💳 Karta: <code>{card}</code>\n"
            f"🏦 Tizim: <b>BRB 24/7 Instant Pay</b>"
        )
    else:
        # Agar BRB kaliti qo'yilmagan bo'lsa adminga ariza sifatida tushadi
        await callback.message.edit_text(
            f"✅ <b>Arizangiz qabul qilindi!</b>\n\n"
            f"Admin tekshirib, pulni kartangizga o'tkazadi.\n"
            f"💰 Summa: <b>{fmt_sum(net_amount)} so'm</b>"
        )

    await callback.answer()


# --- SOS / YORDAM HANDLERS ---

@router.message(F.text.in_(["🆘 Yordam / SOS", "🆘 Помощь / SOS"]))
async def sos_handler(message: Message) -> None:
    lang = await get_lang(message.from_user.id)
    await message.answer(t(lang, "sos_title"), reply_markup=sos_menu_kb(lang))


@router.callback_query(F.data == "sos:loc")
async def sos_location_flow(callback: CallbackQuery, state: FSMContext) -> None:
    lang = await get_lang(callback.from_user.id)
    await state.set_state(SOSStates.waiting_for_location)
    await callback.message.delete()
    await callback.message.answer(t(lang, "sos_ask_loc"), reply_markup=location_request_kb(lang))
    await callback.answer()


@router.message(SOSStates.waiting_for_location, F.location)
async def sos_receive_location(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    lang = await get_lang(uid)
    await state.clear()
    user = await db_get_user(uid)

    lat = message.location.latitude
    lon = message.location.longitude

    alert = (
        f"🚨 <b>DIQQAT: HAYDOVCHIDAN SOS / LOKATSIYA!</b>\n\n"
        f"👤 Haydovchi: <b>{user['full_name']}</b> (<code>{user['position']}</code>)\n"
        f"📱 Telefon: <code>{user['phone']}</code>\n"
        f"🚗 Mashina: <b>{user['car_model']} ({user['car_number']})</b>\n\n"
        f"📍 <a href='https://maps.google.com/?q={lat},{lon}'>Google Xaritada ko'rish</a>"
    )

    adm_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💬 Haydovchi bilan chat", url=f"tg://user?id={uid}")]])
    for adm in ADMIN_IDS:
        try:
            await bot.send_message(adm, alert, reply_markup=adm_kb)
            await bot.send_location(adm, latitude=lat, longitude=lon)
        except Exception:
            pass

    await message.answer(t(lang, "sos_sent"), reply_markup=user_main_kb(lang, uid))


# --- QOLGAN STANDART TUGMALAR ---

@router.message(F.text.in_(["📊 Bugungi buyurtmalar", "📊 Сегодняшние заказы"]))
async def orders_handler(message: Message) -> None:
    lang = await get_lang(message.from_user.id)
    await message.answer("📊 Bugungi barcha safarlaringiz Yandex Pro ilovasida hisoblanadi va balansingizda aks etadi.", reply_markup=user_main_kb(lang, message.from_user.id))

@router.message(F.text.in_(["👤 Profil", "👤 Профиль"]))
async def profile_handler(message: Message) -> None:
    uid = message.from_user.id
    user = await db_get_user(uid)
    lang = await get_lang(uid)
    if not user:
        return
    text = (
        f"👤 <b>Haydovchi Profili:</b>\n\n"
        f"🆔 POSITION: <code>{user['position']}</code>\n"
        f"👤 Ism: <b>{user['full_name']}</b>\n"
        f"📱 Telefon: <b>{user['phone']}</b>\n"
        f"🚗 Mashina: <b>{user['car_model']} ({user['car_number']})</b>\n"
        f"💳 Karta: <code>{user['card_number']}</code>\n"
        f"🚕 Yandex: <code>{user['yandex_driver_id'] or 'Ulanmagan'}</code>"
    )
    await message.answer(text, reply_markup=user_main_kb(lang, uid))

@router.message(F.text.in_(["📢 Yangiliklar / Guruh", "📢 Новости / Группа"]))
async def group_handler(message: Message) -> None:
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💬 Haydovchilar guruhiga qo'shilish", url=DRIVER_GROUP_LINK)]])
    await message.answer(f"📢 <b>{BOT_NAME} Haydovchilar Guruhi:</b>", reply_markup=kb)


# ============================================================
# VEB SERVER VA ISHGA TUSHIRISH
# ============================================================

routes = web.RouteTableDef()
@routes.get("/")
@routes.get("/health")
async def health_check(request: web.Request) -> web.Response:
    return web.Response(text="LOCHIN TAXI 24/7 IS RUNNING", status=200)

async def start_web():
    app = web.Application()
    app.add_routes(routes)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

async def main() -> None:
    await init_database()
    dp.include_router(admin_router)
    dp.include_router(router)
    await start_web()
    logger.info(f"🚕 {BOT_NAME} Enterprise tizimi ishga tushdi!")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
