# scripts/run_scan.py v1.1
# Ручной живой прогон. Использует готовые методы Fetcher.scan_store.
# Аргумент = лимит магазинов (для быстрого теста), по умолчанию все active/expected.
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.db import init_schema, get_conn
from collector.browser import BrowserManager
from collector.fetcher import Fetcher

def main():
    init_schema()
    store_limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0

    conn = get_conn()
    rows = conn.execute(
        "SELECT store_id, address FROM stores WHERE status IN ('active','expected')"
    ).fetchall()
    conn.close()
    stores = [(r["store_id"], r["address"]) for r in rows]
    if not stores:
        print("[SCAN] Нет магазинов. Сначала: python scripts/seed_stores.py")
        return
    if store_limit:
        stores = stores[:store_limit]
    print(f"[SCAN] Магазинов: {len(stores)}")

    bm = BrowserManager()
    f = Fetcher(bm)
    t0 = time.time()
    total_products = 0
    try:
        for sid, addr in stores:
            print(f"\n[SCAN] === {sid} | {addr} ===")
            try:
                count, ok = f.scan_store(sid)
                total_products += count
                print(f"[SCAN] {sid}: {count} товаров (ok={ok})")
            except Exception as e:
                print(f"[SCAN] Ошибка {sid}: {e}")
    finally:
        bm.stop()
    print(f"\n[SCAN] Готово за {int(time.time()-t0)} сек, всего {total_products} товаров")

    conn = get_conn()
    top = conn.execute("""
        SELECT pr.name, p.price, p.regular, p.discount_pct, p.stock, s.address
        FROM prices p
        JOIN products pr ON pr.plu=p.plu
        JOIN stores s ON s.store_id=p.store_id
        WHERE p.stock>0 AND p.regular>p.price
        ORDER BY p.discount_pct DESC LIMIT 10
    """).fetchall()
    total = conn.execute("SELECT COUNT(*) AS c FROM prices").fetchone()["c"]
    conn.close()
    print(f"\n[БАЗА] Всего записей цен: {total}")
    print("[TOP-10 скидок в базе]")
    for r in top:
        print(
            f"-{r['discount_pct']:.0f}% | {r['price']:.0f}р "
            f"(было {r['regular']:.0f}р) | ост:{r['stock']:.0f} "
            f"| {r['address']} | {r['name']}"
        )

if __name__ == "__main__":
    main()
