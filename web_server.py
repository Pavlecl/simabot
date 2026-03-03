from fastapi import FastAPI, Request, Depends, Form, HTTPException, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.future import select
from sqlalchemy import update
from database import AsyncSessionLocal, User, Order, init_db
import uvicorn
import json

app = FastAPI()
templates = Jinja2Templates(directory="templates")


# Зависимость для получения сессии БД
async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


# --- Простая авторизация (Mock) ---
# В реальном проекте используй хеширование паролей (bcrypt)!
async def get_current_user(request: Request, db=Depends(get_db)):
    username = request.cookies.get("user")
    if not username:
        return None
    result = await db.execute(select(User).where(User.username == username))
    return result.scalars().first()


@app.on_event("startup")
async def startup():
    await init_db()
    # Создадим тестовых юзеров, если их нет
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(User))
        if not res.scalars().first():
            db.add(User(username="admin", password_hash="admin123", role="admin"))
            db.add(User(username="ff", password_hash="ff123", role="fulfillment"))
            await db.commit()


# --- Страницы ---

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login")
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...), db=Depends(get_db)):
    result = await db.execute(select(User).where(User.username == username, User.password_hash == password))
    user = result.scalars().first()
    if user:
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie(key="user", value=username)
        return response
    return templates.TemplateResponse("login.html", {"request": request, "error": "Неверный логин или пароль"})


@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/login")
    response.delete_cookie("user")
    return response


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db=Depends(get_db), user=Depends(get_current_user)):
    if not user:
        return RedirectResponse(url="/login")

    # Фильтр: только активные заказы
    query = select(Order).where(Order.ozon_status.in_(["awaiting_packaging", "ready_to_ship"]))
    result = await db.execute(query)
    orders = result.scalars().all()

    # Преобразуем JSON товаров в строку для вывода
    display_orders = []
    for o in orders:
        products = json.loads(o.products_json) if o.products_json else []
        product_str = "<br>".join([f"{p.get('sku')}: {p.get('name')} ({p.get('quantity')} шт)" for p in products])
        display_orders.append({**o.__dict__, "product_str": product_str})

    return templates.TemplateResponse("index.html", {
        "request": request,
        "orders": display_orders,
        "user": user
    })


# --- API для обновления данных (AJAX) ---
@app.post("/update_order")
async def update_order(
        posting_number: str = Form(...),
        field: str = Form(...),
        value: str = Form(...),
        user=Depends(get_current_user),
        db=Depends(get_db)
):
    if not user: return {"error": "Unauthorized"}

    # Проверка прав
    if user.role == "fulfillment" and field != "comment":
        return {"error": "Forbidden"}

    # Обновляем поле динамически
    update_data = {field: value}
    await db.execute(update(Order).where(Order.posting_number == posting_number).values(**update_data))
    await db.commit()
    return {"status": "ok"}
