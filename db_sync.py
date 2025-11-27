# db_sync.py
import os
import datetime
# !!! ИСПРАВЛЕНИЕ #1: Добавляем 'text' из SQLAlchemy для явной проверки связи с БД
from sqlalchemy import create_engine, select, Column, Integer, String, BigInteger, Boolean, DateTime, text 
from sqlalchemy.orm import sessionmaker

# 1. Импорт моделей БЕЗ ТОЧЕК!
from db_models import Base, User, Candidate, OwnedBusiness, Chat 

# 2. НАСТРОЙКИ БАЗЫ ДАННЫХ (PostgreSQL)
DB_PATH = os.environ.get("DATABASE_URL") 

# Fix: SQLAlchemy и psycopg2 требуют схему postgresql://
if DB_PATH and DB_PATH.startswith("postgres://"):
    DB_PATH = DB_PATH.replace("postgres://", "postgresql://", 1)

if not DB_PATH:
    # Запасной вариант для локального запуска
    DB_PATH = "sqlite:///data/bongobot.db"
    
# 3. Создание Engine
engine = create_engine(DB_PATH, pool_pre_ping=True)

# 4. Создание сессии (ЭТО НУЖНО ДЛЯ ВАШЕГО КОДА)
Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
SessionLocal = Session # Добавим SessionLocal, на случай если он нужен где-то еще

# 5. Функция инициализации (создает таблицы)
def init_db():
    try:
        # !!! ИСПРАВЛЕНИЕ #2: Явная проверка подключения (ping)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            print("БД: Подключение успешно установлено.")
        
        # Создает все таблицы, определенные в Base
        Base.metadata.create_all(bind=engine)
        print("БД: Таблицы успешно созданы (или уже существовали).")
        return True
    except Exception as e:
        # Теперь эта ошибка выведет причину в логах Railway!
        print(f"FATAL: Ошибка инициализации БД. Таблицы НЕ созданы: {e}") 
        return False

# 6. Функция для получения пользователя (пример)
def get_user_profile_sync(telegram_id: int, username: str, admin_id: int):
    # Эта логика должна создавать пользователя, если его нет
    with Session() as session:
        user = session.query(User).filter(User.telegram_id == telegram_id).first()
        
        if not user:
            is_owner = telegram_id == admin_id # Проверка на владельца
            user = User(
                telegram_id=telegram_id, 
                username=username, 
                is_owner=is_owner, # Установим владельца
                balance=1000
                # last_work_time будет установлено в db_models.py по умолчанию
            )
            session.add(user)
            session.commit()
            
        return user
        
# 7. Функция для сохранения чата
def save_chat_sync(chat_id: int):
    with Session() as session:
        if not session.query(Chat).filter(Chat.chat_id == chat_id).first():
            session.add(Chat(chat_id=chat_id))
            session.commit()

# --- НОВЫЕ ФУНКЦИИ (ИСПРАВЛЕНИЕ ОШИБКИ ИМПОРТА) ---

# 8. Функция для обновления пользователя
# Обновляет любые поля, переданные через **kwargs
def update_user_sync(telegram_id: int, **kwargs):
    with Session() as session:
        # Используем .filter_by для обновления полей
        result = session.query(User).filter(User.telegram_id == telegram_id).update(kwargs)
        session.commit()
        return result > 0

# 9. Функция для получения всех пользователей
def get_all_users_sync():
    with Session() as session:
        # Возвращает всех пользователей, отсортированных по балансу (для /top)
        return session.query(User).order_by(User.balance.desc()).all()

# 10. Функция для получения всех чатов
def get_all_chats_sync():
    with Session() as session:
        # Возвращает все чаты (нужно для рассылок)
        return session.query(Chat).all()
        
# 11. Функция для начисления налога
# (Заглушка, так как реальная логика у тебя в main.py)
def apply_tax_sync(tax_rate: float):
    # Эта функция должна быть реализована, если она вызывается в main.py
    # Тут можно обновить всех пользователей: уменьшить баланс на tax_rate
    return # Заглушка, чтобы устранить ошибку импорта
    
# --- КОНЕЦ НОВЫХ ФУНКЦИЙ ---
