# bot/sender.py v1.0
# Рассылка скидок подписчикам.
# Берёт новые скидки из prices, фильтрует по правилам пользователя, отправляет в Telegram.

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict

from aiogram import Bot
from aiogram.types import InputMediaPhoto

from core.db import get_conn, get_user, get_user_stores

logger = logging.getLogger(__name__)
MSK = timezone(timedelta(hours=3))

# Очередь уведомлений (в памяти, теряется при рестарте)
_notification_queue: asyncio.Queue = asyncio.Queue()

# Антидубль: (user_id, store_id, plu) -> ts
_sent_alerts: Dict[tuple, int] = {}


def now_ts() -> int:
    return int(datetime.now(MSK).timestamp())


def format_card(store_addr: str, name: str, price: float, regular: float,
                discount_pct: float, stock: float, plu: str, img_url: str = "") -> str:
    """Форматирует карточку товара."""
    stock_warn = " ⚠️ Минимальный остаток!" if stock <= 2 else ""
    text = (
        f"🏬 {store_addr}\n"
        f"📦 {name}\n"
        f"💰 <b>{price:.0f}₽</b> (было {regular:.0f}₽)\n"
        f"🔥 Скидка -{discount_pct:.0f}%\n"
        f"📊 Остаток: {stock:.0f} шт{stock_warn}\n"
        f"🔢 PLU: {plu}"
    )
    return text


async def send_card(bot: Bot, user_id: int, text: str, img_url: str = "") -> None:
    """Отправляет карточку товара пользователю."""
    try:
        if img_url:
            await bot.send_photo(user_id, img_url, caption=text, parse_mode="HTML")
        else:
            await bot.send_message(user_id, text, parse_mode="HTML")
        await asyncio.sleep(0.06)  # микропауза для защиты от флуд-бана
    except Exception as e:
        logger.error(f"[Sender] Ошибка отправки {user_id}: {e}")


async def process_queue(bot: Bot) -> None:
    """Обработчик очереди уведомлений (крутится в фоне)."""
    while True:
        try:
            user_id, text, img_url = await _notification_queue.get()
            await send_card(bot, user_id, text, img_url)
            _notification_queue.task_done()
        except Exception as e:
            logger.error(f"[Sender] Ошибка обработки очереди: {e}")


def check_user_rules(user_id: int, store_id: str, plu: str, discount_pct: float,
                     category: str, name: str) -> bool:
    """Проверяет, соответствует ли скидка правилам пользователя."""
    u = get_user(user_id)
    if not u:
        return False
    
    # Порог скидки
    min_pct = u["threshold_pct"] if u["threshold_pct"] else 50
    if discount_pct < min_pct:
        return False
    
    # Магазин
    user_stores = get_user_stores(user_id)
    if user_stores and store_id not in user_stores:
        return False
    
    # Стоп-слова
    stop_words = u.get("stop_words", "")
    if stop_words:
        words = [w.strip().lower() for w in stop_words.split(",") if w.strip()]
        name_lower = name.lower()
        if any(word in name_lower for word in words):
            return False
    
    return True


def generate_notifications(new_prices: List[Dict]) -> None:
    """Генерирует уведомления для пользователей по новым скидкам."""
    conn = get_conn()
    users = conn.execute("SELECT user_id FROM users WHERE role='user'").fetchall()
    conn.close()
    
    for user_row in users:
        user_id = user_row["user_id"]
        for price in new_prices:
            store_id = price["store_id"]
            plu = price["plu"]
            discount_pct = price["discount_pct"]
            category = price.get("category_name", "")
            name = price["name"]
            
            # Антидубль
            key = (user_id, store_id, plu)
            if key in _sent_alerts and (now_ts() - _sent_alerts[key]) < 86400:
                continue
            
            if check_user_rules(user_id, store_id, plu, discount_pct, category, name):
                addr = price.get("address", "")
                text = format_card(
                    addr, name, price["price"], price["regular"],
                    discount_pct, price["stock"], plu, price.get("img_url", "")
                )
                _notification_queue.put_nowait((user_id, text, price.get("img_url", "")))
                _sent_alerts[key] = now_ts()


def start_sender(bot: Bot) -> None:
    """Запускает обработчик очереди в фоне."""
    asyncio.create_task(process_queue(bot))
    logger.info("[Sender] Обработчик очереди запущен")
