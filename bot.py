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
from aiogram.enums import ParseMode, ContentType
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
# KONFIGURATSIYA
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "lochin_taxi.db"

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

# Aloqa ma'lumotlari
MANAGER_TG_ID = 8934129079
SUPPORT_PHONE = os.getenv("SUPPORT_PHONE", "+998913773200").strip()
SUPPORT_PHONE_DISPLAY = "+998 91 377 32 00"
DRIVER_GROUP_LINK = os.getenv("DRIVER_GROUP_LINK", "https://t.me/+vLyCiiXNvB5kMTUy").strip()

# Yandex Fleet API
YANDEX_API_KEY = os.getenv("YANDEX_API_KEY", "").strip()
YANDEX_CLIENT_ID = os.getenv("YANDEX_CLIENT_ID", "").strip()
YANDEX_PARK_ID = os.getenv("YANDEX_PARK_ID", "").strip()
YANDEX_FLEET_URL = "https://fleet-api.yandex.ru/v1"

# Tizim limitlari
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
            language TEXT NOT NULL DEFAULT 'uz',
            balance REAL DEFAULT 0,
            blocked_balance REAL DEFAULT 0,
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
    logger.info("Baza tayyor!")

init_db()

def fmt_sum(val: float) -> str:
    return f"{int(val):,}".replace(",", " ")


# ============================================================
# MATNLAR VA TARJIMALAR (UZ / RU)
# ============================================================

TEXTS = {
    "uz": {
        "choose_lang": "🌐 <b>Iltimos, tilni tanlang / Пожалуйста, выберите язык:</b>",
        "welcome": f"🕌 <b>Assalomu alaykum!</b>\n\n"
                   f"🚕 <b>{BOT_NAME}</b> taksoparkiga xush kelibsiz! Biz bilan daromadingizni oshiring! 🤝\n\n"
                   f"Tizimdan toʻliq foydalanish va daromadni boshlash uchun roʻyxatdan oʻting.",
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
        "menu_withdraw": "💸 Pul yechish",
        "menu_profile": "👤 Profil",
        "menu_group": "📢 Yangiliklar / Guruh",
        "menu_sos": "🆘 Yordam / SOS",
        "menu_admin": "🛠 Admin Panel",
        "cancel": "❌ Bekor qilish",
        "back": "⬅️ Orqaga",
        "send_phone_btn": "📱 Telefon raqamni yuborish",
        
        # Balans
        "balance_text": f"💰 <b>{{bot_name}} Balansi:</b>\n\n"
                        f"💵 Asosiy balans: <b>{{balance}} so'm</b>\n"
                        f"🔒 Muzlatilgan (yechishda): <b>{{blocked}} so'm</b>\n"
                        f"✅ Yechish mumkin: <b>{{avail}} so'm</b>\n\n"
                        f"🚕 Yandex Pro balansi: <b>{{y_balance}}</b>",
        "withdraw_min_err": "❌ Minimal yechish summasi: {min_w} so'm",
        "withdraw_no_money": "❌ Balansingizda yetarli mablag' mavjud emas!",
        "withdraw_ask": f"💸 <b>Pul yechish:</b>\n\n"
                        f"🔹 Yechish mumkin: <b>{{avail}} so'm</b>\n"
                        f"🔹 Minimal summa: <b>{{min_w}} so'm</b>\n"
                        f"🔹 Komissiya: <b>{{comm}}%</b>\n\n"
                        f"Yechmoqchi bo'lgan summani kiriting (Masalan: <i>50000</i>):",
        "withdraw_confirm": "💳 <b>Pul yechishni tasdiqlaysizmi?</b>\n\n"
                             "💰 Yechilayotgan summa: <b>{amount} so'm</b>\n"
                             "📊 Komissiya ({comm}%): <b>{comm_amount} so'm</b>\n"
                             "💵 Kartaga tushadi: <b>{net_amount} so'm</b>\n"
                             "💳 Karta raqam: <code>{card}</code>",
        "withdraw_sent": "✅ <b>Arizangiz qabul qilindi!</b>\nMenejer tekshirib, pulni kartangizga o'tkazib beradi.",
        "withdraw_cancel": "❌ Pul yechish bekor qilindi.",

        # SOS / Yordam
        "sos_title": "🆘 <b>Tezkor Yordam va Aloqa Markazi</b>\n\nKerakli bo'limni tanlang:",
        "sos_btn_loc": "📍 Lokatsiya yuborish (DTP / Yo'lda qoldim)",
        "sos_btn_msg": "✍️ Menejerga xabar / Shikoyat yozish",
        "sos_btn_call": "📞 Menejerga qo'ng'iroq",
        "sos_btn_chat": "💬 Menejer bilan shaxsiy chat",
        "sos_ask_loc": "📍 Iltimos, pastdagi <b>[📍 Hozirgi joylashuvimni yuborish]</b> tugmasini bosing:",
        "sos_loc_btn": "📍 Hozirgi joylashuvimni yuborish",
        "sos_ask_msg": "✍️ <b>Muammo yoki savolingizni yozing:</b>\n<i>(Mijoz bilan mojaro, to'lov yoki boshqa vaziyat haqida batafsil yozing)</i>",
        "sos_sent": "🚨 <b>Xabaringiz Bosh Menejerga yetkazildi!</b>\nTez orada siz bilan bog'lanishadi.",
    },
    "ru": {
        "choose_lang": "🌐 <b>Пожалуйста, выберите язык / Iltimos, tilni tanlang:</b>",
        "welcome": f"🕌 <b>Ассаламу алейкум!</b>\n\n"
                   f"🚕 Добро пожаловать в таксопарк <b>{BOT_NAME}</b>! Увеличьте свой доход вместе с нами! 🤝\n\n"
                   f"Для начала работы пройдите быструю регистрацию.",
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
        "menu_withdraw": "💸 Вывод средств",
        "menu_profile": "👤 Профиль",
        "menu_group": "📢 Новости / Группа",
        "menu_sos": "🆘 Помощь / SOS",
        "menu_admin": "🛠 Админ Панель",
        "cancel": "❌ Отмена",
        "back": "⬅️ Назад",
        "send_phone_btn": "📱 Отправить номер телефона",
        
        # Баланс
        "balance_text": f"💰 <b>Баланс в {{bot_name}}:</b>\n\n"
                        f"💵 Основной баланс: <b>{{balance}} сум</b>\n"
                        f"🔒 Заблокировано: <b>{{blocked}} сум</b>\n"
                        f"✅ Доступно к выводу: <b>{{avail}} сум</b>\n\n"
                        f"🚕 Баланс в Яндекс Про: <b>{{y_balance}}</b>",
        "withdraw_min_err": "❌ Минимальная сумма вывода: {min_w} сум",
        "withdraw_no_money": "❌ На вашем балансе недостаточно средств!",
        "withdraw_ask": f"💸 <b>Вывод средств:</b>\n\n"
                        f"🔹 Доступно: <b>{{avail}} сум</b>\n"
                        f"🔹 Мин. сумма: <b>{{min_w}} сум</b>\n"
                        f"🔹 Комиссия: <b>{{comm}}%</b>\n\n"
                        f"Введите сумму для вывода (Пример: <i>50000</i>):",
        "withdraw_confirm": "💳 <b>Подтверждаете вывод средств?</b>\n\n"
                             "💰 Сумма: <b>{amount} сум</b>\n"
                             "📊 Комиссия ({comm}%): <b>{comm_amount} сум</b>\n"
                             "💵 К зачислению на карту: <b>{net_amount} сум</b>\n"
                             "💳 Номер карты: <code>{card}</code>",
        "withdraw_sent": "✅ <b>Ваша заявка принята!</b>\nМенеджер проверит и переведет средства на карту.",
        "withdraw_cancel": "❌ Вывод средств отменен.",

        # SOS / Помощь
        "sos_title": "🆘 <b>Центр Экстренной Помощи и Связи</b>\n\nВыберите нужный раздел:",
        "sos_btn_loc": "📍 Отправить локацию (ДТП / В пути)",
        "sos_btn_msg": "✍️ Написать менеджеру / Жалоба",
        "sos_btn_call": "📞 Позвонить менеджеру",
        "sos_btn_chat": "💬 Личный чат с менеджером",
        "sos_ask_loc": "📍 Пожалуйста, нажмите кнопку <b>[📍 Отправить мою локацию]</b> ниже:",
        "sos_loc_btn": "📍 Отправить мою локацию",
        "sos_ask_msg": "✍️ <b>Опишите вашу проблему или вопрос:</b>\n<i>(Конфликт с клиентом, оплата или другая ситуация)</i>",
        "sos_sent": "🚨 <b>Ваше сообщение доставлено Главному Менеджеру!</b>\nСкоро с вами свяжутся.",
    }
}

def get_user_lang(uid: int) -> str:
    conn = get_db()
    row = conn.execute("SELECT language FROM users WHERE telegram_id = ?", (uid,)).fetchone()
    conn.close()
    return row["language"] if row and row["language"] in TEXTS else "uz"

def t(uid_or_lang: Any, key: str, **kwargs) -> str:
    lang = uid_or_lang if isinstance(uid_or_lang, str) else get_user_lang(uid_or_lang)
    text = TEXTS.get(lang, TEXTS["uz"]).get(key, key)
    return text.format(**kwargs) if kwargs else text


# ============================================================
# KEYBOARDS (TUGMALAR)
# ============================================================

def lang_choice_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🇺🇿 O'zbekcha", callback_data="set_lang:uz"),
                InlineKeyboardButton(text="🇷🇺 Русский", callback_data="set_lang:ru")
            ]
        ]
    )

def user_main_kb(uid: int) -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton(text=t(uid, "menu_balance")), KeyboardButton(text=t(uid, "menu_orders"))],
        [KeyboardButton(text=t(uid, "menu_withdraw")), KeyboardButton(text=t(uid, "menu_profile"))],
        [KeyboardButton(text=t(uid, "menu_group")), KeyboardButton(text=t(uid, "menu_sos"))],
    ]
    if uid in ADMIN_IDS:
        buttons.append([KeyboardButton(text=t(uid, "menu_admin"))])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def cancel_kb(uid: int) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=t(uid, "cancel"))]], resize_keyboard=True)

def phone_request_kb(uid: int) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t(uid, "send_phone_btn"), request_contact=True)],
            [KeyboardButton(text=t(uid, "cancel"))]
        ],
        resize_keyboard=True
    )

def location_request_kb(uid: int) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t(uid, "sos_loc_btn"), request_location=True)],
            [KeyboardButton(text=t(uid, "cancel"))]
        ],
        resize_keyboard=True
    )

def sos_menu_kb(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t(uid, "sos_btn_loc"), callback_data="sos:loc")],
            [InlineKeyboardButton(text=t(uid, "sos_btn_msg"), callback_data="sos:msg")],
            [InlineKeyboardButton(text=t(uid, "sos_btn_chat"), url=f"tg://user?id={MANAGER_TG_ID}")],
            [InlineKeyboardButton(text=t(uid, "sos_btn_call"), url=f"tel:{SUPPORT_PHONE}")],
        ]
    )

def admin_main_kb() -> ReplyKeyboardMarkup:
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

class SOSStates(StatesGroup):
    waiting_for_location = State()
    waiting_for_message = State()

class AdminBroadcast(StatesGroup):
    message = State()

class AdminSearch(StatesGroup):
    query = State()


# ============================================================
# DISPATCHER VA ROUTERLAR
# ============================================================

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
router = Router()
admin_router = Router()


# --- START VA TILNI TANLASH ---

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    uid = message.from_user.id

    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (uid,)).fetchone()
    conn.close()

    if user and user["is_registered"] == 1:
        await message.answer(
            t(uid, "already_reg", position=user["position"], name=user["full_name"]),
            reply_markup=user_main_kb(uid)
        )
        return

    # Yangi haydovchiga avval til tanlashni ko'rsatish
    await message.answer(
        "🌐 <b>Iltimos, tilni tanlang / Пожалуйста, выберите язык:</b>",
        reply_markup=lang_choice_kb()
    )


@router.callback_query(F.data.startswith("set_lang:"))
async def set_lang_cb(callback: CallbackQuery, state: FSMContext) -> None:
    lang = callback.data.split(":")[1]
    uid = callback.from_user.id
    now = utc_now_iso()

    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (uid,)).fetchone()
    if not user:
        conn.execute(
            "INSERT INTO users (telegram_id, username, language, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (uid, callback.from_user.username or "", lang, now, now)
        )
    else:
        conn.execute("UPDATE users SET language = ?, updated_at = ? WHERE telegram_id = ?", (lang, now, uid))
    conn.commit()
    conn.close()

    await callback.message.delete()
    
    # Ro'yxatdan o'tish taklifi
    reg_kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t(lang, "register_btn"))]],
        resize_keyboard=True
    )
    await callback.message.answer(t(lang, "welcome"), reply_markup=reg_kb)
    await callback.answer()


# --- RO'YXATDAN O'TISH (REGISTRATION) ---

@router.message(F.text.in_(["📝 Ro'yxatdan o'tish", "📝 Регистрация"]))
async def start_reg(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    await state.set_state(RegStates.name)
    await message.answer(t(uid, "reg_name"), reply_markup=cancel_kb(uid))


@router.message(RegStates.name)
async def reg_name_step(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    if message.text == t(uid, "cancel"):
        await state.clear()
        await message.answer(t(uid, "cancel"), reply_markup=user_main_kb(uid))
        return

    name = message.text.strip()
    if len(name) < 3:
        await message.answer("⚠️ Iltimos, ism va familiyangizni to'liq yozing:")
        return

    await state.update_data(full_name=name)
    await state.set_state(RegStates.phone)
    await message.answer(t(uid, "reg_phone"), reply_markup=phone_request_kb(uid))


@router.message(RegStates.phone)
async def reg_phone_step(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    if message.text == t(uid, "cancel"):
        await state.clear()
        await message.answer(t(uid, "cancel"), reply_markup=user_main_kb(uid))
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
    await message.answer(t(uid, "reg_card"), reply_markup=cancel_kb(uid))


@router.message(RegStates.card)
async def reg_card_step(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    if message.text == t(uid, "cancel"):
        await state.clear()
        await message.answer(t(uid, "cancel"), reply_markup=user_main_kb(uid))
        return

    card = message.text.strip().replace(" ", "")
    if not (card.isdigit() and len(card) == 16):
        await message.answer("⚠️ Karta raqam 16 ta raqamdan iborat bo'lishi kerak. Qaytadan kiriting:")
        return

    await state.update_data(card_number=card)
    await state.set_state(RegStates.car_model)
    await message.answer(t(uid, "reg_car_model"), reply_markup=cancel_kb(uid))


@router.message(RegStates.car_model)
async def reg_car_model_step(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    if message.text == t(uid, "cancel"):
        await state.clear()
        await message.answer(t(uid, "cancel"), reply_markup=user_main_kb(uid))
        return

    await state.update_data(car_model=message.text.strip())
    await state.set_state(RegStates.car_number)
    await message.answer(t(uid, "reg_car_number"), reply_markup=cancel_kb(uid))


@router.message(RegStates.car_number)
async def reg_car_number_step(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    if message.text == t(uid, "cancel"):
        await state.clear()
        await message.answer(t(uid, "cancel"), reply_markup=user_main_kb(uid))
        return

    car_number = message.text.strip().upper()
    data = await state.get_data()
    await state.clear()

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
        t(uid, "reg_success", position=position),
        reply_markup=user_main_kb(uid)
    )

    # Adminlarga yangi haydovchi haqida xabar
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"🆕 <b>Yangi haydovchi ro'yxatdan o'tdi!</b>\n\n"
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
async def show_balance(message: Message) -> None:
    uid = message.from_user.id
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (uid,)).fetchone()
    conn.close()

    if not user or user["is_registered"] != 1:
        await message.answer("Iltimos, avval ro'yxatdan o'ting: /start")
        return

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
        t(uid, "balance_text",
          bot_name=BOT_NAME,
          balance=fmt_sum(cur_bal),
          blocked=fmt_sum(user["blocked_balance"]),
          avail=fmt_sum(avail),
          y_balance=y_bal_text),
        reply_markup=user_main_kb(uid)
    )


@router.message(F.text.in_(["📊 Bugungi buyurtmalar", "📊 Сегодняшние заказы"]))
async def show_today_orders(message: Message) -> None:
    uid = message.from_user.id
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (uid,)).fetchone()
    conn.close()

    if not user or user["is_registered"] != 1:
        await message.answer("Iltimos, avval ro'yxatdan o'ting: /start")
        return

    text = (
        f"📊 <b>Bugungi buyurtmalar va tushumlar:</b>\n\n"
        f"🚕 Barcha bajarilgan safarlar Yandex Pro ilovangiz orqali real vaqtda hisoblab boriladi.\n\n"
        f"💰 Umumiy tushgan mablag' botdagi balansingizda avtomatik yangilanadi."
    )
    await message.answer(text, reply_markup=user_main_kb(uid))


@router.message(F.text.in_(["👤 Profil", "👤 Профиль"]))
async def show_profile(message: Message) -> None:
    uid = message.from_user.id
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (uid,)).fetchone()
    conn.close()

    if not user or user["is_registered"] != 1:
        await message.answer("Iltimos, avval ro'yxatdan o'ting: /start")
        return

    text = (
        f"👤 <b>Haydovchi Profili:</b>\n\n"
        f"🆔 POSITION: <code>{user['position']}</code>\n"
        f"👤 Ism: <b>{user['full_name']}</b>\n"
        f"📱 Telefon: <b>{user['phone']}</b>\n"
        f"🚗 Mashina: <b>{user['car_model']} ({user['car_number']})</b>\n"
        f"💳 Karta: <code>{user['card_number']}</code>\n"
        f"🚕 Yandex ID: <code>{user['yandex_driver_id'] or 'Ulanmagan'}</code>\n"
        f"📅 Qo'shilgan: <b>{user['created_at'][:10]}</b>"
    )
    await message.answer(text, reply_markup=user_main_kb(uid))


@router.message(F.text.in_(["📢 Yangiliklar / Guruh", "📢 Новости / Группа"]))
async def show_group(message: Message) -> None:
    uid = message.from_user.id
    inline_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💬 Haydovchilar guruhiga qo'shilish", url=DRIVER_GROUP_LINK)]
        ]
    )
    await message.answer(
        f"📢 <b>{BOT_NAME} Rasmiy Haydovchilar Guruhi</b>\n\n"
        f"Barcha yangiliklar, e'lonlar va jonli muloqot bizning guruhimizda!\n\n"
        f"Guruhga qo'shilish uchun quyidagi tugmani bosing 👇",
        reply_markup=inline_kb
    )


# --- SOS / YORDAM TIZIMI (DTP, Mojaro, Lokatsiya) ---

@router.message(F.text.in_(["🆘 Yordam / SOS", "🆘 Помощь / SOS"]))
async def sos_main_menu(message: Message) -> None:
    uid = message.from_user.id
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (uid,)).fetchone()
    conn.close()

    if not user or user["is_registered"] != 1:
        await message.answer("Iltimos, avval ro'yxatdan o'ting: /start")
        return

    await message.answer(
        t(uid, "sos_title"),
        reply_markup=sos_menu_kb(uid)
    )


@router.callback_query(F.data == "sos:loc")
async def sos_location_ask(callback: CallbackQuery, state: FSMContext) -> None:
    uid = callback.from_user.id
    await state.set_state(SOSStates.waiting_for_location)
    await callback.message.delete()
    await callback.message.answer(
        t(uid, "sos_ask_loc"),
        reply_markup=location_request_kb(uid)
    )
    await callback.answer()


@router.message(SOSStates.waiting_for_location, F.location)
async def sos_location_receive(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    await state.clear()

    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (uid,)).fetchone()
    conn.close()

    lat = message.location.latitude
    lon = message.location.longitude
    maps_url = f"https://maps.google.com/?q={lat},{lon}"

    # Adminga / Menejerga yuborish
    admin_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💬 Haydovchi bilan yozishish", url=f"tg://user?id={uid}")]
        ]
    )

    alert_text = (
        f"🚨 <b>DIQQAT: HAYDOVCHIDAN SOS / LOKATSIYA!</b>\n\n"
        f"👤 Haydovchi: <b>{user['full_name']}</b> (<code>{user['position']}</code>)\n"
        f"📱 Telefon: <code>{user['phone']}</code>\n"
        f"🚗 Mashina: <b>{user['car_model']} ({user['car_number']})</b>\n\n"
        f"📍 <a href='{maps_url}'>Xaritadagi joylashuvni ochish</a>"
    )

    for adm in ADMIN_IDS:
        try:
            await bot.send_message(adm, alert_text, reply_markup=admin_kb)
            await bot.send_location(adm, latitude=lat, longitude=lon)
        except Exception:
            pass

    await message.answer(t(uid, "sos_sent"), reply_markup=user_main_kb(uid))


@router.callback_query(F.data == "sos:msg")
async def sos_message_ask(callback: CallbackQuery, state: FSMContext) -> None:
    uid = callback.from_user.id
    await state.set_state(SOSStates.waiting_for_message)
    await callback.message.delete()
    await callback.message.answer(
        t(uid, "sos_ask_msg"),
        reply_markup=cancel_kb(uid)
    )
    await callback.answer()


@router.message(SOSStates.waiting_for_message)
async def sos_message_receive(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    if message.text == t(uid, "cancel"):
        await state.clear()
        await message.answer(t(uid, "cancel"), reply_markup=user_main_kb(uid))
        return

    await state.clear()
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (uid,)).fetchone()
    conn.close()

    admin_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💬 Haydovchiga javob yozish", url=f"tg://user?id={uid}")]
        ]
    )

    alert_text = (
        f"⚠️ <b>HAYDOVCHIDAN MUAMMO / MUROJAAT!</b>\n\n"
        f"👤 Haydovchi: <b>{user['full_name']}</b> (<code>{user['position']}</code>)\n"
        f"📱 Tel: <code>{user['phone']}</code>\n"
        f"🚗 Mashina: <b>{user['car_model']} ({user['car_number']})</b>\n\n"
        f"💬 Xabar: <i>{message.text}</i>"
    )

    for adm in ADMIN_IDS:
        try:
            await bot.send_message(adm, alert_text, reply_markup=admin_kb)
        except Exception:
            pass

    await message.answer(t(uid, "sos_sent"), reply_markup=user_main_kb(uid))


# --- PUL YECHISH (WITHDRAWAL) ---

@router.message(F.text.in_(["💸 Pul yechish", "💸 Вывод средств"]))
async def withdraw_init(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (uid,)).fetchone()
    conn.close()

    if not user or user["is_registered"] != 1:
        await message.answer("Iltimos, avval ro'yxatdan o'ting: /start")
        return

    avail = max(0.0, user["balance"] - user["blocked_balance"])
    if avail < MIN_WITHDRAWAL:
        await message.answer(t(uid, "withdraw_no_money") + f"\nMinimal: {fmt_sum(MIN_WITHDRAWAL)} so'm")
        return

    await state.set_state(WithdrawStates.amount)
    await message.answer(
        t(uid, "withdraw_ask",
          avail=fmt_sum(avail),
          min_w=fmt_sum(MIN_WITHDRAWAL),
          comm=COMMISSION_PERCENT),
        reply_markup=cancel_kb(uid)
    )


@router.message(WithdrawStates.amount)
async def withdraw_amount_input(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    if message.text == t(uid, "cancel"):
        await state.clear()
        await message.answer(t(uid, "withdraw_cancel"), reply_markup=user_main_kb(uid))
        return

    raw = message.text.replace(" ", "").replace("so'm", "").strip()
    if not raw.isdigit():
        await message.answer("⚠️ Iltimos, faqat musbat son kiriting:")
        return

    amount = float(raw)
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (uid,)).fetchone()
    conn.close()

    avail = max(0.0, user["balance"] - user["blocked_balance"])
    if amount < MIN_WITHDRAWAL:
        await message.answer(t(uid, "withdraw_min_err", min_w=fmt_sum(MIN_WITHDRAWAL)))
        return

    if amount > avail:
        await message.answer(t(uid, "withdraw_no_money"))
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
        t(uid, "withdraw_confirm",
          amount=fmt_sum(amount),
          comm=COMMISSION_PERCENT,
          comm_amount=fmt_sum(commission),
          net_amount=fmt_sum(net_amount),
          card=user["card_number"]),
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

    await callback.message.edit_text(t(uid, "withdraw_sent"))
    await callback.answer()

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

@admin_router.message(F.text.in_(["🛠 Admin Panel", "🛠 Админ Панель"]))
async def admin_open(message: Message) -> None:
    if message.from_user.id not in ADMIN_IDS:
        return
    await message.answer("🛠 <b>Admin Boshqaruv Paneli:</b>", reply_markup=admin_main_kb())


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
            f"💳 Karta: <code>{w['card_number']}</code> ga o'tkazildi."
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
            f"❌ <b>Pul yechish arizangiz rad etildi!</b>\n\n"
            f"Mablag' ({fmt_sum(w['amount'])} so'm) balansingizga qaytarildi."
        )
    except Exception:
        pass
    await callback.answer()


# --- YUQORI TEZLIKDAGI BROADCAST (5000 HAYDOVCHIGA XABAR) ---

async def async_broadcast_worker(admin_id: int, message: Message):
    conn = get_db()
    users = conn.execute("SELECT telegram_id FROM users WHERE is_registered = 1").fetchall()
    conn.close()

    total = len(users)
    sent = 0
    failed = 0

    status_msg = await bot.send_message(admin_id, f"⏳ Xabar tarqatilmoqda: 0 / {total}...")

    for idx, u in enumerate(users, 1):
        try:
            await message.copy_to(chat_id=u["telegram_id"])
            sent += 1
        except Exception:
            failed += 1

        # Telegram Flood Limitdan saqlanish (sekundiga ~25 ta)
        await asyncio.sleep(0.04)

        if idx % 100 == 0 or idx == total:
            try:
                await status_msg.edit_text(f"⏳ Xabar tarqatilmoqda: {idx} / {total}...")
            except Exception:
                pass

    await bot.send_message(
        admin_id,
        f"✅ <b>Xabar tarqatish yakunlandi!</b>\n\n"
        f"📊 Jami haydovchilar: <b>{total} ta</b>\n"
        f"✅ Yetkazildi: <b>{sent} ta</b>\n"
        f"❌ Yetkazilmadi (bloklagan): <b>{failed} ta</b>"
    )


@admin_router.message(F.text == "📢 Xabar yuborish (Hammaga)")
async def broadcast_prompt(message: Message, state: FSMContext) -> None:
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminBroadcast.message)
    await message.answer("📢 Barcha haydovchilarga yubormoqchi bo'lgan xabaringizni yozing (rasm, video yoki matn):", reply_markup=cancel_kb(message.from_user.id))


@admin_router.message(AdminBroadcast.message)
async def broadcast_send(message: Message, state: FSMContext) -> None:
    if message.text in ["❌ Bekor qilish", "❌ Отмена"]:
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=admin_main_kb())
        return

    await state.clear()
    await message.answer("🚀 Xabar tarqatish orqa fonda boshlandi! Bot qotmasdan ishlayveradi.", reply_markup=admin_main_kb())
    # Orqa fonda alohida task qilib ishga tushirish
    asyncio.create_task(async_broadcast_worker(message.from_user.id, message))


@admin_router.message(F.text == "⬅️ Asosiy menyu")
async def back_to_user_menu(message: Message) -> None:
    await message.answer("Asosiy menyuga qaytdingiz:", reply_markup=user_main_kb(message.from_user.id))


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
    logger.info(f"🌐 Veb-server port {PORT} da ishga tushdi (Render & UptimeRobot).")


# ============================================================
# MAIN
# ============================================================

async def main() -> None:
    dp.include_router(admin_router)
    dp.include_router(router)

    await start_web_server()
    logger.info(f"🚕 {BOT_NAME} Polling orqali ishga tushdi!")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
