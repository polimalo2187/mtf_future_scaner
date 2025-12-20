# app/plans.py

from datetime import datetime, timedelta
from typing import Optional

from app.database import users_collection
from app.models import activate_plan, is_plan_active, is_trial_active, update_timestamp


# =========================
# CONSTANTES DE PLANES
# =========================

PLAN_FREE = "free"
PLAN_PLUS = "plus"
PLAN_PREMIUM = "premium"

PLAN_DURATION_DAYS = 30


# =========================
# HELPERS DE USUARIO
# =========================

def get_user(user_id: int) -> Optional[dict]:
    return users_collection().find_one({"user_id": user_id})


def save_user(user: dict):
    users_collection().update_one(
        {"user_id": user["user_id"]},
        {"$set": user},
        upsert=False,
    )


# =========================
# VERIFICACIONES DE ESTADO
# =========================

def has_access(user: dict) -> bool:
    """
    Retorna True si el usuario puede ver señales
    (plan activo o trial activo).
    """
    return is_plan_active(user) or is_trial_active(user)


def plan_status(user: dict) -> dict:
    """
    Retorna estado legible del plan.
    """
    now = datetime.utcnow()

    if is_plan_active(user):
        return {
            "plan": user["plan"],
            "status": "active",
            "expires": user["plan_end"],
        }

    if is_trial_active(user):
        return {
            "plan": PLAN_FREE,
            "status": "trial",
            "expires": user["trial_end"],
        }

    return {
        "plan": PLAN_FREE,
        "status": "expired",
        "expires": None,
    }


# =========================
# ACTIVACIONES (ADMIN / SISTEMA)
# =========================

def activate_plus(user_id: int, days: int = PLAN_DURATION_DAYS) -> bool:
    """
    Activa o extiende PLAN PLUS.
    """
    user = get_user(user_id)
    if not user:
        return False

    user = activate_plan(user, PLAN_PLUS, days)
    save_user(user)
    return True


def activate_premium(user_id: int, days: int = PLAN_DURATION_DAYS) -> bool:
    """
    Activa o extiende PLAN PREMIUM.
    """
    user = get_user(user_id)
    if not user:
        return False

    user = activate_plan(user, PLAN_PREMIUM, days)
    save_user(user)
    return True


# =========================
# EXPIRACIONES AUTOMÁTICAS
# =========================

def expire_plans():
    """
    Revisa y expira planes vencidos.
    Debe ejecutarse periódicamente (scheduler).
    """
    now = datetime.utcnow()
    users_col = users_collection()

    expired_users = users_col.find({
        "plan_end": {"$lt": now}
    })

    for user in expired_users:
        user["plan"] = PLAN_FREE
        user["plan_end"] = None
        user = update_timestamp(user)
        users_col.update_one(
            {"user_id": user["user_id"]},
            {"$set": user},
        )


# =========================
# UTILIDADES DE EXTENSIÓN
# =========================

def extend_current_plan(user_id: int, days: int = PLAN_DURATION_DAYS) -> bool:
    """
    Extiende el plan actual del usuario.
    """
    user = get_user(user_id)
    if not user or not is_plan_active(user):
        return False

    user["plan_end"] = user["plan_end"] + timedelta(days=days)
    user = update_timestamp(user)
    save_user(user)
    return True
