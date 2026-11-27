# bot/keyboards.py v1.0
# Меню по ролям. callback_data короткие, с префиксом роли.

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def _kb(buttons: list, width: int = 1) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for text, data in buttons:
        b.button(text=text, callback_data=data)
    b.adjust(width)
    return b.as_markup()


def user_main_menu() -> InlineKeyboardMarkup:
    return _kb([
        ("📡 Радар скидок", "user:radar"),
        ("🔎 Искатель", "user:seeker"),
        ("🏬 Мои магазины", "user:stores"),
        ("💳 Подписка", "user:subs"),
        ("🆘 Поддержка", "user:help"),
    ], width=1)


def owner_main_menu() -> InlineKeyboardMarkup:
    return _kb([
        ("📊 Статистика", "owner:stats"),
        ("🏬 Магазины", "owner:stores"),
        ("👥 Персонал", "owner:staff"),
        ("💰 Бухгалтерия", "owner:finance"),
        ("📢 Канал", "owner:channel"),
        ("🧪 Симуляторы", "owner:sim"),
        ("⚙️ Настройки", "owner:settings"),
    ], width=2)


def admin_main_menu() -> InlineKeyboardMarkup:
    return _kb([
        ("🏬 Магазины", "admin:stores"),
        ("📋 Задания тестерам", "admin:tasks"),
        ("🚨 Алерты", "admin:alerts"),
    ], width=1)


def tester_main_menu() -> InlineKeyboardMarkup:
    return _kb([
        ("🧾 Начать тест", "tester:start"),
        ("📋 Мои задания", "tester:tasks"),
        ("📤 Отчёты", "tester:reports"),
    ], width=1)


def operator_main_menu() -> InlineKeyboardMarkup:
    return _kb([
        ("🎫 Тикеты", "op:tickets"),
        ("📞 Прозвон", "op:calls"),
    ], width=1)


def accountant_main_menu() -> InlineKeyboardMarkup:
    return _kb([
        ("💳 Платежи", "acc:payments"),
        ("↩️ Возвраты", "acc:refunds"),
        ("📈 НПД / лимит", "acc:npd"),
        ("📤 Экспорт", "acc:export"),
    ], width=2)


def back_to_main() -> InlineKeyboardMarkup:
    return _kb([("⬅️ В меню", "main:back")], width=1)


def confirm_kb(yes_data: str, no_data: str) -> InlineKeyboardMarkup:
    return _kb([("✅ Да", yes_data), ("❌ Нет", no_data)], width=2)
