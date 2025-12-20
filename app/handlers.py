# app/handlers.py

from datetime import datetime, date
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler

from app.database import users_collection
from app.models import is_trial_active, is_plan_active, update_timestamp
from app.plans import PLAN_FREE, PLAN_PLUS, PLAN_PREMIUM
from app.signals import (
    get_latest_base_signal_for_plan,
    generate_user_signal,
    format_user_signal,
)

# ======================================================
# CONFIGURACIÓN DE LÍMITES POR PLAN
# ======================================================

DAILY_LIMITS = {
    PLAN_FREE: 3,
    PLAN_PLUS: 5,
    PLAN_PREMIUM: 7,
}


# ======================================================
# MENÚ AUXILIAR
# ======================================================

def back_to_menu():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ Volver al menú", callback_data="back_menu")]]
    )


# ======================================================
# HANDLER PRINCIPAL DE BOTONES
# ======================================================

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

    # ==================================================
    # VER SEÑALES (ENTREGA CONTROLADA)
    # ==================================================
    if action == "view_signals":

        # 1️⃣ Validar acceso
        if not (is_plan_active(user) or is_trial_active(user)):
            await query.edit_message_text(
                "⛔ Tu acceso ha expirado.\n\n"
                "Para continuar recibiendo señales, revisa los planes disponibles.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("💼 Ver planes", callback_data="plans")]]
                ),
            )
            return

        # 2️⃣ Reset contador diario si cambió el día
        today = date.today()
        if user.get("daily_signal_date") != today.isoformat():
            user["daily_signal_date"] = today.isoformat()
            user["daily_signal_count"] = 0

        plan = user.get("plan", PLAN_FREE)
        daily_limit = DAILY_LIMITS.get(plan, 0)

        # 3️⃣ Validar límite diario
        if user.get("daily_signal_count", 0) >= daily_limit:
            await query.edit_message_text(
                "⚠️ Límite diario alcanzado.\n\n"
                f"Tu plan permite {daily_limit} señales por día.\n"
                "Intenta nuevamente mañana.",
                reply_markup=back_to_menu(),
            )
            return

        # 4️⃣ Obtener señal base válida
        base_signal = get_latest_base_signal_for_plan(plan)
        if not base_signal:
            await query.edit_message_text(
                "📭 No hay señales activas en este momento.\n\n"
                "Cuando se detecte una oportunidad, recibirás una alerta.",
                reply_markup=back_to_menu(),
            )
            return

        # 5️⃣ Validar vigencia
        if base_signal["valid_until"] < datetime.utcnow():
            await query.edit_message_text(
                "⏳ La señal más reciente ha expirado.\n\n"
                "Espera la próxima oportunidad.",
                reply_markup=back_to_menu(),
            )
            return

        # 6️⃣ Generar señal personalizada
        user_signal = generate_user_signal(
            base_signal=base_signal,
            user_id=user["user_id"],
        )

        # 7️⃣ Incrementar contador
        user["daily_signal_count"] = user.get("daily_signal_count", 0) + 1
        user = update_timestamp(user)
        users_col.update_one(
            {"user_id": user["user_id"]},
            {"$set": user},
        )

        # 8️⃣ Mostrar señal
        await query.edit_message_text(
            format_user_signal(user_signal),
            reply_markup=back_to_menu(),
        )

    # ==================================================
    # PLANES
    # ==================================================
    elif action == "plans":
        await query.edit_message_text(
            "💼 PLANES DISPONIBLES\n\n"
            "🟢 FREE\n"
            "- 7 días de prueba\n"
            "- 3 señales por día\n\n"
            "🟡 PLUS\n"
            "- 5 señales por día\n"
            "- 2 USDT / 30 días\n\n"
            "🔴 PREMIUM\n"
            "- 7 señales por día\n"
            "- 4 USDT / 30 días\n\n"
            "Pagos en USDT (BSC).\n"
            "Contacta al administrador para activar.",
            reply_markup=back_to_menu(),
        )

    # ==================================================
    # MI CUENTA
    # ==================================================
    elif action == "my_account":
        plan = user.get("plan", PLAN_FREE).upper()
        used = user.get("daily_signal_count", 0)
        today = user.get("daily_signal_date", "-")

        await query.edit_message_text(
            f"👤 MI CUENTA\n\n"
            f"ID: {user['user_id']}\n"
            f"Plan: {plan}\n"
            f"Señales usadas hoy: {used}\n"
            f"Fecha de control: {today}",
            reply_markup=back_to_menu(),
        )

    # ==================================================
    # REFERIDOS
    # ==================================================
    elif action == "referrals":
        bot_username = context.bot.username
        ref_link = f"https://t.me/{bot_username}?start=ref_{user['user_id']}"

        await query.edit_message_text(
            "👥 SISTEMA DE REFERIDOS\n\n"
            f"Tu enlace:\n{ref_link}\n\n"
            "Recompensas automáticas:\n"
            "- 5 PLUS → PLUS gratis\n"
            "- 5 PREMIUM → PREMIUM gratis\n"
            "- PREMIUM: 10 PLUS o 5 PREMIUM → +30 días",
            reply_markup=back_to_menu(),
        )

    # ==================================================
    # SOPORTE
    # ==================================================
    elif action == "support":
        await query.edit_message_text(
            "📩 SOPORTE\n\n"
            "Para pagos o activaciones, contacta al administrador.",
            reply_markup=back_to_menu(),
        )

    # ==================================================
    # VOLVER AL MENÚ
    # ==================================================
    elif action == "back_menu":
        from app.bot import main_menu
        await query.edit_message_text(
            "Menú principal",
            reply_markup=main_menu(),
        )


# ======================================================
# REGISTRO DEL HANDLER
# ======================================================

def get_handlers():
    return [
        CallbackQueryHandler(handle_menu)
      ]
