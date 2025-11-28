# main.py - Переделано для работы с MySQL/MariaDB через DATABASE_URL
import os
import logging
import random
import datetime
from datetime import datetime, timedelta 
from sqlalchemy import create_engine, Column, Integer, String, BigInteger, Boolean, DateTime, text
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.exc import SQLAlchemyError 
from asyncio import to_thread 

from aiogram import Bot, Dispatcher, types, F 
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton 
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import asyncio 

# =========================================================
# === 1. НАСТРОЙКИ ===
# =========================================================

# Токен берется из переменных окружения Railway
BOT_TOKEN = os.environ.get("BOT_TOKEN") 

# ID владельца бота (Замени на свой Telegram ID)
ADMIN_ID = 1871352653 

# Настройки работы
WORK_COOLDOWN = timedelta(hours=8)
WORK_PROFIT_MIN = 200
WORK_PROFIT_MAX = 500

# Настройки бизнеса
BUSINESSES = {
    1: {"name": "Ларек с шаурмой", "cost": 1500, "base_profit": 500, "cooldown": timedelta(hours=12)},
    2: {"name": "Автомойка", "cost": 5000, "base_profit": 1500, "cooldown": timedelta(hours=24)},
    3: {"name": "Кофейня", "cost": 15000, "base_profit": 3000, "cooldown": timedelta(hours=48)},
}

# Кнопки
WORK_BUTTON = "Работать 💼"
BUSINESS_BUTTON = "Мои бизнесы 💰"
CASINO_BUTTON = "Казино 🎲" 
TOP_BUTTON = "Топ игроков 🏆" 

# =========================================================
# === 2. МОДЕЛИ БАЗЫ ДАННЫХ ===
# =========================================================

Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    # Используем Integer для ID в MySQL, BigInteger для telegram_id
    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, index=True) 
    username = Column(String(50), nullable=True) # Ограничение длины для MySQL
    balance = Column(BigInteger, default=1000)
    xp = Column(Integer, default=0)
    # MySQL/SQLAlchemy требует явного указания типа для DATETIME
    last_work_time = Column(DateTime, default=datetime.min)
    role = Column(String(50), default="Безработный")
    job_id = Column(Integer, default=0)
    property_count = Column(Integer, default=0)
    is_admin = Column(Boolean, default=False)
    is_owner = Column(Boolean, default=False)
    is_president = Column(Boolean, default=False)
    is_banned = Column(Boolean, default=False) 

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
    name = Column(String(100)) # Ограничение длины для MySQL
    count = Column(Integer, default=1)

class Chat(Base):
    __tablename__ = 'chats'
    id = Column(Integer, primary_key=True)
    chat_id = Column(BigInteger, unique=True)
    is_active = Column(Boolean, default=True)


# =========================================================
# === 3. ЛОГИКА ПОДКЛЮЧЕНИЯ И СЕССИЙ (ДЛЯ MySQL) ===
# =========================================================

# В Railway переменная для MySQL будет называться MYSQL_URL. 
# Мы используем ее или DATABASE_URL.
DB_PATH = os.environ.get("MYSQL_URL") or os.environ.get("DATABASE_URL")
if not DB_PATH:
    # Fallback для локальной разработки с SQLite
    DB_PATH = "sqlite:///data/bongobot.db"
    logging.warning("DB_PATH not found. Using local SQLite.")
elif "mysql://" in DB_PATH:
    # Замена префикса для SQLAlchemy и драйвера pymysql
    DB_PATH = DB_PATH.replace("mysql://", "mysql+pymysql://", 1)


engine = create_engine(DB_PATH, pool_pre_ping=True)
Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    """Инициализирует БД и создает таблицы."""
    try:
        with engine.connect() as connection:
            # Простой запрос для проверки подключения
            connection.execute(text("SELECT 1"))
            print("БД: Подключение успешно установлено.")
        
        Base.metadata.create_all(bind=engine)
        print(f"БД: Таблицы успешно созданы (или уже существовали). Найдено моделей: {len(Base.metadata.tables)}.")
        return True
    except SQLAlchemyError as e:
        print(f"FATAL DB ERROR: Ошибка при инициализации БД: {e}") 
        return False
    except Exception as e:
        print(f"FATAL: Ошибка инициализации БД: {e}") 
        return False

# --- Синхронные CRUD-функции ---

def get_user_profile_sync(telegram_id: int, username: str, admin_id: int):
    with Session() as session:
        user = session.query(User).filter(User.telegram_id == telegram_id).first()
        if not user:
            is_owner = telegram_id == admin_id
            user = User(
                telegram_id=telegram_id, 
                username=username, 
                is_owner=is_owner, 
                is_admin=is_owner,
                balance=1000
            )
            session.add(user)
            session.commit()
            session.refresh(user) 
        
        # Принудительная загрузка
        _ = user.is_banned
        _ = user.balance
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

class CasinoState(StatesGroup):
    bet_amount = State()

async def business_payout_job():
    logging.info("Выполняется работа планировщика: Выплата по бизнесам (MySQL).")
    pass 

@dp.message(Command("start")) 
async def send_welcome(message: types.Message):
    await to_thread(save_chat_sync, message.chat.id)
    
    user = await to_thread(
        get_user_profile_sync,
        telegram_id=message.from_user.id,
        username=message.from_user.username or message.from_user.first_name,
        admin_id=ADMIN_ID
    )
    
    if user.is_banned:
        return await message.reply("⛔️ Ты забанен и не можешь пользоваться ботом.")
    
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=WORK_BUTTON), KeyboardButton(text=BUSINESS_BUTTON)],
            [KeyboardButton(text=CASINO_BUTTON), KeyboardButton(text=TOP_BUTTON)]
        ], 
        resize_keyboard=True,
        is_persistent=True
    )

    await message.reply(
        f"Добро пожаловать в BongoBot, **{user.username}**!\n"
        f"Твой баланс: {user.balance} $",
        reply_markup=keyboard
    )

@dp.message(F.text == WORK_BUTTON) 
async def work_handler(message: types.Message):
    telegram_id = message.from_user.id
    user = await to_thread(get_user_profile_sync, telegram_id, message.from_user.username, ADMIN_ID)
    
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
    
    await to_thread(
        update_user_sync,
        telegram_id=telegram_id,
        balance=new_balance,
        last_work_time=datetime.now()
    )
    
    await message.reply(
        f"✅ **Отлично поработал!** Ты заработал **{profit} $**.\n"
        f"Твой новый баланс: {new_balance} $."
    )

@dp.message(F.text == BUSINESS_BUTTON) 
async def businesses_handler(message: types.Message):
    text = "🏢 **Доступные бизнесы для покупки:**\n\n"
    
    buttons = []
    for biz_id, biz_info in BUSINESSES.items():
        text += (
            f"🔹 **{biz_info['name']}**\n"
            f"   💰 Цена: {biz_info['cost']} $\n"
            f"   💸 Доход: {biz_info['base_profit']} $ каждые {int(biz_info['cooldown'].total_seconds() // 3600)} ч.\n"
        )
        buttons.append( 
            InlineKeyboardButton(
                text=f"Купить {biz_info['name']} ({biz_info['cost']} $)",
                callback_data=f"buy_biz_{biz_id}"
            )
        )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[buttons])
    
    await message.reply(text, reply_markup=keyboard)


@dp.callback_query(F.data.startswith('buy_biz_')) 
async def process_callback_buy_biz(callback_query: types.CallbackQuery):
    telegram_id = callback_query.from_user.id
    biz_id = int(callback_query.data.split('_')[2])
    biz_info = BUSINESSES.get(biz_id)
    
    if not biz_info:
        return await bot.answer_callback_query(callback_query.id, text="Ошибка: Бизнес не найден.")

    user = await to_thread(get_user_profile_sync, telegram_id, callback_query.from_user.username, ADMIN_ID)
    
    if user.balance < biz_info['cost']:
        return await bot.answer_callback_query(
            callback_query.id, 
            text=f"❌ Недостаточно средств! Требуется {biz_info['cost']} $."
        )

    # Логика покупки бизнеса
    with Session() as session:
        try:
            # 1. Списание баланса
            user_in_session = session.query(User).filter(User.telegram_id == telegram_id).first()
            if user_in_session:
                user_in_session.balance -= biz_info['cost']

            # 2. Добавление или обновление OwnedBusiness
            owned_biz = session.query(OwnedBusiness).filter_by(user_id=telegram_id, business_id=biz_id).first()
            if owned_biz:
                owned_biz.count += 1
            else:
                new_owned_biz = OwnedBusiness(
                    user_id=telegram_id,
                    business_id=biz_id,
                    name=biz_info['name'],
                    count=1
                )
                session.add(new_owned_biz)
            
            session.commit()
            
            new_balance = user.balance - biz_info['cost'] 
            
            await bot.answer_callback_query(callback_query.id, text=f"✅ Вы успешно купили {biz_info['name']}!")
            
            await bot.edit_message_text(
                f"✅ **Покупка совершена!**\n"
                f"Вы купили **{biz_info['name']}**.\n"
                f"Новый баланс: {new_balance} $.",
                telegram_id,
                callback_query.message.message_id,
                reply_markup=None
            )
            
        except Exception as e:
            session.rollback()
            logging.error(f"Ошибка при покупке бизнеса (MySQL): {e}")
            await bot.answer_callback_query(callback_query.id, text="Произошла ошибка при сохранении данных.")


# =========================================================
# === 5. ЗАПУСК ===
# =========================================================

async def on_startup_action(): 
    print("Бот запускается (MySQL)...")
    
    if init_db():
        # scheduler.add_job(business_payout_job, 'interval', hours=1, id='business_payout_job')
        # scheduler.start() 
        print("Планировщик временно не запущен.")
    else:
        print("Планировщик не запущен из-за критической ошибки БД.")

async def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN не найден. Установите переменную окружения.")
        
    dp.startup.register(on_startup_action)
    
    await dp.start_polling(bot, skip_updates=True)


if __name__ == '__main__':
    if "sqlite" in DB_PATH:
        os.makedirs("data", exist_ok=True)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен вручную.")
    except Exception as e:
        print(f"Критическая ошибка при запуске: {e}")
