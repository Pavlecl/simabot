"""Формула количества к продаже (порт sima-stocks-handoff/app/calc.py).

    нет товара в API Симы            -> держим прошлый остаток (решает вызывающий)
    нет стока целевого склада Симы   -> 0
    balance_text («Достаточно»)      -> ceil(BUDGET / цена_опт / real_min)
    число, balance < CUTOFF          -> 0
    число                            -> floor(balance / SAFETY_DIVISOR / real_min)
    минус заказы Ozon, отсечка по нулю

Порядок в ветке с числом важен: СНАЧАЛА порог по сырому балансу, ПОТОМ делитель.
CUTOFF меряет реальный товар у Симы, а не то, сколько мы решили показать.

Ozon-версия: подбор баркода и деление между складами убраны — на Ozon
единица остатка это offer_id. Значения (бюджет, порог, делитель) подобраны
сверкой с живой трансляцией Симы, менять с осторожностью.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class SimaItem:
    sid: str
    price: float | None
    balance: int | None          # число со склада SIMA_STOCK_ID
    enough: bool                 # пришёл balance_text («Достаточно»)
    barcodes: list[str] = field(default_factory=list)
    min_qty: int = 1
    qty_multiplier: int = 1

    @property
    def has_stock_record(self) -> bool:
        return self.balance is not None or self.enough

    @property
    def api_real_min(self) -> int:
        """Фасовка из ответа Симы: qty_multiplier==1 -> штучный, иначе min_qty.
        В handoff API временами врал (20 расхождений на 68k) — поэтому в
        вызывающем коде это значение можно перекрыть ручным из веб-интерфейса."""
        if int(self.qty_multiplier or 1) == 1:
            return 1
        return max(1, int(self.min_qty or 1))


@dataclass
class CalcResult:
    qty: int
    branch: str          # Достаточно | число | нет в Симе | нет стока | ОтклОстаток
    reason: str          # почему ноль; пусто, если не ноль
    balance: int | None
    price: float | None
    real_min: int


def resolve_real_min(item: SimaItem | None, override: int | None,
                     fasovka_disabled: bool) -> tuple[int, str]:
    """(real_min, источник).

    Приоритет: ОтклФас -> ручное переопределение -> API Симы -> 1.
    Молчаливый дефолт 1 при отсутствии данных превратил бы пачку в штуку —
    вызывающий обязан поднять алерт в этом случае.
    """
    if fasovka_disabled:
        return 1, "ОтклФас"
    if override and int(override) > 0:
        return max(1, int(override)), "ручное"
    if item is not None:
        return item.api_real_min, "API Симы"
    return 1, "нет данных"


def compute(item: SimaItem | None, real_min: int, *,
            budget: int, cutoff: int, divisor: float,
            orders: int = 0) -> CalcResult:
    real_min = max(1, int(real_min))

    if item is None:
        return CalcResult(0, "нет в Симе", "нет в Симе", None, None, real_min)

    if not item.has_stock_record:
        return CalcResult(0, "нет стока", "нет стока Симы", None,
                          item.price, real_min)

    if item.enough:
        if not item.price or item.price <= 0:
            return CalcResult(0, "Достаточно", "нет цены", None,
                              item.price, real_min)
        qty = math.ceil(budget / item.price / real_min)
        return _finish(qty, "Достаточно", None, item.price, real_min, orders)

    balance = int(item.balance or 0)
    if balance < cutoff:
        return CalcResult(0, "число", f"balance {balance} < {cutoff}",
                          balance, item.price, real_min)

    # СНАЧАЛА порог по сырому балансу (выше), ПОТОМ делитель — не наоборот.
    # balance=25 -> порог пройден (25>=20) -> 25/1.5 = 16. Если делить первым,
    # 16.6 < 20 дало бы 0, и мы отсекали бы втрое больше задуманного.
    qty = math.floor(balance / divisor / real_min)
    return _finish(qty, "число", balance, item.price, real_min, orders)


def _finish(qty: int, branch: str, balance, price, real_min: int,
            orders: int) -> CalcResult:
    if orders:
        qty -= orders
    if qty <= 0:
        reason = "съели заказы" if orders else "расчёт дал 0"
        return CalcResult(0, branch, reason, balance, price, real_min)
    return CalcResult(qty, branch, "", balance, price, real_min)
