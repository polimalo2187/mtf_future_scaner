# app/main.py

import asyncio
import os
from dotenv import load_dotenv
from telegram import Bot
from telegram.ext import Application

from app.bot import start, main_menu
from app.handlers import get_handlers
from app.scanner import scan_market
from app.scheduler import scheduler_loop

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN no está definido")

# =========================
# INICIALIZAR BOT
# =========================

async def run_bot():
    application = Application.builder().token(BOT_TOKEN).build()

    # /start
    application.add_handler(
        application.command_handler("start", start)
        if hasattr(application, "command_handler")
        else None
    )

    # Handlers de botones
    for handler in get_handlers():
        application.add_handler(handler)

    await application.initialize()
    await application.start()
    await application.bot.initialize()

    print("Bot de Telegram iniciado")

    # Mantener polling activo
    await application.updater.start_polling()


# =========================
# MAIN ASYNC
# =========================

async def main():
    bot = Bot(token=BOT_TOKEN)

    await asyncio.gather(
        run_bot(),              # Telegram
        asyncio.to_thread(scan_market),  # Scanner (loop bloqueante)
        scheduler_loop(bot),    # Scheduler
    )


if __name__ == "__main__":
    asyncio.run(main())
