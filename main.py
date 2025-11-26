import datetime
import asyncio
import os
import logging
import random
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject
from aiogram.enums import ParseMode
from aiogram.utils.keyboard import InlineKeyboardBuilder

from sqlalchemy import create_engine, Column, Integer, String, BigInteger, Boolean, update, select, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, relationship 
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
BUSINESS_PAYOUT_INTERVAL_SECONDS = 3600 # Выплата каждый час

# --- НОВЫЕ ЭКОНОМИЧЕСКИЕ КОНСТАНТЫ ---
# Бизнесы: Цена, Название, Доход в час
BUSINESSES = {
    1: {"name": "Уличный Ларек", "price": 100_000, "hourly_income": 2_000},
    2: {"name": "Автомойка", "price": 500_000, "hourly_income": 8_000},
    3: {"name": "ТехноХаб", "price": 1_000_000, "hourly_income": 15_000},
}

# Имущество: Цена, Название (для команды /buy_property)
PROPERTIES = {
    1: {"name": "Маленькая квартира", "price": 5_000}, # Старый дом
    2: {"name": "Роскошная вилла", "price": 50_000},
    3: {"name": "Частный остров", "price": 250_000},
}

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
    last_election_time = Column(BigInteger, default=0) 

class Candidate(Base):
    __tablename__ = 'candidates'
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, ForeignKey('users.id'), unique=True)
    votes = Column(Integer, default=0)
    
    # Связь с таблицей User
    user = relationship("User")
    class OwnedBusiness(Base):
    __tablename__ = 'owned_businesses'
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, ForeignKey('users.id'))
    business_id = Column(Integer) # ID бизнеса из словаря BUSINESSES
    name = Column(String) # Название бизнеса
    count = Column(Integer, default=1) # Количество одинаковых бизнесов

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

def apply_tax_sync(tax_percent: float, president_id: int):
    """Применяет КОМПЛЕКСНЫЙ налог (баланс, имущество, бизнес) ко всем игрокам, кроме президента и админа, и возвращает общую собранную сумму."""
    session = Session()
    total_tax_collected = 0
    
    try:
        # 1. Получаем всех владельцев бизнесов и рассчитываем их общий часовой доход
        businesses = session.query(OwnedBusiness).all()
        user_business_income = {}
        for ob in businesses:
            biz_data = BUSINESSES.get(ob.business_id)
            if biz_data:
                income = biz_data['hourly_income'] * ob.count
                user_business_income[ob.user_id] = user_business_income.get(ob.user_id, 0) + income

        # 2. Получаем пользователей, которые будут платить налог
        users_to_tax = session.execute(
            select(User)
            .filter(User.id != president_id)
            .filter(User.id != ADMIN_ID)
            # Применяем налог, если есть баланс ИЛИ имущество ИЛИ бизнес-доход
            .filter( (User.balance > 1000) | (User.property_count > 0) | (User.id.in_(user_business_income.keys())) )
        ).scalars().all()
        
        # 3. Применяем налоги
        for user in users_to_tax:
            # --- Tax 1: Налог на Капитал (на баланс) ---
            taxable_balance = user.balance - 1000 # Налог только на баланс выше 1000
            wealth_tax = int(taxable_balance * (tax_percent / 100)) if taxable_balance > 0 else 0
            
            # --- Tax 2: Налог на Имущество ---
            PROPERTY_TAX_VALUE = PROPERTIES.get(1)['price']
            property_value = user.property_count * PROPERTY_TAX_VALUE
            property_tax = int(property_value * (tax_percent / 100))
            
            # --- Tax 3: Налог на Бизнес (на часовой доход) ---
            total_hourly_income = user_business_income.get(user.id, 0)
            business_tax = int(total_hourly_income * (tax_percent / 100))
            
            # Общий налог
            total_tax = wealth_tax + property_tax + business_tax
            
            if total_tax > 0:
                tax_to_pay = min(total_tax, user.balance) 
                
                user.balance -= tax_to_pay
                total_tax_collected += tax_to_pay
        
        # 4. Начисляем собранную сумму Президенту
        president = session.get(User, president_id)
        if president:
            president.balance += total_tax_collected
        
        session.commit()
        return total_tax_collected
        
    except Exception as e:
        session.rollback()
        logging.error(f"Ошибка при сборе налога: {e}")
        return 0
    finally:
        session.close()
        # --- Логика Пассивного Дохода ---

async def business_payout_job():
    """Запускается каждый час для выплаты дохода владельцам бизнесов."""
    session = Session()
    try:
        # 1. Получаем все бизнесы и рассчитываем общие выплаты
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

        # 2. Обновляем балансы и уведомляем
        for user_id, amount in payouts.items():
            
            # Обновление баланса (используем update_user_sync через asyncio.to_thread)
            # Внимание: тут нужно аккуратно обращаться к сессии, чтобы не вызвать конфликты.
            # Правильно будет использовать update_user_sync, которая управляет своей сессией.
            await asyncio.to_thread(
                lambda uid, amt: update_user_sync(uid, balance=User.balance + amt),
                user_id, amount
            )
            
            # Отправка уведомления
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
    builder.adjust(1) 
    
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
    await asyncio.to_thread(
        get_user_profile_sync,
        message.from_user.id,
        message.from_user.username or message.from_user.first_name
    )
    await asyncio.to_thread(save_chat_sync, message.chat.id)
    
    await message.answer("🎉 Добро пожаловать в BongoBot! 🎉\n\n"
                         "Напиши /profile, чтобы увидеть свой счет.\n"
                         "Используй /work, чтобы заработать денег.")


@dp.message(Command("profile"))
async def cmd_profile(message: types.Message):
    """Показывает профиль пользователя."""
    user_id = message.from_user.id
    user_data = await asyncio.to_thread(
        get_user_profile_sync,
        user_id,
        message.from_user.username or message.from_user.first_name
    )
    
    # Получение информации о бизнесах
    session = Session()
    owned_businesses = session.execute(select(OwnedBusiness).filter_by(user_id=user_id)).scalars().all()
    session.close()
    
    total_hourly_income = sum(
        BUSINESSES.get(b.business_id)['hourly_income'] * b.count 
        for b in owned_businesses 
        if BUSINESSES.get(b.business_id)
    )
    
    business_text = "\n".join(
        [f"   💼 {b.name}: {b.count} шт." for b in owned_businesses]
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


@dp.message(Command("work"))
async def cmd_work(message: types.Message):
    """Позволяет пользователю работать и зарабатывать деньги."""
    user_id = message.from_user.id
    current_time = int(datetime.datetime.now().timestamp())
    
    user_data = await asyncio.to_thread(
        get_user_profile_sync,
        user_id,
        message.from_user.username or message.from_user.first_name
    )
    
    time_elapsed = current_time - user_data.last_work_time
    if time_elapsed < JOB_COOLDOWN_SECONDS:
        remaining_time = JOB_COOLDOWN_SECONDS - time_elapsed
        minutes = remaining_time // 60
        seconds = remaining_time % 60
        return await message.answer(
            f"❌ Вы устали. Вы сможете снова работать через **{minutes} мин {seconds} сек**."
        )

    money_earned = random.randint(50, 150)
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


@dp.message(Command("properties"))
async def cmd_properties(message: types.Message):
    """Показывает список доступного имущества для покупки."""
    property_list = "🏡 **ДОСТУПНОЕ ИМУЩЕСТВО:** 🏡\n\n"
    
    for prop_id, data in PROPERTIES.items():
        property_list += (
            f"**{prop_id}. {data['name']}**\n"
            f"   💰 Цена: **{data['price']:,} Bongo$**\n\n"
        )
    
    property_list += "Для покупки используйте: `/buy_property [номер_имущества]`"
    await message.answer(property_list, parse_mode=ParseMode.MARKDOWN)


@dp.message(Command("buy_property", "buy_house")) 
async def cmd_buy_property(message: types.Message, command: CommandObject):
    """Позволяет купить недвижимость."""
    user_id = message.from_user.id
    
    if not command.args:
        return await message.answer("Использование: /buy_property [номер_имущества] (или /properties, чтобы увидеть список)")
        
    try:
        prop_id = int(command.args.split()[0])
        prop_data = PROPERTIES.get(prop_id)
    except (ValueError, IndexError):
        return await message.answer("❌ Номер имущества должен быть числом.")

    if not prop_data:
        return await message.answer("❌ Имущество с таким номером не найдено. Используйте `/properties`, чтобы увидеть список.")

    PROPERTY_PRICE = prop_data['price']
    PROPERTY_NAME = prop_data['name']
    
    user_data = await asyncio.to_thread(
        get_user_profile_sync,
        user_id,
        message.from_user.username or message.from_user.first_name
    )

    if user_data.balance < PROPERTY_PRICE:
        return await message.answer(
            f"❌ Для покупки **{PROPERTY_NAME}** нужно **{PROPERTY_PRICE:,} Bongo$**. У вас только **{user_data.balance:,} Bongo$**."
        )

    new_balance = user_data.balance - PROPERTY_PRICE
    
    user_data = await asyncio.to_thread(
        update_user_sync,
        user_id,
        balance=new_balance,
        property_count=user_data.property_count + 1
    )

    await message.answer(
        f"✅ Вы купили **{PROPERTY_NAME}** за **{PROPERTY_PRICE:,} Bongo$**!\n"
        f"Ваш баланс: **{user_data.balance:,} Bongo$**\n"
        f"Имущество: **{user_data.property_count}** объектов",
        parse_mode=ParseMode.MARKDOWN
    )


@dp.message(Command("businesses"))
async def cmd_businesses(message: types.Message):
    """Показывает список доступных бизнесов для покупки."""
    business_list = "💼 **ДОСТУПНЫЕ БИЗНЕСЫ:** 💼\n\n"
    
    for biz_id, data in BUSINESSES.items():
        business_list += (
            f"**{biz_id}. {data['name']}**\n"
            f"   💰 Цена: **{data['price']:,} Bongo$**\n"
            f"   💵 Доход в час: **{data['hourly_income']:,} Bongo$**\n\n"
        )
    
    business_list += "Для покупки используйте: `/buy_business [номер_бизнеса]`"
    await message.answer(business_list, parse_mode=ParseMode.MARKDOWN)


@dp.message(Command("buy_business"))
async def cmd_buy_business(message: types.Message, command: CommandObject):
    """Позволяет купить бизнес."""
    user_id = message.from_user.id
    
    if not command.args:
        return await message.answer("Использование: /buy_business [номер_бизнеса]")
    
    try:
        biz_id = int(command.args.split()[0])
        biz_data = BUSINESSES.get(biz_id)
    except (ValueError, IndexError):
        return await message.answer("❌ Номер бизнеса должен быть числом.")

    if not biz_data:
        return await message.answer("❌ Бизнес с таким номером не найден. Используйте `/businesses`, чтобы увидеть список.")

    BUSINESS_PRICE = biz_data['price']
    
    user_data = await asyncio.to_thread(
        get_user_profile_sync,
        user_id,
        message.from_user.username or message.from_user.first_name
    )

    if user_data.balance < BUSINESS_PRICE:
        return await message.answer(
            f"❌ Для покупки **{biz_data['name']}** нужно **{BUSINESS_PRICE:,} Bongo$**. У вас только **{user_data.balance:,} Bongo$**."
        )

    session = Session()
    try:
        existing_business = session.execute(
            select(OwnedBusiness)
            .filter_by(user_id=user_id, business_id=biz_id)
        ).scalars().first()
        
        if existing_business:
            existing_business.count += 1
            new_count = existing_business.count
        else:
            new_business = OwnedBusiness(
                user_id=user_id,
                business_id=biz_id,
                name=biz_data['name'],
                count=1
            )
            session.add(new_business)
            new_count = 1
        
        new_balance = user_data.balance - BUSINESS_PRICE
        
        await asyncio.to_thread(
            update_user_sync,
            user_id,
            balance=new_balance
        )

        session.commit()
        
        await message.answer(
            f"✅ Поздравляем! Вы купили **{biz_data['name']}** за **{BUSINESS_PRICE:,} Bongo$**.\n"
            f"Теперь у вас **{new_count}** таких бизнесов.\n"
            f"💰 Новый баланс: **{new_balance:,} Bongo$**",
            parse_mode=ParseMode.MARKDOWN
        )
        
    except Exception as e:
        session.rollback()
        await message.answer(f"❌ Произошла ошибка при покупке бизнеса: {e}")
        logging.error(f"Business buy error: {e}")
    finally:
        session.close()


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

    president_user = await asyncio.to_thread(
        lambda: Session().execute(select(User).filter_by(is_president=True)).scalars().first()
    )
    
    if president_user:
        return await message.answer(f"👑 Текущий Президент: **@{president_user.username}**.")
    else:
        return await message.answer("ℹ️ Президент не выбран. Администратор может начать выборы командой `/start_elections`.")


@dp.message(Command("tax"))
async def cmd_tax(message: types.Message, command: CommandObject):
    """Позволяет Президенту установить и собрать налог с игроков (Макс 5%)."""
    user_id = message.from_user.id
    
    user_data = await asyncio.to_thread(
        get_user_profile_sync,
        user_id,
        message.from_user.username or message.from_user.first_name
    )
    if not user_data.is_president:
        return await message.answer("❌ Эта команда доступна **только Президенту**.")

    if not command.args:
        return await message.answer("Использование: /tax [1-5] (процент налога).")

    try:
        tax_percent = int(command.args.split()[0])
    except ValueError:
        return await message.answer("Процент налога должен быть целым числом.")

    MAX_TAX_PERCENT = 5
    if not 1 <= tax_percent <= MAX_TAX_PERCENT:
        return await message.answer(f"❌ Налог должен быть установлен в пределах от **1% до {MAX_TAX_PERCENT}%**.")
        
    total_collected = await asyncio.to_thread(
        apply_tax_sync,
        tax_percent,
        user_id 
    )

    if total_collected > 0:
        await message.answer(
            f"✅ **Президент @{user_data.username} ввел комплексный налог {tax_percent}%!**\n"
            f"Налог включил в себя: **наличные, имущество и доходы от бизнеса**.\n"
            f"Собрано: **{total_collected:,} Bongo$**.\n"
            f"Деньги перечислены в казну Президента.",
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await message.answer("ℹ️ Налог не был собран. Возможно, у игроков нет достаточных активов.")


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

    current_time = int(datetime.datetime.now().timestamp())
    time_elapsed = current_time - user_data.last_election_time
    if time_elapsed < ELECTION_COOLDOWN_SECONDS:
        hours = ELECTION_COOLDOWN_SECONDS // 3600
        return await message.answer(f"❌ Вы можете участвовать в выборах или голосовать только раз в **{hours} часов**.")

    session = Session()
    existing_candidate = session.execute(select(Candidate).where(Candidate.user_id == user_id)).scalars().first()
    session.close()
    
    if existing_candidate:
        return await message.answer("❌ Вы уже зарегистрированы как кандидат.")

    session = Session()
    try:
        candidate = Candidate(user_id=user_id)
        session.add(candidate)
        
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

    voter_data = await asyncio.to_thread(
        get_user_profile_sync,
        voter_id,
        message.from_user.username or message.from_user.first_name
    )
    current_time = int(datetime.datetime.now().timestamp())
    time_elapsed = current_time - voter_data.last_election_time
    if time_elapsed < ELECTION_COOLDOWN_SECONDS:
        return await message.answer("❌ Вы уже участвовали в выборах или голосовали. Вы сможете снова голосовать через 24 часа.")

    session = Session()
    candidate_record = session.execute(select(Candidate).where(Candidate.user_id == candidate_id)).scalars().first()
    
    if candidate_record is None:
        session.close()
        return await message.answer(f"❌ Кандидат с ID `{candidate_id}` не найден.")
    
    if candidate_id == voter_id:
        session.close()
        return await message.answer("❌ Вы не можете голосовать за себя.")

    try:
        candidate_record.votes += 1
        
        await asyncio.to_thread(
            update_user_sync,
            voter_id,
            last_election_time=current_time
        )
        
        session.commit()
        await message.answer(f"✅ Вы успешно отдали свой голос за кандидата с ID `{candidate_id}`.")
    finally:
        session.close()

@dp.message(Command("start_elections"))
async def cmd_start_elections(message: types.Message):
    """Админ начинает выборы."""
    if message.from_user.id != ADMIN_ID:
        return await message.answer("❌ У вас нет доступа.")
    
    if ELECTION_STATE != "NONE":
        return await message.answer(f"❌ Выборы уже идут. Текущий этап: **{ELECTION_STATE}**.")

    start_candidate_registration()
    await message.answer("✅ **Выборы запущены!** Объявлен **Набор Кандидатов**.")

@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    """Вход в админ-панель."""
    if message.from_user.id != ADMIN_ID:
        return await message.answer("❌ У вас нет доступа к админ-панели.")
    
    admin_text = (
        "👑 **АДМИН-ПАНЕЛЬ** 👑\n\n"
        "Доступные команды:\n"
        "/give [id] [сумма] - Выдать деньги игроку.\n"
        "/set_president [id] - Назначить игрока Президентом.\n"
        "/reset_db - Сбросить ВСЮ базу данных (используйте осторожно!).\n"
        "/start_elections - Начать выборы."
    )
    await message.answer(admin_text, parse_mode=ParseMode.MARKDOWN)


@dp.message(Command("give"))
async def cmd_give(message: types.Message, command: CommandObject):
    """Выдача денег игроку (Только для админа)."""
    if message.from_user.id != ADMIN_ID:
        return await message.answer("❌ У вас нет доступа.")
    
    if not command.args or len(command.args.split()) != 2:
        return await message.answer("Использование: /give [id] [сумма]")

    try:
        target_id = int(command.args.split()[0])
        amount = int(command.args.split()[1])
    except ValueError:
        return await message.answer("ID и сумма должны быть числами.")
        
    current_user_data = await asyncio.to_thread(
        get_user_profile_sync,
        target_id,
        "UnknownUser" 
    )
    
    if current_user_data is None:
        return await message.answer(f"❌ Пользователь с ID {target_id} не найден.")

    new_balance = current_user_data.balance + amount
        
    await asyncio.to_thread(
        update_user_sync,
        target_id,
        balance=new_balance
    )

    user_data = await asyncio.to_thread(
        get_user_profile_sync,
        target_id,
        "UnknownUser" 
    )

    
    if user_data:
        await message.answer(
            f"✅ Игроку с ID `{target_id}` выдано **{amount:,} Bongo$**.\n"
            f"Новый баланс: **{user_data.balance:,} Bongo$**",
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await message.answer(f"❌ Произошла ошибка при получении данных после выдачи.")


@dp.message(Command("set_president"))
async def cmd_set_president(message: types.Message, command: CommandObject):
    """Назначение игрока президентом (Только для админа)."""
    if message.from_user.id != ADMIN_ID:
        return await message.answer("❌ У вас нет доступа.")

    if not command.args:
        return await message.answer("Использование: /set_president [id]")

    try:
        target_id = int(command.args.split()[0])
    except ValueError:
        return await message.answer("ID должен быть числом.")

    await asyncio.to_thread(
        lambda: Session().execute(update(User).where(User.is_president==True).values(is_president=False, role="Игрок")).commit()
    )

    user_data = await asyncio.to_thread(
        update_user_sync,
        target_id,
        is_president=True,
        role="Президент"
    )

    if user_data:
        await message.answer(
            f"🇺🇸 **@{user_data.username}** назначен новым Президентом!"
        )
    else:
        await message.answer(f"❌ Пользователь с ID {target_id} не найден. Сначала попросите его написать /start.")


@dp.message(Command("reset_db"))
async def cmd_reset_db(message: types.Message):
    """Сбрасывает всю базу данных (ТОЛЬКО ДЛЯ АДМИНА!)."""
    if message.from_user.id != ADMIN_ID:
        return await message.answer("❌ У вас нет доступа.")

    DB_FILE = "data/bongobot.db"
    
    if os.path.exists(DB_FILE):
        try:
            os.remove(DB_FILE)
            global engine, Base, Session
            engine = create_engine(DB_PATH, connect_args={"check_same_thread": False})
            Base.metadata.create_all(engine) 
            Session = sessionmaker(bind=engine)
            
            await message.answer("⚠️ **База данных успешно сброшена!** Файл `bongobot.db` удален и создан заново. **Перезапустите бота.**", parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            await message.answer(f"❌ Ошибка при сбросе БД: {e}")
    else:
        await message.answer("ℹ️ Файл базы данных `bongobot.db` не найден. Сброс не требуется.")


# --- Запуск Бота и Планировщика ---

async def main():
    print("Бот запускается...")
    os.makedirs('data', exist_ok=True)
    
    scheduler.start() 
    
    # --- Планировщик пассивного дохода ---
    scheduler.add_job(
        business_payout_job, 
        'interval', 
        seconds=BUSINESS_PAYOUT_INTERVAL_SECONDS, 
        max_instances=1,
        id='payout_job'
    )
    # ------------------------------------
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
