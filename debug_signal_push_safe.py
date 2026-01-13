# debug_signal_push_safe.py
import logging
from datetime import datetime

from app.database import signals_collection, users_collection
from app.notifier import _eligible_users_for_alert
from app.signal_service import can_create_new_signal

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("DEBUG_SIGNAL_PUSH_SAFE")

# ======================================
# 1️⃣ Revisar señales activas
# ======================================
now = datetime.utcnow()
active_signals = list(signals_collection().find({"telegram_valid_until": {"$gt": now}}))

logger.info(f"Señales activas actualmente: {len(active_signals)}")
for s in active_signals:
    logger.info(f"ID: {s['_id']}, Plan: {s['visibility']}, Vigencia Telegram: {s['telegram_valid_until']}")

# ======================================
# 2️⃣ Revisar si se puede crear nueva señal
# ======================================
can_create = can_create_new_signal()
if can_create:
    logger.info("✅ Se puede crear nueva señal (no hay bloqueo activo)")
else:
    logger.warning("⏸️ NO se puede crear nueva señal: hay una señal vigente en Telegram")

# ======================================
# 3️⃣ Revisar usuarios elegibles por plan
# ======================================
plans = ["free", "plus", "premium"]
for plan in plans:
    eligible_users = _eligible_users_for_alert(plan)
    if eligible_users:
        logger.info(f"Usuarios elegibles para el plan '{plan}': {eligible_users}")
    else:
        logger.warning(f"⚠️ Ningún usuario elegible para el plan '{plan}'")

# ======================================
# 4️⃣ Simulación de envío de push
# ======================================
logger.info("=== Simulando envío de push (sin enviar mensajes reales) ===")
for plan in plans:
    active_for_plan = [s for s in active_signals if s['visibility'] == plan]
    if active_for_plan:
        logger.warning(f"⏸️ Push bloqueado para '{plan}': existe señal activa")
    elif not _eligible_users_for_alert(plan):
        logger.warning(f"⚠️ Push NO enviado para '{plan}': no hay usuarios elegibles")
    else:
        logger.info(f"✅ Push DISPONIBLE para '{plan}': se enviaría a usuarios elegibles")

logger.info("=== DEBUG COMPLETADO ===")
