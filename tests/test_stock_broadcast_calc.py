"""Проверка формулы трансляции остатков без сети.

Эталоны перенесены из sima-stocks-handoff/tests/test_calc.py — сверены
с живой трансляцией Симы. Запуск: `python3 tests/test_stock_broadcast_calc.py`
(или `python3 -m pytest tests/ -q`).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_broadcast import calc  # noqa: E402
from stock_broadcast.calc import SimaItem  # noqa: E402

BUDGET, CUTOFF, DIVISOR = 50000, 20, 1.5
FAIL = 0


def check(name, got, want):
    global FAIL
    ok = got == want
    if not ok:
        FAIL += 1
    print(f"  {'ok  ' if ok else 'FAIL'} {name}: получили {got}, ждали {want}")


def item(price=None, balance=None, enough=False, min_qty=1, qty_multiplier=1):
    return SimaItem(sid="test", price=price, balance=balance, enough=enough,
                    min_qty=min_qty, qty_multiplier=qty_multiplier)


def q(it, real_min=1, orders=0):
    return calc.compute(it, real_min, budget=BUDGET, cutoff=CUTOFF,
                        divisor=DIVISOR, orders=orders).qty


def test_enough_branch():
    print("\n— ветка «Достаточно»: ceil(50000 / цена / real_min)")
    check("цена 1217 -> 42 (ceil, не floor!)", q(item(price=1217, enough=True)), 42)
    check("цена 128 -> 391", q(item(price=128, enough=True)), 391)
    check("цена 60 -> 834", q(item(price=60, enough=True)), 834)
    check("фасовка 2: цена 128 -> 196", q(item(price=128, enough=True), 2), 196)
    check("нет цены -> 0", q(item(price=0, enough=True)), 0)


def test_number_branch():
    print("\n— ветка «число»: порог по СЫРОМУ балансу, потом делитель")
    for bal, want in ((5, 0), (18, 0), (19, 0), (20, 13), (21, 14), (22, 14),
                      (25, 16), (58, 38), (63, 42), (380, 253), (445, 296)):
        check(f"balance {bal}", q(item(balance=bal)), want)
    check("balance 25 -> 16, а не 0 (16.6 < 20)", q(item(balance=25)), 16)


def test_fasovka():
    print("\n— фасовка")
    check("balance 100, real_min 5 -> 13", q(item(balance=100), 5), 13)
    check("balance 100, real_min 50 -> 1", q(item(balance=100), 50), 1)
    check("balance 60, real_min 50 -> 0", q(item(balance=60), 50), 0)


def test_orders():
    print("\n— заказы вычитаются после расчёта")
    check("balance 60 -> 40, минус 5 -> 35", q(item(balance=60), 1, orders=5), 35)
    check("заказов больше расчёта -> 0", q(item(balance=60), 1, orders=99), 0)


def test_edge():
    print("\n— крайние случаи")
    check("нет товара в Симе -> 0", q(None), 0)
    check("ветка при отсутствии товара", calc.compute(
        None, 1, budget=BUDGET, cutoff=CUTOFF, divisor=DIVISOR).branch, "нет в Симе")
    check("нет стока -> 0", q(item(price=100)), 0)


def test_resolve_real_min():
    print("\n— real_min: ОтклФас -> ручное -> API Симы -> 1")
    check("ОтклФас побеждает всё",
          calc.resolve_real_min(item(qty_multiplier=5, min_qty=10), 7, True), (1, "ОтклФас"))
    check("ручное перекрывает API",
          calc.resolve_real_min(item(qty_multiplier=5, min_qty=10), 3, False), (3, "ручное"))
    check("из API: multiplier>1 -> min_qty",
          calc.resolve_real_min(item(qty_multiplier=6, min_qty=10), None, False), (10, "API Симы"))
    check("из API: multiplier==1 -> 1",
          calc.resolve_real_min(item(qty_multiplier=1, min_qty=10), None, False), (1, "API Симы"))
    check("нет данных -> 1", calc.resolve_real_min(None, None, False), (1, "нет данных"))


if __name__ == "__main__":
    for fn in (test_enough_branch, test_number_branch, test_fasovka,
               test_orders, test_edge, test_resolve_real_min):
        fn()
    print(f"\n{'ВСЁ ЗЕЛЁНОЕ' if not FAIL else f'ПРОВАЛЕНО: {FAIL}'}")
    sys.exit(1 if FAIL else 0)
