import aiosqlite
import os

DB_PATH = "bot_database.db"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS virtual_orders (
                posting_number TEXT PRIMARY KEY,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.commit()

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
    """Возвращает полные данные о виртуальных заказах (номер + дата)"""
    async with aiosqlite.connect(DB_PATH) as db:
        # Сортируем от новых к старым
        async with db.execute('SELECT posting_number, added_at FROM virtual_orders ORDER BY added_at DESC') as cursor:
            rows = await cursor.fetchall()
            return rows  # Возвращает список кортежей [('12345', '2023-10-25 12:00:00'), ...]

async def clear_virtual_orders():
    """Очистка таблицы (на случай, если нужно сбросить список)"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('DELETE FROM virtual_orders')
        await db.commit()