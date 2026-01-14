import os
import time
import logging
import asyncio
from datetime import datetime, timedelta
from typing import List, Dict

import requests
import pandas as pd
from telegram import Bot

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

SCAN_INTERVAL_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "300"))
MIN_QUOTE_VOLUME = int(os.getenv("MIN_QUOTE_VOLUME", "50000000"))
DEDUP_MINUTES = int(os.getenv("DEDUP_MINUTES", "10"))

REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "0.2"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "15"))

PLAN_ORDER = ["premium", "plus", "free"]  # usar los nombres de tus planes reales

# ======================================================
# RATE LIMITER ASYNC
# ======================================================
class AsyncRateLimiter:
    def __init__(self, delay: float):
        self.delay = delay
        self.last_request = 0.0

    async def wait(self):
        elapsed = time.time() - self.last_request
        if elapsed < self.delay:
            await asyncio.sleep(self.delay - elapsed)
        self.last_request = time.time()

rate_limiter = AsyncRateLimiter(REQUEST_DELAY)

# ======================================================
# DATA FETCH
# ======================================================
async def get_klines(symbol: str, interval: str, limit: int = 200) -> pd.DataFrame:
    await rate_limiter.wait()
    url = f"{BINANCE_FUTURES_API}/fapi/v1/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    try:
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
    except Exception as e:
        logger.warning(f"Error obteniendo klines para {symbol}: {e}")
        return pd.DataFrame()

async def get_active_futures_symbols() -> List[str]:
    await rate_limiter.wait()
    url = f"{BINANCE_FUTURES_API}/fapi/v1/ticker/24hr"
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return [
            item["symbol"]
            for item in response.json()
            if item["symbol"].endswith("USDT")
            and float(item["quoteVolume"]) >= MIN_QUOTE_VOLUME
        ]
    except Exception as e:
        logger.warning(f"Error obteniendo símbolos activos: {e}")
        return []

# ======================================================
# ANTI DUPLICADOS
# ======================================================
def recent_duplicate_exists(symbol: str, direction: str, visibility: str) -> bool:
    try:
        from app.database import signals_collection  # import local
        since = datetime.utcnow() - timedelta(minutes=DEDUP_MINUTES)
        return signals_collection().find_one(
            {
                "symbol": symbol,
                "direction": direction,
                "visibility": visibility,
                "created_at": {"$gte": since},
            }
        ) is not None
    except Exception as e:
        logger.warning(f"Error verificando duplicados para {symbol}: {e}")
        return False

# ======================================================
# SCANNER PRINCIPAL ASYNC
# ======================================================
async def scan_market_async(bot: Bot):
    logger.info("📡 Scanner iniciado — monitoreo activo")

    # Import local para evitar circular import
    from app.strategy import mtf_strategy
    from app.signals import create_base_signal, can_create_new_signal
    from app.notifier import notify_new_signal_alert

    while True:
        try:
            # Bloqueo global si hay señal Free activa
            if not can_create_new_signal():
                logger.info("⏳ Señal FREE activa en Telegram — scanner en espera")
                await asyncio.sleep(SCAN_INTERVAL_SECONDS)
                continue

            logger.info("🔄 Nuevo ciclo de escaneo")
            symbols = await get_active_futures_symbols()
            if not symbols:
                logger.info("⚠️ No se obtuvieron símbolos activos")
                await asyncio.sleep(SCAN_INTERVAL_SECONDS)
                continue

            candidates: List[Dict] = []

            for symbol in symbols:
                try:
                    df_1h = await get_klines(symbol, "1h")
                    df_15m = await get_klines(symbol, "15m")
                    df_5m = await get_klines(symbol, "5m")

                    if df_1h.empty or df_15m.empty or df_5m.empty:
                        continue

                    result = mtf_strategy(df_1h, df_15m, df_5m)
                    if result and "entry_price" in result and "direction" in result:
                        result["symbol"] = symbol
                        candidates.append(result)

                    await asyncio.sleep(0.05)

                except Exception as e:
                    logger.warning(f"Error procesando {symbol}: {e}")
                    continue

            if not candidates:
                logger.info("⚠️ No hay candidatos válidos")
                await asyncio.sleep(SCAN_INTERVAL_SECONDS)
                continue

            # Ordenar por score descendente y tomar hasta 3 señales
            candidates.sort(key=lambda x: x.get("score", 0), reverse=True)
            selected = candidates[:3]

            for idx, signal in enumerate(selected):
                try:
                    visibility = PLAN_ORDER[idx] if idx < len(PLAN_ORDER) else "free"
                    symbol = signal["symbol"]
                    direction = signal["direction"]

                    entry_price = float(signal.get("entry_price", 0))
                    if entry_price <= 0:
                        logger.warning(f"Señal ignorada por precio inválido: {signal}")
                        continue

                    if recent_duplicate_exists(symbol, direction, visibility):
                        logger.info(f"Duplicado detectado: {symbol} {direction} {visibility}")
                        continue

                    # Calcular stop loss y take profits
                    if direction == "LONG":
                        stop_loss = round(entry_price * 0.99, 4)
                        take_profits = [round(entry_price * 1.01, 4), round(entry_price * 1.02, 4)]
                    else:
                        stop_loss = round(entry_price * 1.01, 4)
                        take_profits = [round(entry_price * 0.99, 4), round(entry_price * 0.98, 4)]

                    # Crear la señal
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

                except Exception as e:
                    logger.warning(f"Error enviando señal {signal.get('symbol')}: {e}")
                    continue

            await asyncio.sleep(SCAN_INTERVAL_SECONDS)

        except Exception as e:
            logger.error(f"❌ Error crítico en scanner: {e}", exc_info=True)
            await asyncio.sleep(5)  # No bloquear demasiado

# ======================================================
# START SCANNER
# ======================================================
def scan_market(bot: Bot):
    logger.info("🚀 Iniciando scanner en thread separado")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(scan_market_async(bot))
