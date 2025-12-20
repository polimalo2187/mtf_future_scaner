# app/notifier.py

from typing import List
from telegram import Bot
from datetime import datetime

from app.database import users_collection
from app.plans import has_access, PLAN_FREE, PLAN_PLUS, PLAN_PREMIUM
from app.signals import format_signal_for_telegram


# =========================
# UTILIDADES
# =========================

def _eligible_users_for_notification(signal_visibility: str) -> List[int]:
    """
    Retorna la lista de user_id que deben recibir notificación
    según la visibilidad de la señal.
    """
    users_col = users_collection()
    eligible = []

    for user in users_col.find({}):
        plan = user.get("plan", PLAN_FREE)

        # FREE → solo free
        if signal_visibility == PLAN_FREE:
            if plan == PLAN_FREE and has_access(user):
                eligible.append(user["user_id"])

        # PLUS → free + plus
        elif signal_visibility == PLAN_PLUS:
            if plan in (PLAN_FREE, PLAN_PLUS) and has_access(user):
                eligible.append(user["user_id"])

        # PREMIUM → todos con acceso
        elif signal_visibility == PLAN_PREMIUM:
            if has_access(user):
                eligible.append(user["user_id"])

    return eligible


# =========================
# NOTIFICACIONES
# =========================

async def notify_new_signal(
    bot: Bot,
    signal: dict,
):
    """
    Envía notificación de nueva señal a los usuarios elegibles.
    """
    signal_text = format_signal_for_telegram(signal)
    visibility = signal.get("visibility", PLAN_PREMIUM)

    user_ids = _eligible_users_for_notification(visibility)

    for user_id in user_ids:
        try:
            await bot.send_message(
                chat_id=user_id,
                text=signal_text,
            )
        except Exception:
            # Usuario bloqueó el bot o chat inválido
            continue


async def notify_plan_activation(
    bot: Bot,
    user_id: int,
    plan: str,
    expires_at: datetime,
):
    """
    Notifica activación o extensión de plan.
    """
    await bot.send_message(
        chat_id=user_id,
        text=(
            f"Plan {plan.upper()} activado.\n\n"
            f"Vence el: {expires_at.strftime('%d/%m/%Y')}\n\n"
            "Gracias por usar MTF Futures Scanner."
        ),
    )


async def notify_plan_expired(
    bot: Bot,
    user_id: int,
):
    """
    Notifica vencimiento de plan.
    """
    await bot.send_message(
        chat_id=user_id,
        text=(
            "Tu plan ha expirado.\n\n"
            "Para continuar recibiendo señales, revisa los planes disponibles."
        ),
)
