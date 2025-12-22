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
    Retorna una señal con SCORE (fuerza) calculado de manera proporcional.
    """

    df_1h = add_indicators(df_1h)
    df_15m = add_indicators(df_15m)
    df_5m = add_indicators(df_5m)

    score = 0
    max_score = 100  # Score máximo posible
    score_components = []

    # =====================
    # 1H → Tendencia
    # =====================
    if is_trend_bullish(df_1h):
        direction = "LONG"
        trend_score = 30
        score += trend_score
        score_components.append(("trend", trend_score))
    elif is_trend_bearish(df_1h):
        direction = "SHORT"
        trend_score = 30
        score += trend_score
        score_components.append(("trend", trend_score))
    else:
        return None

    # =====================
    # 15M → Pullback
    # =====================
    if pullback_confirmation(df_15m, direction):
        pullback_score = 30
        score += pullback_score
        score_components.append(("pullback", pullback_score))
    else:
        return None

    # =====================
    # 5M → Entrada
    # =====================
    if entry_confirmation(df_5m, direction):
        entry_score = 30
        score += entry_score
        score_components.append(("entry", entry_score))
    else:
        return None

    # =====================
    # BONUS → Momentum limpio
    # =====================
    rsi_5m = df_5m.iloc[-1]["rsi"]
    bonus_score = 0
    if direction == "LONG" and rsi_5m > 60:
        bonus_score = 10
        score += bonus_score
    elif direction == "SHORT" and rsi_5m < 40:
        bonus_score = 10
        score += bonus_score

    if bonus_score:
        score_components.append(("bonus", bonus_score))

    # =====================
    # Ajuste final para garantizar que no supere 100
    # =====================
    if score > max_score:
        score = max_score

    last_price = df_5m.iloc[-1]["close"]

    return {
        "direction": direction,
        "entry_price": round(float(last_price), 4),
        "score": score,                  # Score proporcional realista
        "score_components": score_components,  # Para debugging / análisis
      }
