# app/notifier.py

from typing import List
from telegram import Bot
from datetime import datetime

from app.database import users_collection
from app.plans import has_access, PLAN_FREE, PLAN_PLUS, PLAN_PREMIUM


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

        # FREE → solo señales free
        if signal_visibility == PLAN_FREE:
            if plan == PLAN_FREE and has_access(user):
                eligible_users.append(user["user_id"])

        # PLUS → free + plus
        elif signal_visibility == PLAN_PLUS:
            if plan in (PLAN_FREE, PLAN_PLUS) and has_access(user):
                eligible_users.append(user["user_id"])

        # PREMIUM → todos con acceso
        elif signal_visibility == PLAN_PREMIUM:
            if has_access(user):
                eligible_users.append(user["user_id"])

    return eligible_users


# ======================================================
# ALERTA DE NUEVA SEÑAL (SIN DATOS COPIABLES)
# ======================================================

async def notify_new_signal_alert(
    bot: Bot,
    signal_visibility: str,
):
    """
    Envía una ALERTA de nueva señal.
    NO incluye Entry, TP ni SL.
    """
    user_ids = _eligible_users_for_alert(signal_visibility)

    alert_text = (
        "📢 NUEVA SEÑAL DISPONIBLE\n\n"
        "Se ha detectado una nueva oportunidad de trading.\n\n"
        "👉 Entra al bot y toca *Ver señales* para desbloquearla.\n\n"
        "⏳ Las señales tienen tiempo limitado."
    )

    for user_id in user_ids:
        try:
            await bot.send_message(
                chat_id=user_id,
                text=alert_text,
                parse_mode="Markdown",
            )
        except Exception:
            # Usuario bloqueó el bot o chat inválido
            continue


# ======================================================
# NOTIFICACIONES DE PLANES (SE MANTIENEN)
# ======================================================

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
            f"✅ Plan {plan.upper()} activado.\n\n"
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
            "⚠️ Tu plan ha expirado.\n\n"
            "Para continuar recibiendo señales, revisa los planes disponibles."
        ),
  )
