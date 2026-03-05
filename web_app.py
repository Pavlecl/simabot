"""
web_app.py — Веб-интерфейс для управления заказами

📚 УРОК: Структура FastAPI-приложения
--------------------------------------
FastAPI строится на двух типах маршрутов (routes):
  1. Страницы (HTML) — возвращают HTMLResponse через Jinja2-шаблоны
  2. API-эндпоинты (JSON) — возвращают dict/list, используются JS-кодом на странице

Авторизация работает так:
  - POST /login → проверяет логин/пароль → создаёт JWT → кладёт в httpOnly cookie
  - httpOnly = JS не может прочитать cookie (защита от XSS-атак)
  - Каждый запрос → FastAPI читает cookie → декодирует JWT → знает кто вы
"""

import os
import json
import aiohttp
from datetime import datetime, timedelta, date, timezone
from typing import Optional

from fastapi import FastAPI, Request, Form, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from database import AsyncSessionLocal, User, Order, VirtualOrder, init_db

# --- КОНФИГУРАЦИЯ ---
# SECRET_KEY используется для подписи JWT. Если утечёт — злоумышленник сможет
# создать поддельный токен с любой ролью. Поэтому храним в .env, не в коде.
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "ЗАМЕНИТЕ_ЭТО_НА_СЛУЧАЙНУЮ_СТРОКУ_В_ENV")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 12

# CryptContext — менеджер хэширования паролей.
# bcrypt автоматически добавляет "соль" (случайные данные), поэтому
# одинаковые пароли дают разные хэши. Это защита от радужных таблиц.
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

app = FastAPI(title="SimaBot Dashboard")
templates = Jinja2Templates(directory="templates")

# --- OZON API КОНФИГУРАЦИЯ ---
OZON_CLIENT_ID = os.getenv("OZON_CLIENT_ID", "")
OZON_API_KEY = os.getenv("OZON_API_KEY", "")
try:
    OZON_WAREHOUSE_ID = int(os.getenv("OZON_WAREHOUSE_ID", "0"))
except:
    OZON_WAREHOUSE_ID = 0

OZON_HEADERS = {
    "Client-Id": OZON_CLIENT_ID,
    "Api-Key": OZON_API_KEY,
    "Content-Type": "application/json"
}

# Храним время последней синхронизации
_last_sync: Optional[datetime] = None


async def fetch_ozon_postings(statuses: list) -> list:
    """Получает отправления с Ozon по списку статусов."""
    url = "https://api-seller.ozon.ru/v3/posting/fbs/list"
    date_to = datetime.now() + timedelta(days=1)
    date_from = date_to - timedelta(days=60)
    all_postings = []

    async with aiohttp.ClientSession() as session:
        for status in statuses:
            payload = {
                "dir": "ASC",
                "filter": {
                    "status": status,
                    "warehouse_id": [OZON_WAREHOUSE_ID],
                    "since": date_from.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                    "to": date_to.strftime("%Y-%m-%dT%H:%M:%S.000Z")
                },
                "limit": 1000,
                "with": {"analytics_data": False, "financial_data": False}
            }
            try:
                async with session.post(url, json=payload, headers=OZON_HEADERS) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        all_postings.extend(data.get("result", {}).get("postings", []))
            except Exception as e:
                pass

    return all_postings


async def fetch_images_for_skus(skus: list) -> dict:
    """Батч-запрос фото по SKU через /v3/product/info/list.
    Ответ: {"items": [{..., "sources": [{"sku": 123}], "primary_image": [...]}]}
    SKU находится в sources[].sku, а не на верхнем уровне.
    """
    result = {}
    url = "https://api-seller.ozon.ru/v3/product/info/list"
    async with aiohttp.ClientSession() as session:
        for i in range(0, len(skus), 100):
            batch = skus[i:i+100]
            try:
                async with session.post(url, json={"sku": batch}, headers=OZON_HEADERS) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        # Ответ: {"items": [...]} без обёртки result
                        for item in data.get("items", []):
                            images = item.get("primary_image", [])
                            if not images:
                                images = item.get("images", [])
                            image_url = images[0] if isinstance(images, list) and images else ""
                            if not image_url:
                                continue
                            # SKU лежит в sources — берём все и маппим
                            for source in item.get("sources", []):
                                sku = source.get("sku")
                                if sku:
                                    result[int(sku)] = image_url
            except Exception as e:
                import logging
                logging.warning(f"fetch_images_for_skus error: {e}")
    return result


async def sync_from_ozon() -> dict:
    """
    Основная функция синхронизации: получает свежие данные с Ozon,
    обновляет/добавляет заказы в БД, возвращает статистику.

    📚 УРОК: Upsert (INSERT ... ON CONFLICT DO UPDATE)
    Позволяет одной командой вставить запись или обновить если она уже есть.
    Это атомарная операция — не нужно делать SELECT перед INSERT.
    """
    global _last_sync

    postings = await fetch_ozon_postings(["awaiting_packaging", "awaiting_deliver"])

    if not postings:
        return {"synced": 0, "error": "Нет данных от Ozon или ошибка API"}

    # Собираем все SKU для батч-запроса фото
    all_skus = []
    for p in postings:
        for prod in p.get("products", []):
            if prod.get("sku"):
                all_skus.append(int(prod["sku"]))

    sku_images = {}
    if all_skus:
        sku_images = await fetch_images_for_skus(list(set(all_skus)))

    # Множество актуальных номеров отправлений от Ozon
    active_posting_numbers = {p.get("posting_number") for p in postings}

    def parse_dt(s):
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.rstrip("Z").split(".")[0])
        except:
            return None

    async with AsyncSessionLocal() as db:
        # 1. Upsert активных заказов
        for p in postings:
            posting_number = p.get("posting_number")
            ozon_status = p.get("status", "unknown")
            shipment_date = p.get("shipment_date")
            in_process_at = p.get("in_process_at")

            products = []
            for prod in p.get("products", []):
                sku = int(prod["sku"]) if prod.get("sku") else None
                products.append({
                    "offer_id": prod.get("offer_id"),
                    "sku": sku,
                    "name": prod.get("name"),
                    "quantity": prod.get("quantity"),
                    "price": prod.get("price", "0"),
                    "image_url": sku_images.get(sku, "") if sku else ""
                })

            stmt = pg_insert(Order).values(
                posting_number=posting_number,
                ozon_status=ozon_status,
                products_json=json.dumps(products, ensure_ascii=False),
                ozon_accepted_at=parse_dt(in_process_at),
                ozon_created_at=parse_dt(shipment_date),
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["posting_number"],
                set_={
                    "ozon_status": ozon_status,
                    "products_json": json.dumps(products, ensure_ascii=False),
                    "ozon_accepted_at": parse_dt(in_process_at),
                    "ozon_created_at": parse_dt(shipment_date),
                }
            )
            await db.execute(stmt)

        # 2. Заказы которые были активными в нашей БД но пропали у Ozon —
        #    значит отменены/доставлены. Помечаем их соответствующим статусом.
        #    Запрашиваем актуальный статус каждого через /v3/posting/fbs/get
        stale_q = await db.execute(
            select(Order.posting_number)
            .where(Order.ozon_status.in_(["awaiting_packaging", "awaiting_deliver"]))
        )
        stale_postings = [
            row[0] for row in stale_q.all()
            if row[0] not in active_posting_numbers
        ]

        if stale_postings:
            url = "https://api-seller.ozon.ru/v3/posting/fbs/get"
            async with aiohttp.ClientSession() as check_session:
                for p_num in stale_postings:
                    try:
                        async with check_session.post(
                            url,
                            json={"posting_number": p_num},
                            headers=OZON_HEADERS
                        ) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                real_status = data.get("result", {}).get("status", "unknown")
                                await db.execute(
                                    Order.__table__.update()
                                    .where(Order.posting_number == p_num)
                                    .values(ozon_status=real_status)
                                )
                    except Exception as e:
                        import logging
                        logging.warning(f"sync stale check error {p_num}: {e}")

        await db.commit()

    _last_sync = datetime.now()
    removed = len(stale_postings) if stale_postings else 0
    return {
        "synced": len(postings),
        "removed": removed,
        "last_sync": _last_sync.strftime("%d.%m.%Y %H:%M")
    }


# --- ЗАВИСИМОСТИ (Dependency Injection) ---
# 📚 УРОК: Depends() в FastAPI
# Вместо того чтобы копировать код проверки токена в каждый маршрут,
# мы выносим его в отдельную функцию и подключаем через Depends().
# FastAPI сам вызовет её перед вызовом нашего обработчика.

async def get_db() -> AsyncSession:
    """Открывает сессию БД и закрывает её после запроса (паттерн 'контекстный менеджер')"""
    async with AsyncSessionLocal() as session:
        yield session


def create_access_token(data: dict) -> str:
    """Создаёт JWT токен с данными пользователя и сроком жизни"""
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    to_encode.update({"exp": expire})
    # jwt.encode() шифрует данные с помощью SECRET_KEY + алгоритма HS256
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(request: Request) -> Optional[dict]:
    """
    Читает JWT из cookie и возвращает данные пользователя.
    Возвращает None если токена нет или он невалидный.
    """
    token = request.cookies.get("access_token")
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return {"username": payload.get("sub"), "role": payload.get("role")}
    except JWTError:
        # Токен поддельный, истёкший или повреждённый
        return None


def require_admin(request: Request) -> dict:
    """Depends-функция: требует роль admin, иначе редирект на логин"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Только для администратора")
    return user


def require_any_role(request: Request) -> dict:
    """Depends-функция: требует любую авторизацию (admin или fulfillment)"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user


# --- СТРАНИЦЫ (HTML) ---

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db)
):
    """
    📚 УРОК: Form(...) означает что данные ожидаются из HTML-формы (application/x-www-form-urlencoded),
    а не из JSON-тела запроса. Для JSON используется Pydantic BaseModel.
    """
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalars().first()

    # pwd_context.verify() проверяет пароль против хэша в БД
    # Важно: не сравниваем строки напрямую! Это защита от timing-атак
    if not user or not pwd_context.verify(password, user.password_hash):
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Неверный логин или пароль"
        })

    token = create_access_token({"sub": user.username, "role": user.role})

    # Редиректим на нужную страницу в зависимости от роли
    redirect_url = "/" if user.role == "admin" else "/queue"
    response = RedirectResponse(redirect_url, status_code=302)

    # httpOnly=True — JS не может читать эту cookie (защита от XSS)
    # samesite="lax" — cookie не отправляется при запросах с других сайтов (защита от CSRF)
    response.set_cookie("access_token", token, httponly=True, samesite="lax",
                        max_age=ACCESS_TOKEN_EXPIRE_HOURS * 3600)
    return response


@app.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie("access_token")
    return response


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, user: dict = Depends(require_admin)):
    """Главная страница — дашборд для администратора"""
    return templates.TemplateResponse("app.html", {
        "request": request,
        "user": user,
        "active_tab": "dashboard"
    })


@app.get("/orders", response_class=HTMLResponse)
async def orders_page(request: Request, user: dict = Depends(require_admin)):
    return templates.TemplateResponse("app.html", {
        "request": request,
        "user": user,
        "active_tab": "orders"
    })


@app.get("/queue", response_class=HTMLResponse)
async def queue_page(request: Request, user: dict = Depends(require_any_role)):
    """Очередь на сборку — доступна и фулфилменту, и администратору"""
    return templates.TemplateResponse("app.html", {
        "request": request,
        "user": user,
        "active_tab": "queue"
    })


# --- API ENDPOINTS (JSON) ---
# 📚 УРОК: Почему API отдельно от HTML-страниц?
# Страница загружается один раз. Потом JS делает fetch() к API-эндпоинтам,
# чтобы обновить данные без перезагрузки. Это называется SPA (Single Page App) паттерн.

@app.get("/api/stats")
async def get_stats(
    user: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    Статистика для дашборда.
    Логика:
    - Всего активных = awaiting_packaging + awaiting_deliver
    - Отправка сегодня = plan_delivery_date == сегодня И статус активный
    - Просроченные = plan_delivery_date < сегодня И sur_number пустой И статус активный
    """
    today = date.today()
    today_iso = today.isoformat()          # "2026-03-05" для сравнения ISO-дат
    today_dot = today.strftime("%d.%m.%Y") # "05.03.2026" для сравнения дд.мм.гггг
    active_statuses = ["awaiting_packaging", "awaiting_deliver"]

    # Вспомогательная функция: конвертирует дд.мм.гггг → гггг-мм-дд для корректного сравнения
    # На уровне SQL используем CASE для обоих форматов
    from sqlalchemy import case, cast, and_
    from sqlalchemy.sql.expression import literal

    def to_iso_expr(col):
        """
        Конвертирует строковую дату любого формата в DATE через PostgreSQL.
        Форматы в БД:
          - '5.03.26'   → d.mm.yy  (короткий, от бота)
          - '06.03.2026' → dd.mm.yyyy (полный)
          - '2026-03-05' → ISO (от сайта)
        Используем to_date() с нужным форматом через CASE по длине строки.
        """
        from sqlalchemy import func as sqlfunc
        return sqlfunc.to_date(
            case(
                # ISO формат yyyy-mm-dd (10 символов с дефисами)
                (col.op('~')(r'^\d{4}-\d{2}-\d{2}$'), col),
                # Полный дд.мм.гггг (10 символов: 06.03.2026)
                (col.op('~')(r'^\d{2}\.\d{2}\.\d{4}$'),
                 sqlfunc.concat(
                     sqlfunc.substring(col, 7, 4), '-',
                     sqlfunc.substring(col, 4, 2), '-',
                     sqlfunc.substring(col, 1, 2)
                 )),
                # Короткий д.мм.гг или дд.мм.гг (6-8 символов, год 2 цифры)
                # '5.03.26' → день может быть 1 цифра, год 2 цифры → добавляем '20' к году
                else_=sqlfunc.concat(
                    '20',
                    sqlfunc.split_part(col, '.', 3), '-',
                    sqlfunc.lpad(sqlfunc.split_part(col, '.', 2), 2, '0'), '-',
                    sqlfunc.lpad(sqlfunc.split_part(col, '.', 1), 2, '0')
                )
            ),
            literal('YYYY-MM-DD')
        )

    today_date_expr = today  # Python date object для сравнения

    def overdue_filter():
        return and_(
            Order.ozon_status.in_(active_statuses),
            Order.plan_delivery_date != None,
            Order.plan_delivery_date != "",
            or_(Order.sur_number == None, Order.sur_number == ""),
            to_iso_expr(Order.plan_delivery_date) < today_date_expr
        )

    def today_filter():
        return and_(
            Order.ozon_status.in_(active_statuses),
            Order.plan_delivery_date != None,
            Order.plan_delivery_date != "",
            to_iso_expr(Order.plan_delivery_date) == today_date_expr
        )

    # Всего активных заказов
    total_q = await db.execute(
        select(func.count(Order.posting_number))
        .where(Order.ozon_status.in_(active_statuses))
    )
    total_count = total_q.scalar()

    # Отправка сегодня
    today_q = await db.execute(
        select(func.count(Order.posting_number)).where(today_filter())
    )
    today_count = today_q.scalar()

    # Просроченные: дата поставки < сегодня И СУР не заполнен
    overdue_q = await db.execute(
        select(func.count(Order.posting_number)).where(overdue_filter())
    )
    overdue_count = overdue_q.scalar()

    # Виртуальных (ручная сборка)
    virtual_q = await db.execute(select(func.count(VirtualOrder.posting_number)))
    virtual_count = virtual_q.scalar()

    # Просроченные заказы для таблицы на дашборде
    overdue_orders_q = await db.execute(
        select(Order)
        .where(overdue_filter())
        .order_by(to_iso_expr(Order.plan_delivery_date).asc())
        .limit(50)
    )
    overdue_orders = overdue_orders_q.scalars().all()

    return {
        "total": total_count,
        "today": today_count,
        "overdue": overdue_count,
        "virtual": virtual_count,
        "last_sync": _last_sync.strftime("%d.%m.%Y %H:%M") if _last_sync else None,
        "overdue_orders": [
            {
                "posting_number": o.posting_number,
                "ozon_status": o.ozon_status,
                "plan_delivery_date": o.plan_delivery_date,
                "sima_order_number": o.sima_order_number,
                "sur_number": o.sur_number,
                "ozon_accepted_at": o.ozon_accepted_at.isoformat() if o.ozon_accepted_at else None,
                "products": json.loads(o.products_json) if o.products_json else [],
            }
            for o in overdue_orders
        ]
    }


@app.get("/api/orders")
async def get_orders(
    user: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    page: int = 1,
    per_page: int = 50,
    status: str = "active",
    search: str = "",
    date_from: str = "",
    date_to: str = "",
):
    """
    📚 УРОК: Пагинация + фильтрация
    Параметры приходят из query string: /api/orders?page=1&status=...&search=...
    SQLAlchemy позволяет цепочкой добавлять .where() — они объединяются через AND.
    """
    offset = (page - 1) * per_page
    query = select(Order).order_by(Order.added_at.desc())

    ACTIVE_STATUSES = ["awaiting_packaging", "awaiting_deliver"]
    if status == "active":
        query = query.where(Order.ozon_status.in_(ACTIVE_STATUSES))
    elif status:
        query = query.where(Order.ozon_status == status)
    if search:
        # ilike — регистронезависимый LIKE. % — любое количество символов.
        # Ищем по номеру отправления ИЛИ по содержимому products_json (артикулы)
        query = query.where(
            Order.posting_number.ilike(f"%{search}%") |
            Order.products_json.ilike(f"%{search}%")
        )
    if date_from:
        query = query.where(Order.plan_delivery_date >= date_from)
    if date_to:
        query = query.where(Order.plan_delivery_date <= date_to)

    # Считаем total с теми же фильтрами
    count_query = select(func.count(Order.posting_number))
    if status == "active":
        count_query = count_query.where(Order.ozon_status.in_(ACTIVE_STATUSES))
    elif status:
        count_query = count_query.where(Order.ozon_status == status)
    if search:
        count_query = count_query.where(
            Order.posting_number.ilike(f"%{search}%") |
            Order.products_json.ilike(f"%{search}%")
        )
    if date_from:
        count_query = count_query.where(Order.plan_delivery_date >= date_from)
    if date_to:
        count_query = count_query.where(Order.plan_delivery_date <= date_to)

    total = (await db.execute(count_query)).scalar()

    result = await db.execute(query.offset(offset).limit(per_page))
    orders = result.scalars().all()

    import json
    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "orders": [
            {
                "posting_number": o.posting_number,
                "ozon_status": o.ozon_status,
                "sima_order_number": o.sima_order_number,
                "sima_order_date": o.sima_order_date,
                "plan_delivery_date": o.plan_delivery_date,
                "sur_number": o.sur_number,
                "ff_delivery_date": o.ff_delivery_date.isoformat() if o.ff_delivery_date else None,
                "comment": o.comment,
                "added_at": o.added_at.isoformat() if o.added_at else None,
                "products": json.loads(o.products_json) if o.products_json else [],
                "ozon_accepted_at": o.ozon_accepted_at.isoformat() if o.ozon_accepted_at else None,
            }
            for o in orders
        ]
    }


@app.patch("/api/orders/{posting_number}")
async def update_order(
    posting_number: str,
    request: Request,
    user: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    📚 УРОК: HTTP-методы
    GET — получить данные
    POST — создать новую запись
    PATCH — частично обновить запись (только переданные поля)
    PUT — полностью заменить запись
    DELETE — удалить

    Мы используем PATCH, потому что фулфилмент обновляет только СУР и дату,
    а не весь объект заказа.
    """
    body = await request.json()

    result = await db.execute(select(Order).where(Order.posting_number == posting_number))
    order = result.scalars().first()
    if not order:
        raise HTTPException(status_code=404, detail="Заказ не найден")

    # Обновляем только переданные поля (PATCH-семантика)
    if "sur_number" in body:
        order.sur_number = body["sur_number"]
    if "ff_delivery_date" in body and body["ff_delivery_date"]:
        try:
            order.ff_delivery_date = datetime.fromisoformat(body["ff_delivery_date"])
        except ValueError:
            raise HTTPException(status_code=422, detail="Неверный формат даты")
    if "comment" in body:
        order.comment = body["comment"]
    if "plan_delivery_date" in body:
        order.plan_delivery_date = body["plan_delivery_date"]
    if "sima_order_number" in body:
        order.sima_order_number = body["sima_order_number"]

    await db.commit()
    return {"ok": True, "posting_number": posting_number}


@app.get("/api/queue")
async def get_queue(
    user: dict = Depends(require_any_role),
    db: AsyncSession = Depends(get_db)
):
    """
    Очередь на ручную сборку — виртуальные заказы с деталями.
    Доступна фулфилменту.
    """
    import json

    # JOIN: берём виртуальные заказы и подтягиваем детали из таблицы orders
    # 📚 УРОК: outerjoin — LEFT JOIN в SQL.
    # Возвращает все строки из VirtualOrder, даже если в Order нет совпадения.
    from sqlalchemy.orm import aliased
    query = (
        select(VirtualOrder, Order)
        .outerjoin(Order, VirtualOrder.posting_number == Order.posting_number)
        .order_by(Order.plan_delivery_date.asc().nullslast())
    )
    result = await db.execute(query)
    rows = result.all()

    queue = []
    for virtual, order in rows:
        products = []
        if order and order.products_json:
            try:
                products = json.loads(order.products_json)
            except Exception:
                pass
        queue.append({
            "posting_number": virtual.posting_number,
            "added_at": virtual.added_at.isoformat() if virtual.added_at else None,
            "sima_order_number": order.sima_order_number if order else None,
            "plan_delivery_date": order.plan_delivery_date if order else None,
            "sur_number": order.sur_number if order else None,
            "comment": order.comment if order else None,
            "products": products,
        })

    return {"queue": queue, "total": len(queue)}


# --- УТИЛИТЫ ДЛЯ ПЕРВОГО ЗАПУСКА ---

@app.post("/api/setup/create-admin")
async def create_admin(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Создаёт первого администратора. Работает только если в БД нет ни одного пользователя.
    После создания первого admin — эндпоинт больше не работает (защита).

    📚 УРОК: Такой эндпоинт называется "bootstrap" или "setup route".
    В production его часто удаляют или защищают отдельным секретом.
    """
    existing = await db.execute(select(func.count(User.id)))
    if existing.scalar() > 0:
        raise HTTPException(status_code=403, detail="Пользователи уже созданы")

    body = await request.json()
    username = body.get("username", "admin")
    password = body.get("password")
    if not password:
        raise HTTPException(status_code=422, detail="Нужен пароль")

    hashed = pwd_context.hash(password)
    admin = User(username=username, password_hash=hashed, role="admin")
    db.add(admin)
    await db.commit()
    return {"ok": True, "message": f"Администратор '{username}' создан"}


@app.post("/api/setup/create-fulfillment")
async def create_fulfillment_user(
    request: Request,
    user: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Создаёт аккаунт для сотрудника фулфилмента. Только admin."""
    body = await request.json()
    username = body.get("username")
    password = body.get("password")
    if not username or not password:
        raise HTTPException(status_code=422, detail="Нужны username и password")

    existing = await db.execute(select(User).where(User.username == username))
    if existing.scalars().first():
        raise HTTPException(status_code=409, detail="Пользователь уже существует")

    hashed = pwd_context.hash(password)
    ff_user = User(username=username, password_hash=hashed, role="fulfillment")
    db.add(ff_user)
    await db.commit()
    return {"ok": True, "message": f"Пользователь '{username}' создан"}


@app.post("/api/sync")
async def api_sync(user: dict = Depends(require_admin)):
    """
    Синхронизация с Ozon API по запросу пользователя.
    Вызывается кнопкой 'Обновить' на сайте.
    """
    result = await sync_from_ozon()
    return result


@app.get("/api/sync/status")
async def api_sync_status(user: dict = Depends(require_admin)):
    """Возвращает время последней синхронизации."""
    return {
        "last_sync": _last_sync.strftime("%d.%m.%Y %H:%M") if _last_sync else None
    }


@app.get("/users", response_class=HTMLResponse)
async def users_page(request: Request, user: dict = Depends(require_admin)):
    """Страница управления пользователями — только для admin"""
    return templates.TemplateResponse("app.html", {
        "request": request,
        "user": user,
        "active_tab": "users"
    })


@app.get("/api/users")
async def get_users(
    user: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    Список всех пользователей системы.
    📚 УРОК: Никогда не возвращаем password_hash клиенту — только нужные поля.
    """
    result = await db.execute(select(User).order_by(User.id))
    users = result.scalars().all()
    return {
        "users": [
            {
                "id": u.id,
                "username": u.username,
                "role": u.role,
                # created_at нет в модели — добавим None, фронт обработает
                "created_at": None,
            }
            for u in users
        ]
    }


# --- ЗАПУСК ---
# 📚 УРОК: lifespan — современный способ запускать код при старте/остановке приложения.
# Вместо @app.on_event("startup") используем async context manager.
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # Синхронизируемся с Ozon при старте (в фоне, не блокируем запуск)
    import asyncio
    asyncio.create_task(sync_from_ozon())
    yield

app.router.lifespan_context = lifespan


if __name__ == "__main__":
    import uvicorn
    # reload=True — при изменении файлов сервер перезапускается автоматически.
    # Удобно для разработки, НЕ использовать в production!
    uvicorn.run("web_app:app", host="0.0.0.0", port=8000, reload=True)