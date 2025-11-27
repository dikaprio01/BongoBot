# main.py
import os
import logging
import random
import datetime
from datetime import datetime, timedelta 
from sqlalchemy import create_engine, Column, Integer, String, BigInteger, Boolean, DateTime, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm import declarative_base

from aiogram import Bot, Dispatcher, types, F # <-- ДОБАВЛЕНО F для фильтров
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command # <-- ДОБАВЛЕНО для команд
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import asyncio 

# =========================================================
# === 1. НАСТРОЙКИ ===
# =========================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN") 
ADMIN_ID = 1871352653 
WORK_COOLDOWN = timedelta(hours=8)
WORK_PROFIT_MIN = 200
WORK_PROFIT_MAX = 500
BUSINESSES = {
    1: {"name": "Ларек с шаурмой", "cost": 1500, "base_profit": 500, "cooldown": timedelta(hours=12)},
    2: {"name": "Автомойка", "cost": 5000, "base_profit": 1500, "cooldown": timedelta(hours=24)},
    3: {"name": "Кофейня", "cost": 15000, "base_profit": 3000, "cooldown": timedelta(hours=48)},
}
WORK_BUTTON = "Работать 💼"
BUSINESS_BUTTON = "Мои бизнесы 💰"


# =========================================================
# === 2. МОДЕЛИ БАЗЫ ДАННЫХ ===
# =========================================================

Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, index=True)
    username = Column(String)
    balance = Column(BigInteger, default=1000)
    xp = Column(Integer, default=0)
    last_work_time = Column(DateTime, default=datetime.min)
    role = Column(String, default="Безработный")
    job_id = Column(Integer, default=0)
    property_count = Column(Integer, default=0)
    is_admin = Column(Boolean, default=False)
    is_owner = Column(Boolean, default=False)
    is_president = Column(Boolean, default=False)

class Candidate(Base):
    __tablename__ = 'candidates'
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, unique=True, index=True)
    votes = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)

class OwnedBusiness(Base):
    __tablename__ = 'owned_businesses'
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, index=True)
    business_id = Column(Integer)
    name = Column(String)
    count = Column(Integer, default=1)

class Chat(Base):
    __tablename__ = 'chats'
    id = Column(Integer, primary_key=True)
    chat_id = Column(BigInteger, unique=True)
    is_active = Column(Boolean, default=True)


# =========================================================
# === 3. ЛОГИКА ПОДКЛЮЧЕНИЯ И СЕССИЙ ===
# =========================================================

DB_PATH = os.environ.get("DATABASE_URL") 
if DB_PATH and DB_PATH.startswith("postgres://"):
    DB_PATH = DB_PATH.replace("postgres://", "postgresql://", 1)
if not DB_PATH:
    DB_PATH = "sqlite:///data/bongobot.db"

engine = create_engine(DB_PATH, pool_pre_ping=True)
Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    try:
        if not Base.metadata.tables:
            print("FATAL-DEBUG: Base.metadata пуст! Модели не определены.")
            return False
            
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            print("БД: Подключение успешно установлено.")
        
        Base.metadata.create_all(bind=engine)
        print(f"БД: Таблицы успешно созданы (или уже существовали). Найдено моделей: {len(Base.metadata.tables)}.")
        return True
    except Exception as e:
        print(f"FATAL: Ошибка инициализации БД. Таблицы НЕ созданы: {e}") 
        return False

def get_user_profile_sync(telegram_id: int, username: str, admin_id: int):
    with Session() as session:
        user = session.query(User).filter(User.telegram_id == telegram_id).first()
        if not user:
            is_owner = telegram_id == admin_id
            user = User(
                telegram_id=telegram_id, 
                username=username, 
                is_owner=is_owner, 
                balance=1000
            )
            session.add(user)
            session.commit()
        return user

def update_user_sync(telegram_id: int, **kwargs):
    with Session() as session:
        result = session.query(User).filter(User.telegram_id == telegram_id).update(kwargs)
        session.commit()
        return result > 0
        
def save_chat_sync(chat_id: int):
    with Session() as session:
        if not session.query(Chat).filter(Chat.chat_id == chat_id).first():
            session.add(Chat(chat_id=chat_id))
            session.commit()

# =========================================================
# === 4. ЛОГИКА БОТА (КОМАНДЫ) ===
# =========================================================

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher() 

scheduler = AsyncIOScheduler()

async def business_payout_job():
    logging.info("Выполняется работа планировщика: Выплата по бизнесам.")
    pass 

# ИСПРАВЛЕНО: @dp.message_handler(commands=['start'])
@dp.message(Command("start")) 
async def send_welcome(message: types.Message):
    save_chat_sync(message.chat.id)
    user = get_user_profile_sync(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        admin_id=ADMIN_ID
    )
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row(WORK_BUTTON, BUSINESS_BUTTON)

    await message.reply(
        f"Добро пожаловать в BongoBot, **{user.username}**!\n"
        f"Твой баланс: {user.balance} $",
        reply_markup=keyboard
    )

# ИСПРАВЛЕНО: @dp.message_handler(text=WORK_BUTTON)
@dp.message(F.text == WORK_BUTTON)
async def work_handler(message: types.Message):
    telegram_id = message.from_user.id
    user = get_user_profile_sync(telegram_id, message.from_user.username, ADMIN_ID)
    
    time_since_work = datetime.now() - user.last_work_time
    
    if time_since_work < WORK_COOLDOWN:
        remaining_time = WORK_COOLDOWN - time_since_work
        hours = int(remaining_time.total_seconds() // 3600)
        minutes = int((remaining_time.total_seconds() % 3600) // 60)
        
        return await message.reply(
            f"❌ **Ты уже работал!**\n"
            f"Сможешь работать снова через {hours} ч. {minutes} мин."
        )

    profit = random.randint(WORK_PROFIT_MIN, WORK_PROFIT_MAX)
    new_balance = user.balance + profit
    
    update_user_sync(
        telegram_id=telegram_id,
        balance=new_balance,
        last_work_time=datetime.now()
    )
    
    await message.reply(
        f"✅ **Отлично поработал!** Ты заработал **{profit} $**.\n"
        f"Твой новый баланс: {new_balance} $."
    )

# ИСПРАВЛЕНО: @dp.message_handler(text=BUSINESS_BUTTON)
@dp.message(F.text == BUSINESS_BUTTON)
async def businesses_handler(message: types.Message):
    text = "🏢 **Доступные бизнесы для покупки:**\n\n"
    keyboard = InlineKeyboardMarkup(row_width=1)
    
    for biz_id, biz_info in BUSINESSES.items():
        text += (
            f"🔹 **{biz_info['name']}**\n"
            f"   💰 Цена: {biz_info['cost']} $\n"
            f"   💸 Доход: {biz_info['base_profit']} $ каждые {int(biz_info['cooldown'].total_seconds() // 3600)} ч.\n"
        )
        keyboard.add(
            InlineKeyboardButton(
                f"Купить {biz_info['name']} ({biz_info['cost']} $)",
                callback_data=f"buy_biz_{biz_id}"
            )
        )
    await message.reply(text, reply_markup=keyboard)


# ИСПРАВЛЕНО: @dp.callback_query_handler(lambda c: c.data and c.data.startswith('buy_biz_'))
@dp.callback_query(F.data.startswith('buy_biz_'))
async def process_callback_buy_biz(callback_query: types.CallbackQuery):
    telegram_id = callback_query.from_user.id
    biz_id = int(callback_query.data.split('_')[2])
    biz_info = BUSINESSES.get(biz_id)
    
    if not biz_info:
        return await bot.answer_callback_query(callback_query.id, text="Ошибка: Бизнес не найден.")

    user = get_user_profile_sync(telegram_id, callback_query.from_user.username, ADMIN_ID)
    
    if user.balance < biz_info['cost']:
        return await bot.answer_callback_query(
            callback_query.id, 
            text=f"❌ Недостаточно средств! Требуется {biz_info['cost']} $."
        )

    new_balance = user.balance - biz_info['cost']
    update_user_sync(
        telegram_id=telegram_id,
        balance=new_balance
    )
    
    await bot.answer_callback_query(callback_query.id, text=f"✅ Вы успешно купили {biz_info['name']}!")
    
    await bot.edit_message_text(
        f"✅ **Покупка совершена!**\n"
        f"Вы купили **{biz_info['name']}**.\n"
        f"Новый баланс: {new_balance} $.",
        callback_query.from_user.id,
        callback_query.message.message_id,
        reply_markup=None
    )


# --- 5. ЗАПУСК ---

async def on_startup_action(): 
    print("Бот запускается...")
    
    if init_db():
        scheduler.add_job(business_payout_job, 'interval', hours=1, id='business_payout_job')
        scheduler.start()
    else:
        print("Планировщик не запущен из-за ошибки БД.")

async def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN не найден. Установите переменную окружения.")
        
    # Регистрируем обработчик on_startup (v3)
    dp.startup.register(on_startup_action)
    
    # Запускаем polling (новый метод aiogram v3)
    await dp.start_polling(bot, skip_updates=True)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен вручную.")
