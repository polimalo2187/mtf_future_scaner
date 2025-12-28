import logging
import asyncio
from datetime import datetime, date
from functools import partial
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, filters, CommandHandler

from app.database import users_collection
from app.models import new_user, is_trial_active, is_plan_active, update_timestamp
from app.plans import PLAN_FREE, PLAN_PLUS, PLAN_PREMIUM, activate_plus, activate_premium
from app.signals import (
    get_latest_base_signal_for_plan,
    generate_user_signal,
    format_user_signal,
)
from app.config import is_admin, get_admin_whatsapps
from app.menus import main_menu, back_to_menu
from app.referrals import get_user_referral_stats

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
# HANDLER COMANDO /start (CRÍTICO PARA REFERIDOS)
# ======================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja el comando /start - PROCESA REFERIDOS"""
    try:
        user = update.effective_user
        users_col = users_collection()
        
        # Verificar si el usuario ya existe
        existing_user = users_col.find_one({"user_id": user.id})
        
        if existing_user:
            # Usuario existente, solo mostrar menú
            await update.message.reply_text(
                "¡Bienvenido de nuevo! 👋\n\nMenú principal:",
                reply_markup=main_menu(),
            )
            return
        
        # ============================================
        # NUEVO USUARIO - PROCESAR REFERIDO
        # ============================================
        referred_by = None
        ref_code_used = None
        
        # Verificar si hay argumentos en /start (ej: /start ref_ABC123)
        if context.args and len(context.args) > 0:
            ref_arg = context.args[0]
            
            # Verificar si es un código de referido
            if ref_arg.startswith("ref_"):
                ref_code_used = ref_arg  # Ej: "ref_123456"
                
                # Buscar al usuario que tiene ese código de referido
                referrer = users_col.find_one({"ref_code": ref_code_used})
                
                if referrer and referrer["user_id"] != user.id:  # Evitar auto-referido
                    referred_by = referrer["user_id"]
                    logger.info(f"📥 Nuevo usuario {user.id} referido por {referred_by} con código {ref_code_used}")
        
        # Crear nuevo usuario usando el modelo
        new_user_doc = new_user(
            user_id=user.id,
            username=user.username,
            referred_by=referred_by,
        )
        
        # Guardar usuario en la base de datos
        users_col.insert_one(new_user_doc)
        
        # Mensaje de bienvenida personalizado
        welcome_text = f"""
¡Bienvenido {user.first_name}! 👋

📊 **TU CUENTA:**
• ID: {user.id}
• Plan: FREE
• Trial: 7 días activo
• Señales diarias: 3

🎁 **BENEFICIOS:**
✓ 3 señales diarias gratis
✓ Acceso a señales en tiempo real
✓ Sin riesgo durante el trial

📱 **Cómo empezar:**
1. Ve a "🔍 Ver señales" para tu primera señal
2. Configura tu exchange preferido
3. ¡Comienza a operar!

{f'🎯 **REFERIDO DETECTADO:** Vinculado con {ref_code_used}' if ref_code_used else '💡 **TIP:** Comparte tu enlace de referido para ganar planes gratis!'}
"""
        
        await update.message.reply_text(
            welcome_text,
            reply_markup=main_menu(),
            parse_mode="Markdown",
        )
        
        # Notificar al referidor si existe
        if referred_by:
            await _notify_referrer(context.bot, referred_by, user)
            
    except Exception as e:
        logger.error(f"Error en start_command: {e}", exc_info=True)
        await update.message.reply_text(
            "❌ Error al procesar el registro. Intenta nuevamente.",
            reply_markup=main_menu(),
        )


async def _notify_referrer(bot, referrer_id: int, referred_user):
    """Notifica al referidor que alguien se registró con su código"""
    try:
        message = f"""
🎉 **NUEVO REFERIDO REGISTRADO**

👤 Usuario: {referred_user.first_name} (@{referred_user.username or 'Sin username'})
🆔 ID: {referred_user.id}

📊 Cuando active un plan PLUS/PREMIUM, ganarás créditos en tu sistema de referidos.

🔗 Tu enlace de referido sigue activo para más referidos.
"""
        await bot.send_message(
            chat_id=referrer_id,
            text=message,
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Error notificando al referidor {referrer_id}: {e}")


# ======================================================
# HANDLER MENÚ PRINCIPAL
# ======================================================

async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query is None:
        return

    await query.answer()

    try:
        user_id = query.from_user.id
        users_col = users_collection()
        user = users_col.find_one({"user_id": user_id})

        if not user:
            await query.edit_message_text(
                "Usuario no encontrado. Usa /start nuevamente.",
                reply_markup=main_menu(),
            )
            return

        action = query.data
        admin = is_admin(user_id)

        # ======================================================
        # ADMIN PANEL
        # ======================================================
        if action == "admin_panel" and admin:
            await query.edit_message_text(
                "👑 PANEL ADMINISTRADOR",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("➕ Activar plan", callback_data="admin_activate_plan")],
                    [InlineKeyboardButton("⬅️ Volver", callback_data="back_menu")],
                ])
            )
            return

        if action == "admin_activate_plan" and admin:
            context.user_data["awaiting_user_id"] = True
            await query.edit_message_text("🆔 Envía el User ID del usuario:")
            return

        if action == "view_signals":
            await handle_view_signals(query, user, admin, users_col)
            return

        if action == "plans":
            await handle_plans(query)
            return

        if action == "my_account":
            await handle_my_account(query, user, admin)
            return

        if action == "referrals":
            await handle_referrals(query, user)
            return

        if action == "support":
            await handle_support(query)
            return

        # ======================================================
        # REGISTRAR EXCHANGE
        # ======================================================
        if action == "register_exchange":
            context.user_data["awaiting_exchange"] = True
            await query.edit_message_text(
                "🌐 Envía el nombre de tu exchange (ej: Binance, CoinEx, KuCoin):"
            )
            return

        if action == "back_menu":
            await query.edit_message_text(
                "Menú principal",
                reply_mup=main_menu(),
            )
            return

        # ======================================================
        # ELEGIR PLAN ADMIN
        # ======================================================
        if action in ["choose_plus_plan", "choose_premium_plan"]:
            target_user_id = context.user_data.get("target_user_id")
            if target_user_id:
                # Ejecutar operación de BD en un hilo separado para no bloquear
                loop = asyncio.get_event_loop()
                
                if action == "choose_plus_plan":
                    success = await loop.run_in_executor(
                        None, 
                        partial(activate_plus, target_user_id)
                    )
                    plan_name = "PLUS"
                else:
                    success = await loop.run_in_executor(
                        None,
                        partial(activate_premium, target_user_id)
                    )
                    plan_name = "PREMIUM"

                if success:
                    await query.edit_message_text(f"✅ Plan {plan_name} activado correctamente.")
                else:
                    await query.edit_message_text(f"❌ No se pudo activar el plan {plan_name}.")

                # Limpiar estados
                context.user_data.pop("awaiting_plan_choice", None)
                context.user_data.pop("target_user_id", None)
            return

    except Exception as e:
        logger.error(f"Error en handle_menu: {e}", exc_info=True)
        await query.edit_message_text(
            "❌ Ocurrió un error inesperado.",
            reply_markup=main_menu(),
  )

  # ======================================================
# HANDLER REFERRALS
# ======================================================

async def handle_referrals(query, user):
    try:
        user_id = user["user_id"]
        stats = get_user_referral_stats(user_id)

        if not stats:
            await query.edit_message_text(
                "❌ No se pudo cargar la información de referidos.",
                reply_markup=back_to_menu(),
            )
            return

        ref_code = stats["ref_code"]
        ref_link = f"https://t.me/MTFSignsls_bot?start=ref_{ref_code}"

        message = "👥 SISTEMA DE REFERIDOS\n\n"
        message += f"🔗 Tu enlace de referido:\n{ref_link}\n\n"

        message += "📊 ESTADÍSTICAS:\n"
        message += f"• Total referidos: {stats['total_referred']}\n"
        message += f"• Referidos PLUS: {stats['plus_referred']}\n"
        message += f"• Referidos PREMIUM: {stats['premium_referred']}\n\n"

        message += "🎯 CONTADORES ACTUALES:\n"
        message += f"• PLUS válidos: {stats['current_plus']}/5 → Ganancia: {stats['current_plus']*2} USDT\n"
        message += f"• PREMIUM válidos: {stats['current_premium']}/5 → Ganancia: {stats['current_premium']*4} USDT\n\n"

        if stats["pending_rewards"]:
            message += "✨ RECOMPENSAS PENDIENTES:\n"
            for reward in stats["pending_rewards"]:
                message += f"• {reward}\n"
            message += "\n"
        else:
            message += "📝 No tienes recompensas pendientes\n\n"

        message += "📢 CÓMO REFERIR:\n1. Comparte tu enlace\n2. Ellos entran al bot\n3. Activan un plan\n\n"
        message += "📌 REGLAS:\n"
        message += "• FREE: 5 PLUS = Plan PLUS gratis\n"
        message += "• FREE: 5 PREMIUM = Plan PREMIUM gratis\n"
        message += "• PLUS: 5 PLUS = Extender plan\n"
        message += "• PLUS: 5 PREMIUM = Subir a PREMIUM\n"
        message += "• PREMIUM: 5 PREMIUM = Extender plan\n"
        message += "• PREMIUM: 10 PLUS = Extender plan\n"

        keyboard = [
            [InlineKeyboardButton("📋 Copiar enlace", callback_data="copy_ref_code")],
            [InlineKeyboardButton("⬅️ Volver al menú", callback_data="back_menu")]
        ]

        await query.edit_message_text(
            text=message,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    except Exception as e:
        logger.error(f"Error en handle_referrals: {e}", exc_info=True)
        await query.edit_message_text(
            "❌ Error al cargar información de referidos.",
            reply_markup=back_to_menu(),
        )


# ======================================================
# HANDLER COPIAR ENLACE DE REFERIDO
# ======================================================

async def handle_copy_ref_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        ref_link = f"https://t.me/MTFSignsls_bot?start=ref_{ref_code}"

        await query.edit_message_text(
            text=(
                f"📋 Tu enlace de referido es:\n\n{ref_link}\n\nCópialo y compártelo."
            ),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Volver a referidos", callback_data="referrals")],
                [InlineKeyboardButton("🏠 Menú principal", callback_data="back_menu")]
            ]),
        )

    except Exception as e:
        logger.error(f"Error en handle_copy_ref_code: {e}", exc_info=True)
        await query.edit_message_text(
            "❌ Error al copiar enlace.",
            reply_markup=main_menu(),
        )


# ======================================================
# HANDLER DE MENSAJES DE TEXTO COMBINADO
# ======================================================

async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja todos los mensajes de texto, decidiendo el flujo correcto"""
    
    # 1. Primero verificar si estamos esperando un User ID (admin)
    if context.user_data.get("awaiting_user_id"):
        await handle_admin_text_input(update, context)
        return
    
    # 2. Luego verificar si estamos esperando un exchange
    if context.user_data.get("awaiting_exchange"):
        await handle_exchange_text_input(update, context)
        return
    
    # 3. Si no es ninguno de los flujos, ignorar o mostrar ayuda
    # await update.message.reply_text("Envía /start para ver el menú principal.")


# ======================================================
# HANDLER REGISTRAR EXCHANGE (MENSAJE CONFIRMACIÓN)
# ======================================================

async def handle_exchange_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja el input de exchange"""
    try:
        context.user_data["awaiting_exchange"] = False
        exchange_name = update.message.text.strip()

        users_col = users_collection()
        user_id = update.effective_user.id
        
        # Ejecutar en hilo separado
        loop = asyncio.get_event_loop()
        user = await loop.run_in_executor(
            None,
            lambda: users_col.find_one({"user_id": user_id})
        )

        if not user:
            await update.message.reply_text("❌ Usuario no encontrado.")
            return

        # Actualizar exchange
        await loop.run_in_executor(
            None,
            lambda: users_col.update_one(
                {"user_id": user_id},
                {"$set": {"exchange": exchange_name}}
            )
        )

        await update.message.reply_text(
            f"✅ Exchange confirmado: {exchange_name}\nMenú principal:",
            reply_markup=main_menu(),
        )

    except Exception as e:
        logger.error(f"Error en handle_exchange_text: {e}", exc_info=True)
        await update.message.reply_text("❌ Error al registrar exchange.")
        context.user_data["awaiting_exchange"] = False


# ======================================================
# HANDLER VIEW SIGNALS
# ======================================================

async def handle_view_signals(query, user, admin, users_col):
    try:
        user_id = user["user_id"]

        plan = PLAN_PREMIUM if admin else user.get("plan", PLAN_FREE)

        if not admin and not (is_plan_active(user) or is_trial_active(user)):
            await query.edit_message_text(
                "⛔ Acceso expirado.",
                reply_markup=back_to_menu(),
            )
            return

        today = date.today().isoformat()
        if user.get("daily_signal_date") != today:
            users_col.update_one(
                {"user_id": user_id},
                {"$set": {"daily_signal_count": 0, "daily_signal_date": today, "last_signal_id": None}}
            )
            user["daily_signal_count"] = 0
            user["daily_signal_date"] = today
            user["last_signal_id"] = None

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
            users_col.update_one(
                {"user_id": user_id},
                {"$inc": {"daily_signal_count": 1}, "$set": {"last_signal_id": signal_id}}
            )
            user["daily_signal_count"] += 1
            user["last_signal_id"] = signal_id

        users_col.update_one(
            {"user_id": user_id},
            {"$set": update_timestamp(user)}
        )

        user_signal = generate_user_signal(base_signal, user_id)

        await query.edit_message_text(
            format_user_signal(user_signal),
            reply_markup=back_to_menu(),
        )

    except Exception as e:
        logger.error(f"Error en handle_view_signals: {e}", exc_info=True)
        await query.edit_message_text(
            "❌ Error al obtener señales.",
            reply_markup=back_to_menu(),
        )


# ======================================================
# HANDLER PLANS
# ======================================================

async def handle_plans(query):
    await query.edit_message_text(
        "💼 PLANES DISPONIBLES\n\n"
        "🟢 FREE – 3 señales/día\n"
        "🟡 PLUS – 5 señales/día\n"
        "🔴 PREMIUM – 7 señales/día\n\n"
        f"{format_whatsapp_contacts()}",
        reply_markup=back_to_menu(),
    )


# ======================================================
# HANDLER MY ACCOUNT
# ======================================================

async def handle_my_account(query, user, admin=False):
    plan = PLAN_PREMIUM if admin else user.get("plan", PLAN_FREE)
    signals_today = user.get("daily_signal_count", 0)

    message = (
        f"👤 MI CUENTA\n\n"
        f"ID: {user['user_id']}\n"
        f"Plan: {plan}\n"
        f"Señales hoy: {signals_today}\n"
    )

    if admin:
        message += "\n👑 PANEL ADMINISTRADOR\n"
        keyboard = [
            [InlineKeyboardButton("➕ Activar plan", callback_data="admin_activate_plan")],
            [InlineKeyboardButton("⬅️ Volver", callback_data="back_menu")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
    else:
        reply_markup = back_to_menu()

    await query.edit_message_text(
        text=message,
        reply_markup=reply_markup,
    )


# ======================================================
# HANDLER SUPPORT
# ======================================================

async def handle_support(query):
    await query.edit_message_text(
        f"📩 SOPORTE\n\n{format_whatsapp_contacts()}",
        reply_markup=back_to_menu(),
    )


# ======================================================
# HANDLER ADMIN TEXT INPUT (CORREGIDO)
# ======================================================

async def handle_admin_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja el input del admin para activar plan"""
    try:
        target_user_id_str = update.message.text.strip()
        logger.info(f"[ADMIN] Recibido User ID: {target_user_id_str}")
        
        # Validar que sea un número
        try:
            target_user_id = int(target_user_id_str)
        except ValueError:
            await update.message.reply_text("❌ ID inválido. Debe ser un número.")
            context.user_data["awaiting_user_id"] = False
            return

        # Verificar permisos de admin
        if not is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Permisos revocados.")
            context.user_data["awaiting_user_id"] = False
            return

        # Verificar si el usuario existe en la base de datos
        users_col = users_collection()
        loop = asyncio.get_event_loop()
        
        target_user = await loop.run_in_executor(
            None,
            lambda: users_col.find_one({"user_id": target_user_id})
        )
        
        if not target_user:
            await update.message.reply_text("❌ Usuario no encontrado en la base de datos.")
            context.user_data["awaiting_user_id"] = False
            return

        # Guardar ID y mostrar botones de plan
        context.user_data["awaiting_user_id"] = False
        context.user_data["awaiting_plan_choice"] = True
        context.user_data["target_user_id"] = target_user_id

        keyboard = [
            [InlineKeyboardButton("🟡 Activar PLAN PLUS", callback_data="choose_plus_plan")],
            [InlineKeyboardButton("🔴 Activar PLAN PREMIUM", callback_data="choose_premium_plan")],
            [InlineKeyboardButton("❌ Cancelar", callback_data="back_menu")]
        ]

        await update.message.reply_text(
            f"✅ Usuario encontrado: {target_user_id}\nSeleccione el plan a activar:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    except Exception as e:
        logger.error(f"[ADMIN] Error en handle_admin_text: {e}", exc_info=True)
        await update.message.reply_text("❌ Error procesando la solicitud.")
        context.user_data["awaiting_user_id"] = False


# ======================================================
# REGISTRO DE HANDLERS (CORREGIDO CON /start)
# ======================================================

def get_handlers():
    return [
        # Comando /start (IMPORTANTE PARA REFERIDOS)
        CommandHandler("start", start_command),
        
        # Handlers de callback queries
        CallbackQueryHandler(
            handle_menu,
            pattern="^(view_signals|plans|my_account|referrals|support|admin_panel|admin_activate_plan|register_exchange|back_menu|choose_plus_plan|choose_premium_plan)$"
        ),
        CallbackQueryHandler(handle_copy_ref_code, pattern="^copy_ref_code$"),
        
        # UN SOLO MessageHandler que maneja todos los flujos de texto
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages),
                                      ]
