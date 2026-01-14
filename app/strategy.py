import pandas as pd
from typing import Optional, Dict
import ta

# =========================
# CONFIGURACIÓN
# =========================

EMA_FAST = 20
EMA_SLOW = 50

RSI_PERIOD = 14
RSI_TREND_MIN = 50
RSI_PULLBACK_MIN = 45
RSI_PULLBACK_MAX = 55

MAX_SCORE = 100

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
# CONDICIONES DE MERCADO
# =========================

def is_trend_bullish(df: pd.DataFrame) -> bool:
    last = df.iloc[-1]
    return last["ema_fast"] > last["ema_slow"] and last["rsi"] >= RSI_TREND_MIN

def is_trend_bearish(df: pd.DataFrame) -> bool:
    last = df.iloc[-1]
    return last["ema_fast"] < last["ema_slow"] and last["rsi"] <= RSI_TREND_MIN

def pullback_confirmation(df: pd.DataFrame, direction: str) -> bool:
    last = df.iloc[-1]
    if direction == "LONG":
        return last["close"] >= last["ema_fast"] and RSI_PULLBACK_MIN <= last["rsi"] <= RSI_PULLBACK_MAX
    else:
        return last["close"] <= last["ema_fast"] and RSI_PULLBACK_MIN <= last["rsi"] <= RSI_PULLBACK_MAX

def entry_confirmation(df: pd.DataFrame, direction: str) -> bool:
    last = df.iloc[-1]
    if direction == "LONG":
        return last["ema_fast"] > last["ema_slow"] and last["rsi"] > RSI_TREND_MIN
    else:
        return last["ema_fast"] < last["ema_slow"] and last["rsi"] < RSI_TREND_MIN

# =========================
# ESTRATEGIA MTF
# =========================

def mtf_strategy(
    df_1h: pd.DataFrame,
    df_15m: pd.DataFrame,
    df_5m: pd.DataFrame,
) -> Optional[Dict]:
    """
    Detecta oportunidades reales y devuelve un score comparable.
    NO clasifica Oro/Plata/Bronce.
    """

    df_1h = add_indicators(df_1h)
    df_15m = add_indicators(df_15m)
    df_5m = add_indicators(df_5m)

    score = 0
    components = []

    # =====================
    # 1H → Tendencia
    # =====================

    if is_trend_bullish(df_1h):
        direction = "LONG"
        score += 35
        components.append(("trend_1h", 35))
    elif is_trend_bearish(df_1h):
        direction = "SHORT"
        score += 35
        components.append(("trend_1h", 35))
    else:
        return None

    # =====================
    # 15M → Pullback limpio
    # =====================

    if pullback_confirmation(df_15m, direction):
        score += 30
        components.append(("pullback_15m", 30))
    else:
        return None

    # =====================
    # 5M → Entrada precisa
    # =====================

    if not entry_confirmation(df_5m, direction):
        return None

    last = df_5m.iloc[-1]

    # Distancia normalizada (independiente del precio)
    distance_pct = abs(last["close"] - last["ema_fast"]) / last["close"]
    entry_score = max(0, 30 - distance_pct * 3000)
    entry_score = min(30, entry_score)

    score += entry_score
    components.append(("entry_5m", round(entry_score, 2)))

    # =====================
    # BONUS → Momentum fuerte
    # =====================

    bonus = 0
    if direction == "LONG" and last["rsi"] > 60:
        bonus = 5
    elif direction == "SHORT" and last["rsi"] < 40:
        bonus = 5

    if bonus:
        score += bonus
        components.append(("momentum_bonus", bonus))

    score = max(0, min(score, MAX_SCORE))

    return {
        "direction": direction,
        "entry_price": round(float(last["close"]), 4),
        "score": round(score, 2),
        "components": components,
      }
