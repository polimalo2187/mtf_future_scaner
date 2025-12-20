# app/database.py

import os
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

# =========================
# CONEXIÓN MONGODB
# =========================

MONGODB_URI = os.getenv("MONGODB_URI")
DATABASE_NAME = os.getenv("DATABASE_NAME")

if not MONGODB_URI or not DATABASE_NAME:
    raise RuntimeError("MONGODB_URI o DATABASE_NAME no están definidos")

_client = MongoClient(MONGODB_URI)
_db = _client[DATABASE_NAME]


# =========================
# COLECCIONES PRINCIPALES
# =========================

def users_collection():
    """
    Usuarios del bot
    """
    return _db["users"]


def referrals_collection():
    """
    Historial de referidos válidos
    """
    return _db["referrals"]


def signals_collection():
    """
    Señales BASE generadas por el scanner
    """
    return _db["signals"]


def user_signals_collection():
    """
    Señales PERSONALIZADAS entregadas a cada usuario
    (anti-compartición)
    """
    return _db["user_signals"]
