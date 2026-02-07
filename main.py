import os
import asyncio
import logging
import pandas as pd
from datetime import datetime, timedelta
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    FSInputFile,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.base import JobLookupError
from database import init_db, get_virtual_orders_full, clear_virtual_orders

# Импортируем наши функции
from ozon_api import get_new_orders, assemble_orders

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

logging.basicConfig(level=logging.INFO)

# Идентификатор задачи напоминания
REMINDER_JOB_ID = "assembly_reminder"


# --- Клавиатуры ---

def get_main_kb():
    kb = [
        [KeyboardButton(text="Получить заказы Сима")],
        [KeyboardButton(text="📂 Виртуальные заказы")],
        [KeyboardButton(text="Проверить заказ")],
        [KeyboardButton(text="🔕 Остановить напоминания")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


def get_assemble_kb():
    buttons = [[InlineKeyboardButton(text="📦 Собрать поставку", callback_data="assemble_all")]]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# --- Логика сравнения (ВОССТАНОВЛЕНО) ---

async def compare_orders_with_file(file_path):
    orders = await get_new_orders()
    if not orders:
        return "❌ На Ozon сейчас нет заказов в статусе 'Ожидает упаковки'."

    needed_totals = {}
    for order in orders:
        for p in order['products']:
            art = str(p['offer_id']).strip()
            qty = int(p['quantity'])
            needed_totals[art] = needed_totals.get(art, 0) + qty

    try:
        # Автоматическое определение: CSV или Excel
        if file_path.endswith('.csv'):
            # Читаем CSV (Сима-ленд обычно использует кодировку utf-8 или cp1251)
            df_raw = pd.read_csv(file_path, header=None, encoding='utf-8')
        else:
            df_raw = pd.read_excel(file_path, header=None)

        # Поиск строки-заголовка
        header_idx = None
        for i, row in df_raw.iterrows():
            if "Артикул" in row.values:
                header_idx = i
                break

        if header_idx is None:
            return "❌ Не удалось найти колонку 'Артикул' в файле."

        # Перечитываем данные с правильного места
        if file_path.endswith('.csv'):
            df = pd.read_csv(file_path, skiprows=header_idx, encoding='utf-8')
        else:
            df = pd.read_excel(file_path, skiprows=header_idx)

        # Очистка: убираем пустые строки и строку "Итого"
        df = df.dropna(subset=["Артикул", "Количество"])

        # Безопасное преобразование артикула в строку без ".0"
        def clean_art(val):
            try:
                # Если это число (float/int), превращаем в int, потом в str
                return str(int(float(val)))
            except:
                return str(val).strip()

        df["Артикул"] = df["Артикул"].apply(clean_art)
        df["Количество"] = pd.to_numeric(df["Количество"], errors='coerce').fillna(0).astype(int)

        # Исключаем строку "Итого", если она попала в данные
        df = df[df["Артикул"].str.lower() != "итого"]

        found_in_file = df.groupby("Артикул")["Количество"].sum().to_dict()

    except Exception as e:
        return f"❌ Ошибка при чтении файла: {e}"

    # 3. Сравниваем и формируем отчет в новом формате
    discrepancies = []
    all_articles = set(needed_totals.keys()) | set(found_in_file.keys())

    for art in sorted(all_articles):
        needed = needed_totals.get(art, 0)
        found = found_in_file.get(art, 0)

        if needed != found:
            # Считаем разницу для удобства
            diff = abs(found - needed)

            if needed > 0 and found == 0:
                discrepancies.append(f"🔴 `{art}`: Отсутствует (нужно {needed} шт.)")
            elif needed == 0 and found > 0:
                discrepancies.append(f"🟡 `{art}`: Лишний в файле ({found} шт., в заказах нет)")
            elif found > needed:
                # Формат: ⚠️ 7315480: В файле 4 шт. (нужно 1). Лишних: 3
                discrepancies.append(f"⚠️ `{art}`: В файле {found} шт. (нужно {needed}). Лишних: {diff}")
            elif found < needed:
                discrepancies.append(f"📉 `{art}`: В файле {found} шт. (нужно {needed}). Не хватает: {diff}")

    if not discrepancies:
        return "✅ **Проверка пройдена!**\nВсе количества в файле совпадают с заказами Ozon."

    return "📊 **Результат проверки:**\n\n" + "\n".join(discrepancies)


# --- Логика напоминаний ---

async def send_reminder():
    await bot.send_message(
        ADMIN_ID,
        "⚠️ **Напоминание!**\nВы еще не собрали поставку. Нажмите кнопку в сообщении с Excel или кнопку в меню ниже.",
        parse_mode="Markdown"
    )


def stop_reminder():
    try:
        scheduler.remove_job(REMINDER_JOB_ID)
        logging.info("Напоминания успешно отключены.")
        return True
    except JobLookupError:
        return False


# --- Основная логика отчетов ---

async def send_order_report(is_auto=False):
    orders = await get_new_orders()

    if not orders:
        if not is_auto:
            await bot.send_message(ADMIN_ID, "✅ Новых заказов нет.")
        stop_reminder()
        return

    excel_data = []
    articul_list = []
    for order in orders:
        for p in order['products']:
            excel_data.append({
                "Номер заказа": order['number'],
                "Дата отгрузки": order['ship_date'],
                "Название товара": p['name'],
                "Артикул (Sima)": p['offer_id'],
                "Количество": p['quantity'],
                "Цена": round(float(p['price']), 2) if p['price'] else 0
            })
            for _ in range(int(p['quantity'])):
                articul_list.append(str(p['offer_id']))

    date_str = datetime.now().strftime("%d.%m.%Y_%H-%M")
    file_name = f"Заказы_{date_str}.xlsx"
    pd.DataFrame(excel_data).to_excel(file_name, index=False)

    sima_basket_string = " ".join(articul_list)
    mode_header = "⏰ **АВТО-ОТЧЕТ**" if is_auto else "🔍 **РУЧНОЙ ЗАПРОС**"

    summary_text = (
        f"{mode_header}\n"
        f"📦 **Новых заказов: {len(orders)}**\n\n"
        f"🛒 **Артикулы для Сима-Ленд:**\n"
        f"`{sima_basket_string}`"
    )

    await bot.send_message(ADMIN_ID, summary_text, parse_mode="Markdown")

    document = FSInputFile(file_name)
    await bot.send_document(
        ADMIN_ID,
        document,
        caption="Excel-отчет готов. Соберите поставку, когда будете готовы.",
        reply_markup=get_assemble_kb()
    )
    os.remove(file_name)

    stop_reminder()
    if is_auto:
        run_at = datetime.now() + timedelta(minutes=10)
        scheduler.add_job(
            send_reminder,
            'interval',
            minutes=10,
            id=REMINDER_JOB_ID,
            next_run_time=run_at
        )


# --- Обработчики команд ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("Бот готов к работе. Отчеты приходят в 13:00 и 22:30.", reply_markup=get_main_kb())


@dp.message(F.text == "Получить заказы Сима")
async def cmd_check_manual(message: types.Message):
    if str(message.from_user.id) == str(ADMIN_ID):
        await message.answer("🔎 Проверяю... (напоминания не будут включены)")
        await send_order_report(is_auto=False)


# --- НОВЫЕ ОБРАБОТЧИКИ ДЛЯ ПРОВЕРКИ (ВОССТАНОВЛЕНО) ---

@dp.message(F.text == "Проверить заказ")
async def cmd_verify_request(message: types.Message):
    if str(message.from_user.id) == str(ADMIN_ID):
        await message.answer("📥 Пожалуйста, пришлите Excel-файл от Сима-ленд для сверки артикулов.")


@dp.message(F.document)
async def handle_document(message: types.Message):
    if str(message.from_user.id) != str(ADMIN_ID):
        return

    # Создаем папку для временных файлов, если её нет
    os.makedirs("downloads", exist_ok=True)
    file_id = message.document.file_id
    file = await bot.get_file(file_id)
    file_path = file.file_path
    download_path = f"downloads/{message.document.file_name}"

    await bot.download_file(file_path, download_path)
    await message.answer("⏳ Сверяю данные...")

    try:
        report_text = await compare_orders_with_file(download_path)
        await message.answer(report_text, parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"❌ Произошла ошибка: {e}")
    finally:
        if os.path.exists(download_path):
            os.remove(download_path)


# ---------------------------------------------------

@dp.message(F.text == "🔕 Остановить напоминания")
async def cmd_stop_reminders_handler(message: types.Message):
    if stop_reminder():
        await message.answer("🔕 Напоминания отключены до следующего авто-отчета.")
    else:
        await message.answer("Напоминания и так не были активны.")


@dp.callback_query(F.data == "assemble_all")
async def process_assemble(callback: CallbackQuery):
    await callback.answer("Начинаю сборку...")
    result_text = await assemble_orders()
    await callback.message.answer(result_text)

    stop_reminder()
    await callback.message.edit_reply_markup(reply_markup=None)


@dp.message(F.text == "📂 Виртуальные заказы")
async def cmd_show_virtual_orders(message: types.Message):
    if str(message.from_user.id) != str(ADMIN_ID):
        return

    orders = await get_virtual_orders_full()

    if not orders:
        await message.answer(
            "📂 **Список виртуальных заказов пуст.**\nВсе заказы обрабатываются автоматически или уже отгружены.")
        return

    # Формируем красивый список
    text_lines = ["📋 **Заказы для ручной сборки (Многопозиционные):**\n"]

    for number, date_str in orders:
        # Отрезаем миллисекунды от времени, если они есть
        clean_date = date_str.split('.')[0]
        text_lines.append(f"📦 `{number}` (добавлен: {clean_date})")

    text_lines.append("\n_Эти заказы бот пропустил, чтобы вы собрали их вручную._")

    # Добавляем инлайн-кнопку для очистки, если вдруг нужно
    clear_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Очистить список", callback_data="clear_virtual_db")]
    ])

    await message.answer("\n".join(text_lines), parse_mode="Markdown", reply_markup=clear_kb)


@dp.callback_query(F.data == "clear_virtual_db")
async def process_clear_db(callback: CallbackQuery):
    await clear_virtual_orders()
    await callback.message.edit_text("🗑 **Список виртуальных заказов очищен.**")
    await callback.answer()


async def main():
    # Инициализация БД
    await init_db()

    scheduler.add_job(send_order_report, 'cron', hour=13, minute=0, kwargs={'is_auto': True})
    scheduler.add_job(send_order_report, 'cron', hour=22, minute=30, kwargs={'is_auto': True})

    scheduler.start()

    await bot.set_my_commands([
        types.BotCommand(command="start", description="Главное меню"),
        types.BotCommand(command="stop", description="Выключить напоминания")
    ])

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())