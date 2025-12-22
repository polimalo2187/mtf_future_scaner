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
# REGLAS DE ELEGIBILIDAD (MISMA LÓGICA DEL BOT)
# (basado en app/referrals.py que estás usando)
# ======================================================

REF_PREMIUM_TO_PREMIUM = 5
REF_PLUS_TO_PLUS = 5
REF_PLUS_TO_EXTEND_PREMIUM = 10


# ======================================================
# MENÚ AUXILIAR
# ======================================================

def back_to_menu():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ Volver al menú", callback_data="back_menu")]]
    )


def _format_whatsapp_contacts() -> str:
    """
    Devuelve texto con ambos WhatsApps (si están configurados).
    """
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
    # PANEL ADMIN (NO TOCAR)  ✅ (SOLO SE AÑADE 1 BOTÓN)
    # ==================================================
    if action == "admin_panel" and admin:
        await query.edit_message_text(
            "👑 PANEL ADMINISTRADOR\n\n"
            "Selecciona una acción:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Activar plan", callback_data="admin_activate_plan")],
                [InlineKeyboardButton("👥 Consultar referidos válidos", callback_data="admin_referrals")],
                [InlineKeyboardButton("⬅️ Volver", callback_data="back_menu")]
            ])
        )
        return

    # ==================================================
    # ADMIN → ACTIVAR PLAN (NO TOCAR)
    # ==================================================
    if action == "admin_activate_plan" and admin:
        context.user_data["awaiting_user_id"] = True
        await query.edit_message_text(
            "🆔 Envía el *User ID* del usuario a activar:",
            parse_mode="Markdown"
        )
        return

    # ==================================================
    # ADMIN → CONSULTAR REFERIDOS VÁLIDOS (NUEVO BOTÓN)
    # (SOLO LISTA ELEGIBLES, NO MODIFICA NADA)
    # ==================================================
    if action == "admin_referrals" and admin:
        # Solo mostrar referidores que YA son elegibles según la lógica real
        # (no mostramos a quien tenga 1-2 refs, sino a los que ya cumplen umbral)
        eligible = []

        cursor = users_col.find(
            {"$or": [
                {"ref_plus_valid": {"$gte": 1}},
                {"ref_premium_valid": {"$gte": 1}},
            ]},
            {"user_id": 1, "username": 1, "plan": 1, "ref_plus_valid": 1, "ref_premium_valid": 1},
        )

        for u in cursor:
            uid = u.get("user_id")
            plan = u.get("plan", PLAN_FREE)
            plus = int(u.get("ref_plus_valid", 0) or 0)
            premium = int(u.get("ref_premium_valid", 0) or 0)

            # Evaluar elegibilidad (misma lógica de app/referrals.py)
            # FREE:
            #   - premium>=5 => subir a PREMIUM
            #   - plus>=5 => subir a PLUS
            # PLUS:
            #   - premium>=5 => subir a PREMIUM
            #   - plus>=5 => extender PLUS
            # PREMIUM:
            #   - premium>=5 => extender PREMIUM
            #   - plus>=10 => extender PREMIUM
            is_eligible = False
            reward_label = None

            if plan == PLAN_FREE:
                if premium >= REF_PREMIUM_TO_PREMIUM:
                    is_eligible = True
                    reward_label = "✅ ELEGIBLE: SUBIR A PREMIUM"
                elif plus >= REF_PLUS_TO_PLUS:
                    is_eligible = True
                    reward_label = "✅ ELEGIBLE: SUBIR A PLUS"

            elif plan == PLAN_PLUS:
                if premium >= REF_PREMIUM_TO_PREMIUM:
                    is_eligible = True
                    reward_label = "✅ ELEGIBLE: SUBIR A PREMIUM"
                elif plus >= REF_PLUS_TO_PLUS:
                    is_eligible = True
                    reward_label = "✅ ELEGIBLE: EXTENDER PLUS"

            elif plan == PLAN_PREMIUM:
                if premium >= REF_PREMIUM_TO_PREMIUM:
                    is_eligible = True
                    reward_label = "✅ ELEGIBLE: EXTENDER PREMIUM (por PREMIUM)"
                elif plus >= REF_PLUS_TO_EXTEND_PREMIUM:
                    is_eligible = True
                    reward_label = "✅ ELEGIBLE: EXTENDER PREMIUM (por PLUS)"

            if not is_eligible:
                continue

            username = u.get("username") or ""
            uname_txt = f"@{username}" if username else "(sin username)"
            eligible.append(
                (reward_label, uid, uname_txt, plan.upper(), plus, premium)
            )

        if not eligible:
            await query.edit_message_text(
                "👥 REFERIDOS VÁLIDOS (ADMIN)\n\n"
                "📭 No hay referidores elegibles en este momento.\n\n"
                "Esto significa que nadie ha alcanzado todavía la cantidad mínima\n"
                "de referidos válidos requerida para activar/recompensar un plan.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Volver al panel", callback_data="admin_panel")],
                    [InlineKeyboardButton("⬅️ Volver al menú", callback_data="back_menu")],
                ]),
            )
            return

        # Ordenar: primero PREMIUM, luego PLUS, luego FREE (solo para lectura)
        plan_order = {PLAN_PREMIUM: 0, PLAN_PLUS: 1, PLAN_FREE: 2}
        eligible.sort(key=lambda x: (plan_order.get(x[3].lower(), 9), x[1]))

        lines = []
        for reward_label, uid, uname_txt, plan_txt, plus, premium in eligible[:40]:
            lines.append(
                f"{reward_label}\n"
                f"🆔 {uid} | {uname_txt}\n"
                f"Plan actual: {plan_txt}\n"
                f"PLUS válidos: {plus}\n"
                f"PREMIUM válidos: {premium}\n"
                "────────────"
            )

        text = (
            "👥 REFERIDOS VÁLIDOS (ADMIN)\n\n"
            "⚠️ Aquí solo aparecen los referidores que YA son elegibles\n"
            "según la configuración real del bot.\n\n"
            + "\n".join(lines)
        )

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Volver al panel", callback_data="admin_panel")],
                [InlineKeyboardButton("⬅️ Volver al menú", callback_data="back_menu")],
            ]),
        )
        return

    # ==================================================
    # VER SEÑALES ✅ CONSUMO REAL POR SIGNAL_ID
    # ==================================================
    if action == "view_signals":

        # ✅ Admin: acceso total automático
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

        # Reset contador diario si cambió el día (solo NO-admin)
        if not admin:
            if user.get("daily_signal_date") != today.isoformat():
                user["daily_signal_date"] = today.isoformat()
                user["daily_signal_count"] = 0
                user["last_signal_id"] = None

        plan = user.get("plan", PLAN_FREE)
        if admin:
            plan = PLAN_PREMIUM  # admin ve todo

        daily_limit = ADMIN_DAILY_LIMIT if admin else DAILY_LIMITS.get(plan, 0)

        if not admin and user.get("daily_signal_count", 0) >= daily_limit:
            await query.edit_message_text(
                "⚠️ Límite diario alcanzado.\n\n"
                f"Tu plan permite {daily_limit} señales por día.",
                reply_markup=back_to_menu(),
            )
            return

        base_signal = get_latest_base_signal_for_plan(plan)
        if not base_signal:
            await query.edit_message_text(
                "📭 No hay señales activas en este momento.",
                reply_markup=back_to_menu(),
            )
            return

        # ⛔ si expiró, no entregar y NO cuenta consumo
        if base_signal.get("valid_until") and base_signal["valid_until"] < datetime.utcnow():
            await query.edit_message_text(
                "⏳ La señal más reciente ha expirado.",
                reply_markup=back_to_menu(),
            )
            return

        # ✅ consumo real: SOLO si signal_id cambia
        signal_id = str(base_signal["_id"])
        last_signal_id = user.get("last_signal_id")
        is_new_signal = (signal_id != last_signal_id)

        user_signal = generate_user_signal(
            base_signal=base_signal,
            user_id=user_id,
        )

        # Incrementar contador SOLO si es nueva (y solo NO-admin)
        if not admin and is_new_signal:
            user["daily_signal_count"] = user.get("daily_signal_count", 0) + 1
            user["daily_signal_date"] = today.isoformat()
            user["last_signal_id"] = signal_id

        user = update_timestamp(user)
        users_col.update_one({"user_id": user_id}, {"$set": user})

        await query.edit_message_text(
            format_user_signal(user_signal),
            reply_markup=back_to_menu(),
        )

    # ==================================================
    # ESTADÍSTICAS (si tu repo las usa)
    # ==================================================
    elif action == "statistics":
        plan = user.get("plan", PLAN_FREE)
        if admin:
            plan = PLAN_PREMIUM

        if plan == PLAN_FREE:
            await query.edit_message_text(
                "📊 ESTADÍSTICAS\n\n"
                "Las estadísticas están disponibles a partir del plan PLUS.",
                reply_markup=back_to_menu(),
            )
            return

        daily = get_daily_stats()
        text = (
            "📊 ESTADÍSTICAS DEL SISTEMA\n\n"
            "🟡 HOY\n"
            f"Señales: {daily['total']}\n"
            f"Ganadas: {daily['won']}\n"
            f"Perdidas: {daily['lost']}\n"
            f"Expiradas: {daily['expired']}\n"
            f"Efectividad: {daily['winrate']}%\n"
        )

        if plan == PLAN_PREMIUM:
            weekly = get_weekly_stats()
            monthly = get_monthly_stats()
            text += (
                "\n🔵 ESTA SEMANA\n"
                f"Señales: {weekly['total']}\n"
                f"Ganadas: {weekly['won']}\n"
                f"Perdidas: {weekly['lost']}\n"
                f"Expiradas: {weekly['expired']}\n"
                f"Efectividad: {weekly['winrate']}%\n\n"
                "🔴 ESTE MES\n"
                f"Señales: {monthly['total']}\n"
                f"Ganadas: {monthly['won']}\n"
                f"Perdidas: {monthly['lost']}\n"
                f"Expiradas: {monthly['expired']}\n"
                f"Efectividad: {monthly['winrate']}%\n"
            )

        if admin:
            text = "👑 ADMIN – Acceso total\n\n" + text

        await query.edit_message_text(text, reply_markup=back_to_menu())

    # ==================================================
    # PLANES (NO TOCAR)
    # ==================================================
    elif action == "plans":
        contacts = _format_whatsapp_contacts()
        await query.edit_message_text(
            "💼 PLANES DISPONIBLES\n\n"
            "🟢 FREE\n"
            "- 7 días de prueba\n"
            "- 3 señales por día\n\n"
            "🟡 PLUS\n"
            "- 5 señales por día\n"
            "- Estadísticas diarias\n"
            "- 2 USDT / 30 días\n\n"
            "🔴 PREMIUM\n"
            "- 7 señales por día\n"
            "- Estadísticas diarias, semanales y mensuales\n"
            "- 4 USDT / 30 días\n\n"
            "Pagos en USDT (BSC).\n\n"
            "Para activar un plan, contacta a un administrador:\n"
            f"{contacts}",
            reply_markup=back_to_menu(),
        )

    # ==================================================
    # MI CUENTA (NO TOCAR)
    # ==================================================
    elif action == "my_account":
        plan = user.get("plan", PLAN_FREE).upper()
        used = user.get("daily_signal_count", 0)

        if admin:
            await query.edit_message_text(
                f"👑 MI CUENTA (ADMIN)\n\n"
                f"ID: {user_id}\n"
                "Acceso: TOTAL (equivalente a PREMIUM)\n"
                "Límites: Sin límites\n",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("👑 Panel Admin", callback_data="admin_panel")],
                    [InlineKeyboardButton("⬅️ Volver al menú", callback_data="back_menu")]
                ]),
            )
            return

        await query.edit_message_text(
            f"👤 MI CUENTA\n\n"
            f"ID: {user_id}\n"
            f"Plan: {plan}\n"
            f"Señales usadas hoy: {used}",
            reply_markup=back_to_menu(),
        )

    # ==================================================
    # REFERIDOS (NO TOCAR)
    # ==================================================
    elif action == "referrals":
        bot_username = context.bot.username
        ref_link = f"https://t.me/{bot_username}?start=ref_{user_id}"

        await query.edit_message_text(
            "👥 SISTEMA DE REFERIDOS\n\n"
            f"Tu enlace:\n{ref_link}\n\n"
            "Las recompensas se activan automáticamente.",
            reply_markup=back_to_menu(),
        )

    # ==================================================
    # SOPORTE (NO TOCAR)
    # ==================================================
    elif action == "support":
        contacts = _format_whatsapp_contacts()
        await query.edit_message_text(
            "📩 SOPORTE / ACTIVACIÓN\n\n"
            "Para pagos, activaciones y dudas, contacta a un administrador:\n\n"
            f"{contacts}",
            reply_markup=back_to_menu(),
        )

    # ==================================================
    # VOLVER AL MENÚ
    # ==================================================
    elif action == "back_menu":
        from app.bot import main_menu
        await query.edit_message_text("Menú principal", reply_markup=main_menu())


# ======================================================
# REGISTRO DE HANDLERS
# ======================================================

def get_handlers():
    return [CallbackQueryHandler(handle_menu)]
```0
