from typing import List
from telegram import Bot
from datetime import datetime
import asyncio

from app.database import users_collection
from app.plans import PLAN_FREE, PLAN_PLUS, PLAN_PREMIUM
from app.models import is_trial_active, is_plan_active

# ======================================================
# CONFIGURACIÓN DE ALERTAS PUSH
# ======================================================

ALERT_AUTO_DELETE_SECONDS = 8


# ======================================================
# UTILIDADES INTERNAS
# ======================================================

def _eligible_users_for_alert(signal_visibility: str) -> List[int]:
    """
    Retorna los user_id que deben recibir ALERTA
    (no señal completa), según visibilidad.
    """
    users_col = users_collection()
    eligible_users = []

    for user in users_col.find({}):
        plan = user.get("plan", PLAN_FREE)
        has_access = is_plan_active(user) or is_trial_active(user)

        if not has_access:
            continue

        if signal_visibility == PLAN_FREE and plan == PLAN_FREE:
            eligible_users.append(user["user_id"])

        elif signal_visibility == PLAN_PLUS and plan in (PLAN_FREE, PLAN_PLUS):
            eligible_users.append(user["user_id"])

        elif signal_visibility == PLAN_PREMIUM:
            eligible_users.append(user["user_id"])

    return eligible_users


# ======================================================
# AUTO DELETE ASYNC (NO BLOQUEANTE)
# ======================================================

async def _auto_delete(bot: Bot, chat_id: int, message_id: int):
    await asyncio.sleep(ALERT_AUTO_DELETE_SECONDS)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


# ======================================================
# ALERTA DE NUEVA SEÑAL (PUSH LIMPIO)
# ======================================================

async def notify_new_signal_alert(
    bot: Bot,
    signal_visibility: str,
):
    """
    Envía una ALERTA PUSH de nueva señal.
    El mensaje se borra automáticamente sin bloquear el sistema.
    """
    user_ids = _eligible_users_for_alert(signal_visibility)

    alert_text = (
        "📢 *NUEVA SEÑAL DISPONIBLE*\n\n"
        "Se ha detectado una nueva oportunidad de trading.\n\n"
        "👉 Abre el bot y toca *Ver señales* para desbloquearla.\n\n"
        "⏳ Señal por tiempo limitado."
    )

    for user_id in user_ids:
        try:
            message = await bot.send_message(
                chat_id=user_id,
                text=alert_text,
                parse_mode="Markdown",
            )

            # Auto-delete en background (NO bloquea)
            asyncio.create_task(
                _auto_delete(bot, user_id, message.message_id)
            )

        except Exception:
            continue


# ======================================================
# NOTIFICACIONES DE PLANES
# ======================================================

async def notify_plan_activation(
    bot: Bot,
    user_id: int,
    plan: str,
    expires_at: datetime,
):
    await bot.send_message(
        chat_id=user_id,
        text=(
            f"✅ Plan {plan.upper()} activado.\n\n"
            f"Vence el: {expires_at.strftime('%d/%m/%Y')}\n\n"
            "Gracias por usar MTF Futures Scanner."
        ),
    )


async def notify_plan_expired(
    bot: Bot,
    user_id: int,
):
    await bot.send_message(
        chat_id=user_id,
        text=(
            "⚠️ Tu plan ha expirado.\n\n"
            "Para continuar recibiendo señales, revisa los planes disponibles."
        ),
)
