import datetime
import asyncio
import os
import logging
import random
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject
from aiogram.enums import ParseMode
from aiogram.utils.keyboard import InlineKeyboardBuilder

# !!! ИСПРАВЛЕНИЕ #1: Импортируем 'select' из SQLAlchemy для использования в cmd_profile
from sqlalchemy import select 

from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Импортируем все модели и синхронные функции из новых файлов
from db_models import User, Candidate, OwnedBusiness 
from db_sync import (
    init_db,
    get_user_profile_sync,
    update_user_sync,
    get_all_users_sync,
    save_chat_sync,
    get_all_chats_sync,
    apply_tax_sync,
    # !!! ИСПРАВЛЕНИЕ #2: Импортируем Session в НАЧАЛЕ ФАЙЛА, чтобы избежать 
    # ошибки 'attempted relative import' внутри функций.
    Session 
)

# --- Константы и Настройки ---
logging.basicConfig(level=logging.INFO)

# ID владельца (АДМИНА) бота (ТВОЙ ID)
ADMIN_ID = 1871352653 

# Настройки времени (в секундах)
JOB_COOLDOWN_SECONDS = 3600 # 1 час
ELECTION_COOLDOWN_SECONDS = 86400 # 24 часа
CANDIDATE_PERIOD_SECONDS = 1800 # 30 минут на набор кандидатов
VOTING_PERIOD_SECONDS = 3600  # 60 минут на голосование
BUSINESS_PAYOUT_INTERVAL_SECONDS = 3600 # Выплата каждый час

# --- НОВЫЕ ЭКОНОМИЧЕСКИЕ КОНСТАНТЫ ---
BUSINESSES = {
    1: {"name": "Уличный Ларек", "price": 100_000, "hourly_income": 2_000},
    2: {"name": "Автомойка", "price": 500_000, "hourly_income": 8_000},
    3: {"name": "ТехноХаб", "price": 1_000_000, "hourly_income": 15_000},
}

PROPERTIES = {
    1: {"name": "Маленькая квартира", "price": 5_000}, 
    2: {"name": "Роскошная вилла", "price": 50_000},
    3: {"name": "Частный остров", "price": 250_000},
}

# --- НАСТРОЙКИ БАЗЫ ДАННЫХ (PostgreSQL) ---
# Scalingo предоставит эту переменную окружения
DB_PATH = os.environ.get("DATABASE_URL") 

# Fix: SQLAlchemy и psycopg2 требуют схему postgresql://
if DB_PATH and DB_PATH.startswith("postgres://"):
    DB_PATH = DB_PATH.replace("postgres://", "postgresql://", 1)

if not DB_PATH:
    # Запасной вариант для локального запуска
    DB_PATH = "sqlite:///data/bongobot.db"


# Состояние выборов (для Scheduler)
ELECTION_STATE = "NONE" # NONE, CANDIDATE_REG, VOTING

# --- Настройка Бота и Планировщика ---
TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler()


# --- Логика Пассивного Дохода ---

async def business_payout_job():
    """Запускается каждый час для выплаты дохода владельцам бизнесов."""
    
    # !!! ИСПРАВЛЕНИЕ: Удаляем относительный импорт, так как Session импортирована выше
    # from .db_sync import Session 
    if not Session: return 

    session = Session()
    try:
        all_businesses = session.query(OwnedBusiness).all()
        payouts = {}
        
        for ob in all_businesses:
            business_data = BUSINESSES.get(ob.business_id)
            if business_data:
                income = business_data['hourly_income'] * ob.count
                payouts[ob.user_id] = payouts.get(ob.user_id, 0) + income
        
        if not payouts:
            logging.info("Пассивный доход: Нет активных бизнесов.")
            return

        for user_id, amount in payouts.items():
            await asyncio.to_thread(
                # User.balance доступен, так как User импортирован выше
                lambda uid, amt: update_user_sync(uid, balance=User.balance + amt),
                user_id, amount
            )
            
            try:
                await bot.send_message(
                    user_id,
                    f"💰 Ваш пассивный доход! Ваши бизнесы принесли **{amount:,} Bongo$** за последний час.",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception as e:
                logging.debug(f"Не удалось отправить уведомление о доходе пользователю {user_id}: {e}")

        logging.info(f"Пассивный доход: Выплачено {len(payouts)} игрокам. Общая сумма: {sum(payouts.values()):,}")
        
    finally:
        session.close()


# --- Логика Выборов: Шаг 1 (Набор кандидатов) ---

def start_candidate_registration():
    # ... (Весь код логики выборов, который мы сделали ранее, остается здесь, 
    #      поскольку он использует глобальные ELECTION_STATE и scheduler)
    
    # ... (весь код start_candidate_registration, end_candidate_registration, 
    #       notify_chats_voting_start, end_voting_and_announce_winner и т.д.)
    
    # --- ВЕСЬ КОД ФУНКЦИЙ ЛОГИКИ ВЫБОРОВ ОСТАВЬТЕ ЗДЕСЬ. Я НЕ ВКЛЮЧАЮ ЕГО ДЛЯ ЭКОНОМИИ МЕСТА ---
    pass 


# --- Хэндлеры для Игрового Функционала ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await asyncio.to_thread(
        get_user_profile_sync,
        message.from_user.id,
        message.from_user.username or message.from_user.first_name,
        ADMIN_ID # Передаем admin_id
    )
    await asyncio.to_thread(save_chat_sync, message.chat.id)
    
    await message.answer("🎉 Добро пожаловать в BongoBot! 🎉\n\n"
                         "Напиши /profile, чтобы увидеть свой счет.\n"
                         "Используй /work, чтобы заработать денег.")


@dp.message(Command("profile"))
async def cmd_profile(message: types.Message):
    user_id = message.from_user.id
    user_data = await asyncio.to_thread(
        get_user_profile_sync,
        user_id,
        message.from_user.username or message.from_user.first_name,
        ADMIN_ID
    )
    
    # Получение информации о бизнесах
    # !!! ИСПРАВЛЕНИЕ: Удаляем относительный импорт, так как Session импортирована выше
    # from .db_sync import Session
    
    if not Session: return
    session = Session()
    try:
        # select импортирован в начале файла
        owned_businesses = session.execute(select(OwnedBusiness).filter_by(user_id=user_id)).scalars().all()
    finally:
        session.close()
    
    total_hourly_income = sum(
        BUSINESSES.get(b.business_id)['hourly_income'] * b.count 
        for b in owned_businesses 
        if BUSINESSES.get(b.business_id)
    )
    
    business_text = "\n".join(
        [f"   💼 {BUSINESSES.get(b.business_id)['name']}: {b.count} шт." 
         for b in owned_businesses 
         if BUSINESSES.get(b.business_id)]
    ) if owned_businesses else "   (Нет)"

    role_prefix = ""
    if user_data.is_owner:
        role_prefix = "👑 ВЛАДЕЛЕЦ 👑 "
    elif user_data.is_president:
        role_prefix = "🇺🇸 ПРЕЗИДЕНТ 🇺🇸 "
    
    profile_text = (
        f"{role_prefix}@{user_data.username}\n\n"
        f"💰 Баланс: **{user_data.balance:,} Bongo$**\n"
        f"💼 Должность: {user_data.role}\n"
        f"✨ Опыт (XP): {user_data.xp}\n"
        f"--- ИМУЩЕСТВО ---\n"
        f"🏡 Объектов: **{user_data.property_count}**\n"
        f"--- БИЗНЕС ---\n"
        f"💸 Доход в час: **{total_hourly_income:,} Bongo$**\n"
        f"{business_text}\n"
        f"---"
        f"\nИспользуй /work или покупай /businesses."
    )
    
    await message.answer(profile_text, parse_mode=ParseMode.MARKDOWN)

# ... (Остальные команды /work, /properties, /buy_property, /businesses, /buy_business, /top, 
#       /election, /tax, /candidate, /vote, /admin, /give, /set_president, /reset_db
#       ОСТАВЬТЕ ЗДЕСЬ. Я НЕ ВКЛЮЧАЮ ИХ ДЛЯ ЭКОНОМИИ МЕСТА)
pass # Placeholder for all other handlers


# --- Запуск Бота и Планировщика ---

async def main():
    print("Бот запускается...")
    
    # 1. Инициализация БД
    # !!! ИСПРАВЛЕНИЕ #3: Убираем DB_PATH, чтобы избежать ошибки TypeError
    if not init_db():
        print("FATAL: Database initialization failed. Exiting.")
        return

    # 2. Запуск планировщика
    scheduler.start() 
    
    # Планировщик пассивного дохода
    scheduler.add_job(
        business_payout_job, 
        'interval', 
        seconds=BUSINESS_PAYOUT_INTERVAL_SECONDS, 
        max_instances=1,
        id='payout_job'
    )
    # ... (другие job'ы, если есть)
    
    # 3. Запуск бота
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
