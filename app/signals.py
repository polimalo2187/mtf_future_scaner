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
    signal_results_collection,
    users_collection,
)
from app.models import new_signal
from app.plans import PLAN_FREE, PLAN_PLUS, PLAN_PREMIUM
from app.config import is_admin

logger = logging.getLogger(__name__)

# ======================================================
# CONFIGURACIÓN GLOBAL
# ======================================================

MARGIN_MODE = os.getenv("MARGIN_MODE", "ISOLATED")
SIGNAL_VALIDITY_MINUTES = int(os.getenv("SIGNAL_VALIDITY_MINUTES", "15"))
BINANCE_FUTURES_API = "https://fapi.binance.com"
USER_TIMEZONE = os.getenv("USER_TIMEZONE", "America/Havana")
MAX_SIGNALS_PER_QUERY = int(os.getenv("MAX_SIGNALS_PER_QUERY", "10"))

LEVERAGE_PROFILES = {
    "conservador": os.getenv("LEVERAGE_CONSERVADOR", "5x – 10x"),
    "moderado": os.getenv("LEVERAGE_MODERADO", "10x – 20x"),
    "agresivo": os.getenv("LEVERAGE_AGRESIVO", "30x – 40x"),
}

# ======================================================
# VALIDACIÓN DE PAR POR EXCHANGE
# ======================================================

def symbol_exists_in_exchange(exchange: str, symbol: str) -> bool:
    try:
        exchange = exchange.lower()

        if exchange == "binance":
            r = requests.get(f"{BINANCE_FUTURES_API}/fapi/v1/exchangeInfo", timeout=10)
            r.raise_for_status()
            symbols = {s["symbol"] for s in r.json()["symbols"]}
            return symbol in symbols

        logger.warning(f"Exchange no soportado: {exchange}")
        return False

    except Exception as e:
        logger.error(f"Error validando símbolo {symbol} en {exchange}: {e}")
        return False

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
    signal["margin_mode"] = MARGIN_MODE
    signal["created_at"] = now
    signal["valid_until"] = now + timedelta(minutes=SIGNAL_VALIDITY_MINUTES)
    signal["evaluated"] = False

    result = signals_collection().insert_one(signal)
    signal["_id"] = result.inserted_id
    return signal

# ======================================================
# SEÑAL PERSONALIZADA (FILTRO POR EXCHANGE)
# ======================================================

def generate_user_signal(base_signal: Dict, user_id: int) -> Optional[Dict]:
    user = users_collection().find_one({"user_id": user_id})
    if not user:
        return None

    exchange = user.get("exchange")
    if not exchange:
        return None

    if not symbol_exists_in_exchange(exchange, base_signal["symbol"]):
        return None

    base_id = base_signal["_id"]
    seed = int(hashlib.sha256(f"{base_id}_{user_id}".encode()).hexdigest(), 16)
    rnd = random.Random(seed)

    def vary(value: float, pct: float):
        delta = value * pct
        return round(rnd.uniform(value - delta, value + delta), 4)

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
        "signal_id": str(base_id),
        "symbol": base_signal["symbol"],
        "direction": base_signal["direction"],
        "entry": vary(float(base_signal["entry"]), 0.0005),
        "profiles": profiles,
        "leverage_profiles": base_signal["leverage"],
        "margin_mode": base_signal["margin_mode"],
        "timeframes": base_signal["timeframes"],
        "created_at": datetime.utcnow(),
        "valid_until": base_signal["valid_until"],
        "visibility": base_signal["visibility"],
        "exchange": exchange,
    }

    user_signals_collection().insert_one(user_signal)
    return user_signal

# ======================================================
# OBTENER SEÑALES SEGÚN PLAN (COMPATIBLE CON HANDLERS)
# ======================================================

def get_latest_base_signal_for_user(
    user_id: int,
    user_plan: str,
) -> Optional[List[Dict]]:

    visibility = PLAN_PREMIUM if is_admin(user_id) else user_plan

    signals = list(
        signals_collection().find(
            {
                "visibility": visibility,
                "valid_until": {"$gt": datetime.utcnow()},
            },
            sort=[("created_at", -1)],
        ).limit(MAX_SIGNALS_PER_QUERY)
    )

    return signals if signals else None


def get_latest_base_signal_for_plan(
    user_id: int,
    user_plan: Optional[str] = None,
):
    if user_plan is None:
        user_plan = PLAN_FREE
    return get_latest_base_signal_for_user(user_id, user_plan)

# ======================================================
# FORMATO DE SEÑAL (RESTABLECIDO)
# ======================================================

def format_user_signal(signal: Dict) -> str:
    tz = ZoneInfo(USER_TIMEZONE)
    start = signal["created_at"].replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
    end = signal["valid_until"].replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)

    text = (
        "NUEVA SEÑAL FUTUROS USDT\n\n"
        f"PLAN: {signal['visibility'].upper()}\n"
        f"PAR: {signal['symbol']}\n"
        f"DIRECCIÓN: {signal['direction']}\n"
        f"ENTRADA: {signal['entry']}\n\n"
    )

    for p in ["conservador", "moderado", "agresivo"]:
        text += f"{p.upper()}\n"
        text += f"SL: {signal['profiles'][p]['stop_loss']}\n"
        for i, tp in enumerate(signal["profiles"][p]["take_profits"], 1):
            text += f"TP{i}: {tp}\n"
        text += f"Leverage: {signal['leverage_profiles'][p]}\n\n"

    text += f"Activa: {start.strftime('%H:%M')} - {end.strftime('%H:%M')}"
    return text
