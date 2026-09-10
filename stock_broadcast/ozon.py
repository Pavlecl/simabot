"""Обёртки Ozon Seller API для трансляции остатков.

Заменяет весь app/wb.py из handoff. На Ozon единица остатка — offer_id,
поэтому подбор баркода/размера не нужен.

  чтение FBS   /v4/product/info/stocks   (present по type=='fbs')
  запись FBS   /v2/products/stocks       (result[].updated — НЕ статус ответа)
  каталог      /v1/product/list          (проверка, что артикул ещё жив)
  заказы       /v3/posting/fbs/list      (awaiting_packaging -> вычитаем)
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import aiohttp

BASE = "https://api-seller.ozon.ru"
RETRY_WAITS = [3, 8, 15, 30]
FATAL_STATUS = {401, 402, 403, 406}


class OzonError(Exception):
    pass


class OzonClient:
    def __init__(self, headers: dict, warehouse_id: int):
        self.headers = headers
        self.warehouse_id = int(warehouse_id)
        self.errors = 0

    async def _post(self, session, path: str, payload: dict,
                    timeout: int = 60) -> tuple[int, str]:
        last = None
        for i, wait in enumerate([0] + RETRY_WAITS):
            if wait:
                await asyncio.sleep(wait)
            try:
                async with session.post(
                    f"{BASE}{path}", json=payload, headers=self.headers,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as r:
                    if r.status in (429, 500, 502, 503, 504):
                        last = f"HTTP {r.status}"
                        continue
                    return r.status, await r.text()
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last = str(e)
                continue
        self.errors += 1
        raise OzonError(f"Ozon не ответил: {last}")

    # ------------------------------------------------------------ каталог
    async def catalog_offer_ids(self) -> set[str]:
        """Все offer_id кабинета. Нужен, чтобы отличить «артикул убрали из
        каталога» от «Сима не ответила» — гасить по второму нельзя."""
        out: set[str] = set()
        last_id = ""
        async with aiohttp.ClientSession() as s:
            while True:
                payload = {"filter": {"visibility": "ALL"}, "limit": 1000}
                if last_id:
                    payload["last_id"] = last_id
                st, text = await self._post(s, "/v1/product/list", payload)
                if st != 200:
                    raise OzonError(f"/v1/product/list HTTP {st}: {text[:200]}")
                res = (json.loads(text) or {}).get("result", {})
                items = res.get("items", [])
                for it in items:
                    if it.get("offer_id"):
                        out.add(str(it["offer_id"]))
                last_id = res.get("last_id") or ""
                if not items or not last_id or len(items) < 1000:
                    break
                await asyncio.sleep(0.2)
        return out

    # ------------------------------------------------------------ остатки
    async def fbs_stocks(self, offer_ids: list[str]) -> dict[str, int]:
        """{offer_id: present по FBS} — фактическая база дельты.

        Ozon суммирует FBS-остаток по складам в пределах offer_id и отдельный
        склад в этом ответе не выделяет. Для одного целевого склада (обычный
        случай) это корректно; при нескольких FBS-складах у товара база дельты
        будет включать чужой склад — тогда нужен отдельный источник.
        """
        out: dict[str, int] = {}
        async with aiohttp.ClientSession() as s:
            for i in range(0, len(offer_ids), 100):
                batch = offer_ids[i:i + 100]
                st, text = await self._post(s, "/v4/product/info/stocks", {
                    "filter": {"offer_id": batch, "visibility": "ALL"},
                    "limit": 100, "offset": 0,
                })
                if st != 200:
                    self.errors += 1
                    continue
                d = json.loads(text) or {}
                for it in d.get("items", []):
                    oid = str(it.get("offer_id") or "")
                    if not oid:
                        continue
                    out[oid] = sum(
                        x.get("present", 0) for x in it.get("stocks", [])
                        if x.get("type") == "fbs")
                await asyncio.sleep(0.25)
        return out

    # ------------------------------------------------------------ запись
    async def put_stocks(self, updates: list[dict]
                         ) -> tuple[int, list[tuple[str, str]], list[str]]:
        """updates: [{'offer_id','stock'}].
        -> (записано, [(offer_id, сообщение)] отвергнутых, [ошибки пачек]).

        HTTP 200 != «применено». Ozon возвращает result[].updated (bool) и
        errors[] по каждому offer_id — проверяем флаг, а не статус.
        """
        written = 0
        bad: list[tuple[str, str]] = []
        batch_errs: list[str] = []
        async with aiohttp.ClientSession() as s:
            for i in range(0, len(updates), 100):
                batch = updates[i:i + 100]
                payload = {"stocks": [
                    {"offer_id": u["offer_id"], "stock": int(u["stock"]),
                     "warehouse_id": self.warehouse_id}
                    for u in batch
                ]}
                try:
                    st, text = await self._post(s, "/v2/products/stocks", payload)
                except OzonError as e:
                    batch_errs.append(str(e))
                    continue
                if st != 200:
                    tag = "КРИТИЧНО " if st in FATAL_STATUS else ""
                    batch_errs.append(f"{tag}HTTP {st}: {text[:200]}")
                    continue
                d = json.loads(text) or {}
                for r in d.get("result", []):
                    if r.get("updated"):
                        written += 1
                    else:
                        errs = r.get("errors") or []
                        msg = "; ".join(
                            str(e.get("message") or e.get("code") or "")
                            for e in errs) or "не обновлён"
                        bad.append((str(r.get("offer_id") or ""), msg))
                await asyncio.sleep(0.25)
        return written, bad, batch_errs

    # ------------------------------------------------------------ заказы
    async def awaiting_demand(self) -> dict[str, int]:
        """{offer_id: суммарное количество в awaiting_packaging} целевого склада.

        В отличие от get_total_ozon_demand() (для закупки у Симы) виртуальные
        заказы НЕ исключаем: товар в многопозиционном заказе всё равно уедет,
        и остаток нужно уменьшить, чтобы не продать дважды.
        """
        out: dict[str, int] = {}
        date_to = datetime.now() + timedelta(days=1)
        date_from = date_to - timedelta(days=30)
        async with aiohttp.ClientSession() as s:
            offset = 0
            while True:
                st, text = await self._post(s, "/v3/posting/fbs/list", {
                    "dir": "ASC",
                    "filter": {
                        "status": "awaiting_packaging",
                        "warehouse_id": [self.warehouse_id],
                        "since": date_from.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                        "to": date_to.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                    },
                    "limit": 1000, "offset": offset,
                    "with": {"analytics_data": False, "financial_data": False},
                })
                if st != 200:
                    self.errors += 1
                    raise OzonError(f"/v3/posting/fbs/list HTTP {st}: {text[:200]}")
                postings = (json.loads(text) or {}).get("result", {}).get("postings", [])
                for p in postings:
                    for pr in p.get("products", []):
                        oid = str(pr.get("offer_id") or pr.get("sku") or "")
                        if not oid:
                            continue
                        out[oid] = out.get(oid, 0) + int(pr.get("quantity", 0))
                if len(postings) < 1000:
                    break
                offset += 1000
                await asyncio.sleep(0.2)
        return out
