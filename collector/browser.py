# collector/browser.py v1.0
# Управление Camoufox: запуск, перезапуск, ночной сон, защита от утечек RAM.
# Явное ожидание _SPUID вместо хрупкого sleep(13).

import gc
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, Any

from camoufox.sync_api import Camoufox

from core.config import (
    BROWSER_RESTART_AFTER_REQUESTS,
    SLEEP_START,
    SLEEP_END,
)

logger = logging.getLogger(__name__)
MSK = timezone(timedelta(hours=3))

SITE_URL = "https://" + "5ka.ru/"


class BrowserManager:
    """Управляет жизненным циклом браузера Camoufox."""

    def __init__(self):
        self._cm: Optional[Camoufox] = None
        self._browser: Optional[Any] = None
        self._page: Optional[Any] = None
        self._request_count: int = 0
        self._is_alive: bool = False

    @property
    def page(self):
        """Текущая страница. Вызывает restart если браузер мёртв."""
        if not self._is_alive or self._page is None:
            self.start()
        return self._page

    @property
    def request_count(self) -> int:
        return self._request_count

    def increment_request(self) -> None:
        """Счётчик запросов. При достижении лимита — автоматический перезапуск."""
        self._request_count += 1
        if self._request_count >= BROWSER_RESTART_AFTER_REQUESTS:
            logger.info(
                f"[Browser] Достигнут лимит {BROWSER_RESTART_AFTER_REQUESTS} запросов, перезапуск..."
            )
            self.restart()

    def is_sleep_time(self) -> bool:
        """Проверка окна сна парсера (21:30–06:00 МСК)."""
        now = datetime.now(MSK)
        current = now.strftime("%H:%M")
        # Окно сна пересекает полночь: >= SLEEP_START OR < SLEEP_END
        return current >= SLEEP_START or current < SLEEP_END

    def start(self) -> None:
        """Запустить браузер и дождаться готовности антибота."""
        if self._is_alive and self._page is not None:
            return

        logger.info("[Browser] Запуск Camoufox...")
        try:
            self._cm = Camoufox(headless=True, i_know_what_im_doing=True)
            self._browser = self._cm.__enter__()
            self._page = self._browser.new_page()
            self._page.goto(SITE_URL, wait_until="domcontentloaded", timeout=45000)
            self._wait_for_antibot()
            self._request_count = 0
            self._is_alive = True
            logger.info("[Browser] Camoufox запущен и готов")
        except Exception as e:
            logger.error(f"[Browser] Ошибка запуска: {e}")
            self.stop()
            raise

    def _wait_for_antibot(self, timeout: int = 30) -> None:
        """Явное ожидание _SPUID в localStorage вместо sleep(13).
        Антибот ServicePipe записывает _SPUID через 6-12 сек после загрузки.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                spuid = self._page.evaluate("localStorage.getItem('_SPUID')")
                if spuid:
                    logger.info(f"[Browser] _SPUID получен: {spuid[:8]}...")
                    return
            except Exception:
                pass  # Контекст может быть уничтожен при перезагрузке страницы
            time.sleep(1)
        logger.warning("[Browser] _SPUID не получен за таймаут, продолжаем на свой риск")

    def restart(self) -> None:
        """Перезапуск браузера + очистка памяти."""
        logger.info("[Browser] Перезапуск...")
        self.stop()
        gc.collect()
        time.sleep(2)
        self.start()

    def stop(self) -> None:
        """Остановить браузер и освободить ресурсы."""
        self._is_alive = False
        self._page = None
        if self._cm is not None:
            try:
                self._cm.__exit__(None, None, None)
            except Exception as e:
                logger.warning(f"[Browser] Ошибка остановки: {e}")
            finally:
                self._cm = None
                self._browser = None
        gc.collect()
        logger.info("[Browser] Остановлен")

    def ensure_ready(self) -> None:
        """Проверить готовность перед работой. Если сон — остановить; иначе — запустить."""
        if self.is_sleep_time():
            if self._is_alive:
                logger.info("[Browser] Начало окна сна, остановка...")
                self.stop()
            return
        if not self._is_alive:
            self.start()
