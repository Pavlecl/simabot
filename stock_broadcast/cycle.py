"""Один проход трансляции остатков Сима-Ленд -> Ozon FBS.

Порт sima-stocks-handoff/app/cycle.py, упрощённый под Ozon:
единица — offer_id, один склад, курируемый список артикулов.

Порядок:
  1. конфиг; выключен -> выход
  2. списки из БД: артикулы, ОтклОстаток, Фасовка
  3. guard «список стёрли»
  4. bulk-выгрузка Симы
  5. заказы Ozon (awaiting_packaging)
  6. живой FBS-остаток целевого склада -> база дельты
  7. расчёт по каждому offer_id + ОтклОстаток
  8. дельта -> запись ТОЛЬКО изменившегося
  9. состояние, журнал, слепок плана
 10. Telegram: отчёт + алерты (с дедупликацией)
"""
from __future__ import annotations

import json
import time
import traceback
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from database import (
    AsyncSessionLocal, OzonAccount,
    BroadcastAlert, BroadcastConfig, BroadcastCycle, BroadcastDisable,
    BroadcastFasovka, BroadcastItem, BroadcastRejected, BroadcastState,
)

from . import calc, notify
from .ozon import OzonClient, OzonError
from .sima import SimaClient

PLAN_KEEP = 50
ALERT_COOLDOWN_MIN = 60

# In-memory статус для UI (без БД — просто «что сейчас происходит»).
STATUS: dict = {
    "running": False, "phase": "", "trigger": None,
    "last_run": None, "next_run": None, "last_summary": None,
}


# --------------------------------------------------------------------- конфиг
async def get_or_create_config(db) -> BroadcastConfig:
    cfg = await db.get(BroadcastConfig, 1)
    if cfg is None:
        cfg = BroadcastConfig(id=1)
        db.add(cfg)
        await db.commit()
        cfg = await db.get(BroadcastConfig, 1)
    return cfg


# --------------------------------------------------------------------- алерты
async def _alert(db, key: str, text: str) -> None:
    row = await db.get(BroadcastAlert, key)
    now = datetime.now()
    if row and row.last_sent and (now - row.last_sent) < timedelta(minutes=ALERT_COOLDOWN_MIN):
        return
    await db.execute(
        pg_insert(BroadcastAlert).values(key=key, last_sent=now)
        .on_conflict_do_update(index_elements=["key"], set_={"last_sent": now})
    )
    await db.commit()
    await notify.send(text)


def _set_next_run(cfg: BroadcastConfig) -> None:
    if cfg.enabled and cfg.cycle_minutes:
        STATUS["next_run"] = (datetime.now() + timedelta(minutes=cfg.cycle_minutes)).isoformat()
    else:
        STATUS["next_run"] = None


# --------------------------------------------------------------------- цикл
async def run_cycle(trigger: str = "auto") -> dict:
    if STATUS["running"]:
        return {"skipped": "уже выполняется"}

    STATUS.update(running=True, phase="старт", trigger=trigger)
    t0 = time.time()
    started = datetime.now()
    summary: dict = {"trigger": trigger, "started": started.isoformat(), "errors": [], "notes": []}

    try:
        async with AsyncSessionLocal() as db:
            cfg = await get_or_create_config(db)

            if trigger == "auto" and not cfg.enabled:
                return {"skipped": "трансляция выключена"}
            if not cfg.ozon_account_id or not cfg.warehouse_id:
                summary["error"] = "не выбран кабинет или склад"
                return summary

            acc = await db.get(OzonAccount, cfg.ozon_account_id)
            if acc is None:
                summary["error"] = "кабинет Ozon не найден"
                return summary
            headers = {"Client-Id": acc.client_id, "Api-Key": acc.api_key,
                       "Content-Type": "application/json"}

            # ---- 2. списки
            STATUS["phase"] = "чтение списков"
            item_rows = (await db.execute(
                select(BroadcastItem).where(BroadcastItem.enabled == True))).scalars().all()  # noqa: E712
            offer_ids = [r.offer_id for r in item_rows]
            names = {r.offer_id: (r.name or "") for r in item_rows}

            disable_rows = (await db.execute(select(BroadcastDisable))).scalars().all()
            disabled = {r.offer_id: (r.reason or "ОтклОстаток") for r in disable_rows}

            fas_rows = (await db.execute(select(BroadcastFasovka))).scalars().all()
            fas_override = {r.offer_id: r.real_min for r in fas_rows}
            fas_disabled = {r.offer_id for r in fas_rows if r.disabled}

            if not offer_ids:
                summary["note"] = "список артикулов пуст — нечего транслировать"
                await _save_cycle(db, cfg, summary, started, t0, [], trigger)
                _finish_status(started, cfg, summary)
                return summary

            # ---- 3. guard «список стёрли»
            prev = cfg.last_item_count or 0
            if prev >= 20 and len(offer_ids) < prev * 0.5:
                await _alert(
                    db, "items_wiped",
                    f"🛑 Список артикулов трансляции резко сократился: "
                    f"было {prev}, стало {len(offer_ids)}.\n"
                    f"Цикл остановлен, остатки НЕ изменены.\n"
                    f"Если сокращение намеренное — повторите запуск вручную в разделе."
                )
                summary["error"] = f"guard: {prev} -> {len(offer_ids)} артикулов"
                _finish_status(started, cfg, summary)
                return summary

            # ---- 4. Сима
            STATUS["phase"] = "выгрузка Симы"
            sima = SimaClient(stock_id=cfg.sima_stock_id or 115)
            found, failed = await sima.fetch_many(offer_ids)

            # ---- 5. заказы Ozon
            STATUS["phase"] = "заказы Ozon"
            oz = OzonClient(headers, cfg.warehouse_id)
            try:
                orders_by_oid = {str(k): int(v) for k, v in (await oz.awaiting_demand()).items()}
            except OzonError as e:
                orders_by_oid = {}
                summary["errors"].append(f"заказы: {e}")

            # ---- 6. живой FBS + каталог
            STATUS["phase"] = "остатки Ozon"
            try:
                live = await oz.fbs_stocks(offer_ids)
            except OzonError as e:
                live = {}
                summary["errors"].append(f"чтение остатков: {e}")
            try:
                catalog = await oz.catalog_offer_ids()
            except OzonError as e:
                catalog = None
                summary["errors"].append(f"каталог: {e}")

            state_rows = (await db.execute(select(BroadcastState))).scalars().all()
            prev_amount = {r.offer_id: (r.last_amount or 0) for r in state_rows}

            # ---- 7. расчёт
            STATUS["phase"] = "расчёт"
            plan: list[dict] = []
            gone: list[str] = []
            no_fas: list[str] = []

            for oid in offer_ids:
                if catalog is not None and oid not in catalog:
                    gone.append(oid)
                    continue

                item = found.get(oid)
                forced = oid in disabled

                # Пачка Симы упала -> состояние неизвестно, не трогаем (кроме ОтклОстаток).
                if not forced and oid in failed:
                    continue

                rm, rm_src = calc.resolve_real_min(
                    item, fas_override.get(oid), oid in fas_disabled)
                if (item is not None and rm_src == "API Симы"
                        and oid not in fas_override and oid not in fas_disabled
                        and item.api_real_min > 1):
                    no_fas.append(oid)  # фасовка взята из API — стоит подтвердить вручную

                if forced:
                    r = calc.CalcResult(
                        0, "ОтклОстаток", disabled[oid], None,
                        item.price if item else None, rm)
                    n_ord = 0
                else:
                    n_ord = orders_by_oid.get(oid, 0)
                    r = calc.compute(
                        item, rm, budget=cfg.budget_limit,
                        cutoff=cfg.cutoff_balance, divisor=cfg.safety_divisor,
                        orders=n_ord)

                was = live.get(oid, prev_amount.get(oid, 0))
                plan.append({
                    "offer_id": oid, "name": names.get(oid, ""),
                    "want": r.qty, "was": was,
                    "branch": r.branch, "reason": r.reason,
                    "balance": r.balance, "price": r.price,
                    "real_min": r.real_min, "real_min_src": rm_src,
                    "orders": n_ord,
                    "calc_qty": r.qty + n_ord,
                    "enough": bool(item and item.enough),
                })

            total_before = sum(p["was"] for p in plan)
            total_after = sum(p["want"] for p in plan)
            changed = [p for p in plan if p["want"] != p["was"]]

            # ---- 8. запись
            STATUS["phase"] = "запись"
            written = 0
            bad: list[tuple[str, str]] = []
            batch_errs: list[str] = []

            if changed and not cfg.dry_run:
                rej_ids = {r.offer_id for r in
                           (await db.execute(select(BroadcastRejected))).scalars().all()}
                main = [{"offer_id": p["offer_id"], "stock": p["want"]}
                        for p in changed if p["offer_id"] not in rej_ids]
                retry = [{"offer_id": p["offer_id"], "stock": p["want"]}
                         for p in changed if p["offer_id"] in rej_ids]

                w1, b1, e1 = await oz.put_stocks(main)
                written += w1
                bad += b1
                batch_errs += e1
                if retry:
                    w2, b2, e2 = await oz.put_stocks(retry)
                    written += w2
                    bad += b2
                    batch_errs += e2
                    recovered = {r["offer_id"] for r in retry} - {o for o, _ in b2}
                    if recovered:
                        await db.execute(delete(BroadcastRejected)
                                         .where(BroadcastRejected.offer_id.in_(recovered)))

                for oid, msg in bad:
                    now = datetime.now()
                    await db.execute(
                        pg_insert(BroadcastRejected).values(
                            offer_id=oid, code="", message=(msg or "")[:400],
                            first_seen=now, last_seen=now, hits=1)
                        .on_conflict_do_update(index_elements=["offer_id"], set_={
                            "message": (msg or "")[:400], "last_seen": now,
                            "hits": BroadcastRejected.hits + 1}))
                await db.commit()

            # ---- 9. состояние (по всему плану, не только изменившемуся)
            if not cfg.dry_run:
                now = datetime.now()
                for p in plan:
                    vals = {
                        "last_amount": p["want"], "last_branch": p["branch"],
                        "last_reason": p["reason"], "sima_balance": p["balance"],
                        "calc_qty": p["calc_qty"], "real_min": p["real_min"],
                        "real_min_src": p["real_min_src"], "orders": p["orders"],
                        "updated_at": now,
                    }
                    await db.execute(
                        pg_insert(BroadcastState).values(offer_id=p["offer_id"], **vals)
                        .on_conflict_do_update(index_elements=["offer_id"], set_=vals))
                await db.commit()

            # ---- журнал
            turned_on = sum(1 for p in changed if p["was"] == 0 and p["want"] > 0)
            turned_off = sum(1 for p in changed if p["was"] > 0 and p["want"] == 0)
            changed_n = len(changed) - turned_on - turned_off

            if failed:
                summary["notes"].append(
                    f"Сима не ответила по {len(failed)} артикулам — остатки не тронуты")
            if gone:
                summary["notes"].append(
                    f"нет в каталоге Ozon: {len(gone)} ({', '.join(gone[:5])})")
            if no_fas:
                summary["notes"].append(
                    f"фасовка взята из API для {len(no_fas)} — подтвердите вручную")
            if bad:
                summary["notes"].append(
                    f"Ozon отклонил {len(bad)} записей — повтор в следующем цикле")
            summary["errors"] += sima.failed_batches[:3] + batch_errs[:3]

            summary.update({
                "dry_run": cfg.dry_run, "account": acc.name,
                "warehouse_id": cfg.warehouse_id,
                "planned": len(plan), "written": len(changed) if not cfg.dry_run else 0,
                "turned_on": turned_on, "turned_off": turned_off, "changed": changed_n,
                "total_before": total_before, "total_after": total_after,
                "orders_total": sum(orders_by_oid.values()),
                "orders_subtracted": sum(p["orders"] for p in plan),
                "seconds": round(time.time() - t0, 1),
            })

            cyc_id = await _save_cycle(db, cfg, summary, started, t0, plan, trigger)
            summary["cycle_id"] = cyc_id

            cfg.last_item_count = len(offer_ids)
            cfg.last_run_at = datetime.now()
            await db.commit()

            # ---- 10. Telegram
            await _maybe_report(cfg, summary)
            if failed:
                await _alert(db, "sima_batch",
                             "⚠️ Сима не ответила\n"
                             f"{'; '.join(sima.failed_batches[:2])}\n"
                             f"Не тронуто артикулов: {len(failed)} — держим прошлые остатки.")
            if gone:
                await _alert(db, "gone_from_catalog",
                             f"⚠️ Артикулы пропали из каталога Ozon: {len(gone)}\n"
                             f"{', '.join(gone[:10])}\nОстатки НЕ трогали.")
            fatal = [e for e in batch_errs if e.startswith("КРИТИЧНО")]
            if fatal:
                await _alert(db, "ozon_fatal",
                             "🛑 Ozon отклоняет запись остатков целиком\n"
                             f"{fatal[0][:300]}\n"
                             "Остатки НЕ изменены. Нужен разбор (токен / права / склад).")

            _finish_status(started, cfg, summary)
            return summary

    except Exception as e:  # noqa: BLE001
        tb = traceback.format_exc()[:1500]
        print(f"STOCK-BROADCAST cycle crash:\n{tb}", flush=True)
        try:
            async with AsyncSessionLocal() as db:
                await _alert(db, "cycle_crash",
                             f"⚠️ Цикл трансляции остатков упал\n{e}\n"
                             "Часть шагов могла выполниться. Следующая попытка по расписанию.")
        except Exception:
            pass
        summary["error"] = str(e)
        return summary
    finally:
        STATUS.update(running=False, phase="")


async def _save_cycle(db, cfg, summary, started, t0, plan, trigger) -> int:
    cyc = BroadcastCycle(
        started_at=started, finished_at=datetime.now(),
        seconds=round(time.time() - t0, 1),
        dry_run=cfg.dry_run, trigger=trigger,
        total_before=summary.get("total_before", 0),
        total_after=summary.get("total_after", 0),
        written=summary.get("written", 0),
        turned_on=summary.get("turned_on", 0),
        turned_off=summary.get("turned_off", 0),
        changed=summary.get("changed", 0),
        planned=summary.get("planned", len(plan)),
        orders_total=summary.get("orders_total", 0),
        orders_subtracted=summary.get("orders_subtracted", 0),
        errors_json=json.dumps(summary.get("errors", []), ensure_ascii=False),
        notes_json=json.dumps(summary.get("notes", []), ensure_ascii=False),
        plan_json=json.dumps(plan, ensure_ascii=False, default=str),
    )
    db.add(cyc)
    await db.commit()
    old = (await db.execute(
        select(BroadcastCycle.id).order_by(BroadcastCycle.id.desc()).offset(PLAN_KEEP)
    )).scalars().all()
    if old:
        await db.execute(delete(BroadcastCycle).where(BroadcastCycle.id.in_(old)))
        await db.commit()
    return cyc.id


async def _maybe_report(cfg: BroadcastConfig, s: dict) -> None:
    mode = cfg.tg_report_mode or "onchange"
    if mode == "never":
        return
    has_change = s.get("written") or s.get("errors") or s.get("notes")
    if mode == "onchange" and not has_change:
        return

    dry = " · DRY-RUN" if s.get("dry_run") else ""
    d = s.get("total_after", 0) - s.get("total_before", 0)
    lines = [
        f"Трансляция остатков{dry} · {s.get('account', '')}",
        f"склад {s.get('warehouse_id')} · {s.get('seconds')} с",
        "",
        f"остаток: {s.get('total_before', 0):,} → {s.get('total_after', 0):,} ({d:+})".replace(",", " "),
        f"записано: {s.get('written', 0)} "
        f"(вкл {s.get('turned_on', 0)}, обнул {s.get('turned_off', 0)}, изм {s.get('changed', 0)})",
        f"артикулов в плане: {s.get('planned', 0)}",
        f"вычтено по заказам: {s.get('orders_subtracted', 0)}",
    ]
    if s.get("errors"):
        lines += ["", "ОШИБКИ:"] + [f"• {e}" for e in s["errors"][:5]]
    else:
        lines.append("ошибок нет")
    for n in s.get("notes", [])[:5]:
        lines.append(f"ℹ️ {n}")
    await notify.send("\n".join(lines))


def _finish_status(started: datetime, cfg: BroadcastConfig, summary: dict) -> None:
    STATUS["last_run"] = started.isoformat()
    STATUS["last_summary"] = summary
    _set_next_run(cfg)


# --------------------------------------------------------------------- loop
async def loop_forever() -> None:
    """Фоновый цикл, живёт в lifespan web-контейнера (как fbo_sales_watch_loop)."""
    import asyncio
    await asyncio.sleep(60)  # даём приложению подняться
    while True:
        delay = 14 * 60
        try:
            async with AsyncSessionLocal() as db:
                cfg = await get_or_create_config(db)
                delay = max(60, (cfg.cycle_minutes or 14) * 60)
                run = bool(cfg.enabled)
                _set_next_run(cfg)
            if run:
                await run_cycle("auto")
        except Exception as e:  # noqa: BLE001
            print(f"STOCK-BROADCAST loop error: {e}", flush=True)
        await asyncio.sleep(delay)
