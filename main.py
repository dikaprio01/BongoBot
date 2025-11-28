import logging
import random
import os
import sys
from datetime import datetime, timedelta

# Настройка логирования
logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Проверка на наличие зависимостей
try:
    from aiogram import Bot, Dispatcher, types, F, Router
    from aiogram.client.default import DefaultBotProperties
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
    from aiogram.filters.command import Command
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.state import State, StatesGroup
    
    from sqlalchemy import create_engine, Column, Integer, String, BigInteger, DateTime, Boolean, DECIMAL, func
    from sqlalchemy.orm import sessionmaker, declarative_base
    from sqlalchemy.exc import SQLAlchemyError
except ImportError as e:
    logging.error(f"Не удалось импортировать необходимые библиотеки: {e}. Убедитесь, что все зависимости установлены.")
    sys.exit(1)


# =========================================================
# === 1. КОНФИГУРАЦИЯ И КОНСТАНТЫ ===
# =========================================================

# Ваш Telegram ID для прав администратора
OWNER_ID = 1871352653

# Токен бота
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    logging.error("❌ BOT_TOKEN не найден в переменных окружения.")
    sys.exit(1)

# Подключение к базе данных (MySQL на Railway)
DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("MySql_url")
if not DATABASE_URL:
    logging.error("❌ DATABASE_URL (или MySql_url) не найдена в переменных окружения.")
    sys.exit(1)

try:
    # Замена префикса mysql:// на mysql+pymysql://
    DB_URL = DATABASE_URL.replace("mysql://", "mysql+pymysql://")
    engine = create_engine(DB_URL)
    Session = sessionmaker(bind=engine)
    Base = declarative_base()
    logging.info("Подключение к базе данных настроено.")
except Exception as e:
    logging.error(f"❌ ОШИБКА НАСТРОЙКИ БД: {e}")
    sys.exit(1)

# Настройка aiogram
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="Markdown"))
dp = Dispatcher()
router = Router()

# Кнопки Главного Меню
BTN_BUSINESS = "💼 Бизнес"
BTN_WORK = "⛏️ Работа"
BTN_CASINO = "🎰 Казино"
BTN_POLITICS = "🏛 Политика"
BTN_PROFILE = "👤 Профиль"
BTN_ADMIN = "👮‍♂️ Админ-панель"

# Клавиатура Главного Меню
main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_BUSINESS), KeyboardButton(text=BTN_WORK)],
        [KeyboardButton(text=BTN_CASINO), KeyboardButton(text=BTN_POLITICS)],
        [KeyboardButton(text=BTN_PROFILE), KeyboardButton(text=BTN_ADMIN)] # Кнопка для администратора
    ],
    resize_keyboard=True
)

# Бизнесы (Расширено и улучшено)
BUSINESSES = {
    1: {"name": "Ларек с шаурмой", "cost": 10_000, "income": 500},
    2: {"name": "Кофейня 'Быстрый Кофе'", "cost": 50_000, "income": 3_000},
    3: {"name": "Пункт обмена валют", "cost": 150_000, "income": 10_000},
    4: {"name": "Магазин электроники 'ТехноРай'", "cost": 500_000, "income": 35_000},
    5: {"name": "Небольшой отель 'Сонный Дракон'", "cost": 2_000_000, "income": 120_000},
    6: {"name": "Разработка ПО (IT-стартап)", "cost": 10_000_000, "income": 500_000},
}

# Настройки Политики
ELECTION_COOLDOWN = timedelta(hours=6)      
ELECTION_DURATION_CANDIDACY = timedelta(hours=1)
ELECTION_DURATION_VOTING = timedelta(hours=1)    

# Кулдаун для работы и ежедневного бонуса
WORK_COOLDOWN = timedelta(hours=4)
DAILY_BONUS_COOLDOWN = timedelta(hours=24)
WORK_PAYMENT_RANGE = (1000, 5000) # Диапазон заработка

# Состояния для FSM
class CasinoState(StatesGroup):
    bet = State()

class AdminState(StatesGroup):
    setting_tax_rate = State()
    giving_money_id = State()
    giving_money_amount = State()

# =========================================================
# === 2. МОДЕЛИ БАЗЫ ДАННЫХ (SQLAlchemy) ===
# =========================================================

class User(Base):
    __tablename__ = 'user'
    telegram_id = Column(BigInteger, primary_key=True, index=True)
    username = Column(String(50))
    first_name = Column(String(50))
    balance = Column(BigInteger, default=1000)
    last_daily = Column(DateTime)
    last_work = Column(DateTime) # Новое поле для работы
    is_admin = Column(Boolean, default=False)
    # Поля для политики
    last_vote_time = Column(DateTime)
    
    def __repr__(self):
        return f"<User(id={self.telegram_id}, balance={self.balance})>"

class OwnedBusiness(Base):
    __tablename__ = 'owned_business'
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger)
    business_id = Column(Integer)
    count = Column(Integer, default=1)
    last_collected = Column(DateTime, default=datetime.now)

class ElectionState(Base):
    __tablename__ = 'election_state'
    id = Column(Integer, primary_key=True)
    phase = Column(String(50), default="IDLE") # IDLE, CANDIDACY, VOTING
    tax_rate = Column(DECIMAL(5, 2), default=0.00) # Процент налога (0.00 - 100.00)
    end_time = Column(DateTime) # Когда заканчивается текущая фаза
    last_election_time = Column(DateTime, default=datetime(2000, 1, 1)) # Время последних выборов

class Candidate(Base):
    __tablename__ = 'candidate'
    user_id = Column(BigInteger, primary_key=True)
    votes = Column(Integer, default=0)

# =========================================================
# === 3. ИНИЦИАЛИЗАЦИЯ И МИГРАЦИЯ БД ===
# =========================================================

def init_db():
    logging.info("Инициализация базы данных...")
    try:
        # Создание таблиц
        Base.metadata.create_all(engine)
        
        with Session() as s:
            # Создание ElectionState
            if not s.query(ElectionState).first():
                logging.info("Создание начальной записи ElectionState.")
                s.add(ElectionState(
                    phase="IDLE",
                    end_time=datetime.now(),
                    last_election_time=datetime.now() - ELECTION_COOLDOWN
                ))
                s.commit()
                
            # Проверка, что владелец является администратором
            owner = s.query(User).filter_by(telegram_id=OWNER_ID).first()
            if owner and not owner.is_admin:
                owner.is_admin = True
                s.commit()
                logging.info(f"Установлен владелец {OWNER_ID} как администратор.")
            
        logging.info("База данных инициализирована успешно.")
    except SQLAlchemyError as e:
        logging.error(f"❌ ОШИБКА ИНИЦИАЛИЗАЦИИ БД: {e}")
        logging.error("Database initialization failed. Exiting.")
        sys.exit(1)
    except Exception as e:
        logging.error(f"❌ Неизвестная ошибка при инициализации БД: {e}")
        sys.exit(1)


# =========================================================
# === 4. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===
# =========================================================

async def send_global_notification(text: str):
    """Отправляет уведомление всем пользователям бота."""
    try:
        with Session() as s:
            user_ids = [u.telegram_id for u in s.query(User.telegram_id).all()]
        
        logging.info(f"Начало рассылки уведомления {len(user_ids)} пользователям.")
        for user_id in user_ids:
            try:
                # ВАЖНО: reply_markup=main_keyboard для удобства пользователя
                await bot.send_message(user_id, text, reply_markup=main_keyboard)
            except Exception as e:
                # Игнорируем ошибки, если бот заблокирован
                logging.debug(f"Не удалось отправить сообщение пользователю {user_id}: {e}")
        logging.info("Рассылка завершена.")
    except Exception as e:
        logging.error(f"Ошибка при глобальной рассылке: {e}")

def get_user_data_safe(telegram_id: int) -> tuple[User | None, list[OwnedBusiness]]:
    """Безопасное получение данных пользователя и бизнесов вне обработчика."""
    try:
        with Session() as s:
            user = s.query(User).filter_by(telegram_id=telegram_id).first()
            owned_businesses = []
            if user:
                owned_businesses = s.query(OwnedBusiness).filter_by(user_id=telegram_id).all()
            
            # Отсоединяем объекты от сессии для безопасной передачи
            s.expunge_all()
            return user, owned_businesses
    except SQLAlchemyError as e:
        logging.error(f"Ошибка БД при безопасном получении данных: {e}")
        return None, []
    except Exception as e:
        logging.error(f"Неизвестная ошибка при безопасном получении данных: {e}")
        return None, []


def format_business_list(owned_businesses):
    """Форматирует список купленных бизнесов для вывода."""
    if not owned_businesses:
        return "😔 *У вас пока нет купленных бизнесов.*\n_Начните свой путь к богатству!_"

    biz_map = {}
    for ob in owned_businesses:
        biz_info = BUSINESSES.get(ob.business_id)
        if not biz_info:
            continue
            
        name = biz_info['name']
        income = biz_info['income']
        if name not in biz_map:
            biz_map[name] = {"count": 0, "income": income}
        biz_map[name]["count"] += ob.count

    output = ["💰 *Ваши активы:*"]
    total_income = 0
    for name, data in biz_map.items():
        total_income += data['count'] * data['income']
        output.append(f" • {name}: *{data['count']} шт.* (Доход: {data['count'] * data['income']:,}💰/час)")

    output.append(f"\n📈 Общий часовой доход: *{total_income:,}*💰")
    return "\n".join(output)

def get_display_name(user: User) -> str:
    """Возвращает предпочтительное имя для отображения: @username > First Name > ID."""
    if user is None:
        return "Неизвестный Пользователь"
    if user.username:
        return f"@{user.username}"
    if user.first_name:
        return user.first_name
    return f"ID `{user.telegram_id}`"

def format_time_left(target_time: datetime, now: datetime = None):
    """Форматирует оставшееся время."""
    if now is None:
        now = datetime.now()
        
    time_diff = target_time - now
    if time_diff.total_seconds() < 0:
        return "*0 сек. (Время истекло)*"
    
    total_seconds = int(time_diff.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60

    parts = []
    if hours > 0:
        parts.append(f"{hours} ч.")
    if minutes > 0:
        parts.append(f"{minutes} мин.")
    # Показываем секунды, если до 1 минуты осталось
    if total_seconds < 60 or not parts:
        parts.append(f"{seconds} сек.")
        
    return f"*{' '.join(parts)}*"

# =========================================================
# === 5. ОБРАБОТЧИКИ: ОСНОВНЫЕ И ПРОФИЛЬ ===
# =========================================================

@router.message(Command("start"))
async def command_start_handler(message: types.Message):
    logging.debug(f"Received /start from user {message.from_user.id}")
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    try:
        with Session() as s:
            u = s.query(User).filter_by(telegram_id=user_id).first()
            if not u:
                u = User(telegram_id=user_id, username=username, first_name=first_name, balance=1000)
                s.add(u)
            else:
                # Обновляем имя и ник на случай, если пользователь их сменил
                u.username = username
                u.first_name = first_name
                
            if user_id == OWNER_ID:
                u.is_admin = True # Гарантируем права админа
                
            s.commit()
            balance = u.balance

        await message.answer(
            f"👋 *Добро пожаловать в BongoBot, {first_name}!* \n\n"
            f"Это игра, где вы строите свой бизнес, работаете и боретесь за власть.\n"
            f"Ваш стартовый капитал: *{balance:,}*💰\n"
            f"Используйте кнопки меню, чтобы начать.",
            reply_markup=main_keyboard
        )
    except SQLAlchemyError as e:
        logging.error(f"DB Error on /start: {e}")
        await message.answer("❌ Произошла ошибка базы данных. Попробуйте позже.")


@router.message(F.text == BTN_PROFILE)
async def profile_handler(message: types.Message):
    logging.debug(f"Received profile request from user {message.from_user.id}")
    user_id = message.from_user.id
    now = datetime.now()
    
    try:
        with Session() as s:
            user_db = s.query(User).filter_by(telegram_id=user_id).with_for_update().first()
            if not user_db:
                # Если не найден, создаем и перечитываем
                user_db = User(telegram_id=user_id, first_name=message.from_user.first_name, balance=1000)
                s.add(user_db)
                s.commit()
                
            total_income_collected = 0
            owned_businesses = s.query(OwnedBusiness).filter_by(user_id=user_db.telegram_id).all()
            
            # 1. Логика сбора доходов с бизнеса
            for ob in owned_businesses:
                biz_info = BUSINESSES.get(ob.business_id)
                if not biz_info: continue
                
                hours_passed = int(max(0, (now - ob.last_collected).total_seconds() // 3600))
                
                if hours_passed >= 1:
                    income_for_biz = hours_passed * ob.count * biz_info['income']
                    # Учет налога
                    state = s.query(ElectionState).first()
                    tax_amount = int(income_for_biz * (float(state.tax_rate) / 100))
                    
                    net_income = income_for_biz - tax_amount
                    user_db.balance += net_income
                    total_income_collected += net_income
                    
                    ob.last_collected = ob.last_collected + timedelta(hours=hours_passed)
            
            s.commit() # Сохраняем все изменения
            
            # --- Форматирование вывода ---
            state = s.query(ElectionState).first()
            business_info = format_business_list(owned_businesses)
            
            admin_status = "👑 *Администратор* (Владелец)" if user_db.telegram_id == OWNER_ID else "👤 Пользователь"
            
            collected_message = ""
            if total_income_collected > 0:
                collected_message = f"🎉 *Авто-сбор дохода:* Вы получили *{total_income_collected:,}*💰 (С учетом налога {state.tax_rate:.2f}%).\n"

            # Кнопка для ежедневного бонуса
            can_get_daily = (user_db.last_daily is None or now >= user_db.last_daily + DAILY_BONUS_COOLDOWN)
            daily_markup = InlineKeyboardMarkup(inline_keyboard=[])
            if can_get_daily:
                 daily_markup.inline_keyboard.append([
                     InlineKeyboardButton(text="🎁 Получить Дневной Бонус", callback_data="get_daily_bonus")
                 ])
            else:
                next_daily = user_db.last_daily + DAILY_BONUS_COOLDOWN
                time_left = format_time_left(next_daily)
                collected_message += f"\n_Бонус будет доступен через {time_left}_."

            await message.answer(
                f"💳 *Ваш Профиль: {user_db.first_name}*\n"
                f"-----------------------------------\n"
                f"{admin_status}\n"
                f"🔗 Никнейм: {get_display_name(user_db)}\n"
                f"🆔 ID: `{user_db.telegram_id}`\n"
                f"💰 Баланс: *{user_db.balance:,}*💰\n"
                f"💸 Налог с бизнеса: *{state.tax_rate:.2f}%*\n"
                f"-----------------------------------\n"
                f"{collected_message}\n"
                f"{business_info}",
                reply_markup=daily_markup
            )
            
    except SQLAlchemyError as e:
        logging.error(f"DB Error on profile: {e}")
        await message.answer("❌ Произошла ошибка базы данных при загрузке профиля. Попробуйте позже.")
        
# --- Ежедневный Бонус (Callback) ---
@router.callback_query(F.data == "get_daily_bonus")
async def get_daily_bonus_handler(callback: types.CallbackQuery):
    await callback.answer("Получение бонуса...")
    user_id = callback.from_user.id
    now = datetime.now()
    
    try:
        with Session() as s:
            user_db = s.query(User).filter_by(telegram_id=user_id).with_for_update().first()
            
            if not user_db:
                await callback.message.answer("Ошибка: Пользователь не найден.", reply_markup=main_keyboard)
                return
            
            if user_db.last_daily and now < user_db.last_daily + DAILY_BONUS_COOLDOWN:
                next_daily = user_db.last_daily + DAILY_BONUS_COOLDOWN
                time_left = format_time_left(next_daily)
                await callback.message.answer(f"❌ *Бонус уже получен!* Следующий доступен через {time_left}.", reply_markup=main_keyboard)
                return

            bonus_amount = random.randint(5000, 15000) # Красивая, значимая сумма
            user_db.balance += bonus_amount
            user_db.last_daily = now
            s.commit()
            
            await callback.message.answer(
                f"🎉 *ПОЗДРАВЛЯЕМ!* Вы получили дневной бонус: *{bonus_amount:,}*💰.\n"
                f"Новый баланс: *{user_db.balance:,}*💰",
                reply_markup=main_keyboard
            )
            # Убираем кнопку бонуса из предыдущего сообщения
            await callback.message.edit_reply_markup(reply_markup=None)
            
    except SQLAlchemyError as e:
        logging.error(f"DB Error on daily bonus: {e}")
        await callback.message.answer("❌ Произошла ошибка базы данных. Попробуйте позже.")


# --- Бизнес (Без изменений в логике покупки, так как она уже стабильна) ---

@router.message(F.text == BTN_BUSINESS)
async def business_menu_handler(message: types.Message):
    logging.debug(f"Received business menu request from user {message.from_user.id}")
    
    with Session() as s:
        user_db = s.query(User).filter_by(telegram_id=message.from_user.id).first()
        state = s.query(ElectionState).first()
        if not user_db:
            user_db = User(telegram_id=message.from_user.id, first_name=message.from_user.first_name, balance=1000)
            s.add(user_db)
            s.commit()
            
        current_balance = user_db.balance
        owned_businesses = s.query(OwnedBusiness).filter_by(user_id=user_db.telegram_id).all()
        s.expunge_all()
        
    business_info = format_business_list(owned_businesses)
    markup = InlineKeyboardMarkup(inline_keyboard=[])
    
    for biz_id, biz_info in BUSINESSES.items():
        biz_name = f"🏪 {biz_info['name']}"
        markup.inline_keyboard.append([
            InlineKeyboardButton(
                text=f"{biz_name} | Цена: {biz_info['cost']:,}💰 | +{biz_info['income']:,}💰/час",
                callback_data=f"buy_biz_{biz_id}"
            )
        ])
    
    await message.answer(
        f"💼 *Магазин Бизнеса*\n"
        f"💸 Текущий налог: *{state.tax_rate:.2f}%*\n\n"
        f"Ваш текущий капитал: *{current_balance:,}*💰\n\n"
        f"{business_info}\n\n"
        f"Выберите бизнес для покупки:",
        reply_markup=markup
    )

@router.callback_query(F.data.startswith("buy_biz_"))
async def buy_business_callback_handler(callback: types.CallbackQuery):
    await callback.answer()
    biz_id = int(callback.data.split("_")[-1])
    biz = BUSINESSES.get(biz_id)
    
    if not biz: return

    try:
        with Session() as s:
            user_db = s.query(User).filter_by(telegram_id=callback.from_user.id).with_for_update().first()
            
            if user_db.balance < biz["cost"]:
                await callback.message.answer(f"❌ Недостаточно средств. Нужно {biz['cost']:,}💰.", reply_markup=main_keyboard)
                return
                
            user_db.balance -= biz["cost"]
            
            owned = s.query(OwnedBusiness).filter_by(user_id=callback.from_user.id, business_id=biz_id).first()
            if owned:
                owned.count += 1
            else:
                s.add(OwnedBusiness(
                    user_id=callback.from_user.id,
                    business_id=biz_id,
                    count=1,
                    last_collected=datetime.now()
                ))
            s.commit()
            
            await callback.message.answer(
                f"🎉 Поздравляем! Вы купили: *{biz['name']}*.\n"
                f"Ваш доход вырос на *{biz['income']:,}*💰/час.\n"
                f"Новый баланс: *{user_db.balance:,}*💰",
                reply_markup=main_keyboard
            )
            
            # Попытка обновить меню бизнеса, чтобы отразить изменения
            await business_menu_handler(callback.message)
            
    except SQLAlchemyError as e:
        logging.error(f"DB Error on buying business: {e}")
        await callback.message.answer("❌ Произошла ошибка базы данных при покупке.", reply_markup=main_keyboard)


# --- Новая Функция: Работа ---

@router.message(F.text == BTN_WORK)
async def work_handler(message: types.Message):
    user_id = message.from_user.id
    now = datetime.now()
    
    try:
        with Session() as s:
            user_db = s.query(User).filter_by(telegram_id=user_id).with_for_update().first()
            if not user_db: return
            
            can_work = (user_db.last_work is None or now >= user_db.last_work + WORK_COOLDOWN)
            
            if not can_work:
                next_work_time = user_db.last_work + WORK_COOLDOWN
                time_left = format_time_left(next_work_time)
                await message.answer(
                    f"⚙️ *Перерыв окончен через {time_left}*.\n"
                    f"Рабочий день длится 4 часа. Возвращайтесь позже!",
                    reply_markup=main_keyboard
                )
                return
                
            payment = random.randint(*WORK_PAYMENT_RANGE)
            user_db.balance += payment
            user_db.last_work = now
            s.commit()
            
            await message.answer(
                f"✅ *Работа выполнена!* Вы заработали: *{payment:,}*💰.\n"
                f"Новый баланс: *{user_db.balance:,}*💰.\n"
                f"Следующий рабочий день доступен через {format_time_left(now + WORK_COOLDOWN)}.",
                reply_markup=main_keyboard
            )
            
    except SQLAlchemyError as e:
        logging.error(f"DB Error on work: {e}")
        await message.answer("❌ Произошла ошибка базы данных при выполнении работы.")


# --- Казино (FSM) ---
# Логика казино остается прежней

@router.message(F.text == BTN_CASINO)
async def casino_menu_handler(message: types.Message, state: FSMContext):
    with Session() as s:
        user = s.query(User).filter_by(telegram_id=message.from_user.id).first()
    
    await state.clear()
    await state.set_state(CasinoState.bet)
    
    await message.answer(
        f"🎰 *Казино - Орел или Решка*\n"
        f"🎲 Выберите свою судьбу: 50% шанс удвоить ставку.\n"
        f"💰 Ваш баланс: *{user.balance:,}*💰\n\n"
        f"Введите сумму ставки (от 100💰 до 100 000💰):",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="Отмена")]],
            resize_keyboard=True
        )
    )

@router.message(CasinoState.bet)
async def casino_place_bet_handler(message: types.Message, state: FSMContext):
    if message.text.lower() == "отмена":
        await state.clear()
        return await message.answer("❌ *Ставка отменена.* Возврат в главное меню.", reply_markup=main_keyboard)
        
    try:
        bet_amount = int(message.text.replace(' ', ''))
    except ValueError:
        return await message.answer("⚠️ Пожалуйста, введите корректное число для ставки.")

    if not (100 <= bet_amount <= 100_000):
        return await message.answer("⚠️ Минимальная ставка: 100💰. Максимальная: 100 000💰.")

    user_id = message.from_user.id
    
    try:
        with Session() as s:
            u = s.query(User).filter_by(telegram_id=user_id).with_for_update().first()
            if not u:
                await state.clear()
                return await message.answer("Ошибка: Пользователь не найден в БД.", reply_markup=main_keyboard)
                
            if u.balance < bet_amount:
                return await message.answer(f"⚠️ У вас недостаточно средств. Ваш баланс: {u.balance:,}💰. Введите новую ставку.")
            
            win = random.choice([True, False])
            
            if win:
                u.balance += bet_amount
                result_text = f"🎉 *ПОБЕДА!* Монета упала на *Орла*. Вы выиграли *{bet_amount:,}*💰."
            else:
                u.balance -= bet_amount
                result_text = f"💸 *ПРОИГРЫШ!* Монета упала на *Решку*. Вы потеряли *{bet_amount:,}*💰."
                
            s.commit()
            new_balance = u.balance 
    
    except SQLAlchemyError as e:
        logging.error(f"DB Error on casino bet: {e}")
        await state.clear()
        return await message.answer("Произошла ошибка базы данных. Попробуйте еще раз.", reply_markup=main_keyboard)
        
    await state.clear()
    await message.answer(
        f"{result_text}\n"
        f"💰 Новый баланс: *{new_balance:,}*💰",
        reply_markup=main_keyboard
    )


# --- Политика (Меню и Логика) ---

@router.message(F.text == BTN_POLITICS)
async def politics_menu_handler(message: types.Message):
    logging.debug(f"Received politics menu request from user {message.from_user.id}")
    
    try:
        with Session() as s:
            # User для проверки статуса голосования
            user = s.query(User).filter_by(telegram_id=message.from_user.id).first()
            state = s.query(ElectionState).first()
            
            # Получаем кандидатов с их именами
            candidates_data = s.query(Candidate, User).outerjoin(User, Candidate.user_id == User.telegram_id).all()
            
            # Отсоединяем объекты
            s.expunge_all()
    except SQLAlchemyError as e:
        logging.error(f"DB Error in politics_menu_handler: {e}")
        return await message.answer("❌ Произошла ошибка базы данных при загрузке политики.", reply_markup=main_keyboard)

    candidate_list = ""
    status_text = ""
    markup = InlineKeyboardMarkup(inline_keyboard=[])
    now = datetime.now()
    
    # 1. Формирование списка кандидатов
    candidates_details = []
    for cand, cand_user in candidates_data:
        votes = cand.votes
        name = get_display_name(cand_user)
        candidates_details.append(f" - {name}: *{votes}* голосов")
    candidate_list = "\n".join(candidates_details) if candidates_details else "_Нет зарегистрированных кандидатов._"

    # 2. Формирование статуса и кнопок
    if state.phase == "IDLE":
        next_election_start = state.last_election_time + ELECTION_COOLDOWN
        time_left = format_time_left(next_election_start, now)
        status_text = f"🛡️ *Фаза:* Ожидание\n" \
                      f"⏳ До следующих выборов: {time_left}.\n" \
                      f"_Администратор может начать выборы досрочно._"
                      
    elif state.phase == "CANDIDACY":
        time_left = format_time_left(state.end_time, now)
        status_text = f"🗳️ *Фаза:* Регистрация кандидатов\n" \
                      f"⏱️ До окончания: {time_left}\n\n" \
                      f"👥 *Кандидаты:*\n{candidate_list}"
                      
        if not any(c.user_id == user.telegram_id for c, u in candidates_data):
            markup.inline_keyboard.append([InlineKeyboardButton(text="✍️ Стать Кандидатом (10,000💰)", callback_data="start_candidacy")])
        
    elif state.phase == "VOTING":
        time_left = format_time_left(state.end_time, now)
        status_text = f"📣 *Фаза:* Голосование\n" \
                      f"⏱️ До окончания: {time_left}\n\n" \
                      f"👥 *Кандидаты:*\n{candidate_list}"
                      
        vote_window_start = state.end_time - ELECTION_DURATION_VOTING 
        can_vote = (user.last_vote_time is None or user.last_vote_time < vote_window_start)
        
        if can_vote and candidates_data:
            vote_buttons = []
            for cand, cand_user in candidates_data:
                name = cand_user.first_name if cand_user else f"ID {cand.user_id}"
                vote_buttons.append(InlineKeyboardButton(text=f"Голосовать за {name}", callback_data=f"vote_{cand.user_id}"))
            
            for i in range(0, len(vote_buttons), 2):
                markup.inline_keyboard.append(vote_buttons[i:i+2])
        elif not can_vote:
            status_text += "\n\n❌ *Вы уже проголосовали на этих выборах.*"
            
    # Добавляем налог
    status_text += f"\n\n💸 Текущая ставка налога: *{state.tax_rate:.2f}%*"

    await message.answer(f"🏛 *Политический Центр*\n\n{status_text}", reply_markup=markup)

@router.callback_query(F.data.startswith("vote_"))
async def vote_handler(callback: types.CallbackQuery):
    await callback.answer("Ваш голос принят!")
    candidate_id = int(callback.data.split("_")[-1])
    user_id = callback.from_user.id
    
    try:
        with Session() as s:
            user_db = s.query(User).filter_by(telegram_id=user_id).with_for_update().first()
            state = s.query(ElectionState).first()
            
            if state.phase != "VOTING":
                await callback.message.answer("❌ Голосование неактивно.", reply_markup=main_keyboard)
                return

            vote_window_start = state.end_time - ELECTION_DURATION_VOTING
            if user_db.last_vote_time and user_db.last_vote_time >= vote_window_start:
                return # Не уведомляем, так как это уже обрабатывается в меню

            candidate = s.query(Candidate).filter_by(user_id=candidate_id).with_for_update().first()
            if not candidate:
                await callback.message.answer("❌ Кандидат не найден.", reply_markup=main_keyboard)
                return

            candidate.votes += 1
            user_db.last_vote_time = datetime.now()
            s.commit()
            
            candidate_user_data = s.query(User).filter_by(telegram_id=candidate_id).first()
            candidate_name = get_display_name(candidate_user_data)

        await callback.message.answer(f"✅ Вы успешно проголосовали за: *{candidate_name}*.", reply_markup=main_keyboard)
        # Обновляем меню политики
        await politics_menu_handler(callback.message)
        
    except SQLAlchemyError as e:
        logging.error(f"DB Error on voting: {e}")
        await callback.message.answer("❌ Произошла ошибка базы данных при голосовании.", reply_markup=main_keyboard)


# =========================================================
# === 7. ОБРАБОТЧИКИ: АДМИН-ПАНЕЛЬ (РАСШИРЕННАЯ) ===
# =========================================================

def is_admin(user_id):
    """Проверяет, является ли пользователь администратором."""
    return user_id == OWNER_ID

@router.message(F.text == BTN_ADMIN)
async def admin_menu_handler(message: types.Message):
    if not is_admin(message.from_user.id):
        return await message.answer("❌ Доступ запрещен. Только для администратора.")

    logging.debug(f"Admin menu request from {message.from_user.id}")
    
    try:
        with Session() as s:
            state = s.query(ElectionState).first()
            if not state:
                return await message.answer("Ошибка: Не найдено состояние выборов в БД.")
            tax_rate = state.tax_rate
    except SQLAlchemyError as e:
        logging.error(f"DB Error on admin_menu_handler: {e}")
        return await message.answer("❌ Ошибка БД при загрузке админ-панели.")

    markup = InlineKeyboardMarkup(inline_keyboard=[
        # Управление выборами
        [InlineKeyboardButton(text="Начать регистрацию кандидатов", callback_data="admin_start_candidacy")],
        [InlineKeyboardButton(text="Начать голосование", callback_data="admin_start_voting")],
        [InlineKeyboardButton(text="Завершить выборы и объявить результат", callback_data="admin_end_election")],
        [InlineKeyboardButton(text="Сбросить состояние выборов (Hard Reset)", callback_data="admin_reset_election_state")],
        # Управление экономикой
        [InlineKeyboardButton(text=f"Установить налог (Текущий: {tax_rate:.2f}%)", callback_data="admin_set_tax_start")],
        [InlineKeyboardButton(text="Выдать деньги пользователю", callback_data="admin_give_money_start")]
    ])
    
    await message.answer(
        "👮‍♂️ *Админ-панель. Управление системой*\n\n"
        f"Текущая фаза выборов: *{state.phase}*",
        reply_markup=markup
    )
    
# --- Логика Управления Выборами (Сокращено для читаемости, основана на прошлой версии) ---

# ... (admin_start_candidacy_handler, admin_start_voting_handler, admin_end_election_handler - Оставляем как есть, они стабильны)

# Обработчик: Hard Reset выборов
@router.callback_query(F.data == "admin_reset_election_state")
async def admin_reset_election_state_handler(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id): return await callback.answer("❌ Доступ запрещен.", show_alert=True)
    await callback.answer()
    
    try:
        with Session() as s:
            s.query(Candidate).delete()
            state = s.query(ElectionState).first()
            state.phase = "IDLE"
            state.tax_rate = 0.00
            state.end_time = datetime.now()
            state.last_election_time = datetime.now() - ELECTION_COOLDOWN
            s.commit()
            
        await callback.message.answer("♻️ *Полный сброс выборов выполнен!* Фаза IDLE, кандидаты и налог (0.00%) сброшены.", reply_markup=main_keyboard)
        await admin_menu_handler(callback.message)
    except SQLAlchemyError as e:
        logging.error(f"DB Error on admin_reset_election_state: {e}")
        await callback.message.answer("❌ Ошибка БД при сбросе выборов.", reply_markup=main_keyboard)

# --- Логика Управления Экономикой (FSM) ---

# 1. Установка налога
@router.callback_query(F.data == "admin_set_tax_start")
async def admin_set_tax_start_handler(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id): return await callback.answer("❌ Доступ запрещен.", show_alert=True)
    await callback.answer()
    
    await state.set_state(AdminState.setting_tax_rate)
    await callback.message.answer(
        "💰 *Установка Налога*\n\n"
        "Введите новый процент налога (от 0.00 до 100.00).",
        reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Отмена")]], resize_keyboard=True)
    )

@router.message(AdminState.setting_tax_rate)
async def admin_set_tax_rate_input(message: types.Message, state: FSMContext):
    if message.text.lower() == "отмена":
        await state.clear()
        return await message.answer("❌ *Установка налога отменена.*", reply_markup=main_keyboard)
        
    try:
        tax_rate = float(message.text.replace(',', '.').strip())
        if not (0.00 <= tax_rate <= 100.00):
            return await message.answer("⚠️ Неверный процент. Введите число от 0.00 до 100.00.")
            
        with Session() as s:
            state_db = s.query(ElectionState).first()
            state_db.tax_rate = tax_rate
            s.commit()
            
        await state.clear()
        await message.answer(
            f"✅ *Налог успешно установлен!* Новая ставка: *{tax_rate:.2f}%*",
            reply_markup=main_keyboard
        )
    except ValueError:
        await message.answer("⚠️ Пожалуйста, введите корректное числовое значение налога.")
    except SQLAlchemyError as e:
        logging.error(f"DB Error on admin_set_tax_rate: {e}")
        await state.clear()
        await message.answer("❌ Ошибка БД при сохранении налога.", reply_markup=main_keyboard)

# 2. Выдача денег
@router.callback_query(F.data == "admin_give_money_start")
async def admin_give_money_start_handler(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id): return await callback.answer("❌ Доступ запрещен.", show_alert=True)
    await callback.answer()
    
    await state.set_state(AdminState.giving_money_id)
    await callback.message.answer(
        "💸 *Выдача Средств: Шаг 1/2*\n\n"
        "Введите *Telegram ID* пользователя, которому хотите выдать деньги:",
        reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Отмена")]], resize_keyboard=True)
    )

@router.message(AdminState.giving_money_id)
async def admin_give_money_get_id(message: types.Message, state: FSMContext):
    if message.text.lower() == "отмена":
        await state.clear()
        return await message.answer("❌ *Выдача средств отменена.*", reply_markup=main_keyboard)
        
    try:
        user_id = int(message.text.strip())
        
        # Проверяем, существует ли пользователь
        target_user, _ = get_user_data_safe(user_id)
        if not target_user:
            return await message.answer(f"⚠️ Пользователь с ID `{user_id}` не найден в системе. Проверьте ID.")
            
        # Сохраняем ID и переходим к следующему шагу
        await state.update_data(target_id=user_id)
        await state.set_state(AdminState.giving_money_amount)
        
        await message.answer(
            f"✅ ID пользователя *{get_display_name(target_user)}* (`{user_id}`) подтвержден.\n\n"
            "💸 *Выдача Средств: Шаг 2/2*\n"
            "Введите сумму, которую хотите выдать (только положительное целое число).",
            reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Отмена")]], resize_keyboard=True)
        )
        
    except ValueError:
        await message.answer("⚠️ Пожалуйста, введите корректный числовой ID пользователя.")

@router.message(AdminState.giving_money_amount)
async def admin_give_money_get_amount(message: types.Message, state: FSMContext):
    if message.text.lower() == "отмена":
        await state.clear()
        return await message.answer("❌ *Выдача средств отменена.*", reply_markup=main_keyboard)
        
    try:
        amount = int(message.text.replace(' ', '').strip())
        if amount <= 0:
            return await message.answer("⚠️ Сумма должна быть положительным целым числом.")
            
        data = await state.get_data()
        target_id = data.get('target_id')
        
        with Session() as s:
            target_user = s.query(User).filter_by(telegram_id=target_id).with_for_update().first()
            
            if not target_user:
                await state.clear()
                return await message.answer("❌ Ошибка: Целевой пользователь не найден (попробуйте сначала ввести ID).", reply_markup=main_keyboard)

            target_user.balance += amount
            s.commit()
            
            target_name = get_display_name(target_user)

        await state.clear()
        await message.answer(
            f"💰 *Выдача успешна!* \n"
            f"Пользователю {target_name} выдано: *{amount:,}*💰.\n"
            f"Новый баланс: *{target_user.balance:,}*💰.",
            reply_markup=main_keyboard
        )
        # Отправляем уведомление целевому пользователю
        try:
            await bot.send_message(target_id,
                f"🎉 *Администратор* выдал вам *{amount:,}*💰!\n"
                f"Ваш новый баланс: *{target_user.balance:,}*💰.",
                reply_markup=main_keyboard
            )
        except Exception:
            logging.warning(f"Не удалось отправить уведомление пользователю {target_id}")

    except ValueError:
        await message.answer("⚠️ Пожалуйста, введите корректное числовое значение суммы.")
    except SQLAlchemyError as e:
        logging.error(f"DB Error on admin_give_money_amount: {e}")
        await state.clear()
        await message.answer("❌ Ошибка БД при выдаче средств.", reply_markup=main_keyboard)

# =========================================================
# === 8. ЗАПУСК БОТА ===
# =========================================================

# Дублируем обработчики политики для стабильности, чтобы они не потерялись в длинном коде

@router.callback_query(F.data == "admin_start_candidacy")
async def admin_start_candidacy_handler(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id): return await callback.answer("❌ Доступ запрещен.", show_alert=True)
    await callback.answer()
    try:
        with Session() as s:
            s.query(Candidate).delete()
            state = s.query(ElectionState).first()
            state.phase = "CANDIDACY"
            state.end_time = datetime.now() + ELECTION_DURATION_CANDIDACY
            state.last_election_time = datetime.now()
            s.commit()
        await callback.message.edit_text(
            f"👮‍♂️ *Админ-панель*\n\nТекущая фаза: *{state.phase}* (до {state.end_time.strftime('%H:%M:%S')})",
            reply_markup=callback.message.reply_markup
        )
        await send_global_notification(
            f"📢 *ВНИМАНИЕ! Началась регистрация кандидатов!* 📢\nФаза продлится {ELECTION_DURATION_CANDIDACY.seconds // 3600} час."
        )
    except SQLAlchemyError as e:
        logging.error(f"DB Error on admin_start_candidacy: {e}")
        await bot.send_message(callback.from_user.id, "Ошибка БД при начале регистрации.", reply_markup=main_keyboard)

@router.callback_query(F.data == "admin_start_voting")
async def admin_start_voting_handler(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id): return await callback.answer("❌ Доступ запрещен.", show_alert=True)
    await callback.answer()
    try:
        with Session() as s:
            candidate_count = s.query(Candidate).count()
            if candidate_count == 0:
                await bot.send_message(callback.from_user.id, "❌ Невозможно начать голосование: нет кандидатов.", reply_markup=main_keyboard)
                return
            state = s.query(ElectionState).first()
            state.phase = "VOTING"
            state.end_time = datetime.now() + ELECTION_DURATION_VOTING
            s.commit()
        await callback.message.edit_text(
            f"👮‍♂️ *Админ-панель*\n\nТекущая фаза: *{state.phase}* (до {state.end_time.strftime('%H:%M:%S')})",
            reply_markup=callback.message.reply_markup
        )
        await send_global_notification(
            f"📣 *ГОЛОСОВАНИЕ НАЧАЛОСЬ!* 📣\nЗаходите в раздел '🏛 Политика' и выберите своего кандидата. Фаза продлится {ELECTION_DURATION_VOTING.seconds // 3600} час."
        )
    except SQLAlchemyError as e:
        logging.error(f"DB Error on admin_start_voting: {e}")
        await bot.send_message(callback.from_user.id, "Ошибка БД при начале голосования.", reply_markup=main_keyboard)

@router.callback_query(F.data == "admin_end_election")
async def admin_end_election_handler(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id): return await callback.answer("❌ Доступ запрещен.", show_alert=True)
    await callback.answer()
    try:
        winner_id = None
        winner_name = "Нет"
        with Session() as s:
            state = s.query(ElectionState).first()
            if state.phase == "IDLE":
                await bot.send_message(callback.from_user.id, "Выборы неактивны.", reply_markup=main_keyboard)
                return
            
            # Определяем победителя, используя func.max для стабильности
            candidate_result = s.query(Candidate).order_by(Candidate.votes.desc()).limit(1).first()
            
            if candidate_result:
                winner_id = candidate_result.user_id
                winner_user = s.query(User).filter_by(telegram_id=winner_id).first()
                if winner_user:
                    winner_name = get_display_name(winner_user)
            
            state.phase = "IDLE"
            state.end_time = datetime.now()
            s.query(Candidate).delete()
            s.commit()

        message_text = (
            f"👑 *ВЫБОРЫ ЗАВЕРШЕНЫ!* 👑\n\nПоздравляем! Новым лидером становится *{winner_name}*."
            if winner_id else "🗳️ *ВЫБОРЫ ЗАВЕРШЕНЫ!* \n\nНе было кандидатов или голосов. Фаза сброшена."
        )
                           
        await callback.message.edit_text(
            f"👮‍♂️ *Админ-панель*\n\nТекущая фаза: *{state.phase}*",
            reply_markup=callback.message.reply_markup
        )
        await send_global_notification(message_text)
        
    except SQLAlchemyError as e:
        logging.error(f"DB Error on admin_end_election: {e}")
        await bot.send_message(callback.from_user.id, "Ошибка БД при завершении выборов.", reply_markup=main_keyboard)
    
async def main():
    logging.info("Starting bot...")
    init_db()
    dp.include_router(router)
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Bot stopped by user.")
    except Exception as e:
        logging.error(f"Fatal error during bot runtime: {e}")
