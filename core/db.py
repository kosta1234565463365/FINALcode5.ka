# core/db.py v1.1 (полный, исправленный)
import sqlite3
import json
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict
from core.config import DB_PATH

MSK = timezone(timedelta(hours=3))

def get_conn(timeout: int = 30) -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn

def init_schema() -> None:
    conn = get_conn()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, username TEXT DEFAULT '', full_name TEXT DEFAULT '',
        role TEXT DEFAULT 'user', city TEXT DEFAULT 'Москва', threshold_pct INTEGER DEFAULT 50,
        created_at INTEGER NOT NULL, last_seen_at INTEGER NOT NULL, active_until INTEGER DEFAULT 0,
        tariff_id INTEGER DEFAULT NULL, referral_by INTEGER DEFAULT NULL,
        is_muted INTEGER DEFAULT 0, panel_msg_id INTEGER DEFAULT NULL);""")
    c.execute("""CREATE TABLE IF NOT EXISTS staff (
        user_id INTEGER PRIMARY KEY, role TEXT NOT NULL, full_name TEXT DEFAULT '',
        username TEXT DEFAULT '', is_active INTEGER DEFAULT 1, added_by INTEGER DEFAULT NULL,
        added_at INTEGER NOT NULL, last_action_at INTEGER DEFAULT 0);""")
    c.execute("""CREATE TABLE IF NOT EXISTS stores (
        store_id TEXT PRIMARY KEY, sap_code TEXT UNIQUE, network TEXT DEFAULT 'pyaterochka',
        city TEXT NOT NULL, district TEXT DEFAULT '', address TEXT NOT NULL,
        lon REAL NOT NULL, lat REAL NOT NULL, status TEXT DEFAULT 'pending',
        prev_status TEXT DEFAULT NULL, last_seen_at INTEGER DEFAULT 0,
        last_error TEXT DEFAULT '', created_at INTEGER NOT NULL);""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_stores_status ON stores(status);")
    c.execute("""CREATE TABLE IF NOT EXISTS store_status_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, store_id TEXT NOT NULL, from_status TEXT,
        to_status TEXT NOT NULL, reason TEXT DEFAULT '', actor_type TEXT,
        actor_id INTEGER DEFAULT NULL, ts INTEGER NOT NULL);""")
    c.execute("""CREATE TABLE IF NOT EXISTS user_stores (
        user_id INTEGER NOT NULL, store_id TEXT NOT NULL, priority INTEGER DEFAULT 0,
        PRIMARY KEY(user_id, store_id));""")
    c.execute("""CREATE TABLE IF NOT EXISTS products (
        plu TEXT PRIMARY KEY, barcode TEXT DEFAULT '', name TEXT NOT NULL,
        category_id TEXT DEFAULT '', category_name TEXT DEFAULT '', img_url TEXT DEFAULT '',
        first_seen_at INTEGER NOT NULL, last_seen_at INTEGER NOT NULL);""")
    c.execute("""CREATE TABLE IF NOT EXISTS prices (
        id INTEGER PRIMARY KEY AUTOINCREMENT, store_id TEXT NOT NULL, plu TEXT NOT NULL,
        regular REAL NOT NULL, price REAL NOT NULL, discount_pct REAL DEFAULT 0,
        stock REAL DEFAULT 0, source TEXT, promo_type TEXT DEFAULT '', ts INTEGER NOT NULL);""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_prices_store_plu_ts ON prices(store_id, plu, ts);")
    c.execute("""CREATE TABLE IF NOT EXISTS baselines (
        store_id TEXT NOT NULL, plu TEXT NOT NULL, baseline_price REAL NOT NULL,
        method TEXT, calculated_at INTEGER NOT NULL, PRIMARY KEY(store_id, plu));""")
    c.execute("""CREATE TABLE IF NOT EXISTS tariffs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, price_rub INTEGER NOT NULL,
        duration_days INTEGER NOT NULL, max_stores INTEGER NOT NULL, max_radars INTEGER NOT NULL,
        is_active INTEGER DEFAULT 1, version INTEGER DEFAULT 1, created_at INTEGER NOT NULL);""")
    c.execute("""CREATE TABLE IF NOT EXISTS subscriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, tariff_id INTEGER NOT NULL,
        starts_at INTEGER NOT NULL, ends_at INTEGER NOT NULL, status TEXT, created_at INTEGER NOT NULL);""")
    c.execute("""CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, tariff_id INTEGER NOT NULL,
        amount_rub INTEGER NOT NULL, payment_method TEXT DEFAULT '', payment_id TEXT DEFAULT '',
        status TEXT, confirmed_by INTEGER DEFAULT NULL, confirmed_at INTEGER DEFAULT NULL,
        created_at INTEGER NOT NULL);""")
    c.execute("""CREATE TABLE IF NOT EXISTS refunds (
        id INTEGER PRIMARY KEY AUTOINCREMENT, payment_id INTEGER NOT NULL, refund_type TEXT,
        amount_rub INTEGER NOT NULL, initiated_by INTEGER NOT NULL, approved_by INTEGER DEFAULT NULL,
        executed_by INTEGER DEFAULT NULL, status TEXT, reason TEXT DEFAULT '', created_at INTEGER NOT NULL);""")
    c.execute("""CREATE TABLE IF NOT EXISTS balance_ledger (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, amount INTEGER NOT NULL,
        op_type TEXT, ref_id INTEGER DEFAULT NULL, description TEXT DEFAULT '', ts INTEGER NOT NULL);""")
    c.execute("""CREATE TABLE IF NOT EXISTS referrals (
        id INTEGER PRIMARY KEY AUTOINCREMENT, referrer_id INTEGER NOT NULL,
        referred_id INTEGER NOT NULL UNIQUE, bonus_rub INTEGER DEFAULT 30,
        bonus_granted_at INTEGER DEFAULT NULL, bonus_revoked_at INTEGER DEFAULT NULL,
        created_at INTEGER NOT NULL);""")
    c.execute("""CREATE TABLE IF NOT EXISTS tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
        assigned_to INTEGER DEFAULT NULL, closed_by INTEGER DEFAULT NULL, status TEXT,
        rating INTEGER DEFAULT NULL, rating_comment TEXT DEFAULT '',
        created_at INTEGER NOT NULL, closed_at INTEGER DEFAULT NULL);""")
    c.execute("""CREATE TABLE IF NOT EXISTS ticket_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id INTEGER NOT NULL, sender_type TEXT,
        sender_id INTEGER DEFAULT NULL, text TEXT NOT NULL, ts INTEGER NOT NULL);""")
    c.execute("""CREATE TABLE IF NOT EXISTS tester_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, tester_id INTEGER NOT NULL, store_id TEXT NOT NULL,
        started_at INTEGER NOT NULL, finished_at INTEGER DEFAULT NULL, total_items INTEGER DEFAULT 0,
        errors_found INTEGER DEFAULT 0, finalized INTEGER DEFAULT 0);""")
    c.execute("""CREATE TABLE IF NOT EXISTS tester_reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER NOT NULL, plu TEXT NOT NULL,
        api_price REAL, tsd_price REAL DEFAULT NULL, paper_price REAL DEFAULT NULL,
        api_stock REAL, real_stock REAL DEFAULT NULL, promo_type TEXT DEFAULT '',
        photo_msg_id INTEGER DEFAULT NULL, ts INTEGER NOT NULL);""")
    c.execute("""CREATE TABLE IF NOT EXISTS tester_assignments (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_type TEXT, source_id INTEGER DEFAULT NULL,
        store_id TEXT NOT NULL, assigned_to INTEGER NOT NULL, assigned_by INTEGER NOT NULL,
        status TEXT, sla_deadline INTEGER NOT NULL, created_at INTEGER NOT NULL,
        finished_at INTEGER DEFAULT NULL);""")
    c.execute("""CREATE TABLE IF NOT EXISTS access_codes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE, store_id TEXT NOT NULL,
        action TEXT, generated_for INTEGER NOT NULL, expires_at INTEGER NOT NULL,
        used INTEGER DEFAULT 0, used_by INTEGER DEFAULT NULL, used_at INTEGER DEFAULT NULL);""")
    c.execute("""CREATE TABLE IF NOT EXISTS code_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, code TEXT NOT NULL,
        success INTEGER NOT NULL, ts INTEGER NOT NULL);""")
    c.execute("""CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, actor_id INTEGER DEFAULT NULL,
        actor_role TEXT DEFAULT '', actor_name TEXT DEFAULT '', action TEXT NOT NULL,
        object_type TEXT DEFAULT '', object_id TEXT DEFAULT '', before_json TEXT DEFAULT '{}',
        after_json TEXT DEFAULT '{}', source TEXT DEFAULT 'system');""")
    c.execute("""CREATE TABLE IF NOT EXISTS task_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT, priority INTEGER NOT NULL, task_type TEXT NOT NULL,
        payload_json TEXT DEFAULT '{}', status TEXT, created_at INTEGER NOT NULL,
        started_at INTEGER DEFAULT NULL, finished_at INTEGER DEFAULT NULL);""")
    c.execute("""CREATE TABLE IF NOT EXISTS wave_schedule (
        id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, wave_number INTEGER NOT NULL,
        base_time TEXT NOT NULL, actual_time TEXT NOT NULL, executed INTEGER DEFAULT 0,
        UNIQUE(date, wave_number));""")
    c.execute("""CREATE TABLE IF NOT EXISTS wave_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, wave_number INTEGER NOT NULL, started_at INTEGER NOT NULL,
        finished_at INTEGER DEFAULT NULL, stores_scanned INTEGER DEFAULT 0,
        products_found INTEGER DEFAULT 0, status TEXT DEFAULT 'running');""")
    c.execute("""CREATE TABLE IF NOT EXISTS channel_config (
        id INTEGER PRIMARY KEY CHECK(id = 1), channel_id INTEGER DEFAULT NULL,
        channel_username TEXT DEFAULT '', is_active INTEGER DEFAULT 0, bound_at INTEGER DEFAULT NULL);""")
    c.execute("INSERT OR IGNORE INTO channel_config (id) VALUES (1);")
    c.execute("""CREATE TABLE IF NOT EXISTS posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, channel_message_id INTEGER DEFAULT NULL,
        style TEXT DEFAULT 'expert', text TEXT NOT NULL, media_url TEXT DEFAULT '',
        button_text TEXT DEFAULT '', button_url TEXT DEFAULT '', utm_tag TEXT DEFAULT '',
        status TEXT, scheduled_at INTEGER DEFAULT NULL, published_at INTEGER DEFAULT NULL,
        author_id INTEGER NOT NULL, created_at INTEGER NOT NULL);""")
    c.execute("""CREATE TABLE IF NOT EXISTS post_clicks (
        id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
        clicked_at INTEGER NOT NULL);""")
    c.execute("""CREATE TABLE IF NOT EXISTS bot_texts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, template_key TEXT NOT NULL UNIQUE, text TEXT NOT NULL,
        is_default INTEGER DEFAULT 0, edited_by INTEGER DEFAULT NULL, edited_at INTEGER DEFAULT NULL);""")
    c.execute("""CREATE TABLE IF NOT EXISTS sim_users (
        user_id INTEGER PRIMARY KEY, sim_group TEXT DEFAULT '', created_at INTEGER NOT NULL);""")
    c.execute("""CREATE TABLE IF NOT EXISTS sim_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, sim_type TEXT, started_at INTEGER NOT NULL,
        finished_at INTEGER DEFAULT NULL, virtual_users INTEGER DEFAULT 0,
        total_actions INTEGER DEFAULT 0, p50_ms REAL DEFAULT 0, p95_ms REAL DEFAULT 0,
        p99_ms REAL DEFAULT 0, peak_ram_mb INTEGER DEFAULT 0, errors INTEGER DEFAULT 0,
        author_id INTEGER NOT NULL);""")
    c.execute("""CREATE TABLE IF NOT EXISTS owner_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, level TEXT, text TEXT NOT NULL,
        store_id TEXT DEFAULT '', acknowledged INTEGER DEFAULT 0, ack_by INTEGER DEFAULT NULL,
        ack_at INTEGER DEFAULT NULL, repeat_group TEXT DEFAULT '');""")
    conn.commit()
    conn.close()
    print("[DB] Схема инициализирована (полная)")

# === ИНТЕРФЕЙС ЗАПИСИ/ЧТЕНИЯ ===
def upsert_user(user_id, username='', full_name='', role='user'):
    now = int(datetime.now(MSK).timestamp())
    conn = get_conn()
    conn.execute("""INSERT INTO users (user_id,username,full_name,role,created_at,last_seen_at)
        VALUES (?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
        username=excluded.username, full_name=excluded.full_name, last_seen_at=excluded.last_seen_at""",
        (user_id, username, full_name, role, now, now))
    conn.commit(); conn.close()

def get_user(user_id):
    conn = get_conn()
    r = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close(); return r

def set_user_store(user_id, store_id):
    conn = get_conn()
    conn.execute("INSERT OR IGNORE INTO user_stores (user_id,store_id) VALUES (?,?)", (user_id, store_id))
    conn.commit(); conn.close()

def get_user_stores(user_id):
    conn = get_conn()
    rows = conn.execute("SELECT store_id FROM user_stores WHERE user_id=?", (user_id,)).fetchall()
    conn.close(); return [r["store_id"] for r in rows]

def upsert_store(store_id, sap_code, city, address, lon, lat, network='pyaterochka'):
    now = int(datetime.now(MSK).timestamp())
    conn = get_conn()
    conn.execute("""INSERT INTO stores (store_id,sap_code,city,address,lon,lat,network,status,created_at)
        VALUES (?,?,?,?,?,?,?,'expected',?) ON CONFLICT(store_id) DO UPDATE SET
        sap_code=excluded.sap_code, city=excluded.city, address=excluded.address,
        lon=excluded.lon, lat=excluded.lat""",
        (store_id, sap_code, city, address, lon, lat, network, now))
    conn.commit(); conn.close()

def set_store_status(store_id, new_status, actor_type='system', actor_id=None, reason=''):
    now = int(datetime.now(MSK).timestamp())
    conn = get_conn()
    row = conn.execute("SELECT status FROM stores WHERE store_id=?", (store_id,)).fetchone()
    if not row:
        conn.close(); raise ValueError(f"Магазин {store_id} не найден")
    old = row["status"]
    if old == new_status:
        conn.close(); return old
    conn.execute("UPDATE stores SET status=?, prev_status=?, last_seen_at=? WHERE store_id=?",
        (new_status, old, now, store_id))
    conn.execute("""INSERT INTO store_status_history
        (store_id,from_status,to_status,reason,actor_type,actor_id,ts) VALUES (?,?,?,?,?,?,?)""",
        (store_id, old, new_status, reason, actor_type, actor_id, now))
    conn.commit(); conn.close(); return old

def record_price(store_id, plu, name, regular, price, stock, source='api',
                 category_name='', img_url='', promo_type=''):
    now = int(datetime.now(MSK).timestamp())
    conn = get_conn()
    conn.execute("""INSERT INTO products (plu,name,category_name,img_url,first_seen_at,last_seen_at)
        VALUES (?,?,?,?,?,?) ON CONFLICT(plu) DO UPDATE SET name=excluded.name,
        category_name=COALESCE(NULLIF(excluded.category_name,''),products.category_name),
        img_url=COALESCE(NULLIF(excluded.img_url,''),products.img_url),
        last_seen_at=excluded.last_seen_at""", (plu, name, category_name, img_url, now, now))
    last = conn.execute("""SELECT regular,price,stock FROM prices WHERE store_id=? AND plu=?
        ORDER BY ts DESC LIMIT 1""", (store_id, plu)).fetchone()
    if last and abs(last["regular"]-regular)<0.01 and abs(last["price"]-price)<0.01 and abs(last["stock"]-stock)<0.01:
        conn.close(); return
    disc = round((1-price/regular)*100,1) if regular>0 else 0
    conn.execute("""INSERT INTO prices (store_id,plu,regular,price,discount_pct,stock,source,promo_type,ts)
        VALUES (?,?,?,?,?,?,?,?,?)""", (store_id, plu, regular, price, disc, stock, source, promo_type, now))
    conn.commit(); conn.close()

def audit(actor_id, actor_role, action, object_type='', object_id='',
          before=None, after=None, source='system', actor_name=''):
    now = int(datetime.now(MSK).timestamp())
    conn = get_conn()
    conn.execute("""INSERT INTO audit_log (ts,actor_id,actor_role,actor_name,action,object_type,
        object_id,before_json,after_json,source) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (now, actor_id, actor_role, actor_name, action, object_type, str(object_id),
         json.dumps(before or {}, ensure_ascii=False), json.dumps(after or {}, ensure_ascii=False), source))
    conn.commit(); conn.close()

if __name__ == "__main__":
    init_schema()
    print("[DB] Готово.")
