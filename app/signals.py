# app/signals.py

from datetime import datetime, timedelta
from typing import List, Dict, Optional
import hashlib
import random

from app.database import signals_collection, user_signals_collection
from app.models import new_signal
from app.plans import PLAN_FREE, PLAN_PLUS, PLAN_PREMIUM


# =========================
# CONFIGURACIÓN GLOBAL
# =========================

MARGIN_MODE = "ISOLATED"
SIGNAL_VALIDITY_MINUTES = 15  # ventana de uso real

LEVERAGE_RANGES = {
    "conservative": "5x - 10x",
    "moderate": "10x - 20x",
    "aggressive": "30x - 40x",
}


# =========================
# CREAR SEÑAL BASE (SCANNER)
# =========================

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
    Señal BASE. Nunca se envía directamente al usuario.
    """
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

    signal["margin_mode"] = MARGIN_MODE
    signal["created_at"] = datetime.utcnow()
    signal["valid_until"] = signal["created_at"] + timedelta(minutes=SIGNAL_VALIDITY_MINUTES)

    signals_collection().insert_one(signal)
    return signal


# =========================
# GENERAR VARIACIÓN POR USUARIO
# =========================

def generate_user_signal(base_signal: Dict, user_id: int) -> Dict:
    """
    Genera una señal ÚNICA para cada usuario.
    """
    seed = int(hashlib.sha256(f"{base_signal['_id']}_{user_id}".encode()).hexdigest(), 16)
    random.seed(seed)

    def vary(value: float, percent: float):
        delta = value * percent
        return round(random.uniform(value - delta, value + delta), 4)

    entry = vary(float(base_signal["entry"]), 0.0005)
    stop_loss = vary(float(base_signal["stop_loss"]), 0.001)

    take_profits = [
        vary(float(tp), 0.001) for tp in base_signal["take_profits"]
    ]

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
        "fingerprint": hashlib.md5(f"{user_id}{base_signal['_id']}".encode()).hexdigest()[:8],
    }

    user_signals_collection().insert_one(user_signal)
    return user_signal


# =========================
# FORMATEO FINAL (TELEGRAM)
# =========================

def format_user_signal(signal: Dict) -> str:
    tps = "\n".join(
        [f"TP{i+1}: {tp}" for i, tp in enumerate(signal["take_profits"])]
    )

    return (
        "NUEVA SEÑAL – FUTUROS USDT\n\n"
        f"Par: {signal['symbol']}\n"
        f"Dirección: {signal['direction']}\n\n"
        f"Entrada: {signal['entry']}\n\n"
        "Take Profit:\n"
        f"{tps}\n\n"
        f"Stop Loss: {signal['stop_loss']}\n\n"
        "Apalancamiento sugerido:\n"
        f"Conservador: {signal['leverage']['conservative']}\n"
        f"Moderado: {signal['leverage']['moderate']}\n"
        f"Agresivo: {signal['leverage']['aggressive']}\n\n"
        f"Margen: {signal['margin_mode']}\n\n"
        f"Timeframes: {' / '.join(signal['timeframes'])}\n\n"
        f"Válida hasta: {signal['valid_until'].strftime('%H:%M UTC')}\n"
        f"Signal ID: {signal['fingerprint']}"
  )
