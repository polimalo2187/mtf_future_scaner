# app/scanner.py

import time
import logging
import random
import asyncio
import threading
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
import requests
import pandas as pd

from telegram import Bot

from app.strategy import mtf_strategy
from app.signals import create_base_signal
from app.plans import PLAN_FREE, PLAN_PLUS, PLAN_PREMIUM
from app.notifier import notify_new_signal_alert
from app.database import signals_collection

# Configurar logging
logger = logging.getLogger(__name__)

# ======================================================
# CONFIGURACIÓN GENERAL (VARIABLES DE ENTORNO)
# ======================================================

BINANCE_FUTURES_API = "https://fapi.binance.com"
SCAN_INTERVAL_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "1800"))  # ⏱️ 30 minutos por defecto
MIN_QUOTE_VOLUME = int(os.getenv("MIN_QUOTE_VOLUME", "50000000"))  # 50M por defecto
DEDUP_MINUTES = int(os.getenv("DEDUP_MINUTES", "10"))  # Anti-duplicados

# Rate limiting
REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "0.2"))  # 200ms entre requests
MAX_REQUESTS_PER_MINUTE = int(os.getenv("MAX_REQUESTS_PER_MINUTE", "100"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "15"))

# ======================================================
# UTILIDADES DE DATOS CON RATE LIMITING
# ======================================================

class RateLimiter:
    """Simple rate limiter para las llamadas a Binance API."""
    def __init__(self, delay=REQUEST_DELAY):
        self.delay = delay
        self.last_request = 0
    
    def wait(self):
        """Espera el tiempo necesario para respetar el rate limit."""
        elapsed = time.time() - self.last_request
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self.last_request = time.time()

rate_limiter = RateLimiter()

def get_klines(symbol: str, interval: str, limit: int = 200) -> pd.DataFrame:
    """Obtiene klines de Binance con rate limiting."""
    rate_limiter.wait()
    
    url = f"{BINANCE_FUTURES_API}/fapi/v1/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}

    try:
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()

        df = pd.DataFrame(
            data,
            columns=[
                "open_time", "open", "high", "low", "close", "volume",
                "close_time", "quote_volume", "trades",
                "taker_buy_base", "taker_buy_quote", "ignore"
            ],
        )

        df = df[["open", "high", "low", "close", "volume"]].astype(float)
        return df
        
    except requests.exceptions.Timeout:
        logger.error(f"⏱️ Timeout obteniendo klines para {symbol} {interval}")
        raise
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Error obteniendo klines para {symbol} {interval}: {e}")
        raise

def get_active_futures_symbols() -> List[str]:
    """Obtiene símbolos activos con suficiente volumen."""
    rate_limiter.wait()
    
    url = f"{BINANCE_FUTURES_API}/fapi/v1/ticker/24hr"
    
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        
        symbols: List[str] = []
        for item in response.json():
            if item["symbol"].endswith("USDT") and float(item["quoteVolume"]) >= MIN_QUOTE_VOLUME:
                symbols.append(item["symbol"])
        
        logger.info(f"📊 {len(symbols)} símbolos con volumen suficiente")
        return symbols
        
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Error obteniendo símbolos activos: {e}")
        return []

# ======================================================
# ANTI-DUPLICADO (DB)
# ======================================================

def _recent_duplicate_exists(symbol: str, direction: str, visibility: str) -> bool:
    """Verifica si ya existe una señal similar reciente."""
    since = datetime.utcnow() - timedelta(minutes=DEDUP_MINUTES)
    doc = signals_collection().find_one(
        {
            "symbol": symbol,
            "direction": direction,
            "visibility": visibility,
            "created_at": {"$gte": since},
        },
        sort=[("created_at", -1)],
    )
    return doc is not None

# ======================================================
# LOOP PRINCIPAL DE ESCANEO - CORREGIDO PARA ASYNCIO
# ======================================================

async def scan_market_async(bot: Bot):
    """Loop principal de escaneo en asyncio."""
    logger.info("📡 Scanner async iniciado (MTF Futures Scanner)")
    
    while True:
        try:
            symbols = get_active_futures_symbols()
            
            if not symbols:
                logger.warning("📭 No se obtuvieron símbolos activos")
                await asyncio.sleep(SCAN_INTERVAL_SECONDS)
                continue
            
            logger.info(f"🔎 Escaneando {len(symbols)} pares...")
            
            candidates: List[Tuple[int, str, str, float]] = []
            processed = 0
            errors = 0

            # Procesar símbolos con limitación de concurrencia
            for symbol in symbols:
                try:
                    # Obtener datos para diferentes timeframes
                    df_1h = get_klines(symbol, "1h")
                    df_15m = get_klines(symbol, "15m")
                    df_5m = get_klines(symbol, "5m")

                    # Aplicar estrategia
                    result = mtf_strategy(df_1h, df_15m, df_5m)
                    
                    if result:
                        direction = result["direction"]
                        entry_price = float(result["entry_price"])
                        score = int(result.get("score", 0))
                        
                        # Solo considerar señales con score suficiente
                        if score >= 30:  # Umbral mínimo
                            candidates.append((score, symbol, direction, entry_price))
                    
                    processed += 1
                    
                    # Pequeña pausa entre símbolos para no saturar
                    await asyncio.sleep(0.05)
                    
                except Exception as e:
                    errors += 1
                    logger.debug(f"⚠️ Error procesando {symbol}: {e}")
                    continue

            logger.info(f"✅ Procesados: {processed}, Errores: {errors}, Señales: {len(candidates)}")
            
            if not candidates:
                logger.info("📭 No se detectaron señales fuertes en esta ronda.")
                await asyncio.sleep(SCAN_INTERVAL_SECONDS)
                continue

            # Ordenar por score y tomar las mejores
            candidates.sort(key=lambda x: x[0], reverse=True)
            top_signals = candidates[:3]  # Máximo 3 señales por ronda
            
            # Mapear a planes según posición (mejor score = plan más alto)
            plan_map = []
            if len(top_signals) >= 1:
                plan_map.append((PLAN_PREMIUM, "🥇 ORO"))
            if len(top_signals) >= 2:
                plan_map.append((PLAN_PLUS, "🥈 PLATA"))
            if len(top_signals) >= 3:
                plan_map.append((PLAN_FREE, "🥉 BRONCE"))

            # Crear señales para las mejores
            created_count = 0
            for idx, (score, symbol, direction, entry_price) in enumerate(top_signals):
                if idx >= len(plan_map):
                    break
                    
                visibility, medal = plan_map[idx]

                # Verificar duplicados
                if _recent_duplicate_exists(symbol, direction, visibility):
                    logger.debug(f"⏭️ Duplicado omitido: {symbol} {direction} ({visibility})")
                    continue

                # Calcular stop loss y take profits
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

                # Crear señal base
                base_signal = create_base_signal(
                    symbol=symbol,
                    direction=direction,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profits=take_profits,
                    timeframes=["5M", "15M", "1H"],
                    visibility=visibility,
                )

                # Notificar a usuarios
                try:
                    await notify_new_signal_alert(
                        bot,
                        base_signal["visibility"],
                        symbol=base_signal["symbol"],
                        direction=base_signal["direction"],
                        created_at=base_signal["created_at"],
                    )
                except Exception as e:
                    logger.error(f"⚠️ Error enviando notificación: {e}")

                logger.info(f"✅ {medal} creada: {symbol} {direction} | score={score} | plan={visibility}")
                created_count += 1

            if created_count > 0:
                logger.info(f"🎯 {created_count} señales creadas en esta ronda")
            
            # Esperar hasta el próximo ciclo
            await asyncio.sleep(SCAN_INTERVAL_SECONDS)

        except Exception as e:
            logger.error(f"❌ Error crítico en scanner: {e}", exc_info=True)
            # Esperar un minuto antes de reintentar en caso de error crítico
            await asyncio.sleep(60)

def scan_market(bot: Bot):
    """
    Función de compatibilidad que ejecuta el scanner async en un thread.
    Esta es la función que llama bot.py
    """
    logger.info("🚀 Iniciando scanner en thread separado...")
    
    # Crear un nuevo event loop para este thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        # Ejecutar el loop async
        loop.run_until_complete(scan_market_async(bot))
    except KeyboardInterrupt:
        logger.info("🛑 Scanner detenido por usuario")
    except Exception as e:
        logger.error(f"❌ Scanner falló: {e}", exc_info=True)
    finally:
        # Limpiar el loop
        loop.close()
        logger.info("🔌 Loop del scanner cerrado")
