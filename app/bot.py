# app/bot.py

import asyncio
import os
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler

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
# /START
# ======================================================

async def start(update: Update, context):
    user = update.effective_user
    args = context.args

    users_col = users_collection()
    existing_user = users_col.find_one({"user_id": user.id})

    referred_by = None
    if args and not existing_user:
        ref_arg = args[0]
        if ref_arg.startswith("ref_"):
            try:
                ref_user_id = int(ref_arg.replace("ref_", ""))
                if ref_user_id != user.id:
                    if users_col.find_one({"user_id": ref_user_id}):
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
            "Tu acceso gratuito de prueba ha sido activado por 7 días.\n\n"
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
# ORQUESTADOR PRINCIPAL (UN SOLO EVENT LOOP)
# ======================================================

async def orchestrator():
    application = Application.builder().token(BOT_TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler("start", start))
    for handler in get_handlers():
        application.add_handler(handler)

    bot = Bot(token=BOT_TOKEN)

    print("🤖 MTF Futures Scanner iniciado correctamente")

    # Ejecutar TODO correctamente en un solo loop
    await asyncio.gather(
        application.initialize(),
        application.start(),
        application.updater.start_polling(),
        asyncio.to_thread(scan_market, bot),   # loop bloqueante → thread seguro
        scheduler_loop(bot),                   # async nativo
    )

# ======================================================
# ENTRYPOINT ÚNICO (WORKER)
# ======================================================

def run_bot():
    asyncio.run(orchestrator())
