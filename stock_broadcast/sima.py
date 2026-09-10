"""Клиент Сима-Ленд (async, aiohttp). Порт sima-stocks-handoff/app/sima.py.

golem — РФ-VPS, ходим напрямую: без прокси и без ключа (публичный web-API v3,
проверено с сервера). Фасовку из API берём (min_qty / qty_multiplier),
но в цикле её можно перекрыть ручным значением — см. calc.resolve_real_min.
"""
from __future__ import annotations

import asyncio
import json

import aiohttp

from .calc import SimaItem

SIMA_BASE = "https://www.sima-land.ru/api/v3"
FIELDS = "id,sid,wholesale_price,stocks,barcodes,min_qty,qty_multiplier"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept": "application/json",
           "Accept-Language": "ru-RU,ru;q=0.9"}
RETRY_WAITS = [3, 8, 15, 30]


class SimaError(Exception):
    pass


def _int(v, default=1):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _parse(raw: dict, stock_id: int) -> SimaItem:
    st = None
    for s in raw.get("stocks") or []:
        try:
            if int(s.get("stock_id", 0)) == stock_id:
                st = s
                break
        except (TypeError, ValueError):
            continue
    enough = bool(st and st.get("balance_text") is not None)
    bal = None
    if st is not None and not enough:
        try:
            bal = int(st.get("balance") or 0)
        except (TypeError, ValueError):
            bal = 0
    price = raw.get("wholesale_price")
    try:
        price = float(price) if price is not None else None
    except (TypeError, ValueError):
        price = None
    return SimaItem(
        sid=str(raw.get("sid")), price=price,
        barcodes=[str(b) for b in (raw.get("barcodes") or [])],
        balance=bal, enough=enough,
        min_qty=_int(raw.get("min_qty"), 1),
        qty_multiplier=_int(raw.get("qty_multiplier"), 1),
    )


class SimaClient:
    def __init__(self, stock_id: int = 115, bulk_size: int = 100,
                 pause_ms: int = 300):
        self.stock_id = stock_id
        self.bulk_size = bulk_size
        self.pause_ms = pause_ms
        self.errors = 0
        self.failed_batches: list[str] = []

    async def _fetch(self, session, url: str, timeout: int = 60) -> str | None:
        """None = 404."""
        last = None
        for i, wait in enumerate([0] + RETRY_WAITS):
            if wait:
                await asyncio.sleep(wait)
            try:
                async with session.get(
                    url, headers=HEADERS,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as r:
                    if r.status == 404:
                        return None
                    if r.status in (429, 500, 502, 503, 504):
                        last = f"Сима HTTP {r.status}"
                        continue
                    if r.status != 200:
                        raise SimaError(f"Сима HTTP {r.status}")
                    return await r.text()
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last = str(e)
                continue
        raise SimaError(last or "неизвестная ошибка")

    async def fetch_many(self, sids) -> tuple[dict[str, SimaItem], set[str]]:
        """Пачками по bulk_size. -> (найденное, sid_из_упавших_пачек).

        Второе значение критично. «Нет в ответе» имеет два смысла:
          пачка упала            -> состояние НЕИЗВЕСТНО, трогать нельзя;
          пачка ответила без sid -> товара в каталоге нет, транслируем 0.
        Если их спутать, любой таймаут Симы обнулит весь список.
        """
        out: dict[str, SimaItem] = {}
        failed: set[str] = set()
        sids = sorted({str(s) for s in sids})
        total = (len(sids) + self.bulk_size - 1) // self.bulk_size or 1
        async with aiohttp.ClientSession() as session:
            for n, i in enumerate(range(0, len(sids), self.bulk_size), 1):
                part = sids[i:i + self.bulk_size]
                url = (f"{SIMA_BASE}/item/?sid={','.join(part)}"
                       f"&expand=stocks,barcodes&fields={FIELDS}"
                       f"&per-page={self.bulk_size}")
                try:
                    body = await self._fetch(session, url)
                except SimaError as e:
                    self.errors += 1
                    failed.update(part)
                    self.failed_batches.append(
                        f"пачка {n}/{total} ({part[0]}…{part[-1]}): {e}")
                    continue
                if body is None:
                    failed.update(part)
                    continue
                try:
                    items = (json.loads(body) or {}).get("items") or []
                except ValueError:
                    self.errors += 1
                    failed.update(part)
                    self.failed_batches.append(f"пачка {n}/{total}: не-JSON")
                    continue
                for raw in items:
                    it = _parse(raw, self.stock_id)
                    out[it.sid] = it
                await asyncio.sleep(self.pause_ms / 1000)
        return out, failed

    async def find_by_barcode(self, barcode: str) -> SimaItem | None:
        """Точный поиск. ВНИМАНИЕ: параметр только в ЕДИНСТВЕННОМ числе.
        `?barcodes=` фильтр молча игнорирует и отдаёт весь каталог (218k).
        Проверяем totalCount == 1 И что баркод реально в товаре."""
        url = (f"{SIMA_BASE}/item/?barcode={barcode}"
               f"&expand=stocks,barcodes&fields={FIELDS}&per-page=5")
        async with aiohttp.ClientSession() as session:
            try:
                body = await self._fetch(session, url)
            except SimaError:
                return None
        if not body:
            return None
        try:
            d = json.loads(body)
        except ValueError:
            return None
        total = (d.get("_meta") or {}).get("totalCount")
        items = d.get("items") or []
        if total != 1 or not items:
            return None
        it = _parse(items[0], self.stock_id)
        return it if barcode in it.barcodes else None
