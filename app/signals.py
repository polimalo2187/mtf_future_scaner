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
BINANCE_FUTURES_API = os.getenv("BINANCE_FUTURES_API", "https://fapi.binance.com")
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
# OBTENER PRECIO ACTUAL
# ======================================================

def get_current_price(symbol: str) -> float:
    url = f"{BINANCE_FUTURES_API}/fapi/v1/ticker/price"
    for attempt in range(BINANCE_MAX_RETRIES):
        try:
            response = requests.get(
                url,
                params={"symbol": symbol},
                timeout=10,
                headers={"User-Agent": "MTF-Futures-Scanner/1.0"}
            )
            response.raise_for_status()
            return float(response.json()["price"])
        except requests.exceptions.RequestException as e:
            if attempt == BINANCE_MAX_RETRIES - 1:
                logger.error(f"❌ Error obteniendo precio para {symbol}: {e}")
                raise
            import time
            time.sleep(BINANCE_RETRY_DELAY * (2 ** attempt))

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
    supported_exchanges: Optional[List[str]] = None,
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
    # Guardamos todos los exchanges en minúsculas
    signal["supported_exchanges"] = [ex.lower() for ex in (supported_exchanges or ["binance"])]

    result = signals_collection().insert_one(signal)
    signal["_id"] = result.inserted_id
    return signal

# ======================================================
# GENERAR SEÑAL PERSONALIZADA PARA USUARIO
# ======================================================

def generate_user_signal(base_signal: Dict, user: Dict) -> Dict:
    user_id = user["user_id"]
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
        "exchange": user.get("exchange", "binance"),
    }

    user_signals_collection().insert_one(user_signal)
    return user_signal

# ======================================================
# OBTENER SEÑAL SEGÚN PLAN Y EXCHANGE
# ======================================================

def get_latest_base_signal_for_user(user: Dict) -> Optional[List[Dict]]:
    """
    Obtiene las señales base activas para un usuario según su plan y exchange.
    Normaliza exchange en minúsculas para evitar problemas de mayúsculas/minúsculas.
    """
    user_id = user["user_id"]
    user_plan = user.get("plan", PLAN_FREE)
    user_exchange = (user.get("exchange") or "binance").lower()

    visibility = PLAN_PREMIUM if is_admin(user_id) else user_plan

    # Uso de regex case-insensitive para que funcione con cualquier mayúscula/minúscula
    signals = list(
        signals_collection().find(
            {
                "visibility": visibility,
                "valid_until": {"$gt": datetime.utcnow()},
                "supported_exchanges": {"$elemMatch": {"$regex": f"^{user_exchange}$", "$options": "i"}},
            },
            sort=[("created_at", -1)],
        ).limit(MAX_SIGNALS_PER_QUERY)
    )

    return signals if signals else None

# ======================================================
# COMPATIBILIDAD CON HANDLERS
# ======================================================

def get_latest_base_signal_for_plan(user: Dict):
    return get_latest_base_signal_for_user(user)

# ======================================================
# FORMATO DE SEÑAL PARA ENVÍO AL USUARIO
# ======================================================

def format_user_signal(signal: Dict) -> str:
    cuba_tz = ZoneInfo(USER_TIMEZONE)

    start = signal["created_at"].replace(tzinfo=ZoneInfo("UTC")).astimezone(cuba_tz)
    end = signal["valid_until"].replace(tzinfo=ZoneInfo("UTC")).astimezone(cuba_tz)

    text = (
        "📊 NUEVA SEÑAL – FUTUROS USDT\n\n"
        f"🏷️ PLAN: {signal['visibility'].upper()}\n"
        f"🌐 Exchange: {signal.get('exchange', 'binance')}\n\n"
        f"Par: {signal['symbol']}\n"
        f"Dirección: {signal['direction']}\n"
        f"Entrada base: {signal['entry']}\n\n"
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
        f"⏳ Activa: {start.strftime('%H:%M')} → {end.strftime('%H:%M')}\n"
        f"🔐 ID: {signal['fingerprint']}"
    )

    return text
