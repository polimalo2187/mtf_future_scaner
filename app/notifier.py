from typing import List
from telegram import Bot
from datetime import datetime
import asyncio
import os

from app.database import users_collection
from app.plans import PLAN_FREE, PLAN_PLUS, PLAN_PREMIUM
from app.models import is_trial_active, is_plan_active


# ======================================================
# CONFIGURACIÓN
# ======================================================

ALERT_AUTO_DELETE_SECONDS = 8

ADMIN_USER_IDS = [
    int(os.getenv("ADMIN_USER_ID_1", "0")),
    int(os.getenv("ADMIN_USER_ID_2", "0")),
]


# ======================================================
# UTILIDADES
# ======================================================

def _eligible_users_for_alert(signal_visibility: str) -> List[int]:
    """
    Retorna SOLO los usuarios cuyo plan coincide EXACTAMENTE
    con la visibilidad de la señal.
    """
    users_col = users_collection()
    eligible_users = []

    for user in users_col.find({}):
        user_id = user.get("user_id")
        plan = user.get("plan", PLAN_FREE)
        has_access = is_plan_active(user) or is_trial_active(user)

        if not has_access:
            continue

        # Coincidencia exacta de plan
        if plan == signal_visibility:
            eligible_users.append(user_id)

    return eligible_users


def _admin_users() -> List[int]:
    return [uid for uid in ADMIN_USER_IDS if uid > 0]


async def _auto_delete(bot: Bot, chat_id: int, message_id: int):
    await asyncio.sleep(ALERT_AUTO_DELETE_SECONDS)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


# ======================================================
# ALERTA PUSH USUARIOS
# ======================================================

async def notify_new_signal_alert(
    bot: Bot,
    signal_visibility: str,
):
    """
    Envía alerta PUSH SOLO a usuarios del plan correspondiente.
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

            asyncio.create_task(
                _auto_delete(bot, user_id, message.message_id)
            )

        except Exception:
            continue

    # ==================================================
    # ALERTA ADMINISTRADORES (INFORMATIVA)
    # ==================================================

    admin_text = (
        "👑 *ALERTA ADMIN*\n\n"
        f"Tipo de señal: *{signal_visibility}*\n\n"
        "Se ha generado una nueva señal en el sistema."
    )

    for admin_id in _admin_users():
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=admin_text,
                parse_mode="Markdown",
            )
        except Exception:
            continue


# ======================================================
# NOTIFICACIONES DE PLANES (SIN CAMBIOS)
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
