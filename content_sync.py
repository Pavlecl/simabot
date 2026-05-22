"""
content_sync.py — ~/sima_bot/content_sync.py

Сервис синхронизации контента Ozon ↔ Wildberries.
Сопоставление по offer_id (Ozon) == vendorCode (WB).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Optional

import aiohttp
from sqlalchemy import select, update

from database import AsyncSessionLocal, WbAccount



# ─────────────────────────────────────────────────
# Структуры данных
# ─────────────────────────────────────────────────

@dataclass
class ProductContent:
    vendor_code: str
    name: str = ""
    description: str = ""
    images: list[str] = field(default_factory=list)
    attributes: list[dict] = field(default_factory=list)
    product_id: Optional[int] = None
    ozon_attributes_raw: list[dict] = field(default_factory=list)
    nm_id: Optional[int] = None


@dataclass
class MatchedProduct:
    vendor_code: str
    ozon: Optional[ProductContent]
    wb:   Optional[ProductContent]


# ─────────────────────────────────────────────────
# Получение всех товаров Ozon (Content API)
# ─────────────────────────────────────────────────

async def fetch_ozon_products(headers: dict) -> dict[str, ProductContent]:
    result: dict[str, ProductContent] = {}

    async with aiohttp.ClientSession() as session:
        product_ids: list[int] = []
        offer_map:   dict[int, str] = {}
        last_id = ""

        while True:
            async with session.post(
                "https://api-seller.ozon.ru/v3/product/list",
                headers=headers,
                json={"filter": {}, "last_id": last_id, "limit": 1000},
            ) as resp:
                data = await resp.json()
            items = data.get("result", {}).get("items", [])
            if not items:
                break
            for item in items:
                product_ids.append(item["product_id"])
                offer_map[item["product_id"]] = item["offer_id"]
            last_id = data.get("result", {}).get("last_id", "")
            if not last_id:
                break

        if not product_ids:
            return result

        for i in range(0, len(product_ids), 100):
            batch = product_ids[i:i + 100]
            async with session.post(
                "https://api-seller.ozon.ru/v3/product/info/list",
                headers=headers,
                json={"product_id": batch},
            ) as resp:
                info_data = await resp.json(content_type=None)
            for p in info_data.get("items", []):
                pid      = p["id"]
                offer_id = offer_map.get(pid, str(pid))
                attrs = [
                    {
                        "name":  a.get("name", ""),
                        "value": " / ".join(v.get("value", "") for v in a.get("values", [])),
                    }
                    for a in p.get("attributes", []) if a.get("name")
                ]
                result[offer_id] = ProductContent(
                    vendor_code         = offer_id,
                    product_id          = pid,
                    name                = p.get("name", ""),
                    description         = p.get("description", ""),
                    images              = p.get("images", []),
                    attributes          = attrs,
                    ozon_attributes_raw = p.get("attributes", []),
                )

    return result


# ─────────────────────────────────────────────────
# Получение всех карточек WB (Content API v2)
# ─────────────────────────────────────────────────

async def fetch_wb_products(wb_api_key: str) -> dict[str, ProductContent]:
    headers = {"Authorization": wb_api_key, "Content-Type": "application/json"}
    result: dict[str, ProductContent] = {}
    cursor: dict = {}

    async with aiohttp.ClientSession() as session:
        while True:
            async with session.post(
                "https://content-api.wildberries.ru/content/v2/get/cards/list",
                headers=headers,
                json={"settings": {"cursor": {**cursor, "limit": 100}, "filter": {"withPhoto": -1}}},
            ) as resp:
                data = await resp.json()

            cards = data.get("cards", [])
            if not cards:
                break

            for card in cards:
                nm_id       = card.get("nmID")
                vendor_code = card.get("vendorCode") or str(nm_id)

                # Берём готовые URL из API — максимальное качество (big)
                photos = card.get("photos", [])
                images = [p["big"] for p in photos if p.get("big")][:10]

                attrs = [
                    {
                        "name":  ch.get("name", ""),
                        "value": " / ".join(
                            str(v) for v in (
                                ch["value"] if isinstance(ch.get("value"), list)
                                else [ch["value"]] if ch.get("value") is not None
                                else []
                            )
                        ),
                    }
                    for ch in card.get("characteristics", []) if ch.get("name")
                ]
                result[vendor_code] = ProductContent(
                    vendor_code = vendor_code,
                    nm_id       = nm_id,
                    name        = card.get("title", ""),
                    description = card.get("description", ""),
                    images      = images,
                    attributes  = attrs,
                )

            cur = data.get("cursor", {})
            if cur.get("total", 0) < 100:  # правильное условие из документации WB
                break
            cursor = {"updatedAt": cur["updatedAt"], "nmID": cur["nmID"]}

    return result


# ─────────────────────────────────────────────────
# Сопоставление по артикулу продавца
# ─────────────────────────────────────────────────

async def get_matched_products(ozon_headers: dict, wb_api_key: str) -> list[MatchedProduct]:
    ozon, wb = await asyncio.gather(
        fetch_ozon_products(ozon_headers),
        fetch_wb_products(wb_api_key),
    )
    codes = sorted(set(ozon) & set(wb))
    return [MatchedProduct(vendor_code=c, ozon=ozon[c], wb=wb[c]) for c in codes]


# ─────────────────────────────────────────────────
# Применение контента WB → Ozon
# ─────────────────────────────────────────────────

async def apply_wb_to_ozon(
    ozon_headers: dict,
    wb_api_key:   str,
    tasks:        list[dict],
) -> list[dict]:
    """Применяет контент WB → Ozon только для товаров из tasks."""

    needed_codes = {t["vendor_code"] for t in tasks}

    # 1. WB данные берём из запроса (уже показаны пользователю)
    wb = {}
    for task in tasks:
        if task.get("wb_data"):
            d = task["wb_data"]
            wb[task["vendor_code"]] = ProductContent(
                vendor_code=task["vendor_code"],
                name=d.get("name", ""),
                description=d.get("description", ""),
                images=d.get("images", []),
                attributes=d.get("attributes", []),
                nm_id=d.get("nm_id"),
            )

    # 2. product_id из локальной БД — быстро, без API
    from database import Product as ProductModel
    ozon_ids: dict[str, int] = {}
    async with AsyncSessionLocal() as db:
        r = await db.execute(
            select(ProductModel.offer_id, ProductModel.product_id).where(
                ProductModel.offer_id.in_(list(needed_codes))
            )
        )
        ozon_ids = {row.offer_id: row.product_id for row in r.fetchall() if row.product_id}

    print(f"[apply] tasks={len(tasks)} wb_found={len(wb)} ozon_ids_found={len(ozon_ids)}", flush=True)

    results = []
    async with aiohttp.ClientSession() as session:
        for task in tasks:
            code   = task["vendor_code"]
            fields = set(task.get("fields", []))
            wp     = wb.get(code)
            pid    = ozon_ids.get(code)

            print(f"[apply] code={code} pid={pid} fields={fields} images={wp.images[:1] if wp else []}", flush=True)

            if not wp:
                results.append({"vendor_code": code, "status": "error", "error": "Товар не найден на WB"})
                continue
            if not pid:
                results.append({"vendor_code": code, "status": "error", "error": "product_id не найден в БД"})
                continue

            errors = []

            # ── Текст ────────────────────────────────────────────────────────────
            text_fields = fields & {"name", "description"}
            if text_fields:
                # 1. Получаем полные текущие данные товара с Ozon
                try:
                    async with session.post(
                            "https://api-seller.ozon.ru/v3/product/info/list",
                            headers=ozon_headers,
                            json={"offer_id": [code]},
                    ) as r:
                        info = await r.json(content_type=None)
                    current = info.get("items", [{}])[0]
                    if not current:
                        errors.append("Не удалось получить данные товара с Ozon")
                        raise StopIteration

                    # 2. Формируем payload — берём все текущие поля и меняем только нужные
                    update_item = {
                        "offer_id": code,
                        "name": wp.name if "name" in text_fields else current.get("name", ""),
                        "description": wp.description if "description" in text_fields else current.get("description",
                                                                                                       ""),
                        "description_category_id": current.get("description_category_id"),
                        "type_id": current.get("type_id"),
                        "price": current.get("price", "0"),
                        "vat": current.get("vat", "0"),
                        "attributes": current.get("attributes", []),
                        "images": current.get("images", []),
                    }
                    print(f"[apply] text import payload keys={list(update_item.keys())}", flush=True)

                    # 3. Обновляем
                    async with session.post(
                            "https://api-seller.ozon.ru/v3/product/import",
                            headers=ozon_headers,
                            json={"items": [update_item]},
                    ) as r:
                        txt = await r.text()
                        print(f"[apply] text status={r.status} resp={txt[:200]}", flush=True)
                        if r.status != 200:
                            errors.append(f"Текст {r.status}: {txt[:200]}")
                        else:
                            # Обновляем локальную БД чтобы таблица показывала актуальные данные
                            async with AsyncSessionLocal() as db:
                                update_vals = {}
                                if "name" in text_fields: update_vals["name"] = wp.name
                                if "description" in text_fields: update_vals["description"] = wp.description
                                if update_vals:
                                    await db.execute(
                                        update(ProductModel)
                                        .where(ProductModel.offer_id == code)
                                        .values(**update_vals)
                                    )
                                    await db.commit()
                except StopIteration:
                    pass
                except Exception as e:
                    errors.append(f"Текст: {e}")

            # ── Фото ─────────────────────────────────────────────────────
            if "images" in fields and wp.images:
                # Конвертируем .webp → .jpg (Ozon принимает только JPG/PNG)
                PROXY_BASE = "https://simacontrol.ru/api/img-proxy?url="
                images_to_send = [
                    f"{PROXY_BASE}{u}" for u in wp.images[:10] if u
                ]
                print(f"[apply] photo pid={pid} count={len(images_to_send)} url[0]={images_to_send[0] if images_to_send else None}", flush=True)
                try:
                    async with session.post(
                        "https://api-seller.ozon.ru/v1/product/pictures/import",
                        headers=ozon_headers,
                        json={"product_id": pid, "images": images_to_send},
                    ) as r:
                        txt = await r.text()
                        print(f"[apply] photo status={r.status} resp={txt[:300]}", flush=True)
                        if r.status != 200:
                            errors.append(f"Фото {r.status}: {txt[:200]}")
                except Exception as e:
                    errors.append(f"Фото: {e}")

            results.append({
                "vendor_code": code,
                "status": "error" if errors else "ok",
                "error": "; ".join(errors) if errors else None,
            })

    return results


# ─────────────────────────────────────────────────
# Вспомогательные функции
# ─────────────────────────────────────────────────

def _merge_attributes(ozon_raw: list[dict], wb_attrs: list[dict]) -> list[dict]:
    wb_map = {a["name"].lower(): a["value"] for a in wb_attrs if a.get("name")}
    merged = []
    for attr in ozon_raw:
        key = attr.get("name", "").lower()
        if key in wb_map:
            merged.append({**attr, "values": [{"value": wb_map[key]}]})
        else:
            merged.append(attr)
    return merged


async def _validate_wb_images(urls: list[str]) -> list[str]:
    """Возвращаем URL как есть — Ozon сам проверит доступность."""
    return [u for u in urls if u]


# ─────────────────────────────────────────────────
# Работа с WbAccount в БД
# ─────────────────────────────────────────────────

async def get_active_wb_key() -> Optional[str]:
    async with AsyncSessionLocal() as db:
        r = await db.execute(select(WbAccount).where(WbAccount.is_active == True))
        account = r.scalar_one_or_none()
        return account.api_key if account else None


async def get_all_wb_accounts_db() -> list[WbAccount]:
    async with AsyncSessionLocal() as db:
        r = await db.execute(select(WbAccount).order_by(WbAccount.id))
        return r.scalars().all()