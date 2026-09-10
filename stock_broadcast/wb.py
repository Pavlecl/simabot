"""Wildberries: невыполненные сборочные задания FBS.

Товар Сима-Ленд кормит и Ozon, и WB из одного склада. Заказ на WB баланс
Симы не уменьшает (Сима узнаёт о нём только когда мы закупимся), поэтому
транслировать по Ozon полный расчёт по Симе — это оверселл на общий остаток.
Вычитаем невыполненные задания WB из транслируемого числа.

Ограничение (как в исходном проекте, вопрос З1): `orders/new` отдаёт только
нераспределённые задания. Взяли заказ в сборку -> он исчез из выдачи ->
перестал вычитаться. Полное покрытие потребовало бы ещё тянуть поставки.
"""
from __future__ import annotations

import aiohttp

ORDERS_NEW = "https://marketplace-api.wildberries.ru/api/v3/orders/new"


class WbClient:
    def __init__(self, token: str):
        self.token = token
        self.errors = 0

    async def pending_demand(self) -> dict[str, int]:
        """{article: число невыполненных заданий}.

        Только числовые article (= артикул Симы = offer_id Ozon). Свои бренды
        (нечисловые vendorCode) пропускаем — их Сима не кормит.
        """
        out: dict[str, int] = {}
        if not self.token:
            return out
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    ORDERS_NEW, headers={"Authorization": self.token},
                    timeout=aiohttp.ClientTimeout(total=40),
                ) as r:
                    if r.status != 200:
                        self.errors += 1
                        return out
                    d = await r.json()
        except Exception:  # noqa: BLE001 — сбой WB не должен ронять цикл Ozon
            self.errors += 1
            return out
        for o in d.get("orders", []):
            art = str(o.get("article") or "").strip()
            if art.isdigit():
                out[art] = out.get(art, 0) + 1
        return out
