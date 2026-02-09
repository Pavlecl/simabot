import os
import aiohttp
import asyncio
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv
from database import (
    get_all_virtual_orders, remove_virtual_order, add_virtual_order,
    save_order_meta, get_all_meta_postings, delete_shipped_order
)

load_dotenv()

OZON_CLIENT_ID = os.getenv("OZON_CLIENT_ID")
OZON_API_KEY = os.getenv("OZON_API_KEY")
# Превращаем строку списка ID складов в список int, если нужно, или просто берем один
# Предполагаем, что у вас один склад, как в оригинале, или список.
# Для совместимости со старым кодом оставим как int, если там одно число.
try:
    OZON_WAREHOUSE_ID = int(os.getenv("OZON_WAREHOUSE_ID"))
    WAREHOUSE_LIST = [OZON_WAREHOUSE_ID]
except:
    # Если вдруг там список складов через запятую
    WAREHOUSE_LIST = [int(x) for x in os.getenv("OZON_WAREHOUSE_ID", "").split(",") if x]

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
        status = posting.get("status")

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
            "status": status,
            "products": products_list
        })
    return orders


async def fetch_postings(session, status="awaiting_packaging"):
    """Запрашивает список отправлений с определенным статусом"""
    url = "https://api-seller.ozon.ru/v3/posting/fbs/list"
    date_to = datetime.now() + timedelta(days=1)  # Берем с запасом вперед
    date_from = date_to - timedelta(days=30)  # Смотрим назад на месяц

    payload = {
        "dir": "ASC",
        "filter": {
            "status": status,
            "warehouse_id": WAREHOUSE_LIST,
            "since": date_from.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "to": date_to.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        },
        "limit": 1000,
        "with": {"analytics_data": False, "financial_data": False}
    }

    async with session.post(url, json=payload, headers=HEADERS) as response:
        if response.status != 200:
            text = await response.text()
            print(f"Error fetching postings: {text}")
            return []
        data = await response.json()
        return data.get('result', {}).get('postings', [])


async def get_new_orders():
    """Получает заказы и чистит базу от устаревших"""
    async with aiohttp.ClientSession() as session:
        # 1. Получаем актуальные заказы "в сборке"
        ozon_postings = await fetch_postings(session, status="awaiting_packaging")

    # --- ОЧИСТКА ИСТОРИИ (НОВЫЙ ФУНКЦИОНАЛ) ---
    # Запускаем фоновую проверку статусов для очистки БД
    asyncio.create_task(cleanup_history())

    # Логика виртуальных заказов (старая)
    actual_numbers = {p['posting_number'] for p in ozon_postings}
    db_orders = await get_all_virtual_orders()
    for p_num in db_orders:
        if p_num not in actual_numbers:
            await remove_virtual_order(p_num)

    current_db = await get_all_virtual_orders()
    filtered_raw = [p for p in ozon_postings if p['posting_number'] not in current_db]

    return parse_orders(filtered_raw)


async def cleanup_history():
    """Проверяет заказы в нашей БД: если они уже отправлены Ozon, удаляем их"""
    stored_postings = await get_all_meta_postings()
    if not stored_postings:
        return

    # Ozon API позволяет запросить инфу по конкретным номерам
    url = "https://api-seller.ozon.ru/v3/posting/fbs/get"

    async with aiohttp.ClientSession() as session:
        for p_num in stored_postings:
            payload = {"posting_number": p_num}
            async with session.post(url, json=payload, headers=HEADERS) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    res = data.get('result', {})
                    status = res.get('status')

                    # Если статус уже не "ждет сборки" и не "ждет отгрузки" (т.е. уехал или отменен)
                    # awaiting_deliver - это когда мы собрали, но еще не сдали. Это храним.
                    # delivering, delivered, cancelled - удаляем.
                    if status not in ['awaiting_packaging', 'awaiting_deliver', 'arbitration']:
                        await delete_shipped_order(p_num)


async def assemble_orders(sima_order_num, supply_date):
    """
    Собирает заказы.
    Принимает данные от пользователя для сохранения в историю.
    """
    async with aiohttp.ClientSession() as session:
        raw_postings = await fetch_postings(session)

    all_new = parse_orders(raw_postings)
    virtual_orders_db = await get_all_virtual_orders()

    success_count = 0
    virtual_count = 0
    errors = []

    # Текущая дата для фиксации заказа Сима
    sima_date_now = datetime.now().strftime("%d.%m.%Y")

    async with aiohttp.ClientSession() as session:
        for order in all_new:
            p_num = order['number']

            # --- СОХРАНЕНИЕ В ИСТОРИЮ (НОВОЕ) ---
            # Сохраняем ВСЕ заказы, которые попали в сборку (и одиночные, и виртуальные)
            product_names = [f"{p['name']} ({p['quantity']} шт.)" for p in order['products']]

            await save_order_meta(
                posting_number=p_num,
                products=product_names,
                sima_num=sima_order_num,
                sima_date=sima_date_now,  # Считаем, что дата заказа у Сима = сегодня (день сборки)
                deliv_date=supply_date
            )
            # ------------------------------------

            if p_num in virtual_orders_db:
                continue

            total_items = sum(int(p['quantity']) for p in order['products'])

            if total_items > 1:
                await add_virtual_order(p_num)
                virtual_count += 1
            else:
                # ОДИНОЧНЫЙ: Собираем
                url = "https://api-seller.ozon.ru/v4/posting/fbs/ship"
                products_payload = []
                for p in order['products']:
                    # Используем offer_id если он числовой, иначе нужно искать product_id.
                    # Для надежности Ozon рекомендует передавать quantity и product_id
                    # Но часто работает и связка products=[{product_id: ..., quantity: ...}]
                    # В вашем старом коде было int(sku). Оставим SKU, но сделаем try/except
                    try:
                        prod_id = int(p["sku"])
                    except:
                        # Фолбэк, если SKU не число
                        prod_id = 0  # Тут может быть ошибка, если SKU строковый

                    products_payload.append({
                        "product_id": prod_id,
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
                        errors.append(f"{p_num}: {err_text}")

    msg = f"✅ Собранные данные сохранены в БД.\n"
    msg += f"🚚 Отправлено API (Одиночные): {success_count}\n"
    msg += f"📦 В виртуальных (Многопозиционные): {virtual_count}"
    if errors:
        msg += f"\n❌ Ошибки API: {'; '.join(errors[:5])}..."  # Показываем первые 5 ошибок
    return msg