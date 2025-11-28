import logging
import random
import os
import sys
from datetime import datetime, timedelta

# Настройка логирования
logging.basicConfig(level=logging.DEBUG, 
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Проверка на наличие зависимостей, необходимая для запуска в изолированной среде
try:
    from aiogram import Bot, Dispatcher, types, F, Router
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
    from aiogram.filters.command import Command
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.state import State, StatesGroup
    
    from sqlalchemy import create_engine, Column, Integer, String, BigInteger, DateTime, Boolean, DECIMAL
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
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    logging.error("❌ DATABASE_URL не найдена в переменных окружения.")
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
bot = Bot(token=BOT_TOKEN, parse_mode="Markdown")
dp = Dispatcher()
router = Router()

# Клавиатуры
BTN_BUSINESS = "💼 Бизнес"
BTN_CASINO = "🎰 Казино"
BTN_POLITICS = "🏛 Политика"
BTN_PROFILE = "👤 Профиль"
BTN_ADMIN = "👮‍♂️ Админ-панель"

main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_BUSINESS), KeyboardButton(text=BTN_CASINO)],
        [KeyboardButton(text=BTN_POLITICS), KeyboardButton(text=BTN_PROFILE)],
        [KeyboardButton(text=BTN_ADMIN)] # Кнопка для администратора
    ],
    resize_keyboard=True
)

# Бизнесы
BUSINESSES = {
    1: {"name": "Ларек с шаурмой", "cost": 10000, "income": 500},
    2: {"name": "Кофейня", "cost": 50000, "income": 3000},
    3: {"name": "Магазин электроники", "cost": 150000, "income": 10000},
}

# Настройки Политики
ELECTION_COOLDOWN = timedelta(hours=6)      # Перерыв между выборами
ELECTION_DURATION_CANDIDACY = timedelta(hours=1) # Время на регистрацию
ELECTION_DURATION_VOTING = timedelta(hours=1)    # Время на голосование

# Состояние для FSM казино
class CasinoState(StatesGroup):
    bet = State()
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
    tax_rate = Column(DECIMAL(5, 2), default=0.0) # Процент налога
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
        # Создает таблицы, если они не существуют
        Base.metadata.create_all(engine) 
        
        # Проверка и создание единственной записи ElectionState
        with Session() as s:
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

def get_user(telegram_id: int, create_if_not_exists: bool = True) -> User:
    """Получает или создает пользователя по его ID."""
    with Session() as s:
        user = s.query(User).filter_by(telegram_id=telegram_id).first()
        if not user and create_if_not_exists:
            user = User(telegram_id=telegram_id, balance=1000)
            s.add(user)
            s.commit()
            s.refresh(user) # Обновляем объект, чтобы получить актуальные данные
        
        # Для случаев, когда объект User нужен вне сессии, мы делаем его копию
        # (или просто загружаем его атрибуты). Это помогает избежать DetachedInstanceError.
        if user:
            # Создаем временный объект, чтобы избежать DetachedInstanceError
            temp_user = User(
                telegram_id=user.telegram_id,
                username=user.username,
                first_name=user.first_name,
                balance=user.balance,
                last_vote_time=user.last_vote_time,
                is_admin=user.is_admin
            )
            return temp_user
        return None
      def format_business_list(owned_businesses):
    """Форматирует список купленных бизнесов для вывода."""
    if not owned_businesses:
        return "У вас пока нет купленных бизнесов."

    biz_map = {}
    for ob in owned_businesses:
        name = BUSINESSES[ob.business_id]['name']
        income = BUSINESSES[ob.business_id]['income']
        if name not in biz_map:
            biz_map[name] = {"count": 0, "income": income}
        biz_map[name]["count"] += ob.count

    output = ["💰 *Ваши бизнесы:*"]
    total_income = 0
    for name, data in biz_map.items():
        total_income += data['count'] * data['income']
        output.append(f" - {name}: {data['count']} шт. (Доход: {data['count'] * data['income']:,}💰/час)")

    output.append(f"\n✅ Общий часовой доход: *{total_income:,}*💰")
    return "\n".join(output)

def check_arrest_status(user: User):
    """Заглушка для проверки ареста (пока не реализовано)."""
    # if user.is_arrested:
    #     return f"Вы арестованы! Освобождение через {time_left}."
    return None

def format_time_left(target_time: datetime):
    """Форматирует оставшееся время."""
    time_diff = target_time - datetime.now()
    if time_diff.total_seconds() < 0:
        return "0 сек."
    
    hours = int(time_diff.total_seconds() // 3600)
    minutes = int((time_diff.total_seconds() % 3600) // 60)
    seconds = int(time_diff.total_seconds() % 60)

    parts = []
    if hours > 0:
        parts.append(f"{hours} ч.")
    if minutes > 0:
        parts.append(f"{minutes} мин.")
    # Показываем секунды, только если нет часов/минут
    if seconds > 0 or not parts:
        parts.append(f"{seconds} сек.")
        
    return " ".join(parts)


# =========================================================
# === 5. ОБРАБОТЧИКИ: ОСНОВНЫЕ И ПРОФИЛЬ ===
# =========================================================

@router.message(Command("start"))
async def command_start_handler(message: types.Message):
    logging.debug(f"Received /start from user {message.from_user.id}")
    # Обновление информации о пользователе и создание, если не существует
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    with Session() as s:
        u = s.query(User).filter_by(telegram_id=user_id).first()
        if not u:
            u = User(telegram_id=user_id, username=username, first_name=first_name, balance=1000)
            s.add(u)
        else:
            u.username = username
            u.first_name = first_name
        s.commit()
        balance = u.balance

    await message.answer(
        f"👋 *Добро пожаловать, {first_name}!* \n\n"
        f"Ваш текущий баланс: *{balance:,}*💰\n"
        f"Используйте кнопки меню для взаимодействия.",
        reply_markup=main_keyboard
    )

@router.message(F.text == BTN_PROFILE)
async def profile_handler(message: types.Message):
    logging.debug(f"Received profile request from user {message.from_user.id}")
    # Получаем user из базы данных, чтобы быть уверенными в актуальности balance
    with Session() as s:
        user_db = s.query(User).filter_by(telegram_id=message.from_user.id).first()
        if not user_db: 
            user_db = User(telegram_id=message.from_user.id, balance=1000) # Создаем
            s.add(user_db)
            s.commit()
            
        # Проверка на арест (заглушка)
        if arreste_msg := check_arrest_status(user_db):
            return await message.answer(arreste_msg)

        # Сбор доходов
        total_income_collected = 0
        owned_businesses = s.query(OwnedBusiness).filter_by(user_id=user_db.telegram_id).all()
        
        now = datetime.now()
        
        for ob in owned_businesses:
            biz_income = BUSINESSES[ob.business_id]['income']
            
            # Считаем, сколько часов прошло с последнего сбора
            # Используем max(0, ...) чтобы избежать отрицательного времени, хотя это должно быть невозможно при правильной логике
            hours_passed = int(max(0, (now - ob.last_collected).total_seconds() // 3600))
            
            if hours_passed >= 1:
                income_for_biz = hours_passed * ob.count * biz_income
                user_db.balance += income_for_biz
                total_income_collected += income_for_biz
                
                # Обновляем время последнего сбора, используя last_collected + n часов, 
                # чтобы избежать проблем с накоплением ошибки времени
                ob.last_collected = ob.last_collected + timedelta(hours=hours_passed)
        
        s.commit()
        # Сохраняем актуальный баланс и статус для вывода
        current_balance = user_db.balance
        
    business_info = format_business_list(owned_businesses)
    
    # Проверка на админ статус
    admin_status = "✅ Администратор" if user_db.telegram_id == OWNER_ID else "❌ Обычный пользователь"
    
    collected_message = ""
    if total_income_collected > 0:
        collected_message = f"🎉 *Собрано дохода: {total_income_collected:,}*💰\n"

    await message.answer(
        f"👤 *Профиль: {user_db.first_name}*\n"
        f"-----------------------------------\n"
        f"🆔 ID: `{user_db.telegram_id}`\n"
        f"💰 Баланс: *{current_balance:,}*💰\n"
        f"👑 Статус: {admin_status}\n"
        f"-----------------------------------\n"
        f"{collected_message}\n"
        f"{business_info}",
        reply_markup=main_keyboard
    )
# --- Бизнес ---

@router.message(F.text == BTN_BUSINESS)
async def business_menu_handler(message: types.Message):
    logging.debug(f"Received business menu request from user {message.from_user.id}")
    user = get_user(message.from_user.id)
    
    if arreste_msg := check_arrest_status(user):
        return await message.answer(arreste_msg)
    
    markup = InlineKeyboardMarkup(inline_keyboard=[])
    
    for biz_id, biz_info in BUSINESSES.items():
        markup.inline_keyboard.append([
            InlineKeyboardButton(
                text=f"{biz_info['name']} | Цена: {biz_info['cost']:,}💰 | Доход: {biz_info['income']:,}💰/час",
                callback_data=f"buy_biz_{biz_id}"
            )
        ])
    
    with Session() as s:
        owned_businesses = s.query(OwnedBusiness).filter_by(user_id=user.telegram_id).all()
        # Получаем актуальный баланс
        user_db = s.query(User).filter_by(telegram_id=user.telegram_id).first()
        current_balance = user_db.balance if user_db else user.balance
        
    business_info = format_business_list(owned_businesses)

    await message.answer(
        f"💼 *Меню Бизнеса*\n\n"
        f"Ваш текущий баланс: *{current_balance:,}*💰\n\n"
        f"{business_info}\n\n"
        f"Нажмите, чтобы приобрести один из доступных бизнесов:",
        reply_markup=markup
    )

@router.callback_query(F.data.startswith("buy_biz_"))
async def buy_business_callback_handler(callback: types.CallbackQuery):
    logging.debug(f"Received buy business callback from user {callback.from_user.id}: {callback.data}")
    biz_id = int(callback.data.split("_")[-1])
    biz = BUSINESSES.get(biz_id)
    
    if not biz: 
        await callback.answer("Ошибка: Бизнес не найден.", show_alert=True)
        return
    
    # Получаем актуального пользователя из базы для проверки баланса
    with Session() as s:
        user_db = s.query(User).filter_by(telegram_id=callback.from_user.id).first()
        if not user_db: 
            await callback.answer("Ошибка: Пользователь не найден.", show_alert=True)
            return
        current_balance = user_db.balance

    if current_balance < biz["cost"]:
        await callback.answer(f"Недостаточно средств. Нужно {biz['cost']:,}💰.", show_alert=True)
        return
        
    try:
        with Session() as s:
            u = s.query(User).filter_by(telegram_id=callback.from_user.id).first()
            u.balance -= biz["cost"]
            owned = s.query(OwnedBusiness).filter_by(user_id=callback.from_user.id, business_id=biz_id).first()
            if owned:
                owned.count += 1
            else:
                s.add(OwnedBusiness(user_id=callback.from_user.id, business_id=biz_id, count=1))
            s.commit()
            
            new_balance = u.balance
            
            await callback.message.answer(
                f"🎉 Поздравляем! Вы купили: *{biz['name']}*.\n"
                f"Новый баланс: *{new_balance:,}*💰",
                reply_markup=main_keyboard
            )
            await callback.answer("Покупка успешна!", show_alert=False)
            
            # Обновляем сообщение с меню бизнеса, чтобы отобразить изменения
            await business_menu_handler(callback.message)
            
    except SQLAlchemyError as e:
        logging.error(f"DB Error on buying business: {e}")
        await callback.answer("Произошла ошибка базы данных.", show_alert=True)
      # --- Казино (FSM) ---

@router.message(F.text == BTN_CASINO)
async def casino_menu_handler(message: types.Message, state: FSMContext):
    logging.debug(f"Received casino request from user {message.from_user.id}")
    user = get_user(message.from_user.id) # Используем get_user для актуального баланса
    if arreste_msg := check_arrest_status(user): return await message.answer(arreste_msg)

    # ИСПРАВЛЕНИЕ: Очищаем старое состояние, чтобы начать с нуля
    await state.clear() 
    await state.set_state(CasinoState.bet)
    
    await message.answer(
        f"🎰 *Казино - Орел или Решка*\n"
        f"💰 Ваш баланс: *{user.balance:,}*💰\n\n"
        f"Введите сумму ставки (минимум 100💰, максимум 100 000💰):",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="Отмена")]], 
            resize_keyboard=True, 
            one_time_keyboard=True
        )
    )

@router.message(CasinoState.bet)
async def casino_place_bet_handler(message: types.Message, state: FSMContext):
    logging.debug(f"Received casino bet from user {message.from_user.id}: {message.text}")
    
    # 1. Проверка отмены
    if message.text.lower() == "отмена":
        await state.clear()
        return await message.answer("❌ *Ставка отменена.*", reply_markup=main_keyboard)
        
    # 2. Проверка ставки (число)
    try:
        bet_amount = int(message.text.replace(' ', ''))
    except ValueError:
        return await message.answer("⚠️ Пожалуйста, введите корректное число для ставки.")

    # 3. Проверка лимитов ставки
    if bet_amount < 100 or bet_amount > 100_000:
        return await message.answer("⚠️ Минимальная ставка: 100💰. Максимальная: 100 000💰.")

    # 4. Проверка баланса (получаем актуальный объект для проверки)
    user = get_user(message.from_user.id, create_if_not_exists=False)
    if not user:
         await state.clear()
         return await message.answer("Ошибка: Пользователь не найден в БД.", reply_markup=main_keyboard)

    if user.balance < bet_amount:
        return await message.answer(f"⚠️ У вас недостаточно средств. Ваш баланс: {user.balance:,}💰.")

    # 5. Игра и обновление БД
    win = random.choice([True, False])
    new_balance = 0 
    
    try:
        with Session() as s:
            u = s.query(User).filter_by(telegram_id=user.telegram_id).with_for_update().first() # Блокировка строки на время транзакции
            if not u:
                await state.clear()
                return await message.answer("Ошибка: Пользователь не найден в БД.", reply_markup=main_keyboard)
                
            if win:
                u.balance += bet_amount
                result_text = f"🎉 *ПОБЕДА!* Вы выиграли *{bet_amount:,}*💰."
            else:
                u.balance -= bet_amount
                result_text = f"💸 *ПРОИГРЫШ!* Вы потеряли *{bet_amount:,}*💰."
                
            s.commit()
            
            # Получаем актуальный баланс после коммита
            new_balance = u.balance 
    
    except SQLAlchemyError as e:
        logging.error(f"DB Error on casino bet: {e}")
        await state.clear()
        return await message.answer("Произошла ошибка базы данных. Попробуйте еще раз.", reply_markup=main_keyboard)
        
    # 6. Очистка состояния и ответ пользователю
    await state.clear()
    await message.answer(
        f"{result_text}\n"
        f"💰 Новый баланс: *{new_balance:,}*💰", 
        reply_markup=main_keyboard
  )
  # --- Политика (Меню) ---

@router.message(F.text == BTN_POLITICS)
async def politics_menu_handler(message: types.Message):
    logging.debug(f"Received politics menu request from user {message.from_user.id}")
    user = get_user(message.from_user.id)
    if arreste_msg := check_arrest_status(user): return await message.answer(arreste_msg)

    with Session() as s:
        state = s.query(ElectionState).first()
        # outerjoin нужен для получения username, если он есть
        candidates = s.query(Candidate, User).outerjoin(User, Candidate.user_id == User.telegram_id).all()
        
    candidate_list = ""
    if state.phase != "IDLE":
        candidates_details = []
        for cand, cand_user in candidates:
            # Используем username, если доступен, иначе ID
            username = f"@{cand_user.username}" if cand_user and cand_user.username else f"ID: `{cand.user_id}`"
            candidates_details.append(f" - {username} ({cand.votes} голосов)")
        candidate_list = "\n".join(candidates_details) if candidates_details else "Нет кандидатов."
    
    markup = InlineKeyboardMarkup(inline_keyboard=[])
    status_text = ""
    
    if state.phase == "IDLE":
        time_left = format_time_left(state.last_election_time + ELECTION_COOLDOWN)
        status_text = f"⏳ Выборы неактивны. Следующие выборы через: {time_left}."
    elif state.phase == "CANDIDACY":
        time_left = format_time_left(state.end_time)
        status_text = f"🗳️ *Фаза: Регистрация кандидатов.* До окончания: {time_left}.\nТекущие кандидаты:\n{candidate_list}"
        if not any(c.user_id == user.telegram_id for c, u in candidates):
            markup.inline_keyboard.append([InlineKeyboardButton(text="Стать Кандидатом (10к💰)", callback_data="start_candidacy")])
        
    elif state.phase == "VOTING":
        time_left = format_time_left(state.end_time)
        status_text = f"🗳️ *Фаза: Голосование.* До окончания: {time_left}.\nТекущие кандидаты:\n{candidate_list}"
        vote_window_start = state.end_time - ELECTION_DURATION_VOTING 
        can_vote = (user.last_vote_time is None or user.last_vote_time < vote_window_start)
        
        if can_vote and candidates:
            vote_buttons = []
            for cand, cand_user in candidates:
                if cand_user:
                    name = f"@{cand_user.username}" if cand_user.username else f"ID {cand.user_id}"
                    vote_buttons.append(InlineKeyboardButton(text=f"Голосовать за {name}", callback_data=f"vote_{cand.user_id}"))
            
            for i in range(0, len(vote_buttons), 2):
                markup.inline_keyboard.append(vote_buttons[i:i+2])
        elif not can_vote:
            status_text += "\n\n❌ *Вы уже проголосовали на этих выборах.*"

    await message.answer(f"🏛 *Политический Центр*\n\n{status_text}", reply_markup=markup)

# --- Кандидатство и Голосование ---

@router.callback_query(F.data == "start_candidacy")
async def start_candidacy_handler(callback: types.CallbackQuery):
    logging.debug(f"Received candidacy start callback from user {callback.from_user.id}")
    await callback.answer() # ИСПРАВЛЕНИЕ: Отвечаем на колбэк немедленно
    user_id = callback.from_user.id
    CANDIDACY_COST = 10000
    
    # Получаем user из БД для актуальных данных
    with Session() as s:
        user_db = s.query(User).filter_by(telegram_id=user_id).first()
        if not user_db: 
            await callback.message.answer("Ошибка: Пользователь не найден.", reply_markup=main_keyboard)
            return
        
        if user_db.balance < CANDIDACY_COST:
            await callback.message.answer(f"Недостаточно средств. Нужно {CANDIDACY_COST:,}💰 для регистрации.", reply_markup=main_keyboard)
            return

        try:
            state = s.query(ElectionState).first()
            if state.phase != "CANDIDACY":
                await callback.message.answer("Регистрация кандидатов закрыта.", reply_markup=main_keyboard)
                return
            
            if s.query(Candidate).filter_by(user_id=user_id).first():
                await callback.message.answer("Вы уже являетесь кандидатом.", reply_markup=main_keyboard)
                return
                
            user_db.balance -= CANDIDACY_COST
            s.add(Candidate(user_id=user_id, votes=0))
            s.commit()
            
            await callback.message.answer(
                f"🎉 Вы успешно зарегистрировались как кандидат! Списано {CANDIDACY_COST:,}💰.",
                reply_markup=main_keyboard
            )
            # Перезагружаем меню политики
            await politics_menu_handler(callback.message)
            
        except SQLAlchemyError as e:
            logging.error(f"DB Error on candidacy: {e}")
            await callback.message.answer("Произошла ошибка базы данных.", reply_markup=main_keyboard)

@router.callback_query(F.data.startswith("vote_"))
async def vote_handler(callback: types.CallbackQuery):
    logging.debug(f"Received vote callback from user {callback.from_user.id}: {callback.data}")
    await callback.answer() # ИСПРАВЛЕНИЕ: Отвечаем на колбэк немедленно
    candidate_id = int(callback.data.split("_")[-1])
    user_id = callback.from_user.id
    
    try:
        with Session() as s:
            user_db = s.query(User).filter_by(telegram_id=user_id).first()
            if not user_db: 
                await callback.message.answer("Ошибка: Пользователь не найден.", reply_markup=main_keyboard)
                return

            state = s.query(ElectionState).first()
            if not state:
                await callback.message.answer("Ошибка: Не найдено состояние выборов в БД.", reply_markup=main_keyboard)
                return
                
            vote_window_start = state.end_time - ELECTION_DURATION_VOTING
            
            if state.phase != "VOTING": 
                await callback.message.answer("Голосование неактивно.", reply_markup=main_keyboard)
                return
            
            if user_db.last_vote_time and user_db.last_vote_time >= vote_window_start: 
                await callback.message.answer("Вы уже проголосовали на этих выборах.", reply_markup=main_keyboard)
                return

            candidate = s.query(Candidate).filter_by(user_id=candidate_id).first()
            if not candidate: 
                await callback.message.answer("Кандидат не найден.", reply_markup=main_keyboard)
                return

            candidate.votes += 1
            user_db.last_vote_time = datetime.now()
            s.commit()

        candidate_user = get_user(candidate_id)
        candidate_name = f"@{candidate_user.username}" if candidate_user and candidate_user.username else f"ID `{candidate_user.telegram_id}`"

        await callback.message.answer(f"✅ Вы успешно проголосовали за: *{candidate_name}*.", reply_markup=main_keyboard)
        # Перезагружаем меню политики
        await politics_menu_handler(callback.message)
        
    except SQLAlchemyError as e:
        logging.error(f"DB Error on voting: {e}")
        await callback.message.answer("Произошла ошибка базы данных.", reply_markup=main_keyboard)
      # =========================================================
# === 7. ОБРАБОТЧИКИ: АДМИН-ПАНЕЛЬ ===
# =========================================================

def is_admin(user_id):
    """Проверяет, является ли пользователь администратором."""
    return user_id == OWNER_ID

@router.message(F.text == BTN_ADMIN)
async def admin_menu_handler(message: types.Message):
    if not is_admin(message.from_user.id):
        return await message.answer("❌ Доступ запрещен. Только для администратора.")

    logging.debug(f"Admin menu request from {message.from_user.id}")
    
    with Session() as s:
        state = s.query(ElectionState).first()
        if not state:
            return await message.answer("Ошибка: Не найдено состояние выборов в БД.")

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Начать регистрацию кандидатов", callback_data="admin_start_candidacy")],
        [InlineKeyboardButton(text="Начать голосование", callback_data="admin_start_voting")],
        [InlineKeyboardButton(text="Завершить выборы и объявить результат", callback_data="admin_end_election")],
        [InlineKeyboardButton(text=f"Установить налог (Текущий: {state.tax_rate}%)", callback_data="admin_set_tax")]
    ])
    
    await message.answer(
        "👮‍♂️ *Админ-панель*\n\n"
        f"Текущая фаза выборов: *{state.phase}*",
        reply_markup=markup
    )

# Обработчик: Начать регистрацию кандидатов
@router.callback_query(F.data == "admin_start_candidacy")
async def admin_start_candidacy_handler(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id): 
        return await callback.answer("❌ Доступ запрещен.", show_alert=True)
    
    await callback.answer() # ИСПРАВЛЕНИЕ: Отвечаем на колбэк немедленно
    
    try:
        with Session() as s:
            state = s.query(ElectionState).first()
            
            # Очищаем старых кандидатов
            s.query(Candidate).delete()
            
            # Устанавливаем новую фазу
            state.phase = "CANDIDACY"
            state.end_time = datetime.now() + ELECTION_DURATION_CANDIDACY
            state.last_election_time = datetime.now()
            s.commit()
            
        await callback.message.edit_text(
            f"👮‍♂️ *Админ-панель*\n\n"
            f"Текущая фаза выборов: *{state.phase}* (до {state.end_time.strftime('%H:%M:%S')})",
            reply_markup=callback.message.reply_markup
        )
        await bot.send_message(callback.from_user.id, "📢 *Объявление:* Началась регистрация кандидатов! Длительность: 1 час.", reply_markup=main_keyboard)
        
    except SQLAlchemyError as e:
        logging.error(f"DB Error on admin_start_candidacy: {e}")
        await bot.send_message(callback.from_user.id, "Ошибка БД при начале регистрации.", reply_markup=main_keyboard)


# Обработчик: Начать голосование
@router.callback_query(F.data == "admin_start_voting")
async def admin_start_voting_handler(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id): 
        return await callback.answer("❌ Доступ запрещен.", show_alert=True)
    
    await callback.answer() # ИСПРАВЛЕНИЕ: Отвечаем на колбэк немедленно
    
    try:
        with Session() as s:
            state = s.query(ElectionState).first()
            
            # Проверка, есть ли кандидаты
            candidate_count = s.query(Candidate).count()
            if candidate_count == 0:
                await bot.send_message(callback.from_user.id, "Невозможно начать голосование: нет кандидатов.", reply_markup=main_keyboard)
                return

            state.phase = "VOTING"
            state.end_time = datetime.now() + ELECTION_DURATION_VOTING
            s.commit()
            
        await callback.message.edit_text(
            f"👮‍♂️ *Админ-панель*\n\n"
            f"Текущая фаза выборов: *{state.phase}* (до {state.end_time.strftime('%H:%M:%S')})",
            reply_markup=callback.message.reply_markup
        )
        await bot.send_message(callback.from_user.id, "📢 *Объявление:* Началось голосование! Длительность: 1 час.", reply_markup=main_keyboard)
        
    except SQLAlchemyError as e:
        logging.error(f"DB Error on admin_start_voting: {e}")
        await bot.send_message(callback.from_user.id, "Ошибка БД при начале голосования.", reply_markup=main_keyboard)

# Обработчик: Завершить выборы
@router.callback_query(F.data == "admin_end_election")
async def admin_end_election_handler(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id): 
        return await callback.answer("❌ Доступ запрещен.", show_alert=True)

    await callback.answer() # ИСПРАВЛЕНИЕ: Отвечаем на колбэк немедленно
    
    try:
        winner_id = None
        winner_name = "Нет"
        
        with Session() as s:
            state = s.query(ElectionState).first()
            
            if state.phase == "IDLE":
                await bot.send_message(callback.from_user.id, "Выборы неактивны.", reply_markup=main_keyboard)
                return

            # Определяем победителя
            candidates = s.query(Candidate).order_by(Candidate.votes.desc()).limit(1).first()
            
            if candidates:
                winner_id = candidates.user_id
                # Получаем имя победителя для объявления
                winner_user = s.query(User).filter_by(telegram_id=winner_id).first()
                if winner_user:
                    winner_name = f"@{winner_user.username}" if winner_user.username else winner_user.first_name
            
            # Сброс состояния
            state.phase = "IDLE"
            state.end_time = datetime.now() 
            # Очистка кандидатов после завершения выборов
            s.query(Candidate).delete() 
            s.commit()

        # Объявление
        if winner_id:
            message_text = f"🎉 *ВЫБОРЫ ЗАВЕРШЕНЫ!* 🎉\n\n" \
                           f"Новый лидер: *{winner_name}* (ID: `{winner_id}`).\n" \
                           f"Началась пауза до следующих выборов ({format_time_left(datetime.now() + ELECTION_COOLDOWN)})."
        else:
            message_text = "🗳️ *ВЫБОРЫ ЗАВЕРШЕНЫ!* \n\n" \
                           "Не было кандидатов или голосов. Фаза сброшена."
                           
        await callback.message.edit_text(
            f"👮‍♂️ *Админ-панель*\n\n"
            f"Текущая фаза выборов: *{state.phase}*",
            reply_markup=callback.message.reply_markup
        )
        await bot.send_message(callback.from_user.id, message_text, reply_markup=main_keyboard) 
        
    except SQLAlchemyError as e:
        logging.error(f"DB Error on admin_end_election: {e}")
        await bot.send_message(callback.from_user.id, "Ошибка БД при завершении выборов.", reply_markup=main_keyboard)
        
# --- Пока заглушка для установки налога ---
@router.callback_query(F.data == "admin_set_tax")
async def admin_set_tax_handler(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id): 
        return await callback.answer("❌ Доступ запрещен.", show_alert=True)
    
    # ИСПРАВЛЕНИЕ: Отвечаем на колбэк немедленно
    await callback.answer("Функция установки налога пока не реализована.", show_alert=True)
    # Здесь должна быть логика для ввода новой ставки налога


# =========================================================
# === 8. ЗАПУСК БОТА ===
# =========================================================

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
