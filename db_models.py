# db_models.py

from sqlalchemy import Column, Integer, String, BigInteger, Boolean, DateTime
from sqlalchemy.orm import declarative_base
import datetime
# 1. Базовый класс для всех моделей (нужен для Base.metadata.create_all)
Base = declarative_base()

# 2. Модель Пользователя (таблица 'users')
class User(Base):
    __tablename__ = 'users'
    
    # Обязательные поля
    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, index=True)
    username = Column(String)
    
    # Игровые и финансовые поля
    balance = Column(BigInteger, default=1000) # ИСПОЛЬЗУЙ BigInteger для денег
    xp = Column(Integer, default=0) # Опыт
    last_work_time = Column(DateTime, default=datetime.datetime.min) # Для кулдауна работы
    
    # Поля роли и недвижимости
    role = Column(String, default="Безработный") # Должность
    job_id = Column(Integer, default=0) # ID текущей работы
    property_count = Column(Integer, default=0) # Количество объектов недвижимости
    
    # Поля Администрации/Выборов
    is_admin = Column(Boolean, default=False)
    is_owner = Column(Boolean, default=False) # Владелец бота
    is_president = Column(Boolean, default=False) # Текущий президент
    
    # Поля для Бизнеса
    # ВАЖНО: OwnedBusiness обрабатывается отдельной таблицей
    
    # Уникальность
    def __repr__(self):
        return f"<User(telegram_id='{self.telegram_id}', balance='{self.balance}')>"

# 3. Модель Кандидата (для выборов)
class Candidate(Base):
    __tablename__ = 'candidates'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, unique=True, index=True)
    votes = Column(Integer, default=0)
    is_active = Column(Boolean, default=True) # Активен ли в текущих выборах
    
# 4. Модель Приобретенного Бизнеса (Таблица связи "Пользователь-Бизнес")
# У тебя в коде используется обход, но эта структура самая правильная.
class OwnedBusiness(Base):
    __tablename__ = 'owned_businesses'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, index=True) # Владелец бизнеса
    business_id = Column(Integer) # ID типа бизнеса (из словаря BUSINESSES)
    name = Column(String)
    count = Column(Integer, default=1) # Количество купленных одинаковых бизнесов
    
# 5. Модель для чатов (для массовых уведомлений)
class Chat(Base):
    __tablename__ = 'chats'
    
    id = Column(Integer, primary_key=True)
    chat_id = Column(BigInteger, unique=True)
    is_active = Column(Boolean, default=True)
