import pandas as pd
from typing import Optional, Dict
import ta

# =========================
# CONFIGURACIÓN AGRESIVA – OPTIMIZADA
# =========================

EMA_FAST = 20
EMA_SLOW = 50

RSI_PERIOD = 14
RSI_TREND_MIN = 48

# Se amplia el rango para evitar descartar señales por ligera oscilación
RSI_PULLBACK_MIN = 38
RSI_PULLBACK_MAX = 62

MAX_SCORE = 100

# Entrada temprana pero real (ajustada)
# Se amplía el rango de breakout para capturar señales rápidas
MAX_DISTANCE_PCT = 0.005  # 0.5%
MIN_BREAKOUT_PCT = 0.0005  # 0.05%

# =========================
# INDICADORES
# =========================

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema_fast"] = ta.trend.ema_indicator(df["close"], EMA_FAST)
    df["ema_slow"] = ta.trend.ema_indicator(df["close"], EMA_SLOW)
    df["rsi"] = ta.momentum.rsi(df["close"], RSI_PERIOD)
    return df

# =========================
# TENDENCIA
# =========================

def is_trend_bullish(df: pd.DataFrame) -> bool:
    last = df.iloc[-1]
    return last["ema_fast"] > last["ema_slow"] and last["rsi"] >= RSI_TREND_MIN

def is_trend_bearish(df: pd.DataFrame) -> bool:
    last = df.iloc[-1]
    return last["ema_fast"] < last["ema_slow"] and last["rsi"] <= RSI_TREND_MIN

# =========================
# PULLBACK CORTO
# =========================

def pullback_confirmation(df: pd.DataFrame, direction: str) -> bool:
    last = df.iloc[-1]

    if direction == "LONG":
        return (
            last["close"] >= last["ema_fast"] * 0.998
            and RSI_PULLBACK_MIN <= last["rsi"] <= RSI_PULLBACK_MAX
        )
    else:
        return (
            last["close"] <= last["ema_fast"] * 1.002
            and RSI_PULLBACK_MIN <= last["rsi"] <= RSI_PULLBACK_MAX
        )

# =========================
# ENTRADA CONFIRMADA
# =========================

def entry_confirmation(df: pd.DataFrame, direction: str) -> bool:
    last = df.iloc[-1]

    if direction == "LONG":
        return last["close"] > last["ema_fast"]
    else:
        return last["close"] < last["ema_fast"]

# =========================
# ESTRATEGIA MTF ULTRA FINA
# =========================

def mtf_strategy(
    df_1h: pd.DataFrame,
    df_15m: pd.DataFrame,
    df_5m: pd.DataFrame,
) -> Optional[Dict]:

    df_1h = add_indicators(df_1h)
    df_15m = add_indicators(df_15m)
    df_5m = add_indicators(df_5m)

    score = 0
    components = []

    # ========= 1H – DIRECCIÓN =========

    if is_trend_bullish(df_1h):
        direction = "LONG"
        score += 35
    elif is_trend_bearish(df_1h):
        direction = "SHORT"
        score += 35
    else:
        return None

    # ========= 15M – PULLBACK =========

    if not pullback_confirmation(df_15m, direction):
        return None

    score += 30

    # ========= 5M – TIMING =========

    if not entry_confirmation(df_5m, direction):
        return None

    last = df_5m.iloc[-1]

    # ===== DISTANCIA DIRECCIONAL REAL =====

    if direction == "LONG":
        distance_pct = (last["close"] - last["ema_fast"]) / last["ema_fast"]
        if distance_pct < MIN_BREAKOUT_PCT:
            return None
    else:
        distance_pct = (last["ema_fast"] - last["close"]) / last["ema_fast"]
        if distance_pct < MIN_BREAKOUT_PCT:
            return None

    if distance_pct > MAX_DISTANCE_PCT:
        return None

    entry_score = max(0, 30 - distance_pct * 6000)
    score += entry_score

    # ========= BONUS MOMENTUM =========

    if direction == "LONG" and last["rsi"] > 58:
        score += 5
    elif direction == "SHORT" and last["rsi"] < 42:
        score += 5

    score = min(score, MAX_SCORE)

    entry_price = round(float(last["ema_fast"]), 4)

    return {
        "direction": direction,
        "entry_price": entry_price,
        "score": round(score, 2),
        "components": components,
  }
