# app/signals.py

from datetime import datetime
from typing import List, Dict, Optional

from app.database import signals_collection
from app.models import new_signal
from app.plans import PLAN_FREE, PLAN_PLUS, PLAN_PREMIUM


# =========================
# CONSTANTES
# =========================

DEFAULT_LEVERAGE = {
    "conservative": "5x-10x",
    "moderate": "10x-20x",
    "aggressive": "30x-40x",
}

MARGIN_MODE = "isolated"  # fijo, siempre aislado


# =========================
# CREAR Y GUARDAR SEÑALES
# =========================

def create_signal(
    symbol: str,
    direction: str,
    entry: str,
    stop_loss: str,
    take_profits: List[str],
    timeframes: List[str],
    visibility: str,
    leverage: Optional[Dict[str, str]] = None,
) -> Dict:
    """
    Crea y guarda una señal en MongoDB.
    """
    if visibility not in (PLAN_FREE, PLAN_PLUS, PLAN_PREMIUM):
        raise ValueError("Visibilidad inválida para la señal")

    signal_doc = new_signal(
        symbol=symbol,
        direction=direction,
        entry=entry,
        stop_loss=stop_loss,
        take_profits=take_profits,
        timeframes=timeframes,
        visibility=visibility,
        leverage=leverage or DEFAULT_LEVERAGE,
    )

    signal_doc["margin_mode"] = MARGIN_MODE
    signal_doc["created_at"] = datetime.utcnow()

    signals_collection().insert_one(signal_doc)
    return signal_doc


# =========================
# OBTENER SEÑALES POR PLAN
# =========================

def get_latest_signal_for_plan(plan: str) -> Optional[Dict]:
    """
    Retorna la última señal visible según el plan del usuario.
    - FREE: solo señales free
    - PLUS: señales free y plus
    - PREMIUM: todas
    """
    if plan == PLAN_FREE:
        visibility_filter = [PLAN_FREE]
    elif plan == PLAN_PLUS:
        visibility_filter = [PLAN_FREE, PLAN_PLUS]
    else:
        visibility_filter = [PLAN_FREE, PLAN_PLUS, PLAN_PREMIUM]

    return signals_collection().find_one(
        {"visibility": {"$in": visibility_filter}},
        sort=[("created_at", -1)],
    )


# =========================
# FORMATEO PARA TELEGRAM
# =========================

def format_signal_for_telegram(signal: Dict) -> str:
    """
    Devuelve el texto final de la señal para Telegram.
    """
    tps = "\n".join([f"TP{i+1}: {tp}" for i, tp in enumerate(signal["take_profits"])])

    leverage = signal.get("leverage", DEFAULT_LEVERAGE)

    text = (
        "NUEVA SEÑAL – FUTUROS USDT\n\n"
        f"Par: {signal['symbol']}\n"
        f"Dirección: {signal['direction']}\n\n"
        "Zona de entrada:\n"
        f"{signal['entry']}\n\n"
        "Take Profit:\n"
        f"{tps}\n\n"
        "Stop Loss:\n"
        f"{signal['stop_loss']}\n\n"
        "Apalancamiento sugerido:\n"
        f"Conservador: {leverage.get('conservative')}\n"
        f"Moderado: {leverage.get('moderate')}\n"
        f"Agresivo: {leverage.get('aggressive')}\n\n"
        "Tipo de margen:\n"
        "AISLADO\n\n"
        "Timeframes:\n"
        f"{' / '.join(signal['timeframes'])}"
    )

    return text


# =========================
# UTILIDAD (ANTI-DUPLICADOS)
# =========================

def last_signal_for_symbol(symbol: str) -> Optional[Dict]:
    """
    Retorna la última señal creada para un símbolo específico.
    Útil para evitar señales duplicadas.
    """
    return signals_collection().find_one(
        {"symbol": symbol},
        sort=[("created_at", -1)],
  )
