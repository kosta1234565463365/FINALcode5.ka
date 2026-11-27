# collector/watchdog.py v1.0
# Мониторинг здоровья парсера и магазинов.
# Пустой каталог (HTTP 200) -> 💥 Сломанный. Нет ответа -> ⏰ Будильник.
# Алерты владельцу/админу пишутся в таблицу owner_alerts (бот их забирает).

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict

from core.db import get_conn, set_store_status, audit

logger = logging.getLogger(__name__)
MSK = timezone(timedelta(hours=3))

EMPTY_PASS_LIMIT = 2          # пустых проходов подряд до 💥 Сломанный
CHRONIC_THRESHOLD = 3         # падений в будильник за окно -> хронический
CHRONIC_WINDOW_DAYS = 30
RAM_WARN_PCT = 88             # порог тревоги по памяти
PARSER_FAIL_LIMIT = 2         # падений парсера подряд -> ОЧЕНЬ СРОЧНО


def ensure_alerts_table() -> None:
    """Создать таблицу алертов (идемпотентно)."""
    conn = get_conn()
    conn.execute("""
    CREATE TABLE IF NOT EXISTS owner_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER NOT NULL,
        level TEXT CHECK(level IN ('info','warn','critical')),
        text TEXT NOT NULL,
        store_id TEXT DEFAULT '',
        acknowledged INTEGER DEFAULT 0,
        ack_by INTEGER DEFAULT NULL,
        ack_at INTEGER DEFAULT NULL,
        repeat_group TEXT DEFAULT ''
    );""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_alerts_ack ON owner_alerts(acknowledged, level);")
    conn.commit()
    conn.close()


def _read_ram_pct() -> int:
    """Прочитать占用 памяти из /proc/meminfo (без внешних зависимостей)."""
    try:
        info = {}
        with open("/proc/meminfo", "r") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    info[parts[0].rstrip(":")] = int(parts[1])
        total = info.get("MemTotal", 1)
        available = info.get("MemAvailable", 0)
        return int((total - available) * 100 / total)
    except Exception:
        return 0


class Watchdog:
    """Следит за магазинами и здоровьем системы."""

    def __init__(self):
        ensure_alerts_table()
        self._empty_passes: Dict[str, int] = {}
        self._parser_fails: int = 0

    # === АЛЕРТЫ ===

    def alert(self, text: str, level: str = "info",
              store_id: str = "", repeat_group: str = "") -> None:
        """Записать алерт владельцу/админу. Бот заберёт его из БД."""
        now = int(datetime.now(MSK).timestamp())
        conn = get_conn()
        conn.execute("""
            INSERT INTO owner_alerts (ts, level, text, store_id, repeat_group)
            VALUES (?, ?, ?, ?, ?)
        """, (now, level, text, store_id, repeat_group))
        conn.commit()
        conn.close()
        audit(None, "system", f"alert_{level}", "system", store_id,
              after={"text": text})
        logger.info(f"[Watchdog] {level.upper()}: {text}")

    # === РЕЗУЛЬТАТ ОБХОДА МАГАЗИНА ===

    def report_store_result(self, store_id: str, product_count: int,
                            http_ok: bool = True) -> None:
        """Вызывается после обхода магазина.
        http_ok=False -> нет ответа -> ⏰ Будильник.
        http_ok=True и product_count=0 -> пустой каталог -> 💥 Сломанный.
        """
        if not http_ok:
            self._empty_passes[store_id] = 0
            self._to_alarm(store_id, "нет ответа парсера")
            return

        if product_count > 0:
            self._empty_passes[store_id] = 0
            return

        # HTTP 200, но товаров нет
        count = self._empty_passes.get(store_id, 0) + 1
        self._empty_passes[store_id] = count
        logger.warning(f"[Watchdog] {store_id}: пустой проход {count}/{EMPTY_PASS_LIMIT}")

        if count >= EMPTY_PASS_LIMIT:
            self._to_broken(store_id, f"пустой каталог (HTTP 200), {count} проходов подряд")
            self._empty_passes[store_id] = 0

    def _to_alarm(self, store_id: str, reason: str) -> None:
        """Перевести магазин в ⏰ Будильник."""
        try:
            old = set_store_status(store_id, "alarm", "system", None, reason)
        except ValueError:
            return
        if old == "alarm":
            return
        self.alert(
            f"⏰ Магазин [{store_id}] переведён в БУДИЛЬНИК.\nПричина: {reason}.",
            level="warn", store_id=store_id, repeat_group=f"alarm_{store_id}"
        )
        self._check_chronic(store_id)

    def _to_broken(self, store_id: str, reason: str) -> None:
        """Перевести магазин в 💥 Сломанный."""
        try:
            old = set_store_status(store_id, "broken", "system", None, reason)
        except ValueError:
            return
        if old == "broken":
            return
        self.alert(
            f"💥 Магазин [{store_id}] переведён в СЛОМАННЫЙ.\nПричина: {reason}.",
            level="warn", store_id=store_id, repeat_group=f"broken_{store_id}"
        )

    def _check_chronic(self, store_id: str) -> None:
        """Если магазин падал в будильник >=3 раз за 30 дней — хронический алерт."""
        since = int(datetime.now(MSK).timestamp()) - CHRONIC_WINDOW_DAYS * 86400
        conn = get_conn()
        row = conn.execute("""
            SELECT COUNT(*) AS cnt FROM store_status_history
            WHERE store_id=? AND to_status='alarm' AND ts >= ?
        """, (store_id, since)).fetchone()
        conn.close()
        cnt = row["cnt"] if row else 0
        if cnt >= CHRONIC_THRESHOLD:
            self.alert(
                f"🔁 ХРОНИЧЕСКИЙ БУДИЛЬНИК\nМагазин [{store_id}] падает в будильник "
                f"{cnt}-й раз за {CHRONIC_WINDOW_DAYS} дней.\n"
                f"Рекомендуется проверить прокси или удалить точку.",
                level="warn", store_id=store_id, repeat_group=f"chronic_{store_id}"
            )

    # === ЗДОРОВЬЕ ПАРСЕРА ===

    def report_parser_error(self, error: str) -> None:
        """Падение парсера. После 2 подряд — ОЧЕНЬ СРОЧНО."""
        self._parser_fails += 1
        if self._parser_fails >= PARSER_FAIL_LIMIT:
            self.alert(
                f"🚨 ОЧЕНЬ СРОЧНО!\nПарсер упал {self._parser_fails} раза подряд.\n"
                f"Последняя ошибка: {error}",
                level="critical", repeat_group="parser_down"
            )

    def report_parser_success(self) -> None:
        """Успешный проход — сбросить счётчик падений."""
        self._parser_fails = 0

    def check_ram(self) -> None:
        """Проверка памяти. При >88% — критический алерт."""
        pct = _read_ram_pct()
        if pct >= RAM_WARN_PCT:
            self.alert(
                f"🚨 ОЧЕНЬ СРОЧНО!\nRAM: {pct}% (порог {RAM_WARN_PCT}%).\n"
                f"Рекомендуется перезапуск браузера или апгрейд до 2 ГБ.",
                level="critical", repeat_group="ram_high"
            )

    def run_health_check(self) -> None:
        """Периодическая проверка здоровья системы (вызывать раз в 5 мин)."""
        self.check_ram()
