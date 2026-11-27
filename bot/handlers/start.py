# bot/handlers/start.py v1.1
# /start: регистрация, определение роли, показ меню.
# Приоритет роли: staff (назначенная персонально) > users.role (базовая).

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart

from core.db import upsert_user, get_conn, get_user
from bot import keyboards as kb

router = Router()


def get_role(user_id: int) -> str:
    """Сначала смотрим staff (активная персональная роль).
    Если нет — берём users.role. Если и там пусто — 'user'."""
    conn = get_conn()
    row = conn.execute(
        "SELECT role FROM staff WHERE user_id=? AND is_active=1", (user_id,)
    ).fetchone()
    conn.close()
    if row:
        return row["role"]
    u = get_user(user_id)
    if u and u["role"] and u["role"] != "user":
        return u["role"]
    return "user"


def menu_for_role(role: str):
    return {
        "owner": kb.owner_main_menu,
        "admin": kb.admin_main_menu,
        "tester": kb.tester_main_menu,
        "operator": kb.operator_main_menu,
        "accountant": kb.accountant_main_menu,
    }.get(role, kb.user_main_menu)()


WELCOME = {
    "owner": "👑 Панель владельца.",
    "admin": "🛠 Панель администратора.",
    "tester": "🧾 Панель полевого тестировщика.",
    "operator": "🎧 Панель оператора поддержки.",
    "accountant": "💰 Панель бухгалтера.",
    "user": "👋 Привет! Я слежу за скидками «Пятёрочки».\nВыбери раздел:",
}


@router.message(CommandStart())
async def cmd_start(m: Message):
    upsert_user(
        m.from_user.id,
        m.from_user.username or "",
        m.from_user.full_name or "",
    )
    role = get_role(m.from_user.id)
    await m.answer(WELCOME.get(role, WELCOME["user"]), reply_markup=menu_for_role(role))


@router.callback_query(F.data == "main:back")
async def back_to_menu(c: CallbackQuery):
    role = get_role(c.from_user.id)
    await c.message.edit_text("Главное меню:", reply_markup=menu_for_role(role))
    await c.answer()
