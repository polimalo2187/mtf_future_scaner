# app/referrals.py

from typing import Optional
from datetime import datetime

from app.database import users_collection, referrals_collection
from app.plans import (
    activate_plus,
    activate_premium,
    extend_current_plan,
    PLAN_FREE,
    PLAN_PLUS,
    PLAN_PREMIUM,
)
from app.models import update_timestamp


# =========================
# REGISTRO DE REFERIDO VÁLIDO
# =========================

def register_valid_referral(
    referred_user_id: int,
    activated_plan: str,
) -> None:
    """
    Registra un referido válido cuando un usuario ACTIVA un plan
    (plus o premium). Cuenta SOLO una vez por usuario referido.
    """
    users_col = users_collection()
    refs_col = referrals_collection()

    referred_user = users_col.find_one({"user_id": referred_user_id})
    if not referred_user:
        return

    referrer_id = referred_user.get("referred_by")
    if not referrer_id:
        return

    # Evitar doble conteo del mismo referido
    existing = refs_col.find_one({"referred_id": referred_user_id})
    if existing:
        return

    # Registrar histórico
    refs_col.insert_one({
        "referrer_id": referrer_id,
        "referred_id": referred_user_id,
        "activated_plan": activated_plan,
        "activated_at": datetime.utcnow(),
    })

    # Incrementar contador correspondiente
    referrer = users_col.find_one({"user_id": referrer_id})
    if not referrer:
        return

    if activated_plan == PLAN_PLUS:
        referrer["ref_plus_valid"] = referrer.get("ref_plus_valid", 0) + 1
    elif activated_plan == PLAN_PREMIUM:
        referrer["ref_premium_valid"] = referrer.get("ref_premium_valid", 0) + 1

    referrer = update_timestamp(referrer)
    users_col.update_one(
        {"user_id": referrer_id},
        {"$set": referrer},
    )

    # Evaluar recompensas automáticas
    check_ref_rewards(referrer_id)


# =========================
# EVALUACIÓN DE RECOMPENSAS
# =========================

def check_ref_rewards(referrer_id: int) -> None:
    """
    Evalúa y aplica recompensas por referidos según el plan actual
    del referidor. Consumo de contadores por ciclo (mes a mes).
    """
    users_col = users_collection()
    referrer = users_col.find_one({"user_id": referrer_id})
    if not referrer:
        return

    plan = referrer.get("plan", PLAN_FREE)
    plus_count = referrer.get("ref_plus_valid", 0)
    premium_count = referrer.get("ref_premium_valid", 0)

    # =========================
    # USUARIO FREE
    # =========================
    if plan == PLAN_FREE:
        if premium_count >= 5:
            # Subir a PREMIUM
            activate_premium(referrer_id)
            referrer["ref_premium_valid"] -= 5

        elif plus_count >= 5:
            # Subir a PLUS
            activate_plus(referrer_id)
            referrer["ref_plus_valid"] -= 5

    # =========================
    # USUARIO PLUS
    # =========================
    elif plan == PLAN_PLUS:
        if premium_count >= 5:
            # Subir a PREMIUM
            activate_premium(referrer_id)
            referrer["ref_premium_valid"] -= 5

        elif plus_count >= 5:
            # Extender PLUS
            extend_current_plan(referrer_id)
            referrer["ref_plus_valid"] -= 5

    # =========================
    # USUARIO PREMIUM
    # =========================
    elif plan == PLAN_PREMIUM:
        if premium_count >= 5:
            # Extender PREMIUM
            extend_current_plan(referrer_id)
            referrer["ref_premium_valid"] -= 5

        elif plus_count >= 10:
            # Extender PREMIUM con PLUS
            extend_current_plan(referrer_id)
            referrer["ref_plus_valid"] -= 10

    # Guardar consumo de contadores si hubo cambios
    referrer = update_timestamp(referrer)
    users_col.update_one(
        {"user_id": referrer_id},
        {"$set": {
            "ref_plus_valid": referrer.get("ref_plus_valid", 0),
            "ref_premium_valid": referrer.get("ref_premium_valid", 0),
            "updated_at": referrer["updated_at"],
        }},
)
