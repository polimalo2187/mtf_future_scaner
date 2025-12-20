# app/models.py

from datetime import datetime, timedelta
from typing import Optional, Dict, Any


# =========================
# USER MODEL (LOGICAL)
# =========================

def new_user(
    user_id: int,
    username: Optional[str],
    referred_by: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Crea el documento base para un nuevo usuario.
    """
    now = datetime.utcnow()
    trial_days = 7

    return {
        "user_id": user_id,
        "username": username,
        "plan": "free",                  # free | plus | premium
        "trial_end": now + timedelta(days=trial_days),
        "plan_end": None,                # solo para plus / premium

        "ref_code": f"ref_{user_id}",
        "referred_by": referred_by,      # user_id del referidor

        "ref_plus_valid": 0,
        "ref_premium_valid": 0,

        "created_at": now,
        "updated_at": now,
    }


def update_timestamp(doc: Dict[str, Any]) -> Dict[str, Any]:
    """
    Actualiza el campo updated_at.
    """
    doc["updated_at"] = datetime.utcnow()
    return doc


def activate_plan(
    user: Dict[str, Any],
    plan: str,
    days: int = 30,
) -> Dict[str, Any]:
    """
    Activa o extiende un plan (plus / premium).
    """
    now = datetime.utcnow()

    if user.get("plan_end") and user["plan_end"] > now:
        # Extender plan existente
        user["plan_end"] = user["plan_end"] + timedelta(days=days)
    else:
        # Activar desde ahora
        user["plan_end"] = now + timedelta(days=days)

    user["plan"] = plan
    user["trial_end"] = None  # ya no está en trial

    return update_timestamp(user)


def is_trial_active(user: Dict[str, Any]) -> bool:
    """
    Retorna True si el trial sigue activo.
    """
    if user.get("trial_end") is None:
        return False
    return user["trial_end"] >= datetime.utcnow()


def is_plan_active(user: Dict[str, Any]) -> bool:
    """
    Retorna True si el plan de pago está activo.
    """
    if user.get("plan_end") is None:
        return False
    return user["plan_end"] >= datetime.utcnow()


# =========================
# REFERRAL MODEL
# =========================

def new_referral(
    referrer_id: int,
    referred_id: int,
    activated_plan: str,
) -> Dict[str, Any]:
    """
    Crea un registro histórico de referido válido.
    """
    return {
        "referrer_id": referrer_id,
        "referred_id": referred_id,
        "activated_plan": activated_plan,   # plus | premium
        "activated_at": datetime.utcnow(),
    }


# =========================
# SIGNAL MODEL
# =========================

def new_signal(
    symbol: str,
    direction: str,
    entry: str,
    stop_loss: str,
    take_profits: list,
    timeframes: list,
    visibility: str,  # free | plus | premium
    leverage: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Crea una señal lista para guardar en MongoDB.
    """
    return {
        "symbol": symbol,
        "direction": direction,  # LONG | SHORT
        "entry": entry,
        "stop_loss": stop_loss,
        "take_profits": take_profits,

        "timeframes": timeframes,  # ["5m", "15m", "1h"]

        "leverage": leverage or {
            "conservative": "5x-10x",
            "moderate": "10x-20x",
            "aggressive": "30x-40x",
        },

        "margin_mode": "isolated",  # fijo, siempre aislado

        "visibility": visibility,   # quién puede verla
        "created_at": datetime.utcnow(),
  }
