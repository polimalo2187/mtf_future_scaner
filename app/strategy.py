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
    df = df.copy()

    df["ema_fast"] = ta.trend.ema_indicator(df["close"], EMA_FAST)
    df["ema_slow"] = ta.trend.ema_indicator(df["close"], EMA_SLOW)
    df["rsi"] = ta.momentum.rsi(df["close"], RSI_PERIOD)

    return df


def is_trend_bullish(df: pd.DataFrame) -> bool:
    last = df.iloc[-1]
    return last["ema_fast"] > last["ema_slow"] and last["rsi"] > RSI_MIN


def is_trend_bearish(df: pd.DataFrame) -> bool:
    last = df.iloc[-1]
    return last["ema_fast"] < last["ema_slow"] and last["rsi"] < RSI_MAX


def pullback_confirmation(df: pd.DataFrame, direction: str) -> bool:
    last = df.iloc[-1]

    if direction == "LONG":
        return last["close"] > last["ema_fast"] and last["rsi"] >= RSI_MIN
    else:
        return last["close"] < last["ema_fast"] and last["rsi"] <= RSI_MAX


def entry_confirmation(df: pd.DataFrame, direction: str) -> bool:
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
    Retorna una señal con SCORE (fuerza).
    """

    df_1h = add_indicators(df_1h)
    df_15m = add_indicators(df_15m)
    df_5m = add_indicators(df_5m)

    score = 0

    # =====================
    # 1H → Tendencia
    # =====================
    if is_trend_bullish(df_1h):
        direction = "LONG"
        score += 30
    elif is_trend_bearish(df_1h):
        direction = "SHORT"
        score += 30
    else:
        return None

    # =====================
    # 15M → Pullback
    # =====================
    if pullback_confirmation(df_15m, direction):
        score += 30
    else:
        return None

    # =====================
    # 5M → Entrada
    # =====================
    if entry_confirmation(df_5m, direction):
        score += 30
    else:
        return None

    # =====================
    # BONUS → Momentum limpio
    # =====================
    rsi_5m = df_5m.iloc[-1]["rsi"]
    if direction == "LONG" and rsi_5m > 60:
        score += 10
    elif direction == "SHORT" and rsi_5m < 40:
        score += 10

    last_price = df_5m.iloc[-1]["close"]

    return {
        "direction": direction,
        "entry_price": round(float(last_price), 4),
        "score": score,  # 🔥 CLAVE PARA ORO / PLATA / BRONCE
  }
