from datetime import datetime, date
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, filters

from app.database import users_collection
from app.models import istrialactive, isplanactive, update_timestamp
from app.plans import PLANFREE, PLANPLUS, PLANPREMIUM, activatepremium, activateplus
from app.signals import (
    getlatestbasesignalfor_plan,
    generateusersignal,
    formatusersignal,
)
from app.statistics import (
    getdailystats,
    getweeklystats,
    getmonthlystats,
)
from app.config import isadmin, getadmin_whatsapps

DAILY_LIMITS = {
    PLANFREE: 3,
    PLANPLUS: 5,
    PLANPREMIUM: 7,
}

ADMINDAILYLIMIT = 999999

def backtomenu():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ Volver al menú", callback_data="backmenu")]]
    )

def formatwhatsapp_contacts():
    whatsapps = getadmin_whatsapps()
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
    admin = isadmin(user_id)

    # ================ ADMIN PANEL ================
    if action == "admin_panel" and admin:
        await query.edit_message_text(
            "👑 PANEL ADMINISTRADOR",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Activar plan", callback_data="admin_activate_plan")],
                [InlineKeyboardButton("👥 Consultar referidos válidos", callback_data="admin_referrals")],
                [InlineKeyboardButton("⬅️ Volver", callback_data="backmenu")],
            ])
        )
        return

    if action == "admin_activate_plan" and admin:
        context.user_data["awaiting_user_id"] = True
        await query.edit_message_text("🆔 Envía el User ID del usuario:")
        return

    if action == "admin_referrals" and admin:
        cursor = users_col.find(
            {"$or": [{"refplusvalid": {"$gte": 5}}, {"refpremiumvalid": {"$gte": 5}}]}
        )
        lines = []
        for u in cursor:
            lines.append(
                f"🆔 {u['user_id']} | Plan: {u.get('plan', 'free').upper()}\n"
                f"PLUS válidos: {u.get('refplusvalid',0)}\n"
                f"PREMIUM válidos: {u.get('refpremiumvalid',0)}\n"
                "────────────"
            )
        text = "👥 REFERIDOS VÁLIDOS\n\n" + ("\n".join(lines) if lines else "No hay referidores elegibles.")
        await query.edit_message_text(text, reply_markup=backtomenu())
        return

    # ================ VIEW SIGNALS ================
    if action == "view_signals":
        if not admin and not (isplanactive(user) or istrialactive(user)):
            await query.edit_message_text("⛔ Acceso expirado.", reply_markup=backtomenu())
            return

        today = date.today()
        if not admin and user.get("dailysignaldate") != today.isoformat():
            user["dailysignalcount"] = 0
            user["dailysignaldate"] = today.isoformat()
            user["lastsignalid"] = None
    if admin:
            plans = [PLANPREMIUM, PLANPLUS, PLANFREE]
            sent_any = False
            for plan in plans:
                base_signals = getlatestbasesignalfor_plan(user_id, plan)
                if not base_signals:
                    continue
                for basesignal in base_signals:
                    if basesignal["validuntil"] < datetime.utcnow():
                        continue
                    await query.edit_message_text(
                        formatusersignal(generateusersignal(basesignal, user_id)),
                        reply_markup=backtomenu(),
                    )
                    sent_any = True
            if not sent_any:
                await query.edit_message_text("📭 No hay señales.", reply_markup=backtomenu())
            return

        plan = user.get("plan", PLANFREE)
        limit = DAILY_LIMITS.get(plan, 0)
        if user.get("dailysignalcount", 0) >= limit:
            await query.edit_message_text("⚠️ Límite diario alcanzado.", reply_markup=backtomenu())
            return

        base_signals = getlatestbasesignalfor_plan(user_id, plan)
        if not base_signals:
            await query.edit_message_text("📭 No hay señales.", reply_markup=backtomenu())
            return

        basesignal = base_signals[0]
        if basesignal["validuntil"] < datetime.utcnow():
            await query.edit_message_text("⏳ Señal expirada.", reply_markup=backtomenu())
            return

        signal_id = str(basesignal["_id"])
        if signal_id != user.get("lastsignal_id"):
            user["dailysignalcount"] += 1
            user["lastsignalid"] = signal_id

        users_col.update_one({"user_id": user_id}, {"$set": update_timestamp(user)})
        await query.edit_message_text(
            formatusersignal(generateusersignal(basesignal, user_id)),
            reply_markup=backtomenu(),
        )
        return

    # ================ PLANS =================
    if action == "plans":
        await query.edit_message_text(
            "💼 PLANES DISPONIBLES\n\n"
            "🟢 FREE – 3 señales/día\n"
            "🟡 PLUS – 5 señales/día\n"
            "🔴 PREMIUM – 7 señales/día\n\n"
            "Contacta a un administrador:\n"
            f"{formatwhatsapp_contacts()}",
            reply_markup=backtomenu(),
        )
        return

    # ================ SUPPORT =================
    if action == "support":
        await query.edit_message_text(
            f"📩 SOPORTE\n\n{formatwhatsapp_contacts()}",
            reply_markup=backtomenu(),
        )
        return

# ========================
# HANDLER DE ADMIN PARA ACTIVAR PLAN POR ID
# ========================
async def handle_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_user_id"):
        return

    try:
        user_id = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ ID inválido. Envía solo el número del User ID.")
        return

    # Aquí damos opción de activar PLUS o PREMIUM
    keyboard = [
        [InlineKeyboardButton("Activar PLUS", callback_data=f"admin_set_plus_{user_id}")],
        [InlineKeyboardButton("Activar PREMIUM", callback_data=f"admin_set_premium_{user_id}")]
    ]
    await update.message.reply_text("Selecciona el plan a activar:", reply_markup=InlineKeyboardMarkup(keyboard))
    context.user_data["awaiting_user_id"] = False

def get_handlers():
    return [
        CallbackQueryHandler(handle_menu),
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_text),
              ]
