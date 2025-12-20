# app/scheduler.py

import asyncio
from datetime import datetime
from telegram import Bot

from app.database import users_collection
from app.plans import PLAN_FREE
from app.notifier import notify_plan_expired

# =========================
# CONFIGURACIÓN
# =========================

CHECK_INTERVAL_SECONDS = 300  # 5 minutos

# =========================
# TAREA: EXPIRACIÓN DE PLANES
# =========================

async def check_expired_plans(bot: Bot):
    """
    Revisa planes vencidos y notifica al usuario.
    """
    users_col = users_collection()
    now = datetime.utcnow()

    for user in users_col.find({"plan_end": {"$lt": now}}):
        if user.get("plan") == PLAN_FREE:
            continue

        users_col.update_one(
            {"user_id": user["user_id"]},
            {
                "$set": {
                    "plan": PLAN_FREE,
                    "plan_end": None,
                    "updated_at": now,
                }
            }
        )

        try:
            await notify_plan_expired(bot, user["user_id"])
        except Exception:
            pass

# =========================
# LOOP PRINCIPAL
# =========================

async def scheduler_loop(bot: Bot):
    print("Scheduler iniciado correctamente")

    while True:
        try:
            await check_expired_plans(bot)
        except Exception as e:
            print(f"Error en scheduler: {e}")

        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
