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
# ZONA DE ENTRADA
# ======================================================

def calculate_entry_zone(entry: float, pct: float = 0.0015):
    low = round(entry * (1 - pct), 4)
    high = round(entry * (1 + pct), 4)
    return low, high

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

    zone_low, zone_high = calculate_entry_zone(entry_price)

    signal = new_signal(
        symbol=symbol,
        direction=direction,
        entry=str(entry_price),  # compatibilidad
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
        "evaluated": False,
        "entry_zone": {
            "low": str(zone_low),
            "high": str(zone_high),
        }
    })

    signal["_id"] = signals_collection().insert_one(signal).inserted_id
    return signal

# ======================================================
# SEÑAL PERSONALIZADA (CLAVE)
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
        "profiles": profiles,
        "leverage_profiles": base_signal["leverage"],
        "margin_mode": base_signal["margin_mode"],
        "timeframes": base_signal["timeframes"],
        "created_at": datetime.utcnow(),
        "valid_until": base_signal["valid_until"],
        "fingerprint": secrets.token_hex(4),
        "visibility": base_signal["visibility"],
    }

    user_signals_collection().insert_one(user_signal)
    return user_signal

# ======================================================
# FORMATO FINAL DE MENSAJE
# ======================================================

def format_user_signal(signal: Dict) -> str:
    tz = ZoneInfo(USER_TIMEZONE)

    start = signal["created_at"].replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
    end = signal["valid_until"].replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)

    max_tf = max(
        signal["timeframes"],
        key=lambda tf: TIMEFRAME_TO_MINUTES.get(tf.upper(), 0)
    )

    text = (
        "📊 NUEVA SEÑAL – FUTUROS USDT\n\n"
        f"🏷️ PLAN: {signal['visibility'].upper()}\n\n"
        f"Par: {signal['symbol']}\n"
        f"Dirección: {signal['direction']}\n"
        f"Zona de entrada: {signal['entry_zone']['low']} – {signal['entry_zone']['high']}\n\n"
        f"Margen: {signal['margin_mode']}\n"
        f"Timeframes: {' / '.join(signal['timeframes'])}\n\n"
    )

    for p in ["conservador", "moderado", "agresivo"]:
        text += "━━━━━━━━━━━━━━━━━━\n"
        text += f"{p.upper()}\n"
        text += f"SL: {signal['profiles'][p]['stop_loss']}\n"
        for i, tp in enumerate(signal["profiles"][p]["take_profits"], 1):
            text += f"TP{i}: {tp}\n"
        text += f"Apalancamiento: {signal['leverage_profiles'][p]}\n\n"

    text += (
        f"⏳ Vigencia: basada en {max_tf} "
        f"({start.strftime('%H:%M')} → {end.strftime('%H:%M')})\n"
        f"🔐 ID: {signal['fingerprint']}"
    )

    return text
