# app/scanner.py

import time
import requests
import pandas as pd
from typing import List, Dict, Optional, Tuple
import asyncio
from datetime import datetime, timedelta

from telegram import Bot

from app.strategy import mtf_strategy
from app.signals import create_base_signal
from app.plans import PLAN_FREE, PLAN_PLUS, PLAN_PREMIUM
from app.notifier import notify_new_signal_alert
from app.database import signals_collection


# ======================================================
# CONFIGURACIÓN GENERAL
# ======================================================

BINANCE_FUTURES_API = "https://fapi.binance.com"
SCAN_INTERVAL_SECONDS = 300          # 5 minutos
MIN_QUOTE_VOLUME = 50_000_000        # Filtrar pares sin liquidez

# Evita duplicar señales iguales muy seguido (anti-spam DB)
DEDUP_MINUTES = 10


# ======================================================
# UTILIDADES DE DATOS
# ======================================================

def get_klines(symbol: str, interval: str, limit: int = 200) -> pd.DataFrame:
    """
    Descarga velas públicas desde Binance Futures.
    """
    url = f"{BINANCE_FUTURES_API}/fapi/v1/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}

    response = requests.get(url, params=params, timeout=10)
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


def get_active_futures_symbols() -> List[str]:
    """
    Retorna lista de pares USDT con volumen suficiente.
    """
    url = f"{BINANCE_FUTURES_API}/fapi/v1/ticker/24hr"
    response = requests.get(url, timeout=10)
    response.raise_for_status()

    symbols: List[str] = []
    for item in response.json():
        if item["symbol"].endswith("USDT") and float(item["quoteVolume"]) >= MIN_QUOTE_VOLUME:
            symbols.append(item["symbol"])
    return symbols


# ======================================================
# ANTI-DUPLICADO (DB)
# ======================================================

def _recent_duplicate_exists(symbol: str, direction: str, visibility: str) -> bool:
    """
    Evita crear la MISMA señal repetida en poco tiempo.
    """
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
# LOOP PRINCIPAL DE ESCANEO (ORO / PLATA / BRONCE)
# ======================================================

def scan_market(bot: Bot):
    """
    Loop principal:
    - Escanea mercado
    - Encuentra TOP 3 mejores señales por score
    - Reparte:
        🥇 PREMIUM (oro)
        🥈 PLUS (plata)
        🥉 FREE (bronce)
    - Crea SOLO esas 3 señales
    - Envía PUSH a usuarios del plan correspondiente + aviso admin
    """
    print("📡 Scanner iniciado (MTF Futures Scanner)")

    # Crear un event loop propio para este thread (evita usar asyncio.run repetidamente)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    while True:
        try:
            symbols = get_active_futures_symbols()
            print(f"🔎 Escaneando {len(symbols)} pares...")

            candidates: List[Tuple[int, str, str, float]] = []
            # (score, symbol, direction, entry_price)

            for symbol in symbols:
                try:
                    df_1h = get_klines(symbol, "1h")
                    df_15m = get_klines(symbol, "15m")
                    df_5m = get_klines(symbol, "5m")

                    result = mtf_strategy(df_1h, df_15m, df_5m)
                    if not result:
                        continue

                    direction = result["direction"]
                    entry_price = float(result["entry_price"])
                    score = int(result.get("score", 0))

                    # Guardar candidato
                    candidates.append((score, symbol, direction, entry_price))

                except Exception as e:
                    print(f"⚠️ Error procesando {symbol}: {e}")

            # Si no hay candidatos, dormir
            if not candidates:
                print("📭 No se detectaron señales fuertes en esta ronda.")
                time.sleep(SCAN_INTERVAL_SECONDS)
                continue

            # Ordenar por score desc y tomar top 3
            candidates.sort(key=lambda x: x[0], reverse=True)
            top3 = candidates[:3]

            # Asignación oro/plata/bronce
            plan_map = [
                (PLAN_PREMIUM, "🥇 ORO"),
                (PLAN_PLUS, "🥈 PLATA"),
                (PLAN_FREE, "🥉 BRONCE"),
            ]

            for idx, (score, symbol, direction, entry_price) in enumerate(top3):
                visibility, medal = plan_map[idx]

                # Evitar duplicados recientes por plan
                if _recent_duplicate_exists(symbol, direction, visibility):
                    print(f"⏭️ Duplicado reciente omitido: {symbol} {direction} ({visibility})")
                    continue

                # Construcción SL / TP base
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

                # Crear señal base (con visibilidad del plan asignado)
                base_signal = create_base_signal(
                    symbol=symbol,
                    direction=direction,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profits=take_profits,
                    timeframes=["5M", "15M", "1H"],
                    visibility=visibility,
                )

                # 🔔 PUSH a usuarios del plan + mensaje admin (tu notifier ya hace eso)
                try:
                    loop.run_until_complete(
                        notify_new_signal_alert(
                            bot,
                            base_signal["visibility"],
                            symbol=base_signal["symbol"],
                            direction=base_signal["direction"],
                            created_at=base_signal["created_at"],
                        )
                    )
                except Exception as e:
                    print(f"⚠️ Error enviando notificación: {e}")

                print(f"✅ {medal} creada: {symbol} {direction} | score={score} | plan={visibility}")

            time.sleep(SCAN_INTERVAL_SECONDS)

        except Exception as e:
            print(f"❌ Error crítico en scanner: {e}")
            time.sleep(60)
