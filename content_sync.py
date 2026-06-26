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
    LIMIT = 100

    async with aiohttp.ClientSession() as session:
        while True:
            async with session.post(
                "https://content-api.wildberries.ru/content/v2/get/cards/list",
                headers=headers,
                json={"settings": {
                    "sort":   {"ascending": False},
                    "cursor": {**cursor, "limit": LIMIT},
                    "filter": {"withPhoto": -1},
                }},
            ) as resp:
                data = await resp.json(content_type=None)

            cards = data.get("cards", [])
            if not cards:
                break

            for card in cards:
                nm_id       = card.get("nmID")
                vendor_code = card.get("vendorCode") or str(nm_id)

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

            # Правильное условие из документации WB: total < limit = последняя страница
            cur = data.get("cursor", {})
            if cur.get("total", 0) < LIMIT:
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

    # 1. WB данные берём из запроса (уже показаны пользователю — стабильно)
    from database import Product as ProductModel
    wb: dict[str, ProductContent] = {}
    for task in tasks:
        if task.get("wb_data"):
            d = task["wb_data"]
            wb[task["vendor_code"]] = ProductContent(
                vendor_code = task["vendor_code"],
                name        = d.get("name", ""),
                description = d.get("description", ""),
                images      = d.get("images", []),
                attributes  = d.get("attributes", []),
                nm_id       = d.get("nm_id"),
            )

    # 2. product_id из локальной БД
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

            print(f"[apply] code={code} pid={pid} fields={fields}", flush=True)

            if not wp:
                results.append({"vendor_code": code, "status": "error", "error": "Нет WB-данных в запросе"})
                continue
            if not pid:
                results.append({"vendor_code": code, "status": "error", "error": "product_id не найден в БД"})
                continue

            errors = []

            # ── Текст (name / description) ────────────────────────────────
            text_fields = fields & {"name", "description"}
            if text_fields:
                try:
                    # Получаем полные данные товара из Ozon (v4 — включая размеры)
                    async with session.post(
                        "https://api-seller.ozon.ru/v4/product/info/attributes",
                        headers=ozon_headers,
                        json={"filter": {"offer_id": [code]}, "limit": 1, "sort_dir": "ASC"},
                    ) as r:
                        info4 = await r.json(content_type=None)
                    current4 = info4.get("result", [{}])[0]

                    # v3 — только цена и ставка НДС
                    async with session.post(
                        "https://api-seller.ozon.ru/v3/product/info/list",
                        headers=ozon_headers,
                        json={"offer_id": [code]},
                    ) as r:
                        info3 = await r.json(content_type=None)
                    current3 = info3.get("items", [{}])[0]

                    if not current4:
                        errors.append("Не удалось получить данные товара с Ozon")
                        raise StopIteration

                    update_item = {
                        "offer_id":                code,
                        "name":                    wp.name if "name" in text_fields else current4.get("name", ""),
                        "description":             wp.description if "description" in text_fields else "",
                        "description_category_id": current4.get("description_category_id"),
                        "type_id":                 current4.get("type_id"),
                        "price":                   current3.get("price", "0"),
                        "vat":                     current3.get("vat", "0"),
                        "attributes":              current4.get("attributes", []),
                        "images":                  current4.get("images", []),
                        "depth":                   current4.get("depth", 0),
                        "width":                   current4.get("width", 0),
                        "height":                  current4.get("height", 0),
                        "dimension_unit":          current4.get("dimension_unit", "mm"),
                        "weight":                  current4.get("weight", 0),
                        "weight_unit":             current4.get("weight_unit", "g"),
                    }

                    print(f"[apply] text import offer_id={code}", flush=True)
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
                            # Обновляем локальную БД
                            db_vals: dict = {}
                            if "name"        in text_fields: db_vals["name"]        = wp.name
                            if "description" in text_fields: db_vals["description"] = wp.description
                            if db_vals:
                                async with AsyncSessionLocal() as db:
                                    await db.execute(
                                        update(ProductModel)
                                        .where(ProductModel.offer_id == code)
                                        .values(**db_vals)
                                    )
                                    await db.commit()

                except StopIteration:
                    pass
                except Exception as e:
                    errors.append(f"Текст: {e}")

            # ── Фото ─────────────────────────────────────────────────────
            if "images" in fields and wp.images:
                PROXY_BASE = "https://simacontrol.ru/api/img-proxy?url="
                images_to_send = [f"{PROXY_BASE}{u}" for u in wp.images[:10] if u]
                print(f"[apply] photo pid={pid} count={len(images_to_send)}", flush=True)
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
                        else:
                            # Обновляем главное фото в локальной БД
                            if wp.images:
                                async with AsyncSessionLocal() as db:
                                    await db.execute(
                                        update(ProductModel)
                                        .where(ProductModel.offer_id == code)
                                        .values(image_url=wp.images[0])
                                    )
                                    await db.commit()
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


# ═══════════════════════════════════════════════════════════════
# WB → OZON: ПЕРЕНОС КАРТОЧЕК
# ═══════════════════════════════════════════════════════════════

import json as _json
from datetime import datetime, timedelta


async def _ozon_headers_for_account(account_id: int) -> dict:
    from database import OzonAccount
    async with AsyncSessionLocal() as db:
        r = await db.execute(select(OzonAccount).where(OzonAccount.id == account_id))
        acc = r.scalar_one_or_none()
    if not acc:
        raise ValueError(f"Ozon account {account_id} not found")
    return {"Client-Id": str(acc.client_id), "Api-Key": acc.api_key, "Content-Type": "application/json"}


async def _ozon_get_offer_ids(headers: dict) -> set[str]:
    """Получаем все offer_id из кабинета Ozon."""
    ids: set[str] = set()
    last_id = ""
    async with aiohttp.ClientSession() as session:
        while True:
            async with session.post(
                "https://api-seller.ozon.ru/v3/product/list",
                headers=headers,
                json={"filter": {}, "last_id": last_id, "limit": 1000},
            ) as resp:
                data = await resp.json(content_type=None)
            items = data.get("result", {}).get("items", [])
            if not items:
                break
            for item in items:
                ids.add(item["offer_id"])
            last_id = data.get("result", {}).get("last_id", "")
            if not last_id:
                break
    return ids


async def _wb_get_stats(wb_api_key: str, nm_ids: list[int]) -> dict[int, dict]:
    """Заказы за неделю и месяц по nm_id через WB nm-report."""
    if not nm_ids:
        return {}

    today = datetime.now()
    stats_week: dict[int, int] = {}
    stats_month: dict[int, int] = {}
    headers = {"Authorization": wb_api_key, "Content-Type": "application/json"}

    async with aiohttp.ClientSession() as session:
        for period_key, days, store in [
            ("week",  7,  stats_week),
            ("month", 30, stats_month),
        ]:
            begin = (today - timedelta(days=days)).strftime("%Y-%m-%d 00:00:00")
            end   = today.strftime("%Y-%m-%d 23:59:59")
            page  = 1
            while True:
                try:
                    async with session.post(
                        "https://suppliers-api.wildberries.ru/api/v2/nm-report/detail",
                        headers=headers,
                        json={
                            "nmIDs": nm_ids,
                            "timezone": "Europe/Moscow",
                            "period": {"begin": begin, "end": end},
                            "orderBy": {"field": "ordersCount", "mode": "desc"},
                            "page": page,
                        },
                    ) as resp:
                        data = await resp.json(content_type=None)
                    cards = data.get("data", {}).get("cards", [])
                    for card in cards:
                        nm_id = card.get("nmID")
                        for stat in card.get("statistics", []):
                            sp = stat.get("selectedPeriod", stat)
                            store[nm_id] = store.get(nm_id, 0) + sp.get("ordersCount", 0)
                    if not data.get("data", {}).get("isNext"):
                        break
                    page += 1
                except Exception as e:
                    print(f"[wb-stats] {period_key} error: {e}", flush=True)
                    break

    return {
        nm_id: {"week": stats_week.get(nm_id, 0), "month": stats_month.get(nm_id, 0)}
        for nm_id in nm_ids
    }


async def find_wb_missing_from_ozon(ozon_account_id: int, wb_api_key: str) -> list[dict]:
    """WB-товары, которых нет в указанном Ozon-кабинете, с заказами за нед/мес."""
    from database import WbProductCache

    headers  = await _ozon_headers_for_account(ozon_account_id)
    ozon_ids = await _ozon_get_offer_ids(headers)

    async with AsyncSessionLocal() as db:
        r = await db.execute(select(WbProductCache))
        wb_all = r.scalars().all()

    missing = [p for p in wb_all if p.vendor_code not in ozon_ids]

    nm_ids = [p.nm_id for p in missing if p.nm_id]
    stats: dict[int, dict] = {}
    try:
        stats = await _wb_get_stats(wb_api_key, nm_ids)
    except Exception as e:
        print(f"[cross] stats failed: {e}", flush=True)

    result = []
    for p in missing:
        images = _json.loads(p.images_json or "[]")
        nm_stat = stats.get(p.nm_id, {"week": 0, "month": 0}) if p.nm_id else {"week": 0, "month": 0}
        result.append({
            "vendor_code":   p.vendor_code,
            "nm_id":         p.nm_id,
            "name":          p.name or "",
            "image":         images[0] if images else "",
            "orders_week":   nm_stat["week"],
            "orders_month":  nm_stat["month"],
        })

    result.sort(key=lambda x: x["orders_month"], reverse=True)
    return result


async def _ozon_search_category(session: aiohttp.ClientSession, headers: dict, name: str) -> tuple[int, int]:
    """Поиск description_category_id + type_id по названию товара."""
    try:
        async with session.post(
            "https://api-seller.ozon.ru/v1/description-category/search",
            headers=headers,
            json={"language": "RU", "query": name[:100]},
        ) as resp:
            data = await resp.json(content_type=None)
        results = data.get("result", [])
        if results:
            first = results[0]
            return first.get("description_category_id", 0), first.get("type_id", 0)
    except Exception as e:
        print(f"[cat-search] {e}", flush=True)
    return 0, 0


async def _ozon_required_attrs(
    session: aiohttp.ClientSession, headers: dict, desc_cat_id: int, type_id: int
) -> list[dict]:
    """Обязательные атрибуты категории Ozon."""
    try:
        async with session.post(
            "https://api-seller.ozon.ru/v1/description-category/attribute",
            headers=headers,
            json={"description_category_id": desc_cat_id, "type_id": type_id, "language": "RU"},
        ) as resp:
            data = await resp.json(content_type=None)
        return [a for a in data.get("result", []) if a.get("is_required")]
    except Exception as e:
        print(f"[attrs] {e}", flush=True)
    return []


async def create_ozon_cards_from_wb(ozon_account_id: int, vendor_codes: list[str]) -> list[dict]:
    """Создаёт новые карточки на Ozon из WB-кэша."""
    from database import WbProductCache

    headers = await _ozon_headers_for_account(ozon_account_id)
    PROXY = "https://simacontrol.ru/api/img-proxy?url="

    async with AsyncSessionLocal() as db:
        r = await db.execute(
            select(WbProductCache).where(WbProductCache.vendor_code.in_(vendor_codes))
        )
        cache = {p.vendor_code: p for p in r.scalars().all()}

    results = []
    async with aiohttp.ClientSession() as session:
        for vc in vendor_codes:
            p = cache.get(vc)
            if not p:
                results.append({"vendor_code": vc, "status": "error", "error": "Не найден в кеше WB"})
                continue

            images_raw = _json.loads(p.images_json or "[]")
            images = [f"{PROXY}{u}" for u in images_raw[:10] if u]
            if not images:
                results.append({"vendor_code": vc, "status": "error", "error": "Нет фотографий"})
                continue

            # Определяем категорию
            desc_cat_id, type_id = await _ozon_search_category(session, headers, p.name or vc)
            if not desc_cat_id:
                results.append({"vendor_code": vc, "status": "error", "error": "Не удалось определить категорию Ozon"})
                continue

            # Заполняем атрибуты
            req_attrs  = await _ozon_required_attrs(session, headers, desc_cat_id, type_id)
            wb_attrs   = {a["name"].lower(): a["value"] for a in _json.loads(p.attributes_json or "[]")}
            ozon_attrs = []
            for attr in req_attrs:
                key = attr.get("name", "").lower()
                val = wb_attrs.get(key) or (p.name or vc)
                ozon_attrs.append({
                    "id": attr["id"],
                    "complex_id": 0,
                    "values": [{"value": str(val)[:500]}],
                })

            item = {
                "name":                    (p.name or vc)[:500],
                "offer_id":                vc,
                "description_category_id": desc_cat_id,
                "type_id":                 type_id,
                "price":                   "100",
                "vat":                     "0",
                "images":                  images,
                "description":             (p.description or "")[:10000],
                "attributes":              ozon_attrs,
            }

            try:
                async with session.post(
                    "https://api-seller.ozon.ru/v3/product/import",
                    headers=headers,
                    json={"items": [item]},
                ) as resp:
                    resp_data = await resp.json(content_type=None)
                if resp.status == 200:
                    task_id = resp_data.get("result", {}).get("task_id")
                    results.append({"vendor_code": vc, "status": "ok", "task_id": task_id})
                else:
                    err = resp_data.get("message") or str(resp_data)[:300]
                    results.append({"vendor_code": vc, "status": "error", "error": f"Ozon {resp.status}: {err}"})
            except Exception as e:
                results.append({"vendor_code": vc, "status": "error", "error": str(e)})

    return results