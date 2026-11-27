# bot/main.py v1.2 — + sender_loop
import os, asyncio, logging
from pathlib import Path
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from bot.handlers import start, user
from bot import sender

def load_env():
    p = Path(__file__).resolve().parent.parent / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

async def main():
    load_env()
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise SystemExit("BOT_TOKEN не задан")
    logging.basicConfig(level=logging.INFO)
    bot = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(start.router)
    dp.include_router(user.router)
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(sender.sender_loop(bot))
    logging.info("Бот запущен + sender_loop")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
