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


async def _wb_get_stats(wb_api_key: str, nm_ids: list[int]) -> Optional[dict[int, dict]]:
    """Заказы за неделю и месяц по nm_id через WB nm-report.
    Возвращает None если API недоступен (не тот тип токена).
    """
    if not nm_ids:
        return {}

    today = datetime.now()
    stats_week: dict[int, int]  = {}
    stats_month: dict[int, int] = {}
    headers = {"Authorization": wb_api_key, "Content-Type": "application/json"}
    url = "https://seller-analytics-api.wildberries.ru/api/v2/nm-report/detail"

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
                        url, headers=headers,
                        json={
                            "nmIDs": nm_ids,
                            "timezone": "Europe/Moscow",
                            "period": {"begin": begin, "end": end},
                            "orderBy": {"field": "ordersCount", "mode": "desc"},
                            "page": page,
                        },
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        if resp.status in (401, 403, 404):
                            print(f"[wb-stats] API недоступен: {resp.status} (нужен токен типа Analytics)", flush=True)
                            return None
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
                    return None

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
    stats: Optional[dict[int, dict]] = None
    try:
        stats = await _wb_get_stats(wb_api_key, nm_ids)
    except Exception as e:
        print(f"[cross] stats failed: {e}", flush=True)

    result = []
    for p in missing:
        images = _json.loads(p.images_json or "[]")
        if stats is None:
            orders_week, orders_month = None, None
        else:
            nm_stat = stats.get(p.nm_id, {"week": 0, "month": 0}) if p.nm_id else {"week": 0, "month": 0}
            orders_week  = nm_stat["week"]
            orders_month = nm_stat["month"]
        result.append({
            "vendor_code":   p.vendor_code,
            "nm_id":         p.nm_id,
            "name":          p.name or "",
            "image":         images[0] if images else "",
            "subject_name":  getattr(p, "subject_name", None) or "",
            "orders_week":   orders_week,
            "orders_month":  orders_month,
        })

    result.sort(key=lambda x: (x["orders_month"] or 0), reverse=True)
    return result


# Кэш дерева категорий Ozon (обновляется раз в сессию)
_ozon_cat_pairs: list[tuple[int, int, str]] = []


async def _load_ozon_cat_pairs(session: aiohttp.ClientSession, headers: dict) -> list[tuple[int, int, str]]:
    """Загружает и кэширует плоский список (desc_cat_id, type_id, name) из дерева Ozon."""
    global _ozon_cat_pairs
    if _ozon_cat_pairs:
        return _ozon_cat_pairs

    try:
        async with session.post(
            "https://api-seller.ozon.ru/v1/description-category/tree",
            headers=headers,
            json={},
        ) as resp:
            tree = await resp.json(content_type=None)
    except Exception as e:
        print(f"[cat-tree] {e}", flush=True)
        return []

    pairs: list[tuple[int, int, str]] = []

    def traverse(nodes: list, parent_desc_cat: int = 0) -> None:
        for n in nodes:
            desc_cat = n.get("description_category_id") or parent_desc_cat
            type_id  = n.get("type_id", 0)
            name     = (n.get("category_name") or n.get("type_name") or "").lower()
            children = n.get("children") or []
            if type_id and not children and not n.get("disabled"):
                pairs.append((desc_cat, type_id, name))
            if children:
                traverse(children, desc_cat)

    traverse(tree.get("result", []))
    _ozon_cat_pairs = pairs
    print(f"[cat-tree] loaded {len(pairs)} leaf categories", flush=True)
    return pairs


async def _ozon_search_category(session: aiohttp.ClientSession, headers: dict, name: str) -> tuple[int, int]:
    """Ищет (description_category_id, type_id) по ключевым словам из названия товара."""
    import re as _re
    pairs = await _load_ozon_cat_pairs(session, headers)
    if not pairs:
        return 0, 0

    # Токенизация: разбиваем по пробелам и дефисам, минимум 3 символа
    raw_words = name.lower().split()
    tokens: set[str] = set()
    for w in raw_words:
        tokens.add(w)
        for part in _re.split(r'[-/]', w):
            if len(part) >= 3:
                tokens.add(part)

    # 1. Префиксный матч: общий префикс ≥ 4 символа → считается совпадением.
    #    effective = raw * matched / total_cat_words * (2 если первый токен совпал) - штраф за длину
    #    Бонус ×2 за совпадение первого (главного) токена гарантирует, что
    #    "крючок" бьёт "настенные часы" для запроса "Крючки настенные".
    first_token = next((w for w in name.lower().split() if len(w) >= 3), None)
    best_score, best = 0.0, (0, 0)
    for desc_cat, type_id, cat_name in pairs:
        cat_words_list = cat_name.split()
        raw = 0.0
        matched_cat_words = 0
        first_token_matched = False
        for cat_word in cat_words_list:
            if len(cat_word) < 3:
                continue
            for token in tokens:
                if len(token) < 3:
                    continue
                # Длина общего (совпадающего) префикса
                common = 0
                for i in range(min(len(token), len(cat_word))):
                    if token[i] == cat_word[i]:
                        common += 1
                    else:
                        break
                if common >= 4:
                    raw += common
                    matched_cat_words += 1
                    if token == first_token:
                        first_token_matched = True
                    break
        if raw > 0 and matched_cat_words > 0:
            effective = raw * matched_cat_words / len(cat_words_list)
            if first_token_matched:
                effective *= 3.0
            effective -= 0.001 * len(cat_name)  # тайбрейкер: предпочитаем короткие имена
            if effective > best_score:
                best_score = effective
                best = (desc_cat, type_id)

    if best_score >= 4.0:
        return best

    # 2. Substring: каждый токен ищем внутри имени категории
    for token in sorted(tokens, key=len, reverse=True):  # длинные слова — приоритет
        if len(token) < 4:
            continue
        for desc_cat, type_id, cat_name in pairs:
            if token in cat_name:
                return desc_cat, type_id

    return 0, 0


async def _find_ozon_dict_value(
    session: aiohttp.ClientSession, headers: dict,
    attr_id: int, desc_cat_id: int, search_val: str,
) -> Optional[int]:
    """Ищет dictionary_value_id в словаре атрибута Ozon по строке.
    Сначала пробует эндпоинт поиска, иначе перебирает первые страницы."""
    # Пробуем search-эндпоинт (если существует)
    try:
        async with session.post(
            "https://api-seller.ozon.ru/v3/category/attribute/values/search",
            headers=headers,
            json={"attribute_id": attr_id, "category_id": desc_cat_id, "language": "RU", "value": search_val[:255]},
        ) as resp:
            if resp.status == 200:
                data = await resp.json(content_type=None)
                results = data.get("result", [])
                if results:
                    return results[0]["id"]
    except Exception:
        pass

    # Fallback: перебираем словарь постранично (max 5 страниц по 1000)
    search_lower = search_val.lower().strip()
    last_id = 0
    for _ in range(5):
        try:
            async with session.post(
                "https://api-seller.ozon.ru/v2/category/attribute/values",
                headers=headers,
                json={"attribute_id": attr_id, "category_id": desc_cat_id,
                      "language": "RU", "limit": 1000, "last_value_id": last_id},
            ) as resp:
                data = await resp.json(content_type=None)
            items = data.get("result", [])
            for item in items:
                if item.get("value", "").lower().strip() == search_lower:
                    return item["id"]
            if not data.get("has_next"):
                break
            last_id = items[-1]["id"] if items else 0
        except Exception:
            break
    return None


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

            # Определяем категорию: сначала по subjectName WB, иначе по названию
            cat_query = getattr(p, "subject_name", None) or p.name or vc
            desc_cat_id, type_id = await _ozon_search_category(session, headers, cat_query)
            if not desc_cat_id:
                results.append({"vendor_code": vc, "status": "error", "error": "Не удалось определить категорию Ozon"})
                continue

            # Заполняем атрибуты
            import re as _re
            req_attrs  = await _ozon_required_attrs(session, headers, desc_cat_id, type_id)
            wb_chars   = _json.loads(p.attributes_json or "[]")
            wb_attrs   = {a["name"].lower(): str(a.get("value", "")) for a in wb_chars}
            wb_brand   = getattr(p, "brand", None) or ""
            wb_desc    = p.description or ""
            ozon_attrs = []
            for attr in req_attrs:
                attr_id   = attr["id"]
                attr_name = attr.get("name", "").lower()
                dict_type = attr.get("attribute_type", "None")  # "None", "Option", "Tree"
                val_type  = attr.get("type", "String")          # "String", "Integer", "Float"

                # Специальные источники по типу атрибута
                if "бренд" in attr_name:
                    wb_val = wb_brand or wb_attrs.get(attr_name, "")
                elif "тн вэд" in attr_name:
                    wb_val = wb_attrs.get("код тн вэд") or wb_attrs.get(attr_name, "")
                elif "аннотация" in attr_name or "annotation" in attr_name:
                    wb_val = wb_desc[:200].strip()
                else:
                    wb_val = wb_attrs.get(attr_name, "")

                if dict_type in ("Option", "Tree"):
                    # Словарный атрибут — ищем dictionary_value_id в Ozon
                    if wb_val:
                        dict_val_id = await _find_ozon_dict_value(
                            session, headers, attr_id, desc_cat_id, wb_val
                        )
                        if dict_val_id:
                            ozon_attrs.append({
                                "id": attr_id, "complex_id": 0,
                                "values": [{"dictionary_value_id": dict_val_id, "value": wb_val}],
                            })
                    # Если не нашли — пропускаем (пустое поле лучше неверного)
                    continue

                if val_type in ("Integer", "Float"):
                    nums = _re.findall(r'\d+(?:\.\d+)?', wb_val)
                    num  = nums[0] if nums else "0"
                    ozon_attrs.append({"id": attr_id, "complex_id": 0, "values": [{"value": num}]})
                else:
                    val = wb_val or (p.name or vc)
                    ozon_attrs.append({"id": attr_id, "complex_id": 0, "values": [{"value": str(val)[:500]}]})

            # Размеры: берём из WB dimensions_json (мм) → Ozon (мм), 1:1
            # Fallback: ищем в характеристиках в см, умножаем на 10
            wb_dims = _json.loads(getattr(p, "dimensions_json", None) or "{}")

            # WB dimensions_json хранит значения в СМ → конвертируем в мм (×10) для Ozon API
            def _from_dims(key: str) -> Optional[int]:
                v = wb_dims.get(key)
                if v and int(v) > 0:
                    return max(10, int(v) * 10)
                return None

            def _from_attrs_cm(keys: list[str]) -> Optional[int]:
                for k in keys:
                    v = wb_attrs.get(k, "")
                    nums = _re.findall(r'\d+(?:\.\d+)?', v)
                    if nums:
                        mm = int(float(nums[0]) * 10)
                        return max(10, mm)
                return None

            depth  = (_from_dims("length") or _from_attrs_cm(["глубина предмета", "длина предмета", "толщина предмета"]) or 50)
            width  = (_from_dims("width")  or _from_attrs_cm(["ширина предмета", "ширина"]) or 50)
            height = (_from_dims("height") or _from_attrs_cm(["высота предмета", "высота"]) or 50)

            # Вес: сначала из dimensions.weightBrutto (кг → г, только если > 0), иначе из характеристик
            wb_weight_brutto = wb_dims.get("weightBrutto") or 0
            if float(wb_weight_brutto) > 0:
                weight = max(1, int(float(wb_weight_brutto) * 1000))
            else:
                def _weight_to_g(keys: list[str], default_g: int = 500) -> int:
                    for k in keys:
                        v = wb_attrs.get(k, "")
                        nums = _re.findall(r'\d+(?:\.\d+)?', v)
                        if nums:
                            w = float(nums[0])
                            return max(1, int(w * 1000 if w < 100 else w))
                    return default_g
                weight = _weight_to_g(["вес", "вес брутто", "вес нетто", "масса"])

            wb_barcodes = _json.loads(getattr(p, "barcodes_json", None) or "[]")

            # Добавляем аннотацию (id=4191) — не обязательный атрибут, но важный для контента
            wb_annotation = (p.description or "")[:4000].strip()
            if wb_annotation:
                # Убираем дубликат, если аннотация уже добавлена как required
                if not any(a["id"] == 4191 for a in ozon_attrs):
                    ozon_attrs.append({
                        "id": 4191, "complex_id": 0,
                        "values": [{"value": wb_annotation}],
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
                "depth":                   depth,
                "width":                   width,
                "height":                  height,
                "dimension_unit":          "mm",
                "weight":                  weight,
                "weight_unit":             "g",
                "attributes":              ozon_attrs,
            }
            # Штрихкоды WB (29xxx, 69xxx) — внутренние коды WB, Ozon их молча отвергает.
            # Отправляем только штрихкоды с публичными EAN-префиксами (не 29/290).
            public_barcodes = [b for b in wb_barcodes if b and not b.startswith("29") and len(b) in (8, 12, 13, 14)]
            if public_barcodes:
                item["barcodes"] = public_barcodes[:3]

            try:
                async with session.post(
                    "https://api-seller.ozon.ru/v3/product/import",
                    headers=headers,
                    json={"items": [item]},
                ) as resp:
                    resp_data = await resp.json(content_type=None)
                print(f"[ozon-import] {vc} status={resp.status} resp={str(resp_data)[:300]}", flush=True)
                if resp.status == 200:
                    task_id = resp_data.get("result", {}).get("task_id")
                    wb_barcodes_internal = [b for b in wb_barcodes if b and b.startswith("29")]
                    warn = "Штрихкоды WB (29xxx) не переносятся на Ozon — добавьте EAN вручную" if wb_barcodes_internal and not public_barcodes else None
                    results.append({"vendor_code": vc, "status": "ok", "task_id": task_id, "warning": warn})
                else:
                    err = resp_data.get("message") or str(resp_data)[:300]
                    results.append({"vendor_code": vc, "status": "error", "error": f"Ozon {resp.status}: {err}"})
            except Exception as e:
                results.append({"vendor_code": vc, "status": "error", "error": str(e)})

    return results