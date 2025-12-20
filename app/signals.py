# app/signals.py

from datetime import datetime, timedelta
from typing import List, Dict, Optional
import hashlib
import random

from app.database import signals_collection, user_signals_collection
from app.models import new_signal
from app.plans import PLAN_FREE, PLAN_PLUS, PLAN_PREMIUM


# ======================================================
# CONFIGURACIÓN GLOBAL DE SEÑALES
# ======================================================

MARGIN_MODE = "ISOLATED"          # Siempre aislado
SIGNAL_VALIDITY_MINUTES = 15      # Ventana de validez real

LEVERAGE_RANGES = {
    "conservative": "5x - 10x",
    "moderate": "10x - 20x",
    "aggressive": "30x - 40x",
}


# ======================================================
# CREACIÓN DE SEÑAL BASE (USADA SOLO POR EL SCANNER)
# ======================================================

def create_base_signal(
    symbol: str,
    direction: str,
    entry_price: float,
    stop_loss: float,
    take_profits: List[float],
    timeframes: List[str],
    visibility: str,
) -> Dict:
    """
    Crea una SEÑAL BASE.
    Esta señal NUNCA se envía directamente al usuario.
    """
    if visibility not in (PLAN_FREE, PLAN_PLUS, PLAN_PREMIUM):
        raise ValueError("Visibilidad de señal inválida")

    signal = new_signal(
        symbol=symbol,
        direction=direction,
        entry=str(entry_price),
        stop_loss=str(stop_loss),
        take_profits=[str(tp) for tp in take_profits],
        timeframes=timeframes,
        visibility=visibility,
        leverage=LEVERAGE_RANGES,
    )

    now = datetime.utcnow()

    signal["margin_mode"] = MARGIN_MODE
    signal["created_at"] = now
    signal["valid_until"] = now + timedelta(minutes=SIGNAL_VALIDITY_MINUTES)

    signals_collection().insert_one(signal)
    return signal


# ======================================================
# GENERACIÓN DE SEÑAL PERSONALIZADA POR USUARIO
# ======================================================

def generate_user_signal(
    base_signal: Dict,
    user_id: int,
) -> Dict:
    """
    Genera una señal ÚNICA por usuario a partir de la señal base.
    """
    seed_source = f"{base_signal['_id']}_{user_id}"
    seed = int(hashlib.sha256(seed_source.encode()).hexdigest(), 16)
    random.seed(seed)

    def vary(value: float, percent: float) -> float:
        delta = value * percent
        return round(random.uniform(value - delta, value + delta), 4)

    entry = vary(float(base_signal["entry"]), 0.0005)
    stop_loss = vary(float(base_signal["stop_loss"]), 0.001)

    take_profits = [
        vary(float(tp), 0.001) for tp in base_signal["take_profits"]
    ]

    fingerprint = hashlib.md5(
        f"{user_id}_{base_signal['_id']}".encode()
    ).hexdigest()[:8]

    user_signal = {
        "user_id": user_id,
        "signal_id": str(base_signal["_id"]),
        "symbol": base_signal["symbol"],
        "direction": base_signal["direction"],
        "entry": entry,
        "stop_loss": stop_loss,
        "take_profits": take_profits,
        "leverage": base_signal["leverage"],
        "margin_mode": base_signal["margin_mode"],
        "timeframes": base_signal["timeframes"],
        "created_at": datetime.utcnow(),
        "valid_until": base_signal["valid_until"],
        "fingerprint": fingerprint,
    }

    user_signals_collection().insert_one(user_signal)
    return user_signal


# ======================================================
# OBTENER ÚLTIMA SEÑAL BASE DISPONIBLE
# ======================================================

def get_latest_base_signal_for_plan(plan: str) -> Optional[Dict]:
    """
    Obtiene la última señal base visible según el plan del usuario.
    """
    if plan == PLAN_FREE:
        visibility = [PLAN_FREE]
    elif plan == PLAN_PLUS:
        visibility = [PLAN_FREE, PLAN_PLUS]
    else:
        visibility = [PLAN_FREE, PLAN_PLUS, PLAN_PREMIUM]

    return signals_collection().find_one(
        {
            "visibility": {"$in": visibility},
            "valid_until": {"$gt": datetime.utcnow()},
        },
        sort=[("created_at", -1)],
    )


# ======================================================
# FORMATEO FINAL PARA TELEGRAM (USUARIO)
# ======================================================

def format_user_signal(signal: Dict) -> str:
    """
    Devuelve el texto final que verá el usuario en Telegram.
    """
    tps = "\n".join(
        [f"TP{i + 1}: {tp}" for i, tp in enumerate(signal["take_profits"])]
    )

    return (
        "📊 NUEVA SEÑAL – FUTUROS USDT\n\n"
        f"Par: {signal['symbol']}\n"
        f"Dirección: {signal['direction']}\n\n"
        f"Entrada:\n{signal['entry']}\n\n"
        "Take Profit:\n"
        f"{tps}\n\n"
        f"Stop Loss:\n{signal['stop_loss']}\n\n"
        "Apalancamiento sugerido:\n"
        f"Conservador: {signal['leverage']['conservative']}\n"
        f"Moderado: {signal['leverage']['moderate']}\n"
        f"Agresivo: {signal['leverage']['aggressive']}\n\n"
        f"Tipo de margen:\n{signal['margin_mode']}\n\n"
        f"Timeframes:\n{' / '.join(signal['timeframes'])}\n\n"
        f"⏳ Válida hasta: {signal['valid_until'].strftime('%H:%M UTC')}\n"
        f"🔐 Signal ID: {signal['fingerprint']}"
      )
