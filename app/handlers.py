from datetime import datetime, date
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, filters

from app.database import users_collection
from app.models import is_trial_active, is_plan_active, update_timestamp
from app.plans import PLAN_FREE, PLAN_PLUS, PLAN_PREMIUM, activate_premium
from app.signals import (
    get_latest_base_signal_for_plan,
    generate_user_signal,
    format_user_signal,
)
from app.config import is_admin, get_admin_whatsapps


DAILY_LIMITS = {
    PLAN_FREE: 3,
    PLAN_PLUS: 5,
    PLAN_PREMIUM: 7,
}


def back_to_menu():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ Volver al menú", callback_data="back_menu")]]
    )


def format_whatsapp_contacts():
    whatsapps = get_admin_whatsapps()
    if not whatsapps:
        return "WhatsApp: (no configurado)"
    if len(whatsapps) == 1:
        return f"WhatsApp: {whatsapps[0]}"
    return "WhatsApps:\n- " + "\n- ".join(whatsapps)


# ======================================================
# HANDLER MENÚ PRINCIPAL
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
    user_id = user["user_id"]
    admin = is_admin(user_id)

    # ================= ADMIN PANEL =================

    if action == "admin_panel" and admin:
        await query.edit_message_text(
            "👑 PANEL ADMINISTRADOR",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Activar plan PREMIUM", callback_data="admin_activate_plan")],
                [InlineKeyboardButton("⬅️ Volver", callback_data="back_menu")],
            ])
        )
        return

    if action == "admin_activate_plan" and admin:
        context.user_data["awaiting_user_id"] = True
        await query.edit_message_text("🆔 Envía el User ID del usuario:")
        return

    # ================= VER SEÑALES =================

    if action == "view_signals":
        if not admin and not (is_plan_active(user) or is_trial_active(user)):
            await query.edit_message_text(
                "⛔ Acceso expirado.",
                reply_markup=back_to_menu(),
            )
            return

        today = date.today().isoformat()
        if user.get("daily_signal_date") != today:
            user["daily_signal_count"] = 0
            user["daily_signal_date"] = today
            user["last_signal_id"] = None

        plan = user.get("plan", PLAN_FREE)

        if not admin:
            limit = DAILY_LIMITS.get(plan, 0)
            if user.get("daily_signal_count", 0) >= limit:
                await query.edit_message_text(
                    "⚠️ Límite diario alcanzado.",
                    reply_markup=back_to_menu(),
                )
                return

        base_signals = get_latest_base_signal_for_plan(user_id, plan)

        if not base_signals:
            await query.edit_message_text(
                "📭 No hay señales disponibles.",
                reply_markup=back_to_menu(),
            )
            return

        base_signal = base_signals[0]

        signal_id = str(base_signal["_id"])
        if signal_id != user.get("last_signal_id"):
            user["daily_signal_count"] = user.get("daily_signal_count", 0) + 1
            user["last_signal_id"] = signal_id

        user = update_timestamp(user)
        users_col.update_one(
            {"user_id": user_id},
            {"$set": user},
        )

        user_signal = generate_user_signal(base_signal, user_id)

        await query.edit_message_text(
            format_user_signal(user_signal),
            reply_markup=back_to_menu(),
        )
        return

    # ================= PLANES =================

    if action == "plans":
        await query.edit_message_text(
            "💼 PLANES DISPONIBLES\n\n"
            "🟢 FREE – 3 señales/día\n"
            "🟡 PLUS – 5 señales/día\n"
            "🔴 PREMIUM – 7 señales/día\n\n"
            "Contacta a un administrador:\n"
            f"{format_whatsapp_contacts()}",
            reply_markup=back_to_menu(),
        )
        return

    # ================= MI CUENTA =================

    if action == "my_account":
        await query.edit_message_text(
            f"👤 MI CUENTA\n\n"
            f"ID: {user_id}\n"
            f"Plan: {user.get('plan', PLAN_FREE).upper()}\n"
            f"Señales hoy: {user.get('daily_signal_count', 0)}",
            reply_markup=back_to_menu(),
        )
        return

    # ================= SOPORTE =================

    if action == "support":
        await query.edit_message_text(
            f"📩 SOPORTE\n\n{format_whatsapp_contacts()}",
            reply_markup=back_to_menu(),
        )
        return

    # ================= BACK =================

    if action == "back_menu":
        from app.bot import main_menu
        await query.edit_message_text(
            "Menú principal",
            reply_markup=main_menu(),
        )


# ======================================================
# HANDLER ADMIN (ACTIVAR PLAN)
# ======================================================

async def handle_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_user_id"):
        return

    try:
        target_user_id = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ ID inválido.")
        return

    success = activate_premium(target_user_id)

    if success:
        await update.message.reply_text(
            f"✅ Plan PREMIUM activado para el usuario {target_user_id}."
        )
    else:
        await update.message.reply_text(
            f"❌ No se pudo activar el plan para el usuario {target_user_id}."
        )

    context.user_data["awaiting_user_id"] = False


# ======================================================
# REGISTRO DE HANDLERS
# ======================================================

def get_handlers():
    return [
        CallbackQueryHandler(handle_menu),
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_text),
  ]
