# scripts/run_scan.py v1.0
# Ручной живой прогон сбора цен.
# Аргумент = лимит категорий на магазин (для быстрого теста).
# Пример: python scripts/run_scan.py 2   (быстро, 2 категории)
#         python scripts/run_scan.py     (полный обход, долго)
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.db import init_schema, get_conn
from collector.browser import BrowserManager
from collector.fetcher import Fetcher

def main():
    init_schema()
    cat_limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0

    conn = get_conn()
    rows = conn.execute(
        "SELECT store_id FROM stores WHERE status IN ('active','expected')"
    ).fetchall()
    conn.close()
    ids = [r["store_id"] for r in rows]
    if not ids:
        print("[SCAN] Нет магазинов. Сначала: python scripts/seed_stores.py")
        return
    print(f"[SCAN] Магазины: {ids}, лимит категорий: {cat_limit or 'все'}")

    bm = BrowserManager()
    f = Fetcher(bm)
    t0 = time.time()
    try:
        for sid in ids:
            cats = f.get_store_categories(sid)
            if cat_limit:
                cats = cats[:cat_limit]
            total = 0
            for cat in cats:
                cid = str(cat.get("id") or "")
                cname = str(cat.get("name") or "")
                if not cid:
                    continue
                prods = f.get_category_products(sid, cid, cname)
                for it in prods:
                    f._process_product(sid, it, cname)
                total += len(prods)
                f._random_pause()
            print(f"[SCAN] {sid}: {total} товаров")
    finally:
        bm.stop()
    print(f"[SCAN] Готово за {int(time.time() - t0)} сек")

    conn = get_conn()
    top = conn.execute("""
        SELECT pr.name, p.price, p.regular, p.discount_pct, p.stock, s.address
        FROM prices p
        JOIN products pr ON pr.plu=p.plu
        JOIN stores s ON s.store_id=p.store_id
        WHERE p.stock>0 AND p.regular>p.price
        ORDER BY p.discount_pct DESC LIMIT 10
    """).fetchall()
    conn.close()
    print("[TOP-10 скидок в базе]")
    for r in top:
        print(
            f"-{r['discount_pct']:.0f}% | {r['price']:.0f}р "
            f"(было {r['regular']:.0f}р) | ост:{r['stock']:.0f} "
            f"| {r['address']} | {r['name']}"
        )

if __name__ == "__main__":
    main()
