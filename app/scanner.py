import os
import time
import logging
import asyncio
from datetime import datetime, timedelta
from typing import List, Dict

import requests
import pandas as pd
from telegram import Bot

from app.strategy import mtf_strategy
from app.signals import create_base_signal, can_create_new_signal
from app.plans import PLAN_FREE, PLAN_PLUS, PLAN_PREMIUM
from app.notifier import notify_new_signal_alert
from app.database import signals_collection

# ======================================================
# LOGGING
# ======================================================

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)

# ======================================================
# CONFIGURACIÓN GENERAL
# ======================================================

BINANCE_FUTURES_API = "https://fapi.binance.com"

SCAN_INTERVAL_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "300"))  # 5 minutos
MIN_QUOTE_VOLUME = int(os.getenv("MIN_QUOTE_VOLUME", "50000000"))  # 50M USDT
DEDUP_MINUTES = int(os.getenv("DEDUP_MINUTES", "10"))

REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "0.2"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "15"))

TELEGRAM_SIGNAL_COOLDOWN_MINUTES = 15

# ======================================================
# RATE LIMITER
# ======================================================

class RateLimiter:
    def __init__(self, delay: float):
        self.delay = delay
        self.last_request = 0.0

    def wait(self):
        elapsed = time.time() - self.last_request
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self.last_request = time.time()

rate_limiter = RateLimiter(REQUEST_DELAY)

# ======================================================
# DATA FETCH
# ======================================================

def get_klines(symbol: str, interval: str, limit: int = 200) -> pd.DataFrame:
    rate_limiter.wait()
    url = f"{BINANCE_FUTURES_API}/fapi/v1/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    df = pd.DataFrame(
        response.json(),
        columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades",
            "taker_buy_base", "taker_buy_quote", "ignore"
        ],
    )
    return df[["open", "high", "low", "close", "volume"]].astype(float)

def get_active_futures_symbols() -> List[str]:
    rate_limiter.wait()
    url = f"{BINANCE_FUTURES_API}/fapi/v1/ticker/24hr"
    response = requests.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return [
        item["symbol"]
        for item in response.json()
        if item["symbol"].endswith("USDT")
        and float(item["quoteVolume"]) >= MIN_QUOTE_VOLUME
    ]

# ======================================================
# ANTI DUPLICADOS
# ======================================================

def recent_duplicate_exists(symbol: str, direction: str, visibility: str) -> bool:
    since = datetime.utcnow() - timedelta(minutes=DEDUP_MINUTES)
    return signals_collection().find_one(
        {
            "symbol": symbol,
            "direction": direction,
            "visibility": visibility,
            "created_at": {"$gte": since},
        }
    ) is not None

# ======================================================
# SCANNER PRINCIPAL
# ======================================================

PLAN_ORDER = [PLAN_PREMIUM, PLAN_PLUS, PLAN_FREE]

async def scan_market_async(bot: Bot):
    logger.info("📡 Scanner iniciado — monitoreo activo")

    while True:
        try:
            # Bloqueo total mientras exista señal vigente en Telegram
            if not can_create_new_signal():
                logger.info("⏳ Señal vigente en Telegram, escaneo detenido")
                await asyncio.sleep(SCAN_INTERVAL_SECONDS)
                continue

            logger.info("🔄 Nuevo ciclo de escaneo")
            symbols = get_active_futures_symbols()
            candidates: List[Dict] = []

            for symbol in symbols:
                try:
                    df_1h = get_klines(symbol, "1h")
                    df_15m = get_klines(symbol, "15m")
                    df_5m = get_klines(symbol, "5m")

                    result = mtf_strategy(df_1h, df_15m, df_5m)
                    if result:
                        result["symbol"] = symbol
                        candidates.append(result)

                    await asyncio.sleep(0.05)

                except Exception:
                    continue

            if not candidates:
                logger.info("⚠️ No hay candidatos válidos")
                await asyncio.sleep(SCAN_INTERVAL_SECONDS)
                continue

            # Ordenar por score descendente
            candidates.sort(key=lambda x: x["score"], reverse=True)

            # Tomar los 3 mejores
            selected = candidates[:3]

            for signal, visibility in zip(selected, PLAN_ORDER):
                symbol = signal["symbol"]
                direction = signal["direction"]
                entry_price = float(signal["entry_price"])

                if recent_duplicate_exists(symbol, direction, visibility):
                    continue

                if direction == "LONG":
                    stop_loss = round(entry_price * 0.99, 4)
                    take_profits = [
                        round(entry_price * 1.01, 4),
                        round(entry_price * 1.02, 4),
                    ]
                else:
                    stop_loss = round(entry_price * 1.01, 4)
                    take_profits = [
                        round(entry_price * 0.99, 4),
                        round(entry_price * 0.98, 4),
                    ]

                base_signal = create_base_signal(
                    symbol=symbol,
                    direction=direction,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profits=take_profits,
                    timeframes=["5M", "15M", "1H"],
                    visibility=visibility,
                )

                if base_signal:
                    await notify_new_signal_alert(
                        bot,
                        visibility,
                        symbol=symbol,
                        direction=direction,
                        created_at=base_signal["created_at"],
                    )

            await asyncio.sleep(SCAN_INTERVAL_SECONDS)

        except Exception:
            logger.error("❌ Error crítico en scanner", exc_info=True)
            await asyncio.sleep(60)

def scan_market(bot: Bot):
    logger.info("🚀 Iniciando scanner en thread separado")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(scan_market_async(bot))
