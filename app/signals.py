# app/signals.py

from datetime import datetime, timedelta
from typing import List, Dict, Optional
import hashlib
import random

from app.database import signals_collection, user_signals_collection
from app.models import new_signal
from app.plans import PLAN_FREE, PLAN_PLUS, PLAN_PREMIUM


# ======================================================
# CONFIGURACIÓN GLOBAL
# ======================================================

MARGIN_MODE = "ISOLATED"
SIGNAL_VALIDITY_MINUTES = 15

LEVERAGE_PROFILES = {
    "conservador": "5x – 10x",
    "moderado": "10x – 20x",
    "agresivo": "30x – 40x",
}


# ======================================================
# CREAR SEÑAL BASE (SCANNER)
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
    Crea una SEÑAL BASE (no se envía al usuario).
    """
    if visibility not in (PLAN_FREE, PLAN_PLUS, PLAN_PREMIUM):
        raise ValueError("Visibilidad inválida")

    signal = new_signal(
        symbol=symbol,
        direction=direction,
        entry=str(entry_price),
        stop_loss=str(stop_loss),
        take_profits=[str(tp) for tp in take_profits],
        timeframes=timeframes,
        visibility=visibility,
        leverage=LEVERAGE_PROFILES,
    )

    now = datetime.utcnow()
    signal["margin_mode"] = MARGIN_MODE
    signal["created_at"] = now
    signal["valid_until"] = now + timedelta(minutes=SIGNAL_VALIDITY_MINUTES)

    signals_collection().insert_one(signal)
    return signal


# ======================================================
# GENERAR SEÑAL PERSONALIZADA POR USUARIO
# ======================================================

def generate_user_signal(base_signal: Dict, user_id: int) -> Dict:
    """
    Genera una señal personalizada con perfiles de riesgo.
    """
    seed = int(
        hashlib.sha256(f"{base_signal['_id']}_{user_id}".encode()).hexdigest(), 16
    )
    random.seed(seed)

    def vary(value: float, percent: float):
        delta = value * percent
        return round(random.uniform(value - delta, value + delta), 4)

    entry = vary(float(base_signal["entry"]), 0.0005)

    # SL / TP por perfil
    profiles = {
        "conservador": {
            "stop_loss": vary(float(base_signal["stop_loss"]), 0.002),
            "take_profits": [
                vary(float(tp), 0.0005) for tp in base_signal["take_profits"]
            ],
        },
        "moderado": {
            "stop_loss": vary(float(base_signal["stop_loss"]), 0.001),
            "take_profits": [
                vary(float(tp), 0.001) for tp in base_signal["take_profits"]
            ],
        },
        "agresivo": {
            "stop_loss": vary(float(base_signal["stop_loss"]), 0.0005),
            "take_profits": [
                vary(float(tp), 0.0015) for tp in base_signal["take_profits"]
            ],
        },
    }

    fingerprint = hashlib.md5(
        f"{user_id}_{base_signal['_id']}".encode()
    ).hexdigest()[:8]

    user_signal = {
        "user_id": user_id,
        "signal_id": str(base_signal["_id"]),
        "symbol": base_signal["symbol"],
        "direction": base_signal["direction"],
        "entry": entry,
        "profiles": profiles,
        "leverage_profiles": base_signal["leverage"],
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
# FORMATEO FINAL PARA TELEGRAM
# ======================================================

def format_user_signal(signal: Dict) -> str:
    text = (
        "📊 NUEVA SEÑAL – FUTUROS USDT\n\n"
        f"Par: {signal['symbol']}\n"
        f"Dirección: {signal['direction']}\n"
        f"Entrada base: {signal['entry']}\n\n"
        f"Margen: {signal['margin_mode']}\n"
        f"Timeframes: {' / '.join(signal['timeframes'])}\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🟢 MODO CONSERVADOR\n"
        f"SL: {signal['profiles']['conservador']['stop_loss']}\n"
    )

    for i, tp in enumerate(signal["profiles"]["conservador"]["take_profits"], 1):
        text += f"TP{i}: {tp}\n"

    text += (
        f"Apalancamiento: {signal['leverage_profiles']['conservador']}\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🟡 MODO MODERADO\n"
        f"SL: {signal['profiles']['moderado']['stop_loss']}\n"
    )

    for i, tp in enumerate(signal["profiles"]["moderado"]["take_profits"], 1):
        text += f"TP{i}: {tp}\n"

    text += (
        f"Apalancamiento: {signal['leverage_profiles']['moderado']}\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🔴 MODO AGRESIVO\n"
        f"SL: {signal['profiles']['agresivo']['stop_loss']}\n"
    )

    for i, tp in enumerate(signal["profiles"]["agresivo"]["take_profits"], 1):
        text += f"TP{i}: {tp}\n"

    text += (
        f"Apalancamiento: {signal['leverage_profiles']['agresivo']}\n\n"
        f"⏳ Válida hasta: {signal['valid_until'].strftime('%H:%M UTC')}\n"
        f"🔐 Signal ID: {signal['fingerprint']}"
    )

    return text
