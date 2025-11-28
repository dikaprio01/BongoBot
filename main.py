import os
import logging
import random
import asyncio
from datetime import datetime, timedelta

from sqlalchemy import create_engine, Column, Integer, String, BigInteger, Boolean, DateTime, Float, ForeignKey, text
from sqlalchemy.orm import sessionmaker, declarative_base, relationship, selectinload
from sqlalchemy.exc import SQLAlchemyError

from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
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

bot = Bot(token=BOT_TOKEN)
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

# =========================================================
# === 5. ЭКОНОМИКА И ПЛАНИРОВЩИК ===
# =========================================================

async def business_payout():
    """Начисление дохода раз в час."""
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
                if pres and pres.telegram_id != ob.user_id:
                    pres.balance += tax_cut
                
                payouts[ob.user_id] = payouts.get(ob.user_id, 0) + net_income
        
        # Зачисление
        for uid, amount in payouts.items():
            u = s.query(User).filter_by(telegram_id=uid).first()
            if u and not u.is_banned:
                u.balance += amount
                # Пытаемся уведомить
                try:
                    await bot.send_message(uid, f"💼 **Бизнес-доход:** +{amount:,} $\n(Налог {int(tax*100)}% уплачен)")
                except: pass
        s.commit()

# =========================================================
# === 6. ХЕНДЛЕРЫ: ОСНОВНОЕ ===
# =========================================================

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    # Сохраняем чат
    with Session() as s:
        if not s.query(Chat).filter_by(chat_id=message.chat.id).first():
            s.add(Chat(chat_id=message.chat.id))
            s.commit()

    u = await asyncio.to_thread(get_user, message.from_user.id, message.from_user.username)
    
    if u.is_banned: return
    
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text=BTN_PROFILE), KeyboardButton(text=BTN_WORK)],
        [KeyboardButton(text=BTN_BUSINESS), KeyboardButton(text=BTN_CASINO)],
        [KeyboardButton(text=BTN_POLITICS), KeyboardButton(text=BTN_TOP)]
    ], resize_keyboard=True)
    
    await message.answer(
        f"👋 **Привет, {u.username}!**\n\n"
        f"Добро пожаловать в BongoBot — лучший симулятор жизни.\n"
        f"Поднимай кэш, строй бизнес, стань Президентом! 🌍\n\n"
        f"💰 Твой баланс: **{u.balance:,} $**",
        reply_markup=kb,
        parse_mode="Markdown"
    )

@dp.message(F.text == BTN_PROFILE)
async def cmd_profile(message: types.Message):
    u = await asyncio.to_thread(get_user, message.from_user.id, message.from_user.username)
    
    # Проверка ареста
    status_emoji = "👤"
    status_text = "Гражданин"
    
    if u.is_owner: status_text, status_emoji = "Владелец", "👑"
    elif u.is_president: status_text, status_emoji = "Президент", "🦅"
    elif u.is_admin: status_text, status_emoji = "Администратор", "🛡"
    
    arrest_text = ""
    if u.arrest_expires and u.arrest_expires > datetime.now():
        left = u.arrest_expires - datetime.now()
        arrest_text = f"\n🔒 **ТЫ В ТЮРЬМЕ** ещё {int(left.total_seconds()//60)} мин."

    # Считаем бизнес
    with Session() as s:
        biz_count = s.query(OwnedBusiness).filter_by(user_id=u.telegram_id).count()
    
    msg = (
        f"📑 **Твой Профиль**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{status_emoji} **Статус:** {status_text}\n"
        f"🆔 **ID:** `{u.telegram_id}`\n"
        f"👤 **Имя:** {u.username}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💰 **Баланс:** {u.balance:,} $\n"
        f"💼 **Бизнесы:** {biz_count} шт.\n"
        f"━━━━━━━━━━━━━━━━━━{arrest_text}"
    )
    await message.answer(msg, parse_mode="Markdown")

@dp.message(F.text == BTN_WORK)
async def cmd_work(message: types.Message):
    u = await asyncio.to_thread(get_user, message.from_user.id)
    if u.is_banned: return
    if u.arrest_expires and u.arrest_expires > datetime.now():
        return await message.answer("🔒 Ты в тюрьме! Работать нельзя.")

    if datetime.now() - u.last_work_time < WORK_COOLDOWN:
        rem = WORK_COOLDOWN - (datetime.now() - u.last_work_time)
        return await message.answer(f"⏳ Ты устал. Отдохни еще {int(rem.total_seconds()//3600)}ч {int((rem.total_seconds()%3600)//60)}мин.")

    base_earned = random.randint(200, 800)
    
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
        f"💵 Заработано: **{base_earned} $**\n"
        f"💸 Налог ({int(tax_rate*100)}%): -{tax} $\n"
        f"💰 **Итого:** +{net_earned} $"
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
        return await message.answer("❌ Недостаточно средств!")

    # Игра
    win = random.choice([True, False])
    
    with Session() as s:
        user = s.query(User).filter_by(telegram_id=u.telegram_id).first()
        if win:
            user.balance += bet
            res_text = f"🎉 **ПОБЕДА!** Выпало счастливое число!\n➕ {bet} $"
        else:
            user.balance -= bet
            res_text = f"💀 **ПРОИГРЫШ.** Удача отвернулась.\n➖ {bet} $"
        s.commit()
        
    await state.clear()
    await message.answer(res_text, parse_mode="Markdown")

# =========================================================
# === 8. БИЗНЕСЫ ===
# =========================================================

@dp.message(F.text == BTN_BUSINESS)
async def cmd_business(message: types.Message):
    text = "🏢 **Каталог Бизнесов:**\n\n"
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    
    for bid, b in BUSINESSES.items():
        text += f"🔹 **{b['name']}**\n   💰 Цена: {b['cost']:,} $\n   💸 Доход: {b['income']:,} $/час\n\n"
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"Купить: {b['name']}", callback_data=f"buybiz_{bid}")])
    
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
        return await call.answer("❌ Не хватает денег!", show_alert=True)
    
    with Session() as s:
        user = s.query(User).filter_by(telegram_id=uid).first()
        user.balance -= info['cost']
        
        exist = s.query(OwnedBusiness).filter_by(user_id=uid, business_id=bid).first()
        if exist: exist.count += 1
        else: s.add(OwnedBusiness(user_id=uid, business_id=bid, name=info['name'], count=1))
        s.commit()
        
    await call.message.edit_text(f"✅ Поздравляю! Вы купили **{info['name']}**!")

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
        medal = "🥇" if i==1 else "🥈" if i==2 else "🥉" if i==3 else "👤"
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
            f"🦅 **Президент:** {pres_name}\n"
            f"📉 **Налог:** {int(state.tax_rate*100)}%\n"
            f"📊 **Статус выборов:** {state.phase}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
        )
        
        kb = InlineKeyboardMarkup(inline_keyboard=[])
        
        if state.phase == "CANDIDACY":
            text += "🟢 Идет набор кандидатов! Подай заявку!"
            kb.inline_keyboard.append([InlineKeyboardButton(text="📝 Подать заявку", callback_data="el_apply")])
        elif state.phase == "VOTING":
            text += "🗳 Идет голосование! Выбери президента!"
            kb.inline_keyboard.append([InlineKeyboardButton(text="🗳 Голосовать", callback_data="el_vote_menu")])
        else:
            text += "Выборы пока не проводятся."
            
    await message.answer(text, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "el_apply")
async def election_apply(call: types.CallbackQuery):
    uid = call.from_user.id
    with Session() as s:
        if s.query(Candidate).filter_by(user_id=uid).first():
            return await call.answer("Вы уже кандидат!", show_alert=True)
        
        s.add(Candidate(user_id=uid))
        s.commit()
    await call.answer("Заявка подана!", show_alert=True)

@dp.callback_query(F.data == "el_vote_menu")
async def election_vote_menu(call: types.CallbackQuery):
    with Session() as s:
        cands = s.query(Candidate).all()
        kb = InlineKeyboardMarkup(inline_keyboard=[])
        for c in cands:
            u = s.query(User).filter_by(telegram_id=c.user_id).first()
            kb.inline_keyboard.append([InlineKeyboardButton(text=f"За {u.username}", callback_data=f"el_vote_{c.user_id}")])
    await call.message.edit_text("Выберите кандидата:", reply_markup=kb)

@dp.callback_query(F.data.startswith("el_vote_"))
async def election_do_vote(call: types.CallbackQuery):
    cand_id = int(call.data.split("_")[2])
    voter_id = call.from_user.id
    
    with Session() as s:
        # Проверка повторного голоса не реализована для простоты, но можно добавить в User флаг has_voted
        cand = s.query(Candidate).filter_by(user_id=cand_id).first()
        if cand:
            cand.votes += 1
            s.commit()
            await call.answer("Голос принят!", show_alert=True)
        else:
            await call.answer("Кандидат выбыл.", show_alert=True)

# =========================================================
# === 11. АДМИН ПАНЕЛЬ И УПРАВЛЕНИЕ ===
# =========================================================

@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 Выдать деньги", callback_data="adm_give")],
        [InlineKeyboardButton(text="🔒 Арестовать", callback_data="adm_arrest")],
        [InlineKeyboardButton(text="🔓 Освободить", callback_data="adm_release")],
        [InlineKeyboardButton(text="🗳 Начать выборы", callback_data="adm_start_el")],
        [InlineKeyboardButton(text="➡️ Начать голосование", callback_data="adm_start_vote")],
        [InlineKeyboardButton(text="🏁 Завершить выборы", callback_data="adm_end_el")],
        [InlineKeyboardButton(text="📉 Изменить налог", callback_data="adm_tax")]
    ])
    await message.answer("🛠 **Админ Панель**", reply_markup=kb)

# --- Логика Админки (FSM и Callbacks) ---
# (Здесь упрощенно для экономии места, реализуем основные действия)

@dp.callback_query(F.data == "adm_arrest")
async def adm_arrest_start(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer("Введите ID игрока для ареста:")
    await state.set_state(AdminState.arrest_id)

@dp.message(AdminState.arrest_id)
async def adm_arrest_id(message: types.Message, state: FSMContext):
    await state.update_data(id=int(message.text))
    await message.answer("На сколько минут?")
    await state.set_state(AdminState.arrest_time)

@dp.message(AdminState.arrest_time)
async def adm_arrest_finish(message: types.Message, state: FSMContext):
    data = await state.get_data()
    mins = int(message.text)
    uid = data['id']
    
    with Session() as s:
        u = s.query(User).filter_by(telegram_id=uid).first()
        if u:
            u.arrest_expires = datetime.now() + timedelta(minutes=mins)
            s.commit()
            await message.answer(f"✅ Игрок {uid} арестован на {mins} мин.")
            try: await bot.send_message(uid, f"👮 **ВАС АРЕСТОВАЛИ!** Срок: {mins} мин.")
            except: pass
        else:
            await message.answer("Игрок не найден.")
    await state.clear()

@dp.callback_query(F.data == "adm_give")
async def adm_give_start(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer("ID игрока и сумма через пробел (напр: 12345 1000):")
    await state.set_state(AdminState.give_id)

@dp.message(AdminState.give_id)
async def adm_give_exec(message: types.Message, state: FSMContext):
    try:
        uid, amount = map(int, message.text.split())
        with Session() as s:
            u = s.query(User).filter_by(telegram_id=uid).first()
            if u:
                u.balance += amount
                s.commit()
                await message.answer("✅ Выдано.")
    except:
        await message.answer("Ошибка формата.")
    await state.clear()

# Управление выборами
@dp.callback_query(F.data == "adm_start_el")
async def adm_start_el(call: types.CallbackQuery):
    with Session() as s:
        st = s.query(ElectionState).first()
        st.phase = "CANDIDACY"
        s.query(Candidate).delete() # Сброс
        s.commit()
    await call.answer("Набор кандидатов открыт!")
    # Тут можно сделать рассылку по чатам

@dp.callback_query(F.data == "adm_start_vote")
async def adm_start_vote(call: types.CallbackQuery):
    with Session() as s:
        st = s.query(ElectionState).first()
        st.phase = "VOTING"
        s.commit()
    await call.answer("Голосование началось!")

@dp.callback_query(F.data == "adm_end_el")
async def adm_end_el(call: types.CallbackQuery):
    winner_name = "Никто"
    with Session() as s:
        # Считаем голоса
        winner = s.query(Candidate).order_by(Candidate.votes.desc()).first()
        if winner:
            # Снимаем старого
            s.query(User).update({User.is_president: False})
            # Назначаем нового
            u = s.query(User).filter_by(telegram_id=winner.user_id).first()
            u.is_president = True
            winner_name = u.username
        
        st = s.query(ElectionState).first()
        st.phase = "IDLE"
        s.commit()
    
    await call.message.answer(f"🎉 **ВЫБОРЫ ЗАВЕРШЕНЫ!**\nНовый президент: {winner_name}")

# =========================================================
# === ЗАПУСК ===
# =========================================================

async def on_startup():
    if init_db():
        scheduler.add_job(business_payout, 'interval', seconds=BUSINESS_PAYOUT_INTERVAL)
        scheduler.start()
        print("🚀 Бот и планировщик запущены!")

async def main():
    dp.startup.register(on_startup)
    await dp.start_polling(bot, skip_updates=True)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
