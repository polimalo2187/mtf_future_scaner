# app/signals.py

import os
import logging
import secrets
import hashlib
import random
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import requests
from zoneinfo import ZoneInfo

from app.database import (
    signals_collection,
    user_signals_collection,
    signal_results_collection,
)
from app.models import new_signal
from app.plans import PLAN_FREE, PLAN_PLUS, PLAN_PREMIUM
from app.config import is_admin

logger = logging.getLogger(__name__)

# ======================================================
# CONFIGURACIÓN GLOBAL (VARIABLES DE ENTORNO)
# ======================================================

MARGIN_MODE = os.getenv("MARGIN_MODE", "ISOLATED")
SIGNAL_VALIDITY_MINUTES = int(os.getenv("SIGNAL_VALIDITY_MINUTES", "15"))
BINANCE_FUTURES_API = os.getenv("BINANCE_FUTURES_API", "https://fapi.binance.com")
USER_TIMEZONE = os.getenv("USER_TIMEZONE", "America/Havana")

# Número máximo de señales a devolver por consulta
MAX_SIGNALS_PER_QUERY = int(os.getenv("MAX_SIGNALS_PER_QUERY", "10"))

# Configuración de reintentos para API
BINANCE_MAX_RETRIES = int(os.getenv("BINANCE_MAX_RETRIES", "3"))
BINANCE_RETRY_DELAY = float(os.getenv("BINANCE_RETRY_DELAY", "1.0"))

LEVERAGE_PROFILES = {
    "conservador": os.getenv("LEVERAGE_CONSERVADOR", "5x – 10x"),
    "moderado": os.getenv("LEVERAGE_MODERADO", "10x – 20x"),
    "agresivo": os.getenv("LEVERAGE_AGRESIVO", "30x – 40x"),
}

# ======================================================
# PRECIO ACTUAL CON REINTENTOS
# ======================================================

def get_current_price(symbol: str) -> float:
    """
    Obtiene el precio actual con reintentos y manejo de errores.
    """
    url = f"{BINANCE_FUTURES_API}/fapi/v1/ticker/price"
    
    for attempt in range(BINANCE_MAX_RETRIES):
        try:
            response = requests.get(
                url,
                params={"symbol": symbol},
                timeout=10,
                headers={"User-Agent": "MTF-Futures-Scanner/1.0"}
            )
            response.raise_for_status()
            
            data = response.json()
            return float(data["price"])
            
        except requests.exceptions.RequestException as e:
            if attempt == BINANCE_MAX_RETRIES - 1:
                logger.error(f"❌ Error obteniendo precio para {symbol}: {e}")
                raise
            logger.warning(f"⚠️ Reintentando obtener precio para {symbol} (intento {attempt + 1})")
            import time
            time.sleep(BINANCE_RETRY_DELAY * (2 ** attempt))  # Exponential backoff

# ======================================================
# CREAR SEÑAL BASE CON VALIDACIÓN
# ======================================================

def create_base_signal(
    symbol: str,
    direction: str,
    entry_price: float,
    stop_loss: float,
    take_profits: List[float],
    timeframes: List[str],
    visibility: str,
) -> Dict:
    
    # Validar inputs
    if visibility not in (PLAN_FREE, PLAN_PLUS, PLAN_PREMIUM):
        raise ValueError(f"Visibilidad inválida: {visibility}")
    
    if direction not in ("LONG", "SHORT"):
        raise ValueError(f"Dirección inválida: {direction}")
    
    if not symbol or not isinstance(symbol, str):
        raise ValueError(f"Símbolo inválido: {symbol}")
    
    # Crear señal base
    signal = new_signal(
        symbol=symbol,
        direction=direction,
        entry=str(entry_price),
        stop_loss=str(stop_loss),
        take_profits=[str(tp) for tp in take_profits],
        timeframes=timeframes,
        visibility=visibility,
        leverage=LEVERAGE_PROFILES,
    )
    
    now = datetime.utcnow()
    signal["margin_mode"] = MARGIN_MODE
    signal["created_at"] = now
    signal["valid_until"] = now + timedelta(minutes=SIGNAL_VALIDITY_MINUTES)
    signal["evaluated"] = False
    
    # Insertar en base de datos
    result = signals_collection().insert_one(signal)
    signal["_id"] = result.inserted_id
    
    logger.info(f"✅ Señal base creada: {symbol} {direction} (visibilidad: {visibility})")
    
    return signal

# ======================================================
# SEÑAL PERSONALIZADA THREAD-SAFE ✅ CORREGIDO
# ======================================================

def generate_user_signal(base_signal: Dict, user_id: int) -> Dict:
    """
    Genera una señal personalizada para un usuario específico.
    Thread-safe y determinística por usuario.
    """
    base_id = base_signal.get("_id")
    if not base_id:
        raise ValueError("base_signal no tiene _id")
    
    # ✅ CORRECCIÓN CRÍTICA: Crear una instancia local de random para thread safety
    seed_str = f"{base_id}_{user_id}"
    seed_int = int(hashlib.sha256(seed_str.encode()).hexdigest(), 16)
    local_random = random.Random(seed_int)  # ✅ Random local, no global
    
    def vary(value: float, percent: float) -> float:
        """Variar un valor dentro de un porcentaje."""
        delta = value * percent
        return round(local_random.uniform(value - delta, value + delta), 4)  # ✅ Usar local_random
    
    # Variar entrada base
    entry = vary(float(base_signal["entry"]), 0.0005)
    
    # Perfiles de riesgo con variaciones diferentes
    profiles = {
        "conservador": {
            "stop_loss": vary(float(base_signal["stop_loss"]), 0.002),
            "take_profits": [vary(float(tp), 0.0005) for tp in base_signal["take_profits"]],
        },
        "moderado": {
            "stop_loss": vary(float(base_signal["stop_loss"]), 0.001),
            "take_profits": [vary(float(tp), 0.001) for tp in base_signal["take_profits"]],
        },
        "agresivo": {
            "stop_loss": vary(float(base_signal["stop_loss"]), 0.0005),
            "take_profits": [vary(float(tp), 0.0015) for tp in base_signal["take_profits"]],
        },
    }
    
    # Fingerprint - manteniendo MD5 para compatibilidad pero añadiendo más entropía
    fingerprint = hashlib.md5(
        f"{user_id}_{base_id}_{secrets.token_hex(4)}".encode()  # ✅ Añadida entropía extra
    ).hexdigest()[:8]
    
    user_signal = {
        "user_id": user_id,
        "signal_id": str(base_id),
        "symbol": base_signal["symbol"],
        "direction": base_signal["direction"],
        "entry": entry,
        "profiles": profiles,
        "leverage_profiles": base_signal["leverage"],
        "margin_mode": base_signal["margin_mode"],
        "timeframes": base_signal["timeframes"],
        "created_at": datetime.utcnow(),
        "valid_until": base_signal["valid_until"],
        "fingerprint": fingerprint,
        "visibility": base_signal.get("visibility", PLAN_FREE),
    }
    
    user_signals_collection().insert_one(user_signal)
    logger.debug(f"📝 Señal personalizada para usuario {user_id}: {fingerprint}")
    
    return user_signal

# ======================================================
# EVALUAR SEÑALES EXPIRADAS CON BATCH PROCESSING
# ======================================================

def evaluate_expired_signals():
    """
    Evalúa señales expiradas en lotes para evitar sobrecarga.
    """
    now = datetime.utcnow()
    
    # Usar batch processing
    batch_size = 50
    processed = 0
    
    try:
        # Obtener señales expiradas en lotes
        expired_signals = signals_collection().find(
            {
                "valid_until": {"$lt": now}, 
                "evaluated": False
            }
        ).limit(batch_size)
        
        for signal in expired_signals:
            try:
                price = get_current_price(signal["symbol"])
                entry = float(signal["entry"])
                stop_loss = float(signal["stop_loss"])
                tps = [float(tp) for tp in signal["take_profits"]]
                
                result = "expired"
                
                if signal["direction"] == "LONG":
                    if price <= stop_loss:
                        result = "lost"
                    elif any(price >= tp for tp in tps):
                        result = "won"
                else:
                    if price >= stop_loss:
                        result = "lost"
                    elif any(price <= tp for tp in tps):
                        result = "won"
                
                # Registrar resultado
                signal_results_collection().insert_one(
                    {
                        "signal_id": str(signal["_id"]),
                        "symbol": signal["symbol"],
                        "direction": signal["direction"],
                        "result": result,
                        "visibility": signal["visibility"],
                        "created_at": signal["created_at"],
                        "evaluated_at": now,
                    }
                )
                
                # Marcar como evaluada
                signals_collection().update_one(
                    {"_id": signal["_id"]},
                    {"$set": {"evaluated": True}},
                )
                
                processed += 1
                
            except Exception as e:
                logger.error(f"❌ Error evaluando señal {signal.get('_id')}: {e}")
                continue
        
        if processed > 0:
            logger.info(f"📊 Señales evaluadas: {processed}")
            
    except Exception as e:
        logger.error(f"❌ Error en evaluate_expired_signals: {e}")

# ======================================================
# OBTENER SEÑALES POR PLAN / ADMIN CON LÍMITES
# ======================================================

def get_latest_base_signal_for_user(
    user_id: int,
    user_plan: str,
) -> Optional[List[Dict]]:

    # Determinar visibilidad según plan
    if is_admin(user_id):
        visibility = [PLAN_FREE, PLAN_PLUS, PLAN_PREMIUM]
    elif user_plan == PLAN_FREE:
        visibility = [PLAN_FREE]
    elif user_plan == PLAN_PLUS:
        visibility = [PLAN_FREE, PLAN_PLUS]
    else:
        visibility = [PLAN_FREE, PLAN_PLUS, PLAN_PREMIUM]

    # ✅ Añadido límite para evitar traer demasiadas señales
    signals = list(
        signals_collection().find(
            {
                "visibility": {"$in": visibility},
                "valid_until": {"$gt": datetime.utcnow()},
            },
            sort=[("created_at", -1)],
        ).limit(MAX_SIGNALS_PER_QUERY)
    )
    
    return signals if signals else None

# ======================================================
# 🔒 COMPATIBILIDAD CON HANDLERS (FIX CRASH)
# ======================================================

def get_latest_base_signal_for_plan(
    user_id: int,
    user_plan: Optional[str] = None,
):
    """
    Alias de compatibilidad para imports antiguos.
    Admin y usuarios antiguos pueden llamar sin romper.
    """
    if user_plan is None:
        # Asumimos FREE si no se pasa (seguridad)
        user_plan = PLAN_FREE
    return get_latest_base_signal_for_user(user_id, user_plan)

# ======================================================
# FORMATO FINAL PARA USUARIO
# ======================================================

def format_user_signal(signal: Dict) -> str:
    """Formatea una señal para mostrar al usuario."""
    try:
        cuba_tz = ZoneInfo(USER_TIMEZONE)
        
        start_cuba = signal["created_at"].replace(
            tzinfo=ZoneInfo("UTC")
        ).astimezone(cuba_tz)
        
        end_cuba = signal["valid_until"].replace(
            tzinfo=ZoneInfo("UTC")
        ).astimezone(cuba_tz)
        
        plan = signal.get("visibility", "").upper()
        plan_line = f"🏷️ PLAN: {plan}\n\n" if plan else ""
        
        # Construir texto base
        text = (
            "📊 NUEVA SEÑAL – FUTUROS USDT\n\n"
            f"{plan_line}"
            f"Par: {signal['symbol']}\n"
            f"Dirección: {signal['direction']}\n"
            f"Entrada base: {signal['entry']}\n\n"
            f"Margen: {signal['margin_mode']}\n"
            f"Timeframes: {' / '.join(signal['timeframes'])}\n\n"
        )
        
        # Añadir perfiles
        for profile in ["conservador", "moderado", "agresivo"]:
            text += "━━━━━━━━━━━━━━━━━━\n"
            text += f"{profile.upper()}\n"
            text += f"SL: {signal['profiles'][profile]['stop_loss']}\n"
            for i, tp in enumerate(signal["profiles"][profile]["take_profits"], 1):
                text += f"TP{i}: {tp}\n"
            text += f"Apalancamiento: {signal['leverage_profiles'][profile]}\n\n"
        
        # Añadir información de tiempo y ID
        text += (
            f"⏳ Señal activa: "
            f"{start_cuba.strftime('%H:%M')} → {end_cuba.strftime('%H:%M')} "
            f"(Hora {USER_TIMEZONE.split('/')[-1].replace('_', ' ')})\n"
            f"🔐 Signal ID: {signal['fingerprint']}"
        )
        
        # Verificar que no exceda el límite de Telegram (4096 caracteres)
        if len(text) > 4000:
            logger.warning(f"⚠️ Señal muy larga ({len(text)} caracteres), truncando...")
            text = text[:4000] + "..."
        
        return text
        
    except Exception as e:
        logger.error(f"❌ Error formateando señal: {e}")
        return "📊 NUEVA SEÑAL – FUTUROS USDT\n\n❌ Error al formatear la señal. Intenta nuevamente."
