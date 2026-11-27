# scripts/seed_stores.py v1.0 — справочник магазинов через терминал (без правки файлов).
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import argparse
from core.db import get_conn, upsert_store, set_store_status, init_schema


def cmd_add(a):
    init_schema()
    upsert_store(a.store_id, a.sap or a.store_id, a.city, a.address, a.lon, a.lat)
    print(f"✅ Магазин {a.store_id} добавлен: {a.address} ({a.city})")


def cmd_list(a):
    c = get_conn()
    rs = c.execute("SELECT store_id, city, address, status, empty_streak FROM stores ORDER BY city, address").fetchall()
    c.close()
    if not rs:
        print("Справочник пуст. Добавь магазин: seed_stores.py add ...")
        return
    for r in rs:
        print(f"{r['store_id']:>10} | {r['status']:<8} | empty={r['empty_streak']} | {r['city']}, {r['address']}")


def cmd_remove(a):
    c = get_conn()
    c.execute("DELETE FROM stores WHERE store_id=?", (a.store_id,))
    c.execute("DELETE FROM user_stores WHERE store_id=?", (a.store_id,))
    c.commit(); c.close()
    print(f"🗑 Магазин {a.store_id} удалён")


def cmd_status(a):
    old = set_store_status(a.store_id, a.status, 'owner', None, 'ручная установка')
    print(f"✅ Статус {a.store_id}: {old} → {a.status}")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    pa = sub.add_parser("add")
    pa.add_argument("store_id")
    pa.add_argument("address")
    pa.add_argument("lon", type=float)
    pa.add_argument("lat", type=float)
    pa.add_argument("--city", default="Москва")
    pa.add_argument("--sap", default="")
    pa.set_defaults(fn=cmd_add)

    pl = sub.add_parser("list"); pl.set_defaults(fn=cmd_list)

    pr = sub.add_parser("remove"); pr.add_argument("store_id"); pr.set_defaults(fn=cmd_remove)

    ps = sub.add_parser("status"); ps.add_argument("store_id"); ps.add_argument("status"); ps.set_defaults(fn=cmd_status)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
