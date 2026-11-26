# core/config.py v1.0
# Единый файл конфигурации проекта «Монитор скидок Пятёрочки».
# Все числа, пороги и параметры ТОЛЬКО здесь.

import os
from pathlib import Path

# === БАЗОВЫЕ ПУТИ ===
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "5ka_data.db"

# === РЕЖИМЫ И ФЛАГИ ===
SANDBOX_MODE = os.getenv("SANDBOX_MODE", "0") == "1"
IS_SIMULATION = False  # Переключается симулятором при запуске

# === РАСПИСАНИЕ И ВОЛНЫ ===
# Строгие часы (никогда не сдвигаются)
WAVE_07_00 = "07:00"
WAVE_08_00 = "08:00"

# Базовое время для остальных волн (рандомизируется ±30 мин при старте дня)
WAVE_BASE_TIMES = [
    "09:30", "11:00", "12:30", "14:00",
    "15:30", "17:30", "19:30", "21:30"
]

# Окно сна парсера
SLEEP_START = "21:30"
SLEEP_END = "06:00"
BACKUP_TIME = "03:30"

# === ПРАВИЛА ПАРСЕРА (Camoufox) ===
BROWSER_RESTART_AFTER_REQUESTS = 65
MIN_PAUSE_SEC = 3.8
MAX_PAUSE_SEC = 6.2
REQUEST_TIMEOUT_SEC = 60

# === ПРАВИЛА АНТИФЕЙКА И ЦЕН ===
BASELINE_STABLE_DAYS = 7
BASELINE_TOLERANCE_PCT = 3.0
HIDDEN_CRASH_PCT = 15.0
HIDDEN_CRASH_ABS_RUB = 10.0

# === ПРАВИЛА ОСТАТКОВ ===
MORNING_WAVE_MIN_STOCK = 5
MORNING_CHECK_MIN_STOCK = 1
LOW_STOCK_WARNING_THRESHOLD = 2
RESTOCK_TRIGGER_ABS = 5
RESTOCK_TRIGGER_GROWTH = 3

# === ПРАВИЛА 🔄 ПРОВЕРКИ АКТУАЛЬНОСТИ ===
FRESH_TTL_MINUTES = 40
REFRESH_COOLDOWN_SEC = 20
REFRESH_WINDOW_START = "08:00"
REFRESH_WINDOW_END = "21:40"

# === ТЕСТИРОВАНИЕ И СТАТУСЫ ===
TSD_ERROR_THRESHOLD_PCT = 20.0

# === БЕЗОПАСНОСТЬ И КОДЫ ===
ACCESS_CODE_TTL_HOURS = 24
MAX_CODE_ATTEMPTS = 5
CODE_BLOCK_DURATION_HOURS = 24

# === ТЕЛЕГРАМ ЛИМИТЫ ===
TG_MAX_MEDIA_GROUP = 10
TG_MAX_MESSAGE_LENGTH = 4096
