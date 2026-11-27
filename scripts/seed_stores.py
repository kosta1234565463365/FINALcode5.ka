# scripts/seed_stores.py v2.0 — АВТО-обнаружение магазинов + ручное управление.
import sys, pathlib, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import argparse

from core.db import get_conn, upsert_store, set_store_status, init_schema

HOST = "https://5d." + "5ka.ru"

# Кандидаты эндпоинтов гео-поиска магазинов. Скрипт сам определит рабочий.
CANDIDATES = [
    ("catalog_geo",   HOST + "/api/catalog/v2/stores?mode=geo&lat={lat}&lon={lon}&limit=100"),
    ("stores_v2",     HOST + "/api/stores/v2/search/?lat={lat}&lon={lon}&limit=100"),
    ("stores_search", HOST + "/api/stores/search/?lat={lat}&lon={lon}"),
]

_JS = """
async (url) => {
    try {
        const r = await fetch(url, {credentials: "include"});
        const t = await r.text();
        return {status: r.status, body: t};
    } catch (e) { return {status: 0, body: String(e)}; }
}"""


def _extract_stores(data):
    items = None
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        for k in ("stores", "results", "items", "data"):
            v = data.get(k)
            if isinstance(v, list):
                items = v
                break
    if not items:
        return []
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        sid = str(it.get("id") or it.get("store_id") or it.get("uuid") or "")
        addr = str(it.get("address") or it.get("name") or "")
        lon, lat = _get_lonlat(it)
        if sid and addr:
            out.append((sid, addr, lon, lat))
    return out


def _get_lonlat(it):
    for src in (it, (it.get("geo") or {}), (it.get("coordinates") or {})):
        if not isinstance(src, dict):
            continue
        lon = src.get("lon") or src.get("longitude")
        lat = src.get("lat") or src.get("latitude")
        if lon is not None and lat is not None:
            try:
                return float(lon), float(lat)
            except Exception:
                return None, None
    return None, None


def cmd_discover(a):
    init_schema()
    from collector.browser import BrowserManager
    bm = BrowserManager()
    print("🔎 Запускаю браузер для гео-поиска (может занять ~30 сек)...")
    bm.start()
    found = 0
    try:
        for name, tmpl in CANDIDATES:
            url = tmpl.format(lat=a.lat, lon=a.lon)
            res = bm.page.evaluate(_JS, url)
            status = res.get("status") if res else None
            if status != 200:
                print(f"  [{name}] status={status} — не подошёл")
                continue
            try:
                data = json.loads(res.get("body", ""))
            except Exception:
                print(f"  [{name}] не-JSON ответ — не подошёл")
                continue
            stores = _extract_stores(data)
            if not stores:
                print(f"  [{name}] ответ 200, но магазинов не распознал")
                continue
            print(f"  [{name}] ✅ РАБОЧИЙ эндпоинт, найдено магазинов: {len(stores)}")
            for sid, addr, lon, lat in stores:
                upsert_store(sid, sid, a.city, addr,
                             lon if lon is not None else a.lon,
                             lat if lat is not None else a.lat)
                found += 1
            break
    finally:
        bm.stop()
    if found:
        print(f"✅ Добавлено магазинов: {found}")
    else:
        print("⚠️ Авто-поиск не нашёл магазинов ни одним эндпоинтом.")
        print("   Пришли мне вывод выше — я подберу рабочий эндпоинт.")


def cmd_add(a):
    init_schema()
    upsert_store(a.store_id, a.sap or a.store_id, a.city, a.address, a.lon, a.lat)
    print(f"✅ Магазин {a.store_id} добавлен: {a.address}")


def cmd_list(a):
    c = get_conn()
    rs = c.execute("SELECT store_id, city, address, status, empty_streak FROM stores ORDER BY city, address").fetchall()
    c.close()
    if not rs:
        print("Справочник пуст.")
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

    pd = sub.add_parser("discover")
    pd.add_argument("--lat", type=float, default=55.75)
    pd.add_argument("--lon", type=float, default=37.62)
    pd.add_argument("--city", default="Москва")
    pd.set_defaults(fn=cmd_discover)

    pa = sub.add_parser("add")
    pa.add_argument("store_id"); pa.add_argument("address")
    pa.add_argument("lon", type=float); pa.add_argument("lat", type=float)
    pa.add_argument("--city", default="Москва"); pa.add_argument("--sap", default="")
    pa.set_defaults(fn=cmd_add)

    pl = sub.add_parser("list"); pl.set_defaults(fn=cmd_list)
    pr = sub.add_parser("remove"); pr.add_argument("store_id"); pr.set_defaults(fn=cmd_remove)
    ps = sub.add_parser("status"); ps.add_argument("store_id"); ps.add_argument("status"); ps.set_defaults(fn=cmd_status)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
