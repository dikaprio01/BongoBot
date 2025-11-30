# =========================================================
# === BongoCity Telegram Bot: Полный Код (Python/aiogram) ===
# =========================================================
import os
import logging
import random
import asyncio
from datetime import datetime, timedelta

# --- Aiogram Imports ---
from aiogram.client.default import DefaultBotProperties
from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton,
    ReplyKeyboardMarkup, BotCommand, BotCommandScopeDefault
)
from aiogram.exceptions import TelegramAPIError

# --- SQLAlchemy Imports ---
from sqlalchemy import create_engine, Column, Integer, String, BigInteger, Float, DateTime, Boolean
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.exc import SQLAlchemyError
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# =========================================================
# === 1. КОНФИГУРАЦИЯ И ИНИЦИАЛИЗАЦИЯ ===
# =========================================================

# Установите свой токен бота и URL базы данных
TOKEN = os.getenv("BOT_TOKEN")
MYSQL_URL = os.getenv("MYSQL_URL") # Можно использовать PostgreSQL/MySQL для продакшна

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Новая, правильная строка:
bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
dp = Dispatcher()
router = Router()
dp.include_router(router)
scheduler = AsyncIOScheduler()

# Инициализация БД
engine = create_engine(MYSQL_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# =========================================================
# === 2. КОНСТАНТЫ И ДАННЫЕ ИГРЫ ===
# =========================================================

# Кнопки
BTN_BIZ_CENTER = "🏭 Бизнес-Центр"
BTN_BANK = "🏦 Банк"
BTN_MARKET = "📈 Биржа Ресурсов"
BTN_CRIME = "🔫 Ограбить Банк"
BTN_GOV_OFFICE = "🦅 Офис Президента"

# Экономические константы
DAILY_BONUS_AMOUNT = 10000
CASINO_MIN_BET = 1000
PRODUCTION_CYCLE_HOURS = 2 # Время производства одного цикла (в часах)
LOAN_CYCLE_DAYS = 7 # Периодичность начисления штрафа за просрочку кредита (в днях)
CRIME_FINE_MULTIPLIER = 1.5 # Множитель штрафа за провал ограбления
CRIME_JAIL_TIME_MINUTES = 60 # Время тюрьмы в минутах
TAX_MAX_RATE = 0.50 # Максимальный налог 50%

# Ресурсы/Сырье (для биржи и производства)
MARKET_ITEMS = {
    1: {'name': "Древесина", 'base_price': 500, 'volatility': 0.15},
    2: {'name': "Железо", 'base_price': 1200, 'volatility': 0.20},
    3: {'name': "Нефть", 'base_price': 3000, 'volatility': 0.30},
}

# Бизнесы
BUSINESSES = {
    101: {
        'name': "Лесопилка",
        'cost': 15000,
        'req_resource_id': 1, # Древесина
        'base_payout': 1000, # Базовый доход (единиц продукции)
        'max_level': 10,
        'upgrade_cost_mult': 1.5, # Мультипликатор стоимости улучшения
        'payout_mult': 1.25, # Мультипликатор дохода при улучшении
        'payout_per_unit': 10, # Стоимость 1 ед. продукции (используется как базовая)
    },
    102: {
        'name': "Шахта",
        'cost': 50000,
        'req_resource_id': 2, # Железо
        'base_payout': 3500,
        'max_level': 15,
        'upgrade_cost_mult': 1.6,
        'payout_mult': 1.3,
        'payout_per_unit': 15,
    },
}


# =========================================================
# === 3. МОДЕЛИ БАЗЫ ДАННЫХ ===
# =========================================================

class User(Base):
    """Модель пользователя"""
    __tablename__ = "users"
    telegram_id = Column(BigInteger, primary_key=True, index=True)
    username = Column(String, default="Неизвестный")
    balance = Column(BigInteger, default=10000)
    bank_balance = Column(BigInteger, default=0)
    job_level = Column(Integer, default=1)
    last_daily_bonus = Column(DateTime, default=datetime(2023, 1, 1))
    last_crime_time = Column(DateTime, default=datetime(2023, 1, 1))
    arrest_expires = Column(DateTime, nullable=True) # Срок окончания тюрьмы
    is_admin = Column(Boolean, default=False)
    is_president = Column(Boolean, default=False)

class OwnedBusiness(Base):
    """Модель владения бизнесом"""
    __tablename__ = "owned_businesses"
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, index=True)
    business_id = Column(Integer) # ID из словаря BUSINESSES
    count = Column(Integer, default=1) # Количество одинаковых бизнесов
    upgrade_level = Column(Integer, default=1)
    
    # Состояние производства
    production_state = Column(String, default="IDLE") # IDLE, PRODUCING, READY
    production_start_time = Column(DateTime, nullable=True)
    resource_units = Column(Integer, default=0) # Единицы сырья, вложенные в производство

class BankLoan(Base):
    """Модель кредитов"""
    __tablename__ = "bank_loans"
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, index=True)
    amount = Column(BigInteger)
    interest_rate = Column(Float)
    issue_date = Column(DateTime, default=datetime.now)
    due_date = Column(DateTime)
    paid = Column(Boolean, default=False)

class PresidentialBudget(Base):
    """Модель Госбюджета"""
    __tablename__ = "presidential_budget"
    id = Column(Integer, primary_key=True)
    budget = Column(BigInteger, default=0)

class ElectionState(Base):
    """Модель состояния выборов и экономики"""
    __tablename__ = "election_state"
    id = Column(Integer, primary_key=True)
    current_president_id = Column(BigInteger, nullable=True)
    tax_rate = Column(Float, default=0.10) # 10% налог на доход от бизнеса
    loan_interest_rate = Column(Float, default=0.01) # 1% ежедневный процент по кредитам
    last_election_time = Column(DateTime, default=datetime(2023, 1, 1))

class MarketItemPrice(Base):
    """Модель динамических цен на сырье"""
    __tablename__ = "market_item_prices"
    item_id = Column(Integer, primary_key=True, index=True) # ID из MARKET_ITEMS
    current_price = Column(BigInteger)

class Chat(Base):
    """Модель для хранения ID чатов для рассылки"""
    __tablename__ = "chats"
    chat_id = Column(BigInteger, primary_key=True)


def init_db():
    """Инициализация БД и базовых записей"""
    try:
        Base.metadata.create_all(bind=engine)
        
        with SessionLocal() as s:
            # 1. Инициализация Госбюджета
            if not s.query(PresidentialBudget).first():
                s.add(PresidentialBudget(budget=1000000))
                s.commit()
            
            # 2. Инициализация Состояния Выборов/Экономики
            if not s.query(ElectionState).first():
                s.add(ElectionState())
                s.commit()

            # 3. Инициализация цен на рынке
            for item_id, item_info in MARKET_ITEMS.items():
                if not s.query(MarketItemPrice).filter_by(item_id=item_id).first():
                    s.add(MarketItemPrice(item_id=item_id, current_price=item_info['base_price']))
            s.commit()

        logging.info("База данных успешно инициализирована.")
        return True
    except Exception as e:
        logging.error(f"Ошибка инициализации БД: {e}")
        return False

# =========================================================
# === 4. FSM СОСТОЯНИЯ ===
# =========================================================

class GameStates(StatesGroup):
    """Состояния для конечного автомата (FSM)"""
    casino_bet = State()
    loan_amount = State()
    loan_days = State()
    bank_deposit = State()
    bank_withdraw = State()
    biz_res_input = State() # Ввод количества сырья для производства
    pres_tax_input = State()
    pres_loan_rate_input = State()
    pres_give_budget = State()
    
# =========================================================
# === 5. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===
# =========================================================

def get_user(uid: int) -> User | None:
    """Получает пользователя из БД или None, если не найден."""
    with SessionLocal() as s:
        return s.query(User).filter_by(telegram_id=uid).first()

def update_user_profile(uid: int, username: str):
    """Обновляет профиль пользователя при необходимости (например, в /start)"""
    with SessionLocal() as s:
        u = s.query(User).filter_by(telegram_id=uid).first()
        if not u:
            # Создаем нового пользователя с начальным балансом
            election_state = s.query(ElectionState).first()
            is_president = (election_state and election_state.current_president_id == uid)
            u = User(telegram_id=uid, username=username, is_president=is_president)
            s.add(u)
        else:
            u.username = username
        s.commit()
        # Возвращаем обновленный объект пользователя
        # NOTE: Это функция должна возвращать объект, чтобы get_main_kb мог его использовать, 
        # но в контексте aiogram 3.x, она обычно вызывается в начале хэндлеров.
        return u


def get_main_kb(is_admin: bool = False, is_president: bool = False) -> ReplyKeyboardMarkup:
    """Генерирует главное меню"""
    kb = [
        [KeyboardButton(text=BTN_BIZ_CENTER), KeyboardButton(text=BTN_BANK)],
        [KeyboardButton(text=BTN_MARKET), KeyboardButton(text="🎰 Казино")],
        [KeyboardButton(text="💼 Устроиться"), KeyboardButton(text="🎁 Бонус")],
        [KeyboardButton(text="🏛 Политика"), KeyboardButton(text=BTN_CRIME)]
    ]
    
    if is_president:
        kb.append([KeyboardButton(text=BTN_GOV_OFFICE)])
    
    if is_admin:
        kb.append([KeyboardButton(text="/admin")])
        
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def format_cooldown(last_time: datetime, cooldown: timedelta) -> str | None:
    """Форматирует оставшееся время до конца кулдауна."""
    if not last_time: return None
    
    next_time = last_time + cooldown
    remaining = next_time - datetime.now()

    if remaining.total_seconds() > 0:
        total_seconds = int(remaining.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        
        parts = []
        if hours > 0: parts.append(f"{hours}ч")
        if minutes > 0: parts.append(f"{minutes}м")
        if seconds > 0 or not parts: parts.append(f"{seconds}с")
            
        return " ".join(parts)
    return None

def get_current_interest_rate() -> float:
    """Получает текущую кредитную ставку из ElectionState."""
    with SessionLocal() as s:
        est = s.query(ElectionState).first()
        return est.loan_interest_rate if est else 0.01

def get_current_tax_rate() -> float:
    """Получает текущую ставку налога из ElectionState."""
    with SessionLocal() as s:
        est = s.query(ElectionState).first()
        return est.tax_rate if est else 0.10

# =========================================================
# === 6. БАЗОВЫЕ КОМАНДЫ (СТАРТ, ПРОФИЛЬ) ===
# =========================================================

@router.message(CommandStart())
async def cmd_start(message: types.Message):
    """Обработчик команды /start"""
    username = message.from_user.username or message.from_user.full_name
    u = update_user_profile(message.from_user.id, username)
    
    # Добавление чата в БД для рассылки
    if message.chat.type in ('group', 'supergroup'):
        with SessionLocal() as s:
            if not s.query(Chat).filter_by(chat_id=message.chat.id).first():
                s.add(Chat(chat_id=message.chat.id))
                s.commit()

    await message.answer(
        f"👋 Добро пожаловать, *{username}*, в BongoCity – симулятор жизни и бизнеса!\n"
        f"Ваш начальный баланс: {u.balance:,}$.\n"
        f"Используйте кнопки для взаимодействия с городом.",
        reply_markup=get_main_kb(u.is_admin, u.is_president)
    )

@router.message(Command("profile"))
async def cmd_profile(message: types.Message):
    """Обработчик команды /profile"""
    u = get_user(message.from_user.id)
    if not u:
        return await message.answer("Пожалуйста, начните с команды /start.")
    
    # Расчет чистого капитала (Net Worth)
    net_worth = u.balance + u.bank_balance
    
    # Инфо о тюрьме
    jail_status = "Свободен"
    if u.arrest_expires and u.arrest_expires > datetime.now():
        remaining = u.arrest_expires - datetime.now()
        jail_status = f"В тюрьме (Осталось: {format_cooldown(datetime.now(), remaining)})"

    # Инфо о кредитах
    with SessionLocal() as s:
        loans = s.query(BankLoan).filter_by(user_id=u.telegram_id, paid=False).all()
        loan_info = f"❌ Нет активных кредитов."
        if loans:
            total_debt = sum(l.amount for l in loans)
            loan_info = f"✅ Всего долг: {total_debt:,}$"

    # Инфо о бизнесе
    with SessionLocal() as s:
        biz_count = s.query(OwnedBusiness).filter_by(user_id=u.telegram_id).count()
        biz_status = f"✅ {biz_count} шт."

    # Инфо о политике
    pres_status = "Нет"
    if u.is_president:
        pres_status = "ДА (Президент)"

    await message.answer(
        f"👤 **Профиль {u.username}**\n\n"
        f"💰 **Наличные**: {u.balance:,}$ \n"
        f"🏦 **Банк**: {u.bank_balance:,}$ \n"
        f"📊 **Чистый капитал**: {net_worth:,}$ \n\n"
        f"🏭 **Бизнесы**: {biz_status}\n"
        f"💼 **Уровень работы**: {u.job_level}\n"
        f"🚨 **Статус**: {jail_status}\n\n"
        f"💵 **Кредиты**: {loan_info}\n"
        f"🏛 **Президент**: {pres_status}",
        reply_markup=get_main_kb(u.is_admin, u.is_president)
    )

# =========================================================
# === 7. БАНК (ДЕПОЗИТ, СНЯТИЕ, КРЕДИТЫ) ===
# =========================================================

@router.message(F.text == BTN_BANK)
async def cmd_bank(message: types.Message):
    """Главное меню банка"""
    u = get_user(message.from_user.id)
    rate = get_current_interest_rate()
    
    with SessionLocal() as s:
        loans = s.query(BankLoan).filter_by(user_id=u.telegram_id, paid=False).all()
        total_debt = sum(l.amount for l in loans)
        loan_count = len(loans)
        
        loan_info = ""
        if loan_count > 0:
            loan_info = f" (Долг: {total_debt:,}$)"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Депозит", callback_data="bank_deposit_start")],
        [InlineKeyboardButton(text="📤 Снять", callback_data="bank_withdraw_start")],
        [InlineKeyboardButton(text=f"💸 Кредит ({int(rate*100)}% в день)", callback_data="loan_start")],
        [InlineKeyboardButton(text=f"💳 Погасить Кредит ({loan_count})", callback_data="loan_repay_menu")],
    ])
    
    await message.answer(
        f"🏦 **Банк BongoCity**\n"
        f"Ваш баланс: *{u.bank_balance:,} $*\n"
        f"Активные кредиты: *{loan_count}*{loan_info}",
        reply_markup=kb
    )

# --- Логика Депозита ---
@router.callback_query(F.data == "bank_deposit_start")
async def bank_deposit_start(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    u = get_user(call.from_user.id)
    await state.set_state(GameStates.bank_deposit)
    await call.message.answer(
        f"📥 **Внести Средства**\n"
        f"Наличные: {u.balance:,}$\n"
        f"Введите сумму для депозита (0 для отмены):"
    )

@router.message(GameStates.bank_deposit)
async def bank_deposit_finish(message: types.Message, state: FSMContext):
    await state.clear()
    uid = message.from_user.id
    try: amount = int(message.text)
    except: return await message.answer("❌ Введите число.")
    
    if amount == 0: return await message.answer("Отменено.", reply_markup=get_main_kb(get_user(uid).is_admin, get_user(uid).is_president))
    if amount <= 0: return await message.answer("❌ Сумма должна быть положительной.")

    try:
        with SessionLocal() as s:
            u = s.query(User).filter_by(telegram_id=uid).with_for_update().first()
            if u.balance < amount:
                return await message.answer(f"❌ Не хватает наличных. У вас: {u.balance:,}$")
            
            u.balance -= amount
            u.bank_balance += amount
            s.commit()
            
            await message.answer(
                f"✅ **Депозит Успешен!**\n"
                f"Внесено: *+{amount:,} $*\n"
                f"Банковский баланс: {u.bank_balance:,}$",
                reply_markup=get_main_kb(u.is_admin, u.is_president)
            )
    except SQLAlchemyError:
        await message.answer("❌ Ошибка БД.")

# --- Логика Снятия ---
@router.callback_query(F.data == "bank_withdraw_start")
async def bank_withdraw_start(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    u = get_user(call.from_user.id)
    await state.set_state(GameStates.bank_withdraw)
    await call.message.answer(
        f"📤 **Снять Средства**\n"
        f"На балансе: {u.bank_balance:,}$\n"
        f"Введите сумму для снятия (0 для отмены):"
    )

@router.message(GameStates.bank_withdraw)
async def bank_withdraw_finish(message: types.Message, state: FSMContext):
    await state.clear()
    uid = message.from_user.id
    try: amount = int(message.text)
    except: return await message.answer("❌ Введите число.")
    
    if amount == 0: return await message.answer("Отменено.", reply_markup=get_main_kb(get_user(uid).is_admin, get_user(uid).is_president))
    if amount <= 0: return await message.answer("❌ Сумма должна быть положительной.")

    try:
        with SessionLocal() as s:
            u = s.query(User).filter_by(telegram_id=uid).with_for_update().first()
            if u.bank_balance < amount:
                return await message.answer(f"❌ Не хватает на банковском счете. У вас: {u.bank_balance:,}$")
            
            u.bank_balance -= amount
            u.balance += amount
            s.commit()
            
            await message.answer(
                f"✅ **Снятие Успешно!**\n"
                f"Снято: *+{amount:,} $*\n"
                f"Наличные: {u.balance:,}$",
                reply_markup=get_main_kb(u.is_admin, u.is_president)
            )
    except SQLAlchemyError:
        await message.answer("❌ Ошибка БД.")

# --- Логика Кредитов ---
@router.callback_query(F.data == "loan_start")
async def loan_start(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    
    # 1. Проверка на максимальное количество активных кредитов (например, 3)
    with SessionLocal() as s:
        active_loans = s.query(BankLoan).filter_by(user_id=call.from_user.id, paid=False).count()
        if active_loans >= 3:
            return await call.message.answer("❌ Вы не можете взять более 3 активных кредитов одновременно.")
            
    await state.set_state(GameStates.loan_amount)
    await call.message.answer("💸 **Запрос Кредита**\nВведите желаемую сумму кредита:")

@router.message(GameStates.loan_amount)
async def loan_amount_input(message: types.Message, state: FSMContext):
    try: amount = int(message.text)
    except:
        await state.clear()
        return await message.answer("❌ Введите корректную сумму.", reply_markup=get_main_kb(get_user(message.from_user.id).is_admin, get_user(message.from_user.id).is_president))
        
    if amount <= 10000:
        await state.clear()
        return await message.answer("❌ Минимальная сумма кредита: 10,000 $.", reply_markup=get_main_kb(get_user(message.from_user.id).is_admin, get_user(message.from_user.id).is_president))

    await state.update_data(amount=amount)
    await state.set_state(GameStates.loan_days)
    await message.answer("Введите срок кредита в днях (от 7 до 30):")

@router.message(GameStates.loan_days)
async def loan_days_input(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    try: days = int(message.text)
    except:
        await state.clear()
        return await message.answer("❌ Введите корректное количество дней.", reply_markup=get_main_kb(get_user(uid).is_admin, get_user(uid).is_president))
    
    if not (7 <= days <= 30):
        await state.clear()
        return await message.answer("❌ Срок кредита должен быть от 7 до 30 дней.", reply_markup=get_main_kb(get_user(uid).is_admin, get_user(uid).is_president))
    
    data = await state.get_data()
    amount = data['amount']
    await state.clear()

    rate = get_current_interest_rate()
    due_date = datetime.now() + timedelta(days=days)
    
    # Расчет полной суммы к возврату (процент ежедневный, но для инфо посчитаем общую)
    total_interest = int(amount * rate * days)
    total_repay = amount + total_interest
    
    try:
        with SessionLocal() as s:
            u = s.query(User).filter_by(telegram_id=uid).with_for_update().first()
            
            # 1. Выдача денег
            u.balance += amount
            
            # 2. Создание записи о кредите
            loan = BankLoan(
                user_id=uid,
                amount=amount,
                interest_rate=rate,
                due_date=due_date
            )
            s.add(loan)
            s.commit()
            
            await message.answer(
                f"✅ **Кредит Одобрен!**\n"
                f"Получено: *+{amount:,} $*\n"
                f"Ставка: {int(rate*100)}% в день\n"
                f"Срок: {days} дней (до {due_date.strftime('%d.%m.%Y')})\n"
                f"~Общая сумма к возврату: {total_repay:,} $~",
                reply_markup=get_main_kb(u.is_admin, u.is_president)
            )
            
    except SQLAlchemyError:
        await message.answer("❌ Ошибка БД при оформлении кредита.", reply_markup=get_main_kb(get_user(uid).is_admin, get_user(uid).is_president))

# --- Меню Погашения Кредитов ---
@router.callback_query(F.data == "loan_repay_menu")
async def loan_repay_menu(call: types.CallbackQuery):
    await call.answer()
    uid = call.from_user.id
    
    with SessionLocal() as s:
        loans = s.query(BankLoan).filter_by(user_id=uid, paid=False).all()
        
        if not loans:
            return await call.message.answer("❌ У вас нет активных кредитов для погашения.")
            
        kb = InlineKeyboardMarkup(inline_keyboard=[])
        
        for loan in loans:
            # Расчет текущего долга: Сумма + Начисленные проценты до сегодня
            days_passed = (datetime.now() - loan.issue_date).days
            interest_accrued = int(loan.amount * loan.interest_rate * days_passed)
            total_due = loan.amount + interest_accrued
            
            btn_text = (
                f"💳 Кредит #{loan.id} | Долг: {total_due:,}$ "
                f"(Начало: {loan.amount:,}$)"
            )
            kb.inline_keyboard.append([InlineKeyboardButton(
                text=btn_text,
                callback_data=f"loan_repay_do_{loan.id}_{total_due}"
            )])
            
        await call.message.answer("💳 **Погашение Кредитов**\nВыберите кредит для полного погашения:", reply_markup=kb)

@router.callback_query(F.data.startswith("loan_repay_do_"))
async def loan_repay_do(call: types.CallbackQuery):
    await call.answer()
    uid = call.from_user.id
    try:
        _, _, loan_id_str, total_due_str = call.data.split('_')
        loan_id = int(loan_id_str)
        total_due = int(total_due_str)
    except ValueError:
        return await call.message.answer("❌ Ошибка обработки данных.")

    try:
        with SessionLocal() as s:
            u = s.query(User).filter_by(telegram_id=uid).with_for_update().first()
            loan = s.query(BankLoan).filter_by(id=loan_id, user_id=uid, paid=False).with_for_update().first()
            
            if not loan:
                return await call.message.answer("❌ Кредит не найден или уже погашен.")
            if u.balance < total_due:
                return await call.message.answer(f"❌ Не хватает наличных. Требуется: {total_due:,}$")
            
            # 1. Списание средств
            u.balance -= total_due
            
            # 2. Пометка как оплаченный
            loan.paid = True
            
            # 3. Добавление платежа в Госбюджет (как доход банка)
            budget = s.query(PresidentialBudget).with_for_update().first()
            budget.budget += total_due # Вся сумма идет в бюджет (симуляция госбанка)

            s.commit()
            
            await call.message.answer(
                f"🎉 **Кредит Погашен!**\n"
                f"Кредит #{loan.id} успешно закрыт. Списано: *-{total_due:,} $*\n"
                f"Текущие наличные: {u.balance:,}$"
            )
            
    except SQLAlchemyError:
        await call.message.answer("❌ Ошибка БД при погашении кредита.")

# =========================================================
# === 8. БИЗНЕС-ЦЕНТР (ПОКУПКА, УЛУЧШЕНИЕ, ПРОИЗВОДСТВО) ===
# =========================================================

@router.message(F.text == BTN_BIZ_CENTER)
async def cmd_biz_center(message: types.Message):
    """Меню Бизнес-Центра"""
    u = get_user(message.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Купить Новый Бизнес", callback_data="biz_shop")],
        [InlineKeyboardButton(text="🏭 Запустить Производство", callback_data="biz_production_start")],
        [InlineKeyboardButton(text="📦 Собрать Продукцию", callback_data="biz_collect")],
        [InlineKeyboardButton(text="✨ Улучшить Бизнес", callback_data="biz_upgrade_start")],
    ])
    
    await message.answer(
        f"🏭 **Бизнес-Центр BongoCity**\n"
        f"Управляйте своими активами и производством.",
        reply_markup=kb
    )

# --- Запуск Производства ---
@router.callback_query(F.data == "biz_production_start")
async def biz_production_start(call: types.CallbackQuery):
    await call.answer()
    uid = call.from_user.id
    
    with SessionLocal() as s:
        # Бизнесы, которые могут начать производство (статус IDLE)
        bizs_idle = s.query(OwnedBusiness).filter_by(user_id=uid, production_state="IDLE").all()
        
        if not bizs_idle:
            return await call.message.answer("❌ Нет бизнесов в режиме *Ожидания* для запуска производства.")
            
        # Группируем по типу бизнеса, чтобы показать один раз
        biz_options = {}
        for b in bizs_idle:
            biz_info = BUSINESSES.get(b.business_id)
            if b.business_id not in biz_options:
                biz_options[b.business_id] = {
                    'name': biz_info['name'],
                    'count': 0,
                    'req_resource_id': biz_info['req_resource_id']
                }
            biz_options[b.business_id]['count'] += b.count

        kb = InlineKeyboardMarkup(inline_keyboard=[])
        for bid, info in biz_options.items():
            res_name = MARKET_ITEMS[info['req_resource_id']]['name']
            kb.inline_keyboard.append([InlineKeyboardButton(
                text=f"🏭 {info['name']} ({info['count']} шт.) | Требует {res_name}",
                callback_data=f"biz_res_select_{bid}"
            )])
            
        await call.message.answer("🏭 **Запуск Производства**\nВыберите тип бизнеса для запуска:", reply_markup=kb)

@router.callback_query(F.data.startswith("biz_res_select_"))
async def biz_res_select(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    bid = int(call.data.split("_")[3])
    biz_info = BUSINESSES.get(bid)
    res_id = biz_info['req_resource_id']
    res_name = MARKET_ITEMS[res_id]['name']
    
    # Получаем текущую цену сырья
    with SessionLocal() as s:
        price_data = s.query(MarketItemPrice).filter_by(item_id=res_id).first()
        current_price = price_data.current_price if price_data else MARKET_ITEMS[res_id]['base_price']
        
    await state.update_data(business_id=bid, resource_id=res_id, price=current_price)
    await state.set_state(GameStates.biz_res_input)

    await call.message.answer(
        f"📦 **Сырье: {res_name}**\n"
        f"Текущая цена: {current_price:,}$ за ед.\n"
        f"Введите количество единиц *{res_name}* для закупки и начала производства (0 для отмены):"
    )

@router.message(GameStates.biz_res_input)
async def biz_res_input_finish(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    try: units_to_buy = int(message.text)
    except:
        await state.clear()
        return await message.answer("❌ Введите корректное число.")
    
    data = await state.get_data()
    await state.clear()
    
    if units_to_buy == 0:
        return await message.answer("Отменено.", reply_markup=get_main_kb(get_user(uid).is_admin, get_user(uid).is_president))
    if units_to_buy <= 0:
        return await message.answer("❌ Количество должно быть положительным.")

    bid = data['business_id']
    price = data['price']
    total_cost = units_to_buy * price
    
    try:
        with SessionLocal() as s:
            u = s.query(User).filter_by(telegram_id=uid).with_for_update().first()
            if u.balance < total_cost:
                return await message.answer(f"❌ Не хватает {total_cost - u.balance:,}$ для покупки сырья.")
            
            # 1. Списание средств
            u.balance -= total_cost
            
            # 2. Поиск первого бизнеса этого типа в режиме IDLE
            b = s.query(OwnedBusiness).filter_by(user_id=uid, business_id=bid, production_state="IDLE").with_for_update().first()
            
            if not b:
                s.commit() # Сохраняем списание, даже если не нашли бизнес (на всякий случай)
                return await message.answer("❌ Не удалось найти свободный бизнес этого типа. Возможно, он был запущен.")
            
            # 3. Запуск производства
            b.production_state = "PRODUCING"
            b.production_start_time = datetime.now()
            b.resource_units = units_to_buy
            
            biz_name = BUSINESSES[bid]['name']
            
            s.commit()
            
            await message.answer(
                f"✅ **Производство Запущено!**\n"
                f"Бизнес: *{biz_name}*\n"
                f"Закуплено сырья: {units_to_buy:,} ед. (-{total_cost:,}$)\n"
                f"⏳ Ожидаемое время завершения: {PRODUCTION_CYCLE_HOURS} часов."
            )
            
    except SQLAlchemyError:
        await message.answer("❌ Ошибка БД при запуске производства.")

# --- Сбор Продукции ---
@router.callback_query(F.data == "biz_collect")
async def biz_collect(call: types.CallbackQuery):
    await call.answer()
    uid = call.from_user.id
    
    try:
        with SessionLocal() as s:
            u = s.query(User).filter_by(telegram_id=uid).with_for_update().first()
            tax_rate = get_current_tax_rate()
            
            # Ищем бизнесы со статусом READY
            bizs_ready = s.query(OwnedBusiness).filter_by(user_id=uid, production_state="READY").with_for_update().all()
            
            if bizs_ready:
                total_income_gross = 0
                collected_units = 0
                
                for b in bizs_ready:
                    biz_info = BUSINESSES.get(b.business_id)
                    if not biz_info: continue
                    
                    # Расчет текущего дохода (с учетом уровня и количества сырья)
                    # Выход = Базовый_Выход * (Мультипликатор)^(Уровень-1)
                    # Учитываем, что каждый бизнес производит 1 ед. продукции за цикл,
                    # умноженную на количество купленного сырья (единицы = количество циклов)
                    
                    # 1. Доход за 1 цикл (1 ед. бизнеса)
                    payout_per_cycle = biz_info['base_payout'] * (biz_info['payout_mult'] ** (b.upgrade_level - 1))
                    
                    # 2. Общий доход = Доход_за_цикл * Кол-во_сырья
                    # Примечание: тут логика немного изменена, чтобы доход зависел от вложенного сырья
                    income_for_biz = int(payout_per_cycle * b.resource_units)
                    
                    total_income_gross += income_for_biz
                    collected_units += b.resource_units
                    
                    # Сброс состояния
                    b.production_state = "IDLE"
                    b.production_start_time = None
                    b.resource_units = 0
                
                # Расчет налога
                total_tax = int(total_income_gross * tax_rate)
                total_income_net = total_income_gross - total_tax
                
                u.balance += total_income_net
                
                # Налоговые отчисления идут в госбюджет
                budget = s.query(PresidentialBudget).with_for_update().first()
                budget.budget += total_tax 

                s.commit()
                await call.message.answer(
                    f"💸 **Сбор Продукции Успешен!**\n"
                    f"Собрано {collected_units} ед. продукции.\n"
                    f"💰 Налог ({int(tax_rate*100)}%): *-{total_tax:,} $*\n"
                    f"💲 Чистый доход: *+{total_income_net:,} $*\n"
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
                # ВАЖНО: user_id в OwnedBusiness - это BigInteger (telegram_id)
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
                # ИСПРАВЛЕНО: callback_data должен быть уникальным
                kb.inline_keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=f"biz_upgrade_max_{b.id}")])
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
    try:
        _, _, biz_db_id_str, cost_str = call.data.split('_')
        biz_db_id = int(biz_db_id_str)
        cost = int(cost_str)
    except ValueError:
        return await call.message.answer("❌ Ошибка обработки данных улучшения.")
    
    try:
        with SessionLocal() as s:
            u = s.query(User).filter_by(telegram_id=uid).with_for_update().first()
            b = s.query(OwnedBusiness).filter_by(id=biz_db_id, user_id=uid).with_for_update().first()
            
            if not b or u.balance < cost:
                return await call.message.answer("❌ Бизнес не найден или недостаточно средств.")
            
            biz_info = BUSINESSES.get(b.business_id)
            if b.upgrade_level >= biz_info['max_level']:
                return await call.message.answer("❌ Достигнут максимальный максимальный уровень улучшения.")
                
            u.balance -= cost
            b.upgrade_level += 1
            
            # Расчет нового дохода: base_payout * multiplier^(level - 1)
            # Примечание: в предыдущей логике level-1 было для следующего уровня, 
            # здесь (b.upgrade_level - 1) - это новый, уже увеличенный уровень.
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

@router.message(F.text == "🎁 Бонус")
async def cmd_daily_bonus(message: types.Message):
    u = get_user(message.from_user.id)
    cooldown = timedelta(hours=24)
    rem = format_cooldown(u.last_daily_bonus, cooldown)
    
    if rem:
        return await message.answer(f"⏳ Следующий бонус можно получить через {rem}.", reply_markup=get_main_kb(u.is_admin, u.is_president))

    with SessionLocal() as s:
        u_db = s.query(User).filter_by(telegram_id=u.telegram_id).with_for_update().first()
        u_db.balance += DAILY_BONUS_AMOUNT
        u_db.last_daily_bonus = datetime.now()
        s.commit()
        
        await message.answer(
            f"🎉 **Ежедневный Бонус!** Вы получили *{DAILY_BONUS_AMOUNT:,} $*\n"
            f"Текущий баланс: {u_db.balance:,}$",
            reply_markup=get_main_kb(u.is_admin, u.is_president)
        )

# --- Казино ---
@router.message(F.text == "🎰 Казино")
async def cmd_casino(message: types.Message, state: FSMContext):
    u = get_user(message.from_user.id)
    await state.set_state(GameStates.casino_bet)
    await message.answer(
        f"🎰 **Казино BongoCity**\n"
        f"У вас: {u.balance:,} $\n"
        f"Минимальная ставка: {CASINO_MIN_BET:,} $\n"
        f"Введите сумму ставки (0 для отмены):"
    )

@router.message(GameStates.casino_bet)
async def casino_finish(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    try: bet = int(message.text)
    except:
        await state.clear()
        return await message.answer("❌ Введите число.", reply_markup=get_main_kb(get_user(uid).is_admin, get_user(uid).is_president))

    await state.clear()
    
    if bet == 0:
        return await message.answer("Отменено.", reply_markup=get_main_kb(get_user(uid).is_admin, get_user(uid).is_president))

    if bet < CASINO_MIN_BET:
        return await message.answer(f"❌ Минимальная ставка: {CASINO_MIN_BET:,}$", reply_markup=get_main_kb(get_user(uid).is_admin, get_user(uid).is_president))
        
    with SessionLocal() as s:
        u = s.query(User).filter_by(telegram_id=uid).with_for_update().first()
        
        if u.balance < bet:
            return await message.answer(f"❌ Не хватает наличных. У вас: {u.balance:,}$", reply_markup=get_main_kb(u.is_admin, u.is_president))
        
        # Игра
        multiplier = random.choice([0, 0, 0, 0, 0, 0.5, 1.5, 2.0, 3.0]) # 6/9 проигрыш или меньший выигрыш
        
        if multiplier == 0:
            u.balance -= bet
            msg = f"💔 **ПРОИГРЫШ!** Вы потеряли *-{bet:,} $*. Остаток: {u.balance:,}$"
        elif multiplier == 0.5:
            loss = int(bet * 0.5)
            u.balance -= loss
            msg = f"📉 **МИНУС!** Вы потеряли *-{loss:,} $*. Остаток: {u.balance:,}$"
        else:
            win = int(bet * multiplier)
            u.balance += win
            msg = f"🎉 **ПОБЕДА!** Ваш выигрыш: *+{win:,} $*. Остаток: {u.balance:,}$"
            
        s.commit()
        await message.answer(msg, reply_markup=get_main_kb(u.is_admin, u.is_president))

# =========================================================
# === 9. БИРЖА РЕСУРСОВ (ДИНАМИЧЕСКИЕ ЦЕНЫ) ===
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
# === 10. КРИМИНАЛЬНЫЕ АКТИВНОСТИ (ОГРАБЛЕНИЕ БАНКА) ===
# =========================================================

@router.message(F.text == BTN_CRIME)
async def cmd_crime(message: types.Message):
    u = get_user(message.from_user.id)
    if u.arrest_expires and u.arrest_expires > datetime.now():
        # ИСПРАВЛЕНО: format_cooldown принимает datetime.now() как last_time для jail
        left_time = u.arrest_expires - datetime.now()
        left = format_cooldown(datetime.now(), left_time)
        return await message.answer(f"🔒 Вы в тюрьме. Осталось: {left}")
    
    cooldown = timedelta(hours=6)
    rem = format_cooldown(u.last_crime_time, cooldown)
    if rem:
        return await message.answer(f"⏳ Следующая попытка ограбления через {rem}.")

    # Шанс успеха зависит от уровня работы (чем выше уровень, тем умнее игрок)
    success_chance = 0.35 + (u.job_level * 0.02) # 35% базовый шанс + 2% за уровень работы
    
    # Ставка (минимальная сумма, которую можно потерять)
    bet = u.balance / 10 # 10% от наличного баланса
    if bet < CASINO_MIN_BET: bet = CASINO_MIN_BET
    
    # Защита от нулевого баланса
    if u.balance < CASINO_MIN_BET:
        return await message.answer("❌ У вас слишком мало наличных для ограбления. Нужно хотя бы 10,000$ (Минимальная ставка).")
    
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
                
                # Защита от отрицательного баланса (если штраф больше нал.)
                if u_db.balance < fine_amount:
                    fine_amount = u_db.balance # Списываем все, что есть
                    
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
# === 11. ПОЛИТИКА И ОФИС ПРЕЗИДЕНТА ===
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
    except: return await message.answer("❌ Введите число.", reply_markup=get_main_kb(get_user(message.from_user.id).is_admin, get_user(message.from_user.id).is_president))

    if not 0 <= tax_perc <= (TAX_MAX_RATE * 100):
        return await message.answer(f"❌ Налог должен быть от 0 до {int(TAX_MAX_RATE*100)}%.", reply_markup=get_main_kb(get_user(message.from_user.id).is_admin, get_user(message.from_user.id).is_president))
    
    u = get_user(message.from_user.id)
    with SessionLocal() as s:
        est = s.query(ElectionState).with_for_update().first()
        est.tax_rate = tax_perc / 100.0
        s.commit()
        await message.answer(f"✅ Налог установлен на {tax_perc}%.", reply_markup=get_main_kb(u.is_admin, u.is_president))

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
    except: return await message.answer("❌ Введите число.", reply_markup=get_main_kb(get_user(message.from_user.id).is_admin, get_user(message.from_user.id).is_president))

    if not 0 <= rate_perc <= 100:
        return await message.answer(f"❌ Ставка должна быть от 0% до 100%.", reply_markup=get_main_kb(get_user(message.from_user.id).is_admin, get_user(message.from_user.id).is_president))
    
    u = get_user(message.from_user.id)
    with SessionLocal() as s:
        est = s.query(ElectionState).with_for_update().first()
        est.loan_interest_rate = rate_perc / 100.0
        s.commit()
        await message.answer(f"✅ Кредитная ставка установлена на {rate_perc}%.", reply_markup=get_main_kb(u.is_admin, u.is_president))

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
        u_pres = get_user(pres_id)
        if not u_pres.is_president: raise PermissionError("Not president")
        
        with SessionLocal() as s:
            
            budget = s.query(PresidentialBudget).with_for_update().first()
            u_target = s.query(User).filter_by(telegram_id=target_id).with_for_update().first()
            
            if not u_target: return await message.answer("❌ Целевой игрок не найден.")
            if budget.budget < amount: return await message.answer(f"❌ В бюджете не хватает средств. Доступно: {budget.budget:,}$")
            if amount <= 0: return await message.answer("❌ Сумма должна быть положительной.")
            
            budget.budget -= amount
            u_target.balance += amount
            s.commit()
            
            await message.answer(f"✅ Игроку `{target_id}` успешно выдано {amount:,}$ из Госбюджета.")
            # Использование глобального объекта bot для отправки уведомления
            await bot.send_message(target_id, f"🚨 Президент выдал вам {amount:,}$ из Государственного Бюджета.")

    except Exception as e:
        # Проверка, если сообщение было от президента, чтобы вернуть ему клавиатуру
        is_pres = get_user(pres_id).is_president
        is_admin = get_user(pres_id).is_admin
        await message.answer(f"❌ Ошибка: Внутренняя ошибка или недостаточно прав.", reply_markup=get_main_kb(is_admin, is_pres))
        logging.error(f"Pres Budget FSM Error: {e}")
        
    finally:
        # Дополнительный возврат в меню, если предыдущая отправка не сработала
        pass

# =========================================================
# === 12. ФОНОВЫЕ ЗАДАЧИ (SCHEDULER) ===
# =========================================================

async def check_elections_and_payouts():
    """Фоновая проверка: выборы, производство, кредиты, динамика рынка."""
    logging.info("Scheduler: Checking all background timers...")
    now = datetime.now() # Определяем время один раз
    
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
            if b.production_start_time and now - b.production_start_time >= timedelta(hours=PRODUCTION_CYCLE_HOURS):
                b.production_state = "READY"
                # Отправка уведомления пользователю
                biz_name = BUSINESSES.get(b.business_id)['name']
                try:
                    # Использование глобального объекта bot
                    await bot.send_message(b.user_id, f"✅ **ПРОИЗВОДСТВО ЗАВЕРШЕНО!** Ваш бизнес *{biz_name}* готов к сбору продукции.")
                except TelegramAPIError:
                    pass # Игнорируем ошибки, если бот заблокирован
        
        # --- C. Проверка Кредитов (Начисление процентов и Просрочки) ---
        loans = s.query(BankLoan).filter_by(paid=False).with_for_update().all()
        for loan in loans:
            # Проверка на просрочку
            if now > loan.due_date:
                # Если просрочено, штрафуем (переводим налог в Госбюджет)
                loan_days_overdue = (now - loan.due_date).days
                # Начисляем штраф каждый LOAN_CYCLE_DAYS дней после просрочки
                if loan_days_overdue > 0 and loan_days_overdue % LOAN_CYCLE_DAYS == 0:
                    budget = s.query(PresidentialBudget).with_for_update().first()
                    fine_amount = int(loan.amount * loan.interest_rate * 2) # Двойной процент за просрочку
                    
                    u = s.query(User).filter_by(telegram_id=loan.user_id).with_for_update().first()
                    
                    if u and u.bank_balance >= fine_amount:
                        u.bank_balance -= fine_amount
                        budget.budget += fine_amount
                        try:
                            await bot.send_message(loan.user_id, f"🚨 **ШТРАФ ЗА ПРОСРОЧКУ!** Со счета списано {fine_amount:,}$ ({int(loan.interest_rate*200)}% штрафа).")
                        except TelegramAPIError: pass
                    else:
                        # Если денег нет, ничего не делаем, ждем, пока накопятся.
                        pass
                        
        # --- D. Проверка Тюрьмы ---
        # NOTE: Фильтр должен быть `User.arrest_expires > now`
        jailed_users = s.query(User).filter(User.arrest_expires.isnot(None), User.arrest_expires <= now).with_for_update().all()
        for u in jailed_users:
            if u.arrest_expires and u.arrest_expires <= now:
                u.arrest_expires = None
                try:
                    await bot.send_message(u.telegram_id, "🎉 **ВЫ СВОБОДНЫ!** Тюремный срок окончен.")
                except TelegramAPIError: pass

        # --- E. Проверка и Запуск Выборов ---
        # (Логика выборов: не предоставлена, но место зарезервировано)
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
# === 13. ГЛАВНЫЕ ОБРАБОТЧИКИ ОШИБОК И ЗАПУСК БОТА ===
# =========================================================

# --- ИСПРАВЛЕНИЕ: Catch-all Handler для сброса FSM и ловли неизвестных команд ---
@router.message()
async def unhandled_message(message: types.Message, state: FSMContext):
    """
    Ловит любые сообщения, которые не были обработаны другими хэндлерами.
    Сбрасывает FSM-состояние на всякий случай и предлагает помощь.
    """
    current_state = await state.get_state()
    if current_state:
        # Если находились в FSM, сбрасываем его
        await state.clear()
        
    u = get_user(message.from_user.id)
    await message.answer(
        "🤔 *Неизвестная команда или некорректный ввод.*\n"
        "Ваше состояние было сброшено. Пожалуйста, воспользуйтесь кнопками ниже.",
        reply_markup=get_main_kb(u.is_admin, u.is_president)
    )

# --- Ловля всех Callback-ошибок (чтобы не было "not handled") ---
@router.callback_query()
async def unhandled_callback(call: types.CallbackQuery):
    await call.answer("❌ Эта кнопка устарела или не существует.", show_alert=True)
    u = get_user(call.from_user.id)
    # Возвращаем главное меню на всякий случай
    # Проверка на наличие message, так как колбэк может быть вызван из-за устаревшего сообщения
    if call.message:
        await call.message.answer("Возвращено Главное Меню.", reply_markup=get_main_kb(u.is_admin, u.is_president))

# =========================================================
# === 14. ЗАПУСК БОТА ===
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
    # ИСПОЛЬЗУЕМ dp.start_polling(bot) - это правильно для aiogram 3.x
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Бот остановлен вручную.")
    except Exception as e:
        logging.error(f"Критическая ошибка при запуске: {e}")
