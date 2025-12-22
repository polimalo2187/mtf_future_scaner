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
from app.config import is_admin  # 🔹 CORREGIDO: import desde config


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
    # Aseguramos que _id exista después de insert
    signal["_id"] = result.inserted_id

    return signal


# ======================================================
# SEÑAL PERSONALIZADA
# ======================================================

def generate_user_signal(base_signal: Dict, user_id: int) -> Dict:
    # 🔹 Verificación de _id
    base_id = base_signal.get("_id")
    if not base_id:
        raise ValueError("base_signal no tiene '_id'")

    seed = int(
        hashlib.sha256(f"{base_id}_{user_id}".encode()).hexdigest(), 16
    )
    random.seed(seed)

    def vary(value: float, percent: float):
        delta = value * percent
        return round(random.uniform(value - delta, value + delta), 4)

    # 🔹 Conversión segura de entry
    try:
        entry_value = float(base_signal.get("entry", 0))
    except (TypeError, ValueError):
        entry_value = 0.0

    entry = vary(entry_value, 0.0005)

    profiles = {}
    for profile_name, sl_percent, tp_percent in [
        ("conservador", 0.002, 0.0005),
        ("moderado", 0.001, 0.001),
        ("agresivo", 0.0005, 0.0015),
    ]:
        try:
            sl_value = float(base_signal.get("stop_loss", 0))
            tps_list = base_signal.get("take_profits", [])
            tps_value = [float(tp) for tp in tps_list]
        except (TypeError, ValueError):
            sl_value = 0.0
            tps_value = [0.0, 0.0]

        profiles[profile_name] = {
            "stop_loss": vary(sl_value, sl_percent),
            "take_profits": [vary(tp, tp_percent) for tp in tps_value],
        }

    fingerprint = hashlib.md5(
        f"{user_id}_{base_id}".encode()
    ).hexdigest()[:8]

    user_signal = {
        "user_id": user_id,
        "signal_id": str(base_id),
        "symbol": base_signal.get("symbol", ""),
        "direction": base_signal.get("direction", ""),
        "entry": entry,
        "profiles": profiles,
        "leverage_profiles": base_signal.get("leverage", LEVERAGE_PROFILES),
        "margin_mode": base_signal.get("margin_mode", MARGIN_MODE),
        "timeframes": base_signal.get("timeframes", []),
        "created_at": datetime.utcnow(),
        "valid_until": base_signal.get("valid_until", datetime.utcnow()),
        "fingerprint": fingerprint,
        "visibility": base_signal.get("visibility", PLAN_FREE),
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
            price = get_current_price(signal.get("symbol", ""))
            try:
                entry = float(signal.get("entry", 0))
                stop_loss = float(signal.get("stop_loss", 0))
                tps = [float(tp) for tp in signal.get("take_profits", [])]
            except (TypeError, ValueError):
                entry = stop_loss = 0.0
                tps = []

            result = "expired"
            direction = signal.get("direction", "")

            if direction == "LONG":
                if price <= stop_loss:
                    result = "lost"
                elif any(price >= tp for tp in tps):
                    result = "won"
            elif direction == "SHORT":
                if price >= stop_loss:
                    result = "lost"
                elif any(price <= tp for tp in tps):
                    result = "won"

            signal_results_collection().insert_one(
                {
                    "signal_id": str(signal.get("_id", "")),
                    "symbol": signal.get("symbol", ""),
                    "direction": direction,
                    "result": result,
                    "visibility": signal.get("visibility", PLAN_FREE),
                    "created_at": signal.get("created_at", datetime.utcnow()),
                    "evaluated_at": now,
                }
            )

            signals_collection().update_one(
                {"_id": signal.get("_id")},
                {"$set": {"evaluated": True}},
            )

        except Exception:
            continue


# ======================================================
# OBTENER ÚLTIMA SEÑAL DISPONIBLE POR PLAN / ADMIN
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

    signals = list(signals_collection().find(
        {
            "visibility": {"$in": visibility},
            "valid_until": {"$gt": datetime.utcnow()},
        },
        sort=[("created_at", -1)],
    ))

    return signals


# ======================================================
# FORMATEO FINAL (HORA CUBA + PLAN ETIQUETADO)
# ======================================================

def format_user_signal(signal: Dict) -> str:
    cuba_tz = ZoneInfo("America/Havana")

    # 🔹 Asegurar datetime
    start_utc = signal.get("created_at", datetime.utcnow())
    end_utc = signal.get("valid_until", datetime.utcnow())
    if not isinstance(start_utc, datetime):
        start_utc = datetime.utcnow()
    if not isinstance(end_utc, datetime):
        end_utc = start_utc + timedelta(minutes=SIGNAL_VALIDITY_MINUTES)

    start_cuba = start_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(cuba_tz)
    end_cuba = end_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(cuba_tz)

    plan = str(signal.get("visibility", "")).upper().strip()
    plan_line = f"🏷️ PLAN: {plan}\n\n" if plan else ""

    text = (
        "📊 NUEVA SEÑAL – FUTUROS USDT\n\n"
        f"{plan_line}"
        f"Par: {signal.get('symbol', '')}\n"
        f"Dirección: {signal.get('direction', '')}\n"
        f"Entrada base: {signal.get('entry', 0)}\n\n"
        f"Margen: {signal.get('margin_mode', MARGIN_MODE)}\n"
        f"Timeframes: {' / '.join(signal.get('timeframes', []))}\n\n"
    )

    for profile in ["conservador", "moderado", "agresivo"]:
        text += "━━━━━━━━━━━━━━━━━━\n"
        text += f"{profile.upper()}\n"
        prof_data = signal.get("profiles", {}).get(profile, {"stop_loss": 0, "take_profits": []})
        text += f"SL: {prof_data.get('stop_loss', 0)}\n"
        for i, tp in enumerate(prof_data.get("take_profits", []), 1):
            text += f"TP{i}: {tp}\n"
        leverage = signal.get("leverage_profiles", {}).get(profile, LEVERAGE_PROFILES.get(profile, ""))
        text += f"Apalancamiento: {leverage}\n\n"

    text += (
        f"⏳ Señal activa: "
        f"{start_cuba.strftime('%H:%M')} → {end_cuba.strftime('%H:%M')} "
        f"(Hora Cuba 🇨🇺)\n"
        f"🔐 Signal ID: {signal.get('fingerprint', '')}"
    )

    return text
