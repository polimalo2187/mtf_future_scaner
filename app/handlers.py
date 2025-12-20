# app/handlers.py

import os
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler

from app.database import users_collection
from app.models import is_trial_active, is_plan_active

load_dotenv()

ADMIN_WHATSAPP = os.getenv("ADMIN_WHATSAPP", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "MTFFuturesScannerBot")


# =========================
# UTILIDADES
# =========================

def back_to_menu_button():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ Volver al menú", callback_data="back_menu")]]
    )


def support_keyboard():
    if ADMIN_WHATSAPP:
        return InlineKeyboardMarkup(
            [[
                InlineKeyboardButton(
                    "📲 Contactar por WhatsApp",
                    url=f"https://wa.me/{ADMIN_WHATSAPP}"
                )
            ]]
        )
    return back_to_menu_button()


# =========================
# HANDLERS DE BOTONES
# =========================

async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    users_col = users_collection()
    user = users_col.find_one({"user_id": query.from_user.id})

    if not user:
        await query.edit_message_text(
            "Usuario no encontrado. Usa /start nuevamente."
        )
        return

    action = query.data

    # =========================
    # VER SEÑALES
    # =========================
    if action == "view_signals":
        if is_plan_active(user) or is_trial_active(user):
            await query.edit_message_text(
                "📊 Señales disponibles.\n\n"
                "Cuando se genere una nueva señal, recibirás una notificación.\n"
                "Accede aquí para consultarla.\n\n"
                "Las señales incluyen:\n"
                "- Entrada\n"
                "- Stop Loss\n"
                "- Take Profit\n"
                "- Apalancamiento sugerido\n"
                "- Margen aislado fijo",
                reply_markup=back_to_menu_button(),
            )
        else:
            await query.edit_message_text(
                "⛔ Tu acceso ha expirado.\n\n"
                "Para continuar recibiendo señales, revisa los planes disponibles.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("💼 Ver planes", callback_data="plans")]]
                ),
            )

    # =========================
    # PLANES
    # =========================
    elif action == "plans":
        await query.edit_message_text(
            "💼 PLANES DISPONIBLES\n\n"
            "🟢 FREE (7 días)\n"
            "- Acceso de prueba\n"
            "- Señales limitadas\n\n"
            "🟡 PLUS\n"
            "- Señales en tiempo real\n"
            "- Precio: 2 USDT / 30 días\n\n"
            "🔴 PREMIUM\n"
            "- Acceso completo a todas las señales\n"
            "- Precio: 4 USDT / 30 días\n\n"
            "Todos los pagos se realizan en USDT (BSC).\n"
            "La activación se realiza contactando al administrador.",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("🟡 Activar PLUS", callback_data="buy_plus"),
                        InlineKeyboardButton("🔴 Activar PREMIUM", callback_data="buy_premium"),
                    ],
                    [InlineKeyboardButton("⬅️ Volver", callback_data="back_menu")],
                ]
            ),
        )

    elif action == "buy_plus":
        await query.edit_message_text(
            "🟡 PLAN PLUS\n\n"
            "Precio: 2 USDT\n"
            "Duración: 30 días\n"
            "Red: USDT BSC\n\n"
            "Para activar este plan, contacta al administrador.",
            reply_markup=support_keyboard(),
        )

    elif action == "buy_premium":
        await query.edit_message_text(
            "🔴 PLAN PREMIUM\n\n"
            "Precio: 4 USDT\n"
            "Duración: 30 días\n"
            "Red: USDT BSC\n\n"
            "Para activar este plan, contacta al administrador.",
            reply_markup=support_keyboard(),
        )

    # =========================
    # MI CUENTA
    # =========================
    elif action == "my_account":
        plan = user.get("plan", "free").upper()
        now = datetime.utcnow()

        if is_plan_active(user):
            expires = user["plan_end"].strftime("%d/%m/%Y")
            status = "Activo"
        elif is_trial_active(user):
            expires = user["trial_end"].strftime("%d/%m/%Y")
            status = "Trial activo"
        else:
            expires = "Expirado"
            status = "Inactivo"

        await query.edit_message_text(
            f"👤 MI CUENTA\n\n"
            f"ID: {user['user_id']}\n"
            f"Plan: {plan}\n"
            f"Estado: {status}\n"
            f"Vence: {expires}\n\n"
            f"Red de pago: USDT BSC",
            reply_markup=back_to_menu_button(),
        )

    # =========================
    # REFERIDOS
    # =========================
    elif action == "referrals":
        ref_link = f"https://t.me/{BOT_USERNAME}?start=ref_{user['user_id']}"

        await query.edit_message_text(
            "👥 SISTEMA DE REFERIDOS\n\n"
            f"Tu enlace:\n{ref_link}\n\n"
            f"Referidos PLUS: {user.get('ref_plus_valid', 0)}\n"
            f"Referidos PREMIUM: {user.get('ref_premium_valid', 0)}\n\n"
            "Recompensas automáticas:\n"
            "- 5 PLUS → Plan PLUS 30 días\n"
            "- 5 PREMIUM → Plan PREMIUM 30 días\n"
            "- Usuarios PREMIUM: 10 PLUS o 5 PREMIUM → +30 días PREMIUM",
            reply_markup=back_to_menu_button(),
        )

    # =========================
    # SOPORTE
    # =========================
    elif action == "support":
        await query.edit_message_text(
            "📩 SOPORTE\n\n"
            "Para soporte, pagos o activaciones, contacta al administrador.",
            reply_markup=support_keyboard(),
        )

    # =========================
    # VOLVER AL MENÚ
    # =========================
    elif action == "back_menu":
        from app.bot import main_menu
        await query.edit_message_text(
            "Menú principal",
            reply_markup=main_menu(),
        )


# =========================
# REGISTRO DEL HANDLER
# =========================

def get_handlers():
    return [
        CallbackQueryHandler(handle_menu)
  ]
