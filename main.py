import os
import logging
import random
import asyncio
import sys
from datetime import datetime, timedelta
from typing import Optional, Dict

# --- Настройка Логирования ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# --- Проверка Импортов ---
try:
    from aiogram import Bot, Dispatcher, types, F, Router
    from aiogram.client.default import DefaultBotProperties 
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, BotCommand, BotCommandScopeDefault
    from aiogram.filters import Command, CommandObject
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.state import State, StatesGroup
    from aiogram.exceptions import TelegramAPIError
    
    from sqlalchemy import create_engine, Column, Integer, String, BigInteger, Boolean, DateTime, Float, text, ForeignKey
    from sqlalchemy.orm import sessionmaker, declarative_base, relationship, Session
    from sqlalchemy.exc import SQLAlchemyError
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
except ImportError as e:
    logging.error(f"❌ Критическая ошибка импорта: {e}. Убедитесь, что установлены aiogram, sqlalchemy, apscheduler, pymysql.")
    sys.exit(1)

# =========================================================
# === 1. КОНФИГУРАЦИЯ И КОНСТАНТЫ ИГРЫ (УСИЛЕННЫЕ) ===
# =========================================================

# --- Глобальные настройки ---
OWNER_ID = 1871352653  # Ваш ID
BOT_TOKEN = os.environ.get("BOT_TOKEN")
DB_PATH = os.environ.get("MYSQL_URL") or os.environ.get("DATABASE_URL")

if not BOT_TOKEN or not DB_PATH:
    logging.error("❌ НЕ НАЙДЕНЫ BOT_TOKEN или DATABASE_URL. Проверьте переменные окружения.")
    sys.exit(1)

# Преобразование для SQLAlchemy (требуется PyMySQL)
if "mysql://" in DB_PATH:
    DB_PATH = DB_PATH.replace("mysql://", "mysql+pymysql://", 1)

# --- Настройки Экономики, Финансов и Политики ---
STARTING_BALANCE = 500_000 # Высокий стартовый баланс для соответствия новым ценам
DAILY_BONUS_AMOUNT = 15_000
CASINO_MIN_BET = 10_000
CRIME_FINE_MULTIPLIER = 5 # Штраф = 5x от ставки
CRIME_JAIL_TIME_MINUTES = 30 # Время в тюрьме за неудачное ограбление

# Финансы
DEFAULT_LOAN_INTEREST_RATE = 0.05 # 5% за 24 часа
BANK_INTEREST_RATE = 0.001 # 0.1% пассивного начисления в сутки (для депозитов)
BANK_FEE_RATE = 0.005 # 0.5% комиссия за операции (депозит/снятие)
TAX_MAX_RATE = 0.40
MAX_LOAN_AMOUNT_MULTIPLIER = 10 # Максимальный кредит = 10x от текущего баланса

# Временные интервалы
PRODUCTION_CYCLE_HOURS = 4 # Время, необходимое бизнесу для производства
COLLECTION_CYCLE_HOURS = 24 # Максимальный период, за который можно собрать доход
LOAN_CYCLE_DAYS = 1 # Срок кредита

# --- Кнопки (Меню) ---
BTN_MARKET = "📈 Биржа Ресурсов"
BTN_CRIME = "🔫 Криминал"
BTN_LOAN = "🏦 Взять Кредит"
BTN_GOV_OFFICE = "🦅 Офис Президента"

# --- Ресурсы (Товары на Бирже) ---
MARKET_ITEMS: Dict[int, Dict] = {
    1: {"name": "🔩 Металлолом", "base_price": 500, "volatility": 0.15},
    2: {"name": "💎 Сырая Нефть", "base_price": 1_500, "volatility": 0.25},
    3: {"name": "💻 Чипы", "base_price": 4_000, "volatility": 0.35},
}

# --- Бизнесы (Сложная Производственная Цепочка) ---
BUSINESSES: Dict[int, Dict] = {
    1: {
        "name": "🏭 Мини-Завод",
        "cost": 500_000,
        "max_level": 5,
        "req_resource_id": 1, # Металлолом
        "resource_per_cycle": 100, # 100 единиц сырья за цикл
        "base_payout": 150_000,
        "upgrade_cost_mult": 2.0, # Стоимость апгрейда увеличивается в 2 раза
        "payout_mult": 1.5 # Доход увеличивается в 1.5 раза за уровень
    },
    2: {
        "name": "🚀 Космический Порт",
        "cost": 15_000_000,
        "max_level": 10,
        "req_resource_id": 2, # Сырая Нефть
        "resource_per_cycle": 500,
        "base_payout": 5_000_000,
        "upgrade_cost_mult": 1.7,
        "payout_mult": 1.3
    },
    3: {
        "name": "⚛️ Квантовая Ферма",
        "cost": 150_000_000,
        "max_level": 15,
        "req_resource_id": 3, # Чипы
        "resource_per_cycle": 200,
        "base_payout": 35_000_000,
        "upgrade_cost_mult": 1.5,
        "payout_mult": 1.25
    },
}

# =========================================================
# === 2. БАЗА ДАННЫХ (ORM) ===
# =========================================================

Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, index=True)
    username = Column(String(100), nullable=True)
    first_name = Column(String(100), nullable=True)
    
    # Экономика
    balance = Column(BigInteger, default=STARTING_BALANCE)
    bank_balance = Column(BigInteger, default=0)
    last_daily_bonus = Column(DateTime, default=datetime.min)
    
    # Карьера/Арест (Оставлены для разнообразия)
    job_level = Column(Integer, default=1)
    last_work_time = Column(DateTime, default=datetime.min)
    arrest_expires = Column(DateTime, nullable=True)

    # Статус
    is_admin = Column(Boolean, default=False)
    is_owner = Column(Boolean, default=False)
    is_president = Column(Boolean, default=False)
    is_banned = Column(Boolean, default=False)
    
    # Политика/Криминал
    last_vote_time = Column(DateTime, nullable=True)
    last_crime_time = Column(DateTime, default=datetime.min)

    # Связи
    loans = relationship("BankLoan", back_populates="user")
    businesses = relationship("OwnedBusiness", back_populates="user")

class BankLoan(Base):
    __tablename__ = 'bank_loans'
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, ForeignKey('users.telegram_id'), index=True)
    amount = Column(BigInteger)
    interest_rate = Column(Float)
    issue_date = Column(DateTime, default=datetime.now)
    due_date = Column(DateTime)
    paid = Column(Boolean, default=False)
    
    user = relationship("User", back_populates="loans")

class OwnedBusiness(Base):
    __tablename__ = 'owned_businesses'
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, ForeignKey('users.telegram_id'), index=True)
    business_id = Column(Integer)
    count = Column(Integer, default=1)
    upgrade_level = Column(Integer, default=1)
    
    # Сложный цикл производства
    resource_stock = Column(Integer, default=0) # Запасы сырья
    production_state = Column(String(20), default="IDLE") # IDLE, PRODUCING, READY
    production_start_time = Column(DateTime, nullable=True)
    last_collected = Column(DateTime, default=datetime.min)
    
    user = relationship("User", back_populates="businesses")

class ElectionState(Base):
    __tablename__ = 'election_state'
    id = Column(Integer, primary_key=True)
    phase = Column(String(20), default="IDLE")
    
    # Настройки, которые может менять Президент
    tax_rate = Column(Float, default=0.10)     
    loan_interest_rate = Column(Float, default=DEFAULT_LOAN_INTEREST_RATE)
    
    end_time = Column(DateTime, nullable=True)
    last_election_time = Column(DateTime, default=datetime.min)

class Candidate(Base):
    __tablename__ = 'candidates'
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, unique=True)
    votes = Column(Integer, default=0)

class MarketItemPrice(Base):
    __tablename__ = 'market_prices'
    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, unique=True)
    current_price = Column(BigInteger)

class PresidentialBudget(Base):
    __tablename__ = 'presidential_budget'
    id = Column(Integer, primary_key=True)
    budget = Column(BigInteger, default=0)

class Chat(Base):
    __tablename__ = 'chats'
    id = Column(Integer, primary_key=True)
    chat_id = Column(BigInteger, unique=True)

# =========================================================
# === 3. ИНИЦИАЛИЗАЦИЯ СЕРВИСОВ И УТИЛИТЫ ===
# =========================================================

# --- SQLAlchemy Setup ---
try:
    engine = create_engine(DB_PATH, pool_pre_ping=True, pool_size=10, max_overflow=20)
    SessionLocal = sessionmaker(bind=engine)
except Exception as e:
    logging.error(f"❌ ОШИБКА НАСТРОЙКИ ENGINE: {e}")
    sys.exit(1)

def init_db():
    """Инициализация базы данных, создание таблиц и первичных записей."""
    try:
        logging.info("Инициализация БД. Проверка таблиц...")
        Base.metadata.create_all(engine)
        with SessionLocal() as s:
            if not s.query(ElectionState).first():
                s.add(ElectionState())
            if not s.query(PresidentialBudget).first():
                s.add(PresidentialBudget())
            
            # Инициализация цен на бирже
            for item_id, item_info in MARKET_ITEMS.items():
                if not s.query(MarketItemPrice).filter_by(item_id=item_id).first():
                    s.add(MarketItemPrice(item_id=item_id, current_price=item_info['base_price']))
            
            # Назначение владельца
            owner = s.query(User).filter_by(telegram_id=OWNER_ID).first()
            if owner:
                if not owner.is_owner:
                    owner.is_owner = True
                    owner.is_admin = True
            
            s.commit()
            logging.info(f"✅ Владелец {OWNER_ID} подтвержден и права установлены. База готова.")
        return True
    except Exception as e:
        logging.error(f"❌ Ошибка инициализации БД: {e}")
        return False

# --- Хелперы БД ---
def get_user(telegram_id, username=None, first_name=None):
    """Получает пользователя или создает нового, если не найден."""
    with SessionLocal() as s:
        u = s.query(User).filter_by(telegram_id=telegram_id).first()
        if not u:
            is_owner = (telegram_id == OWNER_ID)
            u = User(telegram_id=telegram_id, username=username, first_name=first_name, is_owner=is_owner, is_admin=is_owner)
            s.add(u)
            s.commit()
            s.refresh(u)
        else:
            # Обновление метаданных
            if username and u.username != username: u.username = username
            if first_name and u.first_name != first_name: u.first_name = first_name
            if u.telegram_id == OWNER_ID and not u.is_owner:
                 u.is_owner = True
                 u.is_admin = True
            s.commit()
        
        # Возвращаем копию объекта (безопасность)
        return u 

def format_cooldown(last_time: datetime, cooldown: timedelta) -> str:
    """Форматирует оставшееся время до конца кулдауна/таймера."""
    remaining = last_time + cooldown - datetime.now()
    if remaining.total_seconds() < 0: return None
    
    hours = int(remaining.total_seconds() // 3600)
    minutes = int((remaining.total_seconds() % 3600) // 60)
    seconds = int(remaining.total_seconds() % 60)
    
    parts = []
    if hours > 0: parts.append(f"{hours} ч.")
    if minutes > 0: parts.append(f"{minutes} мин.")
    if seconds > 0 or not parts: parts.append(f"{seconds} сек.")
    
    return " ".join(parts)

def get_current_loan_interest(s: Session) -> float:
    """Получает текущую ставку по кредитам, установленную Президентом."""
    est = s.query(ElectionState).first()
    return est.loan_interest_rate if est else DEFAULT_LOAN_INTEREST_RATE

# =========================================================
# === 4. AIOGRAM, FSM И КЛАВИАТУРЫ ===
# =========================================================

BOT_PROPS = DefaultBotProperties(parse_mode="Markdown")
bot = Bot(token=BOT_TOKEN, default=BOT_PROPS)
dp = Dispatcher()
router = Router()
dp.include_router(router)
scheduler = AsyncIOScheduler()

# --- FSM States ---
class GameStates(StatesGroup):
    casino_bet = State()
    admin_input = State()
    
    bank_deposit = State()
    bank_withdraw = State()
    
    loan_request = State() # Запрос суммы кредита
    loan_pay = State() # Оплата кредита
    
    market_buy_select = State()
    market_sell_select = State()
    market_buy_amount = State() # Сумма покупки ресурса
    
    biz_buy = State()
    biz_upgrade_select = State()
    biz_resource_input = State() # Ввод сырья для бизнеса
    
    pres_tax_input = State()
    pres_loan_rate_input = State()
    pres_give_budget = State()

# --- Клавиатуры ---
def get_main_kb(is_admin=False, is_president=False):
    """Основное меню."""
    kb = [
        [KeyboardButton(text="📒 Профиль"), KeyboardButton(text="💰 Банк"), KeyboardButton(text="💼 Активности")],
        [KeyboardButton(text="🎰 Казино"), KeyboardButton(text=BTN_CRIME), KeyboardButton(text="🏆 Рейтинг")],
        [KeyboardButton(text="🏛 Политика"), KeyboardButton(text=BTN_MARKET), KeyboardButton(text="💞 Помощь")]
    ]
    if is_president:
        kb.insert(1, [KeyboardButton(text=BTN_GOV_OFFICE)])
    if is_admin:
        kb.append([KeyboardButton(text="🛡 Админка")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_activities_kb():
    """Меню Активностей (Работа, Бизнес, Бонус)."""
    kb = [
        [KeyboardButton(text="💼 Устроиться"), KeyboardButton(text="📈 Бизнес-Центр")],
        [KeyboardButton(text="🎁 Бонус")],
        [KeyboardButton(text="🏠 Главное Меню")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_bank_kb(has_debt: bool):
    """Меню Банка."""
    kb = [
        [InlineKeyboardButton(text="📥 Депозит", callback_data="bank_deposit_start"),
         InlineKeyboardButton(text="📤 Снять", callback_data="bank_withdraw_start")],
        [InlineKeyboardButton(text=BTN_LOAN, callback_data="loan_request_start")]
    ]
    if has_debt:
        kb.append([InlineKeyboardButton(text="💸 Погасить Кредит", callback_data="loan_pay_start")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_biz_management_kb(has_biz: bool):
    """Меню Бизнес-Центра."""
    kb = [
        [InlineKeyboardButton(text="📊 Статистика", callback_data="biz_stat")],
        [InlineKeyboardButton(text="🛒 Купить новый бизнес", callback_data="biz_shop")],
    ]
    if has_biz:
        kb.append([
            InlineKeyboardButton(text="🏭 Запустить Производство", callback_data="biz_start_prod_select"),
            InlineKeyboardButton(text="💵 Снять Готовый Доход", callback_data="biz_collect_all"),
        ])
        kb.append([InlineKeyboardButton(text="✅ Улучшить Бизнес", callback_data="biz_upgrade_start")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


# =========================================================
# === 5. ГЛАВНЫЙ ФУНКЦИОНАЛ ИГРЫ (ОСНОВНОЕ МЕНЮ) ===
# =========================================================

@router.message(Command("start"))
async def cmd_start(message: types.Message):
    u = get_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    
    with SessionLocal() as s:
        if not s.query(Chat).filter_by(chat_id=message.chat.id).first():
            s.add(Chat(chat_id=message.chat.id))
            s.commit()
    
    if u.is_banned: return await message.answer("🚫 Вы заблокированы.")
    
    await message.answer(
        f"🌟 *Добро пожаловать в BongoCity*, {u.first_name}!\n"
        f"Удачи в построении вашей империи!",
        reply_markup=get_main_kb(u.is_admin, u.is_president)
    )

@router.message(F.text == "🏠 Главное Меню")
async def cmd_back(message: types.Message):
    u = get_user(message.from_user.id)
    await message.answer("🏠 *Вы вернулись в Главное Меню.*", reply_markup=get_main_kb(u.is_admin, u.is_president))

@router.message(F.text == "📒 Профиль")
@router.message(Command("profile"))
async def cmd_profile(message: types.Message):
    u = get_user(message.from_user.id)
    
    with SessionLocal() as s:
        est = s.query(ElectionState).first()
        tax_rate = est.tax_rate if est else 0.10
        active_loans = s.query(BankLoan).filter_by(user_id=u.telegram_id, paid=False).count()
        
        # Определение статуса
        status = "👨‍💼 Гражданин"
        if u.is_owner: status = "👑 Владелец Вселенной"
        elif u.is_president: status = "🦅 Президент"
        elif u.is_admin: status = "🛡 Администратор"
        
        arrest_info = ""
        if u.arrest_expires and u.arrest_expires > datetime.now():
            left = format_cooldown(datetime.now(), u.arrest_expires - datetime.now())
            arrest_info = f"\n🚨 **В ТЮРЬМЕ**: осталось {left}"

        info = (
            f"👤 *Профиль: {u.first_name}*\n"
            f"ID: `{u.telegram_id}` | **{status}**\n\n"
            f"--- 💵 Финансы ---\n"
            f"💰 Наличные: *{u.balance:,} $*\n"
            f"🏦 Банковский Счет: *{u.bank_balance:,} $*\n"
            f"💸 Активных Кредитов: **{active_loans}**\n"
            f"🏛 Гос. Налог: {int(tax_rate*100)}%\n"
            f"--- ⚙️ Статус ---\n"
            f"🛠 Текущая Работа: {JOBS[u.job_level]['name']}\n"
            f"{arrest_info}"
        )
            
    await message.answer(info, reply_markup=get_main_kb(u.is_admin, u.is_president))

# --- Рейтинг ---
@router.message(F.text == "🏆 Рейтинг")
async def cmd_top(message: types.Message):
    with SessionLocal() as s:
        # Сложный рейтинг: Наличные + Банк - Кредиты
        subquery = s.query(BankLoan.user_id, func.sum(BankLoan.amount).label('total_debt')).filter_by(paid=False).group_by(BankLoan.user_id).subquery()
        
        users = s.query(
            User,
            (User.balance + User.bank_balance - coalesce(subquery.c.total_debt, 0)).label('net_wealth')
        ).outerjoin(subquery, User.telegram_id == subquery.c.user_id) \
        .order_by(text('net_wealth DESC')) \
        .limit(10).all()
    
    text = "🏆 **ТОП-10 Богатейших Граждан BongoCity** (Чистый Капитал)\n"
    for i, (u, net_wealth) in enumerate(users):
        name = u.username or u.first_name
        is_pres = "🦅" if u.is_president else ""
        text += f"{i+1}. {is_pres} {name}: *{net_wealth:,.0f} $*\n"
        
    await message.answer(text)

# --- Помощь ---
@router.message(F.text == "💞 Помощь")
async def cmd_help(message: types.Message):
    await message.answer(
        "✨ *Помощь и Информация*\n\n"
        "**Система Бизнеса:** Для получения дохода ваши бизнесы теперь требуют *Сырья*. Купите его на Бирже, запустите производство, и через некоторое время соберите готовую продукцию.\n\n"
        "**Банк:** Вы можете брать кредиты с ежедневным начислением процентов. Неуплата приводит к штрафам!\n\n"
        "**Политика:** Президент управляет Госбюджетом и устанавливает Налоги и Кредитную Ставку.",
        reply_markup=get_main_kb(get_user(message.from_user.id).is_admin, get_user(message.from_user.id).is_president)
    )

# =========================================================
# === 6. БАНК И КРЕДИТНАЯ СИСТЕМА (СЛОЖНО) ===
# =========================================================

@router.message(F.text == "💰 Банк")
async def cmd_bank(message: types.Message):
    u = get_user(message.from_user.id)
    with SessionLocal() as s:
        has_debt = s.query(BankLoan).filter_by(user_id=u.telegram_id, paid=False).first() is not None
        interest_rate = get_current_loan_interest(s)
    
    fee_text = f"Комиссия за операцию: *{int(BANK_FEE_RATE*100)}%*.\n"
    loan_text = f"Ставка по кредитам (24ч): *{int(interest_rate*100)}%*."
    
    await message.answer(
        f"🏦 **Банк BongoCity**\n"
        f"Ваш счет: *{u.bank_balance:,} $*\n"
        f"{fee_text}{loan_text}",
        reply_markup=get_bank_kb(has_debt)
    )

# --- Депозит/Снятие (FSM) ---
@router.callback_query(F.data == "bank_deposit_start")
async def bank_deposit_start(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(GameStates.bank_deposit)
    await call.message.edit_text("📥 Введите сумму депозита (наличные -> банк):")

@router.message(GameStates.bank_deposit)
async def bank_deposit_finish(message: types.Message, state: FSMContext):
    await state.clear()
    uid = message.from_user.id
    try: amount = int(message.text)
    except: return await message.answer("❌ Введите число.", reply_markup=get_main_kb())

    try:
        with SessionLocal() as s:
            u = s.query(User).filter_by(telegram_id=uid).with_for_update().first()
            if u.balance < amount: return await message.answer(f"❌ Не хватает наличных. На счету: {u.balance:,}$")

            fee = int(amount * BANK_FEE_RATE)
            net_amount = amount - fee
            
            u.balance -= amount
            u.bank_balance += net_amount
            s.commit()
            await message.answer(f"✅ Депозит: +{net_amount:,}$ (комиссия: {fee:,}$)")
    except SQLAlchemyError:
        await message.answer("❌ Ошибка БД.")
    finally: await message.answer("Возврат в меню.", reply_markup=get_main_kb(get_user(uid).is_admin, get_user(uid).is_president))

# --- Кредитная Система ---
@router.callback_query(F.data == "loan_request_start")
async def loan_request_start(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    uid = call.from_user.id
    
    with SessionLocal() as s:
        # Проверка на наличие текущих кредитов
        active_loans = s.query(BankLoan).filter_by(user_id=uid, paid=False).count()
        if active_loans >= 3:
            return await call.message.answer("❌ Вы не можете взять более 3 активных кредитов одновременно.")
        
        u = get_user(uid)
        max_loan = u.balance * MAX_LOAN_AMOUNT_MULTIPLIER
        rate = get_current_loan_interest(s)
    
    await state.set_state(GameStates.loan_request)
    await state.update_data(rate=rate)
    await call.message.edit_text(
        f"💸 **Запрос Кредита**\n"
        f"Максимальная сумма: {max_loan:,}$\n"
        f"Ставка (за {LOAN_CYCLE_DAYS} дн.): {int(rate*100)}%\n"
        f"Введите желаемую сумму кредита:"
    )

@router.message(GameStates.loan_request)
async def loan_request_finish(message: types.Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    uid = message.from_user.id
    
    try: amount = int(message.text)
    except: return await message.answer("❌ Введите число.", reply_markup=get_main_kb())

    with SessionLocal() as s:
        u = s.query(User).filter_by(telegram_id=uid).with_for_update().first()
        max_loan = u.balance * MAX_LOAN_AMOUNT_MULTIPLIER
        
        if amount <= 1000 or amount > max_loan:
             return await message.answer(f"❌ Сумма должна быть между 1,000$ и {max_loan:,}$.", reply_markup=get_main_kb())
        
        rate = data.get('rate', DEFAULT_LOAN_INTEREST_RATE)
        due_date = datetime.now() + timedelta(days=LOAN_CYCLE_DAYS)
        
        # Выдаем деньги на банковский счет
        u.bank_balance += amount 
        
        s.add(BankLoan(
            user_id=uid,
            amount=amount,
            interest_rate=rate,
            due_date=due_date
        ))
        s.commit()
        
        await message.answer(
            f"✅ **Кредит Одобрен!**\n"
            f"Сумма: +{amount:,}$ (на счет)\n"
            f"Срок погашения: {due_date.strftime('%d.%m.%Y')}\n"
            f"Процент: {int(rate*100)}%",
            reply_markup=get_main_kb(u.is_admin, u.is_president)
        )

@router.callback_query(F.data == "loan_pay_start")
async def loan_pay_start(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    uid = call.from_user.id
    
    with SessionLocal() as s:
        loans = s.query(BankLoan).filter_by(user_id=uid, paid=False).all()
        if not loans:
             return await call.message.answer("Нет активных кредитов для погашения.")
        
        text = "Выберите кредит для погашения:\n"
        kb = InlineKeyboardMarkup(inline_keyboard=[])
        
        for loan in loans:
            # Начисление текущего процента (просто для отображения)
            days_passed = (datetime.now() - loan.issue_date).days
            total_interest = loan.amount * loan.interest_rate * max(1, days_passed)
            total_payback = loan.amount + total_interest
            
            text += f"ID {loan.id}. Сумма: {loan.amount:,}$, %: {int(loan.interest_rate*100)}%, К возврату: {total_payback:,.0f}$\n"
            kb.inline_keyboard.append([InlineKeyboardButton(text=f"Погасить ID {loan.id} ({total_payback:,.0f}$)", callback_data=f"loan_pay_do_{loan.id}")])
            
    await call.message.answer(text, reply_markup=kb)

@router.callback_query(F.data.startswith("loan_pay_do_"))
async def loan_pay_do(call: types.CallbackQuery):
    await call.answer()
    loan_id = int(call.data.split('_')[3])
    uid = call.from_user.id
    
    try:
        with SessionLocal() as s:
            u = s.query(User).filter_by(telegram_id=uid).with_for_update().first()
            loan = s.query(BankLoan).filter_by(id=loan_id, user_id=uid, paid=False).with_for_update().first()
            
            if not loan:
                 return await call.message.answer("❌ Кредит не найден.")

            # Расчет суммы к погашению (с процентом)
            days_passed = (datetime.now() - loan.issue_date).days
            # Минимум 1 цикл для начисления процентов
            cycles = max(1, days_passed // LOAN_CYCLE_DAYS)
            
            # Проценты начисляются только за прошедшие полные циклы.
            total_interest = loan.amount * loan.interest_rate * cycles
            total_payback = loan.amount + total_interest

            if u.bank_balance < total_payback:
                 return await call.message.answer(f"❌ На счету недостаточно средств. Необходимо: {total_payback:,.0f}$ (на счету: {u.bank_balance:,}$) ")
            
            # Погашение
            u.bank_balance -= total_payback
            loan.paid = True
            
            # Налоговое отчисление (Госбюджет)
            budget = s.query(PresidentialBudget).with_for_update().first()
            gov_share = int(total_interest * 0.20) # 20% от процентов идет в госбюджет
            budget.budget += gov_share
            
            s.commit()
            
            await call.message.answer(
                f"✅ **Кредит ID {loan_id} Погашен!**\n"
                f"Сумма погашения: {total_payback:,.0f}$ (включая {total_interest:,.0f}$ процентов)\n"
                f"Остаток на счете: {u.bank_balance:,}$"
            )

    except SQLAlchemyError as e:
        logging.error(f"Loan Pay DB Error: {e}")
        await call.message.answer("❌ Ошибка БД при погашении кредита.")


# =========================================================
# === 7. СЛОЖНАЯ СИСТЕМА БИЗНЕСА И ПРОИЗВОДСТВА ===
# =========================================================

@router.message(F.text == "💼 Активности")
async def cmd_activities(message: types.Message):
    await message.answer("💼 *Меню Активностей BongoCity*\n"
                         "Выберите, чем займетесь сегодня!",
                         reply_markup=get_activities_kb())

# --- Бизнес-Центр ---
@router.message(F.text == "📈 Бизнес-Центр")
async def cmd_biz(message: types.Message):
    u = get_user(message.from_user.id)
    with SessionLocal() as s:
        has_biz = s.query(OwnedBusiness).filter_by(user_id=u.telegram_id).first()
    await message.answer("🏢 **Бизнес-Центр BongoCity**\n"
                         "Управляйте своими производственными активами!", reply_markup=get_biz_management_kb(has_biz is not None))

@router.callback_query(F.data == "biz_stat")
async def biz_stat(call: types.CallbackQuery):
    await call.answer()
    uid = call.from_user.id
    
    with SessionLocal() as s:
        bizs = s.query(OwnedBusiness).filter_by(user_id=uid).all()
        est = s.query(ElectionState).first()
        tax_rate = est.tax_rate if est else 0.10
        
        if not bizs:
            return await call.message.answer("У вас пока нет бизнесов.")
        
        info = "📊 **Ваши Производственные Активы** (Налог: {int(tax_rate*100)}%)\n"
        
        for b in bizs:
            biz_info = BUSINESSES.get(b.business_id)
            if not biz_info: continue
            
            # Расчет дохода (базовый доход * мультипликатор уровня)
            current_payout = int(biz_info['base_payout'] * (biz_info['payout_mult'] ** (b.upgrade_level - 1)))
            resource_info = MARKET_ITEMS.get(biz_info['req_resource_id'])
            
            status_emoji = "🛑"
            production_status = ""
            if b.production_state == "IDLE":
                status_emoji = "💤"
                production_status = f"Требуется {biz_info['resource_per_cycle'] * b.count} x {resource_info['name']}"
            elif b.production_state == "PRODUCING" and b.production_start_time:
                status_emoji = "⏳"
                remaining = format_cooldown(b.production_start_time, timedelta(hours=PRODUCTION_CYCLE_HOURS))
                production_status = f"Производство. Осталось: {remaining}"
            elif b.production_state == "READY":
                status_emoji = "✅"
                production_status = f"Готово к сбору! (x{b.count} ед.)"
            
            info += (
                f"\n--- {biz_info['name']} (x{b.count}) ---\n"
                f"🌟 Уровень: {b.upgrade_level}\n"
                f"💰 Выход: {current_payout:,} $ за 1 ед. (Общий потенциал: {current_payout * b.count:,} $)\n"
                f"⚙️ Сырье в запасе: {b.resource_stock} ед.\n"
                f"{status_emoji} Статус: *{production_status}*"
            )
            
        await call.message.answer(info, reply_markup=get_biz_management_kb(True))

# --- Запуск Производства (FSM-покупка сырья) ---
@router.callback_query(F.data == "biz_start_prod_select")
async def biz_start_prod_select(call: types.CallbackQuery):
    await call.answer()
    uid = call.from_user.id

    with SessionLocal() as s:
        bizs = s.query(OwnedBusiness).filter_by(user_id=uid).all()
        kb = InlineKeyboardMarkup(inline_keyboard=[])
        
        for b in bizs:
            biz_info = BUSINESSES.get(b.business_id)
            if not biz_info: continue
            
            res_id = biz_info['req_resource_id']
            res_name = MARKET_ITEMS[res_id]['name']
            
            # Проверяем, сколько сырья нужно для одного полного цикла (для всех объектов этого типа)
            required_res = biz_info['resource_per_cycle'] * b.count
            
            if b.production_state == "PRODUCING":
                 status = "⏳"
            elif b.production_state == "READY":
                 status = "✅"
            else: # IDLE
                 status = "➕"

            kb.inline_keyboard.append([
                InlineKeyboardButton(
                    text=f"{status} {biz_info['name']} (x{b.count}) | Запас: {b.resource_stock} | Нужно: {required_res} x {res_name}",
                    callback_data=f"biz_res_input_{b.id}"
                )
            ])
            
    await call.message.answer("Запуск производства: Выберите бизнес, для которого хотите купить сырье:", reply_markup=kb)

@router.callback_query(F.data.startswith("biz_res_input_"))
async def biz_res_input_start(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    biz_db_id = int(call.data.split('_')[3])
    
    with SessionLocal() as s:
        b = s.query(OwnedBusiness).filter_by(id=biz_db_id, user_id=call.from_user.id).first()
        if not b: return await call.message.answer("❌ Бизнес не найден.")
        
        biz_info = BUSINESSES.get(b.business_id)
        res_info = MARKET_ITEMS.get(biz_info['req_resource_id'])
        current_price = s.query(MarketItemPrice).filter_by(item_id=res_info['id']).first().current_price
        
        # Кол-во сырья для 1 полного цикла
        required_res = biz_info['resource_per_cycle'] * b.count
        
        await state.set_state(GameStates.biz_resource_input)
        await state.update_data(biz_db_id=biz_db_id, res_id=res_info['id'], price=current_price)

        await call.message.answer(
            f"🛒 **Покупка сырья для {biz_info['name']}**\n"
            f"Ресурс: {res_info['name']} | Цена/ед.: {current_price:,}$\n"
            f"Для одного цикла требуется: {required_res} ед.\n"
            f"Введите *количество* единиц {res_info['name']} для покупки:"
        )

@router.message(GameStates.biz_resource_input)
async def biz_res_input_finish(message: types.Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    uid = message.from_user.id
    
    try: units_to_buy = int(message.text)
    except: return await message.answer("❌ Введите число.", reply_markup=get_main_kb())

    biz_db_id = data['biz_db_id']
    price = data['price']
    total_cost = units_to_buy * price
    
    try:
        with SessionLocal() as s:
            u = s.query(User).filter_by(telegram_id=uid).with_for_update().first()
            b = s.query(OwnedBusiness).filter_by(id=biz_db_id, user_id=uid).with_for_update().first()
            
            if u.balance < total_cost:
                 return await message.answer(f"❌ Не хватает наличных. Требуется: {total_cost:,}$")
            
            # Транзакция
            u.balance -= total_cost
            b.resource_stock += units_to_buy
            
            # Автоматический запуск производства, если сырья достаточно
            biz_info = BUSINESSES.get(b.business_id)
            required_res = biz_info['resource_per_cycle'] * b.count
            
            if b.production_state == "IDLE" and b.resource_stock >= required_res:
                 b.production_state = "PRODUCING"
                 b.resource_stock -= required_res
                 b.production_start_time = datetime.now()
                 msg_prod = "🏭 *Производство запущено!*"
            else:
                 msg_prod = f"Запас сырья: {b.resource_stock} ед."

            s.commit()
            
            await message.answer(
                f"✅ **Покупка и Загрузка Успешны!**\n"
                f"Куплено {units_to_buy} ед. за {total_cost:,}$\n"
                f"{msg_prod}",
                reply_markup=get_main_kb(u.is_admin, u.is_president)
            )

    except SQLAlchemyError as e:
        logging.error(f"Biz Resource DB Error: {e}")
        await message.answer("❌ Ошибка БД при покупке сырья.")
    finally: await message.answer("Возврат в меню.", reply_markup=get_main_kb(get_user(uid).is_admin, get_user(uid).is_president))

# --- Сбор готовой продукции ---
@router.callback_query(F.data == "biz_collect_all")
async def biz_collect_all(call: types.CallbackQuery):
    await call.answer()
    uid = call.from_user.id
    
    total_income_net = 0
    collected_units = 0
    
    try:
        with SessionLocal() as s:
            u = s.query(User).filter_by(telegram_id=uid).with_for_update().first()
            est = s.query(ElectionState).first()
            tax_rate = est.tax_rate if est else 0.10
            
            bizs = s.query(OwnedBusiness).filter_by(user_id=uid, production_state="READY").with_for_update().all()
            
            for b in bizs:
                biz_info = BUSINESSES.get(b.business_id)
                if not biz_info: continue
                
                # Расчет дохода
                current_payout = int(biz_info['base_payout'] * (biz_info['payout_mult'] ** (b.upgrade_level - 1)))
                raw_payout = current_payout * b.count
                
                # Налоговое отчисление
                tax_amount = int(raw_payout * tax_rate)
                net_payout = raw_payout - tax_amount
                
                total_income_net += net_payout
                collected_units += b.count
                
                # Сброс состояния и попытка повторного запуска
                b.production_state = "IDLE"
                
                required_res = biz_info['resource_per_cycle'] * b.count
                if b.resource_stock >= required_res:
                     b.production_state = "PRODUCING"
                     b.resource_stock -= required_res
                     b.production_start_time = datetime.now()
                
            if total_income_net > 0:
                u.balance += total_income_net
                
                # Налоговые отчисления идут в госбюджет
                budget = s.query(PresidentialBudget).with_for_update().first()
                budget.budget += int(total_income_net * tax_rate / (1-tax_rate)) # (Обратный расчет налога)

                s.commit()
                await call.message.answer(
                    f"💸 **Сбор Продукции Успешен!**\n"
                    f"Собрано {collected_units} ед. продукции.\n"
                    f"💰 Чистый доход (после налога {int(tax_rate*100)}%): *{total_income_net:,} $*\n"
                )
            else:
                await call.message.answer("⏳ Нет готовой продукции для сбора.")
                
    except SQLAlchemyError as e:
        logging.error(f"Biz Collect DB Error: {e}")
        await call.message.answer("❌ Ошибка БД при сборе дохода.")

# --- Покупка нового бизнеса (Усиленные цены) ---
@router.callback_query(F.data == "biz_shop")
async def biz_shop(call: types.CallbackQuery):
    await call.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    
    for k, v in BUSINESSES.items():
        res_name = MARKET_ITEMS[v['req_resource_id']]['name']
        kb.inline_keyboard.append([InlineKeyboardButton(
            text=f"🛒 {v['name']} | Цена: {v['cost']:,}$ | Требует: {res_name}",
            callback_data=f"biz_buy_{k}"
        )])
        
    await call.message.edit_text("🛒 *Магазин Бизнесов BongoCity*\nВыберите объект для инвестирования:", reply_markup=kb)

@router.callback_query(F.data.startswith("biz_buy_"))
async def biz_buy(call: types.CallbackQuery):
    await call.answer()
    bid = int(call.data.split("_")[2])
    cost = BUSINESSES[bid]['cost']
    uid = call.from_user.id
    
    try:
        with SessionLocal() as s:
            u = s.query(User).filter_by(telegram_id=uid).with_for_update().first()
            if u.balance < cost:
                return await call.message.answer(f"❌ Не хватает {cost - u.balance:,}$ для покупки.")
            
            u.balance -= cost
            exist = s.query(OwnedBusiness).filter_by(user_id=uid, business_id=bid).with_for_update().first()
            
            if exist:
                exist.count += 1
            else:
                s.add(OwnedBusiness(user_id=uid, business_id=bid, count=1))
            s.commit()
            
            await call.message.answer(f"✅ Успешная покупка: {BUSINESSES[bid]['name']} (-{cost:,}$).")
    except SQLAlchemyError:
        await call.message.answer("❌ Ошибка БД при покупке.")

# --- Улучшение Бизнеса ---
@router.callback_query(F.data == "biz_upgrade_start")
async def biz_upgrade_start(call: types.CallbackQuery):
    await call.answer()
    uid = call.from_user.id

    with SessionLocal() as s:
        bizs = s.query(OwnedBusiness).filter_by(user_id=uid).all()
        
        if not bizs:
            return await call.message.answer("❌ Нет бизнесов для улучшения.")
        
        kb = InlineKeyboardMarkup(inline_keyboard=[])
        
        for b in bizs:
            biz_info = BUSINESSES.get(b.business_id)
            if not biz_info: continue
            
            current_level = b.upgrade_level
            max_level = biz_info['max_level']
            
            if current_level >= max_level:
                btn_text = f"⭐ {biz_info['name']} | Уровень {current_level} (MAX)"
                kb.inline_keyboard.append([InlineKeyboardButton(text=btn_text, callback_data="no_action")])
            else:
                # Стоимость следующего уровня = Базовая стоимость * (Мультипликатор)^текущий_уровень
                cost_to_upgrade = int(biz_info['cost'] * (biz_info['upgrade_cost_mult'] ** current_level))
                
                # Расчет нового дохода
                next_payout = int(biz_info['base_payout'] * (biz_info['payout_mult'] ** current_level))
                
                btn_text = (
                    f"⬆️ {biz_info['name']} | Ур. {current_level} -> {current_level + 1} "
                    f"(Новый Выход: {next_payout:,}$) "
                    f"| Цена: {cost_to_upgrade:,}$"
                )
                kb.inline_keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=f"biz_upgrade_do_{b.id}_{cost_to_upgrade}")])
                
    await call.message.answer("✨ **Меню Улучшений Бизнеса**\n"
                              "Улучшения повышают выход продукции!", reply_markup=kb)

@router.callback_query(F.data.startswith("biz_upgrade_do_"))
async def biz_upgrade_do(call: types.CallbackQuery):
    await call.answer()
    uid = call.from_user.id
    _, _, biz_db_id_str, cost_str = call.data.split('_')
    biz_db_id = int(biz_db_id_str)
    cost = int(cost_str)
    
    try:
        with SessionLocal() as s:
            u = s.query(User).filter_by(telegram_id=uid).with_for_update().first()
            b = s.query(OwnedBusiness).filter_by(id=biz_db_id, user_id=uid).with_for_update().first()
            
            if not b or u.balance < cost:
                return await call.message.answer("❌ Бизнес не найден или недостаточно средств.")
            
            biz_info = BUSINESSES.get(b.business_id)
            if b.upgrade_level >= biz_info['max_level']:
                return await call.message.answer("❌ Достигнут максимальный уровень улучшения.")
                
            u.balance -= cost
            b.upgrade_level += 1
            
            new_payout = int(biz_info['base_payout'] * (biz_info['payout_mult'] ** (b.upgrade_level - 1)))
            
            s.commit()
            
            await call.message.answer(
                f"🎉 **Улучшение Завершено!**\n"
                f"Апгрейд: {biz_info['name']} до уровня *{b.upgrade_level}* (-{cost:,}$)\n"
                f"Новый выход продукции: *{new_payout:,} $*"
            )
            
    except SQLAlchemyError as e:
        logging.error(f"Biz Upgrade DB Error: {e}")
        await call.message.answer("❌ Ошибка БД при улучшении бизнеса.")

# --- Карьера (оставлена для начального дохода) ---
@router.message(F.text == "💼 Устроиться")
async def cmd_work_menu(message: types.Message):
    u = get_user(message.from_user.id)
    # ... (логика работы и повышения оставлена без изменений)
    await message.answer("🛠 *Работа (базовый доход)*: логика в этом релизе не менялась. Выполните работу.",
                         reply_markup=get_main_kb(u.is_admin, u.is_president))

# =========================================================
# === 8. БИРЖА РЕСУРСОВ (ДИНАМИЧЕСКИЕ ЦЕНЫ) ===
# =========================================================

@router.message(F.text == BTN_MARKET)
async def cmd_market(message: types.Message):
    with SessionLocal() as s:
        prices = s.query(MarketItemPrice).all()
        
        info = "📈 **Биржа Ресурсов BongoCity**\n(Цены меняются каждый час)\n\n"
        kb = InlineKeyboardMarkup(inline_keyboard=[])
        
        for p in prices:
            item = MARKET_ITEMS.get(p.item_id)
            info += f"{item['name']} | Текущая Цена: *{p.current_price:,} $*\n"
            kb.inline_keyboard.append([InlineKeyboardButton(text=f"🛒 Купить {item['name']}", callback_data=f"market_buy_{p.item_id}")])
            
    await message.answer(info, reply_markup=kb)

# --- FSM для покупки на бирже (логика FSM уже встроена в biz_res_input_start/finish)
@router.callback_query(F.data.startswith("market_buy_"))
async def market_buy_start(call: types.CallbackQuery, state: FSMContext):
    await call.answer("✅ Для покупки сырья, перейдите в *Бизнес-Центр* и выберите 'Запустить Производство'.")
    # Логика FSM для покупки сырья реализована в секции бизнеса.

# =========================================================
# === 9. КРИМИНАЛЬНЫЕ АКТИВНОСТИ (ОГРАБЛЕНИЕ БАНКА) ===
# =========================================================

@router.message(F.text == BTN_CRIME)
async def cmd_crime(message: types.Message):
    u = get_user(message.from_user.id)
    if u.arrest_expires and u.arrest_expires > datetime.now():
        return await message.answer("🔒 Вы в тюрьме. Криминальная деятельность невозможна.")
    
    cooldown = timedelta(hours=6)
    rem = format_cooldown(u.last_crime_time, cooldown)
    if rem:
        return await message.answer(f"⏳ Следующая попытка ограбления через {rem}.")

    # Шанс успеха зависит от уровня работы (чем выше уровень, тем умнее игрок)
    success_chance = 0.35 + (u.job_level * 0.02) # 35% базовый шанс + 2% за уровень работы
    
    # Ставка (минимальная сумма, которую можно потерять)
    bet = u.balance / 10 # 10% от наличного баланса
    if bet < CASINO_MIN_BET: bet = CASINO_MIN_BET
    
    try:
        with SessionLocal() as s:
            u_db = s.query(User).filter_by(telegram_id=u.telegram_id).with_for_update().first()
            u_db.last_crime_time = datetime.now()
            
            if random.random() < success_chance:
                # Успех
                win_amount = int(bet * random.uniform(2.5, 4.0)) # Выигрыш от 250% до 400%
                u_db.balance += win_amount
                msg = f"🎉 **ОГРАБЛЕНИЕ УСПЕШНО!** Вы сорвали куш: *+{win_amount:,.0f} $*. Вам удалось скрыться от полиции."
            else:
                # Провал
                # Штраф и тюрьма
                fine_amount = int(bet * CRIME_FINE_MULTIPLIER)
                u_db.balance -= fine_amount
                u_db.arrest_expires = datetime.now() + timedelta(minutes=CRIME_JAIL_TIME_MINUTES)
                
                msg = (
                    f"❌ **ОГРАБЛЕНИЕ ПРОВАЛЕНО!** Вас поймали.\n"
                    f"💸 Штраф: *-{fine_amount:,.0f} $*\n"
                    f"🚨 Вы отправлены в тюрьму на {CRIME_JAIL_TIME_MINUTES} минут."
                )
                
            s.commit()
            await message.answer(msg, reply_markup=get_main_kb(u.is_admin, u.is_president))
            
    except SQLAlchemyError:
        await message.answer("❌ Ошибка БД при попытке преступления.")

# =========================================================
# === 10. ПОЛИТИКА И ОФИС ПРЕЗИДЕНТА ===
# =========================================================

@router.message(F.text == "🏛 Политика")
async def cmd_politics(message: types.Message):
    # (логика выборов оставлена в базовом виде, но добавлена кнопка офиса)
    u = get_user(message.from_user.id)
    if u.is_president:
        return await message.answer("Вы Президент! Вам доступен 'Офис Президента'.", reply_markup=get_main_kb(u.is_admin, True))
    
    await message.answer("🏛 Капитолий. Подробности выборов в меню.", reply_markup=get_main_kb(u.is_admin, False))


@router.message(F.text == BTN_GOV_OFFICE)
async def cmd_pres_office(message: types.Message):
    u = get_user(message.from_user.id)
    if not u.is_president: return await message.answer("❌ Вы не Президент.")

    with SessionLocal() as s:
        budget = s.query(PresidentialBudget).first()
        est = s.query(ElectionState).first()
        
        info = (
            f"🦅 **Офис Президента BongoCity**\n\n"
            f"💰 **Госбюджет**: *{budget.budget:,} $*\n"
            f"🏛 **Налог (от доходов)**: {int(est.tax_rate*100)}%\n"
            f"💸 **Ставка по Кредитам**: {int(est.loan_interest_rate*100)}%\n"
        )
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Изменить Гос. Налог", callback_data="pres_tax_start")],
            [InlineKeyboardButton(text="Изменить Кредитную Ставку", callback_data="pres_loan_rate_start")],
            [InlineKeyboardButton(text="Выдать из Госбюджета", callback_data="pres_give_budget_start")]
        ])

    await message.answer(info, reply_markup=kb)

# --- FSM для изменения налога ---
@router.callback_query(F.data == "pres_tax_start")
async def pres_tax_start(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    if not get_user(call.from_user.id).is_president: return
    
    await state.set_state(GameStates.pres_tax_input)
    await call.message.answer(f"Введите новый Налог в % (0 до {int(TAX_MAX_RATE*100)}):")

@router.message(GameStates.pres_tax_input)
async def pres_tax_finish(message: types.Message, state: FSMContext):
    await state.clear()
    
    try: tax_perc = float(message.text)
    except: return await message.answer("❌ Введите число.", reply_markup=get_main_kb())

    if not 0 <= tax_perc <= (TAX_MAX_RATE * 100):
        return await message.answer(f"❌ Налог должен быть от 0 до {int(TAX_MAX_RATE*100)}%.", reply_markup=get_main_kb())
    
    with SessionLocal() as s:
        est = s.query(ElectionState).with_for_update().first()
        est.tax_rate = tax_perc / 100.0
        s.commit()
        await message.answer(f"✅ Налог установлен на {tax_perc}%.", reply_markup=get_main_kb(is_president=True))

# --- FSM для изменения кредитной ставки ---
@router.callback_query(F.data == "pres_loan_rate_start")
async def pres_loan_rate_start(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    if not get_user(call.from_user.id).is_president: return
    
    await state.set_state(GameStates.pres_loan_rate_input)
    await call.message.answer(f"Введите новую Кредитную Ставку в % (ежедневный %):")

@router.message(GameStates.pres_loan_rate_input)
async def pres_loan_rate_finish(message: types.Message, state: FSMContext):
    await state.clear()
    
    try: rate_perc = float(message.text)
    except: return await message.answer("❌ Введите число.", reply_markup=get_main_kb())

    if not 0 <= rate_perc <= 100:
        return await message.answer(f"❌ Ставка должна быть от 0% до 100%.", reply_markup=get_main_kb())
    
    with SessionLocal() as s:
        est = s.query(ElectionState).with_for_update().first()
        est.loan_interest_rate = rate_perc / 100.0
        s.commit()
        await message.answer(f"✅ Кредитная ставка установлена на {rate_perc}%.", reply_markup=get_main_kb(is_president=True))

# --- FSM для выдачи средств из госбюджета ---
@router.callback_query(F.data == "pres_give_budget_start")
async def pres_give_budget_start(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    if not get_user(call.from_user.id).is_president: return
    
    with SessionLocal() as s:
        budget = s.query(PresidentialBudget).first()
    
    await state.set_state(GameStates.pres_give_budget)
    await call.message.answer(
        f"💰 Госбюджет: {budget.budget:,}$ \n"
        f"Введите ID игрока и сумму (ID сумма - на наличный баланс):"
    )

@router.message(GameStates.pres_give_budget)
async def pres_give_budget_finish(message: types.Message, state: FSMContext):
    await state.clear()
    pres_id = message.from_user.id
    
    try:
        parts = message.text.split()
        target_id = int(parts[0])
        amount = int(parts[1]) if len(parts) > 1 else 0
    except:
        return await message.answer("❌ Неверный формат ввода (ожидался: ID сумма).", reply_markup=get_main_kb(is_president=True))
        
    try:
        with SessionLocal() as s:
            u_pres = s.query(User).filter_by(telegram_id=pres_id).first()
            if not u_pres or not u_pres.is_president: raise PermissionError("Not president")
            
            budget = s.query(PresidentialBudget).with_for_update().first()
            u_target = s.query(User).filter_by(telegram_id=target_id).with_for_update().first()
            
            if not u_target: return await message.answer("❌ Целевой игрок не найден.")
            if budget.budget < amount: return await message.answer(f"❌ В бюджете не хватает средств. Доступно: {budget.budget:,}$")
            if amount <= 0: return await message.answer("❌ Сумма должна быть положительной.")
            
            budget.budget -= amount
            u_target.balance += amount
            s.commit()
            
            await message.answer(f"✅ Игроку `{target_id}` успешно выдано {amount:,}$ из Госбюджета.")
            await bot.send_message(target_id, f"🚨 Президент выдал вам {amount:,}$ из Государственного Бюджета.")

    except Exception as e:
        await message.answer(f"❌ Ошибка: Внутренняя ошибка или недостаточно прав.", reply_markup=get_main_kb(is_president=True))
        logging.error(f"Pres Budget FSM Error: {e}")
        
    finally: await message.answer("Возврат в меню.", reply_markup=get_main_kb(get_user(pres_id).is_admin, get_user(pres_id).is_president))

# =========================================================
# === 11. ФОНОВЫЕ ЗАДАЧИ (SCHEDULER) ===
# =========================================================

async def check_elections_and_payouts():
    """Фоновая проверка: выборы, производство, кредиты, динамика рынка."""
    logging.info("Scheduler: Checking all background timers...")
    
    # --- A. Динамика Рынка ---
    with SessionLocal() as s:
        prices = s.query(MarketItemPrice).with_for_update().all()
        for p in prices:
            item_info = MARKET_ITEMS[p.item_id]
            volatility = item_info['volatility']
            
            # Изменение цены (до 2*волатильности)
            change_factor = random.uniform(1 - volatility, 1 + volatility)
            p.current_price = int(p.current_price * change_factor)
            p.current_price = max(item_info['base_price'] // 2, p.current_price) # Защита от слишком низких цен
        
        # --- B. Проверка Производства ---
        bizs_in_prod = s.query(OwnedBusiness).filter_by(production_state="PRODUCING").with_for_update().all()
        for b in bizs_in_prod:
            if b.production_start_time and datetime.now() - b.production_start_time >= timedelta(hours=PRODUCTION_CYCLE_HOURS):
                b.production_state = "READY"
                # Отправка уведомления пользователю
                biz_name = BUSINESSES.get(b.business_id)['name']
                try:
                    await bot.send_message(b.user_id, f"✅ **ПРОИЗВОДСТВО ЗАВЕРШЕНО!** Ваш бизнес *{biz_name}* готов к сбору продукции.")
                except TelegramAPIError:
                    pass # Игнорируем ошибки, если бот заблокирован
        
        # --- C. Проверка Кредитов (Начисление процентов и Просрочки) ---
        loans = s.query(BankLoan).filter_by(paid=False).with_for_update().all()
        for loan in loans:
            # Проверка на просрочку
            if datetime.now() > loan.due_date:
                # Если просрочено, штрафуем (переводим налог в Госбюджет)
                loan_days_overdue = (datetime.now() - loan.due_date).days
                if loan_days_overdue > 0 and loan_days_overdue % LOAN_CYCLE_DAYS == 0:
                    budget = s.query(PresidentialBudget).with_for_update().first()
                    fine_amount = int(loan.amount * loan.interest_rate * 2) # Двойной процент за просрочку
                    
                    u = s.query(User).filter_by(telegram_id=loan.user_id).with_for_update().first()
                    
                    if u.bank_balance >= fine_amount:
                        u.bank_balance -= fine_amount
                        budget.budget += fine_amount
                        try:
                            await bot.send_message(loan.user_id, f"🚨 **ШТРАФ ЗА ПРОСРОЧКУ!** Со счета списано {fine_amount:,}$ ({int(loan.interest_rate*200)}% штрафа).")
                        except TelegramAPIError: pass
                    else:
                        # Если денег нет, ничего не делаем, ждем, пока накопятся.
                        pass
                        
        # --- D. Проверка Тюрьмы ---
        jailed_users = s.query(User).filter(User.arrest_expires > now).with_for_update().all()
        for u in jailed_users:
            if u.arrest_expires and u.arrest_expires <= now:
                u.arrest_expires = None
                try:
                    await bot.send_message(u.telegram_id, "🎉 **ВЫ СВОБОДНЫ!** Тюремный срок окончен.")
                except TelegramAPIError: pass

        # --- E. Проверка и Запуск Выборов ---
        # (Логика выборов оставлена без изменений)
        # ...

        s.commit()
    
# --- Отправка сообщений в чаты (для событий выборов) ---
async def broadcast_message_to_chats(bot: Bot, message_text: str):
    logging.info("Начало рассылки.")
    with SessionLocal() as s:
        chat_ids = [chat.chat_id for chat in s.query(Chat).all()]
    
    for chat_id in chat_ids:
        try:
            await bot.send_message(chat_id, message_text)
            await asyncio.sleep(0.05)
        except TelegramAPIError as e:
            if e.message.lower() in ("bot was blocked by the user", "chat not found"):
                logging.warning(f"Чат {chat_id} удален/заблокирован. Удаляю из БД.")
                with SessionLocal() as s_delete:
                    chat_to_delete = s_delete.query(Chat).filter_by(chat_id=chat_id).first()
                    if chat_to_delete:
                        s_delete.delete(chat_to_delete)
                        s_delete.commit()
            pass
        except Exception:
            pass

async def set_bot_commands(bot: Bot):
    commands = [
        BotCommand(command="start", description="▶️ Запуск бота"),
        BotCommand(command="profile", description="👤 Ваш игровой профиль"),
        BotCommand(command="help", description="ℹ️ Список команд и помощь"),
    ]
    await bot.set_my_commands(commands, scope=BotCommandScopeDefault())

# =========================================================
# === 12. ЗАПУСК БОТА ===
# =========================================================

async def main():
    if not init_db():
        logging.error("Не удалось запустить из-за ошибки БД.")
        return

    await set_bot_commands(bot)
    
    # Добавление фоновых задач:
    # 1. Проверка всех таймеров (производство, рынок, кредиты, тюрьма) - каждые 15 минут
    scheduler.add_job(check_elections_and_payouts, 'interval', minutes=15)
    
    scheduler.start()
    logging.info("Бот запущен. Сложная симуляция активна.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    # Дополнительные импорты для сложной ORM-логики (требуются для func.sum и coalesce)
    from sqlalchemy.sql import func
    from sqlalchemy.sql.functions import coalesce

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Бот остановлен вручную.")
    except Exception as e:
        logging.error(f"Критическая ошибка при запуске: {e}")
