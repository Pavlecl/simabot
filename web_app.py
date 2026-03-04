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
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, Request, Form, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

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
    """Статистика для дашборда"""
    # Всего заказов
    total = await db.execute(select(func.count(Order.posting_number)))
    total_count = total.scalar()

    # По статусам
    statuses_q = await db.execute(
        select(Order.ozon_status, func.count(Order.posting_number))
        .group_by(Order.ozon_status)
    )
    statuses = {row[0]: row[1] for row in statuses_q.all()}

    # Виртуальных (ждут ручной сборки)
    virtual_q = await db.execute(select(func.count(VirtualOrder.posting_number)))
    virtual_count = virtual_q.scalar()

    # Заказов за последние 7 дней
    week_ago = datetime.now() - timedelta(days=7)
    recent_q = await db.execute(
        select(func.count(Order.posting_number))
        .where(Order.added_at >= week_ago)
    )
    recent_count = recent_q.scalar()

    # Заказы без СУР (не обработаны фулфилментом)
    no_sur_q = await db.execute(
        select(func.count(Order.posting_number))
        .where(Order.sur_number == None)
        .where(Order.ozon_status.in_(['awaiting_packaging', 'awaiting_deliver', 'processing']))
    )
    no_sur_count = no_sur_q.scalar()

    return {
        "total": total_count,
        "statuses": statuses,
        "virtual": virtual_count,
        "recent_7d": recent_count,
        "no_sur": no_sur_count,
    }


@app.get("/api/orders")
async def get_orders(
    user: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    page: int = 1,
    per_page: int = 50,
    status: str = "",
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

    if status:
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
    if status:
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
    yield

app.router.lifespan_context = lifespan


if __name__ == "__main__":
    import uvicorn
    # reload=True — при изменении файлов сервер перезапускается автоматически.
    # Удобно для разработки, НЕ использовать в production!
    uvicorn.run("web_app:app", host="0.0.0.0", port=8000, reload=True)