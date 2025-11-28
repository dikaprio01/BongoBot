import os
import logging
import random
import asyncio
from datetime import datetime, timedelta

from sqlalchemy import create_engine, Column, Integer, String, BigInteger, Boolean, DateTime, Float, ForeignKey, text
from sqlalchemy.orm import sessionmaker, declarative_base, relationship, selectinload
from sqlalchemy.exc import SQLAlchemyError

from aiogram import Bot, Dispatcher, types, F
from aiogram.client.default import DefaultBotProperties 
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, BotCommand, BotCommandScopeDefault
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramAPIError
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# =========================================================
# === 1. КОНФИГУРАЦИЯ И НАСТРОЙКИ ===
# =========================================================

logging.basicConfig(level=logging.INFO)

# Токен и ID админа
BOT_TOKEN = os.environ.get("BOT_TOKEN")
try:
    ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
except ValueError:
    ADMIN_ID = 0

# Настройки Базы Данных
DB_PATH = os.environ.get("MYSQL_URL") or os.environ.get("DATABASE_URL")
if DB_PATH and "mysql://" in DB_PATH:
    # Замена префикса для SQLAlchemy и драйвера pymysql
    DB_PATH = DB_PATH.replace("mysql://", "mysql+pymysql://", 1)
if not DB_PATH:
    # Если нет env-переменных, используем локальный SQLite
    if not os.path.exists("data"):
        os.makedirs("data")
    DB_PATH = "sqlite:///data/bongobot.db"

# Игровой Баланс
WORK_COOLDOWN = timedelta(hours=4)     # Работать можно раз в 4 часа
BUSINESS_PAYOUT_INTERVAL = 3600        # Выплата с бизнеса раз в час (секунды)
MAX_TAX_RATE = 0.20                    # Максимальный налог 20%

# Бизнесы
BUSINESSES = {
    1: {"name": "🌯 Ларек с шаурмой", "cost": 5_000, "income": 200},
    2: {"name": "🚕 Служба Такси", "cost": 25_000, "income": 800},
    3: {"name": "☕ Кофейня 'Sova'", "cost": 75_000, "income": 2_500},
    4: {"name": "⛽ Заправка Oil", "cost": 250_000, "income": 7_000},
    5: {"name": "💎 Ювелирный Бутик", "cost": 1_000_000, "income": 30_000},
}

# Выборы
ELECTION_DURATION_CANDIDACY = timedelta(minutes=30)
ELECTION_DURATION_VOTING = timedelta(minutes=60)    
ELECTION_COOLDOWN = timedelta(days=1)               

# Кнопки меню
BTN_PROFILE = "👤 Профиль"
BTN_WORK = "🔨 Работать"
BTN_BUSINESS = "💼 Бизнес"
BTN_CASINO = "🎰 Казино"
BTN_TOP = "🏆 Топ Богачей"
BTN_POLITICS = "🏛 Политика"

# =========================================================
# === 2. БАЗА ДАННЫХ (ORM) ===
# =========================================================

Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, index=True)
    username = Column(String(100))
    
    # Экономика
    balance = Column(BigInteger, default=1000)
    last_work_time = Column(DateTime, default=datetime.min)
    
    # Статусы
    is_admin = Column(Boolean, default=False)  
    is_owner = Column(Boolean, default=False)  
    is_president = Column(Boolean, default=False)
    
    # Наказания
    is_banned = Column(Boolean, default=False)
    arrest_expires = Column(DateTime, nullable=True)

    # Выборы
    last_vote_time = Column(DateTime, nullable=True)

class OwnedBusiness(Base):
    __tablename__ = 'owned_businesses'
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, index=True)
    business_id = Column(Integer)
    count = Column(Integer, default=0)

class ElectionState(Base):
    __tablename__ = 'election_state'
    id = Column(Integer, primary_key=True)
    phase = Column(String(20), default="IDLE") # IDLE, CANDIDACY, VOTING
    tax_rate = Column(Float, default=0.05)     # Налог (по умолчанию 5%)
    end_time = Column(DateTime, nullable=True)
    last_election_time = Column(DateTime, default=datetime.min) # Кулдаун выборов

class Candidate(Base):
    __tablename__ = 'candidates'
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, unique=True)
    votes = Column(Integer, default=0)

class Chat(Base):
    __tablename__ = 'chats'
    id = Column(Integer, primary_key=True)
    chat_id = Column(BigInteger, unique=True)

# =========================================================
# === 3. ПОДКЛЮЧЕНИЕ К БД ===
# =========================================================

engine = create_engine(DB_PATH, pool_pre_ping=True, pool_size=10, max_overflow=20)
Session = sessionmaker(bind=engine)

def init_db():
    """Инициализирует БД и создает таблицы, а также состояние выборов."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        Base.metadata.create_all(engine)
        
        # Инициализация состояния выборов/налогов, если нет
        with Session() as s:
            state = s.query(ElectionState).first()
            if not state:
                s.add(ElectionState())
                s.commit()
        return True
    except Exception as e:
        logging.error(f"DB Init Error: {e}")
        return False

# --- Синхронные хелперы для БД ---

def get_user(telegram_id, username=None, init_admin=False):
    """Получает юзера или создает нового."""
    with Session() as s:
        u = s.query(User).filter_by(telegram_id=telegram_id).first()
        if not u:
            is_dev = (telegram_id == ADMIN_ID)
            u = User(telegram_id=telegram_id, username=username, is_owner=is_dev, is_admin=is_dev)
            s.add(u)
            s.commit()
            # Принудительное обновление объекта после создания
            s.refresh(u)
        else:
            # Обновляем юзернейм, если сменился
            if username and u.username != username:
                u.username = username
                s.commit()
                
        # Обращение к атрибутам для их "загрузки" (чтобы избежать DetachedInstanceError)
        _ = u.balance
        _ = u.is_banned
        _ = u.arrest_expires
        _ = u.username
        
        return u

def get_tax_rate():
    """Получает текущую ставку налога."""
    with Session() as s:
        state = s.query(ElectionState).first()
        return state.tax_rate if state else 0.05

def pay_tax_to_president(amount):
    """Переводит налог президенту."""
    with Session() as s:
        pres = s.query(User).filter_by(is_president=True).first()
        if pres:
            pres.balance += amount
            s.commit()

# =========================================================
# === 4. ИНИЦИАЛИЗАЦИЯ БОТА ===
# =========================================================

# ИСПРАВЛЕНО: Использование DefaultBotProperties для совместимости с aiogram 3.7+
BOT_PROPS = DefaultBotProperties(parse_mode="Markdown")
bot = Bot(token=BOT_TOKEN, default=BOT_PROPS)
dp = Dispatcher()
scheduler = AsyncIOScheduler()

class CasinoState(StatesGroup):
    bet = State()

class AdminState(StatesGroup):
    ban_id = State()
    arrest_id = State()
    arrest_time = State()
    
    give_id = State()
    give_amount = State()
    
    tax_rate = State()
    
    # Состояния для выдачи по ID
    give_target_id = State()
    give_amount_input = State()
    
    # Состояния для ареста по ID
    arrest_target_id = State()
    arrest_time_reason = State()

# --- Настройка команд для меню Telegram ---
async def set_bot_commands(bot: Bot):
    """Устанавливает команды для меню Telegram."""
    commands = [
        BotCommand(command="start", description="Запуск бота"),
        BotCommand(command="profile", description="Профиль и баланс"),
        BotCommand(command="work", description="Поработать (кулдаун 4ч)"),
        BotCommand(command="admin", description="Панель администратора (если есть права)"),
        BotCommand(command="help", description="Подробный список команд и их синтаксис"),
    ]
    await bot.set_my_commands(commands, scope=BotCommandScopeDefault())
    logging.info("Команды бота установлены.")

# --- Утилита для рассылки сообщений ---
async def broadcast_message_to_chats(bot: Bot, message_text: str):
    """Отправляет сообщение во все зарегистрированные чаты."""
    logging.info("Начало рассылки уведомлений по чатам.")
    with Session() as s:
        # Получаем список всех chat_id из таблицы Chat
        chat_ids = [chat.chat_id for chat in s.query(Chat).all()]
        
    success_count = 0
    
    for chat_id in chat_ids:
        try:
            await bot.send_message(
                chat_id,
                message_text,
                parse_mode="Markdown"
            )
            success_count += 1
            await asyncio.sleep(0.05)
        except TelegramAPIError as e:
            logging.warning(f"Не удалось отправить сообщение в чат {chat_id}: {e}")
        except Exception as e:
            logging.error(f"Непредвиденная ошибка при рассылке в чат {chat_id}: {e}")

    logging.info(f"Рассылка завершена. Успешно отправлено в {success_count} чатов из {len(chat_ids)}.")

# =========================================================
# === 5. ЭКОНОМИКА И ПЛАНИРОВЩИК ===
# =========================================================

async def business_payout(bot: Bot):
    """
    Начисление дохода раз в час (запускается планировщиком).
    """
    logging.info("Выплата доходов от бизнеса...")
    
    with Session() as s:
        all_biz = s.query(OwnedBusiness).all()
        state = s.query(ElectionState).first()
        tax = state.tax_rate
        
        payouts = {}
        
        # 1. Считаем начисления и налоги
        for ob in all_biz:
            info = BUSINESSES.get(ob.business_id)
            if info:
                gross_income = info['income'] * ob.count
                tax_cut = int(gross_income * tax)
                net_income = gross_income - tax_cut
                
                # Налог президенту (логика без изменений)
                pres = s.query(User).filter_by(is_president=True).first()
                if pres:
                    # Важно: pres может быть None, если в БД нет президента
                    pay_tax_to_president(tax_cut)
                
                payouts[ob.user_id] = payouts.get(ob.user_id, 0) + net_income

        # 2. Зачисление и рассылка уведомлений в ЛС
        for uid, amount in payouts.items():
            u = s.query(User).filter_by(telegram_id=uid).first()
            
            if u:
                # Проверка, что игрок не забанен и не арестован
                if not u.is_banned and (u.arrest_expires is None or u.arrest_expires < datetime.now()):
                    u.balance += amount
                    
                    try:
                        await bot.send_message(
                            uid,
                            f"💼 **Бизнес-доход:** +{amount:,} $\n(Налог {int(tax*100)}% уплачен в Казну)",
                            # ИСПРАВЛЕНО: parse_mode можно указывать тут
                            parse_mode="Markdown"
                        )
                    except TelegramAPIError as e:
                        if "Forbidden" in str(e):
                             logging.warning(f"Пользователь {uid} заблокировал бота. Сообщение не отправлено.")
                        else:
                             logging.error(f"Ошибка при отправке дохода в ЛС {uid}: {e}")
                    except Exception as e:
                        logging.error(f"Непредвиденная ошибка при отправке дохода в ЛС {uid}: {e}")
                    
        # Сохраняем все начисления
        s.commit()
    
    logging.info("Выплата доходов от бизнеса завершена.")

# --- Функция для автоматического завершения выборов ---
async def check_election_end(bot: Bot):
    """Проверяет и завершает фазу выборов по времени."""
    with Session() as s:
        state = s.query(ElectionState).first()
        if not state or state.phase == "IDLE":
            return
            
        now = datetime.now()
        
        if state.end_time and now >= state.end_time:
            # Завершение фазы.
            
            if state.phase == "CANDIDACY":
                # Переход к ГОЛОСОВАНИЮ
                state.phase = "VOTING"
                state.end_time = now + ELECTION_DURATION_VOTING
                s.commit()
                
                message = (
                    "🗳 **НАЧАЛО ГОЛОСОВАНИЯ!**\n"
                    "Прием заявок завершен. Выберите Президента!\n"
                    f"Голосование продлится до {state.end_time.strftime('%H:%M:%S')} МСК."
                )
                await broadcast_message_to_chats(bot, message)
                
            elif state.phase == "VOTING":
                # Завершение ВЫБОРОВ, подсчет голосов
                await end_elections_logic(s, bot)

async def end_elections_logic(s, bot: Bot):
    """Логика подсчета голосов и объявления победителя."""
    state = s.query(ElectionState).first()
    candidates = s.query(Candidate).order_by(Candidate.votes.desc()).all()
    
    # Сбрасываем текущего президента
    old_pres = s.query(User).filter_by(is_president=True).first()
    if old_pres:
        old_pres.is_president = False
    
    winner = None
    if candidates:
        winner = s.query(User).filter_by(telegram_id=candidates[0].user_id).first()
        if winner:
            winner.is_president = True
            
            message = (
                f"🎉 **ВЫБОРЫ ЗАВЕРШЕНЫ!** 🎉\n"
                f"С большим отрывом победил наш новый Президент: **{winner.username}**!\n"
                f"Поздравляем! Теперь он будет управлять налогами."
            )
        else:
             message = "❌ Выборы завершены, но не удалось найти победителя."
    else:
        message = "❌ Выборы завершены. Кандидатов не было, президент не выбран."

    # Очистка состояния
    state.phase = "IDLE"
    state.end_time = None
    state.last_election_time = datetime.now()
    s.query(Candidate).delete()
    s.commit()
    
    await broadcast_message_to_chats(bot, message)


# =========================================================
# === 6. ХЕНДЛЕРЫ: ОСНОВНОЕ ===
# =========================================================

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    # Сохраняем чат
    with Session() as s:
        # Проверяем на is_private, чтобы не сохранять ЛС бота как чат для рассылки
        if message.chat.type != 'private' and not s.query(Chat).filter_by(chat_id=message.chat.id).first():
            s.add(Chat(chat_id=message.chat.id))
            s.commit()

    u = await asyncio.to_thread(get_user, message.from_user.id, message.from_user.username)
    
    if u.is_banned:
        return await message.reply("⛔️ Вы забанены и не можете пользоваться ботом.")
    
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text=BTN_PROFILE), KeyboardButton(text=BTN_WORK)],
        [KeyboardButton(text=BTN_BUSINESS), KeyboardButton(text=BTN_CASINO)],
        [KeyboardButton(text=BTN_POLITICS), KeyboardButton(text=BTN_TOP)]
    ], resize_keyboard=True)
    
    await message.answer(
        f"👋 **Привет, {u.username}**!\n"
        f"💰 Твой баланс: **{u.balance:,} $**",
        reply_markup=kb,
        parse_mode="Markdown"
    )

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    """Обработчик команды /help - отображает все команды и подсказки."""
    u = await asyncio.to_thread(get_user, message.from_user.id, message.from_user.username)

    # Общие команды
    text = (
        f"🤖 **СПРАВКА ПО КОМАНДАМ**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"**Основные команды (Кнопки):**\n"
        f"/start - Запуск бота.\n"
        f"/profile - Ваш профиль и баланс.\n"
        f"/work - Поработать (раз в 4 часа).\n"
        f"/help - Показать это меню.\n"
        f"**Кнопки меню:** Профиль, Работать, Бизнес, Казино, Топ, Политика.\n"
    )

    # Административные команды (показываем, если пользователь админ)
    if u.is_admin or u.is_owner:
        text += (
            f"\n🛡️ **АДМИНИСТРАТОРСКИЕ КОМАНДЫ:**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"1. **Выдать деньги (Быстро):**\n"
            f"   Синтаксис: `/give [сумма]`\n"
            f"   _Использование:_ Ответьте на сообщение игрока и введите команду (напр., `/give 10000`).\n\n"
            f"2. **Арест (Быстро):**\n"
            f"   Синтаксис: `/arrest [минуты] [причина]`\n"
            f"   _Использование:_ Ответьте на сообщение игрока (напр., `/arrest 60 Чит`).\n\n"
            f"3. **Освобождение (Быстро):**\n"
            f"   Синтаксис: `/release`\n"
            f"   _Использование:_ Ответьте на сообщение арестованного игрока.\n\n"
            f"4. **Панель управления:**\n"
            f"   Команда: `/admin` (открывает меню с кнопками для сложных действий: налоги, выборы, ручной ввод ID)."
        )

    await message.answer(text, parse_mode="Markdown")

@dp.message(F.text == BTN_PROFILE)
async def cmd_profile(message: types.Message):
    u = await asyncio.to_thread(get_user, message.from_user.id, message.from_user.username)
    
    status_emoji = "👤"
    status_text = "Гражданин"
    
    if u.is_owner: status_text, status_emoji = "Владелец", "👑"
    elif u.is_president: status_text, status_emoji = "Президент", "🦅"
    elif u.is_admin: status_text, status_emoji = "Администратор", "🛡"
    
    arrest_text = ""
    if u.arrest_expires and u.arrest_expires > datetime.now():
        left = u.arrest_expires - datetime.now()
        minutes = int(left.total_seconds() // 60)
        seconds = int(left.total_seconds() % 60)
        arrest_text = f"\n🔒 **ТЫ В ТЮРЬМЕ**\nСрок истекает через: **{minutes} мин. {seconds} сек.**"

    # Считаем бизнес
    with Session() as s:
        biz_list = s.query(OwnedBusiness).filter_by(user_id=u.telegram_id).all()
        biz_info = "\n".join([f"  - {BUSINESSES[b.business_id]['name']}: {b.count} шт." for b in biz_list])
        biz_count = sum(b.count for b in biz_list)
    
    msg = (
        f"📑 **Твой Профиль**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{status_emoji} **Статус:** {status_text}\n"
        f"🆔 **ID:** `{u.telegram_id}`\n"
        f"👤 **Имя:** {u.username}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💰 **Баланс:** {u.balance:,} $\n"
        f"💼 **Бизнесы:** {biz_count} шт.\n"
        f"{biz_info or '  - Нет бизнеса.'}\n"
        f"━━━━━━━━━━━━━━━━━━{arrest_text}"
    )
    await message.answer(msg, parse_mode="Markdown")

@dp.message(F.text == BTN_WORK)
@dp.message(Command("work"))
async def cmd_work(message: types.Message):
    u = await asyncio.to_thread(get_user, message.from_user.id)
    if u.is_banned: return
    
    # Проверка на арест
    if u.arrest_expires and u.arrest_expires > datetime.now():
        left = u.arrest_expires - datetime.now()
        minutes = int(left.total_seconds() // 60) + 1
        return await message.answer(f"🔒 Ты в тюрьме! Выйдешь через {minutes} мин. Работать нельзя.")

    if datetime.now() - u.last_work_time < WORK_COOLDOWN:
        rem = WORK_COOLDOWN - (datetime.now() - u.last_work_time)
        hours = int(rem.total_seconds()//3600)
        minutes = int((rem.total_seconds()%3600)//60)
        return await message.answer(f"⏳ Ты устал. Отдохни еще {hours}ч {minutes}мин.")

    base_earned = random.randint(300, 1200)
    
    # Налог
    tax_rate = await asyncio.to_thread(get_tax_rate)
    tax = int(base_earned * tax_rate)
    net_earned = base_earned - tax
    
    with Session() as s:
        user = s.query(User).filter_by(telegram_id=u.telegram_id).first()
        user.balance += net_earned
        user.last_work_time = datetime.now()
        
        # Платим президенту
        pres = s.query(User).filter_by(is_president=True).first()
        if pres:
            pay_tax_to_president(tax)
        s.commit()
        
    await message.answer(
        f"🔨 Ты поработал на стройке.\n"
        f"💵 Заработано: **{base_earned:,} $**\n"
        f"💸 Налог ({int(tax_rate*100)}%): -{tax:,} $\n"
        f"💰 **Итого:** +{net_earned:,} $.\n"
        f"Новый баланс: {user.balance:,} $"
    )

# =========================================================
# === 7. КАЗИНО ===
# =========================================================

@dp.message(F.text == BTN_CASINO)
async def cmd_casino_menu(message: types.Message):
    u = await asyncio.to_thread(get_user, message.from_user.id)
    if u.is_banned or (u.arrest_expires and u.arrest_expires > datetime.now()):
        return await message.answer("🚫 Вы не можете играть в казино.")
        
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎲 Играть в Кости (Макс 50к)", callback_data="casino_dice")]
    ])
    await message.answer(
        f"🎰 **Казино**\n"
        f"Твой баланс: **{u.balance:,} $**\n"
        f"Выберите ставку и игру.",
        reply_markup=kb,
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "casino_dice")
async def casino_start_dice(call: types.CallbackQuery, state: FSMContext):
    u = await asyncio.to_thread(get_user, call.from_user.id)
    if u.is_banned or (u.arrest_expires and u.arrest_expires > datetime.now()):
        return await call.answer("🚫 Вы не можете играть в казино.", show_alert=True)
    
    if u.balance <= 0:
        return await call.answer("У вас нет денег для ставки!", show_alert=True)

    await call.message.edit_text(
        f"🎲 **Кости (Макс ставка 50,000 $)**\n"
        f"Твой баланс: **{u.balance:,} $**\n"
        f"Введите сумму ставки (или 'отмена'):",
        parse_mode="Markdown"
    )
    await state.set_state(CasinoState.bet)
    await call.answer()

@dp.message(CasinoState.bet)
async def casino_place_bet(message: types.Message, state: FSMContext):
    if message.text.lower() == 'отмена':
        await state.clear()
        return await message.answer("❌ Отменено.")
        
    try:
        bet_amount = int(message.text)
        if bet_amount <= 0 or bet_amount > 50000:
            return await message.answer("❌ Ставка должна быть от 1 $ до 50,000 $.")
    except ValueError:
        return await message.answer("❌ Неверный формат. Введите целое число.")
        
    u = await asyncio.to_thread(get_user, message.from_user.id)
    if bet_amount > u.balance:
        return await message.answer(f"❌ У тебя нет столько денег! Доступно: {u.balance:,} $")

    await state.clear()
    
    # Логика игры
    result = await bot.send_dice(message.chat.id, emoji='🎲')
    dice_value = result.dice.value
    await asyncio.sleep(4) # Ждем, пока кубик остановится
    
    win = False
    
    # Выигрыш: 5 или 6
    if dice_value in [5, 6]:
        win_amount = bet_amount
        win = True
    else:
        win_amount = -bet_amount
        
    with Session() as s:
        user = s.query(User).filter_by(telegram_id=u.telegram_id).first()
        user.balance += win_amount
        new_balance = user.balance
        s.commit()
    
    if win:
        msg = f"🎉 **ПОБЕДА!** Выпало {dice_value} (+{bet_amount:,} $).\nНовый баланс: {new_balance:,} $"
    else:
        msg = f"😞 **ПРОИГРЫШ!** Выпало {dice_value} (-{bet_amount:,} $).\nНовый баланс: {new_balance:,} $"

    await message.answer(msg, parse_mode="Markdown")

# =========================================================
# === 8. БИЗНЕС (ПОКУПКА/МЕНЮ) ===
# =========================================================

@dp.message(F.text == BTN_BUSINESS)
async def cmd_business(message: types.Message):
    u = await asyncio.to_thread(get_user, message.from_user.id)
    if u.is_banned: return
    
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    biz_list_text = "💼 **Доступные бизнесы:**\n"
    total_income = 0
    
    with Session() as s:
        user_biz = {b.business_id: b for b in s.query(OwnedBusiness).filter_by(user_id=u.telegram_id).all()}
        
    for biz_id, info in BUSINESSES.items():
        count = user_biz.get(biz_id, OwnedBusiness(count=0)).count
        
        # Общий доход (включая принадлежащие)
        total_income += info['income'] * count
        
        biz_list_text += (
            f"\n{info['name']}\n"
            f"  - Цена: **{info['cost']:,} $**\n"
            f"  - Доход/час: **{info['income']:,} $**\n"
            f"  - У тебя: **{count} шт.**"
        )
        
        # Кнопка покупки, если не куплено максимальное количество (можно ограничить позже)
        kb.inline_keyboard.append([
            InlineKeyboardButton(text=f"Купить {info['name']} ({info['cost']:,} $)", callback_data=f"buybiz_{biz_id}")
        ])

    msg = (
        f"💰 **Твой Бизнес-портфель**\n"
        f"Твой баланс: **{u.balance:,} $**\n"
        f"💸 **Общий доход в час:** {total_income:,} $ (выплаты ежечасно)\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{biz_list_text}"
    )
    
    await message.answer(msg, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data.startswith("buybiz_"))
async def buy_business(call: types.CallbackQuery):
    u = await asyncio.to_thread(get_user, call.from_user.id)
    if u.is_banned or (u.arrest_expires and u.arrest_expires > datetime.now()):
        return await call.answer("🚫 Вы не можете покупать бизнес.", show_alert=True)
        
    try:
        biz_id = int(call.data.split("_")[1])
        biz_info = BUSINESSES.get(biz_id)
        if not biz_info:
            return await call.answer("❌ Бизнес не найден.", show_alert=True)
            
        cost = biz_info['cost']
        
        if u.balance < cost:
            return await call.answer(f"❌ Недостаточно средств. Нужно {cost:,} $.", show_alert=True)
            
        with Session() as s:
            user = s.query(User).filter_by(telegram_id=u.telegram_id).first()
            
            # Снимаем деньги
            user.balance -= cost
            
            # Обновляем бизнес
            owned_biz = s.query(OwnedBusiness).filter_by(user_id=u.telegram_id, business_id=biz_id).first()
            if owned_biz:
                owned_biz.count += 1
            else:
                s.add(OwnedBusiness(user_id=u.telegram_id, business_id=biz_id, count=1))
            
            s.commit()
            
            await call.answer(f"✅ Куплено: {biz_info['name']}!", show_alert=True)
            
            # Обновление сообщения
            # Пересоздаем меню, чтобы показать обновленное количество
            await cmd_business(call.message)
            
    except Exception as e:
        logging.error(f"Ошибка при покупке бизнеса: {e}")
        await call.answer("Произошла ошибка при покупке.", show_alert=True)

# =========================================================
# === 9. ТОП ===
# =========================================================

@dp.message(F.text == BTN_TOP)
async def cmd_top(message: types.Message):
    with Session() as s:
        # Исключаем забаненных и берем топ-10 по балансу
        top_users = s.query(User).filter_by(is_banned=False) \
                    .order_by(User.balance.desc()).limit(10).all()

    text = "🏆 **ТОП-10 Богачей**\n"
    text += "━━━━━━━━━━━━━━━━━━\n"
    
    for i, u in enumerate(top_users, 1):
        # Эмодзи статуса
        emoji = "👤"
        if u.is_owner: emoji = "👑"
        elif u.is_president: emoji = "🦅"
        elif u.is_admin: emoji = "🛡"
        
        text += f"{i}. {emoji} **{u.username}** — {u.balance:,} $\n"
        
    await message.answer(text, parse_mode="Markdown")

# =========================================================
# === 10. ПОЛИТИКА И ВЫБОРЫ ===
# =========================================================

@dp.message(F.text == BTN_POLITICS)
async def cmd_politics(message: types.Message):
    with Session() as s:
        state = s.query(ElectionState).first()
        pres = s.query(User).filter_by(is_president=True).first()
        pres_name = pres.username if pres else "Отсутствует"
        
        current_tax = state.tax_rate if state else 0.05
        
        # Время до конца фазы
        time_left_text = ""
        if state.phase != "IDLE" and state.end_time:
            rem = state.end_time - datetime.now()
            if rem.total_seconds() > 0:
                hours = int(rem.total_seconds() // 3600)
                minutes = int((rem.total_seconds() % 3600) // 60)
                seconds = int(rem.total_seconds() % 60)
                time_left_text = f" (Осталось: {hours}ч {minutes}мин {seconds}сек)"

        text = (
            f"🏛 **ПОЛИТИКА**\n"
            f"🦅 **Президент:** {pres_name} (ID: `{pres.telegram_id}`)\n" if pres else f"🦅 **Президент:** Отсутствует\n"
            f"📉 **Налог:** {int(current_tax*100)}% (Макс: {int(MAX_TAX_RATE*100)}%)\n"
            f"📊 **Статус выборов:** **{state.phase}**{time_left_text}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
        )
        
        kb = InlineKeyboardMarkup(inline_keyboard=[])
        
        if state.phase == "CANDIDACY":
            text += "🟢 Идет набор кандидатов! Подай заявку!"
            kb.inline_keyboard.append([InlineKeyboardButton(text="📝 Подать заявку", callback_data="el_apply")])
            kb.inline_keyboard.append([InlineKeyboardButton(text="👀 Кандидаты", callback_data="el_show_cands")])
        elif state.phase == "VOTING":
            text += "🗳 Идет голосование! Выбери президента!"
            kb.inline_keyboard.append([InlineKeyboardButton(text="🗳 Голосовать", callback_data="el_vote_menu")])
        else:
            # Проверка на кулдаун выборов
            if datetime.now() - state.last_election_time < ELECTION_COOLDOWN:
                rem = ELECTION_COOLDOWN - (datetime.now() - state.last_election_time)
                days = int(rem.total_seconds() // (3600 * 24))
                hours = int((rem.total_seconds() % (3600 * 24)) // 3600)
                text += f"Выборы не проводятся. Кулдаун еще {days}д {hours}ч."
            else:
                 text += "Выборы пока не проводятся. Администрация может их объявить."
            
    await message.answer(text, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "el_apply")
async def election_apply(call: types.CallbackQuery):
    uid = call.from_user.id
    u = await asyncio.to_thread(get_user, uid)
    
    with Session() as s:
        user_db = s.query(User).filter_by(telegram_id=uid).first()
        # Проверка условий для кандидатуры (минимум 1 бизнес и 10,000$)
        if s.query(OwnedBusiness).filter_by(user_id=uid).count() < 1 or user_db.balance < 10000:
             return await call.answer("❌ Для участия нужен хотя бы 1 бизнес и баланс > 10,000 $.", show_alert=True)
             
        if s.query(Candidate).filter_by(user_id=uid).first():
            return await call.answer("Вы уже кандидат!", show_alert=True)
        
        s.add(Candidate(user_id=uid))
        s.commit()
    await call.answer("Заявка подана! Успехов!", show_alert=True)

@dp.callback_query(F.data == "el_show_cands")
async def election_show_cands(call: types.CallbackQuery):
    with Session() as s:
        cands = s.query(Candidate).all()
        if not cands:
            return await call.answer("Кандидатов пока нет.", show_alert=True)
            
        text = "📝 **Кандидаты в Президенты:**\n"
        for i, c in enumerate(cands, 1):
            u = s.query(User).filter_by(telegram_id=c.user_id).first()
            # Проверка, что пользователь есть в БД
            if u:
                 text += f"{i}. {u.username} (ID: `{u.telegram_id}`)\n"
            else:
                 text += f"{i}. Неизвестный пользователь (ID: `{c.user_id}`)\n"

        
        await call.message.answer(text, parse_mode="Markdown")

@dp.callback_query(F.data == "el_vote_menu")
async def election_vote_menu(call: types.CallbackQuery):
    voter_id = call.from_user.id
    
    with Session() as s:
        u = s.query(User).filter_by(telegram_id=voter_id).first()
        
        cands = s.query(Candidate).all()
        kb = InlineKeyboardMarkup(inline_keyboard=[])
        
        if not cands:
            return await call.message.edit_text("Кандидатов нет.")
            
        for c in cands:
            user_data = s.query(User).filter_by(telegram_id=c.user_id).first()
            # Проверка, что пользователь есть в БД
            if user_data:
                kb.inline_keyboard.append([InlineKeyboardButton(text=f"За {user_data.username}", callback_data=f"el_vote_{c.user_id}")])
            
    await call.message.edit_text("Выберите кандидата для голосования:", reply_markup=kb)

@dp.callback_query(F.data.startswith("el_vote_"))
async def election_do_vote(call: types.CallbackQuery):
    cand_id = int(call.data.split("_")[2])
    voter_id = call.from_user.id
    
    with Session() as s:
        # Проверка повторного голоса
        voter = s.query(User).filter_by(telegram_id=voter_id).first()
        state = s.query(ElectionState).first()
        
        # Если выборы запущены, голосовать можно только 1 раз за раунд
        if state.phase == "VOTING":
             # Проверяем, голосовал ли он с момента начала фазы голосования.
             # Это более точная проверка, чем просто 24 часа.
             if voter.last_vote_time and state.end_time and voter.last_vote_time > (state.end_time - ELECTION_DURATION_VOTING):
                 return await call.answer("Вы уже голосовали в этом раунде.", show_alert=True)

            
        cand = s.query(Candidate).filter_by(user_id=cand_id).first()
        if cand:
            cand.votes += 1
            voter.last_vote_time = datetime.now() # Отметка о голосовании
            s.commit()
            await call.answer("✅ Голос принят! Спасибо за участие.", show_alert=True)
            
            # Обновляем сообщение, чтобы убрать кнопки
            await call.message.edit_text("✅ Вы успешно проголосовали. Результаты будут объявлены по окончании раунда.")
        else:
            await call.answer("❌ Кандидат выбыл или не найден.", show_alert=True)


# =========================================================
# === 11. АДМИН КОМАНДЫ (REPLY) ===
# =========================================================

# --- 1. Выдача денег (Reply) ---
@dp.message(Command("give"))
async def cmd_give(message: types.Message, command: CommandObject):
    u = await asyncio.to_thread(get_user, message.from_user.id)
    if not u.is_admin and not u.is_owner: return

    if not message.reply_to_message:
        return await message.answer("❌ Нужно ответить на сообщение пользователя.")

    try:
        amount = int(command.args)
        if amount <= 0: raise ValueError
    except (ValueError, TypeError):
        return await message.answer("❌ Неверный синтаксис. Используйте: `/give [сумма > 0]`")
        
    target_id = message.reply_to_message.from_user.id
    target_username = message.reply_to_message.from_user.username
    sender_username = message.from_user.username

    with Session() as s:
        target_user = s.query(User).filter_by(telegram_id=target_id).first()
        
        if target_user:
            target_user.balance += amount
            s.commit()
            
            await message.answer(f"✅ Админ **{sender_username}** выдал **{amount:,} $** пользователю **{target_username}**.")
            try:
                await bot.send_message(target_id, f"🎉 **УВЕДОМЛЕНИЕ ОТ АДМИНА:**\nВам начислено **{amount:,} $**.")
            except: pass
        else:
            await message.answer(f"❌ Пользователь `{target_username}` не найден в базе данных.")

# --- 2. Арест (Reply) ---
@dp.message(Command("arrest"))
async def cmd_arrest(message: types.Message, command: CommandObject):
    u = await asyncio.to_thread(get_user, message.from_user.id)
    if not u.is_admin and not u.is_owner: return

    if not message.reply_to_message:
        return await message.answer("❌ Нужно ответить на сообщение пользователя.")

    try:
        args = command.args.split(maxsplit=1)
        minutes = int(args[0])
        reason = args[1] if len(args) > 1 else "Нарушение правил."
        if minutes <= 0: raise ValueError
    except (ValueError, TypeError, IndexError):
        return await message.answer("❌ Неверный синтаксис. Используйте: `/arrest [минуты] [причина]`")
        
    target_id = message.reply_to_message.from_user.id
    target_username = message.reply_to_message.from_user.username

    with Session() as s:
        target_user = s.query(User).filter_by(telegram_id=target_id).first()
        
        if target_user:
            target_user.arrest_expires = datetime.now() + timedelta(minutes=minutes)
            s.commit()
            
            msg_to_chat = f"🔒 Пользователь **{target_username}** арестован на **{minutes} минут**.\nПричина: *{reason}*."
            await message.answer(msg_to_chat)
            try:
                await bot.send_message(target_id, f"🚨 **ТЫ АРЕСТОВАН!** Срок: **{minutes} мин.**\nПричина: *{reason}*")
            except: pass
        else:
            await message.answer(f"❌ Пользователь `{target_username}` не найден в базе данных.")

# --- 3. Освобождение (Reply) ---
@dp.message(Command("release"))
async def cmd_release(message: types.Message):
    u = await asyncio.to_thread(get_user, message.from_user.id)
    if not u.is_admin and not u.is_owner: return

    if not message.reply_to_message:
        return await message.answer("❌ Нужно ответить на сообщение пользователя.")
        
    target_id = message.reply_to_message.from_user.id
    target_username = message.reply_to_message.from_user.username

    with Session() as s:
        target_user = s.query(User).filter_by(telegram_id=target_id).first()
        
        if target_user:
            if target_user.arrest_expires is None or target_user.arrest_expires <= datetime.now():
                return await message.answer(f"✅ Пользователь **{target_username}** уже на свободе.")
                
            target_user.arrest_expires = datetime.now() - timedelta(minutes=1) # Сразу истекает
            s.commit()
            
            await message.answer(f"🔓 Пользователь **{target_username}** немедленно освобожден по решению администрации.")
            try:
                await bot.send_message(target_id, f"🎉 **ТЫ СВОБОДЕН!** Администрация освободила тебя досрочно.")
            except: pass
        else:
            await message.answer(f"❌ Пользователь `{target_username}` не найден в базе данных.")

# =========================================================
# === 12. АДМИН ПАНЕЛЬ И УПРАВЛЕНИЕ (КОЛБЭКИ) ===
# =========================================================

@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    u = await asyncio.to_thread(get_user, message.from_user.id)
    if not u.is_admin and not u.is_owner: return # Только для админов
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 Выдать деньги (ID)", callback_data="adm_give")],
        [InlineKeyboardButton(text="🔒 Арестовать (ID)", callback_data="adm_arrest"),
         InlineKeyboardButton(text="🔓 Освободить (Reply)", callback_data="adm_release")],
        [InlineKeyboardButton(text="🗳 Начать выборы", callback_data="adm_start_el")],
        [InlineKeyboardButton(text="➡️ Начать голосование", callback_data="adm_start_vote")],
        [InlineKeyboardButton(text="🏁 Завершить выборы", callback_data="adm_end_el")],
        [InlineKeyboardButton(text="📉 Изменить налог", callback_data="adm_tax")]
    ])
    await message.answer("🛠 **Админ Панель**", reply_markup=kb, parse_mode="Markdown")

# --- ОБРАБОТЧИКИ КОЛБЭКОВ ДЛЯ АДМИН-ПАНЕЛИ ---

# 1. Выдача денег по ID (ШАГ 1: Ввод ID)
@dp.callback_query(F.data == "adm_give")
async def adm_start_give(call: types.CallbackQuery, state: FSMContext):
    u = await asyncio.to_thread(get_user, call.from_user.id)
    if not u.is_admin and not u.is_owner: return await call.answer("🚫 Нет прав.", show_alert=True)
    
    await call.message.edit_text("💸 **Выдача денег:** Введите Telegram ID пользователя (или 'отмена'):")
    await state.set_state(AdminState.give_target_id)
    await call.answer()

@dp.message(AdminState.give_target_id)
async def adm_input_give_id(message: types.Message, state: FSMContext):
    if message.text.lower() == 'отмена':
        await state.clear()
        return await message.answer("❌ Отменено.")
        
    try:
        target_id = int(message.text)
        await state.update_data(target_id=target_id)
        await message.answer("✅ ID принят. Введите сумму (целое положительное число):")
        await state.set_state(AdminState.give_amount_input)
    except ValueError:
        await message.answer("❌ Неверный формат ID. Введите целое число.")
        return

@dp.message(AdminState.give_amount_input)
async def adm_input_give_amount(message: types.Message, state: FSMContext):
    if message.text.lower() == 'отмена':
        await state.clear()
        return await message.answer("❌ Отменено.")
        
    try:
        amount = int(message.text)
        if amount <= 0: raise ValueError
    except ValueError:
        return await message.answer("❌ Неверный формат суммы. Введите целое положительное число.")
        
    data = await state.get_data()
    target_id = data.get('target_id')
    sender_username = message.from_user.username
    
    with Session() as s:
        target_user = s.query(User).filter_by(telegram_id=target_id).first()
        
        if target_user:
            target_user.balance += amount
            s.commit()
            
            await message.answer(f"✅ **УСПЕХ!** Админ **{sender_username}** выдал **{amount:,} $** пользователю **{target_user.username}** (ID: `{target_id}`).")
            try:
                await bot.send_message(target_id, f"🎉 **УВЕДОМЛЕНИЕ ОТ АДМИНА:**\nВам начислено **{amount:,} $**.")
            except: pass
        else:
            await message.answer(f"❌ Пользователь с ID `{target_id}` не найден в базе данных.")
            
    await state.clear()


# 2. Арест по ID (ШАГ 1: Ввод ID)
@dp.callback_query(F.data == "adm_arrest")
async def adm_start_arrest(call: types.CallbackQuery, state: FSMContext):
    u = await asyncio.to_thread(get_user, call.from_user.id)
    if not u.is_admin and not u.is_owner: return await call.answer("🚫 Нет прав.", show_alert=True)
    
    await call.message.edit_text("🔒 **Арест:** Введите Telegram ID пользователя (или 'отмена'):")
    await state.set_state(AdminState.arrest_target_id)
    await call.answer()

@dp.message(AdminState.arrest_target_id)
async def adm_input_arrest_id(message: types.Message, state: FSMContext):
    if message.text.lower() == 'отмена':
        await state.clear()
        return await message.answer("❌ Отменено.")
        
    try:
        target_id = int(message.text)
        await state.update_data(target_id=target_id)
        await message.answer("✅ ID принят. Введите время ареста в **минутах** и **причину** (например, `60 Чит`):")
        await state.set_state(AdminState.arrest_time_reason)
    except ValueError:
        await message.answer("❌ Неверный формат ID. Введите целое число.")
        return

@dp.message(AdminState.arrest_time_reason)
async def adm_input_arrest_time_reason(message: types.Message, state: FSMContext):
    if message.text.lower() == 'отмена':
        await state.clear()
        return await message.answer("❌ Отменено.")
        
    try:
        args = message.text.split(maxsplit=1)
        minutes = int(args[0])
        reason = args[1] if len(args) > 1 else "Нарушение правил."
        if minutes <= 0: raise ValueError
    except (ValueError, IndexError):
        return await message.answer("❌ Неверный формат. Используйте: `[минуты] [причина]` (например, `60 Чит`)")
        
    data = await state.get_data()
    target_id = data.get('target_id')
    
    with Session() as s:
        target_user = s.query(User).filter_by(telegram_id=target_id).first()
        
        if target_user:
            target_user.arrest_expires = datetime.now() + timedelta(minutes=minutes)
            s.commit()
            
            msg_to_chat = f"✅ **УСПЕХ!** Пользователь **{target_user.username}** (ID: `{target_id}`) арестован на **{minutes} минут**.\nПричина: *{reason}*."
            await message.answer(msg_to_chat)
            try:
                await bot.send_message(target_id, f"🚨 **ТЫ АРЕСТОВАН!** Срок: **{minutes} мин.**\nПричина: *{reason}*")
            except: pass
        else:
            await message.answer(f"❌ Пользователь с ID `{target_id}` не найден в базе данных.")
            
    await state.clear()

# 3. Управление налогами
@dp.callback_query(F.data == "adm_tax")
async def adm_start_tax_change(call: types.CallbackQuery, state: FSMContext):
    u = await asyncio.to_thread(get_user, call.from_user.id)
    if not u.is_admin and not u.is_owner: return await call.answer("🚫 Нет прав.", show_alert=True)
    
    with Session() as s:
        current_tax = s.query(ElectionState).first().tax_rate * 100
    
    await call.message.edit_text(
        f"📉 **Изменение налога:** Введите новую ставку налога в процентах (0-20). Текущая: **{int(current_tax)}%** (или 'отмена'):"
    )
    await state.set_state(AdminState.tax_rate)
    await call.answer()

@dp.message(AdminState.tax_rate)
async def adm_input_tax_rate(message: types.Message, state: FSMContext):
    if message.text.lower() == 'отмена':
        await state.clear()
        return await message.answer("❌ Отменено.")
        
    try:
        rate_percent = int(message.text)
        if not (0 <= rate_percent <= int(MAX_TAX_RATE * 100)):
            raise ValueError
    except ValueError:
        return await message.answer(f"❌ Неверный формат. Введите целое число от 0 до {int(MAX_TAX_RATE*100)}.")

    new_rate = rate_percent / 100.0
    
    with Session() as s:
        state_db = s.query(ElectionState).first()
        state_db.tax_rate = new_rate
        s.commit()
        
    await message.answer(f"✅ **УСПЕХ!** Новая ставка налога установлена на **{rate_percent}%**.")
    await state.clear()


# 4. Управление выборами (Колбэки)

@dp.callback_query(F.data == "adm_start_el")
async def adm_start_election(call: types.CallbackQuery):
    u = await asyncio.to_thread(get_user, call.from_user.id)
    if not u.is_admin and not u.is_owner: return await call.answer("🚫 Нет прав.", show_alert=True)
    
    with Session() as s:
        state = s.query(ElectionState).first()
        if state.phase != "IDLE":
            return await call.answer(f"❌ Выборы уже идут (Фаза: {state.phase}).", show_alert=True)
            
        if datetime.now() - state.last_election_time < ELECTION_COOLDOWN:
            rem = ELECTION_COOLDOWN - (datetime.now() - state.last_election_time)
            hours = int(rem.total_seconds() // 3600)
            return await call.answer(f"❌ Кулдаун. Следующие выборы можно начать через {hours}ч.", show_alert=True)
            
        # Начинаем фазу КАНДИДАТСТВА
        s.query(Candidate).delete() # Очищаем прошлых кандидатов
        state.phase = "CANDIDACY"
        state.end_time = datetime.now() + ELECTION_DURATION_CANDIDACY
        s.commit()
        
        message = (
            "🗳 **НАЧАЛО ВЫБОРОВ!**\n"
            "Объявлен набор кандидатов в Президенты!\n"
            f"Подать заявку можно до {state.end_time.strftime('%H:%M:%S')} МСК."
        )
        await broadcast_message_to_chats(bot, message)
        
    await call.answer("✅ Фаза Кандидатства запущена.", show_alert=True)

@dp.callback_query(F.data == "adm_start_vote")
async def adm_start_voting(call: types.CallbackQuery):
    u = await asyncio.to_thread(get_user, call.from_user.id)
    if not u.is_admin and not u.is_owner: return await call.answer("🚫 Нет прав.", show_alert=True)
    
    with Session() as s:
        state = s.query(ElectionState).first()
        if state.phase == "VOTING":
            return await call.answer("❌ Голосование уже идет.", show_alert=True)
        if state.phase == "IDLE":
            return await call.answer("❌ Сначала нужно начать выборы (Кандидатство).", show_alert=True)
            
        # Переход к ГОЛОСОВАНИЮ (принудительно)
        state.phase = "VOTING"
        state.end_time = datetime.now() + ELECTION_DURATION_VOTING
        s.commit()
        
        message = (
            "➡️ **НАЧАЛО ГОЛОСОВАНИЯ! (Принудительно)**\n"
            "Прием заявок завершен. Выберите Президента!\n"
            f"Голосование продлится до {state.end_time.strftime('%H:%M:%S')} МСК."
        )
        await broadcast_message_to_chats(bot, message)
        
    await call.answer("✅ Голосование запущено.", show_alert=True)


@dp.callback_query(F.data == "adm_end_el")
async def adm_end_election(call: types.CallbackQuery):
    u = await asyncio.to_thread(get_user, call.from_user.id)
    if not u.is_admin and not u.is_owner: return await call.answer("🚫 Нет прав.", show_alert=True)
    
    with Session() as s:
        state = s.query(ElectionState).first()
        if state.phase == "IDLE":
            return await call.answer("❌ Выборы не проводятся.", show_alert=True)
        
        # Принудительное завершение
        await end_elections_logic(s, bot)
    
    await call.answer("✅ Выборы завершены и подведены итоги.", show_alert=True)
    
@dp.callback_query(F.data == "adm_release")
async def adm_release_info(call: types.CallbackQuery):
    u = await asyncio.to_thread(get_user, call.from_user.id)
    if not u.is_admin and not u.is_owner: return await call.answer("🚫 Нет прав.", show_alert=True)

    await call.answer("Используйте команду /release, ответив на сообщение арестованного игрока!", show_alert=True)


# =========================================================
# === 13. ЗАПУСК БОТА ===
# =========================================================

async def main():
    # 1. Инициализация БД
    if not init_db():
        logging.error("Критическая ошибка: Не удалось инициализировать базу данных. Выход.")
        return

    # 2. Установка команд
    await set_bot_commands(bot)
    
    # 3. Настройка и запуск планировщика
    # Выплата дохода раз в час
    scheduler.add_job(
        business_payout,
        trigger='interval',
        seconds=BUSINESS_PAYOUT_INTERVAL,
        kwargs={'bot': bot},
        id="hourly_payout"
    )
    
    # Проверка завершения выборов раз в минуту
    scheduler.add_job(
        check_election_end,
        trigger='interval',
        minutes=1,
        kwargs={'bot': bot},
        id="election_check"
    )

    scheduler.start()
    logging.info("🚀 Планировщик запущен.")

    # 4. Запуск поллинга
    logging.info("🚀 Бот запущен и готов к работе!")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close() # Закрываем сессию бота

if __name__ == "__main__":
    try:
        # Для корректного завершения процесса при прерывании
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Bot stopped by user (Ctrl+C).")
    except Exception as e:
        logging.critical(f"An unexpected critical error occurred: {e}", exc_info=True)
