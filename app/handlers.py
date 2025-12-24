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
from app.menus import main_menu, back_to_menu, admin_menu
from app.referrals import get_user_referral_stats
from app.statistics import get_daily_stats, get_weekly_stats, get_monthly_stats  # ✅ NUEVO IMPORT

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
                reply_markup=admin_menu(),  # ✅ USA admin_menu() DE menus.py
            )
            return

        # ✅ NUEVO HANDLER PARA ESTADÍSTICAS DE ADMIN
        if action == "admin_stats" and admin:
            await handle_admin_stats(query)
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

        if action == "referrals":
            await handle_referrals(query, user)
            return

        # ================= SOPORTE =================

        if action == "support":
            await handle_support(query)
            return

        # ================= BACK =================

        if action == "back_menu":
            await query.edit_message_text(
                "Menú principal",
                reply_markup=main_menu(),
            )
            return

    except Exception as e:
        logger.error(f"Error en handle_menu para user {query.from_user.id}: {e}", exc_info=True)
        await query.edit_message_text(
            "❌ Ocurrió un error inesperado. Por favor, intenta nuevamente.",
            reply_markup=main_menu(),
        )


# ✅ NUEVA FUNCIÓN: HANDLER PARA ESTADÍSTICAS DE ADMIN
async def handle_admin_stats(query):
    """Muestra estadísticas del sistema a los administradores."""
    try:
        # Obtener estadísticas
        daily = get_daily_stats()
        weekly = get_weekly_stats()
        monthly = get_monthly_stats()
        
        # Formatear mensaje
        message = "📊 *ESTADÍSTICAS DEL SISTEMA*\n\n"
        
        # Estadísticas diarias
        message += "*HOY:*\n"
        message += f"• Total: {daily['total']} señales\n"
        message += f"• Ganadas: {daily['won']} | Perdidas: {daily['lost']} | Expiradas: {daily['expired']}\n"
        message += f"• Winrate: {daily['winrate']}%\n\n"
        
        # Estadísticas semanales
        message += "*ESTA SEMANA:*\n"
        message += f"• Total: {weekly['total']} señales\n"
        message += f"• Ganadas: {weekly['won']} | Perdidas: {weekly['lost']} | Expiradas: {weekly['expired']}\n"
        message += f"• Winrate: {weekly['winrate']}%\n\n"
        
        # Estadísticas mensuales
        message += "*ESTE MES:*\n"
        message += f"• Total: {monthly['total']} señales\n"
        message += f"• Ganadas: {monthly['won']} | Perdidas: {monthly['lost']} | Expiradas: {monthly['expired']}\n"
        message += f"• Winrate: {monthly['winrate']}%\n\n"
        
        # Información adicional
        message += f"*Nota:* El winrate se calcula solo sobre señales con resultado definitivo (ganadas + perdidas).\n"
        message += f"Señales expiradas no se incluyen en el cálculo del winrate."
        
        # Botón para volver al panel de admin
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Volver al panel", callback_data="admin_panel")],
            [InlineKeyboardButton("🏠 Menú principal", callback_data="back_menu")]
        ])
        
        await query.edit_message_text(
            text=message,
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
        
        logger.info(f"📊 Admin {query.from_user.id} consultó estadísticas del sistema")
        
    except Exception as e:
        logger.error(f"❌ Error en handle_admin_stats: {e}", exc_info=True)
        await query.edit_message_text(
            "❌ Error al obtener estadísticas. Intenta nuevamente.",
            reply_markup=admin_menu(),
        )


# ======================================================
# RESTANTES HANDLERS (sin cambios)
# ======================================================

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


async def handle_referrals(query, user):
    """Muestra la información de referidos del usuario con enlace completo."""
    try:
        user_id = user["user_id"]
        
        # Obtener estadísticas de referidos
        stats = get_user_referral_stats(user_id)
        
        if not stats:
            await query.edit_message_text(
                "❌ No se pudo cargar la información de referidos.",
                reply_markup=back_to_menu(),
            )
            return
        
        # Obtener el username del bot para construir el enlace completo
        bot_username = query.bot.username
        ref_code = stats['ref_code']
        
        # Construir enlace completo de referencia
        if bot_username:
            ref_link = f"https://t.me/{bot_username}?start={ref_code}"
            ref_link_display = f"https://t.me/{bot_username}?start={ref_code}"
        else:
            ref_link = f"https://t.me/share/url?url=mtfscanner&start={ref_code}"
            ref_link_display = f"Usa /start {ref_code} en el bot"
        
        # Construir mensaje
        message = "👥 SISTEMA DE REFERIDOS\n\n"
        
        # Enlace de referido COMPLETO
        message += "🔗 TU ENLACE DE REFERIDO COMPLETO:\n"
        message += f"{ref_link_display}\n\n"
        
        # Código de referido (para usar con /start)
        message += "📋 Código para usar con /start:\n"
        message += f"`{ref_code}`\n\n"
        
        # Estadísticas
        message += "📊 ESTADÍSTICAS:\n"
        message += f"• Total referidos: {stats['total_referred']}\n"
        message += f"• Referidos PLUS: {stats['plus_referred']}\n"
        message += f"• Referidos PREMIUM: {stats['premium_referred']}\n\n"
        
        # Contadores actuales (para recompensas)
        message += "🎯 CONTADORES ACTUALES:\n"
        message += f"• PLUS válidos: {stats['current_plus']}/5\n"
        message += f"• PREMIUM válidos: {stats['current_premium']}/5\n\n"
        
        # Recompensas pendientes
        if stats['pending_rewards']:
            message += "✨ RECOMPENSAS PENDIENTES:\n"
            for reward in stats['pending_rewards']:
                message += f"• {reward}\n"
            message += "\n"
        else:
            message += "📝 No tienes recompensas pendientes\n\n"
        
        # Cómo referir
        message += "📢 CÓMO REFERIR:\n"
        message += "1. Comparte tu ENLACE con amigos\n"
        message += "2. Ellos hacen clic en el enlace\n"
        message += "3. Se unen al bot automáticamente\n"
        message += "4. Cuando activen un plan, tú ganas\n\n"
        
        # Reglas
        message += "📌 REGLAS DEL SISTEMA:\n"
        message += "• FREE: 5 referidos PLUS = Plan PLUS gratis\n"
        message += "• FREE: 5 referidos PREMIUM = Plan PREMIUM gratis\n"
        message += "• PLUS: 5 referidos PLUS = Extender plan 30 días\n"
        message += "• PLUS: 5 referidos PREMIUM = Subir a PREMIUM\n"
        message += "• PREMIUM: 5 referidos PREMIUM = Extender plan 30 días\n"
        message += "• PREMIUM: 10 referidos PLUS = Extender plan 30 días\n"
        
        # Crear botones
        keyboard_buttons = []
        
        # Si hay enlace válido, añadir botón para compartir
        if bot_username:
            keyboard_buttons.append([InlineKeyboardButton("🔗 Compartir enlace", url=ref_link)])
        
        keyboard_buttons.append([InlineKeyboardButton("📋 Copiar código", callback_data="copy_ref_code")])
        keyboard_buttons.append([InlineKeyboardButton("⬅️ Volver al menú", callback_data="back_menu")])
        
        await query.edit_message_text(
            text=message,
            reply_markup=InlineKeyboardMarkup(keyboard_buttons),
            parse_mode="Markdown"
        )
        
    except Exception as e:
        logger.error(f"Error en handle_referrals para user {user['user_id']}: {e}", exc_info=True)
        await query.edit_message_text(
            "❌ Error al cargar información de referidos.",
            reply_markup=back_to_menu(),
        )


async def handle_copy_ref_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja la copia del código de referido."""
    query = update.callback_query
    await query.answer()
    
    try:
        user_id = query.from_user.id
        users_col = users_collection()
        user = users_col.find_one({"user_id": user_id})
        
        if not user:
            await query.edit_message_text(
                "❌ Usuario no encontrado.",
                reply_markup=main_menu(),
            )
            return
        
        ref_code = user.get("ref_code", f"ref_{user_id}")
        
        # Obtener el username del bot para construir el enlace completo
        bot_username = query.bot.username
        
        if bot_username:
            ref_link = f"https://t.me/{bot_username}?start={ref_code}"
            link_message = f"🔗 Enlace completo:\n{ref_link}\n\n"
        else:
            link_message = ""
        
        # Enviar mensaje con el código y enlace para copiar
        await query.edit_message_text(
            text=f"📋 INFORMACIÓN DE REFERIDO\n\n"
                 f"Código: `{ref_code}`\n\n"
                 f"{link_message}"
                 f"📝 Instrucciones:\n"
                 f"1. Comparte el enlace o código\n"
                 f"2. Para usar el código: /start {ref_code}\n"
                 f"3. Cada referido que active plan te da recompensa",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Volver a referidos", callback_data="referrals")],
                [InlineKeyboardButton("🏠 Menú principal", callback_data="back_menu")]
            ]),
            parse_mode="Markdown"
        )
        
    except Exception as e:
        logger.error(f"Error en handle_copy_ref_code: {e}")
        await query.edit_message_text(
            "❌ Error al copiar código.",
            reply_markup=main_menu(),
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
# REGISTRO DE HANDLERS (ACTUALIZADO CON ADMIN_STATS)
# ======================================================

def get_handlers():
    return [
        CallbackQueryHandler(
            handle_menu, 
            pattern="^(view_signals|plans|my_account|referrals|support|admin_panel|admin_activate_plan|admin_stats|back_menu)$"
        ),
        CallbackQueryHandler(handle_copy_ref_code, pattern="^copy_ref_code$"),
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_text),
    ]
