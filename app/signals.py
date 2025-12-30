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

BINANCE_MAX_RETRIES = int(os.getenv("BINANCE_MAX_RETRIES", "3"))
BINANCE_RETRY_DELAY = float(os.getenv("BINANCE_RETRY_DELAY", "1.0"))

LEVERAGE_PROFILES = {
    "conservador": os.getenv("LEVERAGE_CONSERVADOR", "5x – 10x"),
    "moderado": os.getenv("LEVERAGE_MODERADO", "10x – 20x"),
    "agresivo": os.getenv("LEVERAGE_AGRESIVO", "30x – 40x"),
}

# ======================================================
# VALIDACIÓN DE PAR POR EXCHANGE
# ======================================================

def symbol_exists_in_exchange(exchange: str, symbol: str) -> bool:
    """
    Verifica si un símbolo existe en el exchange del usuario
    """
    try:
        exchange = exchange.lower()

        if exchange == "binance":
            r = requests.get(
                f"{BINANCE_FUTURES_API}/fapi/v1/exchangeInfo",
                timeout=10,
            )
            r.raise_for_status()
            symbols = {s["symbol"] for s in r.json()["symbols"]}
            return symbol in symbols

        if exchange == "bybit":
            r = requests.get(
                "https://api.bybit.com/v5/market/instruments-info",
                params={"category": "linear"},
                timeout=10,
            )
            r.raise_for_status()
            symbols = {s["symbol"] for s in r.json()["result"]["list"]}
            return symbol in symbols

        if exchange == "okx":
            r = requests.get(
                "https://www.okx.com/api/v5/public/instruments",
                params={"instType": "SWAP"},
                timeout=10,
            )
            r.raise_for_status()
            symbols = {s["instId"].replace("-", "") for s in r.json()["data"]}
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
# SEÑAL PERSONALIZADA (CON FILTRO POR EXCHANGE)
# ======================================================

def generate_user_signal(base_signal: Dict, user_id: int) -> Optional[Dict]:
    user = users_collection().find_one({"user_id": user_id})

    if not user:
        return None

    user_exchange = user.get("exchange")

    if not user_exchange:
        logger.warning(f"Usuario {user_id} sin exchange registrado")
        return None

    if not symbol_exists_in_exchange(user_exchange, base_signal["symbol"]):
        logger.info(
            f"Señal {base_signal['symbol']} descartada para usuario {user_id} "
            f"(no existe en {user_exchange})"
        )
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

    fingerprint = hashlib.md5(
        f"{user_id}_{base_id}_{secrets.token_hex(4)}".encode()
    ).hexdigest()[:8]

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
        "fingerprint": fingerprint,
        "visibility": base_signal["visibility"],
        "exchange": user_exchange,
    }

    user_signals_collection().insert_one(user_signal)
    return user_signal

# ======================================================
# OBTENER SEÑALES SEGÚN PLAN
# ======================================================

def get_latest_base_signal_for_user(
    user_id: int,
    user_plan: str,
) -> Optional[List[Dict]]:

    if is_admin(user_id):
        visibility = PLAN_PREMIUM
    else:
        visibility = user_plan

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
