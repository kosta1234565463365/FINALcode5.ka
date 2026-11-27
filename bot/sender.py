
# bot/sender.py v1.0
# Фоновая рассылка скидок подписчикам.
# Работает как asyncio-задача внутри бота. Проверяет БД каждую минуту.

import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta
from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from core.db import get_conn

logger = logging.getLogger(__name__)
MSK = timezone(timedelta(hours=3))


def init_sender_schema():
    """Добавляем поле last_notify_ts в users, если его нет."""
    conn = get_conn()
    try:
        conn.execute("ALTER TABLE users ADD COLUMN last_notify_ts INTEGER DEFAULT 0")
        conn.commit()
    except Exception:
        pass  # Колонка уже есть
    conn.close()


def format_discount_card(row: dict) -> str:
    stock_warn = " ⚠️ <i>Мало!</i>" if row["stock"] <= 2 else ""
    return (
        f"🔥 <b>{row['name']}</b>\n"
        f"💰 <b>{row['price']:.0f} ₽</b> (было {row['regular']:.0f} ₽)\n"
        f"📉 Скидка: <b>-{row['discount_pct']:.0f}%</b>\n"
        f"🏬 {row['address']}\n"
        f"📦 Остаток: {row['stock']:.0f} шт.{stock_warn}"
    )


async def send_safe(bot: Bot, user_id: int, text: str):
    """Отправка с защитой от 429 Too Many Requests."""
    try:
        await bot.send_message(user_id, text, parse_mode="HTML")
        await asyncio.sleep(0.06)  # Микропауза против флуд-бана
    except TelegramRetryAfter as e:
        logger.warning(f"Rate limit для {user_id}: ждём {e.retry_after}с")
        await asyncio.sleep(e.retry_after)
        await bot.send_message(user_id, text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Не удалось отправить {user_id}: {e}")


async def sender_loop(bot: Bot):
    """Основной цикл: ищет новые скидки и шлёт их."""
    init_sender_schema()
    logger.info("[Sender] Цикл рассылки запущен")

    while True:
        try:
            conn = get_conn()
            # Берём активных пользователей (role='user', порог < 100%)
            users = conn.execute(
                "SELECT user_id, threshold_pct, last_notify_ts "
                "FROM users WHERE role='user' AND threshold_pct < 100"
            ).fetchall()

            for u in users:
                last_ts = u["last_notify_ts"] or 0
                # Ищем новые скидки для этого пользователя
                # В реальной версии здесь будет JOIN с user_stores и фильтр категорий
                rows = conn.execute("""
                    SELECT p.plu, pr.name, p.price, p.regular, p.discount_pct, p.stock, s.address
                    FROM prices p
                    JOIN products pr ON pr.plu = p.plu
                    JOIN stores s ON s.store_id = p.store_id
                    WHERE p.ts > ? AND p.discount_pct >= ? AND p.stock > 0
                    ORDER BY p.discount_pct DESC LIMIT 5
                """, (last_ts, u["threshold_pct"])).fetchall()

                if rows:
                    logger.info(f"[Sender] Найдено {len(rows)} скидок для {u['user_id']}")
                    for r in rows:
                        await send_safe(bot, u["user_id"], format_discount_card(dict(r)))
                    # Обновляем время последней отправки
                    now_ts = int(datetime.now(MSK).timestamp())
                    conn.execute(
                        "UPDATE users SET last_notify_ts = ? WHERE user_id = ?",
                        (now_ts, u["user_id"])
                    )
                    conn.commit()

            conn.close()
        except Exception as e:
            logger.error(f"[Sender] Ошибка цикла: {e}")

        await asyncio.sleep(60)  # Проверка раз в минуту
