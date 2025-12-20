# app/scanner.py

import time
import requests
import pandas as pd
from typing import List

from app.strategy import mtf_strategy
from app.signals import create_signal, last_signal_for_symbol
from app.plans import PLAN_PLUS, PLAN_PREMIUM


# =========================
# CONFIGURACIÓN GENERAL
# =========================

BINANCE_FUTURES_API = "https://fapi.binance.com"
SCAN_INTERVAL_SECONDS = 300  # 5 minutos
MIN_QUOTE_VOLUME = 50_000_000  # filtrar pares sin liquidez


# =========================
# UTILIDADES DE DATOS
# =========================

def get_klines(symbol: str, interval: str, limit: int = 200) -> pd.DataFrame:
    """
    Descarga velas desde Binance Futures (datos públicos).
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

    df = pd.DataFrame(data, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades",
        "taker_buy_base", "taker_buy_quote", "ignore"
    ])

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


# =========================
# LOOP PRINCIPAL DE ESCANEO
# =========================

def scan_market():
    """
    Loop principal de escaneo del mercado.
    """
    print("Scanner iniciado: MTF Futures Scanner")

    while True:
        try:
            symbols = get_active_futures_symbols()
            print(f"Escaneando {len(symbols)} pares...")

            for symbol in symbols:
                try:
                    df_1h = get_klines(symbol, "1h")
                    df_15m = get_klines(symbol, "15m")
                    df_5m = get_klines(symbol, "5m")

                    result = mtf_strategy(df_1h, df_15m, df_5m)
                    if not result:
                        continue

                    # Evitar señales duplicadas
                    last_signal = last_signal_for_symbol(symbol)
                    if last_signal:
                        last_price = float(last_signal["entry"].split("-")[0].strip())
                        if abs(last_price - result["entry_price"]) < (last_price * 0.001):
                            continue

                    direction = result["direction"]
                    entry_price = result["entry_price"]

                    # Construcción simple de SL y TP (base)
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

                    create_signal(
                        symbol=symbol,
                        direction=direction,
                        entry=f"{entry_price}",
                        stop_loss=str(stop_loss),
                        take_profits=[str(tp) for tp in take_profits],
                        timeframes=["5M", "15M", "1H"],
                        visibility=PLAN_PREMIUM,  # señales completas
                    )

                    print(f"Señal creada: {symbol} {direction}")

                except Exception as e:
                    print(f"Error procesando {symbol}: {e}")

            time.sleep(SCAN_INTERVAL_SECONDS)

        except Exception as e:
            print(f"Error en scanner principal: {e}")
            time.sleep(60)
