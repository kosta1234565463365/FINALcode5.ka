# core/outbox.py v1.0
# Единый шлюз отправки сообщений.
# Троттлинг, проверка статусов магазинов, mute, is_sim, защита от дублей.

import asyncio
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

from core.config import DB_PATH, TG_MAX_MEDIA_GROUP

MSK = timezone(timedelta(hours=3))

# Глобальная блокировка для сериализации отправки (защита от 429 Too Many Requests)
_send_lock = asyncio.Lock()
_last_send_ts: float = 0.0
MIN_INTERVAL_SEC = 0.06  # Микропауза между сообщениями


def get_conn(timeout: int = 30) -> sqlite3.Connection:
    """Получить соединение с БД."""
    conn = sqlite3.connect(str(DB_PATH), timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


def _check_store_status(store_id: str) -> bool:
    """Проверить, можно ли слать промо для этого магазина.
    Возвращает True если магазин active/expected/pending.
    False если pause/alarm/broken/deleted.
    """
    with get_conn() as c:
        row = c.execute(
            "SELECT status FROM stores WHERE store_id=?", (store_id,)
        ).fetchone()
        if not row:
            return False
        return row["status"] in ("active", "expected", "pending")


def _check_user_muted(user_id: int) -> bool:
    """Проверить, не заглушил ли пользователь уведомления."""
    with get_conn() as c:
        row = c.execute(
            "SELECT is_muted FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
        return bool(row and row["is_muted"])


def _is_duplicate(user_id: int, store_id: str, plu: str, price: float) -> bool:
    """Проверить, не отправляли ли уже эту скидку этому юзеру.
    Дубль = тот же товар в том же магазине по той же или более высокой цене.
    """
    with get_conn() as c:
        row = c.execute(
            "SELECT price FROM active_alerts WHERE user_id=? AND store_id=? AND plu=?",
            (user_id, store_id, plu)
        ).fetchone()
        if not row:
            return False
        # Если новая цена НИЖЕ старой — это не дубль, а обновление
        return price >= row["price"]


def record_alert(user_id: int, store_id: str, plu: str, price: float) -> None:
    """Записать отправленную скидку в active_alerts (защита от дублей)."""
    now = int(datetime.now(MSK).timestamp())
    with get_conn() as c:
        c.execute("""
        INSERT INTO active_alerts (user_id, store_id, plu, price, ts)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id, store_id, plu) DO UPDATE SET
            price=excluded.price, ts=excluded.ts
        """, (user_id, store_id, plu, price, now))


def clear_alert(user_id: int, store_id: str, plu: str) -> None:
    """Удалить запись из active_alerts (когда акция закончилась или цена вернулась)."""
    with get_conn() as c:
        c.execute(
            "DELETE FROM active_alerts WHERE user_id=? AND store_id=? AND plu=?",
            (user_id, store_id, plu)
        )


async def throttle():
    """Обеспечить минимальный интервал между отправками."""
    global _last_send_ts
    async with _send_lock:
        now = time.monotonic()
        elapsed = now - _last_send_ts
        if elapsed < MIN_INTERVAL_SEC:
            await asyncio.sleep(MIN_INTERVAL_SEC - elapsed)
        _last_send_ts = time.monotonic()


class Outbox:
    """Единый шлюз отправки. Все сообщения идут только через этот класс.
    
    Использование:
        outbox = Outbox(bot)
        await outbox.send_text(user_id, "Привет!")
        await outbox.send_photo(user_id, photo_url, caption="Скидка!")
        await outbox.send_media_group(user_id, media_list)
    """

    def __init__(self, bot):
        self.bot = bot

    async def send_text(self, user_id: int, text: str, 
                        reply_markup=None, parse_mode="HTML",
                        disable_notification: bool = False) -> Optional[Any]:
        """Отправить текстовое сообщение с троттлингом."""
        if _check_user_muted(user_id):
            return None
        await throttle()
        try:
            return await self.bot.send_message(
                chat_id=user_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                disable_notification=disable_notification
            )
        except Exception as e:
            print(f"[OUTBOX] Ошибка отправки текста {user_id}: {e}")
            return None

    async def send_photo(self, user_id: int, photo: str, caption: str = "",
                         reply_markup=None, parse_mode="HTML",
                         disable_notification: bool = False) -> Optional[Any]:
        """Отправить фото с подписью и троттлингом."""
        if _check_user_muted(user_id):
            return None
        await throttle()
        try:
            return await self.bot.send_photo(
                chat_id=user_id,
                photo=photo,
                caption=caption,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                disable_notification=disable_notification
            )
        except Exception as e:
            print(f"[OUTBOX] Ошибка отправки фото {user_id}: {e}")
            return None

    async def send_media_group(self, user_id: int, media: list,
                               disable_notification: bool = False) -> Optional[List[Any]]:
        """Отправить альбом (до TG_MAX_MEDIA_GROUP файлов) с троттлингом."""
        if _check_user_muted(user_id):
            return None
        if len(media) > TG_MAX_MEDIA_GROUP:
            media = media[:TG_MAX_MEDIA_GROUP]
        await throttle()
        try:
            return await self.bot.send_media_group(
                chat_id=user_id,
                media=media,
                disable_notification=disable_notification
            )
        except Exception as e:
            print(f"[OUTBOX] Ошибка отправки альбома {user_id}: {e}")
            return None

    async def edit_text(self, chat_id: int, message_id: int, text: str,
                        reply_markup=None, parse_mode="HTML") -> Optional[Any]:
        """Редактировать существующее сообщение (без троттлинга, но с защитой)."""
        try:
            return await self.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
        except Exception as e:
            print(f"[OUTBOX] Ошибка редактирования {message_id}: {e}")
            return None

    async def delete_message(self, chat_id: int, message_id: int) -> bool:
        """Удалить сообщение."""
        try:
            await self.bot.delete_message(chat_id=chat_id, message_id=message_id)
            return True
        except Exception:
            return False

    def can_send_promo(self, store_id: str) -> bool:
        """Проверить, можно ли слать промо для магазина (синхронная проверка)."""
        return _check_store_status(store_id)

    def is_duplicate(self, user_id: int, store_id: str, plu: str, price: float) -> bool:
        """Проверить дубль (синхронная проверка)."""
        return _is_duplicate(user_id, store_id, plu, price)
