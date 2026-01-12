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

# 🔔 TELEGRAM TTL (FIJO)
TELEGRAM_SIGNAL_TTL_MINUTES = 15

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

    signal.update({
        "margin_mode": MARGIN_MODE,
        "created_at": now,
        "valid_until": now + timedelta(
            minutes=calculate_signal_validity(timeframes)
        ),
        # 🔔 TELEGRAM TTL
        "telegram_valid_until": now + timedelta(
            minutes=TELEGRAM_SIGNAL_TTL_MINUTES
        ),
        "evaluated": False,
    })

    signal["_id"] = signals_collection().insert_one(signal).inserted_id
    return signal

# ======================================================
# FUNCIÓN CRÍTICA (TELEGRAM FILTRO)
# ======================================================

def get_latest_base_signal_for_plan(
    user_id: int,
    user_plan: Optional[str] = None,
):
    if user_plan is None:
        user_plan = PLAN_FREE

    visibility = PLAN_PREMIUM if is_admin(user_id) else user_plan

    now = datetime.utcnow()

    signals = list(
        signals_collection().find(
            {
                "visibility": visibility,
                # 🔔 SOLO SEÑALES VIGENTES EN TELEGRAM
                "telegram_valid_until": {"$gt": now},
            }
        ).sort("created_at", -1).limit(MAX_SIGNALS_PER_QUERY)
    )

    return signals if signals else None
