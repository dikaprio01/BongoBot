import datetime # ИСПРАВЛЕНО
import asyncio
import os
import logging
import random
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject
from aiogram.enums import ParseMode
from aiogram.utils.keyboard import InlineKeyboardBuilder

from sqlalchemy import create_engine, Column, Integer, String, BigInteger, Boolean, update, select, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, relationship # ФИКС: Исправлено предупреждение SQLAlchemy
from sqlalchemy.future import select

from apscheduler.schedulers.asyncio import AsyncIOScheduler

# --- Константы и Настройки ---
logging.basicConfig(level=logging.INFO)

# ID владельца (АДМИНА) бота (ИЗМЕНИ НА СВОЙ ID)
ADMIN_ID = 1871352653 

# Настройки SQLite 
DB_PATH = "sqlite:///data/bongobot.db"

# Настройки времени (в секундах)
JOB_COOLDOWN_SECONDS = 3600 # 1 час
ELECTION_COOLDOWN_SECONDS = 86400 # 24 часа
CANDIDATE_PERIOD_SECONDS = 1800 # 30 минут на набор кандидатов
VOTING_PERIOD_SECONDS = 3600  # 60 минут на голосование

# Состояние выборов (для Scheduler)
ELECTION_STATE = "NONE" # NONE, CANDIDATE_REG, VOTING

Base = declarative_base()

# --- Модели Базы Данных ---
class User(Base):
    __tablename__ = 'users'
    id = Column(BigInteger, primary_key=True, autoincrement=False)
    username = Column(String)
    role = Column(String, default="Игрок")
    is_owner = Column(Boolean, default=False)
    balance = Column(Integer, default=500)
    property_count = Column(Integer, default=0) 
    xp = Column(Integer, default=0)
    is_president = Column(Boolean, default=False)
    last_work_time = Column(BigInteger, default=0) 
    last_election_time = Column(BigInteger, default=0) # Время последнего участия в выборах/голосовании

class Candidate(Base):
    __tablename__ = 'candidates'
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, ForeignKey('users.id'), unique=True)
    votes = Column(Integer, default=0)
    
    # Связь с таблицей User
    user = relationship("User") 

class Chat(Base):
    __tablename__ = 'chats'
    id = Column(BigInteger, primary_key=True, autoincrement=False) # ID чата/группы
    last_message_id = Column(BigInteger, default=0) # ID последнего сообщения для уведомлений

# --- Настройка SQLAlchemy ---
engine = create_engine(DB_PATH, connect_args={"check_same_thread": False})
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)

# --- Настройка Бота и Планировщика ---
TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler()


# --- Вспомогательные синхронные функции для работы с БД ---

def get_user_profile_sync(user_id: int, username: str):
    session = Session()
    try:
        user = session.get(User, user_id)
        
        if user is None:
            # Создание нового профиля
            user = User(
                id=user_id,
                username=username,
                balance=500
            )
            if user_id == ADMIN_ID:
                user.is_owner = True
            
            session.add(user)
            session.commit()
            
        user = session.merge(user)
        return user
    finally:
        session.close()

def update_user_sync(user_id: int, **kwargs):
    """Обновляет любые поля пользователя по ID."""
    session = Session()
    try:
        user = session.get(User, user_id)
        if user:
            for key, value in kwargs.items():
                setattr(user, key, value)
            session.commit()
            user = session.merge(user)
        return user
    finally:
        session.close()

def get_all_users_sync():
    """Получает всех пользователей для топа."""
    session = Session()
    try:
        users = session.execute(select(User).order_by(User.balance.desc())).scalars().all()
        users = [session.merge(u) for u in users]
        return users
    finally:
        session.close()

def save_chat_sync(chat_id: int):
    """Сохраняет ID чата для рассылки уведомлений."""
    session = Session()
    try:
        chat = session.get(Chat, chat_id)
        if chat is None:
            chat = Chat(id=chat_id)
            session.add(chat)
            session.commit()
        return True
    finally:
        session.close()

def get_all_chats_sync():
    """Получает все ID чатов для рассылки."""
    session = Session()
    try:
        chats = session.execute(select(Chat.id)).scalars().all()
        return chats
    finally:
        session.close()

# --- Логика Выборов: Шаг 1 (Набор кандидатов) ---

def start_candidate_registration():
    """Начинает период регистрации кандидатов."""
    global ELECTION_STATE
    ELECTION_STATE = "CANDIDATE_REG"
    logging.info("--- НАЧАЛО РЕГИСТРАЦИИ КАНДИДАТОВ ---")
    
    # 1. Сброс предыдущих кандидатов и голосов
    session = Session()
    try:
        session.query(Candidate).delete()
        session.query(User).filter(User.is_president == True).update({User.is_president: False, User.role: "Игрок"})
        session.commit()
    finally:
        session.close()
        
    # 2. Планирование следующего шага
    scheduler.add_job(
        end_candidate_registration,
        'date',
        run_date=datetime.datetime.now() + datetime.timedelta(seconds=CANDIDATE_PERIOD_SECONDS)
    )

    # 3. Отправка уведомлений в чаты
    asyncio.create_task(notify_chats_registration_start())

async def notify_chats_registration_start():
    """Отправляет уведомления о начале регистрации."""
    chats = await asyncio.to_thread(get_all_chats_sync)
    message_text = (
        "📣 **НАЧАЛО ВЫБОРОВ!** 📣\n\n"
        "Объявляется **Набор Кандидатов** на пост Президента.\n"
        "Чтобы подать заявку, напишите команду: **`/candidate`**\n"
        f"⏳ Набор продлится **{CANDIDATE_PERIOD_SECONDS // 60} минут**."
    )
    for chat_id in chats:
        try:
            await bot.send_message(chat_id, message_text, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logging.error(f"Не удалось отправить сообщение в чат {chat_id}: {e}")

# --- Логика Выборов: Шаг 2 (Голосование) ---

def end_candidate_registration():
    """Завершает период регистрации и начинает голосование."""
    global ELECTION_STATE
    
    # Получаем кандидатов
    session = Session()
    # Используем relationship для загрузки данных пользователя, связанных с кандидатом
    candidates = session.execute(select(Candidate).options(relationship(Candidate.user))).scalars().all()
    session.close()
    
    if not candidates:
        ELECTION_STATE = "NONE"
        asyncio.create_task(notify_chats_no_candidates())
        logging.info("--- ВЫБОРЫ ОТМЕНЕНЫ (НЕТ КАНДИДАТОВ) ---")
        return

    ELECTION_STATE = "VOTING"
    logging.info("--- НАЧАЛО ГОЛОСОВАНИЯ ---")
    
    # Планирование следующего шага
    scheduler.add_job(
        end_voting_and_announce_winner,
        'date',
        run_date=datetime.datetime.now() + datetime.timedelta(seconds=VOTING_PERIOD_SECONDS)
    )

    # Отправка уведомлений в чаты
    asyncio.create_task(notify_chats_voting_start(candidates))

async def notify_chats_no_candidates():
    """Уведомление об отмене выборов."""
    chats = await asyncio.to_thread(get_all_chats_sync)
    message_text = "❌ **ВЫБОРЫ ОТМЕНЕНЫ.** Ни один кандидат не подал заявку."
    for chat_id in chats:
        try:
            await bot.send_message(chat_id, message_text, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logging.error(f"Не удалось отправить сообщение об отмене в чат {chat_id}: {e}")

async def notify_chats_voting_start(candidates):
    """Отправляет уведомления о начале голосования."""
    chats = await asyncio.to_thread(get_all_chats_sync)
    
    # Строим список кандидатов
    candidate_list = "\n".join([f"👤 @{c.user.username}" for c in candidates])

    message_text = (
        "🗳️ **ГОЛОСОВАНИЕ НАЧАЛОСЬ!** 🗳️\n\n"
        "**Кандидаты:**\n"
        f"{candidate_list}\n\n"
        "Чтобы проголосовать, используйте команду:\n"
        "**`/vote [ID_пользователя]`**\n"
        f"⏳ Голосование продлится **{VOTING_PERIOD_SECONDS // 60} минут**."
    )
    
    builder = InlineKeyboardBuilder()
    for candidate in candidates:
        builder.button(text=f"Голосовать за @{candidate.user.username}", callback_data=f"vote_{candidate.user_id}")
    builder.adjust(1) # Кнопки в столбик
    
    for chat_id in chats:
        try:
            await bot.send_message(chat_id, message_text, reply_markup=builder.as_markup(), parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logging.error(f"Не удалось отправить сообщение о голосовании в чат {chat_id}: {e}")

# --- Логика Выборов: Шаг 3 (Результаты) ---

def end_voting_and_announce_winner():
    """Завершает голосование и объявляет победителя."""
    global ELECTION_STATE
    ELECTION_STATE = "NONE"
    
    session = Session()
    candidates = session.execute(select(Candidate).order_by(Candidate.votes.desc()).options(relationship(Candidate.user))).scalars().all()
    session.close()
    
    if not candidates:
        logging.info("--- ВЫБОРЫ ЗАВЕРШЕНЫ (СБОЙ) ---")
        return

    winner_candidate = candidates[0]
    
    # 1. Обновление статуса победителя
    if winner_candidate:
        asyncio.create_task(
            asyncio.to_thread(
                update_user_sync,
                winner_candidate.user_id,
                is_president=True,
                role="Президент"
            )
        )
        
    # 2. Отправка уведомлений
    asyncio.create_task(notify_chats_winner(candidates, winner_candidate))
    logging.info(f"--- ПОБЕДИТЕЛЬ: {winner_candidate.user.username} с {winner_candidate.votes} голосами ---")

async def notify_chats_winner(candidates, winner):
    """Отправляет уведомления о результатах."""
    chats = await asyncio.to_thread(get_all_chats_sync)
    
    # Составляем итоговый список голосов
    results_list = "\n".join([f"👤 @{c.user.username}: **{c.votes} голосов**" for c in candidates])
    
    message_text = (
        "👑 **ПРЕЗИДЕНТ ВЫБРАН!** 👑\n\n"
        f"По итогам голосования, новым Президентом становится:\n"
        f"**@{winner.user.username}** с численностью голосов **{winner.votes}**!\n\n"
        "**Итоговые результаты:**\n"
        f"{results_list}"
    )
    
    for chat_id in chats:
        try:
            await bot.send_message(chat_id, message_text, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logging.error(f"Не удалось отправить сообщение о победителе в чат {chat_id}: {e}")

# --- Хэндлеры для Игрового Функционала ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    # При /start профиль будет создан, если его нет, и чат сохранен
    await asyncio.to_thread(
        get_user_profile_sync,
        message.from_user.id,
        message.from_user.username or message.from_user.first_name
    )
    # Сохраняем ID чата для рассылки уведомлений
    await asyncio.to_thread(save_chat_sync, message.chat.id)
    
    await message.answer("🎉 Добро пожаловать в BongoBot! 🎉\n\n"
                         "Напиши /profile, чтобы увидеть свой счет.\n"
                         "Используй /work, чтобы заработать денег.")


@dp.message(Command("profile"))
async def cmd_profile(message: types.Message):
    """Показывает профиль пользователя."""
    user_data = await asyncio.to_thread(
        get_user_profile_sync,
        message.from_user.id,
        message.from_user.username or message.from_user.first_name
    )

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
        f"🏡 Имущество: {user_data.property_count} объектов\n"
        f"---"
        f"\nИспользуй /work, чтобы заработать денег."
    )
    
    await message.answer(profile_text, parse_mode=ParseMode.MARKDOWN)


@dp.message(Command("work"))
async def cmd_work(message: types.Message):
    """Позволяет пользователю работать и зарабатывать деньги."""
    user_id = message.from_user.id
    current_time = int(datetime.datetime.now().timestamp()) # ИСПРАВЛЕНО
    
    user_data = await asyncio.to_thread(
        get_user_profile_sync,
        user_id,
        message.from_user.username or message.from_user.first_name
    )
    
    # Проверка на кулдаун (cooldown)
    time_elapsed = current_time - user_data.last_work_time
    if time_elapsed < JOB_COOLDOWN_SECONDS:
        remaining_time = JOB_COOLDOWN_SECONDS - time_elapsed
        minutes = remaining_time // 60
        seconds = remaining_time % 60
        return await message.answer(
            f"❌ Вы устали. Вы сможете снова работать через **{minutes} мин {seconds} сек**."
        )

    # Расчет заработка
    money_earned = random.randint(50, 150)
    
    # Обновление данных (баланс и время работы)
    new_balance = user_data.balance + money_earned 
    
    user_data = await asyncio.to_thread(
        update_user_sync,
        user_id,
        balance=new_balance,
        last_work_time=current_time
    )

    await message.answer(
        f"👷 Вы поработали на стройке и заработали **{money_earned} Bongo$**! 💵\n"
        f"Ваш новый баланс: **{user_data.balance:,} Bongo$**",
        parse_mode=ParseMode.MARKDOWN
    )


@dp.message(Command("buy_house"))
async def cmd_buy_house(message: types.Message):
    """Позволяет купить недвижимость (для примера)."""
    HOUSE_PRICE = 5000
    user_id = message.from_user.id
    
    user_data = await asyncio.to_thread(
        get_user_profile_sync,
        user_id,
        message.from_user.username or message.from_user.first_name
    )

    if user_data.balance < HOUSE_PRICE:
        return await message.answer(
            f"❌ Для покупки дома нужно **{HOUSE_PRICE:,} Bongo$**. У вас только **{user_data.balance:,} Bongo$**."
        )

    # Обновление данных (списываем деньги и добавляем имущество)
    new_balance = user_data.balance - HOUSE_PRICE
    
    user_data = await asyncio.to_thread(
        update_user_sync,
        user_id,
        balance=new_balance,
        property_count=user_data.property_count + 1
    )

    await message.answer(
        f"✅ Вы купили **новый дом** за **{HOUSE_PRICE:,} Bongo$**!\n"
        f"Ваш баланс: **{user_data.balance:,} Bongo$**\n"
        f"Имущество: **{user_data.property_count}**",
        parse_mode=ParseMode.MARKDOWN
    )


@dp.message(Command("top"))
async def cmd_top(message: types.Message):
    """Показывает топ 10 самых богатых игроков."""
    users = await asyncio.to_thread(get_all_users_sync)
    
    top_list = "🏆 **ТОП-10 САМЫХ БОГАТЫХ ИГРОКОВ** 🏆\n\n"
    
    for i, user in enumerate(users[:10], 1):
        role = "👑" if user.is_president else ""
        top_list += f"{i}. {role} @{user.username} — **{user.balance:,} Bongo$**\n"
    
    await message.answer(top_list, parse_mode=ParseMode.MARKDOWN)

# --- Системные и Админ-Команды ---

@dp.message(Command("election"))
async def cmd_election(message: types.Message):
    """Показывает текущее состояние выборов."""
    
    if ELECTION_STATE == "CANDIDATE_REG":
        return await message.answer(f"⏳ **ВЫБОРЫ:** Сейчас идет **Набор Кандидатов** (до {CANDIDATE_PERIOD_SECONDS // 60} минут). Используйте `/candidate`.")
    
    if ELECTION_STATE == "VOTING":
        return await message.answer(f"🗳️ **ВЫБОРЫ:** Идет **Голосование** (до {VOTING_PERIOD_SECONDS // 60} минут). Используйте `/vote [ID_кандидата]`.")

    # Если выборы не идут, показываем текущего президента
    president_user = await asyncio.to_thread(
        lambda: Session().execute(select(User).filter_by(is_president=True)).scalars().first()
    )
    
    if president_user:
        return await message.answer(f"👑 Текущий Президент: **@{president_user.username}**.")
    else:
        return await message.answer("ℹ️ Президент не выбран. Администратор может начать выборы командой `/start_elections`.")


@dp.message(Command("candidate"))
async def cmd_candidate(message: types.Message):
    """Подать заявку на пост президента."""
    user_id = message.from_user.id
    
    if ELECTION_STATE != "CANDIDATE_REG":
        return await message.answer("❌ Заявки можно подавать только во время **Набора Кандидатов**.")
        
    user_data = await asyncio.to_thread(
        get_user_profile_sync,
        user_id,
        message.from_user.username or message.from_user.first_name
    )

    # Проверка кулдауна (чтобы один игрок голосовал/баллотировался раз в 24 часа)
    current_time = int(datetime.datetime.now().timestamp())
    time_elapsed = current_time - user_data.last_election_time
    if time_elapsed < ELECTION_COOLDOWN_SECONDS:
        hours = ELECTION_COOLDOWN_SECONDS // 3600
        return await message.answer(f"❌ Вы можете участвовать в выборах или голосовать только раз в **{hours} часов**.")

    # Проверка: не является ли уже кандидатом
    session = Session()
    # Ищем кандидата по user_id
    existing_candidate = session.execute(select(Candidate).where(Candidate.user_id == user_id)).scalars().first()
    session.close()
    
    if existing_candidate:
        return await message.answer("❌ Вы уже зарегистрированы как кандидат.")

    # Регистрация
    session = Session()
    try:
        candidate = Candidate(user_id=user_id)
        session.add(candidate)
        
        # Обновляем время участия игрока (кулдаун)
        await asyncio.to_thread(
            update_user_sync,
            user_id,
            last_election_time=current_time
        )
        
        session.commit()
        await message.answer("✅ **Поздравляем!** Ваша заявка принята. Ожидайте начала голосования.")
    finally:
        session.close()

@dp.message(Command("vote"))
async def cmd_vote(message: types.Message, command: CommandObject):
    """Отдать голос за кандидата."""
    voter_id = message.from_user.id
    
    if ELECTION_STATE != "VOTING":
        return await message.answer("❌ Голосование открыто только в период **Голосования**.")
    
    if not command.args:
        return await message.answer("Использование: /vote [ID_кандидата]")

    try:
        candidate_id = int(command.args.split()[0])
    except ValueError:
        return await message.answer("ID кандидата должен быть числом.")

    # Проверка кулдауна голосующего
    voter_data = await asyncio.to_thread(
        get_user_profile_sync,
        voter_id,
        message.from_user.username or message.from_user.first_name
    )
    current_time = int(datetime.datetime.now().timestamp())
    time_elapsed = current_time - voter_data.last_election_time
    if time_elapsed < ELECTION_COOLDOWN_SECONDS:
        return await message.answer("❌ Вы уже участвовали в выборах или голосовали. Вы сможете снова голосовать через 24 часа.")

    # Проверка: существует ли кандидат (ищем по user_id)
    session = Session()
    candidate_record = session.execute(select(Candidate).where(Candidate.user_id == candidate_id)).scalars().first()
    
    if candidate_record is None:
        session.close()
        return await message.answer(f"❌ Кандидат с ID `{candidate_id}` не найден.")
    
    # Проверка: нельзя голосовать за себя (хотя это косвенно запрещает кулдаун, лучше перестраховаться)
    if candidate_id == voter_id:
        session.close()
        return await message.answer("❌ Вы не можете голосовать за себя.")

    # Увеличение голоса и обновление кулдауна
    try:
        candidate_record.votes += 1
        
        # Обновляем время участия игрока (кулдаун)
        await asyncio.to_thread(
            update_user_sync,
            voter_id,
            last_election_time=current_time
        )
        
        session.commit()
        await message.answer(f"✅ Вы успешно отдали свой голос за кандидата с ID `{candidate_id}`.")
   
