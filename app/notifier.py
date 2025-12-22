# app/notifier.py

from typing import List
from telegram import Bot
from datetime import datetime
import asyncio

from app.database import users_collection
from app.plans import PLAN_FREE
from app.models import is_trial_active, is_plan_active


# ======================================================
# CONFIGURACIÓN
# ======================================================

ALERT_AUTO_DELETE_SECONDS = 8


# ======================================================
# UTILIDADES
# ======================================================

def _eligible_users_for_alert(signal_visibility: str) -> List[int]:
    """
    Retorna SOLO los usuarios cuyo plan coincide EXACTAMENTE
    con la visibilidad de la señal.
    """
    users_col = users_collection()
    eligible_users: List[int] = []

    for user in users_col.find({}):
        user_id = user.get("user_id")
        plan = user.get("plan", PLAN_FREE)
        has_access = is_plan_active(user) or is_trial_active(user)

        if not has_access:
            continue

        if plan == signal_visibility:
            eligible_users.append(user_id)

    return eligible_users


async def _auto_delete(bot: Bot, chat_id: int, message_id: int):
    await asyncio.sleep(ALERT_AUTO_DELETE_SECONDS)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


# ======================================================
# ALERTA PUSH SOLO USUARIOS (LIMPIO)
# ======================================================

async def notify_new_signal_alert(
    bot: Bot,
    signal_visibility: str,
    **kwargs,  # ← FIX CRÍTICO: absorbe symbol y cualquier extra
):
    """
    - Push SOLO a usuarios del plan correspondiente
    - Mensaje corto
    - Auto-borrado
    - Chat siempre limpio
    """

    user_ids = _eligible_users_for_alert(signal_visibility)

    alert_text = (
        "📢 *NUEVA SEÑAL DISPONIBLE*\n\n"
        "👉 Entra al bot y toca *Ver señales*.\n\n"
        "⏳ Tiempo limitado."
    )

    for user_id in user_ids:
        try:
            msg = await bot.send_message(
                chat_id=user_id,
                text=alert_text,
                parse_mode="Markdown",
            )
            asyncio.create_task(_auto_delete(bot, user_id, msg.message_id))
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
            f"Vence el: {expires_at.strftime('%d/%m/%Y')}"
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
            "Contacta a un administrador para renovarlo."
        ),
  )
