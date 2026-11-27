# bot/handlers/tester.py v1.0
# Панель полевого тестировщика: начало теста, задания, отчёты.

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from core.db import get_conn, get_user
from bot import keyboards as kb

router = Router()


class TSDStates(StatesGroup):
    """Состояния анкеты ТСД: цена → остаток → тип ценника."""
    price = State()
    stock = State()
    tag_type = State()


# Хранилище текущего теста в RAM (сбрасывается при рестарте бота)
_active_tests = {}  # user_id -> {store_id, plu_list, current_idx, session_id}


@router.callback_query(F.data == "tester:start")
async def start_test(c: CallbackQuery, state: FSMContext):
    """Начать полевой тест: выбор магазина."""
    conn = get_conn()
    stores = conn.execute(
        "SELECT store_id, address FROM stores WHERE status='active' LIMIT 10"
    ).fetchall()
    conn.close()

    if not stores:
        await c.message.edit_text("🧪 Нет активных магазинов для теста.", reply_markup=kb.back_to_main())
        await c.answer(); return

    b = InlineKeyboardBuilder()
    for s in stores:
        b.button(text=f"🏬 {s['address']}", callback_data=f"tester:store:{s['store_id']}")
    b.button(text="⬅️ В меню", callback_data="main:back")
    b.adjust(1)
    await c.message.edit_text("🧪 Выбери магазин для проверки:", reply_markup=b.as_markup())
    await c.answer()


@router.callback_query(F.data.startswith("tester:store:"))
async def select_store(c: CallbackQuery, state: FSMContext):
    """Выбран магазин — показываем список скидок для проверки."""
    sid = c.data.split(":")[2]
    conn = get_conn()
    # Берём топ-10 скидок из этого магазина за последние 24 часа
    rows = conn.execute("""
        SELECT p.plu, pr.name, p.price, p.regular, p.discount_pct, p.stock
        FROM prices p
        JOIN products pr ON pr.plu = p.plu
        WHERE p.store_id = ? AND p.stock > 0 AND p.regular > p.price
        ORDER BY p.discount_pct DESC LIMIT 10
    """, (sid,)).fetchall()
    conn.close()

    if not rows:
        await c.message.edit_text("🧪 В этом магазине нет активных скидок.", reply_markup=kb.back_to_main())
        await c.answer(); return

    # Создаём сессию теста
    conn = get_conn()
    cur = conn.execute("""
        INSERT INTO tester_sessions (tester_id, store_id, started_at, total_items)
        VALUES (?, ?, ?, ?)
    """, (c.from_user.id, sid, int(__import__('time').time()), len(rows)))
    session_id = cur.lastrowid
    conn.commit(); conn.close()

    _active_tests[c.from_user.id] = {
        "store_id": sid,
        "plu_list": [dict(r) for r in rows],
        "current_idx": 0,
        "session_id": session_id,
    }

    await state.set_state(TSDStates.price)
    await _send_next_item(c.message, c.from_user.id)
    await c.answer()


async def _send_next_item(msg, user_id: int):
    """Отправляем следующий товар для проверки."""
    test = _active_tests.get(user_id)
    if not test or test["current_idx"] >= len(test["plu_list"]):
        await msg.answer("✅ Все товары проверены! Отчёт сохранён.", reply_markup=kb.back_to_main())
        if user_id in _active_tests:
            del _active_tests[user_id]
        return

    item = test["plu_list"][test["current_idx"]]
    text = (
        f"🧾 <b>Проверка {test['current_idx'] + 1}/{len(test['plu_list'])}</b>\n\n"
        f"📦 <b>{item['name']}</b>\n"
        f"💰 Цена API: <b>{item['price']:.0f} ₽</b> (было {item['regular']:.0f} ₽)\n"
        f"📉 Скидка: -{item['discount_pct']:.0f}%\n"
        f"📊 Остаток API: {item['stock']:.0f} шт.\n\n"
        f"👉 Введи реальную цену с прайс-чекера:"
    )
    await msg.answer(text)


@router.message(TSDStates.price)
async def input_price(m: Message, state: FSMContext):
    """Пользователь ввёл реальную цену."""
    try:
        real_price = float(m.text.replace(",", ".").strip())
    except ValueError:
        await m.answer("❌ Неверный формат. Введи число (например, 150 или 150.50):")
        return

    test = _active_tests.get(m.from_user.id)
    if not test:
        await m.answer("⚠️ Тест не найден. Начни заново.", reply_markup=kb.back_to_main())
        await state.clear(); return

    test["real_price"] = real_price
    await state.set_state(TSDStates.stock)

    item = test["plu_list"][test["current_idx"]]
    await m.answer(
        f"✅ Цена записана: {real_price:.0f} ₽\n\n"
        f"👉 Теперь введи реальный остаток на полке (число):"
    )


@router.message(TSDStates.stock)
async def input_stock(m: Message, state: FSMContext):
    """Пользователь ввёл реальный остаток."""
    try:
        real_stock = int(m.text.strip())
    except ValueError:
        await m.answer("❌ Неверный формат. Введи целое число (например, 5):")
        return

    test = _active_tests.get(m.from_user.id)
    if not test:
        await m.answer("⚠️ Тест не найден. Начни заново.", reply_markup=kb.back_to_main())
        await state.clear(); return

    test["real_stock"] = real_stock
    await state.set_state(TSDStates.tag_type)

    b = InlineKeyboardBuilder()
    b.button(text="🏷 Обычный", callback_data="tag:normal")
    b.button(text="🔥 Акционный", callback_data="tag:promo")
    b.button(text="⚠️ Ошибочный", callback_data="tag:error")
    b.adjust(1)
    await m.answer(
        f"✅ Остаток записан: {real_stock} шт.\n\n"
        f"👉 Выбери тип ценника:",
        reply_markup=b.as_markup()
    )


@router.callback_query(F.data.startswith("tag:"), TSDStates.tag_type)
async def input_tag(c: CallbackQuery, state: FSMContext):
    """Пользователь выбрал тип ценника — сохраняем отчёт."""
    tag_type = c.data.split(":")[1]
    test = _active_tests.get(c.from_user.id)
    if not test:
        await c.message.edit_text("⚠️ Тест не найден.", reply_markup=kb.back_to_main())
        await state.clear(); await c.answer(); return

    item = test["plu_list"][test["current_idx"]]

    # Сохраняем отчёт в БД
    conn = get_conn()
    conn.execute("""
        INSERT INTO tester_reports
        (session_id, plu, api_price, tsd_price, real_stock, promo_type, ts)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        test["session_id"],
        item["plu"],
        item["price"],
        test["real_price"],
        test["real_stock"],
        tag_type,
        int(__import__('time').time()),
    ))
    conn.commit(); conn.close()

    # Переходим к следующему товару
    test["current_idx"] += 1
    await state.clear()
    await c.message.edit_text("✅ Отчёт сохранён!")
    await _send_next_item(c.message, c.from_user.id)
    await c.answer()


@router.callback_query(F.data == "tester:tasks")
async def my_tasks(c: CallbackQuery):
    """Мои задания — список активных сессий."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT ts.id, s.address, ts.started_at, ts.total_items, ts.errors_found
        FROM tester_sessions ts
        JOIN stores s ON s.store_id = ts.store_id
        WHERE ts.tester_id = ? AND ts.finished_at IS NULL
        ORDER BY ts.started_at DESC LIMIT 10
    """, (c.from_user.id,)).fetchall()
    conn.close()

    if not rows:
        await c.message.edit_text("📋 Нет активных заданий.", reply_markup=kb.back_to_main())
        await c.answer(); return

    lines = ["📋 <b>Твои задания:</b>\n"]
    for r in rows:
        lines.append(f"• {r['address']} — начато {r['total_items']} проверок")
    await c.message.edit_text("\n".join(lines), reply_markup=kb.back_to_main())
    await c.answer()


@router.callback_query(F.data == "tester:reports")
async def my_reports(c: CallbackQuery):
    """Мои отчёты — последние 10 проверок."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT tr.plu, pr.name, tr.api_price, tr.tsd_price, tr.real_stock, tr.promo_type, tr.ts
        FROM tester_reports tr
        JOIN products pr ON pr.plu = tr.plu
        JOIN tester_sessions ts ON ts.id = tr.session_id
        WHERE ts.tester_id = ?
        ORDER BY tr.ts DESC LIMIT 10
    """, (c.from_user.id,)).fetchall()
    conn.close()

    if not rows:
        await c.message.edit_text("📤 Нет отправленных отчётов.", reply_markup=kb.back_to_main())
        await c.answer(); return

    lines = ["📤 <b>Последние отчёты:</b>\n"]
    for r in rows:
        diff = r["tsd_price"] - r["api_price"]
        sign = "+" if diff > 0 else ""
        lines.append(
            f"• {r['name'][:30]}\n"
            f"  API: {r['api_price']:.0f} ₽ | ТСД: {r['tsd_price']:.0f} ₽ ({sign}{diff:.0f})\n"
            f"  Остаток: {r['real_stock']} шт. | Тип: {r['promo_type']}\n"
        )
    await c.message.edit_text("\n".join(lines), reply_markup=kb.back_to_main())
    await c.answer()
