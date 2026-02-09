import aiosqlite
import json
from datetime import datetime

DB_PATH = "bot_database.db"


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        # Таблица для виртуальных (сложных) заказов - оставляем как было
        await db.execute('''
            CREATE TABLE IF NOT EXISTS virtual_orders (
                posting_number TEXT PRIMARY KEY,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # НОВАЯ таблица для истории и метаданных
        await db.execute('''
            CREATE TABLE IF NOT EXISTS active_orders_meta (
                posting_number TEXT PRIMARY KEY,
                products_json TEXT,
                sima_order_number TEXT,
                sima_order_date TEXT,
                plan_delivery_date TEXT,
                status TEXT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.commit()


# --- Старые функции для virtual_orders оставляем без изменений ---
async def add_virtual_order(posting_number):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('INSERT OR IGNORE INTO virtual_orders (posting_number) VALUES (?)', (posting_number,))
        await db.commit()


async def get_all_virtual_orders():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT posting_number FROM virtual_orders') as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]


async def remove_virtual_order(posting_number):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('DELETE FROM virtual_orders WHERE posting_number = ?', (posting_number,))
        await db.commit()


async def get_virtual_orders_full():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT posting_number, added_at FROM virtual_orders ORDER BY added_at DESC') as cursor:
            return await cursor.fetchall()


async def clear_virtual_orders():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('DELETE FROM virtual_orders')
        await db.commit()


# --- НОВЫЕ функции для active_orders_meta ---

async def save_order_meta(posting_number, products, sima_num, sima_date, deliv_date):
    """Сохраняет данные о заказе при сборке"""
    # Преобразуем список продуктов в JSON строку для хранения
    products_str = json.dumps(products, ensure_ascii=False)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            INSERT OR REPLACE INTO active_orders_meta 
            (posting_number, products_json, sima_order_number, sima_order_date, plan_delivery_date, status)
            VALUES (?, ?, ?, ?, ?, 'processing')
        ''', (posting_number, products_str, sima_num, sima_date, deliv_date))
        await db.commit()


async def get_order_details(posting_number):
    """Ищет заказ по номеру отправления Ozon"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('''
            SELECT products_json, sima_order_number, sima_order_date, plan_delivery_date 
            FROM active_orders_meta WHERE posting_number = ?
        ''', (posting_number,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return {
                    "products": json.loads(row[0]),
                    "sima_num": row[1],
                    "sima_date": row[2],
                    "deliv_date": row[3]
                }
            return None


async def delete_shipped_order(posting_number):
    """Удаляет заказ из истории (когда он уехал)"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('DELETE FROM active_orders_meta WHERE posting_number = ?', (posting_number,))
        await db.commit()


async def get_all_meta_postings():
    """Получает все номера заказов из мета-таблицы для проверки статусов"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT posting_number FROM active_orders_meta') as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]