# app/scanner.py

import time
import requests
import pandas as pd
from typing import List

from telegram import Bot

from app.strategy import mtf_strategy
from app.signals import create_base_signal
from app.notifier import notify_new_signal_alert
from app.plans import PLAN_PREMIUM


# ======================================================
# CONFIGURACIÓN GENERAL
# ======================================================

BINANCE_FUTURES_API = "https://fapi.binance.com"
SCAN_INTERVAL_SECONDS = 300          # 5 minutos
MIN_QUOTE_VOLUME = 50_000_000        # Filtrar pares sin liquidez


# ======================================================
# UTILIDADES DE DATOS
# ======================================================

def get_klines(symbol: str, interval: str, limit: int = 200) -> pd.DataFrame:
    """
    Descarga velas públicas desde Binance Futures.
    """
    url = f"{BINANCE_FUTURES_API}/fapi/v1/klines"
    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": limit,
    }

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

    df = df[["open", "high", "low", "close", "volume"]]
    df = df.astype(float)
    return df


def get_active_futures_symbols() -> List[str]:
    """
    Retorna lista de pares USDT con volumen suficiente.
    """
    url = f"{BINANCE_FUTURES_API}/fapi/v1/ticker/24hr"
    response = requests.get(url, timeout=10)
    response.raise_for_status()

    symbols = []
    for item in response.json():
        if (
            item["symbol"].endswith("USDT")
            and float(item["quoteVolume"]) >= MIN_QUOTE_VOLUME
        ):
            symbols.append(item["symbol"])

    return symbols


# ======================================================
# LOOP PRINCIPAL DE ESCANEO
# ======================================================

def scan_market(bot: Bot):
    """
    Loop principal:
    - Escanea mercado
    - Crea SEÑAL BASE
    - Envía ALERTA (sin datos)
    """
    print("📡 Scanner iniciado (MTF Futures Scanner)")

    while True:
        try:
            symbols = get_active_futures_symbols()
            print(f"🔎 Escaneando {len(symbols)} pares...")

            for symbol in symbols:
                try:
                    df_1h = get_klines(symbol, "1h")
                    df_15m = get_klines(symbol, "15m")
                    df_5m = get_klines(symbol, "5m")

                    result = mtf_strategy(df_1h, df_15m, df_5m)
                    if not result:
                        continue

                    direction = result["direction"]
                    entry_price = result["entry_price"]

                    # Construcción SL / TP base (conservador)
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

                    # Crear SEÑAL BASE (NO se envía al usuario)
                    base_signal = create_base_signal(
                        symbol=symbol,
                        direction=direction,
                        entry_price=entry_price,
                        stop_loss=stop_loss,
                        take_profits=take_profits,
                        timeframes=["5M", "15M", "1H"],
                        visibility=PLAN_PREMIUM,
                    )

                    # Enviar ALERTA (sin Entry/TP/SL)
                    try:
                        notify_new_signal_alert(bot, base_signal["visibility"])
                    except Exception:
                        pass

                    print(f"✅ Señal base creada y alerta enviada: {symbol} {direction}")

                except Exception as e:
                    print(f"⚠️ Error procesando {symbol}: {e}")

            time.sleep(SCAN_INTERVAL_SECONDS)

        except Exception as e:
            print(f"❌ Error crítico en scanner: {e}")
            time.sleep(60)
