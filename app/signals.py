# app/signals.py

import os
import time
import logging
import secrets
import hashlib
import random
from datetime import datetime, timedelta
from typing import List, Dict, Optional

import requests
from app.models import new_signal
from app.plans import PLAN_FREE, PLAN_PREMIUM
from app.config import is_admin
from app.database import signals_collection, user_signals_collection, users_collection

logger = logging.getLogger(__name__)

# ======================================================
# CONFIGURACIÓN GLOBAL
# ======================================================
BINANCE_FUTURES_API = os.getenv("BINANCE_FUTURES_API", "https://fapi.binance.com")
MAX_SIGNALS_PER_QUERY = int(os.getenv("MAX_SIGNALS_PER_QUERY", "10"))
BINANCE_MAX_RETRIES = int(os.getenv("BINANCE_MAX_RETRIES", "3"))
BINANCE_RETRY_DELAY = float(os.getenv("BINANCE_RETRY_DELAY", "1.0"))
USER_TIMEZONE = os.getenv("USER_TIMEZONE", "America/Havana")

LEVERAGE_PROFILES = {
    "conservador": "5x-10x",
    "moderado": "10x-20x",
    "agresivo": "30x-40x",
}

TIMEFRAME_TO_MINUTES = {
    "5M": 5,
    "15M": 15,
    "1H": 60,
}

DEDUP_MINUTES = int(os.getenv("DEDUP_MINUTES", "10"))
TELEGRAM_SIGNAL_COOLDOWN_MINUTES = 15

# ======================================================
# UTILIDADES
# ======================================================

def calculate_signal_validity(timeframes: List[str]) -> int:
    minutes = [TIMEFRAME_TO_MINUTES.get(tf.upper(), 0) for tf in timeframes]
    return max(minutes) if minutes else 15

def calculate_entry_zone(entry: float, pct: float = 0.0015):
    low = round(entry * (1 - pct), 4)
    high = round(entry * (1 + pct), 4)
    return low, high

def get_current_price(symbol: str) -> float:
    url = f"{BINANCE_FUTURES_API}/fapi/v1/ticker/price"
    for attempt in range(BINANCE_MAX_RETRIES):
        try:
            r = requests.get(url, params={"symbol": symbol}, timeout=10)
            r.raise_for_status()
            return float(r.json()["price"])
        except Exception:
            if attempt == BINANCE_MAX_RETRIES - 1:
                raise
            time.sleep(BINANCE_RETRY_DELAY)

def estimate_minutes_to_entry(symbol: str, entry_zone: Dict[str, float], timeframes: List[str]) -> Dict[str, int]:
    try:
        current_price = get_current_price(symbol)
        zone_mid = (entry_zone["low"] + entry_zone["high"]) / 2

        if entry_zone["low"] <= current_price <= entry_zone["high"]:
            return {"min": 1, "max": 5}

        distance_pct = abs(current_price - zone_mid) / current_price

        if "5M" in timeframes:
            speed = 0.004
            base_tf = 5
        elif "15M" in timeframes:
            speed = 0.0025
            base_tf = 15
        else:
            speed = 0.0015
            base_tf = calculate_signal_validity(timeframes)

        candles_needed = max(1, distance_pct / speed)
        minutes_estimated = candles_needed * base_tf

        return {
            "min": max(1, int(minutes_estimated * 0.6)),
            "max": int(minutes_estimated * 1.4),
        }
    except Exception as e:
        logger.warning(f"Fallback estimate_minutes_to_entry: {e}")
        base = calculate_signal_validity(timeframes)
        return {"min": max(1, int(base * 0.5)), "max": int(base * 1.5)}

def recent_duplicate_exists(symbol: str, direction: str, visibility: str) -> bool:
    since = datetime.utcnow() - timedelta(minutes=DEDUP_MINUTES)
    exists = signals_collection().find_one({
        "symbol": symbol,
        "direction": direction,
        "visibility": visibility,
        "created_at": {"$gte": since},
    }) is not None

    if exists:
        logger.info(f"♻️ Duplicado reciente detectado: {symbol} {direction} ({visibility})")
    return exists

def telegram_signal_blocked() -> bool:
    """
    Retorna True si hay señales activas en Telegram (vigentes dentro de TELEGRAM_SIGNAL_COOLDOWN_MINUTES)
    """
    since = datetime.utcnow() - timedelta(minutes=TELEGRAM_SIGNAL_COOLDOWN_MINUTES)
    last_signal = signals_collection().find_one(
        {"created_at": {"$gte": since}},
        sort=[("created_at", -1)]
    )
    return last_signal is not None

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
    score: Optional[float] = None,
    components: Optional[List[str]] = None
) -> Dict:

    if visibility is None:
        visibility = PLAN_FREE

    zone_low, zone_high = calculate_entry_zone(entry_price)
    estimated_minutes = estimate_minutes_to_entry(symbol, {"low": zone_low, "high": zone_high}, timeframes)

    # Crear signal base
    signal = new_signal(
        symbol=symbol,
        direction=direction,
        entry_price=float(entry_price),
        stop_loss=float(stop_loss),
        take_profits=[float(tp) for tp in take_profits],
        timeframes=timeframes,
        visibility=visibility,
        leverage=LEVERAGE_PROFILES,
    )

    now = datetime.utcnow()
    inserted_id = signals_collection().insert_one(signal).inserted_id

    # Actualizar campos calculados
    signals_collection().update_one(
        {"_id": inserted_id},
        {"$set": {
            "created_at": now,
            "valid_until": now + timedelta(minutes=calculate_signal_validity(timeframes)),
            "telegram_valid_until": now + timedelta(minutes=TELEGRAM_SIGNAL_COOLDOWN_MINUTES),
            "entry_zone": {"low": zone_low, "high": zone_high},
            "estimated_entry_minutes": estimated_minutes,
        }}
    )

    # Completar dict de retorno
    signal["_id"] = inserted_id
    signal["created_at"] = now
    signal["valid_until"] = now + timedelta(minutes=calculate_signal_validity(timeframes))
    signal["telegram_valid_until"] = now + timedelta(minutes=TELEGRAM_SIGNAL_COOLDOWN_MINUTES)
    signal["entry_zone"] = {"low": zone_low, "high": zone_high}
    signal["estimated_entry_minutes"] = estimated_minutes

    # Generar automáticamente señales por usuario
    generate_user_signal_for_plan(signal)

    return signal

# ======================================================
# GENERAR SEÑAL USUARIO
# ======================================================

def generate_user_signal(base_signal: Dict, user_id: int) -> Dict:
    seed = int(hashlib.sha256(f"{base_signal['_id']}_{user_id}".encode()).hexdigest(), 16)
    rnd = random.Random(seed)

    def vary(val: float, pct: float):
        return round(rnd.uniform(val * (1 - pct), val * (1 + pct)), 4)

    user_entry = vary(base_signal["entry_price"], 0.0005)
    zone_low, zone_high = calculate_entry_zone(user_entry)
    estimated_minutes = estimate_minutes_to_entry(
        base_signal["symbol"],
        {"low": zone_low, "high": zone_high},
        base_signal["timeframes"]
    )

    # PERFIL DE STOP/TP
    user_signal = {
        "user_id": user_id,
        "signal_id": str(base_signal["_id"]),
        "symbol": base_signal["symbol"],
        "direction": base_signal["direction"],
        "entry_price": user_entry,
        "entry_zone": {"low": zone_low, "high": zone_high},
        "profiles": {
            "conservador": {
                "stop_loss": vary(base_signal["stop_loss"], 0.002),
                "take_profits": [vary(tp, 0.0005) for tp in base_signal["take_profits"]],
            },
            "moderado": {
                "stop_loss": vary(base_signal["stop_loss"], 0.001),
                "take_profits": [vary(tp, 0.001) for tp in base_signal["take_profits"]],
            },
            "agresivo": {
                "stop_loss": vary(base_signal["stop_loss"], 0.0005),
                "take_profits": [vary(tp, 0.0015) for tp in base_signal["take_profits"]],
            },
        },
        "leverage_profiles": base_signal["leverage"],
        "timeframes": base_signal["timeframes"],
        "created_at": datetime.utcnow(),
        "valid_until": base_signal["valid_until"],
        "telegram_valid_until": base_signal["telegram_valid_until"],
        "fingerprint": secrets.token_hex(4),
        "visibility": base_signal["visibility"],
        "estimated_entry_minutes": estimated_minutes,
    }

    user_signals_collection().insert_one(user_signal)
    return user_signal

# ======================================================
# GENERAR SEÑALES POR PLAN
# ======================================================

def generate_user_signal_for_plan(base_signal: Dict):
    """
    Genera automáticamente signals para todos los usuarios que tengan acceso
    según el plan de la señal.
    """
    visibility = base_signal.get("visibility", PLAN_FREE)
    eligible_users = []

    for user in users_collection().find({}):
        user_id = user.get("user_id")
        user_plan = user.get("plan", PLAN_FREE)
        admin = is_admin(user_id)
        plan_end = user.get("plan_end")
        has_access = plan_end is None or plan_end > datetime.utcnow()

        if admin and visibility == PLAN_PREMIUM:
            eligible_users.append(user_id)
        elif not admin and user_plan == visibility and has_access:
            eligible_users.append(user_id)

    for user_id in eligible_users:
        generate_user_signal(base_signal, user_id)

# ======================================================
# FORMATEAR SEÑAL USUARIO
# ======================================================

def format_user_signal(user_signal: Dict) -> str:
    text = (
        f"📊 Señal {user_signal['symbol']}\n"
        f"Dirección: {user_signal['direction']}\n"
        f"Entrada: {user_signal['entry_price']}\n"
        f"Stop Loss: {user_signal['profiles']['conservador']['stop_loss']}\n"
        f"Take Profits: {user_signal['profiles']['conservador']['take_profits']}\n"
    )
    return text

# ======================================================
# OBTENER ÚLTIMA BASE SIGNAL PARA UN USUARIO
# ======================================================

def get_latest_base_signal_for_plan(user_id: int, user_plan: Optional[str] = None):
    if user_plan is None:
        user_plan = PLAN_FREE

    visibility = PLAN_PREMIUM if is_admin(user_id) else user_plan
    now = datetime.utcnow()

    signals = list(
        user_signals_collection()
        .find({
            "user_id": user_id,
            "visibility": visibility,
            "telegram_valid_until": {"$gt": now}
        })
        .sort("created_at", -1)
        .limit(MAX_SIGNALS_PER_QUERY)
    )

    return signals if signals else None
