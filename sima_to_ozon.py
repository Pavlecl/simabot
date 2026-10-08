"""
sima_to_ozon.py — заливка новых карточек на Ozon по списку артикулов Сима-Ленд.

Независимый инструмент. НЕ переиспользует _build_wb_ozon_draft/Excel-роундтрип
из content_sync.py (старая логика WB → Ozon с автоугадыванием категории по
тексту) — там и была причина вечных блокировок на «Честном знаке»: код жёстко
предполагал, что атрибут маркировки всегда Boolean, а Ozon для многих категорий
делает его атрибутом-справочником (Option), и особый случай с дефолтом
«false» просто не срабатывал — поле оставалось пустым и блокирующим всегда,
независимо от реального товара.

Любой обязательный Boolean-атрибут без данных дефолтится в false без
блокировки отправки — без привязки к тому, что это «Честный знак»
конкретно. Если Ozon всё же отклонит карточку — это вернётся как обычная
ошибка импорта, по месту.

Обогащение данными WB: та же карточка почти всегда уже заведена на WB
(кеш WbProductCache, который наполняет страница /content-sync), а там
данные часто качественнее, чем у Сима-Ленд — лучше фото, описание с
SEO-ключами, и, что важно, верный код ТН ВЭД (у Сима-Ленд он регулярно
неверный — проверено пользователем вручную). Если карточка найдена в
кеше WB — её данные перекрывают данные Сима-Ленд (включая атрибуты, см.
_merge_wb_into_draft), а категория Ozon пытается подобраться автоматически
по категории WB через уже проверенный _ozon_search_category (та же
функция, что раньше работала в WB→Ozon — рабочая часть, не трогаем).
Подбор всегда можно переопределить вручную в таблице — ничего не
блокирует отправку, если автоподбор ошибся или не нашёл совпадения.

Переиспользует из content_sync.py только общие, проверенные Ozon-утилиты:
дерево категорий, поиск категории по тексту, резолвинг словарных
атрибутов, конвертацию boolean, заголовки аккаунта.

offer_id на Ozon = артикул (sid) Сима-Ленд — конвенция уже используется в
stock_broadcast (см. stock_broadcast/__init__.py) для трансляции остатков,
поэтому проверка «уже есть на Ozon» — это просто пересечение множеств по
offer_id, без штрихкодов и прочей эвристики.
"""
from __future__ import annotations

import asyncio
import json as _json
import re as _re
from typing import Optional

import aiohttp

from content_sync import (
    _ozon_headers_for_account,
    _load_ozon_cat_pairs,
    _ozon_category_attrs,
    _ozon_search_category,
    _find_ozon_dict_value,
    _to_ozon_boolean,
    _OZON_VALID_VAT_RATES,
)

WB_IMG_PROXY = "https://simacontrol.ru/api/img-proxy?url="

SIMA_BASE = "https://www.sima-land.ru/api/v3"
SIMA_EXPAND = "stocks,barcodes,description,attrs,photos,trademark,country"
SIMA_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
SIMA_HEADERS = {"User-Agent": SIMA_UA, "Accept": "application/json",
                "Accept-Language": "ru-RU,ru;q=0.9"}
SIMA_RETRY_WAITS = [3, 8, 15]
SIMA_BATCH = 50


# ─────────────────────────────────────────────────
# Сима-Ленд: получение товаров по списку артикулов
# ─────────────────────────────────────────────────

async def _fetch_sima_chunk(session: aiohttp.ClientSession, sids: list[str]) -> list[dict]:
    url = (f"{SIMA_BASE}/item/?sid={','.join(sids)}"
           f"&expand={SIMA_EXPAND}&per-page={max(len(sids), 1)}")
    last_err = None
    for wait in [0] + SIMA_RETRY_WAITS:
        if wait:
            await asyncio.sleep(wait)
        try:
            async with session.get(
                url, headers=SIMA_HEADERS, timeout=aiohttp.ClientTimeout(total=30),
            ) as r:
                if r.status == 200:
                    data = await r.json(content_type=None)
                    return data.get("items", []) or []
                last_err = f"HTTP {r.status}"
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            last_err = str(e)
    raise RuntimeError(last_err or "неизвестная ошибка")


def _strip_html(text: str) -> str:
    text = _re.sub(r"<[^>]+>", " ", text or "")
    text = _re.sub(r"\s+", " ", text).strip()
    return text


def _sima_images(raw: dict) -> list[str]:
    photos = raw.get("photos") or []
    urls = [f"{p['url_part']}700.jpg?v={p['version']}" for p in photos if p.get("url_part")]
    if urls:
        return urls[:15]  # максимум Ozon для images в /v3/product/import
    if raw.get("photoUrl"):
        return [raw["photoUrl"]]
    return []


def _sima_barcode(raw: dict) -> tuple[str, list[str]]:
    """Штрихкоды, начинающиеся с 29, — внутренние (не публичный EAN),
    та же конвенция, что и для WB-штрихкодов в content_sync.py."""
    codes = [str(b) for b in (raw.get("barcodes") or []) if b]
    public = [b for b in codes if not b.startswith("29") and len(b) in (8, 12, 13, 14)]
    if public:
        return public[0], [c for c in codes if c != public[0]]
    return "", codes


def _build_sima_draft(raw: dict) -> dict:
    sid = str(raw.get("sid") or "")
    warnings: list[str] = []

    images = _sima_images(raw)
    if not images:
        warnings.append("нет фото")

    barcode, extra_barcodes = _sima_barcode(raw)
    if not barcode:
        warnings.append("нет публичного штрихкода (только внутренние 29xxx)" if extra_barcodes else "нет штрихкода")

    def _mm(key: str) -> int:
        try:
            return int(round(float(raw.get(key) or 0) * 10))
        except (TypeError, ValueError):
            return 0

    depth, width, height = _mm("box_depth"), _mm("box_width"), _mm("box_height")
    dims_defaulted = not (depth and width and height)
    if dims_defaulted:
        depth, width, height = depth or 100, width or 100, height or 100
        warnings.append("габариты упаковки не определены — установлены 100×100×100 мм по умолчанию, проверьте")

    try:
        weight = int(float(raw.get("weight") or 0))
    except (TypeError, ValueError):
        weight = 0
    weight_defaulted = weight <= 0
    if weight_defaulted:
        weight = 500
        warnings.append("вес не определён — установлено 500 г по умолчанию, проверьте")

    attrs_raw: dict[str, str] = {}
    for a in (raw.get("attrs") or []):
        name = (a.get("attr_name") or "").strip().lower()
        val = a.get("value")
        if name and val not in (None, ""):
            attrs_raw[name] = str(val)

    trademark = (raw.get("trademark") or {}).get("name") or ""
    name = raw.get("name") or sid

    warnings.append("цена не определена — установлена 10000 ₽ по умолчанию, скорректируйте перед отправкой")

    return {
        "sid": sid,
        "name": name,
        "description": _strip_html(raw.get("description") or "")[:4000],
        "images": images,
        "barcode": barcode,
        "extra_barcodes": extra_barcodes,
        "price": "10000",
        "vat": "0",
        "depth": depth, "width": width, "height": height, "weight": weight,
        "trademark": trademark,
        "attrs_raw": attrs_raw,
        "description_category_id": 0,
        "type_id": 0,
        "category_name": "",
        "cat_query": name,  # запрос для автоподбора категории, может быть заменён WB-категорией
        "wb_matched": False,
        "warnings": warnings,
    }


# ─────────────────────────────────────────────────
# Обогащение данными WB (кеш WbProductCache с /content-sync)
# ─────────────────────────────────────────────────

async def _fetch_wb_cache_map(sids: list[str]) -> dict:
    from database import AsyncSessionLocal, WbProductCache
    from sqlalchemy import select
    if not sids:
        return {}
    async with AsyncSessionLocal() as db:
        r = await db.execute(select(WbProductCache).where(WbProductCache.vendor_code.in_(sids)))
        return {p.vendor_code: p for p in r.scalars().all()}


def _merge_wb_into_draft(d: dict, wb) -> dict:
    """WB-данные перекрывают Сима-Ленд там, где они есть — включая атрибуты
    (в т.ч. ТН ВЭД, который у Сима-Ленд часто неверный)."""
    if wb is None:
        d["warnings"].append("ℹ карточка не найдена в кеше WB (/content-sync) — использованы только данные Сима-Ленд")
        return d

    try:
        wb_images = _json.loads(wb.images_json or "[]")
    except ValueError:
        wb_images = []
    if wb_images:
        d["images"] = [f"{WB_IMG_PROXY}{u}" for u in wb_images[:15] if u]

    if wb.description and wb.description.strip():
        d["description"] = wb.description.strip()[:4000]

    if wb.name and wb.name.strip():
        d["name"] = wb.name.strip()

    try:
        wb_attrs_list = _json.loads(wb.attributes_json or "[]")
    except ValueError:
        wb_attrs_list = []
    wb_attrs = {a["name"].strip().lower(): str(a.get("value", ""))
                for a in wb_attrs_list if a.get("name") and a.get("value") not in (None, "")}
    # WB называет поле то «ТНВЭД», то «Код ТН ВЭД» в зависимости от категории —
    # нормализуем под один ключ "тнвэд", чтобы матчинг в get_category_attrs_with_draft
    # не зависел от конкретного написания.
    for key, val in list(wb_attrs.items()):
        if "тнвэд" in key.replace(" ", ""):
            wb_attrs["тнвэд"] = val
            break
    d["attrs_raw"].update(wb_attrs)  # WB побеждает при совпадении ключа

    if wb.brand and wb.brand.strip():
        d["trademark"] = wb.brand.strip()

    d["cat_query"] = " ".join(filter(None, [wb.subject_name, wb.name])) or d["cat_query"]
    d["wb_matched"] = True
    d["warnings"].append("✓ обогащено данными WB (фото, описание, атрибуты — включая ТН ВЭД)")
    return d


async def _auto_match_category(session: aiohttp.ClientSession, headers: dict, query: str) -> tuple[int, int, str]:
    desc_cat_id, type_id = await _ozon_search_category(session, headers, query)
    if not desc_cat_id:
        return 0, 0, ""
    pairs = await _load_ozon_cat_pairs(session, headers)
    name = ""
    for dc, ti, nm in pairs:
        if dc == desc_cat_id and ti == type_id:
            name = nm
            break
    return desc_cat_id, type_id, name


# ─────────────────────────────────────────────────
# Ozon: проверка существующих offer_id (без выкачки всего каталога)
# ─────────────────────────────────────────────────

async def _ozon_offer_ids_subset(session: aiohttp.ClientSession, headers: dict, sids: list[str]) -> set[str]:
    found: set[str] = set()
    for i in range(0, len(sids), 500):
        chunk = sids[i:i + 500]
        if not chunk:
            continue
        async with session.post(
            "https://api-seller.ozon.ru/v3/product/list",
            headers=headers,
            json={"filter": {"offer_id": chunk, "visibility": "ALL"}, "limit": 1000},
        ) as resp:
            data = await resp.json(content_type=None)
        for it in (data.get("result", {}) or {}).get("items", []) or []:
            if it.get("offer_id"):
                found.add(str(it["offer_id"]))
    return found


# ─────────────────────────────────────────────────
# Поиск по списку товаров Сима-Ленд + проверка дублей на Ozon
# ─────────────────────────────────────────────────

async def lookup_sima_to_ozon(ozon_account_id: int, raw_sids: list[str]) -> dict:
    headers = await _ozon_headers_for_account(ozon_account_id)

    clean: list[str] = []
    invalid: list[str] = []
    seen: set[str] = set()
    for s in raw_sids:
        s = str(s).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        if s.isdigit() and 0 < int(s) <= 2147483647:
            clean.append(s)
        else:
            invalid.append(s)

    drafts: list[dict] = []
    not_found: list[str] = []
    failed: list[dict] = []

    async with aiohttp.ClientSession() as session:
        exists = await _ozon_offer_ids_subset(session, headers, clean)
        to_fetch = [s for s in clean if s not in exists]

        wb_cache = await _fetch_wb_cache_map(to_fetch)

        sem = asyncio.Semaphore(4)

        async def _one(part: list[str]) -> None:
            async with sem:
                try:
                    items = await _fetch_sima_chunk(session, part)
                except Exception as e:  # noqa: BLE001
                    failed.append({"sids": part, "error": str(e)})
                    return
                got = set()
                for raw in items:
                    sid = str(raw.get("sid") or "")
                    got.add(sid)
                    d = _build_sima_draft(raw)
                    _merge_wb_into_draft(d, wb_cache.get(sid))
                    drafts.append(d)
                for s in part:
                    if s not in got:
                        not_found.append(s)

        await asyncio.gather(*[
            _one(to_fetch[i:i + SIMA_BATCH]) for i in range(0, len(to_fetch), SIMA_BATCH)
        ])

        # Автоподбор категории Ozon — по категории WB, если карточка найдена
        # на WB (query уже заменён на subject_name+name в _merge_wb_into_draft),
        # иначе по названию товара Сима-Ленд. Всегда можно переопределить
        # вручную в таблице — ничего не блокирует, если подбор ошибся.
        cat_sem = asyncio.Semaphore(6)

        async def _match_one(d: dict) -> None:
            async with cat_sem:
                try:
                    desc_cat_id, type_id, name = await _auto_match_category(session, headers, d["cat_query"])
                except Exception as e:  # noqa: BLE001
                    print(f"[sto-catmatch] {d['sid']}: {e}", flush=True)
                    return
                if desc_cat_id:
                    d["description_category_id"] = desc_cat_id
                    d["type_id"] = type_id
                    d["category_name"] = name
                    d["warnings"].append(f"🏷 категория подобрана автоматически: «{name}» — проверьте перед отправкой")

        await asyncio.gather(*[_match_one(d) for d in drafts])

    order = {s: i for i, s in enumerate(clean)}
    drafts.sort(key=lambda d: order.get(d["sid"], 0))

    return {
        "exists": sorted(exists, key=lambda s: order.get(s, 0)),
        "invalid": invalid,
        "not_found": not_found,
        "failed": failed,
        "drafts": drafts,
    }


# ─────────────────────────────────────────────────
# Поиск категории Ozon (ручной выбор, без автоподстановки)
# ─────────────────────────────────────────────────

async def search_ozon_categories(ozon_account_id: int, query: str, limit: int = 20) -> list[dict]:
    headers = await _ozon_headers_for_account(ozon_account_id)
    q = (query or "").strip().lower()
    if not q:
        return []
    words = [w for w in _re.split(r"[\s/,-]+", q) if w]
    if not words:
        return []

    async with aiohttp.ClientSession() as session:
        pairs = await _load_ozon_cat_pairs(session, headers)

    scored = []
    for desc_cat, type_id, name in pairs:
        hits = sum(1 for w in words if w in name)
        if not hits:
            continue
        score = hits + (2 if name.startswith(words[0]) else 0) - 0.001 * len(name)
        scored.append((score, desc_cat, type_id, name))
    scored.sort(key=lambda x: -x[0])
    return [{"description_category_id": dc, "type_id": ti, "name": nm}
            for _, dc, ti, nm in scored[:limit]]


async def get_category_attrs_with_draft(
    ozon_account_id: int, description_category_id: int, type_id: int,
    attrs_raw: dict, trademark: str, fallback_name: str,
) -> list[dict]:
    """Атрибуты выбранной категории Ozon, предзаполненные по данным Сима-Ленд
    там, где имя атрибута совпадает с именем характеристики товара.
    Любой обязательный Boolean без данных дефолтится в false — без
    привязки к конкретному названию атрибута (в отличие от старой логики,
    где это было завязано на эвристику «это атрибут маркировки?» и ломалось,
    когда Ozon делал такой атрибут справочником, а не Boolean)."""
    headers = await _ozon_headers_for_account(ozon_account_id)
    async with aiohttp.ClientSession() as session:
        cat_attrs = await _ozon_category_attrs(
            session, headers, description_category_id, type_id, required_only=False)

    out = []
    for attr in cat_attrs:
        attr_id = attr["id"]
        name = attr.get("name", "")
        name_l = name.lower()
        dict_type = attr.get("attribute_type", "None")
        val_type = attr.get("type", "String")
        is_req = bool(attr.get("is_required"))

        if "бренд" in name_l:
            val = trademark or attrs_raw.get(name_l, "") or "Нет бренда"
        elif "тн вэд" in name_l:
            # WB (нормализовано в "тнвэд" при мёрдже, см. _merge_wb_into_draft) —
            # в приоритете: у Сима-Ленд ("tnved") этот код регулярно неверный,
            # пользователь проверял вручную.
            val = attrs_raw.get("тнвэд") or attrs_raw.get("tnved") or attrs_raw.get(name_l, "")
        else:
            val = attrs_raw.get(name_l, "")

        if dict_type in ("Option", "Tree"):
            value_text = val
        elif val_type == "Boolean":
            value_text = _to_ozon_boolean(val) or ("false" if is_req else "")
        elif val_type in ("Integer", "Float", "Decimal"):
            nums = _re.findall(r"\d+(?:\.\d+)?", val)
            value_text = nums[0] if nums else ""
        elif val_type == "URL":
            value_text = val if val.startswith(("http://", "https://")) else ""
        elif val:
            value_text = val
        elif is_req:
            value_text = fallback_name
        else:
            value_text = ""

        out.append({
            "id": attr_id, "name": name, "attribute_type": dict_type,
            "val_type": val_type, "is_required": is_req, "value_text": value_text,
        })
    return out


# ─────────────────────────────────────────────────
# Отправка на Ozon
# ─────────────────────────────────────────────────

_sima_to_ozon_import_status: dict = {"running": False, "total": 0, "results": {}, "error": ""}


def get_sima_to_ozon_import_status() -> dict:
    return dict(_sima_to_ozon_import_status)


async def _poll_import_task(session: aiohttp.ClientSession, headers: dict, task_id: int) -> dict:
    try:
        async with session.post(
            "https://api-seller.ozon.ru/v1/product/import/info",
            headers=headers, json={"task_id": task_id},
        ) as resp:
            data = await resp.json(content_type=None)
        items = data.get("result", {}).get("items", []) or data.get("items", [])
        out = {}
        for it in items:
            offer_id = it.get("offer_id")
            errors = it.get("errors") or []
            error_text = "; ".join(e.get("description") or e.get("message") or e.get("code", "") for e in errors)
            out[offer_id] = {"status": it.get("status"), "error_text": error_text}
        return out
    except Exception as e:  # noqa: BLE001
        print(f"[sto-import] poll error task_id={task_id}: {e}", flush=True)
        return {}


def _validate_row(row: dict) -> list[str]:
    errors = []
    if not row.get("name"):
        errors.append("не заполнено название")
    if not row.get("description_category_id") or not row.get("type_id"):
        errors.append("не выбрана категория Ozon")
    if not row.get("price"):
        errors.append("не заполнена цена")
    if not row.get("images"):
        errors.append("нет ни одного фото")
    for k, label in [("depth", "глубина"), ("width", "ширина"), ("height", "высота"), ("weight", "вес")]:
        if not row.get(k):
            errors.append(f"не заполнен(а) {label}")
    vat_text = str(row.get("vat") or "0")
    try:
        vat_num = float(vat_text.replace(",", "."))
        if vat_num > 1:
            vat_num = round(vat_num / 100, 2)
        if not any(abs(vat_num - r) < 0.001 for r in _OZON_VALID_VAT_RATES):
            errors.append(f"ставка НДС {vat_text!r} не поддерживается Ozon (0, 5, 7, 10, 20%)")
    except ValueError:
        errors.append(f"НДС не число: {vat_text!r}")
    return errors


async def submit_sima_to_ozon_import(ozon_account_id: int, rows: list[dict]) -> None:
    global _sima_to_ozon_import_status
    results: dict[str, dict] = {}
    valid_rows = []
    for row in rows:
        errs = _validate_row(row)
        if errs:
            results[row["sid"]] = {"status": "error", "message": "; ".join(errs)}
        else:
            results[row["sid"]] = {"status": "pending", "message": ""}
            valid_rows.append(row)

    _sima_to_ozon_import_status = {"running": True, "total": len(rows), "results": dict(results), "error": ""}

    if not valid_rows:
        _sima_to_ozon_import_status = {"running": False, "total": len(rows), "results": results, "error": ""}
        return

    try:
        headers = await _ozon_headers_for_account(ozon_account_id)
    except Exception as e:  # noqa: BLE001
        _sima_to_ozon_import_status = {"running": False, "total": len(rows), "results": results, "error": str(e)}
        return

    dict_cache: dict[tuple, Optional[int]] = {}
    pending_tasks: list[dict] = []

    async with aiohttp.ClientSession() as session:

        async def _resolve_dict(attr_id: int, desc_cat_id: int, text: str) -> Optional[int]:
            key = (attr_id, desc_cat_id, text.lower())
            if key not in dict_cache:
                dict_cache[key] = await _find_ozon_dict_value(session, headers, attr_id, desc_cat_id, text)
            return dict_cache[key]

        items_to_send: list[tuple[str, dict]] = []
        for row in valid_rows:
            desc_cat_id = row.get("description_category_id", 0)
            ozon_attrs = []
            notes = []
            for a in (row.get("attributes") or []):
                text = (a.get("value_text") or "").strip()
                if not text:
                    continue
                if a.get("attribute_type") in ("Option", "Tree"):
                    dict_val_id = await _resolve_dict(a["id"], desc_cat_id, text)
                    if dict_val_id:
                        ozon_attrs.append({"id": a["id"], "complex_id": 0,
                                            "values": [{"dictionary_value_id": dict_val_id, "value": text}]})
                    else:
                        notes.append(f"атрибут «{a.get('name', a['id'])}»: значение «{text}» не найдено в справочнике Ozon, не отправлено")
                elif a.get("val_type") == "Boolean":
                    bool_val = _to_ozon_boolean(text)
                    if bool_val:
                        ozon_attrs.append({"id": a["id"], "complex_id": 0, "values": [{"value": bool_val}]})
                else:
                    ozon_attrs.append({"id": a["id"], "complex_id": 0, "values": [{"value": text[:500]}]})

            vat_text = str(row.get("vat") or "0").replace(",", ".")
            vat_num = float(vat_text)
            if vat_num > 1:
                vat_num = round(vat_num / 100, 2)

            item = {
                "offer_id":                row["sid"],
                "name":                    (row.get("name") or row["sid"])[:500],
                "description":             (row.get("description") or "")[:10000],
                "description_category_id": desc_cat_id,
                "type_id":                 row.get("type_id", 0),
                "price":                   str(row.get("price") or "0"),
                "currency_code":           "RUB",
                "vat":                     str(vat_num),
                "images":                  row.get("images") or [],
                "depth":                   row.get("depth", 0),
                "width":                   row.get("width", 0),
                "height":                  row.get("height", 0),
                "dimension_unit":          "mm",
                "weight":                  row.get("weight", 0),
                "weight_unit":             "g",
                "attributes":              ozon_attrs,
            }
            if row.get("barcode"):
                item["barcode"] = row["barcode"]

            if notes:
                results[row["sid"]]["message"] = "; ".join(notes)
            items_to_send.append((row["sid"], item))
            _sima_to_ozon_import_status = {"running": True, "total": len(rows), "results": dict(results), "error": ""}

        for i in range(0, len(items_to_send), 100):
            chunk = items_to_send[i:i + 100]
            try:
                async with session.post(
                    "https://api-seller.ozon.ru/v3/product/import",
                    headers=headers, json={"items": [it for _, it in chunk]},
                ) as resp:
                    resp_data = await resp.json(content_type=None)
                print(f"[sto-import] submit chunk_start={i} status={resp.status} resp={str(resp_data)[:400]}", flush=True)

                if resp.status == 429 or "item_limit_exceeded" in str(resp_data).lower():
                    retry_after = resp.headers.get("Item-Retry-After", "")
                    msg = "лимит Ozon исчерпан, попробуйте позже" + (f" (через {retry_after} мин)" if retry_after else "")
                    for vc, _ in items_to_send[i:]:
                        results[vc] = {"status": "error", "message": msg}
                    _sima_to_ozon_import_status = {"running": True, "total": len(rows), "results": dict(results), "error": ""}
                    break

                if resp.status != 200:
                    err = resp_data.get("message") or str(resp_data)[:300]
                    for vc, _ in chunk:
                        results[vc] = {"status": "error", "message": f"Ozon {resp.status}: {err}"}
                    _sima_to_ozon_import_status = {"running": True, "total": len(rows), "results": dict(results), "error": ""}
                    continue

                task_id = resp_data.get("result", {}).get("task_id")
                if task_id:
                    pending_tasks.append({"task_id": task_id, "sids": [vc for vc, _ in chunk]})
                else:
                    for vc, _ in chunk:
                        results[vc] = {"status": "error", "message": "Ozon не вернул task_id"}
            except Exception as e:  # noqa: BLE001
                for vc, _ in chunk:
                    results[vc] = {"status": "error", "message": str(e)}
                _sima_to_ozon_import_status = {"running": True, "total": len(rows), "results": dict(results), "error": ""}

        deadline = asyncio.get_event_loop().time() + 120
        while pending_tasks and asyncio.get_event_loop().time() < deadline:
            still_pending = []
            for task in pending_tasks:
                statuses = await _poll_import_task(session, headers, task["task_id"])
                remaining = []
                for sid in task["sids"]:
                    st = statuses.get(sid)
                    if not st or st["status"] == "pending":
                        remaining.append(sid)
                        continue
                    if st["status"] == "imported":
                        notes = [n for n in (results.get(sid, {}).get("message", ""), st["error_text"]) if n]
                        results[sid] = {"status": "ok", "message": "; ".join(notes)}
                    else:
                        err_text = st["error_text"] or "Ozon отклонил карточку"
                        results[sid] = {"status": "error", "message": err_text}
                if remaining:
                    still_pending.append({"task_id": task["task_id"], "sids": remaining})
            pending_tasks = still_pending
            _sima_to_ozon_import_status = {"running": True, "total": len(rows), "results": dict(results), "error": ""}
            if pending_tasks:
                await asyncio.sleep(4)

        for task in pending_tasks:
            for sid in task["sids"]:
                results[sid] = {"status": "unknown", "message": "Ozon не подтвердил статус за 2 мин — проверьте в кабинете Ozon"}

    _sima_to_ozon_import_status = {"running": False, "total": len(rows), "results": results, "error": ""}
