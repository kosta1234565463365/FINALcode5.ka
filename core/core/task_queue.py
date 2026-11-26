# core/task_queue.py v1.0
# Приоритетная очередь задач с per-store блокировками.
# Защита от голодания конвейера (раздел 26 МАСТЕР-ТЗ).

import sqlite3
import json
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

from core.config import DB_PATH

MSK = timezone(timedelta(hours=3))

# Приоритеты (меньше число = выше приоритет)
PRIO_PROZVON = 1       # Барьер: полная остановка конвейера
PRIO_REFRESH = 2       # 🔄 Живая проверка актуальности
PRIO_ADD_STORE = 3     # Добавление магазина в сеть
PRIO_WAVE = 4          # Волна рассылки
PRIO_PING = 5          # Пинги будильников
PRIO_SIM = 6           # Симуляторы (только ночь)


def get_conn(timeout: int = 30) -> sqlite3.Connection:
    """Получить соединение с БД."""
    conn = sqlite3.connect(str(DB_PATH), timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


def enqueue(task_type: str, priority: int, payload: Optional[Dict] = None) -> int:
    """Добавить задачу в очередь. Возвращает ID задачи."""
    now = int(datetime.now(MSK).timestamp())
    payload_json = json.dumps(payload or {}, ensure_ascii=False)
    with get_conn() as c:
        cur = c.execute(
            "INSERT INTO task_queue (priority, task_type, payload_json, status, created_at) "
            "VALUES (?, ?, ?, 'pending', ?)",
            (priority, task_type, payload_json, now)
        )
        return cur.lastrowid


def fetch_next(max_priority: int = PRIO_SIM) -> Optional[sqlite3.Row]:
    """Взять следующую задачу с приоритетом <= max_priority.
    Атомарно переводит в статус 'running'.
    Возвращает None если очередь пуста.
    """
    now = int(datetime.now(MSK).timestamp())
    with get_conn() as c:
        row = c.execute(
            "SELECT * FROM task_queue "
            "WHERE status='pending' AND priority <= ? "
            "ORDER BY priority ASC, created_at ASC "
            "LIMIT 1",
            (max_priority,)
        ).fetchone()
        if row:
            c.execute(
                "UPDATE task_queue SET status='running', started_at=? WHERE id=?",
                (now, row["id"])
            )
        return row


def complete(task_id: int, success: bool = True) -> None:
    """Завершить задачу."""
    now = int(datetime.now(MSK).timestamp())
    status = "done" if success else "failed"
    with get_conn() as c:
        c.execute(
            "UPDATE task_queue SET status=?, finished_at=? WHERE id=?",
            (status, now, task_id)
        )


def is_prozvon_active() -> bool:
    """Проверить, идёт ли сейчас массовый прозвон (барьер)."""
    with get_conn() as c:
        row = c.execute(
            "SELECT COUNT(*) as cnt FROM task_queue "
            "WHERE task_type='prozvon' AND status='running'"
        ).fetchone()
        return row["cnt"] > 0


def count_pending(priority: int) -> int:
    """Количество ожидающих задач с заданным приоритетом."""
    with get_conn() as c:
        row = c.execute(
            "SELECT COUNT(*) as cnt FROM task_queue "
            "WHERE status='pending' AND priority=?",
            (priority,)
        ).fetchone()
        return row["cnt"]


def cleanup_old(days: int = 7) -> int:
    """Удалить завершённые задачи старше N дней. Возвращает количество удалённых."""
    cutoff = int((datetime.now(MSK) - timedelta(days=days)).timestamp())
    with get_conn() as c:
        cur = c.execute(
            "DELETE FROM task_queue WHERE status IN ('done','failed') AND finished_at < ?",
            (cutoff,)
        )
        return cur.rowcount


class StoreLock:
    """Per-store блокировка для предотвращения одновременного опроса одного магазина.
    Используется контекстным менеджером: with StoreLock(store_id): ...
    """

    def __init__(self, store_id: str, timeout_sec: int = 60):
        self.store_id = store_id
        self.timeout_sec = timeout_sec
        self._acquired = False

    def __enter__(self):
        deadline = time.time() + self.timeout_sec
        while time.time() < deadline:
            with get_conn() as c:
                # Проверяем, нет ли уже активной блокировки
                row = c.execute(
                    "SELECT started_at FROM task_queue "
                    "WHERE task_type='store_lock' AND payload_json LIKE ? AND status='running'",
                    (f'%"{self.store_id}"%',)
                ).fetchone()
                if not row:
                    # Блокируем
                    now = int(datetime.now(MSK).timestamp())
                    c.execute(
                        "INSERT INTO task_queue (priority, task_type, payload_json, status, started_at, created_at) "
                        "VALUES (99, 'store_lock', ?, 'running', ?, ?)",
                        (json.dumps({"store_id": self.store_id}), now, now)
                    )
                    self._acquired = True
                    return self
            time.sleep(0.5)
        raise TimeoutError(f"Не удалось получить блокировку для магазина {self.store_id} за {self.timeout_sec} сек")

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._acquired:
            with get_conn() as c:
                c.execute(
                    "DELETE FROM task_queue WHERE task_type='store_lock' AND payload_json LIKE ?",
                    (f'%"{self.store_id}"%',)
                )
        return False
