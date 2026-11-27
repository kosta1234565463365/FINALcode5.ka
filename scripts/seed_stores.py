# scripts/seed_stores.py v1.1
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.db import init_schema, upsert_store, set_store_status, get_conn

STORES = [
    ("3C0H", "3C0H", "Москва", "ул. Снежная, д. 25", 37.6524, 55.8565),
    ("S143", "S143", "Москва", "ул. Уржумская, д. 7", 37.6426, 55.8465),
    ("3BG8", "3BG8", "Москва", "ул. Бибиревская, д. 11", 37.6189, 55.8703),
    ("5156", "5156", "Москва", "ул. Снежная, д. 16к2", 37.6481, 55.8543),
]

def main():
    init_schema()
    for sid, sap, city, addr, lon, lat in STORES:
        upsert_store(sid, sap, city, addr, lon, lat)
        set_store_status(sid, "active", "system", None, "seed")
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) AS c FROM stores").fetchone()["c"]
    conn.close()
    print(f"[SEED] Магазинов в базе: {n}")

if __name__ == "__main__":
    main()
