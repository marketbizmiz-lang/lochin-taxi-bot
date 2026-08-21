import os
import re
import json
import math
import html
import asyncio
import logging
import sqlite3
from pathlib import Path
from typing import Any, Optional, Dict, List
from datetime import datetime, timezone, timedelta

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
    ReplyKeyboardRemove,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
)


# ============================================================
# CONFIG
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "lochin_taxi.db"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("lochin_taxi_bot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
BASE_URL = os.getenv("BASE_URL", "").rstrip("/")
PORT = int(os.getenv("PORT", "8080"))

BOT_NAME = os.getenv("BOT_NAME", "LOCHIN TAXI").strip() or "LOCHIN TAXI"
DEFAULT_LANGUAGE = os.getenv("DEFAULT_LANGUAGE", "uz").strip() or "uz"

# Admin IDs - бу ерга ўз Telegram ID ингизни ёзинг!
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "").strip()
ADMIN_IDS = {
    int(x.strip())
    for x in ADMIN_IDS_RAW.split(",")
    if x.strip() and x.strip().lstrip("-").isdigit()
}

# Links
CHANNEL_LINK = os.getenv("CHANNEL_LINK", "").strip()
DRIVER_GROUP_LINK = os.getenv("DRIVER_GROUP_LINK", "").strip()
SUPPORT_PHONE = os.getenv("SUPPORT_PHONE", "+998712345678").strip()
SUPPORT_TG = os.getenv("SUPPORT_TG", "@lochin_support").strip()

# Settings
MIN_WITHDRAWAL = int(os.getenv("MIN_WITHDRAWAL", "50000"))
COMMISSION_PERCENT = float(os.getenv("COMMISSION_PERCENT", "2.5"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")


# ============================================================
# BOT
# ============================================================

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# Routers (Расмдагидек, иккала Router ҳам ишлатилади)
driver_router = Router()
admin_router = Router()
web_router = web.RouteTableDef()


# ============================================================
# TEXTS
# ============================================================

TEXTS: dict[str, dict[str, str]] = {
    "uz": {
        "welcome": f"🕌 <b>Assalomu alaykum!</b>\n\n"
                   f"{BOT_NAME} bilan hamkorlik qilganingizdan juda minnatdormiz! 🤝\n\n"
                   f"🚕 Tizimga kirish uchun ro'yxatdan o'ting.",
        "register_btn": "📝 Ro'yxatdan o'tish",
        "register_start": "✅ <b>Ro'yxatdan o'tish boshlanmoqda</b>\n\n"
                          f"Iltimos, quyidagi ma'lumotlarni to'ldiring:",
        
        "register_name": "👤 <b>Ism va familiyangizni kiriting:</b>\n\n"
                         f"Misol: <i>Aliyev Alisher</i>",
        "register_phone": "📱 <b>Telefon raqamingizni kiriting:</b>\n\n"
                          f"Misol: <i>+998901234567</i>",
        "register_card": "💳 <b>Karta raqamingizni kiriting:</b>\n\n"
                         f"Misol: <i>8600 1234 5678 9012</i>",
        "register_car_model": "🚗 <b>Avtomobilingiz markasini kiriting:</b>\n\n"
                              f"Misol: <i>Chevrolet Lacetti</i>",
        "register_car_number": "🔢 <b>Avtomobilingiz davlat raqamini kiriting:</b>\n\n"
                               f"Misol: <i>01 A 123 AA</i>",
        
        "register_success": f"✅ <b>Tabriklaymiz!</b>\n\n"
                           f"Siz {BOT_NAME} tizimida muvaffaqiyatli ro'yxatdan o'tdingiz!\n\n"
                           f"🆔 Sizning POSITIONingiz: <code>{{position}}</code>\n\n"
                           f"🔑 Bu kod sizning shaxsiy identifikatoringiz. Uni saqlab qo'ying!\n\n"
                           f"🚕 Endi tizimdan to'liq foydalanishingiz mumkin.",
        
        "register_cancel": "❌ Ro'yxatdan o'tish bekor qilindi.",
        "register_again": "🔄 Qayta ro'yxatdan o'tish uchun /start bosing.",
        "already_registered": "✅ Siz allaqachon ro'yxatdan o'tgansiz!\n\n"
                              f"🆔 POSITION: <code>{{position}}</code>",
        
        "invalid_name": "❌ Ism familiya noto'g'ri. Iltimos, qaytadan kiriting.",
        "invalid_phone": "❌ Telefon raqam noto'g'ri. Format: +998901234567",
        "invalid_card": "❌ Karta raqam noto'g'ri. 16 ta raqam bo'lishi kerak.",
        "invalid_car_model": "❌ Avtomobil markasi noto'g'ri. Qaytadan kiriting.",
        "invalid_car_number": "❌ Davlat raqam noto'g'ri. Qaytadan kiriting.",
        
        # Driver menu
        "main_menu": "🚕 <b>Asosiy menyu</b>\n\n"
                     f"Xush kelibsiz, {{name}}!\n"
                     f"🆔 POSITION: <code>{{position}}</code>",
        "menu_balance": "💰 Balans",
        "menu_today_orders": "📊 Bugungi buyurtmalar",
        "menu_withdraw": "💸 Pul yechish",
        "menu_history": "📜 To'lovlar tarixi",
        "menu_profile": "👤 Profil",
        "menu_news": "📢 Yangiliklar",
        "menu_group": "💬 Haydovchilar guruhi",
        "menu_support": "🆘 Yordam",
        "menu_settings": "⚙️ Sozlamalar",
        "menu_admin": "🛠 Admin",
        "menu_back": "⬅️ Orqaga",
        "menu_cancel": "❌ Bekor qilish",
        
        # Admin menu
        "admin_title": "🛠 Admin panel",
        "admin_drivers": "👥 Haydovchilar",
        "admin_search": "🔎 Qidirish",
        "admin_balances": "💰 Balanslar",
        "admin_withdrawals": "💸 Pul yechishlar",
        "admin_send_news": "📢 Yangilik yuborish",
        "admin_stats": "📊 Statistika",
        "admin_block": "🚫 Bloklash",
        "admin_managers": "👨‍💼 Menejerlar",
        "admin_group": "💬 Guruh boshqaruvi",
        "admin_logs": "📝 Loglar",
        "admin_back": "⬅️ Menyuga",
        "not_admin": "Bu bo'lim faqat adminlar uchun 🚫",
        
        # Balance
        "balance_title": f"💰 <b>{BOT_NAME} da balans</b>",
        "balance_current": "Joriy balans",
        "balance_blocked": "Bloklangan balans",
        "balance_available": "Mavjud balans",
        
        # Orders
        "orders_today_title": "📊 <b>Bugungi buyurtmalar</b>",
        "orders_today_empty": "Bugun hech qanday buyurtma yo'q 📭",
        "orders_total": "Jami buyurtmalar",
        "orders_earnings": "Daromad",
        "order_id": "Buyurtma raqami",
        "order_time": "Vaqt",
        "order_amount": "Summa",
        
        # Withdraw
        "withdraw_title": f"💸 <b>{BOT_NAME} dan pul yechish</b>",
        "withdraw_available": "Yechish mumkin",
        "withdraw_min": "Minimal yechish",
        "withdraw_commission": "Komissiya",
        "withdraw_amount_ask": "Yechmoqchi bo'lgan summani kiriting:",
        "withdraw_type_ask": "Qanday usulda yechmoqchisiz?",
        "withdraw_card_ask": "Karta raqamini kiriting:",
        "withdraw_phone_ask": "Telefon raqamini kiriting (+998...):",
        "withdraw_confirm": "Yechishni tasdiqlaysizmi?",
        "withdraw_success": "✅ Arizangiz qabul qilindi!",
        "withdraw_fail": "❌ Pul yechish amalga oshmadi",
        "withdraw_min_error": "Minimal yechish summasi {min} so'm",
        "withdraw_balance_error": "Balansingizda yetarli mablag' yo'q",
        "withdraw_pending": "⏳ Arizangiz ko'rib chiqilmoqda",
        "withdraw_completed": "✅ Pul yechildi",
        "withdraw_cancelled": "❌ Bekor qilindi",
        "withdraw_type_card": "💳 Kartaga",
        "withdraw_type_cash": "💵 Naqd",
        "withdraw_type_brb": "🏦 BRB 24/7",
        
        # Profile
        "profile_title": "👤 <b>Mening profilim</b>",
        "profile_id": "ID",
        "profile_position": "POSITION",
        "profile_name": "Ism",
        "profile_phone": "Telefon",
        "profile_card": "Karta raqami",
        "profile_car": "Mashina",
        "profile_car_number": "Mashina raqami",
        "profile_rating": "Reyting",
        "profile_orders": "Buyurtmalar",
        "profile_earnings": "Daromad",
        "profile_joined": "Qo'shilgan vaqt",
        "profile_status": "Holat",
        "profile_status_active": "🟢 Faol",
        "profile_status_blocked": "🔴 Bloklangan",
        
        # News
        "news_title": "📢 <b>Yangiliklar</b>",
        "news_empty": "Hozircha yangiliklar yo'q 📭",
        "news_date": "Sana",
        "news_views": "Ko'rishlar",
        
        # Support
        "support_title": "🆘 <b>Yordam</b>",
        "support_phone": "📞 Telefon",
        "support_tg": "📱 Telegram",
        "support_group": "💬 Guruh",
        "support_channel": "📢 Kanal",
        "support_text": "Savol yoki muammolaringiz bo'lsa, quyidagi manzillar orqali bog'lanishingiz mumkin:",
        
        # Settings
        "settings_title": "⚙️ <b>Sozlamalar</b>",
        "settings_lang": "🌐 Til",
        "settings_saved": "✅ Sozlamalar saqlandi!",
        
        # Common
        "yes": "✅ Ha",
        "no": "❌ Yo'q",
        "back": "⬅️ Orqaga",
        "cancel": "❌ Bekor qilish",
        "action_cancelled": "Amal bekor qilindi ❌",
        "invalid_input": "❌ Noto'g'ri ma'lumot, qaytadan kiriting",
        
        # Language
        "lang_updated": "✅ Til muvaffaqiyatli o'zgartirildi!",
        "choose_language": "🌐 Tilni tanlang:\n\nВыберите язык:",
        
        # Statuses
        "status_new": "🆕 Yangi",
        "status_completed": "✅ Bajarilgan",
        "status_cancelled": "❌ Bekor qilingan",
        "payment_pending": "⏳ Kutilmoqda",
        "payment_completed": "✅ To'langan",
    },
    "ru": {
        "welcome": f"🕌 <b>Ассаламу алейкум!</b>\n\n"
                   f"Мы очень благодарны вам за сотрудничество с {BOT_NAME}! 🤝\n\n"
                   f"🚕 Для входа в систему пройдите регистрацию.",
        "register_btn": "📝 Пройти регистрацию",
        "register_start": "✅ <b>Регистрация начинается</b>\n\n"
                          f"Пожалуйста, заполните следующие данные:",
        
        "register_name": "👤 <b>Введите ваше имя и фамилию:</b>\n\n"
                         f"Пример: <i>Алиев Алишер</i>",
        "register_phone": "📱 <b>Введите ваш номер телефона:</b>\n\n"
                          f"Пример: <i>+998901234567</i>",
        "register_card": "💳 <b>Введите номер вашей карты:</b>\n\n"
                         f"Пример: <i>8600 1234 5678 9012</i>",
        "register_car_model": "🚗 <b>Введите марку вашего автомобиля:</b>\n\n"
                              f"Пример: <i>Chevrolet Lacetti</i>",
        "register_car_number": "🔢 <b>Введите госномер автомобиля:</b>\n\n"
                               f"Пример: <i>01 A 123 AA</i>",
        
        "register_success": f"✅ <b>Поздравляем!</b>\n\n"
                           f"Вы успешно зарегистрировались в системе {BOT_NAME}!\n\n"
                           f"🆔 Ваш POSITION: <code>{{position}}</code>\n\n"
                           f"🔑 Сохраните этот код - это ваш личный идентификатор.\n\n"
                           f"🚕 Теперь вы можете полноценно пользоваться системой.",
        
        "register_cancel": "❌ Регистрация отменена.",
        "register_again": "🔄 Для повторной регистрации нажмите /start.",
        "already_registered": "✅ Вы уже зарегистрированы!\n\n"
                              f"🆔 POSITION: <code>{{position}}</code>",
        
        "invalid_name": "❌ Неверное имя. Пожалуйста, введите заново.",
        "invalid_phone": "❌ Неверный номер телефона. Формат: +998901234567",
        "invalid_card": "❌ Неверный номер карты. Должно быть 16 цифр.",
        "invalid_car_model": "❌ Неверная марка автомобиля. Введите заново.",
        "invalid_car_number": "❌ Неверный госномер. Введите заново.",
        
        # Driver menu
        "main_menu": "🚕 <b>Главное меню</b>\n\n"
                     f"Добро пожаловать, {{name}}!\n"
                     f"🆔 POSITION: <code>{{position}}</code>",
        "menu_balance": "💰 Баланс",
        "menu_today_orders": "📊 Сегодняшние заказы",
        "menu_withdraw": "💸 Вывод средств",
        "menu_history": "📜 История платежей",
        "menu_profile": "👤 Профиль",
        "menu_news": "📢 Новости",
        "menu_group": "💬 Группа водителей",
        "menu_support": "🆘 Помощь",
        "menu_settings": "⚙️ Настройки",
        "menu_admin": "🛠 Админ",
        "menu_back": "⬅️ Назад",
        "menu_cancel": "❌ Отмена",
        
        # Admin menu
        "admin_title": "🛠 Админ панель",
        "admin_drivers": "👥 Водители",
        "admin_search": "🔎 Поиск",
        "admin_balances": "💰 Балансы",
        "admin_withdrawals": "💸 Вывод средств",
        "admin_send_news": "📢 Отправить новость",
        "admin_stats": "📊 Статистика",
        "admin_block": "🚫 Блокировка",
        "admin_managers": "👨‍💼 Менеджеры",
        "admin_group": "💬 Управление группой",
        "admin_logs": "📝 Логи",
        "admin_back": "⬅️ В меню",
        "not_admin": "Этот раздел только для администратора 🚫",
        
        # Balance
        "balance_title": f"💰 <b>Баланс в {BOT_NAME}</b>",
        "balance_current": "Текущий баланс",
        "balance_blocked": "Заблокированный баланс",
        "balance_available": "Доступный баланс",
        
        # Orders
        "orders_today_title": "📊 <b>Сегодняшние заказы</b>",
        "orders_today_empty": "Сегодня нет заказов 📭",
        "orders_total": "Всего заказов",
        "orders_earnings": "Заработок",
        "order_id": "Номер заказа",
        "order_time": "Время",
        "order_amount": "Сумма",
        
        # Withdraw
        "withdraw_title": f"💸 <b>Вывод средств из {BOT_NAME}</b>",
        "withdraw_available": "Доступно для вывода",
        "withdraw_min": "Минимальный вывод",
        "withdraw_commission": "Комиссия",
        "withdraw_amount_ask": "Введите сумму для вывода:",
        "withdraw_type_ask": "Способ вывода:",
        "withdraw_card_ask": "Введите номер карты:",
        "withdraw_phone_ask": "Введите номер телефона (+998...):",
        "withdraw_confirm": "Подтверждаете вывод?",
        "withdraw_success": "✅ Заявка принята!",
        "withdraw_fail": "❌ Вывод средств не выполнен",
        "withdraw_min_error": "Минимальная сумма вывода {min} сум",
        "withdraw_balance_error": "Недостаточно средств на балансе",
        "withdraw_pending": "⏳ Заявка рассматривается",
        "withdraw_completed": "✅ Средства выведены",
        "withdraw_cancelled": "❌ Отменено",
        "withdraw_type_card": "💳 На карту",
        "withdraw_type_cash": "💵 Наличными",
        "withdraw_type_brb": "🏦 BRB 24/7",
        
        # Profile
        "profile_title": "👤 <b>Мой профиль</b>",
        "profile_id": "ID",
        "profile_position": "POSITION",
        "profile_name": "Имя",
        "profile_phone": "Телефон",
        "profile_card": "Номер карты",
        "profile_car": "Машина",
        "profile_car_number": "Номер машины",
        "profile_rating": "Рейтинг",
        "profile_orders": "Заказов",
        "profile_earnings": "Заработок",
        "profile_joined": "Дата регистрации",
        "profile_status": "Статус",
        "profile_status_active": "🟢 Активен",
        "profile_status_blocked": "🔴 Заблокирован",
        
        # News
        "news_title": "📢 <b>Новости</b>",
        "news_empty": "Пока нет новостей 📭",
        "news_date": "Дата",
        "news_views": "Просмотров",
        
        # Support
        "support_title": "🆘 <b>Помощь</b>",
        "support_phone": "📞 Телефон",
        "support_tg": "📱 Telegram",
        "support_group": "💬 Группа",
        "support_channel": "📢 Канал",
        "support_text": "По всем вопросам обращайтесь по следующим контактам:",
        
        # Settings
        "settings_title": "⚙️ <b>Настройки</b>",
        "settings_lang": "🌐 Язык",
        "settings_saved": "✅ Настройки сохранены!",
        
        # Common
        "yes": "✅ Да",
        "no": "❌ Нет",
        "back": "⬅️ Назад",
        "cancel": "❌ Отмена",
        "action_cancelled": "Действие отменено ❌",
        "invalid_input": "❌ Неверные данные, попробуйте снова",
        
        # Language
        "lang_updated": "✅ Язык успешно изменен!",
        "choose_language": "🌐 Выберите язык:\n\nTilni tanlang:",
        
        # Statuses
        "status_new": "🆕 Новый",
        "status_completed": "✅ Выполнен",
        "status_cancelled": "❌ Отменён",
        "payment_pending": "⏳ Ожидает",
        "payment_completed": "✅ Оплачен",
    }
}

SUPPORTED_LANGS = ("uz", "ru")


# ============================================================
# DB
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
            block_reason TEXT,
            is_registered INTEGER DEFAULT 0,
            total_orders INTEGER DEFAULT 0,
            total_earnings REAL DEFAULT 0,
            rating REAL DEFAULT 5.0,
            rating_count INTEGER DEFAULT 0,
            notifications_enabled INTEGER DEFAULT 1,
            auto_withdrawal INTEGER DEFAULT 0,
            auto_withdrawal_min REAL DEFAULT 100000,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_activity TEXT
        )
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            payment_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            description TEXT,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
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
            admin_approved_by INTEGER,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            cancelled_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            driver_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            commission REAL DEFAULT 0,
            net_amount REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'new',
            payment_status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            FOREIGN KEY (driver_id) REFERENCES users(id)
        )
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title_uz TEXT,
            title_ru TEXT,
            content_uz TEXT,
            content_ru TEXT,
            image_file_id TEXT,
            is_active INTEGER DEFAULT 1,
            is_pinned INTEGER DEFAULT 0,
            created_by INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            views_count INTEGER DEFAULT 0,
            sent_count INTEGER DEFAULT 0
        )
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS driver_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            driver_id INTEGER,
            action TEXT,
            details TEXT,
            ip_address TEXT,
            created_at TEXT NOT NULL
        )
    """)
    
    cur.execute("CREATE INDEX IF NOT EXISTS idx_users_telegram ON users(telegram_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_users_position ON users(position)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_orders_driver ON orders(driver_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_transactions_user ON transactions(user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_withdrawals_user ON withdrawals(user_id)")
    
    conn.commit()
    conn.close()
    logger.info("Database initialized")


# ============================================================
# HELPERS
# ============================================================

def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default

def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default

def ensure_lang(lang: str) -> str:
    return lang if lang in SUPPORTED_LANGS else DEFAULT_LANGUAGE

def get_user_lang(telegram_id: int) -> str:
    conn = get_db()
    row = conn.execute("SELECT language FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
    conn.close()
    return row["language"] if row and row["language"] in SUPPORTED_LANGS else DEFAULT_LANGUAGE

def t(user_id_or_lang: int | str, key: str) -> str:
    if isinstance(user_id_or_lang, int):
        lang = get_user_lang(user_id_or_lang)
    else:
        lang = ensure_lang(user_id_or_lang)
    return TEXTS.get(lang, TEXTS[DEFAULT_LANGUAGE]).get(key, key)

def fmt_sum(value: Any) -> str:
    return f"{safe_int(value):,}".replace(",", " ") + " so'm"

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def normalize_phone(phone: str) -> str:
    value = (phone or "").strip()
    value = value.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if value.startswith("998") and not value.startswith("+998"):
        value = "+" + value
    return value

def is_valid_phone(phone: str) -> bool:
    return bool(re.fullmatch(r"\+998\d{9}", normalize_phone(phone)))

def normalize_card(card: str) -> str:
    return re.sub(r"\s+", "", (card or "").strip())

def is_valid_card(card: str) -> bool:
    card = normalize_card(card)
    return bool(re.fullmatch(r"\d{16}", card))

def generate_position() -> str:
    import random
    import string
    chars = string.ascii_uppercase + string.digits
    code = ''.join(random.choices(chars, k=5))
    position = f"LCH-{code}"
    
    conn = get_db()
    exists = conn.execute("SELECT id FROM users WHERE position = ?", (position,)).fetchone()
    conn.close()
    
    if exists:
        return generate_position()
    return position

def upsert_user(telegram_id: int, username: Optional[str], full_name: Optional[str]) -> dict:
    now = utc_now_iso()
    conn = get_db()
    
    existing = conn.execute(
        "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
    ).fetchone()
    
    if existing:
        conn.execute(
            """
            UPDATE users SET 
                username = ?, full_name = ?, updated_at = ?, last_activity = ?
            WHERE telegram_id = ?
            """,
            (username or "", full_name or "", now, now, telegram_id)
        )
        conn.commit()
        user = conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()
        conn.close()
        return dict(user)
    
    conn.execute(
        """
        INSERT INTO users (telegram_id, username, full_name, language, created_at, updated_at, last_activity)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (telegram_id, username or "", full_name or "", DEFAULT_LANGUAGE, now, now, now)
    )
    conn.commit()
    user = conn.execute(
        "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
    ).fetchone()
    conn.close()
    return dict(user)

def complete_registration(
    telegram_id: int,
    full_name: str,
    phone: str,
    card_number: str,
    car_model: str,
    car_number: str
) -> Optional[str]:
    conn = get_db()
    
    existing = conn.execute(
        "SELECT is_registered FROM users WHERE telegram_id = ?", (telegram_id,)
    ).fetchone()
    
    if existing and existing["is_registered"] == 1:
        conn.close()
        return None
    
    position = generate_position()
    now = utc_now_iso()
    
    conn.execute(
        """
        UPDATE users SET 
            full_name = ?,
            phone = ?,
            card_number = ?,
            car_model = ?,
            car_number = ?,
            position = ?,
            is_registered = 1,
            updated_at = ?,
            last_activity = ?
        WHERE telegram_id = ?
        """,
        (full_name, phone, card_number, car_model, car_number, position, now, now, telegram_id)
    )
    
    conn.commit()
    conn.close()
    return position

def get_user_by_telegram(telegram_id: int) -> Optional[dict]:
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def get_user_by_position(position: str) -> Optional[dict]:
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE position = ?", (position,)).fetchone()
    conn.close()
    return dict(row) if row else None

def update_user_balance(telegram_id: int, amount: float) -> bool:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET balance = balance + ?, updated_at = ? WHERE telegram_id = ?",
        (amount, utc_now_iso(), telegram_id)
    )
    ok = cur.rowcount > 0
    conn.commit()
    conn.close()
    return ok

def get_all_drivers(limit: int = 100) -> List[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM users WHERE role = 'driver' AND is_registered = 1 ORDER BY id DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]

def search_drivers(query: str) -> List[dict]:
    conn = get_db()
    rows = conn.execute(
        """
        SELECT * FROM users 
        WHERE role = 'driver' AND is_registered = 1 AND (
            telegram_id LIKE ? OR 
            username LIKE ? OR 
            full_name LIKE ? OR 
            phone LIKE ? OR 
            car_number LIKE ? OR
            position LIKE ?
        )
        ORDER BY id DESC
        """,
        (f"%{query}%", f"%{query}%", f"%{query}%", f"%{query}%", f"%{query}%", f"%{query}%")
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_today_orders(telegram_id: int) -> List[dict]:
    today = datetime.now().date().isoformat()
    conn = get_db()
    rows = conn.execute(
        """
        SELECT * FROM orders 
        WHERE driver_id = (SELECT id FROM users WHERE telegram_id = ?)
        AND date(created_at) = ?
        ORDER BY created_at DESC
        """,
        (telegram_id, today)
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_driver_stats(telegram_id: int) -> dict:
    conn = get_db()
    cur = conn.cursor()
    
    today = datetime.now().date().isoformat()
    today_orders = cur.execute(
        """
        SELECT COUNT(*) as count, SUM(net_amount) as sum 
        FROM orders 
        WHERE driver_id = (SELECT id FROM users WHERE telegram_id = ?)
        AND date(created_at) = ? AND status = 'completed'
        """,
        (telegram_id, today)
    ).fetchone()
    
    total = cur.execute(
        """
        SELECT COUNT(*) as count, SUM(net_amount) as sum 
        FROM orders 
        WHERE driver_id = (SELECT id FROM users WHERE telegram_id = ?)
        AND status = 'completed'
        """,
        (telegram_id,)
    ).fetchone()
    
    conn.close()
    return {
        "today_count": safe_int(today_orders[0] if today_orders else 0),
        "today_sum": safe_float(today_orders[1] if today_orders else 0),
        "total_count": safe_int(total[0] if total else 0),
        "total_sum": safe_float(total[1] if total else 0),
    }

def get_transactions(telegram_id: int, limit: int = 50) -> List[dict]:
    conn = get_db()
    rows = conn.execute(
        """
        SELECT * FROM transactions 
        WHERE user_id = (SELECT id FROM users WHERE telegram_id = ?)
        ORDER BY created_at DESC LIMIT ?
        """,
        (telegram_id, limit)
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_withdrawals(telegram_id: int, limit: int = 20) -> List[dict]:
    conn = get_db()
    rows = conn.execute(
        """
        SELECT * FROM withdrawals 
        WHERE user_id = (SELECT id FROM users WHERE telegram_id = ?)
        ORDER BY created_at DESC LIMIT ?
        """,
        (telegram_id, limit)
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]

def create_withdrawal(
    telegram_id: int,
    amount: float,
    payment_type: str,
    card_number: str = "",
    phone_number: str = ""
) -> Optional[int]:
    conn = get_db()
    cur = conn.cursor()
    
    user_row = cur.execute(
        "SELECT id, balance FROM users WHERE telegram_id = ?", (telegram_id,)
    ).fetchone()
    
    if not user_row:
        conn.close()
        return None
    
    user_id = user_row["id"]
    balance = safe_float(user_row["balance"])
    
    if balance < amount:
        conn.close()
        return None
    
    commission = amount * (COMMISSION_PERCENT / 100)
    net_amount = amount - commission
    now = utc_now_iso()
    
    cur.execute(
        """
        INSERT INTO withdrawals (
            user_id, amount, commission, net_amount, payment_type,
            card_number, phone_number, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, amount, commission, net_amount, payment_type, card_number, phone_number, "pending", now)
    )
    
    withdrawal_id = cur.lastrowid
    
    cur.execute(
        "UPDATE users SET balance = balance - ?, blocked_balance = blocked_balance + ?, updated_at = ? WHERE id = ?",
        (amount, amount, now, user_id)
    )
    
    conn.commit()
    conn.close()
    return withdrawal_id

def add_driver_log(driver_id: int, action: str, details: str = "", ip: str = "") -> None:
    conn = get_db()
    now = utc_now_iso()
    conn.execute(
        """
        INSERT INTO driver_logs (driver_id, action, details, ip_address, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (driver_id, action, details, ip, now)
    )
    conn.commit()
    conn.close()


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
    confirm = State()

class AdminStates(StatesGroup):
    search_driver = State()


# ============================================================
# KEYBOARDS
# ============================================================

def welcome_keyboard(telegram_id: int) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t(telegram_id, "register_btn"))]
        ],
        resize_keyboard=True
    )

def user_main_menu(telegram_id: int) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=t(telegram_id, "menu_balance"))],
        [KeyboardButton(text=t(telegram_id, "menu_today_orders"))],
        [KeyboardButton(text=t(telegram_id, "menu_withdraw"))],
        [KeyboardButton(text=t(telegram_id, "menu_history"))],
        [KeyboardButton(text=t(telegram_id, "menu_profile"))],
        [KeyboardButton(text=t(telegram_id, "menu_news"))],
        [KeyboardButton(text=t(telegram_id, "menu_group"))],
        [KeyboardButton(text=t(telegram_id, "menu_support"))],
        [KeyboardButton(text=t(telegram_id, "menu_settings"))],
    ]
    if is_admin(telegram_id):
        rows.append([KeyboardButton(text=t(telegram_id, "menu_admin"))])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)

def admin_main_menu(telegram_id: int) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t(telegram_id, "admin_drivers"))],
            [KeyboardButton(text=t(telegram_id, "admin_search"))],
            [KeyboardButton(text=t(telegram_id, "admin_balances"))],
            [KeyboardButton(text=t(telegram_id, "admin_withdrawals"))],
            [KeyboardButton(text=t(telegram_id, "admin_send_news"))],
            [KeyboardButton(text=t(telegram_id, "admin_stats"))],
            [KeyboardButton(text=t(telegram_id, "admin_block"))],
            [KeyboardButton(text=t(telegram_id, "admin_managers"))],
            [KeyboardButton(text=t(telegram_id, "admin_group"))],
            [KeyboardButton(text=t(telegram_id, "admin_logs"))],
            [KeyboardButton(text=t(telegram_id, "admin_back"))],
        ],
        resize_keyboard=True
    )

def cancel_keyboard(telegram_id: int) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t(telegram_id, "cancel"))]],
        resize_keyboard=True
    )

def yes_no_keyboard(telegram_id: int) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t(telegram_id, "yes")), KeyboardButton(text=t(telegram_id, "no"))]
        ],
        resize_keyboard=True
    )

def withdraw_type_keyboard(telegram_id: int) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t(telegram_id, "withdraw_type_card"))],
            [KeyboardButton(text=t(telegram_id, "withdraw_type_cash"))],
            [KeyboardButton(text=t(telegram_id, "withdraw_type_brb"))],
            [KeyboardButton(text=t(telegram_id, "cancel"))],
        ],
        resize_keyboard=True
    )

def language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🇺🇿 O'zbekcha", callback_data="lang:set:uz")],
            [InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang:set:ru")],
        ]
    )


# ============================================================
# HANDLERS (МУҲИМ ҚИСМ: Барча тугмалар ишлаши учун)
# ============================================================

@driver_router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    user = upsert_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    
    if user.get("is_registered", 0) == 1:
        await message.answer(
            t(message.from_user.id, "already_registered").format(position=user.get("position", "N/A")),
            reply_markup=user_main_menu(message.from_user.id)
        )
        return
    
    await message.answer(
        t(message.from_user.id, "choose_language"),
        reply_markup=language_keyboard()
    )

@driver_router.callback_query(F.data.startswith("lang:set:"))
async def set_language_callback(callback: CallbackQuery) -> None:
    lang = callback.data.split(":")[-1]
    if lang in SUPPORTED_LANGS:
        conn = get_db()
        conn.execute(
            "UPDATE users SET language = ?, updated_at = ? WHERE telegram_id = ?",
            (lang, utc_now_iso(), callback.from_user.id)
        )
        conn.commit()
        conn.close()
        
        await callback.message.delete()
        await callback.message.answer(
            t(lang, "welcome"),
            reply_markup=welcome_keyboard(callback.from_user.id)
        )
    await callback.answer()

# Глобал "cancel" текширувчи функция: FSM ичида "❌ Bekor qilish" босилса
async def is_cancel(message: Message) -> bool:
    return message.text in [TEXTS['uz']['cancel'], TEXTS['ru']['cancel']]

@driver_router.message(F.text.in_([TEXTS['uz']['register_btn'], TEXTS['ru']['register_btn']]))
async def start_registration(message: Message, state: FSMContext) -> None:
    user = get_user_by_telegram(message.from_user.id)
    if user and user.get("is_registered", 0) == 1:
        await message.answer(
            t(message.from_user.id, "already_registered").format(position=user.get("position", "N/A")),
            reply_markup=user_main_menu(message.from_user.id)
        )
        return
    
    await state.set_state(RegisterStates.name)
    await message.answer(
        t(message.from_user.id, "register_start") + "\n\n" +
        t(message.from_user.id, "register_name"),
        reply_markup=cancel_keyboard(message.from_user.id)
    )

@driver_router.message(RegisterStates.name)
async def register_name(message: Message, state: FSMContext) -> None:
    if await is_cancel(message):
        await state.clear()
        await message.answer(t(message.from_user.id, "register_cancel"), reply_markup=welcome_keyboard(message.from_user.id))
        return
    
    name = message.text.strip()
    if len(name.split()) < 2:
        await message.answer(t(message.from_user.id, "invalid_name"))
        return
    
    await state.update_data(full_name=name)
    await state.set_state(RegisterStates.phone)
    await message.answer(
        t(message.from_user.id, "register_phone"),
        reply_markup=cancel_keyboard(message.from_user.id)
    )

@driver_router.message(RegisterStates.phone)
async def register_phone(message: Message, state: FSMContext) -> None:
    if await is_cancel(message):
        await state.clear()
        await message.answer(t(message.from_user.id, "register_cancel"), reply_markup=welcome_keyboard(message.from_user.id))
        return
    
    phone = normalize_phone(message.text)
    if not is_valid_phone(phone):
        await message.answer(t(message.from_user.id, "invalid_phone"))
        return
    
    await state.update_data(phone=phone)
    await state.set_state(RegisterStates.card)
    await message.answer(
        t(message.from_user.id, "register_card"),
        reply_markup=cancel_keyboard(message.from_user.id)
    )

@driver_router.message(RegisterStates.card)
async def register_card(message: Message, state: FSMContext) -> None:
    if await is_cancel(message):
        await state.clear()
        await message.answer(t(message.from_user.id, "register_cancel"), reply_markup=welcome_keyboard(message.from_user.id))
        return
    
    card = normalize_card(message.text)
    if not is_valid_card(card):
        await message.answer(t(message.from_user.id, "invalid_card"))
        return
    
    await state.update_data(card_number=card)
    await state.set_state(RegisterStates.car_model)
    await message.answer(
        t(message.from_user.id, "register_car_model"),
        reply_markup=cancel_keyboard(message.from_user.id)
    )

@driver_router.message(RegisterStates.car_model)
async def register_car_model(message: Message, state: FSMContext) -> None:
    if await is_cancel(message):
        await state.clear()
        await message.answer(t(message.from_user.id, "register_cancel"), reply_markup=welcome_keyboard(message.from_user.id))
        return
    
    car_model = message.text.strip()
    if len(car_model) < 2:
        await message.answer(t(message.from_user.id, "invalid_car_model"))
        return
    
    await state.update_data(car_model=car_model)
    await state.set_state(RegisterStates.car_number)
    await message.answer(
        t(message.from_user.id, "register_car_number"),
        reply_markup=cancel_keyboard(message.from_user.id)
    )

@driver_router.message(RegisterStates.car_number)
async def register_car_number(message: Message, state: FSMContext) -> None:
    if await is_cancel(message):
        await state.clear()
        await message.answer(t(message.from_user.id, "register_cancel"), reply_markup=welcome_keyboard(message.from_user.id))
        return
    
    car_number = message.text.strip().upper()
    if len(car_number) < 5:
        await message.answer(t(message.from_user.id, "invalid_car_number"))
        return
    
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
        add_driver_log(message.from_user.id, "register", f"Registered with position {position}")
        
        await message.answer(
            t(message.from_user.id, "register_success").format(position=position),
            reply_markup=user_main_menu(message.from_user.id)
        )
        
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    f"✅ Yangi haydovchi ro'yxatdan o'tdi!\n\n"
                    f"👤 Ism: {data.get('full_name')}\n"
                    f"📱 Telefon: {data.get('phone')}\n"
                    f"🚗 Mashina: {data.get('car_model')} - {car_number}\n"
                    f"🆔 POSITION: {position}\n"
                    f"🆔 Telegram: @{message.from_user.username or 'No username'}"
                )
            except Exception:
                pass
    else:
        await message.answer(
            "❌ Ro'yxatdan o'tishda xatolik yuz berdi.\n"
            "Iltimos, qaytadan /start bosing.",
            reply_markup=welcome_keyboard(message.from_user.id)
        )

# ================== АСОСИЙ МЕНЮ ТУГМАЛАРИ (ЮҚОРИДАГИ БОТ ТУХТАБ ҚОЛАЁТГАН ЖОЙ) ==================
# Энди ҳар бир тугма ишлайдиган бўлди

@driver_router.message(F.text.in_([TEXTS['uz']['menu_balance'], TEXTS['ru']['menu_balance']]))
async def menu_balance(message: Message) -> None:
    user = get_user_by_telegram(message.from_user.id)
    if not user or user.get("is_registered", 0) == 0:
        await message.answer(t(message.from_user.id, "register_again"))
        return
    
    await message.answer(
        f"{t(message.from_user.id, 'balance_title')}\n\n"
        f"{t(message.from_user.id, 'balance_current')}: <b>{fmt_sum(user['balance'])}</b>\n"
        f"{t(message.from_user.id, 'balance_blocked')}: <b>{fmt_sum(user['blocked_balance'])}</b>\n"
        f"{t(message.from_user.id, 'balance_available')}: <b>{fmt_sum(safe_float(user['balance']))}</b>"
    )

@driver_router.message(F.text.in_([TEXTS['uz']['menu_today_orders'], TEXTS['ru']['menu_today_orders']]))
async def menu_today_orders(message: Message) -> None:
    orders = get_today_orders(message.from_user.id)
    stats = get_driver_stats(message.from_user.id)
    
    text = f"{t(message.from_user.id, 'orders_today_title')}\n\n"
    if not orders:
        text += t(message.from_user.id, 'orders_today_empty')
    else:
        text += f"{t(message.from_user.id, 'orders_total')}: {len(orders)} ta\n"
        text += f"{t(message.from_user.id, 'orders_earnings')}: <b>{fmt_sum(stats['today_sum'])}</b>\n\n"
        for order in orders:
            status_text = t(message.from_user.id, "status_completed") if order['status'] == 'completed' else t(message.from_user.id, "status_new")
            text += f"{t(message.from_user.id, 'order_id')}: <code>#{order['id']}</code> | {fmt_sum(order['amount'])} | {status_text}\n"
    
    await message.answer(text)

@driver_router.message(F.text.in_([TEXTS['uz']['menu_withdraw'], TEXTS['ru']['menu_withdraw']]))
async def menu_withdraw(message: Message, state: FSMContext) -> None:
    user = get_user_by_telegram(message.from_user.id)
    if not user or user.get("is_registered", 0) == 0:
        await message.answer(t(message.from_user.id, "register_again"))
        return
    
    await state.set_state(WithdrawStates.amount)
    await message.answer(
        f"{t(message.from_user.id, 'withdraw_title')}\n\n"
        f"{t(message.from_user.id, 'withdraw_available')}: <b>{fmt_sum(user['balance'])}</b>\n"
        f"{t(message.from_user.id, 'withdraw_min')}: <b>{fmt_sum(MIN_WITHDRAWAL)}</b>\n"
        f"{t(message.from_user.id, 'withdraw_commission')}: <b>{COMMISSION_PERCENT}%</b>\n\n"
        f"{t(message.from_user.id, 'withdraw_amount_ask')}",
        reply_markup=cancel_keyboard(message.from_user.id)
    )

@driver_router.message(WithdrawStates.amount)
async def withdraw_amount(message: Message, state: FSMContext) -> None:
    if await is_cancel(message):
        await state.clear()
        await message.answer(t(message.from_user.id, "action_cancelled"), reply_markup=user_main_menu(message.from_user.id))
        return
    
    try:
        amount = float(message.text.replace(" ", "").replace("so'm", ""))
    except:
        await message.answer(t(message.from_user.id, "invalid_input"))
        return
    
    user = get_user_by_telegram(message.from_user.id)
    
    if amount < MIN_WITHDRAWAL:
        await message.answer(t(message.from_user.id, "withdraw_min_error").format(min=fmt_sum(MIN_WITHDRAWAL)))
        return
    
    if amount > safe_float(user['balance']):
        await message.answer(t(message.from_user.id, "withdraw_balance_error"))
        return
    
    await state.update_data(amount=amount)
    await state.set_state(WithdrawStates.payment_type)
    await message.answer(
        t(message.from_user.id, "withdraw_type_ask"),
        reply_markup=withdraw_type_keyboard(message.from_user.id)
    )

# Барча турдаги тугмаларни ушлайдиган ҳендлер (Карта, Нақд, БРБ)
@driver_router.message(F.text.in_([TEXTS['uz']['withdraw_type_card'], TEXTS['ru']['withdraw_type_card'],
                                   TEXTS['uz']['withdraw_type_cash'], TEXTS['ru']['withdraw_type_cash'],
                                   TEXTS['uz']['withdraw_type_brb'], TEXTS['ru']['withdraw_type_brb']]))
async def withdraw_type(message: Message, state: FSMContext) -> None:
    if await is_cancel(message):
        await state.clear()
        await message.answer(t(message.from_user.id, "action_cancelled"), reply_markup=user_main_menu(message.from_user.id))
        return
    
    payment_type = message.text.split()[-1]  # 'kartaga', 'naqd', 'brb'
    await state.update_data(payment_type=payment_type)
    
    if payment_type in ["Kartaga", "На карту", "kartaga"]:
        await state.set_state(WithdrawStates.card_number)
        await message.answer(t(message.from_user.id, "withdraw_card_ask"))
    else:
        await state.set_state(WithdrawStates.phone_number)
        await message.answer(t(message.from_user.id, "withdraw_phone_ask"))

@driver_router.message(WithdrawStates.card_number)
async def withdraw_card_number(message: Message, state: FSMContext) -> None:
    if await is_cancel(message):
        await state.clear()
        await message.answer(t(message.from_user.id, "action_cancelled"), reply_markup=user_main_menu(message.from_user.id))
        return
    
    card = normalize_card(message.text)
    if not is_valid_card(card):
        await message.answer(t(message.from_user.id, "invalid_card"))
        return
    
    await state.update_data(card_number=card)
    data = await state.get_data()
    
    withdrawal_id = create_withdrawal(
        message.from_user.id,
        data['amount'],
        data['payment_type'],
        card_number=card
    )
    
    await state.clear()
    
    if withdrawal_id:
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    f"💸 <b>Yangi pul yechish so'rovi!</b>\n\n"
                    f"🆔 ID: #{withdrawal_id}\n"
                    f"👤 Haydovchi: {message.from_user.full_name}\n"
                    f"💰 Summa: {fmt_sum(data['amount'])}\n"
                    f"💳 Kartaga: {card}\n"
                    f"Telegram ID: {message.from_user.id}"
                )
            except Exception:
                pass
        
        await message.answer(t(message.from_user.id, "withdraw_success"), reply_markup=user_main_menu(message.from_user.id))
    else:
        await message.answer(t(message.from_user.id, "withdraw_fail"), reply_markup=user_main_menu(message.from_user.id))

@driver_router.message(WithdrawStates.phone_number)
async def withdraw_phone_number(message: Message, state: FSMContext) -> None:
    if await is_cancel(message):
        await state.clear()
        await message.answer(t(message.from_user.id, "action_cancelled"), reply_markup=user_main_menu(message.from_user.id))
        return
    
    phone = normalize_phone(message.text)
    if not is_valid_phone(phone):
        await message.answer(t(message.from_user.id, "invalid_phone"))
        return
    
    await state.update_data(phone_number=phone)
    data = await state.get_data()
    
    withdrawal_id = create_withdrawal(
        message.from_user.id,
        data['amount'],
        data['payment_type'],
        phone_number=phone
    )
    
    await state.clear()
    
    if withdrawal_id:
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    f"💸 <b>Yangi pul yechish so'rovi!</b>\n\n"
                    f"🆔 ID: #{withdrawal_id}\n"
                    f"👤 Haydovchi: {message.from_user.full_name}\n"
                    f"💰 Summa: {fmt_sum(data['amount'])}\n"
                    f"📱 Telefon: {phone}\n"
                    f"Telegram ID: {message.from_user.id}"
                )
            except Exception:
                pass
        
        await message.answer(t(message.from_user.id, "withdraw_success"), reply_markup=user_main_menu(message.from_user.id))
    else:
        await message.answer(t(message.from_user.id, "withdraw_fail"), reply_markup=user_main_menu(message.from_user.id))

@driver_router.message(F.text.in_([TEXTS['uz']['menu_history'], TEXTS['ru']['menu_history']]))
async def menu_history(message: Message) -> None:
    withdrawals = get_withdrawals(message.from_user.id)
    text = f"📜 <b>To'lovlar tarixi</b>\n\n"
    if not withdrawals:
        text += "Hozircha to'lovlar yo'q 📭"
    else:
        for w in withdrawals:
            status_text = t(message.from_user.id, "withdraw_pending") if w['status'] == 'pending' else t(message.from_user.id, "withdraw_completed") if w['status'] == 'completed' else t(message.from_user.id, "withdraw_cancelled")
            text += f"#{w['id']} | {fmt_sum(w['net_amount'])} | {status_text}\n"
    await message.answer(text)

@driver_router.message(F.text.in_([TEXTS['uz']['menu_profile'], TEXTS['ru']['menu_profile']]))
async def menu_profile(message: Message) -> None:
    user = get_user_by_telegram(message.from_user.id)
    if not user:
        await message.answer(t(message.from_user.id, "register_again"))
        return
    
    status_text = t(message.from_user.id, "profile_status_active") if user['is_blocked'] == 0 else t(message.from_user.id, "profile_status_blocked")
    
    await message.answer(
        f"{t(message.from_user.id, 'profile_title')}\n\n"
        f"{t(message.from_user.id, 'profile_id')}: <code>{user['id']}</code>\n"
        f"{t(message.from_user.id, 'profile_position')}: <code>{user['position']}</code>\n"
        f"{t(message.from_user.id, 'profile_name')}: {user['full_name']}\n"
        f"{t(message.from_user.id, 'profile_phone')}: {user['phone']}\n"
        f"{t(message.from_user.id, 'profile_card')}: {user['card_number']}\n"
        f"{t(message.from_user.id, 'profile_car')}: {user['car_model']} | {user['car_number']}\n"
        f"{t(message.from_user.id, 'profile_orders')}: {user['total_orders']}\n"
        f"{t(message.from_user.id, 'profile_earnings')}: {fmt_sum(user['total_earnings'])}\n"
        f"{t(message.from_user.id, 'profile_status')}: {status_text}"
    )

@driver_router.message(F.text.in_([TEXTS['uz']['menu_news'], TEXTS['ru']['menu_news']]))
async def menu_news(message: Message) -> None:
    await message.answer(t(message.from_user.id, "news_empty"))

@driver_router.message(F.text.in_([TEXTS['uz']['menu_group'], TEXTS['ru']['menu_group']]))
async def menu_group(message: Message) -> None:
    if DRIVER_GROUP_LINK:
        await message.answer(f"{t(message.from_user.id, 'support_group')}: <a href='{DRIVER_GROUP_LINK}'>Havola</a>")
    else:
        await message.answer("Guruh havolasi sozlanmagan.")

@driver_router.message(F.text.in_([TEXTS['uz']['menu_support'], TEXTS['ru']['menu_support']]))
async def menu_support(message: Message) -> None:
    await message.answer(
        f"{t(message.from_user.id, 'support_title')}\n\n"
        f"{t(message.from_user.id, 'support_phone')}: {SUPPORT_PHONE}\n"
        f"{t(message.from_user.id, 'support_tg')}: {SUPPORT_TG}\n"
        f"{t(message.from_user.id, 'support_text')}"
    )

@driver_router.message(F.text.in_([TEXTS['uz']['menu_settings'], TEXTS['ru']['menu_settings']]))
async def menu_settings(message: Message) -> None:
    await message.answer(t(message.from_user.id, "settings_title"), reply_markup=language_keyboard())

@driver_router.callback_query(F.data.startswith("lang:set:"))
async def settings_lang_callback(callback: CallbackQuery) -> None:
    lang = callback.data.split(":")[-1]
    if lang in SUPPORTED_LANGS:
        conn = get_db()
        conn.execute("UPDATE users SET language = ?, updated_at = ? WHERE telegram_id = ?", (lang, utc_now_iso(), callback.from_user.id))
        conn.commit()
        conn.close()
        
        await callback.message.delete()
        await callback.message.answer(t(lang, "lang_updated"), reply_markup=user_main_menu(callback.from_user.id))
    await callback.answer()

@driver_router.message(F.text.in_([TEXTS['uz']['menu_admin'], TEXTS['ru']['menu_admin']]))
async def menu_admin(message: Message) -> None:
    if is_admin(message.from_user.id):
        await message.answer(t(message.from_user.id, "admin_title"), reply_markup=admin_main_menu(message.from_user.id))
    else:
        await message.answer(t(message.from_user.id, "not_admin"))

# Админ бўлими тугмалари
@driver_router.message(F.text.in_([TEXTS['uz']['admin_drivers'], TEXTS['ru']['admin_drivers']]))
async def admin_drivers(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer(t(message.from_user.id, "not_admin"))
        return
    
    drivers = get_all_drivers()
    text = f"👥 <b>{t(message.from_user.id, 'admin_drivers')}</b> ({len(drivers)})\n\n"
    for d in drivers:
        text += f"🆔 {d['position']} | {d['full_name']} | {d['car_number']}\n"
    
    await message.answer(text)

@driver_router.message(F.text.in_([TEXTS['uz']['admin_balances'], TEXTS['ru']['admin_balances']]))
async def admin_balances(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer(t(message.from_user.id, "not_admin"))
        return
    
    drivers = get_all_drivers()
    text = f"💰 <b>{t(message.from_user.id, 'admin_balances')}</b>\n\n"
    for d in drivers:
        text += f"{d['full_name']} ({d['position']}): {fmt_sum(d['balance'])}\n"
    
    await message.answer(text)


# ============================================================
# WEB ROUTES
# ============================================================

@web_router.get("/")
async def index_page(request: web.Request) -> web.Response:
    return web.Response(
        text=f"""
        <html>
        <head><title>{BOT_NAME}</title></head>
        <body style="font-family:Arial;text-align:center;padding:50px;background:linear-gradient(135deg,#1a1a2e,#16213e);color:white;">
            <h1>🚕 {BOT_NAME}</h1>
            <p>Haydovchilar uchun aqlli tizim</p>
            <p style="margin-top:30px;color:#888;">v2.0.0 | 24/7</p>
        </body>
        </html>
        """,
        content_type="text/html"
    )

@web_router.get("/health")
async def health_route(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": BOT_NAME})

@web_router.post("/api/webhook/order")
async def order_webhook(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        logger.info(f"Order webhook received: {data}")
        return web.json_response({"status": "ok"})
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return web.json_response({"status": "error"}, status=500)

@web_router.post("/api/webhook/payment")
async def payment_webhook(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        logger.info(f"Payment webhook received: {data}")
        return web.json_response({"status": "ok"})
    except Exception as e:
        logger.error(f"Payment webhook error: {e}")
        return web.json_response({"status": "error"}, status=500)


# ============================================================
# MAIN
# ============================================================

def register_routers() -> None:
    dp.include_router(driver_router)
    dp.include_router(admin_router)

async def main() -> None:
    init_db()
    register_routers()
    
    # Web server
    app = web.Application()
    app.add_routes(web_router)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    
    logger.info(f"🚕 {BOT_NAME} started!")
    logger.info(f"✅ Bot running on port {PORT}")
    logger.info(f"✅ Web server started")
    
    # Муҳим: Webhook'ни ўчириб, Polling'ни ишга туширамиз (409 хатоликни олдини олади)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
