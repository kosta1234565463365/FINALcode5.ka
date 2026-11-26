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
# === ПЛАТЕЖИ И ФИНАНСЫ ===
c.execute("""
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    tariff_id INTEGER NOT NULL,
    amount_rub INTEGER NOT NULL,
    payment_method TEXT DEFAULT '',
    payment_id TEXT DEFAULT '',
    status TEXT CHECK(status IN ('pending','paid','refunded','failed')),
    confirmed_by INTEGER DEFAULT NULL,
    confirmed_at INTEGER DEFAULT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(user_id),
    FOREIGN KEY(tariff_id) REFERENCES tariffs(id)
);""")
c.execute("CREATE INDEX IF NOT EXISTS idx_pay_user ON payments(user_id, status);")

c.execute("""
CREATE TABLE IF NOT EXISTS refunds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    payment_id INTEGER NOT NULL,
    refund_type TEXT CHECK(refund_type IN ('full','duplicate')),
    amount_rub INTEGER NOT NULL,
    initiated_by INTEGER NOT NULL,
    approved_by INTEGER DEFAULT NULL,
    executed_by INTEGER DEFAULT NULL,
    status TEXT CHECK(status IN ('pending','approved','executed','cancelled')),
    reason TEXT DEFAULT '',
    created_at INTEGER NOT NULL,
    FOREIGN KEY(payment_id) REFERENCES payments(id)
);""")

c.execute("""
CREATE TABLE IF NOT EXISTS balance_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    amount INTEGER NOT NULL,
    op_type TEXT CHECK(op_type IN ('referral_bonus','payment','refund','admin_adjust')),
    ref_id INTEGER DEFAULT NULL,
    description TEXT DEFAULT '',
    ts INTEGER NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(user_id)
);""")
c.execute("CREATE INDEX IF NOT EXISTS idx_bal_user ON balance_ledger(user_id, ts);")

c.execute("""
CREATE TABLE IF NOT EXISTS referrals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    referrer_id INTEGER NOT NULL,
    referred_id INTEGER NOT NULL UNIQUE,
    bonus_rub INTEGER DEFAULT 30,
    bonus_granted_at INTEGER DEFAULT NULL,
    bonus_revoked_at INTEGER DEFAULT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY(referrer_id) REFERENCES users(user_id),
    FOREIGN KEY(referred_id) REFERENCES users(user_id)
);""")

# === ПОДДЕРЖКА И ТИКЕТЫ ===
c.execute("""
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    assigned_to INTEGER DEFAULT NULL,
    closed_by INTEGER DEFAULT NULL,
    status TEXT CHECK(status IN ('open','assigned','closed')),
    rating INTEGER DEFAULT NULL CHECK(rating IS NULL OR (rating >= 1 AND rating <= 5)),
    rating_comment TEXT DEFAULT '',
    created_at INTEGER NOT NULL,
    closed_at INTEGER DEFAULT NULL,
    FOREIGN KEY(user_id) REFERENCES users(user_id)
);""")
c.execute("CREATE INDEX IF NOT EXISTS idx_tkt_status ON tickets(status, assigned_to);")

c.execute("""
CREATE TABLE IF NOT EXISTS ticket_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id INTEGER NOT NULL,
    sender_type TEXT CHECK(sender_type IN ('user','operator','system')),
    sender_id INTEGER DEFAULT NULL,
    text TEXT NOT NULL,
    ts INTEGER NOT NULL,
    FOREIGN KEY(ticket_id) REFERENCES tickets(id)
);""")

# === ТЕСТИРОВАНИЕ ===
c.execute("""
CREATE TABLE IF NOT EXISTS tester_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tester_id INTEGER NOT NULL,
    store_id TEXT NOT NULL,
    started_at INTEGER NOT NULL,
    finished_at INTEGER DEFAULT NULL,
    total_items INTEGER DEFAULT 0,
    errors_found INTEGER DEFAULT 0,
    finalized INTEGER DEFAULT 0,
    FOREIGN KEY(tester_id) REFERENCES staff(user_id),
    FOREIGN KEY(store_id) REFERENCES stores(store_id)
);""")

c.execute("""
CREATE TABLE IF NOT EXISTS tester_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    plu TEXT NOT NULL,
    api_price REAL,
    tsd_price REAL DEFAULT NULL,
    paper_price REAL DEFAULT NULL,
    api_stock REAL,
    real_stock REAL DEFAULT NULL,
    promo_type TEXT DEFAULT '',
    photo_msg_id INTEGER DEFAULT NULL,
    ts INTEGER NOT NULL,
    FOREIGN KEY(session_id) REFERENCES tester_sessions(id)
);""")

c.execute("""
CREATE TABLE IF NOT EXISTS tester_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_type TEXT CHECK(ticket_type IN ('user_error','admin_task')),
    source_id INTEGER DEFAULT NULL,
    store_id TEXT NOT NULL,
    assigned_to INTEGER NOT NULL,
    assigned_by INTEGER NOT NULL,
    status TEXT CHECK(status IN ('assigned','done','expired')),
    sla_deadline INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    finished_at INTEGER DEFAULT NULL
);""")

# === БЕЗОПАСНОСТЬ И КОДЫ ===
c.execute("""
CREATE TABLE IF NOT EXISTS access_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    store_id TEXT NOT NULL,
    action TEXT CHECK(action IN ('pause','delete')),
    generated_for INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    used INTEGER DEFAULT 0,
    used_by INTEGER DEFAULT NULL,
    used_at INTEGER DEFAULT NULL,
    FOREIGN KEY(store_id) REFERENCES stores(store_id)
);""")

c.execute("""
CREATE TABLE IF NOT EXISTS code_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    code TEXT NOT NULL,
    success INTEGER NOT NULL,
    ts INTEGER NOT NULL
);""")

c.execute("""
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    actor_id INTEGER DEFAULT NULL,
    actor_role TEXT DEFAULT '',
    actor_name TEXT DEFAULT '',
    action TEXT NOT NULL,
    object_type TEXT DEFAULT '',
    object_id TEXT DEFAULT '',
    before_json TEXT DEFAULT '{}',
    after_json TEXT DEFAULT '{}',
    source TEXT DEFAULT 'system'
);""")
c.execute("CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor_id, ts);")
c.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action, ts);")

# === ОЧЕРЕДЬ И ВОЛНЫ ===
c.execute("""
CREATE TABLE IF NOT EXISTS task_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    priority INTEGER NOT NULL,
    task_type TEXT NOT NULL,
    payload_json TEXT DEFAULT '{}',
    status TEXT CHECK(status IN ('pending','running','done','failed')),
    created_at INTEGER NOT NULL,
    started_at INTEGER DEFAULT NULL,
    finished_at INTEGER DEFAULT NULL
);""")
c.execute("CREATE INDEX IF NOT EXISTS idx_tq_prio ON task_queue(priority, status);")

c.execute("""
CREATE TABLE IF NOT EXISTS wave_schedule (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    wave_number INTEGER NOT NULL,
    base_time TEXT NOT NULL,
    actual_time TEXT NOT NULL,
    executed INTEGER DEFAULT 0,
    UNIQUE(date, wave_number)
);""")

c.execute("""
CREATE TABLE IF NOT EXISTS wave_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wave_number INTEGER NOT NULL,
    started_at INTEGER NOT NULL,
    finished_at INTEGER DEFAULT NULL,
    stores_scanned INTEGER DEFAULT 0,
    products_found INTEGER DEFAULT 0,
    status TEXT DEFAULT 'running'
);""")

# === КАНАЛ И КОНТЕНТ ===
c.execute("""
CREATE TABLE IF NOT EXISTS channel_config (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    channel_id INTEGER DEFAULT NULL,
    channel_username TEXT DEFAULT '',
    is_active INTEGER DEFAULT 0,
    bound_at INTEGER DEFAULT NULL
);""")
c.execute("INSERT OR IGNORE INTO channel_config (id) VALUES (1);")

c.execute("""
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_message_id INTEGER DEFAULT NULL,
    style TEXT DEFAULT 'expert',
    text TEXT NOT NULL,
    media_url TEXT DEFAULT '',
    button_text TEXT DEFAULT '',
    button_url TEXT DEFAULT '',
    utm_tag TEXT DEFAULT '',
    status TEXT CHECK(status IN ('draft','scheduled','published','cancelled')),
    scheduled_at INTEGER DEFAULT NULL,
    published_at INTEGER DEFAULT NULL,
    author_id INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);""")
c.execute("CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status, scheduled_at);")

c.execute("""
CREATE TABLE IF NOT EXISTS post_clicks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    clicked_at INTEGER NOT NULL,
    FOREIGN KEY(post_id) REFERENCES posts(id)
);""")

c.execute("""
CREATE TABLE IF NOT EXISTS bot_texts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_key TEXT NOT NULL UNIQUE,
    text TEXT NOT NULL,
    is_default INTEGER DEFAULT 0,
    edited_by INTEGER DEFAULT NULL,
    edited_at INTEGER DEFAULT NULL
);""")

# === СИМУЛЯТОРЫ (is_sim=1 изоляция) ===
c.execute("""
CREATE TABLE IF NOT EXISTS sim_users (
    user_id INTEGER PRIMARY KEY,
    sim_group TEXT DEFAULT '',
    created_at INTEGER NOT NULL
);""")

c.execute("""
CREATE TABLE IF NOT EXISTS sim_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sim_type TEXT CHECK(sim_type IN ('bots','discounts','store')),
    started_at INTEGER NOT NULL,
    finished_at INTEGER DEFAULT NULL,
    virtual_users INTEGER DEFAULT 0,
    total_actions INTEGER DEFAULT 0,
    p50_ms REAL DEFAULT 0,
    p95_ms REAL DEFAULT 0,
    p99_ms REAL DEFAULT 0,
    peak_ram_mb INTEGER DEFAULT 0,
    errors INTEGER DEFAULT 0,
    author_id INTEGER NOT NULL
);""")

conn.commit()
conn.close()
print("[DB] Схема инициализирована (часть 2/2 - полный файл готов)")


# ============================================================
# ЕДИНЫЙ ИНТЕРФЕЙС ЗАПИСИ/ЧТЕНИЯ (другие модули работают только через него)
# ============================================================

def upsert_user(user_id: int, username: str = '', full_name: str = '', role: str = 'user') -> None:
    """Создать или обновить пользователя."""
    now = int(datetime.now(MSK).timestamp())
    with get_conn() as c:
        c.execute("""
        INSERT INTO users (user_id, username, full_name, role, created_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username=excluded.username,
            full_name=excluded.full_name,
            last_seen_at=excluded.last_seen_at
        """, (user_id, username, full_name, role, now, now))


def get_user(user_id: int) -> Optional[sqlite3.Row]:
    """Получить пользователя по ID."""
    with get_conn() as c:
        return c.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()


def set_user_store(user_id: int, store_id: str) -> None:
    """Привязать магазин к пользователю."""
    with get_conn() as c:
        c.execute("""
        INSERT OR IGNORE INTO user_stores (user_id, store_id) VALUES (?, ?)
        """, (user_id, store_id))


def get_user_stores(user_id: int) -> List[str]:
    """Получить список магазинов пользователя."""
    with get_conn() as c:
        rows = c.execute("""
        SELECT store_id FROM user_stores WHERE user_id=?
        """, (user_id,)).fetchall()
        return [r["store_id"] for r in rows]


def upsert_store(store_id: str, sap_code: str, city: str, address: str,
                 lon: float, lat: float, network: str = 'pyaterochka') -> None:
    """Добавить магазин в справочник."""
    now = int(datetime.now(MSK).timestamp())
    with get_conn() as c:
        c.execute("""
        INSERT INTO stores (store_id, sap_code, city, address, lon, lat, network, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'expected', ?)
        ON CONFLICT(store_id) DO UPDATE SET
            sap_code=excluded.sap_code,
            city=excluded.city,
            address=excluded.address,
            lon=excluded.lon,
            lat=excluded.lat
        """, (store_id, sap_code, city, address, lon, lat, network, now))


def set_store_status(store_id: str, new_status: str, actor_type: str = 'system',
                     actor_id: Optional[int] = None, reason: str = '') -> str:
    """Изменить статус магазина с записью в историю. Возвращает предыдущий статус."""
    now = int(datetime.now(MSK).timestamp())
    with get_conn() as c:
        row = c.execute("SELECT status FROM stores WHERE store_id=?", (store_id,)).fetchone()
        if not row:
            raise ValueError(f"Магазин {store_id} не найден")
        old_status = row["status"]
        if old_status == new_status:
            return old_status
        c.execute("""
        UPDATE stores SET status=?, prev_status=?, last_seen_at=? WHERE store_id=?
        """, (new_status, old_status, now, store_id))
        c.execute("""
        INSERT INTO store_status_history
        (store_id, from_status, to_status, reason, actor_type, actor_id, ts)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (store_id, old_status, new_status, reason, actor_type, actor_id, now))
        return old_status


def record_price(store_id: str, plu: str, name: str, regular: float, price: float,
                 stock: float, source: str = 'api', category_name: str = '',
                 img_url: str = '', promo_type: str = '') -> None:
    """Записать цену. Только при реальном сдвиге (вызывать из collector после проверки)."""
    now = int(datetime.now(MSK).timestamp())
    with get_conn() as c:
        # Upsert товара
        c.execute("""
        INSERT INTO products (plu, name, category_name, img_url, first_seen_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(plu) DO UPDATE SET
            name=excluded.name,
            category_name=COALESCE(NULLIF(excluded.category_name,''), products.category_name),
            img_url=COALESCE(NULLIF(excluded.img_url,''), products.img_url),
            last_seen_at=excluded.last_seen_at
        """, (plu, name, category_name, img_url, now, now))
        # Проверка: была ли такая же цена недавно (без дублей)
        last = c.execute("""
        SELECT regular, price, stock FROM prices
        WHERE store_id=? AND plu=?
        ORDER BY ts DESC LIMIT 1
        """, (store_id, plu)).fetchone()
        if last and abs(last["regular"] - regular) < 0.01 \
                and abs(last["price"] - price) < 0.01 \
                and abs(last["stock"] - stock) < 0.01:
            return  # Дубликат, не пишем
        discount_pct = round((1 - price / regular) * 100, 1) if regular > 0 else 0
        c.execute("""
        INSERT INTO prices
        (store_id, plu, regular, price, discount_pct, stock, source, promo_type, ts)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (store_id, plu, regular, price, discount_pct, stock, source, promo_type, now))


def audit(actor_id: Optional[int], actor_role: str, action: str,
          object_type: str = '', object_id: str = '',
          before: Optional[Dict] = None, after: Optional[Dict] = None,
          source: str = 'system', actor_name: str = '') -> None:
    """Единая запись в аудит-лог."""
    import json as _json
    now = int(datetime.now(MSK).timestamp())
    with get_conn() as c:
        c.execute("""
        INSERT INTO audit_log
        (ts, actor_id, actor_role, actor_name, action, object_type, object_id,
         before_json, after_json, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (now, actor_id, actor_role, actor_name, action, object_type, str(object_id),
              _json.dumps(before or {}, ensure_ascii=False),
              _json.dumps(after or {}, ensure_ascii=False), source))


# ============================================================
# ТОЧКА ВХОДА ПРИ ПЕРВОМ ЗАПУСКЕ
# ============================================================

if __name__ == "__main__":
    print(f"[DB] Инициализация схемы в {DB_PATH}")
    init_schema()
    print("[DB] Готово. Все таблицы созданы.")
