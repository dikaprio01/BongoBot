import asyncio
import os
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

# Включаем логирование, чтобы видеть ошибки в консоли Scalingo
logging.basicConfig(level=logging.INFO)

# Получаем токен из настроек сервера (чтобы не палить его в коде)
TOKEN = os.getenv("BOT_TOKEN")

# Создаем бота и диспетчер
bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- Хэндлер: Команда /start ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("Йо! BongoBot на связи! 🦍\nЯ работаю на сервере Scalingo.")

# --- Функция запуска (начинается с края, не внутри других функций!) ---
async def main():
    print("Бот запускается...")
    # Удаляем вебхуки и запускаем опрос
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

# --- Блок запуска скрипта (начинается с края) ---
if __name__ == "__main__":
    asyncio.run(main())
