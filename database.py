import os
import json
from datetime import datetime
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, Integer, BigInteger, String, DateTime, Text, Boolean, select, delete, update
from sqlalchemy.dialects.postgresql import insert

# --- КОНФИГУРАЦИЯ ПОДКЛЮЧЕНИЯ ---
# Берем данные из .env файла, либо используем значения по умолчанию
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "postgres")
DB_HOST = os.getenv("DB_HOST", "db")  # 'db' - это имя сервиса в docker-compose
DB_NAME = os.getenv("DB_NAME", "sima_control")

DATABASE_URL = f"postgresql+asyncpg://{DB_USER}:{DB_PASS}@{DB_HOST}/{DB_NAME}"

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
Base = declarative_base()


# --- МОДЕЛИ ТАБЛИЦ (ДЛЯ САЙТА И БОТА) ---

class User(Base):
    """Таблица пользователей для входа на сайт"""
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    password_hash = Column(String)
    role = Column(String)  # 'admin' или 'fulfillment'


class Order(Base):
    """Основная таблица заказов (бывшая active_orders_meta, но расширенная)"""
    __tablename__ = "orders"

    # Ключевые поля Ozon
    posting_number = Column(String, primary_key=True)  # Номер отправления
    ozon_status = Column(String, default="unknown")  # Статус (awaiting_packaging и т.д.)
    products_json = Column(Text)  # Состав заказа

    # Поля Сима-Ленд (заполняет бот или админ на сайте)
    sima_order_number = Column(String, nullable=True)
    sima_order_date = Column(String, nullable=True)  # Храним как строку, чтобы не ломать логику бота "25.10"
    plan_delivery_date = Column(String, nullable=True)  # Дата поставки (от бота)

    # Новые поля для Сайта/Фулфилмента
    sur_number = Column(String, nullable=True)  # СУР
    ff_delivery_date = Column(DateTime, nullable=True)  # Реальная дата поставки на ФФ
    comment = Column(Text, nullable=True)  # Комментарии

    added_at = Column(DateTime, default=datetime.now)
    ozon_created_at = Column(DateTime, nullable=True)  # Дата создания на стороне Ozon
    ozon_accepted_at = Column(DateTime, nullable=True)  # Дата принятия заказа (in_process_at)


class VirtualOrder(Base):
    """
    Таблица только для ID сложных заказов.
    Оставляем её отдельной, чтобы не ломать логику бота "get_all_virtual_orders".
    """
    __tablename__ = "virtual_orders"
    posting_number = Column(String, primary_key=True)
    added_at = Column(DateTime, default=datetime.now)


class Product(Base):
    """
    Таблица товаров для репрайсера.
    Хранит себестоимость, правила ценообразования и кэш данных с Ozon.
    """
    __tablename__ = "products"

    offer_id = Column(String, primary_key=True)   # Артикул продавца
    product_id = Column(BigInteger, nullable=True)   # ID на Ozon (может быть > int32)
    name = Column(String, nullable=True)           # Название
    image_url = Column(String, nullable=True)      # Фото

    # Текущие цены (кэш с Ozon, обновляется при синхронизации)
    price = Column(Integer, nullable=True)         # Цена продажи
    old_price = Column(Integer, nullable=True)     # Зачёркнутая цена
    min_price = Column(Integer, nullable=True)     # Минимальная цена
    net_price = Column(Integer, nullable=True)     # Чистая выручка (после комиссий)
    marketing_price = Column(Integer, nullable=True)  # Цена по акции

    # Комиссии FBS (кэш с Ozon)
    commission_fbs_percent = Column(Integer, nullable=True)   # % комиссии FBS
    commission_fbs_logistics = Column(Integer, nullable=True) # Логистика FBS (руб)

    # Индексы цен (кэш с Ozon)
    price_index_color = Column(String, nullable=True)         # RED/YELLOW/GREEN
    price_index_ozon = Column(String, nullable=True)          # Индекс внутри Ozon
    price_index_external = Column(String, nullable=True)      # Индекс vs внешние МП
    competitor_min_price = Column(Integer, nullable=True)     # Мин. цена конкурента

    # Себестоимость и правила (задаёт пользователь)
    cost_price = Column(Integer, nullable=True)               # Закупочная цена (руб)
    target_margin_pct = Column(Integer, nullable=True)        # Целевая маржа %
    auto_reprice_enabled = Column(Boolean, default=False)     # Авто-репрайсинг вкл/выкл
    auto_rule = Column(String, nullable=True)                 # Правило: "margin" / "competitor"
    auto_rule_value = Column(Integer, nullable=True)          # Значение правила

    # Категория, бренд, склад (из Ozon)
    brand = Column(String, nullable=True)                     # Бренд (attribute_id=85)
    category_id = Column(BigInteger, nullable=True)           # ID категории Ozon
    category_name = Column(String, nullable=True)             # Название категории
    warehouse_type = Column(String, nullable=True)            # fbs/fbo/sds

    updated_at = Column(DateTime, nullable=True)              # Последнее обновление с Ozon


class CostHistory(Base):
    """История изменений себестоимости."""
    __tablename__ = "cost_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    offer_id = Column(String, index=True)
    old_cost = Column(Integer, nullable=True)
    new_cost = Column(Integer)
    source = Column(String, nullable=True)    # "excel_upload" / "sima_cart" / "manual"
    changed_by = Column(String, nullable=True)
    changed_at = Column(DateTime, default=datetime.now)


class PriceHistory(Base):
    """История изменений цены по товару"""
    __tablename__ = "price_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    offer_id = Column(String, index=True)
    old_price = Column(Integer)
    new_price = Column(Integer)
    reason = Column(String, nullable=True)   # "manual" / "auto_margin" / "auto_competitor"
    changed_by = Column(String, nullable=True)  # username
    changed_at = Column(DateTime, default=datetime.now)


# --- ФУНКЦИИ ИНИЦИАЛИЗАЦИИ ---

async def init_db():
    """Создает таблицы в PostgreSQL при запуске"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# --- ФУНКЦИИ БОТА (АДАПТИРОВАННЫЕ ПОД POSTGRES) ---

async def add_virtual_order(posting_number):
    async with AsyncSessionLocal() as session:
        # Используем insert с игнорированием дубликатов
        stmt = insert(VirtualOrder).values(posting_number=posting_number)
        stmt = stmt.on_conflict_do_nothing(index_elements=['posting_number'])
        await session.execute(stmt)
        await session.commit()


async def get_all_virtual_orders():
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(VirtualOrder.posting_number))
        return result.scalars().all()


async def remove_virtual_order(posting_number):
    async with AsyncSessionLocal() as session:
        await session.execute(delete(VirtualOrder).where(VirtualOrder.posting_number == posting_number))
        await session.commit()


async def get_virtual_orders_full():
    """
    Возвращает список кортежей, как это делал старый SQL запрос.
    Join таблицы VirtualOrder с таблицей Order.
    """
    async with AsyncSessionLocal() as session:
        # Эмулируем старый SQL запрос через ORM
        query = select(
            VirtualOrder.posting_number,
            VirtualOrder.added_at,
            Order.sima_order_number,
            Order.plan_delivery_date
        ).outerjoin(Order, VirtualOrder.posting_number == Order.posting_number).order_by(VirtualOrder.added_at.desc())

        result = await session.execute(query)
        rows = result.all()

        # Преобразуем datetime в строки, если нужно, чтобы формат совпадал со старым sqlite
        # Но обычно бот просто выводит данные, так что datetime тоже сработает при str()
        formatted_rows = []
        for row in rows:
            # Превращаем row в кортеж (posting, date_str, sima_num, deliv_date)
            p_num = row[0]
            # row[1] это datetime, старый код ждал строку, преобразуем для совместимости
            added_at = row[1].strftime("%Y-%m-%d %H:%M:%S") if row[1] else "---"
            sima_num = row[2]
            deliv_date = row[3]
            formatted_rows.append((p_num, added_at, sima_num, deliv_date))

        return formatted_rows


async def clear_virtual_orders():
    async with AsyncSessionLocal() as session:
        await session.execute(delete(VirtualOrder))
        await session.commit()


# --- ФУНКЦИИ ACTIVE ORDERS (META) ---

async def save_order_meta(posting_number, products, sima_num, sima_date, deliv_date, accepted_at=None):
    """
    Сохраняет или обновляет данные о заказе.
    products — список dict с offer_id, sku, name, quantity, image_url.
    accepted_at — datetime in_process_at из Ozon API.
    """
    products_str = json.dumps(products, ensure_ascii=False)

    async with AsyncSessionLocal() as session:
        stmt = insert(Order).values(
            posting_number=posting_number,
            products_json=products_str,
            sima_order_number=sima_num,
            sima_order_date=sima_date,
            plan_delivery_date=deliv_date,
            ozon_status='processing',
            ozon_accepted_at=accepted_at,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=['posting_number'],
            set_={
                'products_json': products_str,
                'sima_order_number': sima_num,
                'sima_order_date': sima_date,
                'plan_delivery_date': deliv_date,
                'ozon_accepted_at': accepted_at,
            }
        )
        await session.execute(stmt)
        await session.commit()


async def get_order_details(posting_number):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Order).where(Order.posting_number == posting_number))
        order = result.scalars().first()

        if order:
            return {
                "products": json.loads(order.products_json) if order.products_json else [],
                "sima_num": order.sima_order_number,
                "sima_date": order.sima_order_date,
                "deliv_date": order.plan_delivery_date
            }
        return None


async def delete_shipped_order(posting_number):
    """Удаляет заказ из БД"""
    async with AsyncSessionLocal() as session:
        await session.execute(delete(Order).where(Order.posting_number == posting_number))
        await session.commit()


async def get_all_meta_postings():
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Order.posting_number))
        return result.scalars().all()


async def get_all_virtual_articles():
    """Сложная логика подсчета товаров в виртуальных заказах"""
    async with AsyncSessionLocal() as session:
        # 1. Получаем ID всех виртуальных
        v_res = await session.execute(select(VirtualOrder.posting_number))
        virtual_postings = v_res.scalars().all()

        if not virtual_postings:
            return {}

        # 2. Получаем JSON продуктов для этих ID из основной таблицы
        q = select(Order.products_json).where(Order.posting_number.in_(virtual_postings))
        res = await session.execute(q)
        rows = res.scalars().all()

        total_virtual_items = {}
        for p_json in rows:
            if not p_json: continue
            products = json.loads(p_json)
            for p in products:
                art = str(p.get('offer_id') or p.get('sku'))
                qty = int(p.get('quantity', 0))
                total_virtual_items[art] = total_virtual_items.get(art, 0) + qty

        return total_virtual_items


async def clear_all_virtual_orders():
    await clear_virtual_orders()