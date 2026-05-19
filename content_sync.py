"""
content_sync.py — ~/sima_bot/content_sync.py

Сервис синхронизации контента Ozon ↔ Wildberries.
Сопоставление по offer_id (Ozon) == vendorCode (WB).
Использует aiohttp — как и весь остальной проект.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Optional

import aiohttp
from sqlalchemy import select

from database import AsyncSessionLocal, WbAccount


# ─────────────────────────────────────────────────
# WB CDN: вычисление номера basket-сервера по nmId
# ─────────────────────────────────────────────────

def _wb_basket(nm_id: int) -> str:
    vol = nm_id // 100_000
    for threshold, basket in [
        (143,"01"),(287,"02"),(431,"03"),(719,"04"),(1007,"05"),(1061,"06"),
        (1115,"07"),(1169,"08"),(1313,"09"),(1601,"10"),(1655,"11"),(1919,"12"),
        (2045,"13"),(2189,"14"),(2405,"15"),(2621,"16"),(2837,"17"),
    ]:
        if vol <= threshold:
            return basket
    return "18"


def wb_photo_url(nm_id: int, index: int = 1) -> str:
    b    = _wb_basket(nm_id)
    vol  = nm_id // 100_000
    part = nm_id // 1_000
    return f"https://basket-{b}.wbbasket.ru/vol{vol}/part{part}/{nm_id}/images/big/{index}.jpg"


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
        # 1. Собираем все product_id через /v3/product/list
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

        # 2. Детали (название, описание, фото, атрибуты) — батчами по 100
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
                    vendor_code          = offer_id,
                    product_id           = pid,
                    name                 = p.get("name", ""),
                    description          = p.get("description", ""),
                    images               = p.get("images", []),
                    attributes           = attrs,
                    ozon_attributes_raw  = p.get("attributes", []),
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
                nm_id        = card.get("nmID")
                vendor_code  = card.get("vendorCode") or str(nm_id)
                photos = card.get("photos", [])
                images = [p["big"] for p in photos if p.get("big")][:10]
                attrs  = [
                    {
                        "name":  ch.get("name", ""),
                        "value": " / ".join(str(v) for v in (ch["value"] if isinstance(ch.get("value"), list) else [ch["value"]] if ch.get("value") is not None else [])),
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
            if not cur.get("updatedAt") or not cur.get("nmID"):
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
    codes = sorted(set(ozon) & set(wb))   # только пары где товар есть на обоих МП
    return [MatchedProduct(vendor_code=c, ozon=ozon[c], wb=wb[c]) for c in codes]


# ─────────────────────────────────────────────────
# Применение контента WB → Ozon
# ─────────────────────────────────────────────────

async def apply_wb_to_ozon(
    ozon_headers: dict,
    wb_api_key:   str,
    tasks:        list[dict],   # [{vendor_code, fields: [name|description|images|attributes]}]
) -> list[dict]:

    ozon, wb = await asyncio.gather(
        fetch_ozon_products(ozon_headers),
        fetch_wb_products(wb_api_key),
    )

    results = []
    async with aiohttp.ClientSession() as session:
        for task in tasks:
            code   = task["vendor_code"]
            fields = set(task.get("fields", []))
            op = ozon.get(code)
            wp = wb.get(code)

            if not op or not wp:
                results.append({"vendor_code": code, "status": "error", "error": "Товар не найден"})
                continue

            errors = []

            # ── Текст: name / description / attributes ────────────────────
            text_fields = fields & {"name", "description", "attributes"}
            if text_fields:
                payload: dict = {"offer_id": code}
                if "name"        in text_fields: payload["name"]        = wp.name
                if "description" in text_fields: payload["description"] = wp.description
                if "attributes"  in text_fields:
                    payload["attributes"] = _merge_attributes(op.ozon_attributes_raw, wp.attributes)
                try:
                    async with session.post(
                        "https://api-seller.ozon.ru/v1/product/update",
                        headers=ozon_headers,
                        json={"items": [payload]},
                    ) as r:
                        if r.status != 200:
                            txt = await r.text()
                            errors.append(f"Текст {r.status}: {txt[:200]}")
                except Exception as e:
                    errors.append(f"Текст: {e}")

            # ── Фото ──────────────────────────────────────────────────────
            if "images" in fields and wp.images:
                valid = await _validate_wb_images(wp.images[:10])
                if valid:
                    try:
                        async with session.post(
                            "https://api-seller.ozon.ru/v1/product/import/pictures",
                            headers=ozon_headers,
                            json={"product_id": op.product_id, "images": valid},
                        ) as r:
                            if r.status != 200:
                                txt = await r.text()
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
    """Обновляет значения Ozon-атрибутов значениями WB по совпадению имени."""
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
    """WB CDN не поддерживает HEAD — просто возвращаем URL как есть."""
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