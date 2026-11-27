# collector/runner.py v1.0
# Главный цикл коллектора: волны через scheduler, режимы через fetcher.
# Волна 1 (07:00) = полный проход всех активных магазинов.
# Волна 2 (08:00) = точечная проверка только скидочных PLU из 07:00 + вчера.
# Остальные волны = полный проход.
# Ночью = очистка базы старше 21 дня.

import time
import logging
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.db import init_schema, get_conn
from collector.browser import BrowserManager
from collector.fetcher import Fetcher
from collector.scheduler import (
    seconds_until_next_wave,
    get_next_wave,
    mark_wave_executed,
)
from collector.watchdog import Watchdog

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("runner")
MSK = timezone(timedelta(hours=3))


def now_ts() -> int:
    return int(datetime.now(MSK).timestamp())


def today_str() -> str:
    return datetime.now(MSK).strftime("%Y-%m-%d")


def get_active_stores() -> list:
    """Список активных магазинов для полного прохода."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT store_id, address FROM stores WHERE status IN ('active','expected')"
    ).fetchall()
    conn.close()
    return [(r["store_id"], r["address"]) for r in rows]


def get_pointcheck_plu() -> dict:
    """PLU для точечной проверки (08:00):
    — скидки от 25% за последние 24 часа,
    — скидочные записи за предыдущий день (вчера).
    Группируем по store_id -> [plu, plu, ...]."""
    cutoff_24h = now_ts() - 24 * 3600
    conn = get_conn()
    rows = conn.execute(
        """SELECT store_id, plu FROM prices
           WHERE discount_pct >= 25 AND stock > 0 AND ts >= ?""",
        (cutoff_24h,),
    ).fetchall()
    conn.close()
    by_store = {}
    for r in rows:
        by_store.setdefault(r["store_id"], []).append(r["plu"])
    return by_store


def cleanup_old_records(days: int = 21) -> int:
    """Удалить записи цен старше 21 дня. Возвращает число удалённых строк."""
    cutoff = now_ts() - days * 86400
    conn = get_conn()
    cur = conn.execute("DELETE FROM prices WHERE ts < ?", (cutoff,))
    conn.commit()
    conn.close()
    return cur.rowcount


def full_pass(fetcher: Fetcher) -> None:
    """Полный проход по всем активным магазинам."""
    stores = get_active_stores()
    if not stores:
        logger.warning("[Runner] Нет активных магазинов")
        return
    logger.info(f"[Runner] Полный проход: {len(stores)} магазинов")
    for sid, addr in stores:
        try:
            count, ok = fetcher.scan_store(sid)
            logger.info(f"[Runner] {sid} | {addr}: {count} товаров (ok={ok})")
        except Exception as e:
            logger.error(f"[Runner] Ошибка {sid}: {e}")


def pointcheck_pass(fetcher: Fetcher) -> None:
    """Точечная проверка 08:00 — только скидочные PLU."""
    by_store = get_pointcheck_plu()
    if not by_store:
        logger.info("[Runner] 08:00: нет скидочных PLU для точечной проверки")
        return
    total_plu = sum(len(v) for v in by_store.values())
    logger.info(
        f"[Runner] 08:00 точечный проход: {len(by_store)} магазинов, {total_plu} PLU"
    )
    for sid, plu_list in by_store.items():
        try:
            updated = fetcher.check_specific_plu(sid, plu_list)
            logger.info(f"[Runner] {sid}: обновлено {updated}/{len(plu_list)} PLU")
        except Exception as e:
            logger.error(f"[Runner] Ошибка {sid}: {e}")


def main() -> None:
    init_schema()
    logger.info("=== 5ka-collector запущен ===")

    bm = BrowserManager()
    f = Fetcher(bm)
    last_cleanup_date = ""

    try:
        while True:
            # Ночная очистка (один раз в сутки, если сейчас 03:xx)
            now = datetime.now(MSK)
            if now.hour == 3 and last_cleanup_date != today_str():
                try:
                    deleted = cleanup_old_records()
                    logger.info(f"[Runner] Ночная очистка: удалено {deleted} записей")
                except Exception as e:
                    logger.error(f"[Runner] Ошибка очистки: {e}")
                last_cleanup_date = today_str()

            # Проверка окна сна
            if bm.is_sleep_time():
                if bm._is_alive:
                    logger.info("[Runner] Окно сна — останавливаем браузер")
                    bm.stop()
                time.sleep(60)
                continue

            # Ждём следующую волну
            secs = seconds_until_next_wave()
            if secs is None:
                logger.info("[Runner] Волн на сегодня больше нет, засыпаем до утра")
                time.sleep(60)
                continue

            if secs > 60:
                logger.info(f"[Runner] До следующей волны: {secs // 60} мин")
                time.sleep(min(secs - 30, 300))
                continue

            # Пора запускать волну
            wave = get_next_wave()
            if not wave:
                time.sleep(30)
                continue

            wnum = wave["wave_number"]
            logger.info(
                f"[Runner] === ВОЛНА {wnum} в {wave['actual_time']} ==="
            )

            try:
                bm.ensure_ready()
                if wnum == 1:
                    # 07:00 — полный проход
                    full_pass(f)
                elif wnum == 2:
                    # 08:00 — точечный проход по скидкам
                    pointcheck_pass(f)
                else:
                    # остальные волны — полный проход
                    full_pass(f)
            except Exception as e:
                logger.error(f"[Runner] Ошибка волны {wnum}: {e}")

            mark_wave_executed(wnum)
            logger.info(f"[Runner] Волна {wnum} завершена")
            time.sleep(60)

    except KeyboardInterrupt:
        logger.info("[Runner] Остановка по Ctrl+C")
    finally:
        bm.stop()
        logger.info("=== 5ka-collector остановлен ===")


if __name__ == "__main__":
    main()
