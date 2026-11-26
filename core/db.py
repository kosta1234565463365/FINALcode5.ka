# core/db.py v1.0 - Часть 1/2
# Схема базы данных SQLite WAL. Идемпотентные миграции.
# Единый интерфейс записи - другие модули работают только через этот файл.

import sqlite3
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any

from core.config import DB_PATH, SANDBOX_MODE

MSK = timezone(timedelta(hours=3))


def get_conn(timeout: int = 30) -> sqlite3.Connection:
    """Получить соединение с БД с настройками WAL и FOREIGN KEYS."""
    conn = sqlite3.connect(str(DB_PATH), timeout=timeout)
    conn.row_factory = sqlite3.Row  # Доступ по именам колонок, не по индексам
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


def init_schema() -> None:
    """Создать все таблицы. Идемпотентно - можно запускать повторно."""
    conn = get_conn()
    c = conn.cursor()

    # === ПОЛЬЗОВАТЕЛИ И РОЛИ ===
    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT DEFAULT '',
        full_name TEXT DEFAULT '',
        role TEXT DEFAULT 'user' CHECK(role IN ('user','sim')),
        city TEXT DEFAULT 'Москва',
        threshold_pct INTEGER DEFAULT 50,
        created_at INTEGER NOT NULL,
        last_seen_at INTEGER NOT NULL,
        active_until INTEGER DEFAULT 0,
        tariff_id INTEGER DEFAULT NULL,
        referral_by INTEGER DEFAULT NULL,
        is_muted INTEGER DEFAULT 0,
        panel_msg_id INTEGER DEFAULT NULL
    );""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS staff (
        user_id INTEGER PRIMARY KEY,
        role TEXT NOT NULL CHECK(role IN ('owner','admin','tester','operator','accountant')),
        full_name TEXT DEFAULT '',
        username TEXT DEFAULT '',
        is_active INTEGER DEFAULT 1,
        added_by INTEGER DEFAULT NULL,
        added_at INTEGER NOT NULL,
        last_action_at INTEGER DEFAULT 0
    );""")

    # === МАГАЗИНЫ И ИХ СТАТУСЫ ===
    c.execute("""
    CREATE TABLE IF NOT EXISTS stores (
        store_id TEXT PRIMARY KEY,
        sap_code TEXT UNIQUE,
        network TEXT DEFAULT 'pyaterochka',
        city TEXT NOT NULL,
        district TEXT DEFAULT '',
        address TEXT NOT NULL,
        lon REAL NOT NULL,
        lat REAL NOT NULL,
        status TEXT DEFAULT 'pending' CHECK(status IN (
            'pending','active','expected','pause','alarm','broken','deleted'
        )),
        prev_status TEXT DEFAULT NULL,
        last_seen_at INTEGER DEFAULT 0,
        last_error TEXT DEFAULT '',
        created_at INTEGER NOT NULL
    );""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_stores_status ON stores(status);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_stores_city ON stores(city);")

    c.execute("""
    CREATE TABLE IF NOT EXISTS store_status_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT NOT NULL,
        from_status TEXT,
        to_status TEXT NOT NULL,
        reason TEXT DEFAULT '',
        actor_type TEXT CHECK(actor_type IN ('system','owner','admin')),
        actor_id INTEGER DEFAULT NULL,
        ts INTEGER NOT NULL,
        FOREIGN KEY(store_id) REFERENCES stores(store_id)
    );""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_ssh_store ON store_status_history(store_id);")

    c.execute("""
    CREATE TABLE IF NOT EXISTS user_stores (
        user_id INTEGER NOT NULL,
        store_id TEXT NOT NULL,
        priority INTEGER DEFAULT 0,
        PRIMARY KEY(user_id, store_id),
        FOREIGN KEY(user_id) REFERENCES users(user_id),
        FOREIGN KEY(store_id) REFERENCES stores(store_id)
    );""")

    # === ТОВАРЫ, ЦЕНЫ, ИСТОРИЯ ===
    c.execute("""
    CREATE TABLE IF NOT EXISTS products (
        plu TEXT PRIMARY KEY,
        barcode TEXT DEFAULT '',
        name TEXT NOT NULL,
        category_id TEXT DEFAULT '',
        category_name TEXT DEFAULT '',
        img_url TEXT DEFAULT '',
        first_seen_at INTEGER NOT NULL,
        last_seen_at INTEGER NOT NULL
    );""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_products_barcode ON products(barcode);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_products_cat ON products(category_id);")

    c.execute("""
    CREATE TABLE IF NOT EXISTS prices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT NOT NULL,
        plu TEXT NOT NULL,
        regular REAL NOT NULL,
        price REAL NOT NULL,
        discount_pct REAL DEFAULT 0,
        stock REAL DEFAULT 0,
        source TEXT CHECK(source IN ('api','tsd','manual')),
        promo_type TEXT DEFAULT '',
        ts INTEGER NOT NULL,
        FOREIGN KEY(store_id) REFERENCES stores(store_id),
        FOREIGN KEY(plu) REFERENCES products(plu)
    );""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_prices_store_plu_ts ON prices(store_id, plu, ts);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_prices_ts ON prices(ts);")

    c.execute("""
    CREATE TABLE IF NOT EXISTS baselines (
        store_id TEXT NOT NULL,
        plu TEXT NOT NULL,
        baseline_price REAL NOT NULL,
        method TEXT CHECK(method IN ('stable_7d','p80_21d')),
        calculated_at INTEGER NOT NULL,
        PRIMARY KEY(store_id, plu)
    );""")

    # === ПОДПИСКИ И ТАРИФЫ ===
    c.execute("""
    CREATE TABLE IF NOT EXISTS tariffs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        price_rub INTEGER NOT NULL,
        duration_days INTEGER NOT NULL,
        max_stores INTEGER NOT NULL,
        max_radars INTEGER NOT NULL,
        is_active INTEGER DEFAULT 1,
        version INTEGER DEFAULT 1,
        created_at INTEGER NOT NULL
    );""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS subscriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        tariff_id INTEGER NOT NULL,
        starts_at INTEGER NOT NULL,
        ends_at INTEGER NOT NULL,
        status TEXT CHECK(status IN ('active','expired','cancelled','refunded')),
        created_at INTEGER NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(user_id),
        FOREIGN KEY(tariff_id) REFERENCES tariffs(id)
    );""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_sub_user ON subscriptions(user_id, status);")

    conn.commit()
    conn.close()
    print("[DB] Схема инициализирована (часть 1/2)")
