# collector/fetcher.py v2.0 — сбор цен + антифейк.
import json, time, random, logging
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timezone, timedelta

from core.config import (
    MIN_PAUSE_SEC, MAX_PAUSE_SEC,
    BASELINE_STABLE_DAYS, BASELINE_TOLERANCE_PCT,
    HIDDEN_CRASH_PCT, HIDDEN_CRASH_ABS_RUB,
)
from core.db import get_conn, record_price
from collector.browser import BrowserManager
from collector.watchdog import Watchdog

logger = logging.getLogger(__name__)
MSK = timezone(timedelta(hours=3))
HOST = "https://5d." + "5ka.ru"

class Fetcher:
    def __init__(self, browser: BrowserManager, watchdog: Optional[Watchdog] = None):
        self.browser = browser
        self.wd = watchdog or Watchdog()
        self._js = """
        async (url) => {
            try {
                const r = await fetch(url, {credentials: "include"});
                const t = await r.text();
                return {status: r.status, body: t};
            } catch (e) { return {status: 0, body: String(e)}; }
        }"""

    # --- низкоуровневые запросы ---
    def _safe_json(self, text, ctx=""):
        if not text: return None
        try: return json.loads(text)
        except Exception as e:
            logger.warning(f"[Fetch] bad json {ctx}: {e} :: {text[:80]}")
            return None

    def _api(self, url, retries=4) -> Optional[Dict]:
        for a in range(retries):
            try:
                res = self.browser.page.evaluate(self._js, url)
                if res and res.get("status") == 200:
                    return res
                logger.info(f"[Fetch] status={res.get('status') if res else None} {url}")
            except Exception as e:
                logger.warning(f"[Fetch] exc a{a+1}: {e}")
            time.sleep(5)
        return None

    def _pause(self):
        time.sleep(random.uniform(MIN_PAUSE_SEC, MAX_PAUSE_SEC))
        self.browser.increment_request()

    # --- каталог ---
    def get_categories(self, store_id) -> Tuple[bool, List[Dict]]:
        url = f"{HOST}/api/catalog/v2/stores/{store_id}/categories?mode=store&include_restrict=true"
        res = self._api(url)
        if not res or res.get("status") != 200:
            return False, []
        data = self._safe_json(res.get("body", ""), f"cat/{store_id}")
        if not isinstance(data, list):
            return False, []
        return True, data

    def get_products(self, store_id, cat_id) -> List[Dict]:
        out, offset, limit = [], 0, 36
        while True:
            url = (f"{HOST}/api/catalog/v2/stores/{store_id}/categories/{cat_id}/products"
                   f"?mode=store&include_restrict=true&limit={limit}&offset={offset}")
            res = self._api(url)
            if not res or res.get("status") != 200: break
            data = self._safe_json(res.get("body", ""), f"prod/{store_id}/{cat_id}")
            if not isinstance(data, dict): break
            items = data.get("products", [])
            if not items: break
            out.extend(items)
            if len(items) < limit: break
            offset += limit
            self._pause()
        return out

    def _img(self, item) -> str:
        il = item.get("image_links") or {}
        if isinstance(il, dict):
            for k in ("normal", "small", "big"):
                v = il.get(k)
                if isinstance(v, list) and v: return str(v[0])
                if isinstance(v, str) and v: return v
        return str(item.get("image_url") or "")

    # --- АНТИФЕЙК: честная базовая цена ---
    def get_baseline(self, store_id, plu, current_regular) -> Tuple[float, str]:
        n = int(datetime.now(MSK).timestamp())
        d7 = n - BASELINE_STABLE_DAYS * 86400
        d21 = n - 21 * 86400
        c = get_conn()
        rows = c.execute("SELECT regular FROM prices WHERE store_id=? AND plu=? AND ts>=? AND regular>0 ORDER BY ts",
                         (store_id, plu, d7)).fetchall()
        if len(rows) >= 3:
            ps = [r["regular"] for r in rows]
            mn, mx = min(ps), max(ps)
            if mx > 0 and (mx - mn) / mx <= BASELINE_TOLERANCE_PCT / 100:
                base = round(sum(ps) / len(ps), 2)
                c.execute("""INSERT INTO baselines (store_id,plu,baseline_price,method,calculated_at)
                    VALUES(?,?,?,?,?) ON CONFLICT(store_id,plu) DO UPDATE SET
                    baseline_price=excluded.baseline_price, method=excluded.method, calculated_at=excluded.calculated_at""",
                    (store_id, plu, base, 'stable_7d', n))
                c.commit(); c.close(); return base, 'stable_7d'
        rows = c.execute("SELECT regular FROM prices WHERE store_id=? AND plu=? AND ts>=? AND regular>0",
                         (store_id, plu, d21)).fetchall()
        c.close()
        if not rows:
            return current_regular, 'none'
        ps = sorted(r["regular"] for r in rows)
        if current_regular > 0:
            ps.append(current_regular); ps.sort()
        base = round(ps[int(0.8 * (len(ps) - 1))], 2)
        c = get_conn()
        c.execute("""INSERT INTO baselines (store_id,plu,baseline_price,method,calculated_at)
            VALUES(?,?,?,?,?) ON CONFLICT(store_id,plu) DO UPDATE SET
            baseline_price=excluded.baseline_price, method=excluded.method, calculated_at=excluded.calculated_at""",
            (store_id, plu, base, 'p80_21d', n))
        c.commit(); c.close()
        return base, 'p80_21d'

    def _is_hidden_crash(self, baseline, current_regular) -> bool:
        if baseline <= 0: return False
        drop_abs = baseline - current_regular
        return drop_abs >= HIDDEN_CRASH_ABS_RUB and drop_abs >= baseline * HIDDEN_CRASH_PCT / 100

    # --- обработка товара ---
    def _process(self, store_id, item, cat_name):
        plu = str(item.get("plu") or item.get("id") or "")
        if not plu: return
        name = str(item.get("name") or "")
        pd = item.get("prices") or {}
        regular = float(pd.get("regular") or pd.get("price") or 0)
        price = float(pd.get("discount") or pd.get("price") or regular)
        stock = float(item.get("stock_limit") or item.get("stock") or 0)
        promo = str(item.get("promo_type") or "")
        img = self._img(item)
        if stock <= 0 or regular <= 0 or price >= regular: return

        baseline, method = self.get_baseline(store_id, plu, regular)
        # Антифейк: если регулярка подозрительно рухнула — не верим ей, считаем от базовой
        if self._is_hidden_crash(baseline, regular):
            logger.warning(f"[Fetch] hidden crash {store_id}/{plu}: reg={regular} base={baseline}")
            regular = baseline
        record_price(store_id, plu, name, regular, price, stock,
                     source='api', category_name=cat_name, img_url=img, promo_type=promo)

    # --- обход магазина ---
    def scan_store(self, store_id) -> Tuple[int, bool]:
        ok, cats = self.get_categories(store_id)
        if not ok:
            self.wd.report_store_result(store_id, 0, http_ok=False)
            return 0, False
        total = 0
        for cat in cats:
            cid = str(cat.get("id") or "")
            cname = str(cat.get("name") or "")
            if not cid: continue
            for item in self.get_products(store_id, cid):
                self._process(store_id, item, cname)
                total += 1
            self._pause()
        self.wd.report_store_result(store_id, total, http_ok=True)
        logger.info(f"[Fetch] store {store_id}: {total} items")
        return total, True

    def run_cycle(self, store_ids: List[str]) -> Dict[str, int]:
        res = {}
        for sid in store_ids:
            if self.browser.is_sleep_time():
                logger.info("[Fetch] sleep window, stop cycle")
                self.browser.stop(); break
            try:
                cnt, _ = self.scan_store(sid)
                res[sid] = cnt
            except Exception as e:
                logger.error(f"[Fetch] scan fail {sid}: {e}")
                self.wd.report_parser_error(str(e))
                res[sid] = 0
            self._pause()
        self.wd.report_parser_success()
        return res
