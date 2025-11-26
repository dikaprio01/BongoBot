import logging
import os
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.future import select
from sqlalchemy.exc import SQLAlchemyError

from .db_models import Base, User, Candidate, OwnedBusiness, Chat # Импортируем модели

# --- НАСТРОЙКА БД ---
# ВАЖНО: Эти переменные будут инициализированы в main.py
engine = None
Session = None

def init_db(db_path):
    """Инициализирует подключение к базе данных."""
    global engine, Session
    logging.info(f"Connecting to DB: {db_path}")
    
    try:
        engine = create_engine(db_path) 
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        return True
    except Exception as e:
        logging.error(f"FATAL: Database connection failed: {e}")
        return False


# --- Вспомогательные синхронные функции для работы с БД ---
# (Вся твоя логика get_user_profile_sync, update_user_sync, apply_tax_sync и т.д.)

def get_user_profile_sync(user_id: int, username: str, admin_id: int):
    """Получает или создает профиль пользователя."""
    if not Session: return None
    session = Session()
    try:
        user = session.get(User, user_id)
        
        if user is None:
            user = User(
                id=user_id,
                username=username,
                balance=500
            )
            if user_id == admin_id:
                user.is_owner = True
            
            session.add(user)
            session.commit()
            
        user = session.merge(user)
        return user
    finally:
        session.close()

def update_user_sync(user_id: int, **kwargs):
    """Обновляет любые поля пользователя по ID."""
    if not Session: return None
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
    if not Session: return []
    session = Session()
    try:
        users = session.execute(select(User).order_by(User.balance.desc())).scalars().all()
        users = [session.merge(u) for u in users]
        return users
    finally:
        session.close()

def save_chat_sync(chat_id: int):
    """Сохраняет ID чата для рассылки уведомлений."""
    if not Session: return False
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
    if not Session: return []
    session = Session()
    try:
        chats = session.execute(select(Chat.id)).scalars().all()
        return chats
    finally:
        session.close()

from .main import BUSINESSES, PROPERTIES, ADMIN_ID # Импортируем константы из main.py

def apply_tax_sync(tax_percent: float, president_id: int):
    """Применяет КОМПЛЕКСНЫЙ налог."""
    if not Session: return 0
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
            .filter( (User.balance > 1000) | (User.property_count > 0) | (User.id.in_(user_business_income.keys())) )
        ).scalars().all()
        
        # 3. Применяем налоги
        for user in users_to_tax:
            # Tax 1: Налог на Капитал
            taxable_balance = user.balance - 1000
            wealth_tax = int(taxable_balance * (tax_percent / 100)) if taxable_balance > 0 else 0
            
            # Tax 2: Налог на Имущество
            PROPERTY_TAX_VALUE = PROPERTIES.get(1)['price']
            property_value = user.property_count * PROPERTY_TAX_VALUE
            property_tax = int(property_value * (tax_percent / 100))
            
            # Tax 3: Налог на Бизнес
            total_hourly_income = user_business_income.get(user.id, 0)
            business_tax = int(total_hourly_income * (tax_percent / 100))
            
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
        
    except SQLAlchemyError as e:
        session.rollback()
        logging.error(f"Ошибка при сборе налога: {e}")
        return 0
    finally:
        session.close()
