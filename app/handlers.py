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
        context.user_data["awaiting_user_id"] = True
        await query.edit_message_text("🆔 Envía el User ID del usuario:")
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
    if action == "view_signals":
        if not admin and not (is_plan_active(user) or is_trial_active(user)):
            await query.edit_message_text("⛔ Acceso expirado.", reply_markup=back_to_menu())
            return

        today = date.today()
        if not admin and user.get("daily_signal_date") != today.isoformat():
            user["daily_signal_count"] = 0
            user["daily_signal_date"] = today.isoformat()
            user["last_signal_id"] = None

        plan = PLAN_PREMIUM if admin else user.get("plan", PLAN_FREE)
        limit = ADMIN_DAILY_LIMIT if admin else DAILY_LIMITS.get(plan, 0)

        if not admin and user.get("daily_signal_count", 0) >= limit:
            await query.edit_message_text("⚠️ Límite diario alcanzado.", reply_markup=back_to_menu())
            return

        base_signal = get_latest_base_signal_for_plan(plan)
        if not base_signal:
            await query.edit_message_text("📭 No hay señales.", reply_markup=back_to_menu())
            return

        if base_signal["valid_until"] < datetime.utcnow():
            await query.edit_message_text("⏳ Señal expirada.", reply_markup=back_to_menu())
            return

        signal_id = str(base_signal["_id"])
        if not admin and signal_id != user.get("last_signal_id"):
            user["daily_signal_count"] += 1
            user["last_signal_id"] = signal_id

        users_col.update_one({"user_id": user_id}, {"$set": update_timestamp(user)})

        await query.edit_message_text(
            format_user_signal(generate_user_signal(base_signal, user_id)),
            reply_markup=back_to_menu(),
        )
        return

    # ================= PLANS =================
    if action == "plans":
        await query.edit_message_text(
            "💼 PLANES DISPONIBLES\n\n"
            "🟢 FREE – 3 señales/día\n"
            "🟡 PLUS – 5 señales/día\n"
            "🔴 PREMIUM – 7 señales/día\n\n"
            "Contacta a un administrador:\n"
            f"{_format_whatsapp_contacts()}",
            reply_markup=back_to_menu(),
        )
        return

    # ================= MY ACCOUNT =================
    if action == "my_account":
        if admin:
            await query.edit_message_text(
                f"👑 MI CUENTA (ADMIN)\n\n"
                f"ID: {user_id}\n"
                "Plan: PREMIUM\n"
                "Acceso total\n",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("👑 Panel Administrador", callback_data="admin_panel")],
                    [InlineKeyboardButton("⬅️ Volver", callback_data="back_menu")],
                ]),
            )
        else:
            await query.edit_message_text(
                f"👤 MI CUENTA\n\n"
                f"ID: {user_id}\n"
                f"Plan: {user.get('plan', PLAN_FREE).upper()}\n"
                f"Señales hoy: {user.get('daily_signal_count', 0)}",
                reply_markup=back_to_menu(),
            )
        return

    # ================= REFERRALS =================
    if action == "referrals":
        link = f"https://t.me/{context.bot.username}?start=ref_{user_id}"
        await query.edit_message_text(
            f"👥 TU ENLACE DE REFERIDO:\n\n{link}",
            reply_markup=back_to_menu(),
        )
        return

    # ================= SUPPORT =================
    if action == "support":
        await query.edit_message_text(
            f"📩 SOPORTE\n\n{_format_whatsapp_contacts()}",
            reply_markup=back_to_menu(),
        )
        return

    # ================= BACK =================
    if action == "back_menu":
        from app.bot import main_menu
        await query.edit_message_text("Menú principal", reply_markup=main_menu())


def get_handlers():
    return [CallbackQueryHandler(handle_menu)]
