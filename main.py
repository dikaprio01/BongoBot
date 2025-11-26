# check_same_thread=False нужен для асинхронной работы с SQLite
engine = create_engine(DB_PATH, connect_args={"check_same_thread": False})
Base.metadata.create_all(engine) # Создаем таблицу, если ее нет

# Создаем сессию для общения с БД
Session = sessionmaker(bind=engine)
# ---------------------------------------------

# Получаем токен из настроек сервера
TOKEN = os.getenv("BOT_TOKEN")

# Создаем бота и диспетчер
bot = Bot(token=TOKEN)
dp = Dispatcher()


# --- Вспомогательная функция для получения/создания профиля (Синхронная) ---
def get_user_profile_sync(user_id: int, username: str):
    session = Session()
    try:
        user = session.get(User, user_id)
        
        if user is None:
            user = User(
                id=user_id,
                username=username,
                role="Игрок",
                is_owner=False,
                balance=500,
                property_count=0,
                xp=0,
                is_president=False
            )
            session.add(user)
            session.commit()
            
        return user
    finally:
        session.close()

# --- Хэндлер: Команда /profile ---
@dp.message(Command("profile"))
async def cmd_profile(message: types.Message):
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
        f"🏡 Имущество: {user_data.property_count} объектов"
    )
    
    await message.answer(profile_text, parse_mode='Markdown')


# --- Оставшаяся часть кода остается прежней ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("БонгоБот на связи! Напиши /profile, чтобы увидеть свой счет.")


async def main():
    print("Бот запускается...")
    # Создание папки data, если ее нет (на всякий случай)
    os.makedirs('data', exist_ok=True)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
