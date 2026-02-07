import os
import aiohttp
import asyncio
from datetime import datetime, timedelta
from dotenv import load_dotenv
from database import get_all_virtual_orders, remove_virtual_order, add_virtual_order

load_dotenv()

OZON_CLIENT_ID = os.getenv("OZON_CLIENT_ID")
OZON_API_KEY = os.getenv("OZON_API_KEY")
OZON_WAREHOUSE_ID = int(os.getenv("OZON_WAREHOUSE_ID"))

HEADERS = {
    "Client-Id": OZON_CLIENT_ID,
    "Api-Key": OZON_API_KEY,
    "Content-Type": "application/json"
}


def parse_orders(postings):
    """Преобразует сырой ответ Ozon в наш формат"""
    orders = []
    for posting in postings:
        order_number = posting.get("posting_number")
        shipment_date = posting.get("shipment_date")

        products_list = []
        for product in posting.get("products", []):
            products_list.append({
                "offer_id": product.get("offer_id"),
                "sku": product.get("sku"),
                "name": product.get("name"),
                "quantity": product.get("quantity"),
                "price": product.get("price", "0")
            })

        orders.append({
            "number": order_number,
            "ship_date": shipment_date,
            "products": products_list
        })
    return orders


async def fetch_postings(session, status="awaiting_packaging"):
    """Вспомогательная функция для запроса к API"""
    url = "https://api-seller.ozon.ru/v3/posting/fbs/list"
    date_to = datetime.now()
    date_from = date_to - timedelta(days=14)

    payload = {
        "dir": "ASC",
        "filter": {
            "status": status,
            "warehouse_id": [OZON_WAREHOUSE_ID],
            "since": date_from.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "to": date_to.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        },
        "limit": 1000,
        "with": {"analytics_data": False, "financial_data": False}
    }

    async with session.post(url, json=payload, headers=HEADERS) as response:
        if response.status != 200:
            return []
        data = await response.json()
        return data.get('result', {}).get('postings', [])


async def get_new_orders():
    """Получает заказы и фильтрует их через БД (для отчетов)"""
    async with aiohttp.ClientSession() as session:
        all_postings = await fetch_postings(session)

    virtual_orders = await get_all_virtual_orders()
    filtered_raw = []

    for p in all_postings:
        p_num = p['posting_number']

        # Очистка БД от старых заказов (которые уже не awaiting_packaging)
        # Примечание: fetch_postings запрашивает только awaiting_packaging,
        # поэтому здесь логика "если статус != ..." не сработает напрямую,
        # так как API не вернет другие статусы.
        # Но для надежности оставим проверку, если вы поменяете фильтр.
        if p['status'] != 'awaiting_packaging':
            if p_num in virtual_orders:
                await remove_virtual_order(p_num)
            continue

        if p_num in virtual_orders:
            continue

        filtered_raw.append(p)

    # ВАЖНО: Возвращаем распаршенные данные
    return parse_orders(filtered_raw)


async def assemble_orders():
    """Собирает заказы. Многопозиционные -> в БД, Одиночные -> API"""

    # 1. Получаем СЫРЫЕ данные (без фильтрации БД), но распаршенные для удобства
    async with aiohttp.ClientSession() as session:
        raw_postings = await fetch_postings(session)

    # Парсим, чтобы удобно работать с products
    all_new = parse_orders(raw_postings)
    virtual_orders_db = await get_all_virtual_orders()

    success_count = 0
    virtual_count = 0
    errors = []

    async with aiohttp.ClientSession() as session:
        for order in all_new:
            p_num = order['number']  # Используем наш ключ после парсинга

            # Если уже обработан виртуально - пропускаем
            if p_num in virtual_orders_db:
                continue

            total_items = sum(int(p['quantity']) for p in order['products'])

            if total_items > 1:
                await add_virtual_order(p_num)
                virtual_count += 1
            else:
                # ОДИНОЧНЫЙ: Собираем
                url = "https://api-seller.ozon.ru/v4/posting/fbs/ship"

                # Формируем payload. ВАЖНО: product_id должен быть SKU или ItemID
                # В parse_orders мы сохранили 'sku'.
                products_payload = []
                for p in order['products']:
                    products_payload.append({
                        "product_id": int(p["sku"]),  # API требует int обычно
                        "quantity": int(p["quantity"])
                    })

                payload = {
                    "packages": [{"products": products_payload}],
                    "posting_number": p_num
                }

                async with session.post(url, json=payload, headers=HEADERS) as resp:
                    if resp.status == 200:
                        success_count += 1
                    else:
                        err_text = await resp.text()
                        print(f"Error shipping {p_num}: {err_text}")  # Логирование ошибки
                        errors.append(p_num)

    msg = f"✅ Автоматически собрано: {success_count}\n"
    msg += f"📦 Отправлено на ручную сборку: {virtual_count}"
    if errors:
        msg += f"\n❌ Ошибки API: {', '.join(errors)}"
    return msg