"""Telegram-уведомления трансляции остатков.

Бот @Sima_Ozon_Bot (BOT_TOKEN), получатель ADMIN_ID, SOCKS5-прокси
(PROXY_URL) — из РФ Telegram напрямую не ходит.

Принципы из handoff:
  1. Уведомления НЕ могут остановить цикл. Сбой Telegram не роняет запись.
  2. Каждый алерт говорит, тронули остаток или нет — иначе ночью никто
     не полезет на сервер разбираться.
"""
from __future__ import annotations

import os

import aiohttp


def _cfg() -> tuple[str, str, str]:
    token = os.getenv("BOT_TOKEN", "") or os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat = os.getenv("TELEGRAM_CHAT_ID", "") or os.getenv("ADMIN_ID", "")
    proxy = os.getenv("PROXY_URL", "socks5://quicknode-tg-proxy:1080")
    return token, chat, proxy


async def send(text: str) -> bool:
    token, chat, proxy = _cfg()
    if not token or not chat:
        print(f"[stock-broadcast TG off] {text}", flush=True)
        return False
    try:
        from aiohttp_socks import ProxyConnector
        connector = ProxyConnector.from_url(proxy)
        async with aiohttp.ClientSession(connector=connector) as s:
            async with s.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat, "text": text[:3900],
                      "disable_web_page_preview": True},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as r:
                data = await r.json(content_type=None)
                if not data.get("ok"):
                    print(f"[stock-broadcast TG fail] {data}", flush=True)
                return bool(data.get("ok"))
    except Exception as e:  # noqa: BLE001 — уведомление не должно ронять цикл
        print(f"[stock-broadcast TG error] {e}", flush=True)
        return False
