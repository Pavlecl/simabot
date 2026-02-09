import os
import asyncio
import logging
import html
from datetime import datetime, timedelta
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, BufferedInputFile
)
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Импорт наших модулей
from database import init_db, get_virtual_orders_full, clear_virtual_orders, get_order_details
from ozon_api import get_new_orders, assemble_orders
from analytics import OzonAnalytics

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
analyzer = OzonAnalytics()

logging.basicConfig(level=logging.INFO)


# --- FSM (Машина состояний) ---
class AnalyticsStates(StatesGroup):
    # Для отчетов (из прошлой задачи)
    waiting_fbs = State()
    waiting_fbo = State()
    # НОВЫЕ: Для поиска заказа
    waiting_search_query = State()


class AssemblyStates(StatesGroup):
    # НОВЫЕ: Для сборки поставки
    waiting_sima_order = State()
    waiting_supply_date = State()


# --- Клавиатуры ---

def get_main_kb():
    kb = [
        [KeyboardButton(text="Получить заказы Сима")],
        [KeyboardButton(text="📊 Аналитика")],
        [KeyboardButton(text="📂 Виртуальные заказы")],
        [KeyboardButton(text="Проверить заказ")]
        # Кнопка остановки напоминаний скрыта во второй уровень или можно добавить сюда
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


def get_analytics_kb():
    kb = [
        [KeyboardButton(text="📈 Отчет по заказам")],
        [KeyboardButton(text="🔍 История заказов")],  # НОВАЯ КНОПКА
        [KeyboardButton(text="⬅️ Назад")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


def get_assemble_kb():
    # Эта кнопка теперь запускает ДИАЛОГ, а не мгновенную сборку
    buttons = [[InlineKeyboardButton(text="📦 Собрать поставку", callback_data="start_assembly_dialog")]]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# --- ЛОГИКА АНАЛИТИКИ (МЕНЮ) ---

@dp.message(F.text == "📊 Аналитика")
async def cmd_analytics(message: types.Message):
    await message.answer("Выберите раздел:", reply_markup=get_analytics_kb())


@dp.message(F.text == "⬅️ Назад")
async def back_to_main_menu(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Главное меню", reply_markup=get_main_kb())


# --- ЛОГИКА ПОИСКА (ИСТОРИЯ ЗАКАЗОВ) ---

@dp.message(F.text == "🔍 История заказов")
async def search_order_start(message: types.Message, state: FSMContext):
    await state.set_state(AnalyticsStates.waiting_search_query)
    await message.answer("🔢 Введите номер отправления Ozon (например, 14539857-0021-1):")


@dp.message(AnalyticsStates.waiting_search_query)
async def search_order_process(message: types.Message, state: FSMContext):
    posting_number = message.text.strip()

    # Ищем в базе
    info = await get_order_details(posting_number)

    if info:
        # Формируем красивый ответ
        products_str = "\n".join([f"- {p}" for p in info['products']])

        response_text = (
            f"📦 <b>Заказ {posting_number}</b>\n\n"
            f"🛒 <b>Состав:</b>\n{products_str}\n\n"
            f"🏭 <b>Заказано у Сима-Ленд:</b>\n"
            f"Номер: {info['sima_num']}\n"
            f"Дата: {info['sima_date']}\n\n"
            f"🚚 <b>Дата поставки на Фулфилмент:</b> {info['deliv_date']}"
        )
        await message.answer(response_text, parse_mode="HTML")
    else:
        await message.answer(
            f"❌ Информации по заказу `{posting_number}` не найдено.\n"
            "Возможно, он уже отгружен, отменен или еще не был собран через бота.",
            parse_mode="Markdown"
        )

    # Не сбрасываем состояние полностью, можно искать дальше, или предложим меню
    await message.answer("Введите другой номер или нажмите Назад.", reply_markup=get_analytics_kb())


# --- ЛОГИКА СБОРКИ ПОСТАВКИ (НОВАЯ) ---

@dp.callback_query(F.data == "start_assembly_dialog")
async def assembly_start(callback: CallbackQuery, state: FSMContext):
    # Спрашиваем номер заказа Сима
    await state.set_state(AssemblyStates.waiting_sima_order)
    await callback.message.answer("✍️ Введите <b>номер заказа Сима-Ленд</b>, по которому пришел товар:",
                                  parse_mode="HTML")
    await callback.answer()


@dp.message(AssemblyStates.waiting_sima_order)
async def assembly_sima_num(message: types.Message, state: FSMContext):
    await state.update_data(sima_num=message.text)
    await state.set_state(AssemblyStates.waiting_supply_date)
    await message.answer("📅 Введите <b>планируемую дату поставки</b> на Фулфилмент (например, 25.10):",
                         parse_mode="HTML")


@dp.message(AssemblyStates.waiting_supply_date)
async def assembly_finish_and_run(message: types.Message, state: FSMContext):
    data = await state.get_data()
    sima_num = data['sima_num']
    supply_date = message.text

    await message.answer("⏳ Начинаю сборку и сохранение данных...")

    # Запускаем сборку с новыми параметрами
    result_text = await assemble_orders(sima_order_num=sima_num, supply_date=supply_date)

    await message.answer(result_text)
    await state.clear()

    # Убираем напоминания, так как сборка прошла
    # (Функция stop_reminder должна быть доступна, импортируйте её или перенесите логику)
    # В рамках этого примера просто сообщим:
    # stop_reminder()


# --- (Остальные обработчики: Отчеты, Файлы и т.д. остаются без изменений) ---
# ... Код для отчетов (Excel/Графики), который мы делали ранее, вставьте сюда ...

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("Бот обновлен. Добавлена История заказов.", reply_markup=get_main_kb())


async def main():
    await init_db()
    # ... scheduler jobs ...
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())