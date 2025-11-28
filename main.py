import os
import logging
import random
import asyncio
from datetime import datetime, timedelta

from sqlalchemy import create_engine, Column, Integer, String, BigInteger, Boolean, DateTime, Float, ForeignKey, text
from sqlalchemy.orm import sessionmaker, declarative_base, relationship, selectinload
from sqlalchemy.exc import SQLAlchemyError

from aiogram import Bot, Dispatcher, types, F
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
    DB_PATH = "sqlite:///data/bongobot.db" # Локальный фоллбэк

# Игровой Баланс
WORK_COOLDOWN = timedelta(hours=4)     # Работать можно раз в 4 часа
BUSINESS_PAYOUT_INTERVAL = 3600        # Выплата с бизнеса раз в час (секунды)
MAX_TAX_RATE = 0.20                    # Максимальный налог 20% (чтобы не было бунта)

# Бизнесы (ID, Название, Цена, Доход/час)
BUSINESSES = {
    1: {"name": "🌯 Ларек с шаурмой", "cost": 5_000, "income": 200},
    2: {"name": "🚕 Служба Такси", "cost": 25_000, "income": 800},
    3: {"name": "☕ Кофейня 'Sova'", "cost": 75_000, "income": 2_500},
    4: {"name": "⛽ Заправка Oil", "cost": 250_000, "income": 7_000},
    5: {"name": "💎 Ювелирный Бутик", "cost": 1_000_000, "income": 30_000},
}

# Выборы
ELECTION_DURATION_CANDIDACY = timedelta(minutes=30) # Длительность набора
ELECTION_DURATION_VOTING = timedelta(minutes=60)    # Длительность голосования
ELECTION_COOLDOWN = timedelta(days=1)               # Как часто можно проводить

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
    is_admin = Column(Boolean, default=False)  # Админ бота
    is_owner = Column(Boolean, default=False)  # Владелец (Создатель)
    is_president = Column(Boolean, default=False) # Президент игры
    
    # Наказания
    is_banned = Column(Boolean, default=False) # Бан (нет доступа к боту)
    arrest_expires = Column(DateTime, nullable=True) # Время окончания ареста

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
        with Session() as session:
            state = session.query(ElectionState).first()
            if not state:
                session.add(ElectionState())
                session.commit()
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
            s.refresh(u)
        else:
            # Обновляем юзернейм, если сменился
            if username and u.username != username:
                u.username = username
                s.commit()
        
        # Прогрев атрибутов для избежания DetachedInstanceError
        _ = u.balance
        _ = u.is_banned
        _ = u.arrest_expires
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

bot = Bot(token=BOT_TOKEN, parse_mode="Markdown")
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
        # Используем try/except, потому что бот мог быть удален из чата
        try:
            # Отправка обычного сообщения. Telegram сам решит, присылать уведомление или нет.
            await bot.send_message(
                chat_id,
                message_text,
                parse_mode="Markdown"
                # disable_notification=False is default
            )
            success_count += 1
            await asyncio.sleep(0.05) # Задержка для предотвращения флуда
        except TelegramAPIError as e:
            logging.warning(f"Не удалось отправить сообщение в чат {chat_id}: {e}")
        except Exception as e:
            logging.error(f"Непредвиденная ошибка при рассылке в чат {chat_id}: {e}")

    logging.info(f"Рассылка завершена. Успешно отправлено в {success_count} чатов из {len(chat_ids)}.")

# =========================================================
# === 5. ЭКОНОМИКА И ПЛАНИРОВЩИК ===
# =========================================================

async def business_payout():
    """Начисление дохода раз в час (запускается планировщиком)."""
    logging.info("Выплата доходов от бизнеса...")
    with Session() as s:
        all_biz = s.query(OwnedBusiness).all()
        state = s.query(ElectionState).first()
        tax = state.tax_rate
        
        payouts = {} # user_id: income
        
        for ob in all_biz:
            info = BUSINESSES.get(ob.business_id)
            if info:
                gross_income = info['income'] * ob.count
                tax_cut = int(gross_income * tax)
                net_income = gross_income - tax_cut
                
                # Налог президенту
                pres = s.query(User).filter_by(is_president=True).first()
                # Налог платится, только если президент не сам владелец бизнеса
                if pres and pres.telegram_id != ob.user_id:
                    pres.balance += tax_cut
                
                payouts[ob.user_id] = payouts.get(ob.user_id, 0) + net_income
        
        # Зачисление
        for uid, amount in payouts.items():
            u = s.query(User).filter_by(telegram_id=uid).first()
            # Проверка, что игрок не забанен и не арестован
            if u and not u.is_banned and (u.arrest_expires is None or u.arrest_expires < datetime.now()):
                u.balance += amount
                # Пытаемся уведомить
                try:
                    # Важно использовать parse_mode="Markdown"
                    await bot.send_message(uid, f"💼 **Бизнес-доход:** +{amount:,} $\n(Налог {int(tax*100)}% уплачен в Казну)")
                except: pass
        s.commit()

# =========================================================
# === 6. ХЕНДЛЕРЫ: ОСНОВНОЕ ===
# =========================================================

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    # Сохраняем чат
    with Session() as s:
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
        if pres and pres.telegram_id != user.telegram_id:
            pres.balance += tax
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
async def cmd_casino(message: types.Message, state: FSMContext):
    u = await asyncio.to_thread(get_user, message.from_user.id)
    if u.arrest_expires and u.arrest_expires > datetime.now():
        return await message.answer("🔒 В тюрьме азартные игры запрещены!")
        
    await message.answer("🎰 Введите сумму ставки (или 'отмена'):")
    await state.set_state(CasinoState.bet)

@dp.message(CasinoState.bet)
async def process_bet(message: types.Message, state: FSMContext):
    if message.text.lower() == 'отмена':
        await state.clear()
        return await message.answer("Казино закрыто.")
        
    try:
        bet = int(message.text)
        if bet <= 0: raise ValueError
    except:
        return await message.answer("❌ Введите целое положительное число!")

    u = await asyncio.to_thread(get_user, message.from_user.id)
    if u.balance < bet:
        return await message.answer(f"❌ Недостаточно средств! У тебя {u.balance:,} $.")

    # Игра: шанс выигрыша 45%
    win = random.random() < 0.45
    
    with Session() as s:
        user = s.query(User).filter_by(telegram_id=u.telegram_id).first()
        if win:
            # Выигрыш: x2 от ставки
            user.balance += bet
            res_text = f"🎉 **ПОБЕДА!** Выпало счастливое число!\n➕ {bet:,} $"
        else:
            user.balance -= bet
            res_text = f"💀 **ПРОИГРЫШ.** Удача отвернулась.\n➖ {bet:,} $"
        s.commit()
        
    await state.clear()
    await message.answer(
        f"{res_text}\n"
        f"Новый баланс: **{user.balance:,} $**",
        parse_mode="Markdown"
    )

# =========================================================
# === 8. БИЗНЕСЫ ===
# =========================================================

@dp.message(F.text == BTN_BUSINESS)
async def cmd_business(message: types.Message):
    text = "🏢 **Каталог Бизнесов:**\n\n"
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    
    for bid, b in BUSINESSES.items():
        text += (
            f"🔹 **{b['name']}**\n"
            f"   💰 Цена: {b['cost']:,} $\n"
            f"   💸 Доход: {b['income']:,} $/час\n\n"
        )
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"Купить: {b['name']} ({b['cost']:,} $)", callback_data=f"buybiz_{bid}")])
    
    await message.answer(text, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data.startswith("buybiz_"))
async def buy_biz_cb(call: types.CallbackQuery):
    bid = int(call.data.split("_")[1])
    info = BUSINESSES[bid]
    uid = call.from_user.id
    
    u = await asyncio.to_thread(get_user, uid)
    if u.arrest_expires and u.arrest_expires > datetime.now():
        return await call.answer("🔒 Тюрьма не место для сделок!", show_alert=True)

    if u.balance < info['cost']:
        return await call.answer(f"❌ Не хватает денег! Требуется {info['cost']:,} $.", show_alert=True)
    
    new_balance = u.balance - info['cost']
    
    with Session() as s:
        user = s.query(User).filter_by(telegram_id=uid).first()
        user.balance -= info['cost']
        
        exist = s.query(OwnedBusiness).filter_by(user_id=uid, business_id=bid).first()
        if exist: exist.count += 1
        else: s.add(OwnedBusiness(user_id=uid, business_id=bid, count=1))
        s.commit()
        
    await call.answer(f"✅ Вы успешно купили {info['name']}!", show_alert=True)
    await call.message.edit_text(
        f"✅ **Покупка совершена!**\n"
        f"Вы купили **{info['name']}**.\n"
        f"Новый баланс: **{new_balance:,} $**.",
        parse_mode="Markdown"
    )

# =========================================================
# === 9. ТОП ИГРОКОВ ===
# =========================================================

@dp.message(F.text == BTN_TOP)
async def cmd_top(message: types.Message):
    with Session() as s:
        # Топ по балансу
        users = s.query(User).order_by(User.balance.desc()).limit(10).all()
        
    text = "🏆 **ТОП 10 БОГАЧЕЙ** 🏆\n━━━━━━━━━━━━━━━━━━\n"
    for i, u in enumerate(users, 1):
        medal = "🥇" if i==1 else "🥈" if i==2 else "🥉" if i==3 else "🔸"
        role = "👑" if u.is_owner else "🦅" if u.is_president else ""
        text += f"{i}. {medal} {u.username} {role} — **{u.balance:,} $**\n"
        
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
        
        text = (
            f"🏛 **ПОЛИТИКА**\n"
            f"🦅 **Президент:** {pres_name} (ID: `{pres.telegram_id}`)\n" if pres else f"🦅 **Президент:** Отсутствует\n"
            f"📉 **Налог:** {int(state.tax_rate*100)}% (Макс: {int(MAX_TAX_RATE*100)}%)\n"
            f"📊 **Статус выборов:** **{state.phase}**\n"
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
            text += "Выборы пока не проводятся. Администрация может их объявить."
            
    await message.answer(text, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "el_apply")
async def election_apply(call: types.CallbackQuery):
    uid = call.from_user.id
    u = await asyncio.to_thread(get_user, uid)
    
    # Требования для кандидата: хотя бы 1 бизнес и баланс > 10000
    with Session() as s:
        if s.query(OwnedBusiness).filter_by(user_id=uid).count() < 1 or u.balance < 10000:
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
            text += f"{i}. {u.username} (ID: `{u.telegram_id}`)\n"
        
        await call.message.answer(text, parse_mode="Markdown")

@dp.callback_query(F.data == "el_vote_menu")
async def election_vote_menu(call: types.CallbackQuery):
    voter_id = call.from_user.id
    
    with Session() as s:
        u = s.query(User).filter_by(telegram_id=voter_id).first()
        if u.last_vote_time and datetime.now() - u.last_vote_time < timedelta(hours=24):
            return await call.answer("Вы уже голосовали в этом раунде.", show_alert=True)
            
        cands = s.query(Candidate).all()
        kb = InlineKeyboardMarkup(inline_keyboard=[])
        
        if not cands:
            return await call.message.edit_text("Кандидатов нет.")
            
        for c in cands:
            user_data = s.query(User).filter_by(telegram_id=c.user_id).first()
            kb.inline_keyboard.append([InlineKeyboardButton(text=f"За {user_data.username}", callback_data=f"el_vote_{c.user_id}")])
            
    await call.message.edit_text("Выберите кандидата для голосования:", reply_markup=kb)

@dp.callback_query(F.data.startswith("el_vote_"))
async def election_do_vote(call: types.CallbackQuery):
    cand_id = int(call.data.split("_")[2])
    voter_id = call.from_user.id
    
    with Session() as s:
        # Проверка повторного голоса
        voter = s.query(User).filter_by(telegram_id=voter_id).first()
        if voter.last_vote_time and datetime.now() - voter.last_vote_time < timedelta(hours=24):
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
# === 11. АДМИН ПАНЕЛЬ И УПРАВЛЕНИЕ ===
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

# --- Новые команды на ответ (Reply) с уведомлениями ---

@dp.message(Command("give"), F.reply_to_message)
async def cmd_give_money_reply(message: types.Message, command: CommandObject):
    """Выдача денег по ответу на сообщение."""
    sender = await asyncio.to_thread(get_user, message.from_user.id)
    if not sender.is_admin and not sender.is_owner:
        return await message.reply("🚫 **Нет прав.** Только для администраторов.")

    target_msg = message.reply_to_message
    if not target_msg.from_user:
        return await message.reply("❌ Нельзя выдать деньги этому объекту (например, каналу).")

    try:
        if command.args is None:
            raise ValueError("Нет суммы")
        # Берем только первый аргумент как сумму
        amount = int(command.args.split()[0])
        if amount <= 0:
            raise ValueError("Сумма должна быть положительной")
    except ValueError:
        return await message.reply("❌ **Неверный формат.** Используйте: `/give [сумма]`, ответив на сообщение игрока.")

    target_id = target_msg.from_user.id
    target_username = target_msg.from_user.username
    
    target_user = await asyncio.to_thread(get_user, target_id, target_username)
    
    # Обновление баланса
    with Session() as s:
        u = s.query(User).filter_by(telegram_id=target_id).first()
        if u:
            u.balance += amount
            s.commit()
            
            # 1. Уведомление в чате (Подтверждение действия)
            await message.reply(
                f"✅ **УСПЕХ!** Админ **{sender.username}** выдал "
                f"**{amount:,} $** пользователю **{target_user.username}**."
            )
            
            # 2. Уведомление в ЛС (Приватное уведомление)
            try:
                await bot.send_message(
                    target_id,
                    f"🎉 **УВЕДОМЛЕНИЕ ОТ АДМИНА:**\n"
                    f"Вам начислено **{amount:,} $**."
                )
            except:
                logging.warning(f"Не удалось отправить ЛС пользователю {target_id}.")
                pass
        else:
            await message.reply("❌ Игрок не найден в базе данных.")


@dp.message(Command("arrest"), F.reply_to_message)
async def cmd_arrest_reply(message: types.Message, command: CommandObject):
    """Арест по ответу на сообщение."""
    sender = await asyncio.to_thread(get_user, message.from_user.id)
    if not sender.is_admin and not sender.is_owner:
        return await message.reply("🚫 **Нет прав.**")

    target_msg = message.reply_to_message
    if not target_msg.from_user:
        return await message.reply("❌ Нельзя арестовать этот объект.")
        
    if command.args is None:
        return await message.reply("❌ **Неверный формат.** Используйте: `/arrest [минуты] [причина]`, ответив на сообщение игрока.")
        
    args = command.args.split(maxsplit=1)
    
    try:
        mins = int(args[0])
        reason = args[1] if len(args) > 1 else "Не указана"
        if mins <= 0: raise ValueError
    except:
        return await message.reply("❌ **Неверный формат.** Первым аргументом должно быть число минут.")

    target_id = target_msg.from_user.id
    target_username = target_msg.from_user.username
    
    # Обновление ареста
    with Session() as s:
        u = s.query(User).filter_by(telegram_id=target_id).first()
        if u:
            u.arrest_expires = datetime.now() + timedelta(minutes=mins)
            s.commit()
            
            # 1. Уведомление в чате
            await message.reply(
                f"🚨 Игрок **{target_username}** арестован "
                f"на **{mins} мин.** (Причина: {reason})."
            )
            
            # 2. Уведомление в ЛС
            try:
                await bot.send_message(
                    target_id,
                    f"👮 **ВАС АРЕСТОВАЛИ!**\n"
                    f"Срок: **{mins} мин.**\n"
                    f"Причина: **{reason}**"
                )
            except: pass
        else:
            await message.reply("❌ Игрок не найден в базе данных.")


@dp.message(Command("release"), F.reply_to_message)
async def cmd_release_reply(message: types.Message):
    """Освобождение по ответу на сообщение."""
    sender = await asyncio.to_thread(get_user, message.from_user.id)
    if not sender.is_admin and not sender.is_owner:
        return await message.reply("🚫 **Нет прав.**")

    target_msg = message.reply_to_message
    if not target_msg.from_user:
        return await message.reply("❌ Нельзя освободить этот объект.")

    target_id = target_msg.from_user.id
    target_username = target_msg.from_user.username
    
    # Освобождение
    with Session() as s:
        u = s.query(User).filter_by(telegram_id=target_id).first()
        if u:
            if u.arrest_expires and u.arrest_expires > datetime.now():
                u.arrest_expires = datetime.now() # Немедленное освобождение
                s.commit()
                
                # 1. Уведомление в чате
                await message.reply(f"✅ Игрок **{target_username}** освобожден **АДМИНИСТРАЦИЕЙ**.")
                
                # 2. Уведомление в ЛС
                try:
                    await bot.send_message(target_id, f"🎉 **ВЫ СВОБОДНЫ!** Администрация объявила амнистию.")
                except: pass
            else:
                await message.reply("❌ Игрок не находится под арестом.")
        else:
            await message.reply("❌ Игрок не найден в базе данных.")

# --- Арест/Освобождение (callback fix) ---

@dp.callback_query(F.data == "adm_release")
async def adm_release_callback(call: types.CallbackQuery):
    """Информирование админа о новой команде /release"""
    await call.answer("Используйте команду /release в чате, ответив на сообщение игрока!", show_alert=True)
    await call.message.answer(
        "🔓 **Освобождение:** Чтобы быстро освободить игрока, ответьте на его сообщение командой `/release`."
    )
    
# --- Арест/Освобождение (Старый Flow - через ID) ---
# Оставлен, так как может использоваться для других целей

@dp.callback_query(F.data == "adm_arrest")
async def adm_arrest_start(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer("Введите ID игрока для ареста:")
    await state.set_state(AdminState.arrest_id)

@dp.message(AdminState.arrest_id)
async def adm_arrest_id(message: types.Message, state: FSMContext):
    try:
        uid = int(message.text.strip())
        await state.update_data(id=uid)
        await message.answer("На сколько **минут** арестовать?")
        await state.set_state(AdminState.arrest_time)
    except:
        await message.answer("❌ ID должен быть числом. Попробуйте снова.")

@dp.message(AdminState.arrest_time)
async def adm_arrest_finish(message: types.Message, state: FSMContext):
    data = await state.get_data()
    
    try:
        mins = int(message.text.strip())
        uid = data['id']
        
        with Session() as s:
            u = s.query(User).filter_by(telegram_id=uid).first()
            if u:
                u.arrest_expires = datetime.now() + timedelta(minutes=mins)
                s.commit()
                await message.answer(f"✅ Игрок `{uid}` арестован на {mins} мин.")
                try: await bot.send_message(uid, f"👮 **ВАС АРЕСТОВАЛИ!** Срок: **{mins} мин.**")
                except: pass
            else:
                await message.answer("Игрок не найден.")
    except:
        await message.answer("❌ Количество минут должно быть числом. Начните с `/admin` снова.")
        
    await state.clear()

# --- Управление Деньгами (Старый Flow - через ID) ---

@dp.callback_query(F.data == "adm_give")
async def adm_give_start(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer("Введите ID игрока и сумму через пробел (напр: 12345 10000):")
    await state.set_state(AdminState.give_id)

@dp.message(AdminState.give_id)
async def adm_give_exec(message: types.Message, state: FSMContext):
    # Добавлена проверка и уведомления в этом старом flow
    try:
        uid, amount = map(int, message.text.split())
        with Session() as s:
            u = s.query(User).filter_by(telegram_id=uid).first()
            if u:
                sender = await asyncio.to_thread(get_user, message.from_user.id)
                u.balance += amount
                s.commit()
                
                # Уведомление в чате (для админа)
                await message.answer(f"✅ Выдано **{amount:,} $** игроку `{uid}`. Новый баланс: {u.balance:,} $")
                
                # Уведомление в ЛС
                try:
                    await bot.send_message(uid, f"💸 **АДМИН {sender.username} ВЫДАЛ** вам **{amount:,} $**!")
                except: pass
            else:
                await message.answer("Игрок не найден.")
    except:
        await message.answer("❌ Ошибка формата. Попробуйте снова.")
    await state.clear()
    
# --- Управление Налогом ---

@dp.callback_query(F.data == "adm_tax")
async def adm_tax_start(call: types.CallbackQuery, state: FSMContext):
    current_tax = await asyncio.to_thread(get_tax_rate)
    await call.message.answer(
        f"Текущий налог: {int(current_tax*100)}%. "
        f"Максимальный: {int(MAX_TAX_RATE*100)}%.\n"
        f"Введите новую ставку налога (число от 0 до {int(MAX_TAX_RATE*100)}):"
    )
    await state.set_state(AdminState.tax_rate)

@dp.message(AdminState.tax_rate)
async def adm_tax_set(message: types.Message, state: FSMContext):
    try:
        new_rate_percent = int(message.text.strip())
        new_rate_float = new_rate_percent / 100.0
        
        if not (0 <= new_rate_float <= MAX_TAX_RATE):
            await message.answer(f"❌ Ставка должна быть между 0% и {int(MAX_TAX_RATE*100)}%.")
            return
            
        with Session() as s:
            st = s.query(ElectionState).first()
            st.tax_rate = new_rate_float
            s.commit()
            
            # Уведомление о действии
            await message.answer(f"✅ Налог успешно изменен на **{new_rate_percent}%**.")
            
    except:
        await message.answer("❌ Введите корректное число.")
    finally:
        await state.clear()


# --- Управление Выборами с рассылкой уведомлений ---

@dp.callback_query(F.data == "adm_start_el")
async def adm_start_el(call: types.CallbackQuery):
    with Session() as s:
        st = s.query(ElectionState).first()
        st.phase = "CANDIDACY"
        s.query(Candidate).delete() # Сброс старых кандидатов
        s.commit()
    
    # --- НОВОЕ: Рассылка уведомления по всем чатам ---
    await broadcast_message_to_chats(
        call.bot,
        f"📢 **НАБОР КАНДИДАТОВ ОТКРЫТ!**\n"
        f"Начались выборы президента. Успейте подать заявку через меню 'Политика'!"
    )
    
    await call.answer("Набор кандидатов открыт!", show_alert=True)
    await call.message.edit_text("✅ **Набор кандидатов открыт!** (Уведомления разосланы)")

@dp.callback_query(F.data == "adm_start_vote")
async def adm_start_vote(call: types.CallbackQuery):
    with Session() as s:
        if s.query(Candidate).count() == 0:
            return await call.answer("❌ Нельзя начать голосование, нет кандидатов.", show_alert=True)
            
        st = s.query(ElectionState).first()
        st.phase = "VOTING"
        s.commit()
        
    # --- НОВОЕ: Рассылка уведомления по всем чатам ---
    await broadcast_message_to_chats(
        call.bot,
        f"🗳️ **ГОЛОСОВАНИЕ НАЧАЛОСЬ!**\n"
        f"Отдайте свой голос за кандидата через меню 'Политика'."
    )
    
    await call.answer("Голосование началось!", show_alert=True)
    await call.message.edit_text("✅ **ГОЛОСОВАНИЕ НАЧАЛОСЬ!** (Уведомления разосланы)")

@dp.callback_query(F.data == "adm_end_el")
async def adm_end_el(call: types.CallbackQuery):
    winner_name = "Никто"
    winner_id = None
    
    with Session() as s:
        # Считаем голоса
        winner = s.query(Candidate).order_by(Candidate.votes.desc()).first()
        
        # 1. Снимаем старого президента
        s.query(User).filter_by(is_president=True).update({User.is_president: False})
        
        if winner:
            winner_user = s.query(User).filter_by(telegram_id=winner.user_id).first()
            
            # 2. Назначаем нового
            winner_user.is_president = True
            winner_name = winner_user.username
            winner_id = winner_user.telegram_id
        
        # 3. Сбрасываем статус выборов
        st = s.query(ElectionState).first()
        st.phase = "IDLE"
        s.query(Candidate).delete() # Очистка кандидатов
        
        s.commit()
        
    await call.answer("Выборы завершены!", show_alert=True)
    
    # Отправка объявления
    msg = f"🎉 **ВЫБОРЫ ЗАВЕРШЕНЫ!** 🎉\n"
    if winner_id:
        msg += f"Новый президент: 🦅 **{winner_name}** (ID: `{winner_id}`).\nПоздравляем!"
        await bot.send_message(winner_id, "🔥 **ПОЗДРАВЛЯЕМ!** Вы стали новым Президентом игры!")
    else:
        msg += "Президент не был избран."
    
    # --- НОВОЕ: Рассылка объявления о результатах ---
    await broadcast_message_to_chats(call.bot, msg)
        
    await call.message.edit_text(msg, parse_mode="Markdown")


# =========================================================
# === 12. ЗАПУСК ===
# =========================================================

async def on_startup():
    """Действия при запуске бота: инициализация БД и планировщика."""
    if init_db():
        # Добавляем задачу начислений с интервалом в 1 час
        scheduler.add_job(business_payout, 'interval', seconds=BUSINESS_PAYOUT_INTERVAL, id='biz_payout')
        scheduler.start()
        print("🚀 Бот и планировщик запущены!")
    else:
        print("❌ Критическая ошибка БД. Бот запущен, но может работать некорректно.")


async def main():
    """Основная точка входа для запуска бота."""
    if not BOT_TOKEN:
        logging.error("❌ BOT_TOKEN не найден. Завершение работы.")
        return

    if init_db():
        # Вызов set_bot_commands для установки команд в меню Telegram
        await set_bot_commands(bot)
        
        # Добавляем задачу начислений с интервалом в 1 час
        scheduler.add_job(business_payout, 'interval', seconds=BUSINESS_PAYOUT_INTERVAL, id='biz_payout')
        scheduler.start()
        logging.info("🚀 Бот и планировщик запущены!")
    else:
        logging.error("❌ Критическая ошибка БД. Бот запущен, но может работать некорректно.")
    
    # Запуск поллинга
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logging.error(f"Критическая ошибка запуска: {e}")
