# collector/fetcher.py v2.0
# Поддержка полного (07:00) и точечного (08:00) прохода.
import json, time, random, logging
from typing import List, Dict, Optional, Tuple, Any

from core.db import get_conn, record_price, set_store_status
from collector.browser import BrowserManager

logger = logging.getLogger(__name__)
HOST = "https://5d." + "5ka.ru"
SITE = "https://" + "5ka.ru/"

# Настройки пагинации (после совета коллеги)
PAGE_LIMIT = 100
MIN_PAUSE = 1.5
MAX_PAUSE = 2.5


class Fetcher:
    def __init__(self, browser: BrowserManager, watchdog=None):
        self.browser = browser
        self.watchdog = watchdog
        self._js = (
            'async (u) => { try { const r = await fetch(u, {credentials: "include"}); '
            'return {s: r.status, t: await r.text()}; } catch(e) { return {s:0, t:String(e)}; } }'
        )

    def _api(self, url: str, retries: int = 4) -> Optional[Dict]:
        for att in range(retries):
            try:
                r = self.browser.page.evaluate(self._js, url)
                if r and r.get("s") == 200:
                    return r
            except Exception:
                pass
            time.sleep(5)
        return None

    def _safe_json(self, text: str, ctx: str = "") -> Any:
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning(f"[Fetcher] JSON error {ctx}: {e}")
            return None

    def _pause(self) -> None:
        time.sleep(random.uniform(MIN_PAUSE, MAX_PAUSE))
        self.browser.increment_request()

    # === ПОЛНЫЙ ПРОХОД (07:00) ===

    def get_categories(self, store_id: str) -> List[Dict]:
        url = f"{HOST}/api/catalog/v2/stores/{store_id}/categories?mode=store&include_restrict=true"
        r = self._api(url)
        if not r or r.get("s") != 200:
            return []
        data = self._safe_json(r.get("t", ""), f"cats/{store_id}")
        return data if isinstance(data, list) else []

    def get_products(self, store_id: str, cat_id: str) -> List[Dict]:
        """Пагинация по limit=100 с динамическим шагом (совет коллеги)."""
        all_prods = []
        offset = 0
        while True:
            url = (
                f"{HOST}/api/catalog/v2/stores/{store_id}/categories/{cat_id}/products"
                f"?mode=store&include_restrict=true&limit={PAGE_LIMIT}&offset={offset}"
            )
            r = self._api(url)
            if not r or r.get("s") != 200:
                break
            data = self._safe_json(r.get("t", ""), f"prods/{store_id}/{cat_id}")
            if not isinstance(data, dict):
                break
            prods = data.get("products", [])
            if not prods:
                break
            all_prods.extend(prods)
            if len(prods) < PAGE_LIMIT:
                break
            offset += len(prods)
            self._pause()
        return all_prods

    def _img(self, item: Dict) -> str:
        imgs = item.get("image_links") or {}
        if isinstance(imgs, dict):
            for k in ("normal", "small", "big"):
                v = imgs.get(k)
                if isinstance(v, list) and v:
                    return str(v[0])
                if isinstance(v, str) and v:
                    return v
        return str(item.get("image_url") or "")

    def get_baseline(self, store_id: str, plu: str, cur_reg: float) -> Tuple[float, str]:
        """Честная регулярная цена: 7 дней стабильности ±3% или 80-й перцентиль за 21 день."""
        from datetime import datetime, timezone, timedelta
        MSK = timezone(timedelta(hours=3))
        now = int(datetime.now(MSK).timestamp())
        conn = get_conn()
        c = conn.cursor()
        rows = c.execute(
            "SELECT regular FROM prices WHERE store_id=? AND plu=? AND ts>=? AND regular>0 ORDER BY ts",
            (store_id, plu, now - 7 * 86400),
        ).fetchall()
        if len(rows) >= 3:
            prices = [r["regular"] for r in rows]
            mn, mx = min(prices), max(prices)
            if mx > 0 and (mx - mn) / mx <= 0.03:
                conn.close()
                return round(sum(prices) / len(prices), 2), "stable_7d"
        rows = c.execute(
            "SELECT regular FROM prices WHERE store_id=? AND plu=? AND ts>=? AND regular>0",
            (store_id, plu, now - 21 * 86400),
        ).fetchall()
        conn.close()
        if not rows:
            return cur_reg, "current"
        prices = sorted([r["regular"] for r in rows] + ([cur_reg] if cur_reg > 0 else []))
        return round(prices[int(0.8 * (len(prices) - 1))], 2), "p80_21d"

    def _is_hidden_crash(self, baseline: float, cur_reg: float) -> bool:
        """Если текущая 'регулярная' выше базы на >15% — магазин пытается накрутить."""
        if baseline <= 0:
            return False
        return cur_reg > baseline * 1.15

    def _process(self, store_id: str, item: Dict, cat_name: str) -> None:
        plu = str(item.get("plu") or item.get("id") or "")
        if not plu:
            return
        name = str(item.get("name") or "")
        prices = item.get("prices") or {}
        reg = float(prices.get("regular") or prices.get("price") or 0)
        price = float(prices.get("discount") or prices.get("price") or reg)
        stock = float(item.get("stock_limit") or item.get("stock") or 0)
        if stock <= 0 or price >= reg or reg <= 0:
            return
        baseline, method = self.get_baseline(store_id, plu, reg)
        if self._is_hidden_crash(baseline, reg):
            logger.info(f"[Fetcher] {plu}: скрытое завышение {reg} vs base {baseline}")
            baseline = reg  # не блокируем, но фиксируем
        record_price(
            store_id=store_id, plu=plu, name=name,
            regular=baseline, price=price, stock=stock, source="api",
            category_name=cat_name, img_url=self._img(item),
            promo_type=str(item.get("promo_type") or ""),
        )

    def scan_store(self, store_id: str) -> Tuple[int, bool]:
        cats = self.get_categories(store_id)
        if not cats:
            return 0, False
        total = 0
        for c in cats:
            cid = str(c.get("id") or "")
            cname = str(c.get("name") or "")
            if not cid:
                continue
            prods = self.get_products(store_id, cid)
            for it in prods:
                self._process(store_id, it, cname)
            total += len(prods)
            self._pause()
        return total, True

    # === ТОЧЕЧНЫЙ ПРОХОД (08:00) ===

    def check_specific_plu(self, store_id: str, plu_list: List[str]) -> int:
        """Проверяет только конкретные PLU: по 1 микро-запросу на позицию.
        Для каждого PLU запрашиваем все категории магазина и ищем совпадение.
        Возвращает количество обновлённых позиций.
        """
        if not plu_list:
            return 0
        cats = self.get_categories(store_id)
        if not cats:
            return 0
        plu_set = set(plu_list)
        updated = 0
        for c in cats:
            if not plu_set:
                break
            cid = str(c.get("id") or "")
            cname = str(c.get("name") or "")
            if not cid:
                continue
            prods = self.get_products(store_id, cid)
            found_in_cat = []
            for it in prods:
                plu = str(it.get("plu") or "")
                if plu in plu_set:
                    self._process(store_id, it, cname)
                    found_in_cat.append(plu)
                    updated += 1
            plu_set -= set(found_in_cat)
            self._pause()
        return updated

    def run_cycle(self, store_ids: List[str]) -> Dict[str, int]:
        results = {}
        for sid in store_ids:
            if self.browser.is_sleep_time():
                self.browser.stop()
                break
            try:
                count, ok = self.scan_store(sid)
                results[sid] = count
                if self.watchdog:
                    self.watchdog.report_store_result(sid, count, http_ok=ok)
            except Exception as e:
                logger.error(f"[Fetcher] {sid}: {e}")
                results[sid] = 0
                if self.watchdog:
                    self.watchdog.report_parser_error(str(e))
            self._pause()
        return results
