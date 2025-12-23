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
from app.statistics import (
    get_daily_stats,
    get_weekly_stats,
    get_monthly_stats,
)
from app.config import is_admin, get_admin_whatsapps

DAILY_LIMITS = {
    PLAN_FREE: 3,
    PLAN_PLUS: 5,
    PLAN_PREMIUM: 7,
}

ADMIN_DAILY_LIMIT = 999999

def back_to_menu():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ Volver al menú", callback_data="back_menu")]]
    )

def _format_whatsapp_contacts():
    whatsapps = get_admin_whatsapps()
    if not whatsapps:
        return "WhatsApp: (no configurado)"
    if len(whatsapps) == 1:
        return f"WhatsApp: {whatsapps[0]}"
    return "WhatsApps:\n- " + "\n- ".join(whatsapps)

# ========================
# HANDLER DE MENÚ PRINCIPAL
# ========================

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

# ================= ADMIN PANEL =================

    if action == "admin_panel" and admin:
        await query.edit_message_text(
            "👑 PANEL ADMINISTRADOR",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Activar plan", callback_data="admin_activate_plan")],
                [InlineKeyboardButton("👥 Consultar referidos válidos", callback_data="admin_referrals")],
                [InlineKeyboardButton("⬅️ Volver", callback_data="back_menu")],
            ])
        )
        return

    if action == "admin_activate_plan" and admin:
        await query.edit_message_text(
            "Selecciona el plan a activar:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🟡 PLUS", callback_data="activate_plus")],
                [InlineKeyboardButton("🔴 PREMIUM", callback_data="activate_premium")],
                [InlineKeyboardButton("⬅️ Volver", callback_data="admin_panel")],
            ])
        )
        return

  if action == "activate_plus" and admin:
        context.user_data["awaiting_user_id"] = "PLUS"
        await query.edit_message_text("🆔 Envía el User ID del usuario para activar PLUS:")
        return

    if action == "activate_premium" and admin:
        context.user_data["awaiting_user_id"] = "PREMIUM"
        await query.edit_message_text("🆔 Envía el User ID del usuario para activar PREMIUM:")
        return

    if action == "admin_referrals" and admin:
        cursor = users_col.find(
            {"$or": [{"ref_plus_valid": {"$gte": 5}}, {"ref_premium_valid": {"$gte": 5}}]}
        )

        lines = []
        for u in cursor:
            lines.append(
                f"🆔 {u['user_id']} | Plan: {u.get('plan', 'free').upper()}\n"
                f"PLUS válidos: {u.get('ref_plus_valid',0)}\n"
                f"PREMIUM válidos: {u.get('ref_premium_valid',0)}\n"
                "────────────"
            )

        text = "👥 REFERIDOS VÁLIDOS\n\n" + ("\n".join(lines) if lines else "No hay referidores elegibles.")
        await query.edit_message_text(text, reply_markup=back_to_menu())
        return

# ================= VIEW SIGNALS =================
# (sin cambios, igual que tu archivo original)

# ================= PLANS =================
# (sin cambios, igual que tu archivo original)

# ================= MY ACCOUNT =================
# (sin cambios, excepto que ya no aparece el botón de activar plan fuera del panel)

# ================= REFERRALS =================
# (sin cambios)

# ================= SUPPORT =================
# (sin cambios)

# ================= BACK =================
# (sin cambios)

# ========================
# HANDLER DE ADMIN PARA ACTIVAR PLAN POR ID
# ========================

async def handle_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    plan = context.user_data.get("awaiting_user_id")
    if not plan:
        return

    try:
        user_id = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ ID inválido. Envía solo el número del User ID.")
        return

    await update.message.reply_text(
        f"🟢 Solicitud de activación para el usuario {user_id}.\n\n"
        f"Plan seleccionado: {plan}\n"
        f"Para completar la activación, contacta a un administrador:\n"
        f"{_format_whatsapp_contacts()}"
    )

    context.user_data["awaiting_user_id"] = False

# ========================
# REGISTRAR HANDLERS
# ========================
def get_handlers():
    return [
        CallbackQueryHandler(handle_menu),
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_text),
          ]
