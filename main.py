import os
import asyncio
import logging
import html
import pandas as pd
import io
from datetime import datetime, timedelta
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, BufferedInputFile
)
# !!! ВАЖНО: Добавлен недостающий импорт для кнопок !!!
from aiogram.utils.keyboard import InlineKeyboardBuilder

from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Импорт ваших модулей
from database import init_db, get_order_details, get_virtual_orders_full, clear_virtual_orders
from ozon_api import get_new_orders, assemble_orders
from analytics import OzonAnalytics

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
# Если ADMIN_ID нужен как число, преобразуем:
try:
    ADMIN_ID = int(os.getenv("ADMIN_ID"))
except:
    ADMIN_ID = os.getenv("ADMIN_ID")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
analyzer = OzonAnalytics()

logging.basicConfig(level=logging.INFO)


# --- FSM (Машина состояний) ---
class AnalyticsStates(StatesGroup):
    waiting_fbs = State()
    waiting_fbo = State()
    waiting_search_query = State()
    waiting_check_file = State()


class AssemblyStates(StatesGroup):
    waiting_sima_order = State()
    waiting_supply_date = State()


# --- Клавиатуры ---
def get_main_kb():
    kb = [
        [KeyboardButton(text="Получить заказы Сима")],
        [KeyboardButton(text="Проверить заказ")],
        [KeyboardButton(text="📊 Аналитика")],
        [KeyboardButton(text="📂 Виртуальные заказы")]

    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


def get_analytics_kb():
    kb = [
        [KeyboardButton(text="📈 Отчет по заказам")],
        [KeyboardButton(text="🔍 История заказов")],
        [KeyboardButton(text="⬅️ Назад")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


# --- ГЛАВНЫЙ ФУНКЦИОНАЛ ---

@dp.message(F.text == "Получить заказы Сима")
async def cmd_get_orders(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("🔄 Запрашиваю данные у Ozon...")

    try:
        orders = await get_new_orders()

        if not orders:
            await message.answer("✅ Новых заказов нет.")
            return

        # Собираем уникальные артикулы
        all_articles = []
        for o in orders:
            for p in o['products']:
                qty = int(p.get('quantity', 1))
                art = str(p.get('offer_id') or p.get('sku'))
                # Добавляем артикул в список столько раз, сколько его заказали
                for _ in range(qty):
                    all_articles.append(art)

        # 2. Формируем текст: артикулы через ПРОБЕЛ
        art_text = " ".join(all_articles)

        # --- ТОТ САМЫЙ ФОРМАТ СООБЩЕНИЯ ---
        caption = (
            f"🔍 <b>РУЧНОЙ ЗАПРОС</b>\n"
            f"📦 Новых заказов: {len(orders)}\n\n"
            f"🛒 <b>Артикулы для Сима-Ленд:</b>\n"
            f"<code>{art_text}</code>"  # Тег code позволит скопировать всё одним кликом
        )

        # Отправляем текстовый блок
        await message.answer(caption, parse_mode="HTML")

        # --- ПОДГОТОВКА EXCEL И КНОПКИ СБОРКИ ---
        import pandas as pd
        import io

        data_for_excel = []
        for order in orders:
            for product in order['products']:
                data_for_excel.append({
                    "Номер заказа": order['number'],
                    "Дата отгрузки": order['ship_date'],
                    "Название": product['name'],
                    "Артикул": product.get('offer_id'),
                    "Количество": product['quantity'],
                    "Цена": product['price']
                })

        df = pd.DataFrame(data_for_excel)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False)
        output.seek(0)

        document = BufferedInputFile(output.read(), filename=f"Zakaz_Sima_{datetime.now().strftime('%d_%m')}.xlsx")

        # Кнопка для запуска диалога сборки
        builder = InlineKeyboardBuilder()
        builder.row(types.InlineKeyboardButton(
            text="📦 Собрать поставку",
            callback_data="start_assembly_dialog"
        ))

        await message.answer_document(
            document=document,
            caption="📂 Файл для импорта Ozon.\nПосле закупки нажмите кнопку ниже.",
            reply_markup=builder.as_markup()
        )

    except Exception as e:
        logging.error(f"Error in get_orders: {e}")
        await message.answer(f"❌ Произошла ошибка: {e}")


# --- ЛОГИКА СБОРКИ (ДИАЛОГ) ---

@dp.callback_query(F.data == "start_assembly_dialog")
async def assembly_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AssemblyStates.waiting_sima_order)
    await callback.message.answer("✍️ Введите <b>номер заказа Сима-Ленд</b>, по которому заказан товар:",
                                  parse_mode="HTML")
    await callback.answer()


@dp.message(AssemblyStates.waiting_sima_order)
async def assembly_sima_num(message: types.Message, state: FSMContext):
    await state.update_data(sima_num=message.text)
    await state.set_state(AssemblyStates.waiting_supply_date)
    await message.answer("📅 Введите <b>планируемую дату поставки</b> (например: 25.10):", parse_mode="HTML")


@dp.message(AssemblyStates.waiting_supply_date)
async def assembly_finish_and_run(message: types.Message, state: FSMContext):
    data = await state.get_data()
    sima_num = data['sima_num']
    supply_date = message.text

    await message.answer("⏳ Начинаю сборку заказов на Ozon и сохранение истории...")

    try:
        # Вызываем функцию сборки из ozon_api
        result_text = await assemble_orders(sima_order_num=sima_num, supply_date=supply_date)
        await message.answer(result_text)
    except Exception as e:
        await message.answer(f"❌ Ошибка при сборке: {e}")

    await state.clear()


# --- АНАЛИТИКА И ИСТОРИЯ ---

@dp.message(F.text == "📊 Аналитика")
async def cmd_analytics(message: types.Message):
    await message.answer("Раздел аналитики:", reply_markup=get_analytics_kb())


@dp.message(F.text == "🔍 История заказов")
async def search_order_start(message: types.Message, state: FSMContext):
    await state.set_state(AnalyticsStates.waiting_search_query)
    await message.answer("🔢 Введите номер отправления Ozon (например: 14539857-0021-1):")


# Добавляем проверку, чтобы не искать, если нажата кнопка "Назад"
@dp.message(AnalyticsStates.waiting_search_query)
async def search_order_process(message: types.Message, state: FSMContext):
    if message.text == "⬅️ Назад":
        return await back_to_main_menu(message, state)

    posting_number = message.text.strip()
    info = await get_order_details(posting_number)

    if info:
        products_str = "\n".join([f"- {p}" for p in info['products']])
        response_text = (
            f"📦 <b>Заказ {posting_number}</b>\n\n"
            f"🛒 <b>Состав:</b>\n{products_str}\n\n"
            f"🏭 <b>Заказано у Сима-Ленд:</b>\n"
            f"Номер: <code>{info['sima_num']}</code>\n"
            f"Дата: {info['sima_date']}\n\n"
            f"🚚 <b>Дата поставки на ФФ:</b> {info['deliv_date']}"
        )
        await message.answer(response_text, parse_mode="HTML")
    else:
        await message.answer(f"❌ Заказ `{posting_number}` не найден в активной истории.", parse_mode="Markdown")

    await message.answer("Введите другой номер или нажмите Назад.")


# --- ОТЧЕТЫ (АНАЛИТИКА) ---

@dp.message(F.text == "📈 Отчет по заказам")
async def start_report(message: types.Message, state: FSMContext):
    await state.set_state(AnalyticsStates.waiting_fbs)
    await message.answer("Шаг 1/2: Отправьте файл **Заказы с моих складов (FBS)**", parse_mode="Markdown")


@dp.message(AnalyticsStates.waiting_fbs, F.document)
async def handle_fbs(message: types.Message, state: FSMContext):
    file_info = await bot.get_file(message.document.file_id)
    content = await bot.download_file(file_info.file_path)
    await state.update_data(fbs=content.read())
    await state.set_state(AnalyticsStates.waiting_fbo)
    await message.answer("Шаг 2/2: Отправьте файл **Заказы со складов Ozon (FBO)**", parse_mode="Markdown")


@dp.message(AnalyticsStates.waiting_fbo, F.document)
async def handle_fbo(message: types.Message, state: FSMContext):
    data = await state.get_data()
    fbo_info = await bot.get_file(message.document.file_id)
    fbo_content = await bot.download_file(fbo_info.file_path)

    await message.answer("📊 Обработка...", reply_markup=get_analytics_kb())  # Возвращаем клавиатуру

    res, err = analyzer.process_files(data['fbs'], fbo_content.read())

    if err:
        await message.answer(f"❌ {err}")
    else:
        if res.get('chart'):
            chart_file = BufferedInputFile(res['chart'].getvalue(), filename="chart.png")
            await message.answer_photo(chart_file, caption="📈 Динамика")

        try:
            summary_text = "📋 <b>Сводка:</b>\n"
            current_date = None
            # Сортировка и вывод...
            # (Упрощенный вывод для надежности)
            for _, row in res['brief'].iterrows():
                summary_text += f"\n🗓 {row['Дата']}: {html.escape(str(row['Склад']))} - <b>{int(row['Количество'])} шт.</b>"

            excel_io = analyzer.get_excel(res['detailed'])
            excel_file = BufferedInputFile(excel_io.read(), filename="Report.xlsx")
            await message.answer_document(excel_file, caption=summary_text[:1000], parse_mode="HTML")
        except Exception as e:
            await message.answer(f"⚠️ Ошибка вывода: {e}")

    await state.clear()


# --- ОБЩИЕ ХЕНДЛЕРЫ ---

# Добавляем state="*", чтобы кнопка работала даже внутри поиска или сборки
@dp.message(F.text == "⬅️ Назад")
async def back_to_main_menu(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Главное меню", reply_markup=get_main_kb())


@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Бот готов к работе.", reply_markup=get_main_kb())


# --- РАБОТА С ВИРТУАЛЬНЫМИ ЗАКАЗАМИ ---

@dp.message(F.text == "📂 Виртуальные заказы")
async def show_virtual_orders(message: types.Message):
    # Получаем данные (номер, дата_добавления, номер_сима, дата_поставки)
    orders = await get_virtual_orders_full()

    if not orders:
        await message.answer("✅ Список виртуальных заказов пуст.")
        return

    # Заголовок как в старой версии
    text_lines = ["📋 <b>Заказы для ручной сборки (Многопозиционные):</b>\n"]

    for row in orders:
        p_num = row[0]
        added_at = row[1].split('.')[0] if row[1] else "---"
        sima_num = row[2]
        deliv_date = row[3]

        # Основная строка с номером заказа (копируемым)
        line = f"📦 <code>{p_num}</code> (добавлен: {added_at})"
        text_lines.append(line)

        # Если в базе уже есть данные по Симе для этого заказа, добавляем подстроку
        if sima_num:
            text_lines.append(f"   └ 🛒 Сима: <code>{sima_num}</code> | 🚚 Поставка: {deliv_date}")

    text_lines.append("\nЭти заказы бот пропустил, чтобы вы собрали их вручную.")

    # Кнопка очистки
    clear_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Очистить список", callback_data="clear_virtual_db")]
    ])

    # Если список очень длинный, разбиваем (Telegram лимит 4096 символов)
    full_text = "\n".join(text_lines)
    if len(full_text) > 4000:
        # Упрощенная разбивка для длинных списков
        await message.answer(full_text[:4000], parse_mode="HTML")
        await message.answer(full_text[4000:], parse_mode="HTML", reply_markup=clear_kb)
    else:
        await message.answer(full_text, parse_mode="HTML", reply_markup=clear_kb)


@dp.callback_query(F.data == "clear_virtual_db")
async def process_clear_db(callback: CallbackQuery):
    await clear_virtual_orders()
    await callback.message.edit_text("🗑 <b>Список виртуальных заказов очищен.</b>", parse_mode="HTML")
    await callback.answer()


# --- ПОИСК ЗАКАЗА (Кнопка из главного меню) ---

@dp.message(F.text == "Проверить заказ")
async def cmd_check_order_start(message: types.Message, state: FSMContext):
    await state.set_state(AnalyticsStates.waiting_check_file)
    await message.answer("📁 Пришлите Excel-файл (выгрузку корзины) из Сима-Ленд для проверки состава:")


@dp.message(AnalyticsStates.waiting_check_file, F.document)
async def process_check_file(message: types.Message, state: FSMContext):
    await message.answer("⏳ Сверяю корзину с заказами Ozon...")

    try:
        file_id = message.document.file_id
        file = await bot.get_file(file_id)
        file_data = await bot.download_file(file.file_path)
        content = file_data.read()

        # 1. Пытаемся прочитать как Excel, если не выйдет - как CSV
        try:
            # Читаем без заголовков (header=None), чтобы самим найти нужную строку
            df_raw = pd.read_excel(io.BytesIO(content), header=None)
        except:
            # Если это CSV (Сима иногда так отдает)
            df_raw = pd.read_csv(io.BytesIO(content), header=None, sep=None, engine='python')

        # 2. Ищем строку, в которой есть слово "Артикул"
        header_row_index = -1
        for i, row in df_raw.iterrows():
            # Превращаем всю строку в текст и ищем ключевые слова
            row_content = " ".join([str(val).lower() for val in row if pd.notna(val)])
            if 'артикул' in row_content and ('количество' in row_content or 'кол-во' in row_content):
                header_row_index = i
                break

        if header_row_index == -1:
            await message.answer("❌ Не нашел в файле таблицу с колонками 'Артикул' и 'Количество'.")
            return

        # 3. Пересобираем таблицу, начиная с найденной строки
        # Назначаем найденную строку заголовками
        headers = [str(h).strip().lower() for h in df_raw.iloc[header_row_index]]
        df = df_raw.iloc[header_row_index + 1:].copy()
        df.columns = headers

        # Определяем точные названия колонок
        col_art = next((c for c in df.columns if 'артикул' in c or 'код' in c), None)
        col_qty = next((c for c in df.columns if 'количество' in c or 'кол-во' in c or 'кол.' in c), None)

        file_items = {}
        for _, row in df.iterrows():
            try:
                art = str(row[col_art]).strip()
                # Пропускаем пустые строки или строку "Итого"
                if not art or art == 'nan' or 'итого' in art.lower():
                    continue

                qty = int(float(row[col_qty]))  # float на случай если в Excel записано 1.0
                file_items[art] = file_items.get(art, 0) + qty
            except:
                continue

        # 4. Получаем данные Ozon и сравниваем
        from ozon_api import get_total_ozon_demand
        ozon_demand = await get_total_ozon_demand()

        diff_lines = []
        all_arts = set(list(file_items.keys()) + list(ozon_demand.keys()))

        for art in sorted(all_arts):
            in_file = file_items.get(art, 0)
            needed = ozon_demand.get(art, 0)

            if in_file > needed:
                diff_lines.append(
                    f"⚠️ <code>{art}</code>: В файле {in_file} шт. (нужно {needed}). <b>Лишних: {in_file - needed}</b>")
            elif in_file < needed:
                diff_lines.append(
                    f"❌ <code>{art}</code>: В файле {in_file} шт. (нужно {needed}). <b>Не хватает: {needed - in_file}</b>")

        if not diff_lines:
            await message.answer("✅ <b>Результат проверки:</b>\n\nСостав корзины идеально совпадает с заказами Ozon!",
                                 parse_mode="HTML")
        else:
            report = "📊 <b>Результат проверки:</b>\n\n" + "\n".join(diff_lines)
            if len(report) > 4000:
                await message.answer("⚠️ Слишком много расхождений, проверьте внимательно!")
                # Вывод частями, если список огромный
                for i in range(0, len(diff_lines), 20):
                    await message.answer("\n".join(diff_lines[i:i + 20]), parse_mode="HTML")
            else:
                await message.answer(report, parse_mode="HTML")

    except Exception as e:
        logging.error(f"Full check error: {e}")
        await message.answer(f"❌ Ошибка: {e}")

    await state.clear()


async def main():
    await init_db()
    # Удаляем вебхук на всякий случай, чтобы polling работал сразу
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())