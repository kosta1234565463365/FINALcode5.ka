# bot/handlers/user.py v1.0
# Обработчики кнопок пользовательского меню.

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.db import get_conn, get_user, get_user_stores, set_user_store, radar_rows, search_products, stores_in_city
from bot import keyboards as kb

router = Router()
_pending_search = {}


@router.callback_query(F.data == "user:radar")
async def radar(c: CallbackQuery):
    u = get_user(c.from_user.id)
    thr = u["threshold_pct"] if u else 50
    rows = radar_rows(thr, 10)
    if not rows:
        await c.message.edit_text("📡 Радар пуст. Данные появятся после сбора парсером.", reply_markup=kb.back_to_main())
        await c.answer(); return
    lines = ["📡 <b>Топ скидок:</b>\n"]
    for r in rows:
        lines.append(f"🔥 <b>{r['name']}</b>\n💰 {r['price']:.0f}₽ вместо {r['regular']:.0f}₽ (−{r['discount_pct']:.0f}%)\n🏬 {r['address']} · ост. {r['stock']:.0f}\n")
    await c.message.edit_text("\n".join(lines), reply_markup=kb.back_to_main())
    await c.answer()


@router.callback_query(F.data == "user:seeker")
async def seeker(c: CallbackQuery):
    _pending_search[c.from_user.id] = True
    await c.message.edit_text("🔎 Напиши название товара:", reply_markup=kb.back_to_main())
    await c.answer()


@router.message(F.text & ~F.text.startswith("/"))
async def seeker_text(m: Message):
    if not _pending_search.pop(m.from_user.id, False):
        return
    rows = search_products(m.text.strip(), 10)
    if not rows:
        await m.answer("Ничего не нашёл.", reply_markup=kb.back_to_main()); return
    lines = [f"🔎 По запросу «{m.text}»:\n"]
    for r in rows:
        lines.append(f"• {r['name']} — от {r['best']:.0f}₽")
    await m.answer("\n".join(lines), reply_markup=kb.back_to_main())


@router.callback_query(F.data == "user:stores")
async def stores(c: CallbackQuery):
    ids = get_user_stores(c.from_user.id)
    b = InlineKeyboardBuilder()
    if ids:
        rows = get_conn().execute("SELECT store_id,address,status FROM stores WHERE store_id IN (%s)" % ",".join("?"*len(ids)), ids).fetchall()
        text = "🏬 Твои магазины:\n" + "\n".join(f"• {r['address']} [{r['status']}]" for r in rows)
    else:
        text = "🏬 У тебя пока нет магазинов."
    b.button(text="➕ Добавить", callback_data="user:storepick")
    b.button(text="⬅️ В меню", callback_data="main:back")
    b.adjust(1)
    await c.message.edit_text(text, reply_markup=b.as_markup())
    await c.answer()


@router.callback_query(F.data == "user:storepick")
async def storepick(c: CallbackQuery):
    u = get_user(c.from_user.id)
    city = u["city"] if u else "Москва"
    rows = stores_in_city(city, 10)
    if not rows:
        await c.message.edit_text("Магазины ещё не загружены.", reply_markup=kb.back_to_main()); await c.answer(); return
    b = InlineKeyboardBuilder()
    for r in rows:
        b.button(text=f"➕ {r['address']}", callback_data=f"user:add:{r['store_id']}")
    b.button(text="⬅️ Назад", callback_data="user:stores")
    b.adjust(1)
    await c.message.edit_text("Выбери магазин:", reply_markup=b.as_markup())
    await c.answer()


@router.callback_query(F.data.startswith("user:add:"))
async def store_add(c: CallbackQuery):
    sid = c.data.split(":", 2)[2]
    set_user_store(c.from_user.id, sid)
    await c.answer("Магазин добавлен ✅", show_alert=True)
    await stores(c)


@router.callback_query(F.data == "user:subs")
async def subs(c: CallbackQuery):
    await c.message.edit_text("💳 Подписка. Тарифы появятся позже.", reply_markup=kb.back_to_main())
    await c.answer()


@router.callback_query(F.data == "user:help")
async def help_(c: CallbackQuery):
    conn = get_conn()
    import time
    conn.execute("INSERT INTO tickets (user_id,status,created_at) VALUES (?,?,?)", (c.from_user.id, "open", int(time.time())))
    conn.commit(); conn.close()
    await c.message.edit_text("🆘 Тикет создан. Оператор ответит здесь.", reply_markup=kb.back_to_main())
    await c.answer()
