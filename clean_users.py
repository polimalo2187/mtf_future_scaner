import os
from app.database import users_collection

def clean_users():
    # No se ejecutará si ya hemos eliminado este archivo
    print("🚀 Proceso de limpieza de usuarios iniciando...")

    # Obtener la colección de usuarios
    users_col = users_collection()

    # Eliminar todos los usuarios de la colección
    result = users_col.delete_many({})
    print(f"✅ Usuarios eliminados: {result.deleted_count}")

    print("🔧 Proceso de limpieza completado con éxito.")

    # Eliminar este archivo después de la ejecución
    delete_script()

def delete_script():
    # Eliminar el archivo de script después de que se ejecute
    try:
        script_name = os.path.basename(__file__)
        os.remove(script_name)
        print(f"🗑️ El archivo {script_name} ha sido eliminado automáticamente.")
    except Exception as e:
        print(f"❌ Error al intentar eliminar el script: {e}")

# Ejecutar la limpieza de usuarios
clean_users()
