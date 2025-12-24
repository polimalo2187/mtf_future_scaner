# app/referrals.py

import logging
from typing import Optional, Dict, Tuple
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
# ESTADÍSTICAS DE REFERIDOS PARA USUARIO
# =========================

def get_user_referral_stats(user_id: int) -> Optional[Dict]:
    """
    Obtiene las estadísticas de referidos de un usuario.
    Retorna un diccionario con la información o None si el usuario no existe.
    """
    users_col = users_collection()
    refs_col = referrals_collection()
    
    user = users_col.find_one({"user_id": user_id})
    if not user:
        return None
    
    # Contar referidos totales
    total_referred = refs_col.count_documents({"referrer_id": user_id})
    
    # Contar por tipo de plan activado
    plus_referred = refs_col.count_documents({
        "referrer_id": user_id,
        "activated_plan": PLAN_PLUS
    })
    
    premium_referred = refs_col.count_documents({
        "referrer_id": user_id,
        "activated_plan": PLAN_PREMIUM
    })
    
    # Obtener últimos 5 referidos
    recent_referrals = list(refs_col.find(
        {"referrer_id": user_id},
        sort=[("activated_at", -1)],
        limit=5
    ))
    
    # Procesar referidos recientes para mostrar
    recent_list = []
    for ref in recent_referrals:
        referred_user = users_col.find_one({"user_id": ref["referred_id"]})
        username = referred_user.get("username", "Sin nombre") if referred_user else "Usuario eliminado"
        recent_list.append({
            "user_id": ref["referred_id"],
            "username": username,
            "plan": ref["activated_plan"],
            "date": ref["activated_at"].strftime("%d/%m/%Y")
        })
    
    # Calcular recompensas pendientes según plan actual
    plan = user.get("plan", PLAN_FREE)
    plus_count = user.get("ref_plus_valid", 0)
    premium_count = user.get("ref_premium_valid", 0)
    
    pending_rewards = []
    
    if plan == PLAN_FREE:
        if premium_count >= 5:
            pending_rewards.append("🎯 5 referidos PREMIUM = Plan PREMIUM GRATIS")
        elif plus_count >= 5:
            pending_rewards.append("🎯 5 referidos PLUS = Plan PLUS GRATIS")
    
    elif plan == PLAN_PLUS:
        if premium_count >= 5:
            pending_rewards.append("🎯 5 referidos PREMIUM = Subir a PREMIUM")
        elif plus_count >= 5:
            pending_rewards.append("🎯 5 referidos PLUS = Extender tu plan PLUS")
    
    elif plan == PLAN_PREMIUM:
        if premium_count >= 5:
            pending_rewards.append("🎯 5 referidos PREMIUM = Extender tu plan PREMIUM")
        elif plus_count >= 10:
            pending_rewards.append("🎯 10 referidos PLUS = Extender tu plan PREMIUM")
    
    # Código de referido del usuario
    ref_code = user.get("ref_code", f"ref_{user_id}")
    
    return {
        "user_id": user_id,
        "ref_code": ref_code,
        "total_referred": total_referred,
        "plus_referred": plus_referred,
        "premium_referred": premium_referred,
        "current_plus": plus_count,
        "current_premium": premium_count,
        "recent_referrals": recent_list,
        "pending_rewards": pending_rewards,
        "plan": plan,
    }


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
