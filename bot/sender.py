# bot/sender.py v1.0 — рассылка скидок подписчикам
import asyncio, time, logging
from aiogram import Bot
from core.db import get_conn, now_ts

logger = logging.getLogger(__name__)

def ensure_sender_tables():
    c = get_conn()
    c.execute("""CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL, store_id TEXT, plu TEXT, name TEXT,
        regular REAL, price REAL, pct INTEGER, stock REAL,
        img_url TEXT DEFAULT '', sent INTEGER DEFAULT 0, ts INTEGER NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS active_alerts (
        user_id INTEGER, store_id TEXT, plu TEXT, PRIMARY KEY(user_id, store_id, plu))""")
    c.commit(); c.close()

def generate_notifications():
    """После каждого прохода парсера: найти новые скидки, отфильтровать по пользователям."""
    c = get_conn()
    cutoff = now_ts() - 40*60
    new_discounts = c.execute("""
        SELECT p.store_id, p.plu, pr.name, p.regular, p.price, p.discount_pct, p.stock, pr.img_url, s.address
        FROM prices p
        JOIN products pr ON pr.plu=p.plu
        JOIN stores s ON s.store_id=p.store_id
        WHERE p.ts >= ? AND p.discount_pct >= 25 AND p.stock > 0
    """, (cutoff,)).fetchall()
    
    users = c.execute("SELECT user_id, threshold_pct FROM users WHERE role='user'").fetchall()
    stores_by_user = {}
    for u in users:
        rows = c.execute("SELECT store_id FROM user_stores WHERE user_id=?", (u["user_id"],)).fetchall()
        stores_by_user[u["user_id"]] = {r["store_id"] for r in rows}
    
    added = 0
    for d in new_discounts:
        pct = int(d["discount_pct"])
        for u in users:
            if pct < u["threshold_pct"]:
                continue
            user_stores = stores_by_user.get(u["user_id"], set())
            if user_stores and d["store_id"] not in user_stores:
                continue
            exists = c.execute("SELECT 1 FROM active_alerts WHERE user_id=? AND store_id=? AND plu=?",
                               (u["user_id"], d["store_id"], d["plu"])).fetchone()
            if exists:
                continue
            c.execute("INSERT INTO notifications (user_id,store_id,plu,name,regular,price,pct,stock,img_url,ts) VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (u["user_id"], d["store_id"], d["plu"], d["name"], d["regular"], d["price"], pct, d["stock"], d["img_url"], now_ts()))
            c.execute("INSERT OR IGNORE INTO active_alerts (user_id,store_id,plu) VALUES(?,?,?)",
                      (u["user_id"], d["store_id"], d["plu"]))
            added += 1
    c.commit(); c.close()
    return added

async def send_pending(bot: Bot, limit=50):
    c = get_conn()
    rows = c.execute("SELECT * FROM notifications WHERE sent=0 ORDER BY id LIMIT ?", (limit,)).fetchall()
    c.close()
    for r in rows:
        stock_warn = " ⚠️ Остаток мал!" if r["stock"] <= 2 else ""
        text = (
            f"🔥 <b>-{r['pct']}%</b>\n"
            f"<b>{r['name']}</b>\n"
            f"💰 {r['price']:.0f}₽ (было {r['regular']:.0f}₽)\n"
            f"📦 Остаток: {r['stock']:.0f} шт{stock_warn}\n"
            f"🏪 Магазин: {r['store_id']}"
        )
        try:
            if r["img_url"]:
                await bot.send_photo(r["user_id"], r["img_url"], caption=text, parse_mode="HTML")
            else:
                await bot.send_message(r["user_id"], text, parse_mode="HTML")
            c = get_conn()
            c.execute("UPDATE notifications SET sent=1 WHERE id=?", (r["id"],))
            c.commit(); c.close()
            await asyncio.sleep(0.06)
        except Exception as e:
            logger.warning(f"[Sender] ошибка отправки {r['id']}: {e}")
            c = get_conn()
            c.execute("UPDATE notifications SET sent=1 WHERE id=?", (r["id"],))
            c.commit(); c.close()

async def sender_loop(bot: Bot):
    """Фоновая задача: раз в 60 сек генерировать и отправлять уведомления."""
    ensure_sender_tables()
    logger.info("[Sender] цикл запущен")
    while True:
        try:
            added = generate_notifications()
            if added:
                logger.info(f"[Sender] сгенерировано {added} уведомлений")
            await send_pending(bot)
        except Exception as e:
            logger.error(f"[Sender] ошибка цикла: {e}")
        await asyncio.sleep(60)
