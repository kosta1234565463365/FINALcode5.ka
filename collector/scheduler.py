# collector/scheduler.py v1.0
# Расписание волн: 07:00/08:00 строго, остальные ±30 мин, защита от слипания.

import random
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional

from core.config import WAVE_07_00, WAVE_08_00, WAVE_BASE_TIMES
from core.db import get_conn

logger = logging.getLogger(__name__)
MSK = timezone(timedelta(hours=3))

WAVE_RANDOM_JITTER_MIN = 30
MIN_GAP_BETWEEN_WAVES_MIN = 60
LAST_WAVE_DEADLINE = "22:00"


def _time_to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _minutes_to_time(minutes: int) -> str:
    minutes = max(0, min(minutes, 23 * 60 + 59))
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def generate_daily_schedule(date_str: str) -> List[Dict]:
    waves = [
        {"wave_number": 1, "base_time": WAVE_07_00, "actual_time": WAVE_07_00},
        {"wave_number": 2, "base_time": WAVE_08_00, "actual_time": WAVE_08_00},
    ]
    prev_minutes = _time_to_minutes(WAVE_08_00)

    for i, base in enumerate(WAVE_BASE_TIMES, start=3):
        base_min = _time_to_minutes(base)
        jitter = random.randint(-WAVE_RANDOM_JITTER_MIN, WAVE_RANDOM_JITTER_MIN)
        actual_min = max(base_min + jitter, prev_minutes + MIN_GAP_BETWEEN_WAVES_MIN)
        actual_min = min(actual_min, _time_to_minutes(LAST_WAVE_DEADLINE))
        actual_time = _minutes_to_time(actual_min)
        waves.append({"wave_number": i, "base_time": base, "actual_time": actual_time})
        prev_minutes = actual_min

    conn = get_conn()
    for w in waves:
        conn.execute("""
            INSERT INTO wave_schedule (date, wave_number, base_time, actual_time, executed)
            VALUES (?, ?, ?, ?, 0)
            ON CONFLICT(date, wave_number) DO UPDATE SET
                base_time=excluded.base_time, actual_time=excluded.actual_time
        """, (date_str, w["wave_number"], w["base_time"], w["actual_time"]))
    conn.commit()
    conn.close()
    logger.info(f"[Scheduler] Расписание на {date_str}: {[w['actual_time'] for w in waves]}")
    return waves


def get_today_schedule() -> List[Dict]:
    today = datetime.now(MSK).strftime("%Y-%m-%d")
    conn = get_conn()
    rows = conn.execute("""
        SELECT wave_number, base_time, actual_time, executed
        FROM wave_schedule WHERE date=? ORDER BY wave_number
    """, (today,)).fetchall()
    conn.close()
    if rows:
        return [dict(r) for r in rows]
    return generate_daily_schedule(today)


def get_next_wave() -> Optional[Dict]:
    now = datetime.now(MSK)
    now_min = now.hour * 60 + now.minute
    for w in get_today_schedule():
        if w["executed"]:
            continue
        if _time_to_minutes(w["actual_time"]) >= now_min:
            return w
    return None


def mark_wave_executed(wave_number: int) -> None:
    today = datetime.now(MSK).strftime("%Y-%m-%d")
    conn = get_conn()
    conn.execute("UPDATE wave_schedule SET executed=1 WHERE date=? AND wave_number=?",
                 (today, wave_number))
    conn.commit()
    conn.close()


def seconds_until_next_wave() -> Optional[int]:
    nxt = get_next_wave()
    if not nxt:
        return None
    now = datetime.now(MSK)
    now_min = now.hour * 60 + now.minute
    diff_min = _time_to_minutes(nxt["actual_time"]) - now_min
    return max(0, diff_min * 60 - now.second)


def shift_wave(wave_number: int, delay_min: int) -> None:
    """Сдвиг волны при нагрузке. Волны 1 и 2 (07:00/08:00) не сдвигаются."""
    if wave_number <= 2:
        logger.warning(f"[Scheduler] Волну {wave_number} нельзя сдвигать")
        return
    today = datetime.now(MSK).strftime("%Y-%m-%d")
    conn = get_conn()
    row = conn.execute("SELECT actual_time FROM wave_schedule WHERE date=? AND wave_number=?",
                       (today, wave_number)).fetchone()
    if row:
        new_min = min(_time_to_minutes(row["actual_time"]) + delay_min,
                      _time_to_minutes(LAST_WAVE_DEADLINE))
        conn.execute("UPDATE wave_schedule SET actual_time=? WHERE date=? AND wave_number=?",
                     (_minutes_to_time(new_min), today, wave_number))
        conn.commit()
        logger.info(f"[Scheduler] Волна {wave_number} сдвинута → {_minutes_to_time(new_min)}")
    conn.close()
