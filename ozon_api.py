import os
import logging
import aiohttp
import asyncio
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv
from database import (
    get_all_virtual_orders, remove_virtual_order, add_virtual_order,
    save_order_meta, get_all_meta_postings, delete_shipped_order, AsyncSessionLocal, Order
)
from sqlalchemy.dialects.postgresql import insert

load_dotenv()

OZON_CLIENT_ID = os.getenv("OZON_CLIENT_ID")
OZON_API_KEY = os.getenv("OZON_API_KEY")

try:
    OZON_WAREHOUSE_ID = int(os.getenv("OZON_WAREHOUSE_ID"))
    WAREHOUSE_LIST = [OZON_WAREHOUSE_ID]
except:
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
    date_to = datetime.now() + timedelta(days=1)
    date_from = date_to - timedelta(days=30)

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
        ozon_postings = await fetch_postings(session, status="awaiting_packaging")

    asyncio.create_task(cleanup_history())

    actual_numbers = {p['posting_number'] for p in ozon_postings}
    db_orders = await get_all_virtual_orders()

    # Удаляем из базы виртуальных те, которых уже нет в Озоне (отменены или отправлены вручную)
    for p_num in db_orders:
        if p_num not in actual_numbers:
            await remove_virtual_order(p_num)

    current_db = await get_all_virtual_orders()

    # Фильтруем: берем только те, которых НЕТ в виртуальных
    filtered_raw = [p for p in ozon_postings if p['posting_number'] not in current_db]

    return parse_orders(filtered_raw)


async def cleanup_history():
    """Проверяет заказы в нашей БД: если они уже отправлены Ozon, удаляем их.
    Все запросы выполняются параллельно через asyncio.gather.
    """
    stored_postings = await get_all_meta_postings()
    if not stored_postings:
        return

    url = "https://api-seller.ozon.ru/v3/posting/fbs/get"

    async def check_single(session, p_num):
        """Проверяет один заказ и удаляет из БД если уже отправлен."""
        try:
            payload = {"posting_number": p_num}
            async with session.post(url, json=payload, headers=HEADERS) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    status = data.get('result', {}).get('status')
                    if status not in ['awaiting_packaging', 'awaiting_deliver', 'arbitration']:
                        await delete_shipped_order(p_num)
                else:
                    logging.warning(f"cleanup_history: статус {resp.status} для {p_num}")
        except Exception as e:
            logging.error(f"cleanup_history: ошибка при проверке {p_num}: {e}")

    async with aiohttp.ClientSession() as session:
        # Запускаем все проверки параллельно — вместо N последовательных запросов
        # return_exceptions=True гарантирует что одна ошибка не отменит все остальные
        await asyncio.gather(
            *[check_single(session, p_num) for p_num in stored_postings],
            return_exceptions=True
        )


async def assemble_orders(sima_order_num, supply_date):
    """Собирает заказы."""
    async with aiohttp.ClientSession() as session:
        raw_postings = await fetch_postings(session)

    # Сохраняем сырые postings для доступа к in_process_at
    raw_by_number = {p['posting_number']: p for p in raw_postings}
    all_new = parse_orders(raw_postings)
    virtual_orders_db = await get_all_virtual_orders()

    success_count = 0
    virtual_count = 0
    errors = []

    sima_date_now = datetime.now().strftime("%d.%m.%Y")

    # Собираем все SKU для батч-запроса фото
    all_skus = []
    for order in all_new:
        for p in order['products']:
            if p.get('sku'):
                all_skus.append(int(p['sku']))

    # Получаем фото одним батч-запросом
    sku_images = {}
    if all_skus:
        sku_images = await fetch_product_images(all_skus)

    async with aiohttp.ClientSession() as session:
        for order in all_new:
            p_num = order['number']

            # Сохраняем полный JSON с артикулом, sku, количеством и фото
            products_full = []
            for p in order['products']:
                sku = int(p['sku']) if p.get('sku') else None
                products_full.append({
                    "offer_id": p.get('offer_id'),
                    "sku": sku,
                    "name": p.get('name'),
                    "quantity": p.get('quantity'),
                    "price": p.get('price', '0'),
                    "image_url": sku_images.get(sku, '') if sku else ''
                })

            # Парсим дату принятия заказа (in_process_at)
            raw = raw_by_number.get(p_num, {})
            accepted_at = None
            raw_dt = raw.get('in_process_at')
            if raw_dt:
                try:
                    accepted_at = datetime.fromisoformat(raw_dt.rstrip('Z').split('.')[0])
                except (ValueError, AttributeError):
                    accepted_at = None

            await save_order_meta(
                posting_number=p_num,
                products=products_full,
                sima_num=sima_order_num,
                sima_date=sima_date_now,
                deliv_date=supply_date,
                accepted_at=accepted_at,
            )

            if p_num in virtual_orders_db:
                continue

            total_items = sum(int(p['quantity']) for p in order['products'])

            if total_items > 1:
                await add_virtual_order(p_num)
                virtual_count += 1
            else:
                url = "https://api-seller.ozon.ru/v4/posting/fbs/ship"
                products_payload = []
                for p in order['products']:
                    try:
                        prod_id = int(p["sku"])
                    except:
                        prod_id = 0

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
        msg += f"\n❌ Ошибки API: {'; '.join(errors[:5])}..."
    return msg


async def get_total_ozon_demand():
    """
    Считает реальную потребность:
    Берет все заказы 'awaiting_packaging'
    И ИСКЛЮЧАЕТ из расчета те отправления, которые есть в таблице virtual_orders.
    """
    # 1. Получаем список ID виртуальных заказов
    virtual_postings_ids = set(await get_all_virtual_orders())

    async with aiohttp.ClientSession() as session:
        # [cite_start]Получаем все текущие заказы с Ozon [cite: 1]
        postings = await fetch_postings(session, status="awaiting_packaging")

    final_demand = {}

    for p in postings:
        # 2. Если номер отправления есть в списке виртуальных — пропускаем его целиком.
        if p.get('posting_number') in virtual_postings_ids:
            continue

        # 3. Если заказа нет в виртуальных, считаем его товары
        for product in p.get('products', []):
            art = str(product.get('offer_id') or product.get('sku'))
            qty = int(product.get('quantity', 0))
            final_demand[art] = final_demand.get(art, 0) + qty

    return final_demand

async def sync_orders_to_db(postings):
    """Синхронизирует сырые posting'и из Ozon API в БД (до parse_orders)"""
    async with AsyncSessionLocal() as db:
        for p in postings:
            # Парсим дату — shipment_date может быть None или пустой строкой
            ozon_dt = None
            raw_date = p.get('shipment_date') or p.get('ship_date')
            if raw_date:
                try:
                    # Ozon присылает формат "2024-10-25T10:00:00.000Z"
                    ozon_dt = datetime.fromisoformat(raw_date.rstrip('Z').split('.')[0])
                except (ValueError, AttributeError):
                    ozon_dt = None

            stmt = insert(Order).values(
                posting_number=p.get('posting_number') or p.get('number'),
                ozon_status=p.get('status', 'unknown'),
                products_json=json.dumps(p.get('products', []), ensure_ascii=False),
                ozon_created_at=ozon_dt
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=['posting_number'],
                set_={'ozon_status': p.get('status', 'unknown')}
            )
            await db.execute(stmt)
        await db.commit()

async def fetch_product_images(skus: list) -> dict:
    """Gets primary_image for a list of SKUs via /v3/product/info/list.
    Returns dict {sku: image_url}. Batches of 100 (API limit).
    """
    result = {}
    url = "https://api-seller.ozon.ru/v3/product/info/list"
    async with aiohttp.ClientSession() as session:
        for i in range(0, len(skus), 100):
            batch = skus[i:i+100]
            try:
                async with session.post(url, json={"sku": batch}, headers=HEADERS) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for item in data.get("result", {}).get("items", []):
                            sku = item.get("sku") or item.get("fbs_sku")
                            images = item.get("primary_image", [])
                            if sku and images:
                                result[int(sku)] = images[0] if isinstance(images, list) else images
            except Exception as e:
                logging.warning(f"fetch_product_images error: {e}")
    return result