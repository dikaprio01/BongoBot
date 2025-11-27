# db_sync.py
import os
import datetime
from sqlalchemy import create_engine, select, Column, Integer, String, BigInteger, Boolean, DateTime
from sqlalchemy.orm import sessionmaker

# 1. Импорт моделей БЕЗ ТОЧЕК!
from db_models import Base, User, Candidate, OwnedBusiness, Chat 

# 2. НАСТРОЙКИ БАЗЫ ДАННЫХ (PostgreSQL) - используем переменную, как в main.py
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

# 5. Функция инициализации (создает таблицы)
def init_db():
    try:
        # Создает все таблицы, определенные в Base
        Base.metadata.create_all(bind=engine)
        return True
    except Exception as e:
        print(f"Ошибка инициализации БД: {e}")
        return False

# 6. Функция для получения пользователя (пример)
def get_user_profile_sync(telegram_id: int, username: str, admin_id: int):
    # Эта логика должна создавать пользователя, если его нет
    with Session() as session:
        user = session.query(User).filter(User.telegram_id == telegram_id).first()
        
        if not user:
            is_admin = telegram_id == admin_id
            user = User(
                telegram_id=telegram_id, 
                username=username, 
                is_owner=is_admin, # Установим владельца
                balance=1000
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

# ... ТВОИ ДРУГИЕ ФУНКЦИИ (update_user_sync, get_all_users_sync, apply_tax_sync) ИДУТ ЗДЕСЬ
