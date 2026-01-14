import os
import logging
import secrets
import hashlib
import random
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import requests
from zoneinfo import ZoneInfo

from app.database import (
    signals_collection,
    user_signals_collection,
)
from app.models import new_signal
from app.plans import PLAN_FREE, PLAN_PREMIUM
from app.config import is_admin

logger = logging.getLogger(__name__)

# ======================================================
# CONFIGURACIÓN GLOBAL
# ======================================================

MARGIN_MODE = os.getenv("MARGIN_MODE", "ISOLATED")
BINANCE_FUTURES_API = os.getenv("BINANCE_FUTURES_API", "https://fapi.binance.com")
USER_TIMEZONE = os.getenv("USER_TIMEZONE", "America/Havana")
MAX_SIGNALS_PER_QUERY = int(os.getenv("MAX_SIGNALS_PER_QUERY", "10"))

BINANCE_MAX_RETRIES = int(os.getenv("BINANCE_MAX_RETRIES", "3"))
BINANCE_RETRY_DELAY = float(os.getenv("BINANCE_RETRY_DELAY", "1.0"))

LEVERAGE_PROFILES = {
    "conservador": "5x – 10x",
    "moderado": "10x – 20x",
    "agresivo": "30x – 40x",
}

# ======================================================
# TIMEFRAMES → MINUTOS
# ======================================================

TIMEFRAME_TO_MINUTES = {
    "5M": 5,
    "15M": 15,
    "1H": 60,
}

def calculate_signal_validity(timeframes: List[str]) -> int:
    minutes = [
        TIMEFRAME_TO_MINUTES.get(tf.upper(), 0)
        for tf in timeframes
    ]
    return max(minutes) if minutes else 15

# ======================================================
# PRECIO ACTUAL
# ======================================================

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
            import time
            time.sleep(BINANCE_RETRY_DELAY)

# ======================================================
# ESTIMACIÓN INTELIGENTE (NIVEL 2)
# ======================================================

def estimate_minutes_to_entry(
    symbol: str,
    entry_zone: Dict[str, str],
    timeframes: List[str],
) -> Dict[str, int]:

    try:
        current_price = get_current_price(symbol)
        zone_low = float(entry_zone["low"])
        zone_high = float(entry_zone["high"])

        if zone_low <= current_price <= zone_high:
            return {"min": 1, "max": 5}

        distance_pct = abs(
            (current_price - ((zone_low + zone_high) / 2))
            / current_price
        )

        tf_upper = [tf.upper() for tf in timeframes]

        if "5M" in tf_upper:
            speed = 0.004
            base_tf = 5
        elif "15M" in tf_upper:
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
        return {
            "min": max(1, int(base * 0.5)),
            "max": int(base * 1.5),
        }

# ======================================================
# ZONA DE ENTRADA
# ======================================================

def calculate_entry_zone(entry: float, pct: float = 0.0015):
    low = round(entry * (1 - pct), 4)
    high = round(entry * (1 + pct), 4)
    return low, high

# ======================================================
# TRACKER DE ENVÍO TELEGRAM
# ======================================================

LAST_TELEGRAM_SEND: Dict[str, datetime] = {}

def can_send_telegram(signal_id: str) -> bool:
    now = datetime.utcnow()
    last_sent = LAST_TELEGRAM_SEND.get(signal_id)
    if last_sent is None or (now - last_sent).total_seconds() >= 15:
        LAST_TELEGRAM_SEND[signal_id] = now
        return True
    return False

# ======================================================
# BLOQUEO DE CREACIÓN DE NUEVAS SEÑALES
# ======================================================

def can_create_new_signal() -> bool:
    now = datetime.utcnow()
    active_signal = signals_collection().find_one(
        {"telegram_valid_until": {"$gt": now}}
    )
    return active_signal is None

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
    block_after_creation: bool = False,  # 🔧 compatibilidad scanner.py
) -> Optional[Dict]:

    if not can_create_new_signal():
        logger.info("⏳ Señal vigente en Telegram, esperando expiración")
        return None

    if visibility is None:
        visibility = PLAN_FREE

    zone_low, zone_high = calculate_entry_zone(entry_price)

    estimated_entry_minutes = estimate_minutes_to_entry(
        symbol,
        {"low": zone_low, "high": zone_high},
        timeframes,
    )

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

    now_cuba = datetime.now(ZoneInfo(USER_TIMEZONE))
    now_utc = now_cuba.astimezone(ZoneInfo("UTC"))

    signal.update({
        "margin_mode": MARGIN_MODE,
        "created_at": now_utc,
        "valid_until": now_utc + timedelta(
            minutes=calculate_signal_validity(timeframes)
        ),
        "telegram_valid_until": now_utc + timedelta(minutes=15),
        "evaluated": False,
        "entry_zone": {
            "low": str(zone_low),
            "high": str(zone_high),
        },
        "estimated_entry_minutes": estimated_entry_minutes,
    })

    signal["_id"] = signals_collection().insert_one(signal).inserted_id

    if can_send_telegram(str(signal["_id"])):
        logger.info(f"📤 Señal enviada a Telegram: {signal['_id']}")

    return signal

# ======================================================
# SEÑAL PERSONALIZADA
# ======================================================

def generate_user_signal(base_signal: Dict, user_id: int) -> Dict:
    seed = int(
        hashlib.sha256(f"{base_signal['_id']}_{user_id}".encode()).hexdigest(),
        16
    )
    rnd = random.Random(seed)

    def vary(val: float, pct: float):
        return round(rnd.uniform(val * (1 - pct), val * (1 + pct)), 4)

    user_entry = vary(float(base_signal["entry"]), 0.0005)
    zone_low, zone_high = calculate_entry_zone(user_entry)

    estimated_entry_minutes = estimate_minutes_to_entry(
        base_signal["symbol"],
        {"low": zone_low, "high": zone_high},
        base_signal["timeframes"],
    )

    user_signal = {
        "user_id": user_id,
        "signal_id": str(base_signal["_id"]),
        "symbol": base_signal["symbol"],
        "direction": base_signal["direction"],
        "entry": user_entry,
        "entry_zone": {
            "low": zone_low,
            "high": zone_high,
        },
        "profiles": base_signal["profiles"],
        "leverage_profiles": base_signal["leverage"],
        "margin_mode": base_signal["margin_mode"],
        "timeframes": base_signal["timeframes"],
        "created_at": datetime.utcnow(),
        "valid_until": base_signal["valid_until"],
        "telegram_valid_until": base_signal["telegram_valid_until"],
        "fingerprint": secrets.token_hex(4),
        "visibility": base_signal["visibility"],
        "estimated_entry_minutes": estimated_entry_minutes,
    }

    user_signals_collection().insert_one(user_signal)
    return user_signal

# ======================================================
# VISIBILIDAD TELEGRAM
# ======================================================

def get_latest_base_signal_for_plan(
    user_id: int,
    user_plan: Optional[str] = None,
):
    if user_plan is None:
        user_plan = PLAN_FREE

    visibility = PLAN_PREMIUM if is_admin(user_id) else user_plan

    return list(
        signals_collection().find(
            {
                "visibility": visibility,
                "telegram_valid_until": {"$gt": datetime.utcnow()},
            }
        ).sort("created_at", -1).limit(MAX_SIGNALS_PER_QUERY)
    ) or None
