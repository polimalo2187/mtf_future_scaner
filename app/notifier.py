# app/notifier.py

import asyncio
import logging
from typing import List
from telegram import Bot
from datetime import datetime

from app.database import users_collection
from app.plans import PLAN_FREE
from app.config import is_admin  # ✅ CORREGIDO: importar desde config, no desde models
from app.models import is_trial_active, is_plan_active

# Configurar logging
logger = logging.getLogger(__name__)

# ======================================================
# CONFIGURACIÓN
# ======================================================

ALERT_AUTO_DELETE_SECONDS = 8


# ======================================================
# UTILIDADES
# ======================================================

def _eligible_users_for_alert(signal_visibility: str, required_leverage: str = None) -> List[int]:
    """
    Retorna los usuarios elegibles para recibir la alerta.
    Los administradores siempre reciben todas las señales.
    Solo se incluyen usuarios cuyo apalancamiento sea compatible (si se indica).
    """
    users_col = users_collection()
    eligible_users: List[int] = []

    query = {}
    users = users_col.find(query, {"user_id": 1, "plan": 1, "trial_end": 1, "plan_end": 1, "leverage": 1})

    for user in users:
        user_id = user.get("user_id")
        plan = user.get("plan", PLAN_FREE)
        has_access = is_plan_active(user) or is_trial_active(user)
        admin = is_admin(user_id)

        if not has_access and not admin:
            continue

        # Verificar compatibilidad con la señal
        user_leverage = user.get("leverage")  # puede ser "conservador", "moderado" o "agresivo"
        if required_leverage and user_leverage and required_leverage != user_leverage and not admin:
            continue

        if admin or plan == signal_visibility:
            eligible_users.append(user_id)

    return eligible_users


async def _auto_delete(bot: Bot, chat_id: int, message_id: int):
    await asyncio.sleep(ALERT_AUTO_DELETE_SECONDS)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        logger.debug(f"🗑️ Mensaje auto-eliminado: chat_id={chat_id}, message_id={message_id}")
    except Exception as e:
        logger.debug(f"⚠️ No se pudo auto-eliminar mensaje: {e}")


# ======================================================
# ALERTA PUSH SOLO USUARIOS (LIMPIO)
# ======================================================

async def notify_new_signal_alert(
    bot: Bot,
    signal_visibility: str,
    required_leverage: str = None,
    **kwargs,  # ← absorbe symbol y cualquier extra
):
    """
    - Push a usuarios elegibles según su plan y apalancamiento
    - Los admins reciben todas las señales
    - Mensaje corto y auto-borrado
    """

    user_ids = _eligible_users_for_alert(signal_visibility, required_leverage)

    if not user_ids:
        logger.info("📭 No hay usuarios elegibles para la alerta")
        return

    alert_text_template = (
        "📢 *NUEVA SEÑAL DISPONIBLE*\n\n"
        "👉 Entra al bot y toca *Ver señales*.\n"
        "🌐 Exchange registrado: {exchange}\n\n"
        "⏳ Tiempo limitado."
    )

    sent_count = 0
    users_col = users_collection()
    for user_id in user_ids:
        try:
            user = users_col.find_one({"user_id": user_id})
            exchange = user.get("exchange", "No registrado")

            alert_text = alert_text_template.format(exchange=exchange)

            msg = await bot.send_message(
                chat_id=user_id,
                text=alert_text,
                parse_mode="Markdown",
            )
            asyncio.create_task(_auto_delete(bot, user_id, msg.message_id))
            sent_count += 1
        except Exception as e:
            logger.warning(f"⚠️ Error enviando alerta a {user_id}: {e}")
            continue

    logger.info(f"📨 Alertas enviadas: {sent_count}/{len(user_ids)} usuarios")


# ======================================================
# NOTIFICACIONES DE PLANES (SIN CAMBIOS)
# ======================================================

async def notify_plan_activation(
    bot: Bot,
    user_id: int,
    plan: str,
    expires_at: datetime,
):
    try:
        await bot.send_message(
            chat_id=user_id,
            text=(
                f"✅ Plan {plan.upper()} activado.\n\n"
                f"Vence el: {expires_at.strftime('%d/%m/%Y')}"
            ),
        )
        logger.info(f"✅ Notificado usuario {user_id} sobre activación de plan {plan}")
    except Exception as e:
        logger.error(f"❌ Error notificando activación a {user_id}: {e}")


async def notify_plan_expired(
    bot: Bot,
    user_id: int,
):
    try:
        await bot.send_message(
            chat_id=user_id,
            text=(
                "⚠️ Tu plan ha expirado.\n\n"
                "Contacta a un administrador para renovarlo."
            ),
        )
        logger.info(f"⚠️ Notificado usuario {user_id} sobre plan expirado")
    except Exception as e:
        logger.error(f"❌ Error notificando expiración a {user_id}: {e}")
