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
from app.statistics import (
    get_daily_stats,
    get_weekly_stats,
    get_monthly_stats,
)
from app.config import is_admin, get_admin_whatsapps


# ======================================================
# CONFIGURACIÓN DE LÍMITES POR PLAN
# ======================================================

DAILY_LIMITS = {
    PLAN_FREE: 3,
    PLAN_PLUS: 5,
    PLAN_PREMIUM: 7,
}

ADMIN_DAILY_LIMIT = 999999  # admin sin límites


# ======================================================
# MENÚ AUXILIAR
# ======================================================

def back_to_menu():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ Volver al menú", callback_data="back_menu")]]
    )


def _format_whatsapp_contacts() -> str:
    whatsapps = get_admin_whatsapps()
    if not whatsapps:
        return "WhatsApp: (no configurado)"
    if len(whatsapps) == 1:
        return f"WhatsApp: {whatsapps[0]}"
    return "WhatsApps:\n- " + "\n- ".join(whatsapps)


# ======================================================
# HANDLER PRINCIPAL
# ======================================================

async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    users_col = users_collection()
    user = users_col.find_one({"user_id": query.from_user.id})

    if not user:
        await query.edit_message_text("Usuario no encontrado. Usa /start nuevamente.")
        return

    action = query.data
    user_id = user["user_id"]
    admin = is_admin(user_id)

    # ==================================================
    # PANEL ADMIN
    # ==================================================
    if action == "admin_panel" and admin:
        await query.edit_message_text(
            "👑 PANEL ADMINISTRADOR\n\n"
            "Selecciona una acción:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Activar plan", callback_data="admin_activate_plan")],
                [InlineKeyboardButton("⬅️ Volver", callback_data="back_menu")]
            ])
        )
        return

    # ==================================================
    # ADMIN → ACTIVAR PLAN
    # ==================================================
    if action == "admin_activate_plan" and admin:
        context.user_data["awaiting_user_id"] = True
        await query.edit_message_text(
            "🆔 Envía el *User ID* del usuario a activar:",
            parse_mode="Markdown"
        )
        return

    # ==================================================
    # VER SEÑALES  ✅ CONSUMO REAL POR SIGNAL_ID
    # ==================================================
    if action == "view_signals":

        if not admin:
            if not (is_plan_active(user) or is_trial_active(user)):
                await query.edit_message_text(
                    "⛔ Tu acceso ha expirado.\n\n"
                    "Revisa los planes disponibles para continuar.",
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton("💼 Ver planes", callback_data="plans")]]
                    ),
                )
                return

        today = date.today()

        # Reset diario (NO admin)
        if not admin:
            if user.get("daily_signal_date") != today.isoformat():
                user["daily_signal_date"] = today.isoformat()
                user["daily_signal_count"] = 0
                user["last_signal_id"] = None

        plan = PLAN_PREMIUM if admin else user.get("plan", PLAN_FREE)
        daily_limit = ADMIN_DAILY_LIMIT if admin else DAILY_LIMITS.get(plan, 0)

        if not admin and user.get("daily_signal_count", 0) >= daily_limit:
            await query.edit_message_text(
                "⚠️ Límite diario alcanzado.",
                reply_markup=back_to_menu(),
            )
            return

        base_signal = get_latest_base_signal_for_plan(plan)
        if not base_signal:
            await query.edit_message_text(
                "📭 No hay señales activas.",
                reply_markup=back_to_menu(),
            )
            return

        # ⛔ Si la señal ya expiró → no cuenta
        if base_signal["valid_until"] < datetime.utcnow():
            await query.edit_message_text(
                "⏳ Esta señal ya ha expirado.",
                reply_markup=back_to_menu(),
            )
            return

        signal_id = str(base_signal["_id"])
        last_signal_id = user.get("last_signal_id")

        # ✅ SOLO CONSUME SI ES UNA SEÑAL NUEVA
        consume_signal = (signal_id != last_signal_id)

        user_signal = generate_user_signal(base_signal, user_id)

        if not admin and consume_signal:
            user["daily_signal_count"] = user.get("daily_signal_count", 0) + 1
            user["last_signal_id"] = signal_id
            user["daily_signal_date"] = today.isoformat()

        user = update_timestamp(user)
        users_col.update_one({"user_id": user_id}, {"$set": user})

        await query.edit_message_text(
            format_user_signal(user_signal),
            reply_markup=back_to_menu(),
        )

    # ==================================================
    # PLANES
    # ==================================================
    elif action == "plans":
        contacts = _format_whatsapp_contacts()
        await query.edit_message_text(
            "💼 PLANES DISPONIBLES\n\n"
            "🟡 PLUS – 2 USDT\n"
            "🔴 PREMIUM – 4 USDT\n\n"
            "Contacta a un administrador:\n"
            f"{contacts}",
            reply_markup=back_to_menu(),
        )

    # ==================================================
    # MI CUENTA
    # ==================================================
    elif action == "my_account":
        if admin:
            await query.edit_message_text(
                "👑 CUENTA ADMIN\n\nAcceso total.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("👑 Panel Admin", callback_data="admin_panel")],
                    [InlineKeyboardButton("⬅️ Volver", callback_data="back_menu")]
                ])
            )
            return

        await query.edit_message_text(
            f"👤 MI CUENTA\n\n"
            f"Plan: {user.get('plan', PLAN_FREE).upper()}\n"
            f"Señales usadas hoy: {user.get('daily_signal_count', 0)}",
            reply_markup=back_to_menu(),
        )

    # ==================================================
    # SOPORTE
    # ==================================================
    elif action == "support":
        await query.edit_message_text(
            "📩 Contacta a un administrador.",
            reply_markup=back_to_menu(),
        )

    # ==================================================
    # VOLVER
    # ==================================================
    elif action == "back_menu":
        from app.bot import main_menu
        await query.edit_message_text("Menú principal", reply_markup=main_menu())


# ======================================================
# HANDLERS
# ======================================================

def get_handlers():
    return [CallbackQueryHandler(handle_menu)]
