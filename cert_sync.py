"""
cert_sync.py — перенос разрешительных документов (деклараций и сертификатов)
с карточек WB на Ozon.

Зачем: с 01.10.2026 Ozon снимает с продажи карточки без подтверждённых
документов качества («Не подтверждены документы качества»). На WB те же
документы у продавца уже заведены — нужно просто перенести их номера.

Ключевой факт, выясненный на живом API (а не из документации): **файл
сертификата загружать не нужно**. Ozon сам сверяет номер с государственным
реестром — в ответе метода создания поле FILES даже не фигурирует среди
проверяемых параметров, а созданный по одному номеру документ сразу получает
status_code=approved. Поэтому вся схема сводится к переносу номера, типа и
сроков действия.

Используемые методы Ozon (проверены вживую, актуальны на 10.2026):
  POST /v2/product/certificate/create — создать документ и сразу привязать
       к товарам через params.skus. Отдельный /v1/product/certificate/bind
       не нужен, если SKU известны на момент создания.
  POST /v1/product/certificate/list   — что уже загружено (чтобы не плодить дубли).
  POST /v3/product/list               — offer_id → sku.

Схема запроса create отличается от документации Go-клиентов и от имён полей
в ответе /v1/product/certificate/list — ниже именно рабочий вариант:
  certificate_type      — ЗАГЛАВНЫМИ: DECLARATION, CERTIFICATE_OF_CONFORMITY…
                          (lowercase из /v1/product/certificate/types не принимается)
  accordance_type       — EAEU для документов ЕАЭС/ТС, GOST для «РОСС …»
                          (коды из /v2/product/certificate/accordance-types/list
                          вроде technical_regulations_cu тоже не принимаются)
  certificate_country   — страна регистрации; критично: Ozon проверяет номер
                          по маске, своей для каждой страны. Для «ЕАЭС KG417/…»
                          с country=RU будет «Введите корректный номер», с
                          country=KG тот же номер проходит.
  expired_date          — либо {"date": {...}}, либо {"infinite": true}, но не оба
"""
from __future__ import annotations

import asyncio
import json as _json
import re as _re
from typing import Optional

import aiohttp

from content_sync import _ozon_headers_for_account

OZON_API = "https://api-seller.ozon.ru"

# Тип документа WB (поле documents.items[].type) → certificate_type Ozon.
# Значения WB взяты из спецификации Content API (02-items.yaml, documentsRequest).
WB_TYPE_TO_OZON: dict[int, str] = {
    1: "CERTIFICATE_OF_CONFORMITY",    # Сертификат соответствия
    2: "DECLARATION",                  # Декларация о соответствии
    3: "CERTIFICATE_OF_REGISTRATION",  # Свидетельство о госрегистрации (СГР)
    4: "REGISTRATION_CERTIFICATE",     # РУ на медицинские изделия
    5: "REGISTRATION_CERTIFICATE",     # РУ Республики Беларусь
    9: "REGISTRATION_CERTIFICATE",     # РУ на лекарственные препараты
}

WB_TYPE_NAME: dict[int, str] = {
    1: "Сертификат соответствия",
    2: "Декларация о соответствии",
    3: "Свидетельство о госрегистрации",
    4: "Регистрационное удостоверение",
    5: "Регистрационное удостоверение РБ",
    7: "Регистрация пестицида",
    8: "Регистрация агрохимиката",
    9: "РУ на лекарственный препарат",
}

_EAEU_COUNTRIES = {"RU", "KG", "KZ", "BY", "AM"}


def _country_from_number(number: str) -> str:
    """Страна регистрации документа — двухбуквенный код сразу после префикса
    реестра («ЕАЭС», «ЕАЭС N», «ТС», «РОСС»).

    Важно не перепутать с кодом после «Д-»/«С-»: там страна ИЗГОТОВИТЕЛЯ.
    Например, в «ЕАЭС N RU Д-CN.РА04.В.29932/25» регистрация российская (RU),
    а CN — страна производства. А в «ЕАЭС KG417/055.RU.02.03946» регистрация
    киргизская, хотя дальше в номере встречается RU.

    От страны зависит маска номера на стороне Ozon, поэтому ошибка здесь —
    это отказ «Введите корректный номер» на валидации.
    """
    m = _re.match(r"^\s*(?:ЕАЭС|ТС|РОСС)\s+(?:N\s+)?([A-Z]{2})", (number or "").upper())
    if m and m.group(1) in _EAEU_COUNTRIES:
        return m.group(1)
    return "RU"


def _accordance_from_number(number: str) -> str:
    """«РОСС …» — национальная система (ГОСТ Р), остальное (ЕАЭС/ТС) — EAEU.

    Значения подобраны экспериментально: коды из справочника
    /v2/product/certificate/accordance-types/list (gost, technical_regulations_cu)
    метод создания не принимает, равно как и GOST/GOST_R.
    """
    return "NATIONAL" if (number or "").strip().upper().startswith("РОСС") else "EAEU"


def _parse_wb_date(value: Optional[str]) -> Optional[dict]:
    """'2029-09-23T00:00:00Z' → {'day': 23, 'month': 9, 'year': 2029}."""
    if not value:
        return None
    m = _re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(value))
    if not m:
        return None
    return {"year": int(m.group(1)), "month": int(m.group(2)), "day": int(m.group(3))}


# ─────────────────────────────────────────────────
# Чтение документов WB из кеша
# ─────────────────────────────────────────────────

def _wb_documents(documents_json: Optional[str]) -> list[dict]:
    """Действующие документы карточки WB.

    Берём только те, что WB уже проверил (verdict.status == 1): если документ
    не прошёл проверку на WB, на Ozon он тем более не пройдёт — незачем гонять
    заведомо отказные номера через чужую модерацию.
    """
    try:
        data = _json.loads(documents_json or "{}")
    except ValueError:
        return []
    out = []
    for item in (data.get("items") or []):
        number = (item.get("number") or "").strip()
        if not number:
            continue
        if (item.get("verdict") or {}).get("status") != 1:
            continue
        out.append(item)
    return out


async def _load_wb_cache(offer_ids: Optional[list[str]]) -> dict[str, dict]:
    """{vendor_code: {name, documents: [...]}} из кеша WB (/content-sync)."""
    from database import AsyncSessionLocal, WbProductCache
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        q = select(WbProductCache.vendor_code, WbProductCache.name, WbProductCache.documents_json)
        if offer_ids:
            q = q.where(WbProductCache.vendor_code.in_(offer_ids))
        rows = (await db.execute(q)).all()

    return {
        vc: {"name": name or "", "documents": _wb_documents(docs_json)}
        for vc, name, docs_json in rows
    }


# ─────────────────────────────────────────────────
# Ozon: что уже загружено и какие SKU у товаров
# ─────────────────────────────────────────────────

REJECTION_REASONS = {
    "not_active_in_registry": "документ не активен в реестре",
    "annulled": "документ аннулирован в реестре",
    "incorrect_type": "нужен другой тип документа",
    "not_all_pages": "предоставлены не все страницы",
    "not_in_registry": "документа нет в едином реестре",
    "not_true": "информация не соответствует действительности",
    "not_found": "не найден",
    "archive": "документ в архиве",
    "expired_document": "срок действия истёк",
    "not_match_information": "копия не соответствует указанным данным",
    "not_signed": "нет подписи или печати",
    "not_valid_in_rf": "не действует на территории РФ",
}


async def _ozon_existing_certs(session: aiohttp.ClientSession, headers: dict) -> dict[str, dict]:
    """{НОМЕР в верхнем регистре: {id, status, rejection, comment}} — что уже
    заведено в кабинете.

    Ozon спокойно принимает повторную загрузку того же номера и плодит
    одинаковые записи, так что защита от дублей — только на нашей стороне.
    certificate_id нужен, чтобы доложить недостающие товары к уже
    существующему документу через bind, а не создавать его заново.

    Статус важен не меньше: Ozon сверяет номер с госреестром и может
    отклонить документ (например, not_active_in_registry) даже после
    успешного создания — и товар остаётся без подтверждения. WB при этом
    тот же документ может считать проверенным, так что расхождение надо видеть.
    """
    out: dict[str, dict] = {}
    page = 1
    while page <= 200:
        async with session.post(
            f"{OZON_API}/v1/product/certificate/list",
            headers=headers,
            # page_size этого метода ограничен сотней (при 1000 — 400 Bad Request),
            # в отличие от большинства других списков Ozon
            json={"page": page, "page_size": 100},
        ) as resp:
            if resp.status != 200:
                break
            data = await resp.json(content_type=None)
        result = data.get("result") or {}
        for cert in (result.get("certificates") or []):
            num = (cert.get("certificate_number") or "").strip()
            cid = cert.get("certificate_id")
            if not (num and cid) or num.upper() in out:
                continue
            reason = cert.get("rejection_reason_code") or ""
            out[num.upper()] = {
                "id": int(cid),
                "status": cert.get("status_code") or "",
                "rejection": REJECTION_REASONS.get(reason, reason),
                "comment": cert.get("verification_comment") or "",
            }
        if page >= (result.get("page_count") or 1):
            break
        page += 1
    return out


async def _ozon_cert_skus(session: aiohttp.ClientSession, headers: dict, certificate_id: int) -> set[str]:
    """SKU, уже привязанные к документу — чтобы доложить только недостающие."""
    skus: set[str] = set()
    page = 1
    while page <= 20:
        async with session.post(
            f"{OZON_API}/v1/product/certificate/products/list",
            headers=headers,
            json={"certificate_id": certificate_id, "page": page, "limit": 1000},
        ) as resp:
            if resp.status != 200:
                break
            data = await resp.json(content_type=None)
        items = ((data.get("result") or {}).get("items") or [])
        for it in items:
            if it.get("sku"):
                skus.add(str(it["sku"]))
        if len(items) < 1000:
            break
        page += 1
    return skus


async def _ozon_skus(session: aiohttp.ClientSession, headers: dict, offer_ids: list[str]) -> dict[str, str]:
    """{offer_id: sku}. Привязка документа идёт по SKU, не по offer_id/product_id."""
    out: dict[str, str] = {}
    for i in range(0, len(offer_ids), 500):
        chunk = offer_ids[i:i + 500]
        async with session.post(
            f"{OZON_API}/v3/product/list",
            headers=headers,
            json={"filter": {"offer_id": chunk, "visibility": "ALL"}, "limit": 1000},
        ) as resp:
            if resp.status != 200:
                continue
            data = await resp.json(content_type=None)
        for it in ((data.get("result") or {}).get("items") or []):
            sku = it.get("sku")
            if it.get("offer_id") and sku:
                out[str(it["offer_id"])] = str(sku)
    return out


# ─────────────────────────────────────────────────
# Подготовка плана переноса
# ─────────────────────────────────────────────────

async def lookup_certificates(ozon_account_id: int, raw_offer_ids: Optional[list[str]] = None) -> dict:
    """Сопоставляет документы WB с товарами Ozon и собирает план загрузки.

    Документы группируются по номеру: один документ обычно покрывает десятки
    товаров, и Ozon принимает все их SKU одним запросом — так и дублей меньше,
    и запросов.
    """
    headers = await _ozon_headers_for_account(ozon_account_id)

    requested: list[str] = []
    if raw_offer_ids:
        seen = set()
        for s in raw_offer_ids:
            s = str(s).strip()
            if s and s not in seen:
                seen.add(s)
                requested.append(s)

    wb_cache = await _load_wb_cache(requested or None)

    no_documents: list[dict] = []
    # {number: {type, name, issue_date, expired_date, infinite, offer_ids: [...]}}
    groups: dict[str, dict] = {}

    for vc, info in wb_cache.items():
        docs = info["documents"]
        if not docs:
            no_documents.append({"offer_id": vc, "name": info["name"]})
            continue
        for d in docs:
            number = (d.get("number") or "").strip()
            g = groups.setdefault(number, {
                "number": number,
                "wb_type": d.get("type"),
                "type_name": WB_TYPE_NAME.get(d.get("type"), f"тип {d.get('type')}"),
                "certificate_type": WB_TYPE_TO_OZON.get(d.get("type") or 0, ""),
                "certificate_country": _country_from_number(number),
                "accordance_type": _accordance_from_number(number),
                "issue_date": d.get("startDate"),
                "expire_date": d.get("endDate"),
                "infinite": bool(d.get("isEndless")),
                "offer_ids": [],
            })
            g["offer_ids"].append(vc)

    not_in_wb = [s for s in requested if s not in wb_cache]

    plan: list[dict] = []
    async with aiohttp.ClientSession() as session:
        existing = await _ozon_existing_certs(session, headers)
        all_offer_ids = sorted({vc for g in groups.values() for vc in g["offer_ids"]})
        sku_map = await _ozon_skus(session, headers, all_offer_ids)

        for number, g in groups.items():
            offer_ids = sorted(set(g["offer_ids"]))
            skus = [sku_map[o] for o in offer_ids if o in sku_map]
            missing_on_ozon = [o for o in offer_ids if o not in sku_map]
            found = existing.get(number.upper()) or {}
            certificate_id = found.get("id")

            # Документ уже заведён — но мог быть привязан не ко всем товарам
            # (например, часть карточек появилась на Ozon позже). Догружаем
            # только недостающие SKU через bind, не создавая документ заново.
            if certificate_id and skus:
                bound = await _ozon_cert_skus(session, headers, certificate_id)
                skus_to_add = [s for s in skus if s not in bound]
            else:
                bound = set()
                skus_to_add = skus

            # blocking — не даём отправить вовсе; остальное просто показываем,
            # чтобы «часть товаров ещё не заведена на Ozon» не мешала залить
            # документ для тех, что уже заведены
            issues: list[str] = []
            blocking = False
            if not g["certificate_type"]:
                issues.append(f"тип документа WB ({g['wb_type']}) не поддерживается Ozon")
                blocking = True
            if not skus:
                issues.append("ни один товар не найден на Ozon")
                blocking = True
            if missing_on_ozon and skus:
                issues.append(f"нет на Ozon: {len(missing_on_ozon)} шт.")

            if certificate_id:
                action = "bind" if skus_to_add else "done"
            else:
                action = "create"

            plan.append({
                **g,
                "offer_ids": offer_ids,
                "skus": skus_to_add,
                "products_count": len(skus_to_add),
                "already_bound": len(bound & set(skus)),
                "certificate_id": certificate_id,
                "ozon_status": found.get("status", ""),
                "ozon_rejection": found.get("rejection", ""),
                "ozon_comment": found.get("comment", ""),
                "action": action,
                "issues": issues,
                "blocking": blocking,
            })

    # Сначала то, что требует действия, внутри — крупные документы выше
    plan.sort(key=lambda p: (p["action"] == "done", -p["products_count"]))

    return {
        "plan": plan,
        "no_documents": no_documents,
        "not_in_wb_cache": not_in_wb,
        "totals": {
            "documents": len(plan),
            "create": sum(1 for p in plan if p["action"] == "create" and not p["blocking"]),
            "bind": sum(1 for p in plan if p["action"] == "bind" and not p["blocking"]),
            "done": sum(1 for p in plan if p["action"] == "done"),
            "declined": sum(1 for p in plan if p.get("ozon_status") == "declined"),
            "products_covered": sum(p["products_count"] for p in plan if p["action"] != "done"),
            "no_documents": len(no_documents),
        },
    }


# ─────────────────────────────────────────────────
# Загрузка на Ozon
# ─────────────────────────────────────────────────

_import_status: dict = {"running": False, "total": 0, "done": 0, "results": [], "error": ""}


def get_import_status() -> dict:
    return dict(_import_status)


def _build_create_payload(row: dict) -> dict:
    expired: dict = {}
    if row.get("infinite"):
        expired = {"infinite": True}
    else:
        date = _parse_wb_date(row.get("expire_date"))
        if date:
            expired = {"date": date}
        else:
            # Ozon требует либо срок, либо явную бессрочность — без этого
            # документ не создастся вовсе, а бессрочный вариант безопаснее
            # отказа: срок всё равно перепроверяется Ozon по реестру.
            expired = {"infinite": True}

    params = {
        "certificate_type": row["certificate_type"],
        "accordance_type": row.get("accordance_type") or "EAEU",
        "certificate_country": row.get("certificate_country") or "RU",
        "name": f"{row.get('type_name') or 'Документ'} {row['number']}"[:100],
        "number": row["number"][:100],
        "expired_date": expired,
        "product_type": "UNKNOWN",
        "skus": row["skus"],
    }
    issue = row.get("issue_date")
    if issue:
        params["issue_date"] = issue
    return {"params": params}


async def submit_certificates(ozon_account_id: int, rows: list[dict]) -> None:
    """Создаёт документы на Ozon и привязывает к SKU. Фоновая задача."""
    global _import_status
    _import_status = {"running": True, "total": len(rows), "done": 0, "results": [], "error": ""}

    try:
        headers = await _ozon_headers_for_account(ozon_account_id)
    except Exception as e:  # noqa: BLE001
        _import_status = {"running": False, "total": len(rows), "done": 0, "results": [], "error": str(e)}
        return

    results: list[dict] = []
    async with aiohttp.ClientSession() as session:
        for row in rows:
            number = row.get("number", "")
            if not row.get("skus"):
                results.append({"number": number, "status": "error",
                                "message": "нет SKU для привязки", "products": 0})
                _import_status = {"running": True, "total": len(rows), "done": len(results),
                                  "results": list(results), "error": ""}
                continue

            try:
                if row.get("certificate_id"):
                    # Документ уже заведён — только доприязываем недостающие товары
                    async with session.post(
                        f"{OZON_API}/v1/product/certificate/bind",
                        headers=headers,
                        json={"certificate_id": int(row["certificate_id"]), "skus": row["skus"]},
                    ) as resp:
                        data = await resp.json(content_type=None)
                        http_status = resp.status
                    if http_status == 200 and data.get("result"):
                        results.append({"number": number, "status": "ok",
                                        "message": f"привязано товаров: {len(row['skus'])}",
                                        "products": len(row["skus"])})
                    else:
                        msg = data.get("message") or str(data)[:200]
                        results.append({"number": number, "status": "error",
                                        "message": f"привязка не удалась: {msg}", "products": 0})
                    _import_status = {"running": True, "total": len(rows), "done": len(results),
                                      "results": list(results), "error": ""}
                    await asyncio.sleep(0.3)
                    continue

                async with session.post(
                    f"{OZON_API}/v2/product/certificate/create",
                    headers=headers,
                    json=_build_create_payload(row),
                ) as resp:
                    data = await resp.json(content_type=None)
                    http_status = resp.status

                if http_status != 200:
                    msg = data.get("message") or str(data)[:200]
                    results.append({"number": number, "status": "error",
                                    "message": f"Ozon {http_status}: {msg}", "products": 0})
                elif data.get("status") == "COMPLETED" and data.get("certificate_id"):
                    results.append({"number": number, "status": "ok",
                                    "message": f"создан, certificate_id {data['certificate_id']}",
                                    "products": len(row["skus"])})
                else:
                    bad = [p for p in (data.get("params") or []) if p.get("state") != "VALID"]
                    detail = "; ".join(
                        f"{p.get('name')}: {p.get('error') or 'не принято'}" for p in bad
                    ) or "Ozon не принял документ"
                    results.append({"number": number, "status": "error",
                                    "message": detail, "products": 0})
            except Exception as e:  # noqa: BLE001
                results.append({"number": number, "status": "error",
                                "message": str(e)[:200], "products": 0})

            _import_status = {"running": True, "total": len(rows), "done": len(results),
                              "results": list(results), "error": ""}
            await asyncio.sleep(0.3)

    _import_status = {"running": False, "total": len(rows), "done": len(results),
                      "results": results, "error": ""}
