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
                              f"🆔 POSITION: <code>{position}</code>",
        
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
        "settings_title": "⚙️ <b>Sozlamalar</b>",
        "settings_lang": "🌐 Til",
        "settings_notifications": "🔔 Bildirishnomalar",
        "settings_auto_withdraw": "💰 Avtomatik pul yechish",
        "settings_auto_withdraw_min": "Avtomatik yechish chegarasi",
        "settings_saved": "✅ Sozlamalar saqlandi!",
        
        # Common
        "yes": "✅ Ha",
        "no": "❌ Yo'q",
        "skip": "⏭ O'tkazib yuborish",
        "back": "⬅️ Orqaga",
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
                              f"🆔 POSITION: <code>{position}</code>",
        
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
        "settings_title": "⚙️ <b>Настройки</b>",
        "settings_lang": "🌐 Язык",
        "settings_notifications": "🔔 Уведомления",
        "settings_auto_withdraw": "💰 Автоматический вывод",
        "settings_auto_withdraw_min": "Порог автоматического вывода",
        "settings_saved": "✅ Настройки сохранены!",
        
        # Common
        "yes": "✅ Да",
        "no": "❌ Нет",
        "skip": "⏭ Пропустить",
        "back": "⬅️ Назад",
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
    """Generate unique POSITION code"""
    import random
    import string
    # Format: LCH-XXXXX (e.g., LCH-A1B2C)
    chars = string.ascii_uppercase + string.digits
    code = ''.join(random.choices(chars, k=5))
    position = f"LCH-{code}"
    
    # Check if unique
    conn = get_db()
    exists = conn.execute("SELECT id FROM users WHERE position = ?", (position,)).fetchone()
    conn.close()
    
    if exists:
        return generate_position()  # Recursive until unique
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
    """Complete driver registration and return position"""
    conn = get_db()
    
    # Check if already registered
    existing = conn.execute(
        "SELECT is_registered FROM users WHERE telegram_id = ?", (telegram_id,)
    ).fetchone()
    
    if existing and existing["is_registered"] == 1:
        conn.close()
        return None
    
    # Generate unique position
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

def get_user_by_id(user_id: int) -> Optional[dict]:
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
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

def get_pending_withdrawals(limit: int = 50) -> List[dict]:
    conn = get_db()
    rows = conn.execute(
        """
        SELECT w.*, u.telegram_id, u.full_name, u.username, u.phone
        FROM withdrawals w
        JOIN users u ON w.user_id = u.id
        WHERE w.status = 'pending'
        ORDER BY w.created_at ASC LIMIT ?
        """,
        (limit,)
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]

def approve_withdrawal(withdrawal_id: int, admin_telegram_id: int) -> bool:
    conn = get_db()
    cur = conn.cursor()
    now = utc_now_iso()
    
    withdrawal = cur.execute(
        "SELECT * FROM withdrawals WHERE id = ?", (withdrawal_id,)
    ).fetchone()
    
    if not withdrawal or withdrawal["status"] != "pending":
        conn.close()
        return False
    
    admin = cur.execute(
        "SELECT id FROM users WHERE telegram_id = ?", (admin_telegram_id,)
    ).fetchone()
    
    if not admin:
        conn.close()
        return False
    
    cur.execute(
        """
        UPDATE withdrawals SET 
            status = 'completed', 
            admin_approved_by = ?, 
            admin_approved_at = ?,
            completed_at = ?
        WHERE id = ?
        """,
        (admin["id"], now, now, withdrawal_id)
    )
    
    cur.execute(
        "UPDATE users SET blocked_balance = blocked_balance - ? WHERE id = ?",
        (withdrawal["amount"], withdrawal["user_id"])
    )
    
    conn.commit()
    conn.close()
    return True

def reject_withdrawal(withdrawal_id: int, admin_telegram_id: int) -> bool:
    conn = get_db()
    cur = conn.cursor()
    now = utc_now_iso()
    
    withdrawal = cur.execute(
        "SELECT * FROM withdrawals WHERE id = ?", (withdrawal_id,)
    ).fetchone()
    
    if not withdrawal or withdrawal["status"] != "pending":
        conn.close()
        return False
    
    cur.execute(
        """
        UPDATE withdrawals SET 
            status = 'cancelled',
            cancelled_at = ?
        WHERE id = ?
        """,
        (now, withdrawal_id)
    )
    
    cur.execute(
        "UPDATE users SET balance = balance + ?, blocked_balance = blocked_balance - ? WHERE id = ?",
        (withdrawal["amount"], withdrawal["amount"], withdrawal["user_id"])
    )
    
    conn.commit()
    conn.close()
    return True

def create_news(
    title_uz: str,
    title_ru: str,
    content_uz: str,
    content_ru: str,
    image_file_id: str = "",
    created_by_telegram: int = 0
) -> Optional[int]:
    conn = get_db()
    cur = conn.cursor()
    now = utc_now_iso()
    
    cur.execute(
        """
        INSERT INTO news (
            title_uz, title_ru, content_uz, content_ru,
            image_file_id, created_by, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (title_uz, title_ru, content_uz, content_ru, image_file_id, created_by_telegram, now, now)
    )
    
    news_id = cur.lastrowid
    conn.commit()
    conn.close()
    return news_id

def get_active_news(limit: int = 20) -> List[dict]:
    conn = get_db()
    rows = conn.execute(
        """
        SELECT * FROM news 
        WHERE is_active = 1 
        ORDER BY is_pinned DESC, created_at DESC LIMIT ?
        """,
        (limit,)
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_all_news(limit: int = 50) -> List[dict]:
    conn = get_db()
    rows = conn.execute(
        """
        SELECT * FROM news 
        ORDER BY created_at DESC LIMIT ?
        """,
        (limit,)
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_statistics() -> dict:
    conn = get_db()
    cur = conn.cursor()
    
    stats = {
        "total_drivers": 0,
        "active_drivers": 0,
        "blocked_drivers": 0,
        "registered_drivers": 0,
        "total_orders": 0,
        "today_orders": 0,
        "pending_orders": 0,
        "completed_orders": 0,
        "total_earnings": 0,
        "today_earnings": 0,
        "pending_withdrawals": 0,
        "total_withdrawals": 0,
        "total_balance": 0,
    }
    
    today = datetime.now().date().isoformat()
    
    # Drivers
    row = cur.execute("SELECT COUNT(*) FROM users WHERE role = 'driver'").fetchone()
    stats["total_drivers"] = safe_int(row[0]) if row else 0
    
    row = cur.execute("SELECT COUNT(*) FROM users WHERE role = 'driver' AND is_registered = 1").fetchone()
    stats["registered_drivers"] = safe_int(row[0]) if row else 0
    
    row = cur.execute("SELECT COUNT(*) FROM users WHERE role = 'driver' AND is_active = 1 AND is_blocked = 0").fetchone()
    stats["active_drivers"] = safe_int(row[0]) if row else 0
    
    row = cur.execute("SELECT COUNT(*) FROM users WHERE role = 'driver' AND is_blocked = 1").fetchone()
    stats["blocked_drivers"] = safe_int(row[0]) if row else 0
    
    # Orders
    row = cur.execute("SELECT COUNT(*) FROM orders").fetchone()
    stats["total_orders"] = safe_int(row[0]) if row else 0
    
    row = cur.execute("SELECT COUNT(*) FROM orders WHERE date(created_at) = ?", (today,)).fetchone()
    stats["today_orders"] = safe_int(row[0]) if row else 0
    
    row = cur.execute("SELECT COUNT(*) FROM orders WHERE status = 'new'").fetchone()
    stats["pending_orders"] = safe_int(row[0]) if row else 0
    
    row = cur.execute("SELECT COUNT(*) FROM orders WHERE status = 'completed'").fetchone()
    stats["completed_orders"] = safe_int(row[0]) if row else 0
    
    row = cur.execute("SELECT SUM(net_amount) FROM orders WHERE status = 'completed'").fetchone()
    stats["total_earnings"] = safe_float(row[0]) if row else 0
    
    row = cur.execute("SELECT SUM(net_amount) FROM orders WHERE status = 'completed' AND date(created_at) = ?", (today,)).fetchone()
    stats["today_earnings"] = safe_float(row[0]) if row else 0
    
    # Withdrawals
    row = cur.execute("SELECT COUNT(*) FROM withdrawals WHERE status = 'pending'").fetchone()
    stats["pending_withdrawals"] = safe_int(row[0]) if row else 0
    
    row = cur.execute("SELECT COUNT(*) FROM withdrawals WHERE status = 'completed'").fetchone()
    stats["total_withdrawals"] = safe_int(row[0]) if row else 0
    
    row = cur.execute("SELECT SUM(balance) FROM users WHERE role = 'driver'").fetchone()
    stats["total_balance"] = safe_float(row[0]) if row else 0
    
    conn.close()
    return stats

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
    block_driver = State()
    unblock_driver = State()
    send_news_title = State()
    send_news_content = State()
    send_news_image = State()
    send_news_confirm = State()
    add_manager = State()
    remove_manager = State()
    view_logs = State()

class SettingsStates(StatesGroup):
    auto_withdraw_min = State()


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

def back_keyboard(telegram_id: int) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t(telegram_id, "back"))]],
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

def settings_keyboard(telegram_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t(telegram_id, "settings_lang"), callback_data="settings:lang")],
            [InlineKeyboardButton(text=t(telegram_id, "settings_notifications"), callback_data="settings:notifications")],
            [InlineKeyboardButton(text=t(telegram_id, "settings_auto_withdraw"), callback_data="settings:auto_withdraw")],
        ]
    )

def language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🇺🇿 O'zbekcha", callback_data="lang:set:uz")],
            [InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang:set:ru")],
        ]
    )

def social_links_keyboard() -> Optional[InlineKeyboardMarkup]:
    buttons = []
    if CHANNEL_LINK:
        buttons.append(InlineKeyboardButton(text="📢 Kanal", url=CHANNEL_LINK))
    if DRIVER_GROUP_LINK:
        buttons.append(InlineKeyboardButton(text="💬 Guruh", url=DRIVER_GROUP_LINK))
    return InlineKeyboardMarkup(inline_keyboard=[buttons]) if buttons else None


# ============================================================
# REGISTRATION HANDLERS
# ============================================================

@driver_router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    user = upsert_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    
    # Check if already registered
    if user.get("is_registered", 0) == 1:
        await message.answer(
            t(message.from_user.id, "already_registered").format(position=user.get("position", "N/A")),
            reply_markup=user_main_menu(message.from_user.id)
        )
        return
    
    # Show welcome with register button
    await message.answer(
        t(message.from_user.id, "welcome"),
        reply_markup=welcome_keyboard(message.from_user.id)
    )

@driver_router.message(F.text.in_([TEXTS['uz']['register_btn'], TEXTS['ru']['register_btn']]))
async def start_registration(message: Message, state: FSMContext) -> None:
    # Check if already registered
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
    if message.text == t(message.from_user.id, "cancel"):
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
    if message.text == t(message.from_user.id, "cancel"):
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
    if message.text == t(message.from_user.id, "cancel"):
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
    if message.text == t(message.from_user.id, "cancel"):
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
    if message.text == t(message.from_user.id, "cancel"):
        await state.clear()
        await message.answer(t(message.from_user.id, "register_cancel"), reply_markup=welcome_keyboard(message.from_user.id))
        return
    
    car_number = message.text.strip().upper()
    if len(car_number) < 5:
        await message.answer(t(message.from_user.id, "invalid_car_number"))
        return
    
    await state.update_data(car_number=car_number)
    data = await state.get_data()
    
    # Complete registration
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
        # Log registration
        add_driver_log(message.from_user.id, "register", f"Registered with position {position}")
        
        await message.answer(
            t(message.from_user.id, "register_success").format(position=position),
            reply_markup=user_main_menu(message.from_user.id)
        )
        
        # Notify admins
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


# ============================================================
# DRIVER HANDLERS (Rest of the handlers)
# ============================================================

@driver_router.message(Command("menu"))
async def cmd_menu(message: Message) -> None:
    user = get_user_by_telegram(message.from_user.id)
    if not user or user.get("is_registered", 0) != 1:
        await message.answer(
            "❗ Iltimos, avval ro'yxatdan o'ting: /start",
            reply_markup=welcome_keyboard(message.from_user.id)
        )
        return
    
    await message.answer(
        t(message.from_user.id, "main_menu").format(
            name=user.get("full_name", "Haydovchi"),
            position=user.get("position", "N/A")
        ),
        reply_markup=user_main_menu(message.from_user.id)
    )

# Balance
@driver_router.message(F.text.in_([TEXTS['uz']['menu_balance'], TEXTS['ru']['menu_balance']]))
async def show_balance(message: Message) -> None:
    user = get_user_by_telegram(message.from_user.id)
    if not user or user.get("is_registered", 0) != 1:
        await message.answer("❗ Iltimos, avval ro'yxatdan o'ting: /start", reply_markup=welcome_keyboard(message.from_user.id))
        return
    
    if user.get("is_blocked", 0) == 1:
        await message.answer("🚫 Siz bloklangansiz! Admin bilan bog'laning.")
        return
    
    text = (
        f"{t(message.from_user.id, 'balance_title')}\n\n"
        f"{t(message.from_user.id, 'balance_current')}: <b>{fmt_sum(user['balance'])}</b>\n"
        f"{t(message.from_user.id, 'balance_blocked')}: <b>{fmt_sum(user['blocked_balance'])}</b>\n"
        f"{t(message.from_user.id, 'balance_available')}: <b>{fmt_sum(user['balance'] - user['blocked_balance'])}</b>"
    )
    
    await message.answer(text, reply_markup=user_main_menu(message.from_user.id))

# Today Orders
@driver_router.message(F.text.in_([TEXTS['uz']['menu_today_orders'], TEXTS['ru']['menu_today_orders']]))
async def show_today_orders(message: Message) -> None:
    user = get_user_by_telegram(message.from_user.id)
    if not user or user.get("is_registered", 0) != 1:
        await message.answer("❗ Iltimos, avval ro'yxatdan o'ting: /start", reply_markup=welcome_keyboard(message.from_user.id))
        return
    
    if user.get("is_blocked", 0) == 1:
        await message.answer("🚫 Siz bloklangansiz! Admin bilan bog'laning.")
        return
    
    orders = get_today_orders(message.from_user.id)
    stats = get_driver_stats(message.from_user.id)
    
    if not orders:
        await message.answer(
            f"{t(message.from_user.id, 'orders_today_title')}\n\n"
            f"{t(message.from_user.id, 'orders_today_empty')}",
            reply_markup=user_main_menu(message.from_user.id)
        )
        return
    
    text = f"{t(message.from_user.id, 'orders_today_title')}\n\n"
    text += f"{t(message.from_user.id, 'orders_total')}: <b>{len(orders)}</b>\n"
    text += f"{t(message.from_user.id, 'orders_earnings')}: <b>{fmt_sum(stats['today_sum'])}</b>\n\n"
    
    for order in orders[:10]:
        status = t(message.from_user.id, f"status_{order['status']}")
        text += (
            f"🔹 {t(message.from_user.id, 'order_id')}: #{order['id']}\n"
            f"   {t(message.from_user.id, 'order_amount')}: {fmt_sum(order['net_amount'])}\n"
            f"   {t(message.from_user.id, 'order_status')}: {status}\n"
            f"   {t(message.from_user.id, 'order_time')}: {order['created_at'][:16]}\n\n"
        )
    
    await message.answer(text, reply_markup=user_main_menu(message.from_user.id))

# Withdraw (same as before but with registration check)
@driver_router.message(F.text.in_([TEXTS['uz']['menu_withdraw'], TEXTS['ru']['menu_withdraw']]))
async def start_withdraw(message: Message, state: FSMContext) -> None:
    user = get_user_by_telegram(message.from_user.id)
    if not user or user.get("is_registered", 0) != 1:
        await message.answer("❗ Iltimos, avval ro'yxatdan o'ting: /start", reply_markup=welcome_keyboard(message.from_user.id))
        return
    
    if user.get("is_blocked", 0) == 1:
        await message.answer("🚫 Siz bloklangansiz! Admin bilan bog'laning.")
        return
    
    available = user['balance'] - user['blocked_balance']
    if available < MIN_WITHDRAWAL:
        await message.answer(
            f"{t(message.from_user.id, 'withdraw_title')}\n\n"
            f"{t(message.from_user.id, 'withdraw_available')}: <b>{fmt_sum(available)}</b>\n"
            f"{t(message.from_user.id, 'withdraw_min')}: <b>{fmt_sum(MIN_WITHDRAWAL)}</b>\n"
            f"{t(message.from_user.id, 'withdraw_commission')}: <b>{COMMISSION_PERCENT}%</b>\n\n"
            f"{t(message.from_user.id, 'withdraw_balance_error')}",
            reply_markup=user_main_menu(message.from_user.id)
        )
        return
    
    await state.set_state(WithdrawStates.amount)
    await message.answer(
        f"{t(message.from_user.id, 'withdraw_title')}\n\n"
        f"{t(message.from_user.id, 'withdraw_available')}: <b>{fmt_sum(available)}</b>\n"
        f"{t(message.from_user.id, 'withdraw_min')}: <b>{fmt_sum(MIN_WITHDRAWAL)}</b>\n"
        f"{t(message.from_user.id, 'withdraw_commission')}: <b>{COMMISSION_PERCENT}%</b>\n\n"
        f"{t(message.from_user.id, 'withdraw_amount_ask')}",
        reply_markup=cancel_keyboard(message.from_user.id)
    )

# ... (rest of withdraw handlers same as before)
# [WithdrawStates handlers - keep from previous code]

# History
@driver_router.message(F.text.in_([TEXTS['uz']['menu_history'], TEXTS['ru']['menu_history']]))
async def show_history(message: Message) -> None:
    user = get_user_by_telegram(message.from_user.id)
    if not user or user.get("is_registered", 0) != 1:
        await message.answer("❗ Iltimos, avval ro'yxatdan o'ting: /start", reply_markup=welcome_keyboard(message.from_user.id))
        return
    
    transactions = get_transactions(message.from_user.id, 20)
    withdrawals = get_withdrawals(message.from_user.id, 20)
    
    if not transactions and not withdrawals:
        await message.answer(
            "📜 <b>To'lovlar tarixi</b>\n\n"
            "Hozircha hech qanday to'lov yo'q",
            reply_markup=user_main_menu(message.from_user.id)
        )
        return
    
    text = "📜 <b>To'lovlar tarixi</b>\n\n"
    
    if transactions:
        text += "💰 <b>To'lovlar:</b>\n"
        for txn in transactions[:10]:
            status = t(message.from_user.id, f"payment_{txn['status']}")
            text += f"  • {fmt_sum(txn['amount'])} | {status} | {txn['created_at'][:16]}\n"
    
    if withdrawals:
        text += "\n💸 <b>Pul yechishlar:</b>\n"
        for w in withdrawals[:10]:
            status = t(message.from_user.id, f"withdraw_{w['status']}")
            text += f"  • {fmt_sum(w['amount'])} | {status} | {w['created_at'][:16]}\n"
    
    await message.answer(text, reply_markup=user_main_menu(message.from_user.id))

# Profile (updated with POSITION)
@driver_router.message(F.text.in_([TEXTS['uz']['menu_profile'], TEXTS['ru']['menu_profile']]))
async def show_profile(message: Message) -> None:
    user = get_user_by_telegram(message.from_user.id)
    if not user or user.get("is_registered", 0) != 1:
        await message.answer("❗ Iltimos, avval ro'yxatdan o'ting: /start", reply_markup=welcome_keyboard(message.from_user.id))
        return
    
    stats = get_driver_stats(message.from_user.id)
    status_text = t(message.from_user.id, "profile_status_active") if not user.get("is_blocked") else t(message.from_user.id, "profile_status_blocked")
    
    text = (
        f"{t(message.from_user.id, 'profile_title')}\n\n"
        f"{t(message.from_user.id, 'profile_position')}: <code>{user.get('position', 'N/A')}</code>\n"
        f"{t(message.from_user.id, 'profile_id')}: <code>{user['telegram_id']}</code>\n"
        f"{t(message.from_user.id, 'profile_name')}: {user['full_name'] or '—'}\n"
        f"{t(message.from_user.id, 'profile_phone')}: {user['phone'] or '—'}\n"
        f"{t(message.from_user.id, 'profile_card')}: {user['card_number'] or '—'}\n"
        f"{t(message.from_user.id, 'profile_car')}: {user['car_model'] or '—'}\n"
        f"{t(message.from_user.id, 'profile_car_number')}: {user['car_number'] or '—'}\n"
        f"{t(message.from_user.id, 'profile_status')}: {status_text}\n"
        f"\n{t(message.from_user.id, 'profile_rating')}: ⭐ {user['rating']:.1f}\n"
        f"{t(message.from_user.id, 'profile_orders')}: {stats['total_count']}\n"
        f"{t(message.from_user.id, 'profile_earnings')}: {fmt_sum(stats['total_sum'])}\n"
        f"{t(message.from_user.id, 'profile_joined')}: {user['created_at'][:16]}"
    )
    
    await message.answer(text, reply_markup=user_main_menu(message.from_user.id))

# News, Group, Support, Settings - keep same as before with registration check


# ============================================================
# ADMIN HANDLERS - Updated with registration info
# ============================================================

@admin_router.message(F.text.in_([TEXTS['uz']['menu_admin'], TEXTS['ru']['menu_admin']]))
async def admin_menu_open(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer(t(message.from_user.id, "not_admin"))
        return
    await message.answer(t(message.from_user.id, "admin_title"), reply_markup=admin_main_menu(message.from_user.id))

@admin_router.message(F.text.in_([TEXTS['uz']['admin_drivers'], TEXTS['ru']['admin_drivers']]))
async def admin_drivers_handler(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer(t(message.from_user.id, "not_admin"))
        return
    
    drivers = get_all_drivers(50)
    text = f"👥 <b>Haydovchilar ({len(drivers)} ta ro'yxatdan o'tgan)</b>\n\n"
    
    for driver in drivers[:20]:
        text += (
            f"POSITION: <code>{driver.get('position', 'N/A')}</code>\n"
            f"ID: <code>{driver['telegram_id']}</code>\n"
            f"Ism: {driver['full_name'] or '—'}\n"
            f"Mashina: {driver['car_model'] or '—'} ({driver['car_number'] or '—'})\n"
            f"Balans: {fmt_sum(driver['balance'])}\n"
            f"Holat: {'🔴 Bloklangan' if driver['is_blocked'] else '🟢 Faol'}\n"
            f"---\n"
        )
    
    await message.answer(text, reply_markup=admin_main_menu(message.from_user.id))

@admin_router.message(F.text.in_([TEXTS['uz']['admin_search'], TEXTS['ru']['admin_search']]))
async def admin_search_start(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        await message.answer(t(message.from_user.id, "not_admin"))
        return
    
    await state.set_state(AdminStates.search_driver)
    await message.answer(
        "🔎 <b>Haydovchi qidirish</b>\n\n"
        "Qidirish uchun: ism, telefon, POSITION, yoki telegram ID kiriting:",
        reply_markup=cancel_keyboard(message.from_user.id)
    )

@admin_router.message(AdminStates.search_driver)
async def admin_search_handler(message: Message, state: FSMContext) -> None:
    if message.text == t(message.from_user.id, "cancel"):
        await state.clear()
        await message.answer(t(message.from_user.id, "action_cancelled"), reply_markup=admin_main_menu(message.from_user.id))
        return
    
    query = message.text.strip()
    drivers = search_drivers(query)
    
    if not drivers:
        await message.answer("❌ Hech qanday haydovchi topilmadi")
        return
    
    text = f"🔎 <b>Qidiruv natijalari ({len(drivers)})</b>\n\n"
    
    for driver in drivers[:10]:
        text += (
            f"POSITION: <code>{driver.get('position', 'N/A')}</code>\n"
            f"ID: <code>{driver['telegram_id']}</code>\n"
            f"Ism: {driver['full_name'] or '—'}\n"
            f"Telefon: {driver['phone'] or '—'}\n"
            f"Mashina: {driver['car_model'] or '—'}\n"
            f"Balans: {fmt_sum(driver['balance'])}\n"
            f"Holat: {'🔴 Bloklangan' if driver['is_blocked'] else '🟢 Faol'}\n"
            f"---\n"
        )
    
    await message.answer(text, reply_markup=admin_main_menu(message.from_user.id))
    await state.clear()


# ============================================================
# LANGUAGE HANDLERS
# ============================================================

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
        
        await callback.message.answer(
            t(lang, "lang_updated"),
            reply_markup=user_main_menu(callback.from_user.id)
        )
    await callback.answer()


# ============================================================
# FALLBACK HANDLER
# ============================================================

@driver_router.message()
async def fallback_handler(message: Message) -> None:
    user = get_user_by_telegram(message.from_user.id)
    if not user or user.get("is_registered", 0) != 1:
        await message.answer(
            "❗ Iltimos, avval ro'yxatdan o'ting: /start",
            reply_markup=welcome_keyboard(message.from_user.id)
        )
        return
    
    await message.answer(
        "Iltimos, menyudan biror tugmani bosing.",
        reply_markup=user_main_menu(message.from_user.id)
    )


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
            <p>Bot: <a href="https://t.me/lochin_taxi_bot" style="color:#4fc3f7;">@lochin_taxi_bot</a></p>
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
    # Initialize DB
    init_db()
    
    # Register routers
    register_routers()
    
    # Start web server
    app = web.Application()
    app.add_routes(web_router)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    
    logger.info(f"🚕 {BOT_NAME} started!")
    logger.info(f"✅ Bot running on port {PORT}")
    logger.info(f"✅ Web server started")
    
    # Start bot polling
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
