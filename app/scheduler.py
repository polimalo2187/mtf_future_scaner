# app/scheduler.py

import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

from telegram import Bot

from app.database import users_collection
from app.plans import PLAN_FREE, expire_plans
from app.notifier import notify_plan_expired

logger = logging.getLogger(__name__)

# ======================================================
# CONFIGURACIÓN (VARIABLES DE ENTORNO)
# ======================================================

CHECK_INTERVAL_SECONDS = int(os.getenv("SCHEDULER_CHECK_INTERVAL", "300"))  # 5 min por defecto
BATCH_SIZE = int(os.getenv("SCHEDULER_BATCH_SIZE", "100"))  # Usuarios por batch
MAX_RETRIES = 3

# ======================================================
# TAREA: EXPIRACIÓN DE PLANES
# ======================================================

async def check_expired_plans(bot: Bot) -> int:
    """
    Revisa planes vencidos y notifica al usuario.
    Retorna el número de usuarios procesados.
    """
    users_col = users_collection()
    now = datetime.utcnow()
    
    # ✅ Consulta optimizada: solo usuarios con plan activo que hayan expirado
    # Usar batch processing para no sobrecargar MongoDB
    expired_users = users_col.find(
        {
            "plan_end": {"$lt": now, "$ne": None},
            "plan": {"$ne": PLAN_FREE}  # Solo usuarios con plan activo
        }
    ).limit(BATCH_SIZE)
    
    processed_count = 0
    
    for user in expired_users:
        try:
            user_id = user["user_id"]
            
            # Actualizar a FREE
            result = users_col.update_one(
                {"user_id": user_id},
                {
                    "$set": {
                        "plan": PLAN_FREE,
                        "plan_end": None,
                        "updated_at": now,
                    }
                }
            )
            
            if result.modified_count > 0:
                logger.info(f"📋 Plan expirado para usuario {user_id}, actualizado a FREE")
                
                # Notificar al usuario (con reintentos)
                for attempt in range(MAX_RETRIES):
                    try:
                        await notify_plan_expired(bot, user_id)
                        logger.info(f"✅ Notificado usuario {user_id} sobre plan expirado")
                        break
                    except Exception as e:
                        if attempt == MAX_RETRIES - 1:
                            logger.error(f"❌ Error notificando usuario {user_id}: {e}")
                        else:
                            await asyncio.sleep(2 ** attempt)  # Exponential backoff
                
                processed_count += 1
                
        except Exception as e:
            logger.error(f"❌ Error procesando usuario {user.get('user_id', 'unknown')}: {e}")
    
    return processed_count

# ======================================================
# TAREAS ADICIONALES (OPCIONALES)
# ======================================================

async def cleanup_old_signals():
    """Limpia señales antiguas de la base de datos."""
    try:
        from app.database import signals_collection, user_signals_collection
        
        # Señales base: mantener 7 días
        cutoff_date = datetime.utcnow() - timedelta(days=7)
        
        result_base = signals_collection().delete_many({
            "created_at": {"$lt": cutoff_date}
        })
        
        # Señales de usuario: mantener 3 días
        cutoff_user = datetime.utcnow() - timedelta(days=3)
        result_user = user_signals_collection().delete_many({
            "created_at": {"$lt": cutoff_user}
        })
        
        if result_base.deleted_count > 0 or result_user.deleted_count > 0:
            logger.info(f"🧹 Limpieza: {result_base.deleted_count} señales base, "
                       f"{result_user.deleted_count} señales usuario")
            
    except Exception as e:
        logger.error(f"❌ Error en cleanup_old_signals: {e}")

async def send_daily_stats(bot: Bot):
    """Envía estadísticas diarias a los admins."""
    try:
        from app.config import ADMIN_USER_IDS
        from app.database import users_collection, signals_collection
        
        today = datetime.utcnow().date()
        today_start = datetime.combine(today, datetime.min.time())
        
        # Estadísticas básicas
        total_users = users_collection().count_documents({})
        new_users_today = users_collection().count_documents({
            "created_at": {"$gte": today_start}
        })
        signals_today = signals_collection().count_documents({
            "created_at": {"$gte": today_start}
        })
        
        # Usuarios activos hoy (con actividad)
        active_users_today = users_collection().count_documents({
            "last_activity": {"$gte": today_start}
        })
        
        stats_message = (
            f"📊 Estadísticas diarias\n\n"
            f"👥 Usuarios totales: {total_users}\n"
            f"🆕 Nuevos hoy: {new_users_today}\n"
            f"📡 Señales hoy: {signals_today}\n"
            f"🏃 Usuarios activos hoy: {active_users_today}\n"
            f"⏰ Generado: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
        )
        
        # Enviar a cada admin
        sent_count = 0
        for admin_id in ADMIN_USER_IDS:
            try:
                await bot.send_message(chat_id=admin_id, text=stats_message)
                sent_count += 1
            except Exception as e:
                logger.error(f"❌ Error enviando stats a admin {admin_id}: {e}")
        
        if sent_count > 0:
            logger.info(f"📨 Estadísticas enviadas a {sent_count} admins")
                
    except Exception as e:
        logger.error(f"❌ Error en send_daily_stats: {e}")

async def check_database_health():
    """Verifica la salud de la base de datos."""
    try:
        from app.database import get_client
        
        client = get_client()
        
        # Verificar conexión
        client.admin.command('ping')
        
        # Verificar colecciones
        db = client.get_default_database()
        collections = db.list_collection_names()
        
        required_collections = ['users', 'signals', 'user_signals', 'referrals', 'signal_results']
        missing = [col for col in required_collections if col not in collections]
        
        if missing:
            logger.warning(f"⚠️ Colecciones faltantes: {missing}")
        else:
            logger.debug("✅ Base de datos saludable")
            
        return True
        
    except Exception as e:
        logger.error(f"❌ Error en check_database_health: {e}")
        return False

# ======================================================
# LOOP PRINCIPAL CON SHUTDOWN ELEGANTE
# ======================================================

async def scheduler_loop(bot: Bot):
    """Loop principal del scheduler."""
    logger.info("⏰ Scheduler iniciado correctamente")
    
    iteration = 0
    errors_in_row = 0
    max_errors_in_row = 5
    
    while True:
        try:
            # Tarea principal: revisar planes expirados
            processed = await check_expired_plans(bot)
            if processed > 0:
                logger.info(f"📋 Procesados {processed} planes expirados")
            
            # Cada hora: limpiar señales antiguas (5 min * 12 = 60 min)
            if iteration % 12 == 0:
                await cleanup_old_signals()
            
            # Cada 6 horas: verificar salud de base de datos (5 min * 72 = 6 horas)
            if iteration % 72 == 0:
                await check_database_health()
            
            # Cada día: enviar estadísticas (5 min * 288 = 24 horas)
            if iteration % 288 == 0:
                await send_daily_stats(bot)
            
            iteration += 1
            errors_in_row = 0  # Reset error counter
            
            # Esperar intervalo
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)
            
        except asyncio.CancelledError:
            logger.info("🛑 Scheduler cancelado")
            break
        except Exception as e:
            errors_in_row += 1
            logger.error(f"❌ Error en scheduler loop (error #{errors_in_row}): {e}", exc_info=True)
            
            if errors_in_row >= max_errors_in_row:
                logger.critical(f"🚨 Demasiados errores consecutivos ({errors_in_row}), reiniciando scheduler...")
                # Pequeño delay antes de continuar
                await asyncio.sleep(60)
                errors_in_row = 0
            else:
                # Esperar antes de reintentar
                await asyncio.sleep(30)
