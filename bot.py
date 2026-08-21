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

# Admin IDs
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

# Yandex Fleet
YANDEX_API_KEY = os.getenv("YANDEX_API_KEY", "").strip()
YANDEX_FLEET_URL = os.getenv("YANDEX_FLEET_URL", "https://fleet-api.yandex.ru/v1")

# BRB 24/7 API
BRB_API_URL = os.getenv("BRB_API_URL", "https://api.brb.uz/v1").strip()
BRB_API_KEY = os.getenv("BRB_API_KEY", "").strip()
BRB_MERCHANT_ID = os.getenv("BRB_MERCHANT_ID", "").strip()

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

# Routers
driver_router = Router()
admin_router = Router()
web_router = web.RouteTableDef()

# ============================================================
# TEXTS
# ============================================================

TEXTS: dict[str, dict[str, str]] = {
    "uz": {
        # Welcome & Registration
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
                              "🆔 POSITION: <code>{position}</code>",
        
        "invalid_name": "❌ Ism familiya noto'g'ri. Iltimos, qaytadan kiriting.",
        "invalid_phone": "❌ Telefon raqam noto'g'ri. Format: +998901234567",
        "invalid_card": "❌ Karta raqam noto'g'ri. 16 ta raqam bo'lishi kerak.",
        "invalid_car_model": "❌ Avtomobil markasi noto'g'ri. Qaytadan kiriting.",
        "invalid_car_number": "❌ Davlat raqam noto'g'ri. Qaytadan kiriting.",
        
        # Driver menu
        "main_menu": "🚕 <b>Asosiy menyu</b>\n\n"
                     "Xush kelibsiz, {name}!\n"
                     "🆔 POSITION: <code>{position}</code>",
        "menu_balance": "💰 Balans",
        "menu_today_orders": "📊 Bugungi buyurtmalar",
        "menu_withdraw": "💸 Pul yechish",
        "menu_history": "📜 To'lovlar tarixi",
        "menu_profile": "👤 Profil",
        "menu_news": "📢 Yangiliklar",
        "menu_group": "💬 Haydovchilar guruhi",
        "menu_support": "🆘 Yordam",
        "menu_settings": "⚙ Sozlamalar",
        "menu_admin": "🛠 Admin",
        "menu_back": "⬅ Orqaga",
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
        "admin_managers": "👨💼 Menejerlar",
        "admin_group": "💬 Guruh boshqaruvi",
        "admin_logs": "📝 Loglar",
        "admin_back": "⬅ Menyuga",
        "not_admin": "Bu bo'lim faqat adminlar uchun 🚫",
        
        # Balance
        "balance_title": f"💰 <b>{BOT_NAME} da balans</b>",
        "balance_current": "Joriy balans",
        "balance_blocked": "Bloklangan balans",
        "balance_available": "Mavjud balans",
        "balance_withdraw": "Pul yechish",
        "balance_add": "Balansni to'ldirish",
        "balance_history": "To'lovlar tarixi",
        
        # Orders
        "orders_today_title": "📊 <b>Bugungi buyurtmalar</b>",
        "orders_today_empty": "Bugun hech qanday buyurtma yo'q 📭",
        "orders_total": "Jami buyurtmalar",
        "orders_earnings": "Daromad",
        "orders_completed": "Bajarilgan",
        "orders_cancelled": "Bekor qilingan",
        "order_id": "Buyurtma raqami",
        "order_time": "Vaqt",
        "order_amount": "Summa",
        "order_distance": "Masofa",
        "order_from": "Qayerdan",
        "order_to": "Qayerga",
        "order_status": "Holati",
        
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
        "settings_title": "⚙ <b>Sozlamalar</b>",
        "settings_lang": "🌐 Til",
        "settings_notifications": "🔔 Bildirishnomalar",
        "settings_auto_withdraw": "💰 Avtomatik pul yechish",
        "settings_auto_withdraw_min": "Avtomatik yechish chegarasi",
        "settings_saved": "✅ Sozlamalar saqlandi!",
        "lang_updated": "✅ Til o'zgartirildi!",
        
        # Common
        "yes": "✅ Ha",
        "no": "❌ Yo'q",
        "skip": "⏭ O'tkazib yuborish",
        "back": "⬅ Orqaga",
        "cancel": "❌ Bekor qilish",
        "confirm": "✅ Tasdiqlash",
        "action_cancelled": "Amal bekor qilindi ❌",
        "invalid_input": "❌ Noto'g'ri ma'lumot, qaytadan kiriting",
        "not_found": "❌ Ma'lumot topilmadi",
        
        # Statuses
        "status_new": "🆕 Yangi",
        "status_accepted": "✅ Qabul qilingan",
        "status_in_progress": "🚕 Yo'lda",
        "status_completed": "✅ Bajarilgan",
        "status_cancelled": "❌ Bekor qilingan",
        "status_failed": "❌ Muvaffaqiyatsiz",
        
        "payment_pending": "⏳ Kutilmoqda",
        "payment_completed": "✅ To'langan",
        "payment_failed": "❌ Xato",
        "payment_cancelled": "❌ Bekor",
    },
    "ru": {
        # Welcome & Registration
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
                              "🆔 POSITION: <code>{position}</code>",
        
        "invalid_name": "❌ Неверное имя. Пожалуйста, введите заново.",
        "invalid_phone": "❌ Неверный номер телефона. Формат: +998901234567",
        "invalid_card": "❌ Неверный номер карты. Должно быть 16 цифр.",
        "invalid_car_model": "❌ Неверная марка автомобиля. Введите заново.",
        "invalid_car_number": "❌ Неверный госномер. Введите заново.",
        
        # Driver menu
        "main_menu": "🚕 <b>Главное меню</b>\n\n"
                     "Добро пожаловать, {name}!\n"
                     "🆔 POSITION: <code>{position}</code>",
        "menu_balance": "💰 Баланс",
        "menu_today_orders": "📊 Сегодняшние заказы",
        "menu_withdraw": "💸 Вывод средств",
        "menu_history": "📜 История платежей",
        "menu_profile": "👤 Профиль",
        "menu_news": "📢 Новости",
        "menu_group": "💬 Группа водителей",
        "menu_support": "🆘 Помощь",
        "menu_settings": "⚙ Настройки",
        "menu_admin": "🛠 Админ",
        "menu_back": "⬅ Назад",
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
        "admin_managers": "👨💼 Менеджеры",
        "admin_group": "💬 Управление группой",
        "admin_logs": "📝 Логи",
        "admin_back": "⬅ В меню",
        "not_admin": "Этот раздел только для администратора 🚫",
        
        # Balance
        "balance_title": f"💰 <b>Баланс в {BOT_NAME}</b>",
        "balance_current": "Текущий баланс",
        "balance_blocked": "Заблокированный баланс",
        "balance_available": "Доступный баланс",
        "balance_withdraw": "Вывести средства",
        "balance_add": "Пополнить баланс",
        "balance_history": "История платежей",
        
        # Orders
        "orders_today_title": "📊 <b>Сегодняшние заказы</b>",
        "orders_today_empty": "Сегодня нет заказов 📭",
        "orders_total": "Всего заказов",
        "orders_earnings": "Заработок",
        "orders_completed": "Выполнено",
        "orders_cancelled": "Отменено",
        "order_id": "Номер заказа",
        "order_time": "Время",
        "order_amount": "Сумма",
        "order_distance": "Расстояние",
        "order_from": "Откуда",
        "order_to": "Куда",
        "order_status": "Статус",
        
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
        "settings_title": "⚙ <b>Настройки</b>",
        "settings_lang": "🌐 Язык",
        "settings_notifications": "🔔 Уведомления",
        "settings_auto_withdraw": "💰 Автоматический вывод",
        "settings_auto_withdraw_min": "Порог автоматического вывода",
        "settings_saved": "✅ Настройки сохранены!",
        "lang_updated": "✅ Язык изменен!",
        
        # Common
        "yes": "✅ Да",
        "no": "❌ Нет",
        "skip": "⏭ Пропустить",
        "back": "⬅ Назад",
        "cancel": "❌ Отмена",
        "confirm": "✅ Подтвердить",
        "action_cancelled": "Действие отменено ❌",
        "invalid_input": "❌ Неверные данные, попробуйте снова",
        "not_found": "❌ Данные не найдены",
        
        # Statuses
        "status_new": "🆕 Новый",
        "status_accepted": "✅ Принят",
        "status_in_progress": "🚕 В пути",
        "status_completed": "✅ Выполнен",
        "status_cancelled": "❌ Отменён",
        "status_failed": "❌ Неудачный",
        
        "payment_pending": "⏳ Ожидает",
        "payment_completed": "✅ Оплачен",
        "payment_failed": "❌ Ошибка",
        "payment_cancelled": "❌ Отменён",
    }
}

SUPPORTED_LANGS = ("uz", "ru")
ORDER_STATUSES = ("new", "accepted", "in_progress", "completed", "cancelled", "failed")
PAYMENT_STATUSES = ("pending", "completed", "failed", "cancelled")
WITHDRAWAL_STATUSES = ("pending", "processing", "completed", "failed", "cancelled")

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
    
    # Users table with all registration fields
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
            yandex_driver_id TEXT,
            yandex_park_id TEXT,
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
    
    # Transactions table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            payment_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            description TEXT,
            external_id TEXT,
            external_data TEXT,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    
    # Withdrawals table
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
            external_id TEXT,
            external_data TEXT,
            admin_approved_by INTEGER,
            admin_approved_at TEXT,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            cancelled_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    
    # Orders table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            driver_id INTEGER NOT NULL,
            yandex_order_id TEXT UNIQUE,
            yandex_data TEXT,
            amount REAL NOT NULL,
            commission REAL DEFAULT 0,
            net_amount REAL NOT NULL,
            distance REAL,
            duration INTEGER,
            wait_time INTEGER,
            start_address TEXT,
            end_address TEXT,
            start_lat REAL,
            start_lng REAL,
            end_lat REAL,
            end_lng REAL,
            status TEXT NOT NULL DEFAULT 'new',
            payment_status TEXT NOT NULL DEFAULT 'pending',
            customer_name TEXT,
            customer_phone TEXT,
            customer_rating REAL,
            started_at TEXT,
            completed_at TEXT,
            cancelled_at TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (driver_id) REFERENCES users(id)
        )
    """)
    
    # News table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title_uz TEXT,
            title_ru TEXT,
            content_uz TEXT,
            content_ru TEXT,
            image_url TEXT,
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
    
    # Driver logs
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
    
    # Indexes
    cur.execute("CREATE INDEX IF NOT EXISTS idx_users_telegram ON users(telegram_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_users_phone ON users(phone)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_users_position ON users(position)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_orders_driver ON orders(driver_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_orders_yandex ON orders(yandex_order_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_transactions_user ON transactions(user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_withdrawals_user ON withdrawals(user_id)")
    
    conn.commit()
    conn.close()
    logger.info("Database initialized")
