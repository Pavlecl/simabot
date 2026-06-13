# SimaBot — правила работы с проектом

## Серверы и пути
- **Сервер (golem):** `ssh -i ~/.ssh/finmind_server -p 2222 pavlecl@89.179.122.136`
- **Путь на сервере:** `/home/pavlecl/sima_bot/`
- **Локальная копия:** `/Users/pavellyashenko/Documents/Py projects/SimaBot/`
- **GitHub:** https://github.com/Pavlecl/simabot

## Стек
- Bot: aiogram (Telegram-бот, main.py)
- Web: FastAPI + Jinja2 (web_app.py, порт 8001 на хосте)
- DB: PostgreSQL 15 (SQLAlchemy async, AsyncSessionLocal)
- Планировщик: apscheduler (AsyncIOScheduler)
- Deploy: Docker Compose (3 контейнера: db, bot, web)

## Контейнеры Docker
| Контейнер | Описание | Порт |
|---|---|---|
| db | PostgreSQL 15 | внутренний |
| bot | Telegram-бот (aiogram) | — |
| web | FastAPI веб-интерфейс | 8001→8000 |

## Ключевые файлы
- `main.py` — Telegram-бот (команды, хендлеры, планировщик)
- `web_app.py` — FastAPI приложение (веб-интерфейс)
- `web_server.py` — вспомогательный серверный код
- `ozon_api.py` — работа с Ozon API (заказы, сборка)
- `database.py` — модели БД и async-сессии (Order, Product, FboSalesWatch…)
- `analytics.py` — аналитический модуль OzonAnalytics
- `content_sync.py` — синхронизация контента
- `templates/` — Jinja2 шаблоны (dashboard, queue, unit_economics…)
- `static/js/` — JavaScript фронтенда
- `.env` — секреты (не в git)

## Секреты (.env)
- `BOT_TOKEN` — токен Telegram-бота
- `DB_USER`, `DB_PASS`, `DB_NAME` — подключение к PostgreSQL
- Ozon API ключи

## Workflow после любых изменений

### 1. Коммит и пуш на GitHub (с сервера)
```bash
cd ~/sima_bot
git add -A
git commit -m "feat/fix: описание изменений"
git push origin main
```

### 2. Пулл на локальном ПК
```bash
cd "/Users/pavellyashenko/Documents/Py projects/SimaBot"
git pull origin main
```

### 3. Пересборка Docker на сервере
```bash
cd ~/sima_bot

# Только бот:
docker compose build --no-cache bot && docker compose up -d bot

# Только веб:
docker compose build --no-cache web && docker compose up -d web

# Всё сразу:
docker compose build --no-cache && docker compose up -d
```

## Что не трогать
- `.env` — секреты, не коммитить
- `pgdata` volume — данные PostgreSQL
- `bot_database.db` — SQLite (если используется), не коммитить
- `__pycache__/`, `venv/` — не коммитить
