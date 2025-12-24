"""
MTF Futures Scanner Bot
=======================

Bot de Telegram para escaneo de mercados de futuros con señales multi-timeframe.
"""

import logging
import sys
from datetime import datetime

# ======================================================
# CONFIGURACIÓN DE LOGGING PARA EL MÓDULO
# ======================================================

def setup_logging():
    """
    Configura el logging para toda la aplicación.
    Esta función debe llamarse al inicio del programa.
    """
    # Formato del log
    log_format = (
        '%(asctime)s - %(name)s - %(levelname)s - '
        '[%(filename)s:%(lineno)d] - %(message)s'
    )
    
    # Configurar nivel de logging según entorno
    log_level = logging.INFO
    if "--debug" in sys.argv or "-d" in sys.argv:
        log_level = logging.DEBUG
    
    # Configurar logging básico
    logging.basicConfig(
        level=log_level,
        format=log_format,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(f"bot_{datetime.now().strftime('%Y%m%d')}.log")
        ]
    )
    
    # Reducir verbosidad de algunas librerías
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("pymongo").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    
    logger = logging.getLogger(__name__)
    logger.info("✅ Logging configurado (nivel: %s)", logging.getLevelName(log_level))
    
    return logger

# ======================================================
# METADATOS DEL PAQUETE
# ======================================================

__version__ = "1.0.0"
__author__ = "MTF Futures Scanner Team"
__description__ = "Bot de escaneo de mercados para futuros con señales MTF"
__license__ = "Proprietary"
__copyright__ = f"Copyright {datetime.now().year} MTF Futures Scanner"

# ======================================================
# EXPORTACIONES PRINCIPALES
# ======================================================

# Modelos
from app.models import (
    new_user,
    update_timestamp,
    activate_plan,
    is_trial_active,
    is_plan_active,
    new_referral,
    new_signal,
)

# Base de datos
from app.database import (
    get_client,
    get_db,
    users_collection,
    referrals_collection,
    signals_collection,
    user_signals_collection,
    signal_results_collection,
    check_connection,
    close_connection,
    ensure_indexes,
)

# Configuración
from app.config import (
    ADMIN_USER_IDS,
    is_admin,
    ADMIN_WHATSAPPS,
    get_admin_whatsapps,
)

# Planes
from app.plans import (
    PLAN_FREE,
    PLAN_PLUS,
    PLAN_PREMIUM,
    get_user,
    save_user,
    has_access,
    plan_status,
    activate_plus,
    activate_premium,
    expire_plans,
    extend_current_plan,
)

# Señales
from app.signals import (
    get_current_price,
    create_base_signal,
    generate_user_signal,
    evaluate_expired_signals,
    get_latest_base_signal_for_user,
    get_latest_base_signal_for_plan,
    format_user_signal,
)

# Estrategia
from app.strategy import mtf_strategy

# Escaneo
from app.scanner import scan_market

# Scheduler
from app.scheduler import scheduler_loop

# Notificaciones
from app.notifier import (
    notify_new_signal_alert,
    notify_plan_activation,
    notify_plan_expired,
)

# Handlers (para el bot)
from app.handlers import get_handlers

# Menús
from app.menus import main_menu, back_to_menu, admin_menu

# Estadísticas
from app.statistics import get_daily_stats, get_weekly_stats, get_monthly_stats

# Referidos
from app.referrals import register_valid_referral, check_ref_rewards

# ======================================================
# FUNCIÓN DE INICIALIZACIÓN
# ======================================================

def initialize_app():
    """
    Inicializa la aplicación y verifica dependencias.
    Retorna True si todo está correcto, False en caso de error.
    """
    logger = logging.getLogger(__name__)
    
    try:
        logger.info("=" * 60)
        logger.info(f"Iniciando MTF Futures Scanner v{__version__}")
        logger.info(f"Descripción: {__description__}")
        logger.info(f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 60)
        
        # Verificar conexión a base de datos
        logger.info("🔍 Verificando conexión a MongoDB...")
        if check_connection():
            logger.info("✅ Conexión a MongoDB establecida")
            
            # Verificar/crear índices
            logger.info("📊 Verificando índices de la base de datos...")
            ensure_indexes()
        else:
            logger.error("❌ No se pudo conectar a MongoDB")
            return False
        
        # Verificar variables de entorno críticas
        import os
        critical_vars = ["BOT_TOKEN", "MONGODB_URI", "DATABASE_NAME"]
        missing_vars = [var for var in critical_vars if not os.getenv(var)]
        
        if missing_vars:
            logger.error(f"❌ Variables de entorno faltantes: {missing_vars}")
            return False
        else:
            logger.info("✅ Variables de entorno críticas verificadas")
        
        logger.info("✅ Aplicación inicializada correctamente")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error durante la inicialización: {e}", exc_info=True)
        return False

# ======================================================
# MÓDULO PRINCIPAL (solo para pruebas)
# ======================================================

if __name__ == "__main__":
    # Este código solo se ejecuta si se corre este archivo directamente
    print(f"{__description__} v{__version__}")
    print(f"Por: {__author__}")
    print(f"Licencia: {__license__}")
    print()
    print("Este archivo es parte del paquete 'app'.")
    print("Para iniciar el bot, ejecuta: python main.py")
