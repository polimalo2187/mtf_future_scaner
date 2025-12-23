from datetime import datetime, date
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, filters

from app.database import users_collection
from app.models import istrialactive, isplanactive, update_timestamp
from app.plans import PLANFREE, PLANPLUS, PLANPREMIUM, activatepremium
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
    PLAN_FREE: 3,
    PLAN_PLUS: 5,
    PLAN_PREMIUM: 7,
}

ADMINDAILYLIMIT = 999999

def backtomenu():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ Volver al menú", callbackdata="backmenu")]]
    )

def formatwhatsapp_contacts():
    whatsapps = getadminwhatsapps()
    if not whatsapps:
        return "WhatsApp: (no configurado)"
    if len(whatsapps) == 1:
        return f"WhatsApp: {whatsapps[0]}"
    return "WhatsApps:\n- " + "\n- ".join(whatsapps)

========================

HANDLER DE MENÚ PRINCIPAL

========================

async def handlemenu(update: Update, context: ContextTypes.DEFAULTTYPE):
    query = update.callback_query
    await query.answer()

    userscol = userscollection()
    user = userscol.findone({"userid": query.fromuser.id})
    if not user:
        await query.editmessagetext("Usuario no encontrado. Usa /start nuevamente.")
        return

    action = query.data
    userid = user["userid"]
    admin = isadmin(userid)

================= ADMIN PANEL =================

    if action == "admin_panel" and admin:
        await query.editmessagetext(
            "👑 PANEL ADMINISTRADOR",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Activar plan", callbackdata="adminactivate_plan")],
                [InlineKeyboardButton("👥 Consultar referidos válidos", callbackdata="adminreferrals")],
                [InlineKeyboardButton("⬅️ Volver", callbackdata="backmenu")],
            ])
        )
        return

    if action == "adminactivateplan" and admin:
        context.userdata["awaitinguser_id"] = True
        await query.editmessagetext("🆔 Envía el User ID del usuario:")
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
        await query.editmessagetext(text, replymarkup=backto_menu())    
        return

================= VIEW SIGNALS =================

    if action == "view_signals":
        if not admin and not (isplanactive(user) or istrialactive(user)):
            await query.editmessagetext("⛔ Acceso expirado.", replymarkup=backto_menu())
            return

        today = date.today()    
        if not admin and user.get("dailysignaldate") != today.isoformat():    
            user["dailysignalcount"] = 0    
            user["dailysignaldate"] = today.isoformat()    
            user["lastsignalid"] = None    

================= ADMIN: VER TODAS LAS SEÑALES =================
        if admin:    
            plans = [PLANPREMIUM, PLANPLUS, PLAN_FREE]    

            sent_any = False    
            for plan in plans:    
                basesignals = getlatestbasesignalforplan(user_id, plan)    
                if not base_signals:    
                    continue    
                for basesignal in basesignals:    
                    if basesignal["validuntil"] < datetime.utcnow():    
                        continue    

                    await query.editmessagetext(    
                        formatusersignal(generateusersignal(basesignal, userid)),    
                        replymarkup=backto_menu(),    
                    )    
                    sent_any = True    

            if not sent_any:    
                await query.editmessagetext("📭 No hay señales.", replymarkup=backto_menu())    

            return    

================= USUARIO NORMAL (SIN CAMBIOS) =================
        plan = user.get("plan", PLAN_FREE)    
        limit = DAILY_LIMITS.get(plan, 0)    

        if user.get("dailysignalcount", 0) >= limit:    
            await query.editmessagetext("⚠️ Límite diario alcanzado.", replymarkup=backto_menu())    
            return    

        basesignals = getlatestbasesignalforplan(user_id, plan)    
        if not base_signals:    
            await query.editmessagetext("📭 No hay señales.", replymarkup=backto_menu())    
            return    

Tomamos la primera señal disponible
        basesignal = basesignals[0]    

        if basesignal["validuntil"] < datetime.utcnow():    
            await query.editmessagetext("⏳ Señal expirada.", replymarkup=backto_menu())    
            return    

        signalid = str(basesignal["_id"])    
        if signalid != user.get("lastsignal_id"):    
            user["dailysignalcount"] += 1    
            user["lastsignalid"] = signal_id    

        userscol.updateone({"userid": userid}, {"$set": update_timestamp(user)})    

        await query.editmessagetext(    
            formatusersignal(generateusersignal(basesignal, userid)),    
            replymarkup=backto_menu(),    
        )    
        return

================= PLANS =================

    if action == "plans":
        await query.editmessagetext(
            "💼 PLANES DISPONIBLES\n\n"
            "🟢 FREE – 3 señales/día\n"
            "🟡 PLUS – 5 señales/día\n"
            "🔴 PREMIUM – 7 señales/día\n\n"
            "Contacta a un administrador:\n"
            f"{formatwhatsapp_contacts()}",
            replymarkup=backto_menu(),
        )
        return

================= MY ACCOUNT =================

    if action == "my_account":
        if admin:
            await query.editmessagetext(
                f"👑 MI CUENTA (ADMIN)\n\n"
                f"ID: {user_id}\n"
                "Plan: PREMIUM\n"
                "Acceso total\n",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("👑 Panel Administrador", callbackdata="adminpanel")],
                    [InlineKeyboardButton("⬅️ Volver", callbackdata="backmenu")],
                ]),
            )
        else:
            await query.editmessagetext(
                f"👤 MI CUENTA\n\n"
                f"ID: {user_id}\n"
                f"Plan: {user.get('plan', PLAN_FREE).upper()}\n"
                f"Señales hoy: {user.get('dailysignalcount', 0)}",
                replymarkup=backto_menu(),
            )
        return

================= REFERRALS =================

    if action == "referrals":
        link = f"https://t.me/{context.bot.username}?start=ref{userid}"
        await query.editmessagetext(
            f"👥 TU ENLACE DE REFERIDO:\n\n{link}",
            replymarkup=backto_menu(),
        )
        return

================= SUPPORT =================

    if action == "support":
        await query.editmessagetext(
            f"📩 SOPORTE\n\n{formatwhatsapp_contacts()}",
            replymarkup=backto_menu(),
        )
        return

================= BACK =================

    if action == "back_menu":
        from app.bot import main_menu
        await query.editmessagetext("Menú principal", replymarkup=mainmenu())

========================

HANDLER DE ADMIN PARA ACTIVAR PLAN POR ID

========================

async def handleadmintext(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.userdata.get("awaitinguser_id"):
        return  # No estamos esperando un User ID

    try:
        user_id = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ ID inválido. Envía solo el número del User ID.")
        return

    # Activamos plan PREMIUM por defecto
    success = activatepremium(userid)
    if success:
        await update.message.replytext(f"✅ Plan PREMIUM activado para el usuario {userid}.")
    else:
        await update.message.replytext(f"❌ No se pudo activar el plan para el usuario {userid}.")
      # Limpiar flag
    context.userdata["awaitinguser_id"] = False

========================

REGISTRAR HANDLERS

========================
def get_handlers():
    return [
        CallbackQueryHandler(handle_menu),
        MessageHandler(filters.TEXT & ~filters.COMMAND, handleadmintext),
]
