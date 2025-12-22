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
            "👑 PANEL ADMINISTRADOR\n\nSelecciona una acción:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Activar plan", callback_data="admin_activate_plan")],
                [InlineKeyboardButton("📊 Referidores elegibles", callback_data="admin_referrals_status")],
                [InlineKeyboardButton("⬅️ Volver", callback_data="back_menu")]
            ])
        )
        return

    # ==================================================
    # ADMIN → REFERIDORES ELEGIBLES
    # ==================================================
    if action == "admin_referrals_status" and admin:
        eligible = []

        for u in users_col.find({}):
            plan = u.get("plan", PLAN_FREE)
            plus = u.get("ref_plus_valid", 0)
            premium = u.get("ref_premium_valid", 0)

            if plan == PLAN_FREE and (plus >= 5 or premium >= 5):
                eligible.append(u)
            elif plan == PLAN_PLUS and (premium >= 5 or plus >= 5):
                eligible.append(u)
            elif plan == PLAN_PREMIUM and (premium >= 5 or plus >= 10):
                eligible.append(u)

        if not eligible:
            await query.edit_message_text(
                "📭 No hay referidores elegibles actualmente.",
                reply_markup=back_to_menu(),
            )
            return

        text = "📊 *REFERIDORES ELEGIBLES*\n\n"

        for u in eligible:
            text += (
                f"👤 ID: `{u['user_id']}`\n"
                f"Plan: {u.get('plan', PLAN_FREE).upper()}\n"
                f"PLUS válidos: {u.get('ref_plus_valid', 0)}\n"
                f"PREMIUM válidos: {u.get('ref_premium_valid', 0)}\n"
                "──────────────\n"
            )

        await query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=back_to_menu(),
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
    # VER SEÑALES
    # ==================================================
    if action == "view_signals":

        if not admin:
            if not (is_plan_active(user) or is_trial_active(user)):
                await query.edit_message_text(
                    "⛔ Tu acceso ha expirado.\n\nRevisa los planes disponibles.",
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton("💼 Ver planes", callback_data="plans")]]
                    ),
                )
                return

        today = date.today()

        if not admin and user.get("daily_signal_date") != today.isoformat():
            user["daily_signal_date"] = today.isoformat()
            user["daily_signal_count"] = 0
            user["last_signal_id"] = None

        plan = PLAN_PREMIUM if admin else user.get("plan", PLAN_FREE)
        daily_limit = ADMIN_DAILY_LIMIT if admin else DAILY_LIMITS.get(plan, 0)

        if not admin and user.get("daily_signal_count", 0) >= daily_limit:
            await query.edit_message_text("⚠️ Límite diario alcanzado.", reply_markup=back_to_menu())
            return

        base_signal = get_latest_base_signal_for_plan(plan)
        if not base_signal or base_signal["valid_until"] < datetime.utcnow():
            await query.edit_message_text("📭 No hay señales activas.", reply_markup=back_to_menu())
            return

        signal_id = str(base_signal["_id"])
        if not admin and signal_id != user.get("last_signal_id"):
            user["daily_signal_count"] += 1
            user["last_signal_id"] = signal_id
            user["daily_signal_date"] = today.isoformat()

        user = update_timestamp(user)
        users_col.update_one({"user_id": user_id}, {"$set": user})

        await query.edit_message_text(
            format_user_signal(generate_user_signal(base_signal, user_id)),
            reply_markup=back_to_menu(),
        )

    elif action == "plans":
        await query.edit_message_text(
            "💼 PLANES DISPONIBLES\n\n🟡 PLUS – 2 USDT\n🔴 PREMIUM – 4 USDT",
            reply_markup=back_to_menu(),
        )

    elif action == "support":
        await query.edit_message_text("📩 Contacta a un administrador.", reply_markup=back_to_menu())

    elif action == "back_menu":
        from app.bot import main_menu
        await query.edit_message_text("Menú principal", reply_markup=main_menu())


def get_handlers():
    return [CallbackQueryHandler(handle_menu)]
