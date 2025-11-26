import asyncio
import os
import logging
import random
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandObject
from aiogram.enums import ParseMode
from sqlalchemy import create_engine, Column, Integer, String, BigInteger, Boolean, update
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.future import select

# --- Константы и Настройки ---
# Включаем логирование
logging.basicConfig(level=logging.INFO)

# ID владельца (АДМИНА) бота для админ-панели (ЗАМЕНИ НА СВОЙ ID ТЕЛЕГРАМ)
ADMIN_ID = 1871352653 # <--- ОБЯЗАТЕЛЬНО ИЗМЕНИТЬ!

# Настройки SQLite (сохранение в папке data на Bothost.ru)
DB_PATH = "sqlite:///data/bongobot.db"
JOB_COOLDOWN_SECONDS = 3600 # 1 час
ELECTION_COOLDOWN_SECONDS = 86400 # 24 часа

# Базовый класс для всех моделей
Base = declarative_base()

# --- Модель Базы Данных ---
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
    last_work_time = Column(BigInteger, default=0) # Время последнего "работы"
    last_election_time = Column(BigInteger, default=0) # Время участия в выборах

# --- Настройка SQLAlchemy ---
engine = create_engine(DB_PATH, connect_args={"check_same_thread": False})
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)

# --- Настройка Бота ---
TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=TOKEN)
dp = Dispatcher()

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
            # Если ID совпадает с ADMIN_ID, делаем владельцем
            if user_id == ADMIN_ID:
                user.is_owner = True
            
            session.add(user)
            session.commit()
            
        # Загружаем данные перед закрытием сессии (критический фикс DetachedInstanceError)
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
            # Загружаем данные перед закрытием сессии
            user = session.merge(user)
        return user
    finally:
        session.close()

def get_all_users_sync():
    """Получает всех пользователей для топа."""
    session = Session()
    try:
        users = session.execute(select(User).order_by(User.balance.desc())).scalars().all()
        # Загружаем данные перед закрытием сессии
        users = [session.merge(u) for u in users]
        return users
    finally:
        session.close()


# --- Хэндлеры для Игрового Функционала ---

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
    current_time = int(types.datetime.datetime.now().timestamp())
    
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
    user_data = await asyncio.to_thread(
        update_user_sync,
        user_id,
        balance=user_data.balance + money_earned,
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
    user_data = await asyncio.to_thread(
        update_user_sync,
        user_id,
        balance=user_data.balance - HOUSE_PRICE,
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


# --- Система Выборов и Президентства ---

@dp.message(Command("election"))
async def cmd_election(message: types.Message):
    """Начинает или завершает выборы, или показывает текущего президента."""
    
    # Получаем текущего президента
    current_president = await asyncio.to_thread(
        lambda: Session().execute(select(User).filter_by(is_president=True)).scalars().first()
    )
    
    if not current_president:
        # Если президента нет, начинаем выборы (для простоты - объявляем первого кандидата)
        user_id = message.from_user.id
        current_time = int(types.datetime.datetime.now().timestamp())
        
        user_data = await asyncio.to_thread(
            get_user_profile_sync,
            user_id,
            message.from_user.username or message.from_user.first_name
        )

        # Проверка кулдауна на выборы (чтобы не спамили)
        time_elapsed = current_time - user_data.last_election_time
        if time_elapsed < ELECTION_COOLDOWN_SECONDS:
            remaining_time = ELECTION_COOLDOWN_SECONDS - time_elapsed
            hours = remaining_time // 3600
            return await message.answer(
                f"❌ Вы можете баллотироваться или голосовать только раз в 24 часа. Осталось **{hours} ч**."
            )
            
        # Устанавливаем текущего пользователя президентом
        await asyncio.to_thread(
            update_user_sync,
            user_id,
            is_president=True,
            last_election_time=current_time,
            role="Президент"
        )
        
        await message.answer(
            f"🇺🇸 **ПОЗДРАВЛЯЕМ!** @{user_data.username} стал первым Президентом!\n"
            f"Используйте /president_info для информации."
        )

    else:
        # Президент уже есть, просто показываем информацию
        await message.answer(
            f"🇺🇸 Текущий Президент: **@{current_president.username}**.\n"
            f"Его баланс: **{current_president.balance:,} Bongo$**."
        )


# --- Админ-Панель ---

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
        "/reset_db - Сбросить ВСЮ базу данных (используйте осторожно!)."
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
        
    user_data = await asyncio.to_thread(
        update_user_sync,
        target_id,
        balance=lambda b: b + amount # SQLAlchemy примет функцию для обновления
    )
    
    if user_data:
        await message.answer(
            f"✅ Игроку с ID `{target_id}` выдано **{amount:,} Bongo$**.\n"
            f"Новый баланс: **{user_data.balance:,} Bongo$**",
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await message.answer(f"❌ Пользователь с ID {target_id} не найден.")


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

    # Сбрасываем текущего президента (если есть)
    await asyncio.to_thread(
        lambda: Session().execute(update(User).where(User.is_president==True).values(is_president=False))
    )

    # Назначаем нового президента
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
        await message.answer(f"❌ Пользователь с ID {target_id} не найден.")


# --- Базовая команда /start ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("🎉 Добро пожаловать в BongoBot! 🎉\n\n"
                         "Напиши /profile, чтобы увидеть свой счет.\n"
                         "Используй /work, чтобы заработать денег.")


# --- Запуск Бота ---

async def main():
    print("Бот запускается...")
    # Создание папки data, если ее нет (критично для SQLite на Bothost.ru)
    os.makedirs('data', exist_ok=True)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
