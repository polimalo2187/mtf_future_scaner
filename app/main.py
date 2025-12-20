# app/main.py

import asyncio
import os
from telegram import Bot
from telegram.ext import Application, CommandHandler

from app.bot import start
from app.handlers import get_handlers
from app.scanner import scan_market
from app.scheduler import scheduler_loop

# ======================================================
# VARIABLES DE ENTORNO (Railway compatible)
# ======================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN no está definido")

# ======================================================
# INICIALIZACIÓN DEL BOT DE TELEGRAM
# ======================================================

async def run_telegram_bot(application: Application):
    """
    Inicializa y mantiene activo el bot de Telegram.
    """
    await application.initialize()
    await application.start()
    print("🤖 Bot de Telegram iniciado")

    # Mantener polling activo
    await application.bot.initialize()
    await application.updater.start_polling()

# ======================================================
# FUNCIÓN PRINCIPAL ASYNC
# ======================================================

async def main():
    """
    Orquestador principal:
    - Bot Telegram
    - Scanner de mercado
    - Scheduler de planes
    """
    application = Application.builder().token(BOT_TOKEN).build()

    # /start
    application.add_handler(CommandHandler("start", start))

    # Handlers de botones
    for handler in get_handlers():
        application.add_handler(handler)

    bot = Bot(token=BOT_TOKEN)

    await asyncio.gather(
        run_telegram_bot(application),
        asyncio.to_thread(scan_market, bot),   # Scanner (loop bloqueante)
        scheduler_loop(bot),                   # Scheduler
    )

# ======================================================
# ENTRYPOINT
# ======================================================

if __name__ == "__main__":
    asyncio.run(main())
