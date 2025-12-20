# app/database.py

from pymongo import MongoClient
from pymongo.errors import ConnectionFailure
import os
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

# Variables de entorno
MONGODB_URI = os.getenv("MONGODB_URI")
DATABASE_NAME = os.getenv("DATABASE_NAME", "mtf_futures_scanner")

# Cliente global (singleton)
_client = None
_db = None


def get_client():
    """
    Retorna un MongoClient único para toda la app (singleton).
    """
    global _client

    if _client is None:
        if not MONGODB_URI:
            raise RuntimeError("MONGODB_URI no está definido en las variables de entorno")

        try:
            _client = MongoClient(
                MONGODB_URI,
                serverSelectionTimeoutMS=5000,  # timeout razonable
                connectTimeoutMS=5000,
                socketTimeoutMS=5000,
                maxPoolSize=10,                # suficiente para un bot
            )
            # Ping para validar conexión
            _client.admin.command("ping")
        except ConnectionFailure as e:
            raise RuntimeError(f"No se pudo conectar a MongoDB: {e}")

    return _client


def get_db():
    """
    Retorna la base de datos principal.
    """
    global _db

    if _db is None:
        client = get_client()
        _db = client[DATABASE_NAME]

    return _db


# Helpers de colecciones (recomendado usar estos)
def users_collection():
    return get_db()["users"]


def referrals_collection():
    return get_db()["referrals"]


def signals_collection():
    return get_db()["signals"]
