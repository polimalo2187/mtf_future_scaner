# app/push_worker.py

import asyncio
import logging
from datetime import datetime
from telegram import Bot

from app.database import signals_collection
from app.notifier import notify_new_signal_alert

logger = logging.getLogger(__name__)

# ======================================================
# CONFIG
# ======================================================

PUSH_SCAN_INTERVAL = 5  # segundos
MAX_RETRIES = 5


# ======================================================
# WORKER
# ======================================================

async def push_worker(bot: Bot):
    logger.info("🚀 Push Worker iniciado")

    while True:
        try:
            pending_signals = list(
                signals_collection().find(
                    {
                        "push_sent": {"$ne": True}
                    }
                ).sort("created_at", 1)
            )

            for signal in pending_signals:
                try:
                    visibility = signal["visibility"]

                    await notify_new_signal_alert(
                        bot,
                        visibility,
                        symbol=signal.get("symbol"),
                        direction=signal.get("direction"),
                        created_at=signal.get("created_at"),
                    )

                    signals_collection().update_one(
                        {"_id": signal["_id"]},
                        {
                            "$set": {
                                "push_sent": True,
                                "push_sent_at": datetime.utcnow(),
                            }
                        }
                    )

                    logger.info(
                        f"📨 Push enviado correctamente | {signal['symbol']} {signal['direction']} ({visibility})"
                    )

                except Exception as e:
                    retries = signal.get("push_retries", 0) + 1

                    signals_collection().update_one(
                        {"_id": signal["_id"]},
                        {
                            "$set": {"push_retries": retries},
                            "$setOnInsert": {"push_sent": False},
                        }
                    )

                    logger.warning(
                        f"⚠️ Error enviando push ({retries}/{MAX_RETRIES}) | {signal.get('symbol')} | {e}"
                    )

                    if retries >= MAX_RETRIES:
                        signals_collection().update_one(
                            {"_id": signal["_id"]},
                            {
                                "$set": {
                                    "push_sent": True,
                                    "push_failed": True,
                                }
                            }
                        )
                        logger.error(
                            f"❌ Push descartado tras {MAX_RETRIES} intentos | {signal.get('symbol')}"
                        )

            await asyncio.sleep(PUSH_SCAN_INTERVAL)

        except Exception:
            logger.exception("❌ Error crítico en push_worker")
            await asyncio.sleep(5)
