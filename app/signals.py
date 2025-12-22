# app/signals.py

from datetime import datetime, timedelta
from typing import List, Dict, Optional
import hashlib
import random
import requests
from zoneinfo import ZoneInfo

from app.database import (
    signals_collection,
    user_signals_collection,
    signal_results_collection,
)
from app.models import new_signal
from app.plans import PLAN_FREE, PLAN_PLUS, PLAN_PREMIUM
from app.config import is_admin
from app.strategy import mtf_strategy  # 🔹 Importamos la estrategia para score

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

    result = signals_collection().insert_one(signal)
    signal["_id"] = result.inserted_id

    return signal

# ======================================================
# SEÑAL PERSONALIZADA CON SCORE Y PLAN
# ======================================================

def generate_user_signal(base_signal: Dict, user_id: int, user_plan: str) -> Dict:
    """
    Genera la señal personalizada para el usuario, asignando SCORE
    y clasificación Oro / Plata / Bronce según el plan.
    """
    base_id = base_signal.get("_id")
    if not base_id:
        raise ValueError("base_signal no tiene _id")

    # Calculamos la señal y score usando mtf_strategy
    score_data = mtf_strategy(
        df_1h=base_signal.get("df_1h"),
        df_15m=base_signal.get("df_15m"),
        df_5m=base_signal.get("df_5m"),
    )
    if not score_data:
        score_data = {"score": 0, "direction": base_signal.get("direction", "LONG"), "entry_price": float(base_signal.get("entry", 0))}

    score = score_data["score"]
    direction = score_data["direction"]
    entry_price = score_data["entry_price"]

    # Determinar visibilidad real según plan y score
    if user_plan == PLAN_PREMIUM:
        visibility = PLAN_PREMIUM
    elif user_plan == PLAN_PLUS:
        visibility = PLAN_PLUS if score >= 50 else PLAN_FREE
    else:
        visibility = PLAN_FREE

    # Aplicar random seed para perfiles
    seed = int(
        hashlib.sha256(f"{base_id}_{user_id}".encode()).hexdigest(), 16
    )
    random.seed(seed)

    def vary(value: float, percent: float):
        delta = value * percent
        return round(random.uniform(value - delta, value + delta), 4)

    profiles = {
        "conservador": {
            "stop_loss": vary(float(base_signal.get("stop_loss", 0)), 0.002),
            "take_profits": [vary(float(tp), 0.0005) for tp in base_signal.get("take_profits", [0.0])],
        },
        "moderado": {
            "stop_loss": vary(float(base_signal.get("stop_loss", 0)), 0.001),
            "take_profits": [vary(float(tp), 0.001) for tp in base_signal.get("take_profits", [0.0])],
        },
        "agresivo": {
            "stop_loss": vary(float(base_signal.get("stop_loss", 0)), 0.0005),
            "take_profits": [vary(float(tp), 0.0015) for tp in base_signal.get("take_profits", [0.0])],
        },
    }

    fingerprint = hashlib.md5(f"{user_id}_{base_id}".encode()).hexdigest()[:8]

    user_signal = {
        "user_id": user_id,
        "signal_id": str(base_id),
        "symbol": base_signal.get("symbol", ""),
        "direction": direction,
        "entry": entry_price,
        "profiles": profiles,
        "leverage_profiles": base_signal.get("leverage", LEVERAGE_PROFILES),
        "margin_mode": base_signal.get("margin_mode", MARGIN_MODE),
        "timeframes": base_signal.get("timeframes", []),
        "created_at": datetime.utcnow(),
        "valid_until": base_signal.get("valid_until", datetime.utcnow()),
        "fingerprint": fingerprint,
        "visibility": visibility,
        "score": score,
    }

    user_signals_collection().insert_one(user_signal)
    return user_signal

# ======================================================
# EVALUAR SEÑALES EXPIRADAS
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
# OBTENER SEÑALES POR PLAN / ADMIN
# ======================================================

def get_latest_base_signal_for_user(user_id: int, user_plan: str) -> Optional[List[Dict]]:
    if is_admin(user_id):
        visibility = [PLAN_FREE, PLAN_PLUS, PLAN_PREMIUM]
    elif user_plan == PLAN_FREE:
        visibility = [PLAN_FREE]
    elif user_plan == PLAN_PLUS:
        visibility = [PLAN_FREE, PLAN_PLUS]
    else:
        visibility = [PLAN_FREE, PLAN_PLUS, PLAN_PREMIUM]

    return list(
        signals_collection().find(
            {
                "visibility": {"$in": visibility},
                "valid_until": {"$gt": datetime.utcnow()},
            },
            sort=[("created_at", -1)],
        )
    )

# ======================================================
# FORMATO FINAL PARA USUARIO
# ======================================================

def format_user_signal(signal: Dict) -> str:
    cuba_tz = ZoneInfo("America/Havana")

    start_cuba = signal["created_at"].replace(
        tzinfo=ZoneInfo("UTC")
    ).astimezone(cuba_tz)

    end_cuba = signal["valid_until"].replace(
        tzinfo=ZoneInfo("UTC")
    ).astimezone(cuba_tz)

    plan = signal.get("visibility", "").upper()
    plan_line = f"🏷️ PLAN: {plan}\n\n" if plan else ""

    text = (
        "📊 NUEVA SEÑAL – FUTUROS USDT\n\n"
        f"{plan_line}"
        f"Par: {signal['symbol']}\n"
        f"Dirección: {signal['direction']}\n"
        f"Entrada base: {signal['entry']}\n\n"
        f"Margen: {signal['margin_mode']}\n"
        f"Timeframes: {' / '.join(signal['timeframes'])}\n\n"
    )

    for profile in ["conservador", "moderado", "agresivo"]:
        text += "━━━━━━━━━━━━━━━━━━\n"
        text += f"{profile.upper()}\n"
        text += f"SL: {signal['profiles'][profile]['stop_loss']}\n"
        for i, tp in enumerate(signal["profiles"][profile]["take_profits"], 1):
            text += f"TP{i}: {tp}\n"
        text += f"Apalancamiento: {signal['leverage_profiles'][profile]}\n\n"

    text += (
        f"⏳ Señal activa: "
        f"{start_cuba.strftime('%H:%M')} → {end_cuba.strftime('%H:%M')} "
        f"(Hora Cuba 🇨🇺)\n"
        f"🔐 Signal ID: {signal['fingerprint']}"
    )

    return text
