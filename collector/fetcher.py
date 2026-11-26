# collector/fetcher.py v1.0 - Часть 1/2
# Ядро метода опроса Пятёрочки: обход магазинов, категорий, пагинация.
# Работает только через Camoufox (browser.py), пишет в БД через core/db.py.

import json
import time
import random
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone, timedelta

from core.config import (
    MIN_PAUSE_SEC,
    MAX_PAUSE_SEC,
    REQUEST_TIMEOUT_SEC,
    MORNING_WAVE_MIN_STOCK,
    MORNING_CHECK_MIN_STOCK,
    LOW_STOCK_WARNING_THRESHOLD,
)
from core.db import record_price, get_conn
from collector.browser import BrowserManager

logger = logging.getLogger(__name__)
MSK = timezone(timedelta(hours=3))

# Разбитый домен для защиты от автозамены в мессенджерах
HOST = "https://5d." + "5ka.ru"
SITE = "https://" + "5ka.ru/"


class Fetcher:
    """Сборщик данных из API Пятёрочки через Camoufox."""

    def __init__(self, browser: BrowserManager):
        self.browser = browser
        self._js_fetch = """
        async (url) => {
            try {
                const r = await fetch(url, {credentials: "include"});
                const text = await r.text();
                return {status: r.status, body: text};
            } catch (e) {
                return {status: 0, body: String(e)};
            }
        }
        """

    def _safe_json(self, text: str, context: str = "") -> Optional[Any]:
        """Безопасный парсинг JSON с логом ошибок."""
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning(f"[Fetcher] JSON error {context}: {e}, preview: {text[:100]}")
            return None

    def _api_request(self, url: str, retries: int = 4) -> Optional[Dict]:
        """Запрос к API с ретраями при перезагрузке страницы антиботом.
        Антибот ServicePipe перезагружает вкладку через 6-12 сек после входа,
        поэтому page.evaluate может упасть с 'Execution context was destroyed'.
        """
        for attempt in range(retries):
            try:
                page = self.browser.page
                result = page.evaluate(self._js_fetch, url)
                if result and result.get("status") == 200:
                    return result
                if result and result.get("status") == 403:
                    logger.warning(f"[Fetcher] 403 на {url}, attempt {attempt+1}")
                elif result:
                    logger.info(f"[Fetcher] Status {result.get('status')} on {url}")
            except Exception as e:
                logger.warning(f"[Fetcher] Exception attempt {attempt+1}: {e}")
            # Пауза перед ретраем (антибот может перезагружать страницу)
            time.sleep(5)
        logger.error(f"[Fetcher] Все {retries} попытки провалены для {url}")
        return None

    def _random_pause(self) -> None:
        """Случайная пауза между запросами (защита от антибота)."""
        pause = random.uniform(MIN_PAUSE_SEC, MAX_PAUSE_SEC)
        time.sleep(pause)
        self.browser.increment_request()

    def get_store_categories(self, store_id: str) -> List[Dict]:
        """Получить список категорий магазина.
        URL: /api/catalog/v2/stores/{store_id}/categories?mode=store&include_restrict=true
        """
        url = (
            f"{HOST}/api/catalog/v2/stores/{store_id}/categories"
            f"?mode=store&include_restrict=true"
        )
        result = self._api_request(url)
        if not result or result.get("status") != 200:
            return []
        data = self._safe_json(result.get("body", ""), f"categories/{store_id}")
        if not isinstance(data, list):
            logger.warning(f"[Fetcher] Categories не list для {store_id}")
            return []
        logger.info(f"[Fetcher] Магазин {store_id}: {len(data)} категорий")
        return data

    def get_category_products(
        self, store_id: str, category_id: str, category_name: str = ""
    ) -> List[Dict]:
        """Получить все товары категории с пагинацией (limit=36, offset=0,36,72...).
        URL: /api/catalog/v2/stores/{store_id}/categories/{cat_id}/products?mode=store&...&limit=36&offset=N
        """
        all_products = []
        offset = 0
        limit = 36

        while True:
            url = (
                f"{HOST}/api/catalog/v2/stores/{store_id}/categories/{category_id}/products"
                f"?mode=store&include_restrict=true&limit={limit}&offset={offset}"
            )
            result = self._api_request(url)
            if not result or result.get("status") != 200:
                break

            data = self._safe_json(
                result.get("body", ""), f"products/{store_id}/{category_id}"
            )
            if not isinstance(data, dict):
                break

            products = data.get("products", [])
            if not products:
                break

            all_products.extend(products)
            logger.debug(
                f"[Fetcher] {store_id}/{category_name}: offset={offset}, +{len(products)} товаров"
            )

            # Если получили меньше limit - это последняя страница
            if len(products) < limit:
                break

            offset += limit
            self._random_pause()

        return all_products

    def _extract_image_url(self, item: Dict) -> str:
        """Извлечь URL картинки товара из разных возможных форматов API."""
        imgs = item.get("image_links") or {}
        if isinstance(imgs, dict):
            for key in ("normal", "small", "big"):
                val = imgs.get(key)
                if isinstance(val, list) and val:
                    return str(val[0])
                if isinstance(val, str) and val:
                    return val
        return str(item.get("image_url") or "")
    def _calculate_baseline(self, store_id: str, plu: str, current_regular: float) -> float:
        """Расчёт честной регулярной цены.
        Основной метод: 7 дней стабильности ±3% (canonical).
        Резервный метод: 80-й перцентиль за 21 день (для новинок).
        """
        from datetime import datetime, timedelta
        now = int(datetime.now(MSK).timestamp())
        seven_days_ago = now - 7 * 86400
        twenty_one_days_ago = now - 21 * 86400

        conn = get_conn()
        c = conn.cursor()

        # Основной метод: стабильная цена за 7 дней
        rows = c.execute("""
            SELECT regular FROM prices
            WHERE store_id=? AND plu=? AND ts >= ? AND regular > 0
            ORDER BY ts
        """, (store_id, plu, seven_days_ago)).fetchall()

        if len(rows) >= 3:
            prices = [r["regular"] for r in rows]
            min_p, max_p = min(prices), max(prices)
            # Если разброс ≤3% — цена стабильна
            if max_p > 0 and (max_p - min_p) / max_p <= 0.03:
                conn.close()
                return round(sum(prices) / len(prices), 2)

        # Резервный метод: 80-й перцентиль за 21 день
        rows = c.execute("""
            SELECT regular FROM prices
            WHERE store_id=? AND plu=? AND ts >= ? AND regular > 0
        """, (store_id, plu, twenty_one_days_ago)).fetchall()

        conn.close()

        if not rows:
            return current_regular

        prices = sorted([r["regular"] for r in rows])
        if current_regular > 0:
            prices.append(current_regular)
            prices.sort()

        idx = int(0.8 * (len(prices) - 1))
        return round(prices[idx], 2)

    def _process_product(self, store_id: str, item: Dict, category_name: str) -> None:
        """Обработать один товар: извлечь цены, рассчитать baseline, записать в БД."""
        plu = str(item.get("plu") or item.get("id") or "")
        if not plu:
            return

        name = str(item.get("name") or "")
        prices_data = item.get("prices") or {}
        regular = float(prices_data.get("regular") or prices_data.get("price") or 0)
        price = float(prices_data.get("discount") or prices_data.get("price") or regular)
        stock = float(item.get("stock_limit") or item.get("stock") or 0)
        promo_type = str(item.get("promo_type") or "")
        img_url = self._extract_image_url(item)

        # Фильтр: только товары с остатком > 0
        if stock <= 0:
            return

        # Если цена скидки >= регулярной — это не скидка
        if price >= regular or regular <= 0:
            return

        # Рассчитать честную базовую цену
        baseline = self._calculate_baseline(store_id, plu, regular)

        # Записать в БД (только при реальном сдвиге, дубли игнорируются внутри record_price)
        record_price(
            store_id=store_id,
            plu=plu,
            name=name,
            regular=baseline,  # Честная регулярная цена
            price=price,
            stock=stock,
            source="api",
            category_name=category_name,
            img_url=img_url,
            promo_type=promo_type,
        )

    def scan_store(self, store_id: str) -> int:
        """Полный обход магазина: все категории, все товары.
        Возвращает количество обработанных товаров.
        """
        categories = self.get_store_categories(store_id)
        if not categories:
            logger.warning(f"[Fetcher] Нет категорий для магазина {store_id}")
            return 0

        total_products = 0
        for cat in categories:
            cat_id = str(cat.get("id") or "")
            cat_name = str(cat.get("name") or "")
            if not cat_id:
                continue

            products = self.get_category_products(store_id, cat_id, cat_name)
            for item in products:
                self._process_product(store_id, item, cat_name)
                total_products += 1

            self._random_pause()

        logger.info(f"[Fetcher] Магазин {store_id}: обработано {total_products} товаров")
        return total_products

    def run_cycle(self, store_ids: List[str]) -> Dict[str, int]:
        """Один полный цикл обхода списка магазинов.
        Возвращает словарь {store_id: количество_товаров}.
        """
        results = {}
        for store_id in store_ids:
            # Проверка окна сна перед каждым магазином
            if self.browser.is_sleep_time():
                logger.info("[Fetcher] Окно сна, останавливаем цикл")
                self.browser.stop()
                break

            try:
                count = self.scan_store(store_id)
                results[store_id] = count
            except Exception as e:
                logger.error(f"[Fetcher] Ошибка обхода {store_id}: {e}")
                results[store_id] = 0

            self._random_pause()

        return results
