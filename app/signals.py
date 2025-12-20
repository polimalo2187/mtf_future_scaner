# app/signals.py

from datetime import datetime, timedelta
from typing import List, Dict, Optional
import hashlib
import random
import requests

from app.database import (
    signals_collection,
    user_signals_collection,
    signal_results_collection,
)
from app.models import new_signal
from app.plans import PLAN_FREE, PLAN_PLUS, PLAN_PREMIUM


# ======================================================
# CONFIGURACIÓN GLOBAL
# ======================================================

MARGIN_MODE = "ISOLATED"
SIGNAL_VALIDITY_MINUTES = 15
BINANCE_FUTURES_API = "https://fapi.binance.com"

LEVERAGE_PROFILES = {
    "conservador": "5x – 10x",
    "moderado": "10x – 20x",
    "agresivo": "30x – 40x",
}


# ======================================================
# PRECIO ACTUAL
# ======================================================

def get_current_price(symbol: str) -> float:
    r = requests.get(
        f"{BINANCE_FUTURES_API}/fapi/v1/ticker/price",
        params={"symbol": symbol},
        timeout=10,
    )
    r.raise_for_status()
    return float(r.json()["price"])


# ======================================================
# CREAR SEÑAL BASE
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
    signal["evaluated"] = False

    signals_collection().insert_one(signal)
    return signal


# ======================================================
# SEÑAL PERSONALIZADA
# ======================================================

def generate_user_signal(base_signal: Dict, user_id: int) -> Dict:
    seed = int(
        hashlib.sha256(f"{base_signal['_id']}_{user_id}".encode()).hexdigest(), 16
    )
    random.seed(seed)

    def vary(value: float, percent: float):
        delta = value * percent
        return round(random.uniform(value - delta, value + delta), 4)

    entry = vary(float(base_signal["entry"]), 0.0005)

    profiles = {
        "conservador": {
            "stop_loss": vary(float(base_signal["stop_loss"]), 0.002),
            "take_profits": [vary(float(tp), 0.0005) for tp in base_signal["take_profits"]],
        },
        "moderado": {
            "stop_loss": vary(float(base_signal["stop_loss"]), 0.001),
            "take_profits": [vary(float(tp), 0.001) for tp in base_signal["take_profits"]],
        },
        "agresivo": {
            "stop_loss": vary(float(base_signal["stop_loss"]), 0.0005),
            "take_profits": [vary(float(tp), 0.0015) for tp in base_signal["take_profits"]],
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
# EVALUAR SEÑALES EXPIRADAS (WON / LOST / EXPIRED)
# ======================================================

def evaluate_expired_signals():
    now = datetime.utcnow()
    expired_signals = signals_collection().find(
        {"valid_until": {"$lt": now}, "evaluated": False}
    )

    for signal in expired_signals:
        try:
            price = get_current_price(signal["symbol"])

            entry = float(signal["entry"])
            stop_loss = float(signal["stop_loss"])
            tps = [float(tp) for tp in signal["take_profits"]]

            result = "expired"

            if signal["direction"] == "LONG":
                if price <= stop_loss:
                    result = "lost"
                elif any(price >= tp for tp in tps):
                    result = "won"
            else:
                if price >= stop_loss:
                    result = "lost"
                elif any(price <= tp for tp in tps):
                    result = "won"

            signal_results_collection().insert_one(
                {
                    "signal_id": str(signal["_id"]),
                    "symbol": signal["symbol"],
                    "direction": signal["direction"],
                    "result": result,
                    "visibility": signal["visibility"],
                    "created_at": signal["created_at"],
                    "evaluated_at": now,
                }
            )

            signals_collection().update_one(
                {"_id": signal["_id"]},
                {"$set": {"evaluated": True}},
            )

        except Exception:
            continue


# ======================================================
# OBTENER ÚLTIMA SEÑAL DISPONIBLE
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
# FORMATEO FINAL
# ======================================================

def format_user_signal(signal: Dict) -> str:
    text = (
        "📊 NUEVA SEÑAL – FUTUROS USDT\n\n"
        f"Par: {signal['symbol']}\n"
        f"Dirección: {signal['direction']}\n"
        f"Entrada base: {signal['entry']}\n\n"
        f"Margen: {signal['margin_mode']}\n"
        f"Timeframes: {' / '.join(signal['timeframes'])}\n\n"
    )

    for profile in ["conservador", "moderado", "agresivo"]:
        text += f"━━━━━━━━━━━━━━━━━━\n"
        text += f"{profile.upper()}\n"
        text += f"SL: {signal['profiles'][profile]['stop_loss']}\n"
        for i, tp in enumerate(signal["profiles"][profile]["take_profits"], 1):
            text += f"TP{i}: {tp}\n"
        text += f"Apalancamiento: {signal['leverage_profiles'][profile]}\n\n"

    text += (
        f"⏳ Válida hasta: {signal['valid_until'].strftime('%H:%M UTC')}\n"
        f"🔐 Signal ID: {signal['fingerprint']}"
    )

    return text
