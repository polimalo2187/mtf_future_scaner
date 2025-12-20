# app/bot.py
# CEREBRO COMPLETO DEL BOT – NO TOCAR DESDE FUERA

import asyncio
import os
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
)

from app.database import users_collection
from app.models import new_user
from app.handlers import get_handlers
from app.scanner import scan_market
from app.scheduler import scheduler_loop

# ======================================================
# VARIABLES DE ENTORNO
# ======================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN no está definido")

# ======================================================
# MENÚ PRINCIPAL
# ======================================================

def main_menu():
    keyboard = [
        [InlineKeyboardButton("📊 Ver señales", callback_data="view_signals")],
        [
            InlineKeyboardButton("💼 Planes", callback_data="plans"),
            InlineKeyboardButton("👤 Mi cuenta", callback_data="my_account"),
        ],
        [
            InlineKeyboardButton("👥 Referidos", callback_data="referrals"),
            InlineKeyboardButton("📩 Soporte", callback_data="support"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)

# ======================================================
# /start HANDLER
# ======================================================

async def start(update: Update, context):
    user = update.effective_user
    args = context.args

    users_col = users_collection()
    existing_user = users_col.find_one({"user_id": user.id})

    referred_by = None

    # Manejo de referido: /start ref_123456
    if args and not existing_user:
        ref_arg = args[0]
        if ref_arg.startswith("ref_"):
            try:
                ref_user_id = int(ref_arg.replace("ref_", ""))

                # Evitar auto-referido
                if ref_user_id != user.id:
                    ref_user = users_col.find_one({"user_id": ref_user_id})
                    if ref_user:
                        referred_by = ref_user_id
            except ValueError:
                referred_by = None

    if not existing_user:
        user_doc = new_user(
            user_id=user.id,
            username=user.username,
            referred_by=referred_by,
        )
        users_col.insert_one(user_doc)

        welcome_text = (
            "Bienvenido a MTF Futures Scanner.\n\n"
            "Tu acceso gratuito de prueba ha sido activado por 7 días.\n"
            "Durante este periodo podrás evaluar la calidad del sistema.\n\n"
            "Utiliza el menú para navegar."
        )
    else:
        welcome_text = (
            "Bienvenido de nuevo a MTF Futures Scanner.\n\n"
            "Utiliza el menú para acceder a las funciones disponibles."
        )

    await update.message.reply_text(
        text=welcome_text,
        reply_markup=main_menu(),
    )

# ======================================================
# ORQUESTADOR ASYNC COMPLETO (ANTES ESTABA EN main.py)
# ======================================================

async def orchestrator():
    """
    Orquesta:
    - Bot de Telegram
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
        _run_telegram_bot(application),
        asyncio.to_thread(scan_market, bot),
        scheduler_loop(bot),
    )

# ======================================================
# RUN TELEGRAM BOT (ANTES EN main.py)
# ======================================================

async def _run_telegram_bot(application: Application):
    await application.initialize()
    await application.start()
    print("🤖 Bot de Telegram iniciado correctamente")

    await application.bot.initialize()
    await application.updater.start_polling()

# ======================================================
# FUNCIÓN PÚBLICA DE ARRANQUE (LA USA Railway)
# ======================================================

def run_bot():
    """
    Punto único de entrada del bot.
    Railway llama a esta función desde main.py raíz.
    """
    asyncio.run(orchestrator())
