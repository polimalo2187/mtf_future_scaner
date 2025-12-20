# app/strategy.py

import pandas as pd
from typing import Optional, Dict

import ta


# =========================
# CONFIGURACIÓN ESTRATEGIA
# =========================

EMA_FAST = 20
EMA_SLOW = 50
RSI_PERIOD = 14

RSI_MIN = 45
RSI_MAX = 55


# =========================
# UTILIDADES
# =========================

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Añade indicadores técnicos al DataFrame.
    Espera columnas: open, high, low, close, volume
    """
    df = df.copy()

    df["ema_fast"] = ta.trend.ema_indicator(df["close"], EMA_FAST)
    df["ema_slow"] = ta.trend.ema_indicator(df["close"], EMA_SLOW)
    df["rsi"] = ta.momentum.rsi(df["close"], RSI_PERIOD)

    return df


def is_trend_bullish(df: pd.DataFrame) -> bool:
    """
    Tendencia alcista básica (1H).
    """
    last = df.iloc[-1]
    return last["ema_fast"] > last["ema_slow"] and last["rsi"] > RSI_MIN


def is_trend_bearish(df: pd.DataFrame) -> bool:
    """
    Tendencia bajista básica (1H).
    """
    last = df.iloc[-1]
    return last["ema_fast"] < last["ema_slow"] and last["rsi"] < RSI_MAX


def pullback_confirmation(df: pd.DataFrame, direction: str) -> bool:
    """
    Confirmación en 15M (pullback sano).
    """
    last = df.iloc[-1]

    if direction == "LONG":
        return last["close"] > last["ema_fast"] and last["rsi"] >= RSI_MIN
    else:
        return last["close"] < last["ema_fast"] and last["rsi"] <= RSI_MAX


def entry_confirmation(df: pd.DataFrame, direction: str) -> bool:
    """
    Confirmación de entrada en 5M.
    """
    last = df.iloc[-1]

    if direction == "LONG":
        return last["ema_fast"] > last["ema_slow"] and last["rsi"] > 50
    else:
        return last["ema_fast"] < last["ema_slow"] and last["rsi"] < 50


# =========================
# ESTRATEGIA PRINCIPAL MTF
# =========================

def mtf_strategy(
    df_1h: pd.DataFrame,
    df_15m: pd.DataFrame,
    df_5m: pd.DataFrame,
) -> Optional[Dict]:
    """
    Retorna una señal si los 3 timeframes coinciden.
    """
    df_1h = add_indicators(df_1h)
    df_15m = add_indicators(df_15m)
    df_5m = add_indicators(df_5m)

    # 1H → definir tendencia
    if is_trend_bullish(df_1h):
        direction = "LONG"
    elif is_trend_bearish(df_1h):
        direction = "SHORT"
    else:
        return None

    # 15M → confirmar estructura
    if not pullback_confirmation(df_15m, direction):
        return None

    # 5M → confirmar entrada
    if not entry_confirmation(df_5m, direction):
        return None

    # Señal válida
    last_price = df_5m.iloc[-1]["close"]

    return {
        "direction": direction,
        "entry_price": round(float(last_price), 4),
  }
