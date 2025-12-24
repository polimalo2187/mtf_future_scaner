# app/handlers.py

import logging
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
from app.menus import main_menu, back_to_menu  # ✅ CORREGIDO: importar desde menus

# Configurar logging
logger = logging.getLogger(__name__)

DAILY_LIMITS = {
    PLAN_FREE: 3,
    PLAN_PLUS: 5,
    PLAN_PREMIUM: 7,
}


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
    if not query:
        return
    
    await query.answer()

    try:
        user_id = query.from_user.id
        users_col = users_collection()
        user = users_col.find_one({"user_id": user_id})

        if not user:
            logger.warning(f"Usuario no encontrado en handle_menu: {user_id}")
            await query.edit_message_text(
                "Usuario no encontrado. Usa /start nuevamente.",
                reply_markup=main_menu(),
            )
            return

        action = query.data
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
            await handle_view_signals(query, user, admin, users_col)
            return

        # ================= PLANES =================

        if action == "plans":
            await handle_plans(query)
            return

        # ================= MI CUENTA =================

        if action == "my_account":
            await handle_my_account(query, user)
            return

        # ================= REFERRALS =================
        # TODO: Implementar esta función si no existe
        if action == "referrals":
            await query.edit_message_text(
                "👥 Funcionalidad de referidos en desarrollo.",
                reply_markup=back_to_menu(),
            )
            return

        # ================= SOPORTE =================

        if action == "support":
            await handle_support(query)
            return

        # ================= BACK =================

        if action == "back_menu":
            await query.edit_message_text(
                "Menú principal",
                reply_markup=main_menu(),  # ✅ Usa la función importada
            )
            return

    except Exception as e:
        logger.error(f"Error en handle_menu para user {query.from_user.id}: {e}", exc_info=True)
        await query.edit_message_text(
            "❌ Ocurrió un error inesperado. Por favor, intenta nuevamente.",
            reply_markup=main_menu(),
        )


async def handle_view_signals(query, user, admin, users_col):
    """Maneja la lógica de visualización de señales."""
    try:
        user_id = user["user_id"]
        
        # Verificar acceso
        if not admin and not (is_plan_active(user) or is_trial_active(user)):
            await query.edit_message_text(
                "⛔ Acceso expirado.",
                reply_markup=back_to_menu(),
            )
            return

        # Reset diario en la base de datos si es un nuevo día
        today = date.today().isoformat()
        if user.get("daily_signal_date") != today:
            users_col.update_one(
                {"user_id": user_id},
                {
                    "$set": {
                        "daily_signal_count": 0,
                        "daily_signal_date": today,
                        "last_signal_id": None
                    }
                }
            )
            # Actualizar objeto local
            user["daily_signal_count"] = 0
            user["daily_signal_date"] = today
            user["last_signal_id"] = None

        plan = user.get("plan", PLAN_FREE)

        # Verificar límite diario (excepto para admins)
        if not admin:
            limit = DAILY_LIMITS.get(plan, 0)
            if user.get("daily_signal_count", 0) >= limit:
                await query.edit_message_text(
                    f"⚠️ Límite diario alcanzado.\n\n"
                    f"Has usado {user.get('daily_signal_count', 0)} de {limit} señales hoy.\n"
                    f"El contador se reiniciará a las 00:00 UTC.",
                    reply_markup=back_to_menu(),
                )
                return

        # Obtener señales
        base_signals = get_latest_base_signal_for_plan(user_id, plan)
        if not base_signals:
            await query.edit_message_text(
                "📭 No hay señales disponibles.",
                reply_markup=back_to_menu(),
            )
            return

        base_signal = base_signals[0]
        signal_id = str(base_signal["_id"])

        # Verificar si es una señal nueva (incrementar contador si es nueva)
        if signal_id != user.get("last_signal_id"):
            # Incrementar contador en la base de datos
            users_col.update_one(
                {"user_id": user_id},
                {
                    "$inc": {"daily_signal_count": 1},
                    "$set": {
                        "last_signal_id": signal_id,
                        "last_signal_at": datetime.utcnow()
                    }
                }
            )
            # Actualizar objeto local
            user["daily_signal_count"] = user.get("daily_signal_count", 0) + 1
            user["last_signal_id"] = signal_id

        # Actualizar timestamp de actividad del usuario
        users_col.update_one(
            {"user_id": user_id},
            {"$set": update_timestamp(user)}
        )

        # Generar señal personalizada
        user_signal = generate_user_signal(base_signal, user_id)

        # Enviar señal al usuario
        await query.edit_message_text(
            format_user_signal(user_signal),
            reply_markup=back_to_menu(),
        )
        logger.info(f"✅ Señal entregada a usuario {user_id} (plan: {plan})")

    except Exception as e:
        logger.error(f"Error en handle_view_signals para user {user['user_id']}: {e}", exc_info=True)
        await query.edit_message_text(
            "❌ Error al obtener señales. Por favor, intenta nuevamente.",
            reply_markup=back_to_menu(),
        )


async def handle_plans(query):
    """Maneja la visualización de planes."""
    try:
        await query.edit_message_text(
            "💼 PLANES DISPONIBLES\n\n"
            "🟢 FREE – 3 señales/día\n"
            "🟡 PLUS – 5 señales/día\n"
            "🔴 PREMIUM – 7 señales/día\n\n"
            "Contacta a un administrador:\n"
            f"{format_whatsapp_contacts()}",
            reply_markup=back_to_menu(),
        )
    except Exception as e:
        logger.error(f"Error en handle_plans: {e}")
        await query.edit_message_text(
            "❌ Error al mostrar planes.",
            reply_markup=main_menu(),
        )


async def handle_my_account(query, user):
    """Muestra la información de la cuenta del usuario."""
    try:
        user_id = user["user_id"]
        plan = user.get("plan", PLAN_FREE).upper()
        signals_today = user.get("daily_signal_count", 0)
        
        # Determinar estado
        status = "🟢 ACTIVO"
        if not (is_plan_active(user) or is_trial_active(user)):
            status = "🔴 INACTIVO"
        elif is_trial_active(user):
            status = "🟡 PRUEBA"
        
        await query.edit_message_text(
            f"👤 MI CUENTA\n\n"
            f"ID: {user_id}\n"
            f"Plan: {plan}\n"
            f"Estado: {status}\n"
            f"Señales hoy: {signals_today}",
            reply_markup=back_to_menu(),
        )
    except Exception as e:
        logger.error(f"Error en handle_my_account para user {user['user_id']}: {e}")
        await query.edit_message_text(
            "❌ Error al mostrar información de cuenta.",
            reply_markup=main_menu(),
        )


async def handle_support(query):
    """Muestra información de soporte."""
    try:
        await query.edit_message_text(
            f"📩 SOPORTE\n\n{format_whatsapp_contacts()}",
            reply_markup=back_to_menu(),
        )
    except Exception as e:
        logger.error(f"Error en handle_support: {e}")
        await query.edit_message_text(
            "❌ Error al mostrar soporte.",
            reply_markup=main_menu(),
        )


# ======================================================
# HANDLER ADMIN (ACTIVAR PLAN)
# ======================================================

async def handle_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja la entrada de texto para activar planes (admin)."""
    try:
        if not context.user_data.get("awaiting_user_id"):
            return

        # Limpiar el estado después de procesar
        context.user_data["awaiting_user_id"] = False

        try:
            target_user_id = int(update.message.text.strip())
        except ValueError:
            await update.message.reply_text("❌ ID inválido. Debe ser un número.")
            return

        # Verificar que el admin aún tiene permisos
        if not is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Permisos de administrador revocados.")
            return

        success = activate_premium(target_user_id)

        if success:
            await update.message.reply_text(
                f"✅ Plan PREMIUM activado para el usuario {target_user_id}."
            )
            logger.info(f"Admin {update.effective_user.id} activó plan PREMIUM para {target_user_id}")
        else:
            await update.message.reply_text(
                f"❌ No se pudo activar el plan para el usuario {target_user_id}."
            )
            logger.warning(f"Admin {update.effective_user.id} falló al activar plan para {target_user_id}")

    except Exception as e:
        logger.error(f"Error en handle_admin_text: {e}", exc_info=True)
        await update.message.reply_text("❌ Error interno al procesar la solicitud.")


# ======================================================
# REGISTRO DE HANDLERS
# ======================================================

def get_handlers():
    return [
        CallbackQueryHandler(handle_menu),
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_text),
      ]
