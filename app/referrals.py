# app/referrals.py

import logging
from typing import Optional, Dict
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

logger = logging.getLogger(__name__)


# =========================
# REGISTRO DE REFERIDO VÁLIDO
# =========================

def register_valid_referral(
    referred_user_id: int,
    activated_plan: str,
) -> None:
    """
    Registra un referido válido cuando un usuario activa un plan
    (PLUS o PREMIUM). Cuenta solo una vez por usuario referido.
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
# ESTADÍSTICAS DE REFERIDOS PARA USUARIO
# =========================

def get_user_referral_stats(user_id: int) -> Optional[Dict]:
    """
    Obtiene las estadísticas de referidos de un usuario.
    Retorna un diccionario con toda la info, incluyendo USDT acumulado.
    """
    try:
        users_col = users_collection()
        refs_col = referrals_collection()
        
        user = users_col.find_one({"user_id": user_id})
        if not user:
            # Retornar estructura básica
            return {
                "user_id": user_id,
                "ref_code": f"ref_{user_id}",
                "total_referred": 0,
                "plus_referred": 0,
                "premium_referred": 0,
                "current_plus": 0,
                "current_premium": 0,
                "pending_rewards": [],
                "usdt_earned": 0.0,
                "plan": PLAN_FREE,
            }
        
        # Contar referidos
        total_referred = refs_col.count_documents({"referrer_id": user_id})
        plus_referred = refs_col.count_documents({"referrer_id": user_id, "activated_plan": PLAN_PLUS})
        premium_referred = refs_col.count_documents({"referrer_id": user_id, "activated_plan": PLAN_PREMIUM})

        # Contadores actuales
        current_plus = user.get("ref_plus_valid", 0)
        current_premium = user.get("ref_premium_valid", 0)

        # Calcular USDT ganados
        usdt_earned = (plus_referred // 5) * 2 + (premium_referred // 5) * 4

        # Recompensas pendientes
        plan = user.get("plan", PLAN_FREE)
        pending_rewards = []

        if plan == PLAN_FREE:
            if current_premium >= 5:
                pending_rewards.append("🎯 5 referidos PREMIUM = Plan PREMIUM GRATIS")
            if current_plus >= 5:
                pending_rewards.append("🎯 5 referidos PLUS = Plan PLUS GRATIS")

        elif plan == PLAN_PLUS:
            if current_premium >= 5:
                pending_rewards.append("🎯 5 referidos PREMIUM = Subir a PREMIUM")
            if current_plus >= 5:
                pending_rewards.append("🎯 5 referidos PLUS = Extender tu plan PLUS")

        elif plan == PLAN_PREMIUM:
            if current_premium >= 5:
                pending_rewards.append("🎯 5 referidos PREMIUM = Extender tu plan PREMIUM")
            if current_plus >= 10:
                pending_rewards.append("🎯 10 referidos PLUS = Extender tu plan PREMIUM")

        ref_code = user.get("ref_code", f"ref_{user_id}")

        return {
            "user_id": user_id,
            "ref_code": ref_code,
            "total_referred": total_referred,
            "plus_referred": plus_referred,
            "premium_referred": premium_referred,
            "current_plus": current_plus,
            "current_premium": current_premium,
            "pending_rewards": pending_rewards,
            "usdt_earned": usdt_earned,
            "plan": plan,
        }
        
    except Exception as e:
        logger.error(f"❌ Error crítico en get_user_referral_stats para user_id {user_id}: {e}", exc_info=True)
        return None


# =========================
# EVALUACIÓN DE RECOMPENSAS
# =========================

def check_ref_rewards(referrer_id: int) -> None:
    """
    Evalúa y aplica recompensas automáticas según plan y contadores.
    """
    users_col = users_collection()
    referrer = users_col.find_one({"user_id": referrer_id})
    if not referrer:
        return

    plan = referrer.get("plan", PLAN_FREE)
    plus_count = referrer.get("ref_plus_valid", 0)
    premium_count = referrer.get("ref_premium_valid", 0)

    # USUARIO FREE
    if plan == PLAN_FREE:
        if premium_count >= 5:
            activate_premium(referrer_id)
            referrer["ref_premium_valid"] -= 5
        elif plus_count >= 5:
            activate_plus(referrer_id)
            referrer["ref_plus_valid"] -= 5

    # USUARIO PLUS
    elif plan == PLAN_PLUS:
        if premium_count >= 5:
            activate_premium(referrer_id)
            referrer["ref_premium_valid"] -= 5
        elif plus_count >= 5:
            extend_current_plan(referrer_id)
            referrer["ref_plus_valid"] -= 5

    # USUARIO PREMIUM
    elif plan == PLAN_PREMIUM:
        if premium_count >= 5:
            extend_current_plan(referrer_id)
            referrer["ref_premium_valid"] -= 5
        elif plus_count >= 10:
            extend_current_plan(referrer_id)
            referrer["ref_plus_valid"] -= 10

    referrer = update_timestamp(referrer)
    users_col.update_one(
        {"user_id": referrer_id},
        {"$set": {
            "ref_plus_valid": referrer.get("ref_plus_valid", 0),
            "ref_premium_valid": referrer.get("ref_premium_valid", 0),
            "updated_at": referrer["updated_at"],
        }},
  )
